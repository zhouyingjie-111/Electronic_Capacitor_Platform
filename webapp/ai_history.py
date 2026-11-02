"""Local encrypted history; API keys are used for identity, never stored."""

# 隐私边界：API Key 用于会话身份，不落盘；Windows 历史使用当前账户的 DPAPI。runtime 为用户数据，禁止随源码发布。

import base64
from contextlib import contextmanager
import copy
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import zlib

from ai_providers import AIError


class WindowsProtection:
    """DPAPI binds saved data to the current Windows user, including elevated runs."""

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    def __init__(self):
        self.crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        self.free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
        self.free.argtypes = [ctypes.c_void_p]
        self.free.restype = ctypes.c_void_p
        for name in ("CryptProtectData", "CryptUnprotectData"):
            method = getattr(self.crypt, name)
            method.restype = wintypes.BOOL
            method.argtypes = [
                ctypes.POINTER(self.Blob),
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(self.Blob),
            ]

    def _apply(self, data, method):
        buffer = ctypes.create_string_buffer(data)
        source = self.Blob(
            len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        )
        result = self.Blob()
        if not getattr(self.crypt, method)(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)
        ):
            raise AIError(
                "本机历史记录加密或解密失败，请使用原 Windows 账户打开网站。", 500
            )
        try:
            return ctypes.string_at(result.data, result.size)
        finally:
            self.free(result.data)

    def seal(self, data):
        return self._apply(data, "CryptProtectData")

    def open(self, data):
        return self._apply(data, "CryptUnprotectData")


def encode(value):
    return zlib.compress(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            default=lambda data: (
                {"__bytes__": base64.b64encode(data).decode()}
                if isinstance(data, bytes)
                else None
            ),
        ).encode("utf-8")
    )


def decode(data):
    return json.loads(
        zlib.decompress(data).decode("utf-8"),
        object_hook=lambda item: (
            base64.b64decode(item["__bytes__"]) if set(item) == {"__bytes__"} else item
        ),
    )


def compact(thread, attachments):
    result = copy.deepcopy(thread)
    images = {
        item.get("data_url"): item["id"]
        for item in attachments.values()
        if item.get("data_url")
    }
    for message in result["messages"]:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for index, block in enumerate(content):
            if (
                block.get("type") == "image_url"
                and block.get("image_url", {}).get("url") in images
            ):
                content[index] = {
                    "type": "saved_image",
                    "attachment_id": images[block["image_url"]["url"]],
                }
    return result


def hydrate(thread, attachments):
    for message in thread["messages"]:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for index, block in enumerate(content):
            if block.get("type") == "saved_image":
                item = attachments.get(block["attachment_id"])
                content[index] = (
                    {"type": "image_url", "image_url": {"url": item["data_url"]}}
                    if item
                    else {"type": "text", "text": "[原图片附件缺失，请重新上传。]"}
                )
    if thread.get("busy"):
        thread["messages"] = thread["messages"][
            : max(
                (i for i, m in enumerate(thread["messages"]) if m["role"] == "user"),
                default=-1,
            )
            + 1
        ]
        for message in thread["public"]:
            if message.get("state") == "responding":
                message.update(
                    state="stopped",
                    error="上次服务中断，已恢复保存的内容，可重新生成。",
                )
    thread["busy"] = False
    return thread


class MemoryHistory:
    """Dependency injection for tests; production always supplies LocalHistory."""

    def __init__(self):
        self.secret = secrets.token_bytes(32)
        self.records = {}

    def identity(self, provider, key):
        return hmac.new(
            self.secret, (provider + "\0" + key).encode(), hashlib.sha256
        ).hexdigest()

    def preferences(self, account):
        return copy.deepcopy(self.records.get(account, {}).get("prefs", {}))

    def save_preferences(self, account, value):
        self.records.setdefault(account, {})["prefs"] = copy.deepcopy(value)

    def save_thread(self, account, thread, attachments):
        self.records.setdefault(account, {}).setdefault("threads", {})[thread["id"]] = (
            compact(thread, attachments)
        )

    def save_file(self, account, item, generated=False):
        self.records.setdefault(account, {}).setdefault("files", {})[item["id"]] = (
            copy.deepcopy(item),
            generated,
        )

    def delete_file(self, account, item_id):
        self.records.get(account, {}).get("files", {}).pop(item_id, None)

    def delete_thread(self, account, thread_id):
        self.records.get(account, {}).get("threads", {}).pop(thread_id, None)

    def load(self, account):
        value = copy.deepcopy(self.records.get(account, {}))
        attachments = {}
        artifacts = {}
        for item, generated in value.get("files", {}).values():
            (artifacts if generated else attachments)[item["id"]] = item
        threads = {
            key: hydrate(thread, attachments)
            for key, thread in value.get("threads", {}).items()
        }
        return {
            "threads": threads,
            "attachments": attachments,
            "artifacts": artifacts,
            "prefs": value.get("prefs", {}),
        }


class LocalHistory(MemoryHistory):
    def __init__(self, folder, protection=None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.protection = protection or WindowsProtection()
        self.lock = threading.RLock()
        secret_path = self.folder / "identity.dpapi"
        try:
            with secret_path.open("xb") as target:
                target.write(self.protection.seal(secrets.token_bytes(32)))
        except FileExistsError:
            pass
        self.secret = self.protection.open(secret_path.read_bytes())
        self.path = self.folder / "history.sqlite3"
        with self._db() as db:
            db.executescript(
                """CREATE TABLE IF NOT EXISTS preferences(account TEXT PRIMARY KEY,payload BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS threads(account TEXT,id TEXT,payload BLOB NOT NULL,PRIMARY KEY(account,id));
            CREATE TABLE IF NOT EXISTS files(account TEXT,id TEXT,generated INTEGER,payload BLOB NOT NULL,PRIMARY KEY(account,id));"""
            )

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _pack(self, value):
        return self.protection.seal(encode(value))

    def _unpack(self, value):
        return decode(self.protection.open(value))

    def preferences(self, account):
        with self.lock, self._db() as db:
            row = db.execute(
                "SELECT payload FROM preferences WHERE account=?", (account,)
            ).fetchone()
            return self._unpack(row[0]) if row else {}

    def save_preferences(self, account, value):
        with self.lock, self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO preferences VALUES(?,?)",
                (account, self._pack(value)),
            )

    def save_thread(self, account, thread, attachments):
        value = compact(thread, attachments)
        with self.lock, self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO threads VALUES(?,?,?)",
                (account, thread["id"], self._pack(value)),
            )

    def save_file(self, account, item, generated=False):
        value = dict(item)
        value.pop("data_url", None)
        with self.lock, self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO files VALUES(?,?,?,?)",
                (account, item["id"], int(generated), self._pack(value)),
            )

    def delete_file(self, account, item_id):
        with self.lock, self._db() as db:
            db.execute("DELETE FROM files WHERE account=? AND id=?", (account, item_id))

    def delete_thread(self, account, thread_id):
        with self.lock, self._db() as db:
            db.execute(
                "DELETE FROM threads WHERE account=? AND id=?", (account, thread_id)
            )

    def load(self, account):
        with self.lock, self._db() as db:
            attachments = {}
            artifacts = {}
            for generated, payload in db.execute(
                "SELECT generated,payload FROM files WHERE account=?", (account,)
            ):
                item = self._unpack(payload)
                if not generated and item["kind"] == "image":
                    item["data_url"] = (
                        "data:"
                        + item["mime"]
                        + ";base64,"
                        + base64.b64encode(item["raw"]).decode()
                    )
                (artifacts if generated else attachments)[item["id"]] = item
            threads = {}
            for key, payload in db.execute(
                "SELECT id,payload FROM threads WHERE account=?", (account,)
            ):
                threads[key] = hydrate(self._unpack(payload), attachments)
        return {
            "threads": threads,
            "attachments": attachments,
            "artifacts": artifacts,
            "prefs": self.preferences(account),
        }


class LazyLocalHistory:
    """Page rendering/tests must not create a store under a different OS identity."""

    def __init__(self, folder):
        self.folder = folder
        self.value = None
        self.lock = threading.RLock()

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        with self.lock:
            if self.value is None:
                self.value = LocalHistory(self.folder)
            return getattr(self.value, name)
