# CNN 数据链：示波器数据审计→预处理→训练→数值导出→编译核验。科学计算只在子进程中加载；与网页线程隔离。

import sys

"""File-based scope CNN jobs; scientific dependencies stay in a child process."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

from training_runner import TrainingError, TrainingRunner
from capacitance_data import inventory, unpack_dataset

ACTIVE = {"queued", "preparing", "training", "compiling"}


class CapTrainingRunner:
    def __init__(self, root, runtime, python=None):
        self.root = Path(root)
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.code = self.root / "Code/python/scope_cap"
        self.config = (
            json.loads((self.code / "config.json").read_text(encoding="utf-8"))
            if (self.code / "config.json").is_file()
            else {"data_dir": "../../../InputData/capacitance_scope", "labels": {}}
        )
        self.default_data = (self.code / self.config["data_dir"]).resolve()
        self.python = python or os.environ.get("CAP_PYTHON", sys.executable)
        self.jobs = {}
        self.uploads = {}
        self.lock = threading.RLock()
        self.active_id = None
        self.latest = {kind: None for kind in ("prepare", "training", "compile")}
        seed = self.root / "OutputData/scope_cap_v1"
        if (seed / "metrics.json").is_file() and (seed / "export_check.json").is_file():
            for kind in ("training", "compile"):
                if (
                    kind == "compile"
                    and not (seed / "dsp/cap_monitor_model.lib").is_file()
                ):
                    continue
                jid = hashlib.sha256((str(seed) + kind).encode()).hexdigest()[:32]
                logfile = (
                    Path(__file__).parent
                    / "runtime"
                    / (
                        "cap_initial_training.log"
                        if kind == "training"
                        else "cap_initial_compile.log"
                    )
                )
                lines = (
                    logfile.read_text(encoding="utf-8", errors="replace").splitlines()
                    if logfile.is_file()
                    else []
                )
                self.jobs[jid] = {
                    "id": jid,
                    "kind": kind,
                    "state": "completed",
                    "message": "训练完成。" if kind == "training" else "编译完成。",
                    "folder": seed,
                    "output": seed,
                    "started_at": seed.stat().st_mtime,
                    "finished_at": seed.stat().st_mtime,
                    "logs": [
                        clean
                        for line in lines
                        if (clean := TrainingRunner._public_log(line)) is not None
                    ],
                    "process": None,
                    "cancel": False,
                    "dataset_name": "默认示波器数据",
                }
                self.latest[kind] = jid
        for record in sorted(
            self.runtime.glob("*/record.json"), key=lambda p: p.stat().st_mtime
        ):
            try:
                job = json.loads(record.read_text(encoding="utf-8"))
                if (
                    not re.fullmatch(r"[a-f0-9]{32}", job["id"])
                    or job["id"] != record.parent.name
                ):
                    continue
                job.update(
                    folder=record.parent,
                    output=record.parent / "output",
                    process=None,
                    cancel=False,
                    logs=(record.parent / "progress.log")
                    .read_text(encoding="utf-8")
                    .splitlines(),
                )
                if job["state"] in ACTIVE:
                    job.update(
                        state="interrupted",
                        message="上次任务已中断，已有文件保留。",
                        finished_at=time.time(),
                    )
                self.jobs[job["id"]] = job
                if job["state"] == "completed":
                    self.latest[job["kind"]] = job["id"]
            except (OSError, ValueError, KeyError):
                continue

    def dataset(self, token="default"):
        if not token or token == "default":
            return self.default_data, self.config["labels"], "默认示波器数据"
        item = self.uploads.get(token)
        if not item or item["kind"] != "dataset":
            raise TrainingError("上传数据已失效，请重新选择 ZIP。", 404)
        return item["path"], item["labels"], item["name"]

    def preview(self, token="default", filename=None):
        directory, labels, _ = self.dataset(token)
        return inventory(directory, labels, filename)

    def upload(self, file, kind):
        if (
            kind not in ("dataset", "model")
            or not file
            or Path(file.filename or "").suffix.lower() != ".zip"
        ):
            raise TrainingError("请选择 ZIP 文件。")
        maximum = (200 if kind == "dataset" else 100) * 1024**2
        raw = file.stream.read(maximum + 1)
        if not raw or len(raw) > maximum:
            raise TrainingError(f"ZIP 不能为空且不能超过 {maximum//1024**2} MB。")
        token = uuid.uuid4().hex
        folder = self.runtime / "uploads" / token
        name = Path(file.filename.replace("\\", "/")).name
        if kind == "dataset":
            preview, labels = unpack_dataset(raw, folder, self.config["labels"])
            self.uploads[token] = {
                "kind": kind,
                "path": folder,
                "labels": labels,
                "name": name,
            }
            return {"token": token, "name": name, "preview": preview}
        import io

        folder.mkdir(parents=True)
        try:
            with ZipFile(io.BytesIO(raw)) as archive:
                if (
                    len(archive.infolist()) > 200
                    or sum(i.file_size for i in archive.infolist()) > 256 * 1024**2
                ):
                    raise TrainingError("模型压缩包过大。")
                for target in ("weights.npz", "golden_vectors.npz"):
                    found = [
                        i
                        for i in archive.infolist()
                        if i.filename.replace("\\", "/").split("/")[-1] == target
                    ]
                    if len(found) != 1 or found[0].file_size > 128 * 1024**2:
                        raise TrainingError(
                            "模型 ZIP 须包含唯一的 weights.npz 和 golden_vectors.npz。"
                        )
                    (folder / target).write_bytes(archive.read(found[0]))
        except TrainingError:
            raise
        except Exception:
            raise TrainingError("模型 ZIP 无法读取。") from None
        self.uploads[token] = {"kind": kind, "path": folder, "name": name}
        return {"token": token, "name": name}

    def _log(self, job, line):
        clean = TrainingRunner._public_log(line)
        if clean is None:
            return
        with self.lock:
            job["logs"].append(clean)
        with (job["folder"] / "progress.log").open("a", encoding="utf-8") as f:
            f.write(clean + "\n")

    def start(self, kind, dataset="default", model="latest"):
        """按 prepare/training/compile 创建任务；运行前检查数据契约，同一服务一次运行一个任务。"""
        if kind not in ("prepare", "training", "compile"):
            raise TrainingError("任务类型不正确。")
        if not Path(self.python).is_file():
            raise TrainingError("训练运行环境不可用。", 409)
        directory, labels, name = (
            self.dataset(dataset) if kind != "compile" else (None, None, "训练输出")
        )
        if directory:
            inventory(directory, labels)
        source = None
        if kind == "compile":
            if model == "latest":
                previous = max(
                    (
                        j
                        for j in self.jobs.values()
                        if j["kind"] == "training" and j["state"] == "completed"
                    ),
                    key=lambda j: j["started_at"],
                    default=None,
                )
                if not previous:
                    raise TrainingError("请先完成训练或上传模型 ZIP。", 409)
                source = previous["output"]
            else:
                item = self.uploads.get(model)
                if not item or item["kind"] != "model":
                    raise TrainingError("请重新上传模型 ZIP。", 404)
                source = item["path"]
        with self.lock:
            if self.active_id:
                raise TrainingError("已有辨识任务正在运行。", 409)
            jid = uuid.uuid4().hex
            folder = self.runtime / jid
            folder.mkdir()
            job = {
                "id": jid,
                "kind": kind,
                "state": "queued",
                "message": "正在准备任务…",
                "folder": folder,
                "output": folder / "output",
                "started_at": time.time(),
                "finished_at": None,
                "logs": [],
                "process": None,
                "cancel": False,
                "dataset_name": name,
            }
            self.jobs[jid] = job
            self.active_id = jid
            self.latest[kind] = jid
        threading.Thread(
            target=self._run,
            args=(job, directory, labels, source),
            daemon=True,
            name="cap-" + kind,
        ).start()
        return self.snapshot(jid)

    def _process(self, job, args):
        env = os.environ.copy()
        env.update(
            PYTHONIOENCODING="utf-8",
            PYTHONUNBUFFERED="1",
            TF_ENABLE_ONEDNN_OPTS="0",
            TF_CPP_MIN_LOG_LEVEL="2",
            OMP_NUM_THREADS="2",
        )
        kwargs = (
            {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        )
        with subprocess.Popen(
            [self.python, "-u", *map(str, args)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            text=True,
            env=env,
            cwd=job["folder"],
            **kwargs,
        ) as process:
            with self.lock:
                job["process"] = process
            if job["cancel"]:
                process.terminate()
            for line in process.stdout:
                self._log(job, line.rstrip())
            if process.wait() != 0:
                raise RuntimeError("程序未正常完成，请查看日志。")
        with self.lock:
            job["process"] = None

    def _run(self, job, directory, labels, source):
        """按固定流水线处理任务，在 finally 中写回记录并释放活动任务位置，允许失败后重试。"""
        try:
            output = job["output"]
            output.mkdir()
            if directory:
                job.update(
                    state="preparing" if job["kind"] == "prepare" else "training",
                    message=(
                        "正在预处理示波器数据…"
                        if job["kind"] == "prepare"
                        else "正在预处理并训练 CNN…"
                    ),
                )
                snapshot = job["folder"] / "dataset"
                snapshot.mkdir()
                for item in inventory(directory, labels)["files"]:
                    shutil.copy2(
                        Path(directory) / item["name"], snapshot / item["name"]
                    )
                cfg = {
                    **self.config,
                    "data_dir": str(snapshot.resolve()),
                    "output_dir": str(output.resolve()),
                    "labels": labels,
                }
                (job["folder"] / "config.json").write_text(
                    json.dumps(cfg, ensure_ascii=False), encoding="utf-8"
                )
                self._log(job, "开始示波器数据审计、抗混叠降采样与小波特征处理。")
                self._process(
                    job,
                    [
                        self.code / "train.py",
                        "--config",
                        job["folder"] / "config.json",
                        *(["--prepare-only"] if job["kind"] == "prepare" else []),
                    ],
                )
                required = (
                    "dataset.npz" if job["kind"] == "prepare" else "export_check.json"
                )
                if not (output / required).is_file():
                    raise RuntimeError("缺少完整运行产物。")
                message = (
                    "预处理完成。"
                    if job["kind"] == "prepare"
                    else "模型训练完成，下面请进行编译。"
                )
            else:
                job.update(state="compiling", message="正在核验导出模型并编译…")
                self._process(
                    job,
                    [
                        Path(__file__).with_name("prepare_cap_compile.py"),
                        source,
                        output,
                        self.code,
                    ],
                )
                clang = Path(os.environ.get("CAP_CLANG", shutil.which("clang") or ""))
                compiler = Path(
                    os.environ.get("CAP_CL2000", shutil.which("cl2000") or "")
                )
                if not clang.is_file() or not compiler.is_file():
                    raise RuntimeError(
                        "请在 config.json 设置 CAP_CLANG 和 CAP_CL2000；训练和源码导出结果已保留。"
                    )
                build = Path(__file__).parent / "runtime" / ("cap_build_" + job["id"])
                self._process(
                    job,
                    [
                        self.code / "verify_export.py",
                        output,
                        "--clang",
                        clang,
                        "--cl2000",
                        compiler,
                        "--target-build-dir",
                        build,
                    ],
                )
                if not (output / "dsp/cap_monitor_model.lib").is_file():
                    raise RuntimeError("未生成模型库。")
                message = "导出核验与模型编译完成。"
            job.update(state="completed", message=message)
            self._log(job, message)
        except Exception as error:
            self._log(job, str(error))
            job.update(
                state="cancelled" if job["cancel"] else "failed",
                message=(
                    "任务已停止，已有产物保留。"
                    if job["cancel"]
                    else "任务未完成，请查看运行日志。"
                ),
            )
        finally:
            job.update(finished_at=time.time(), process=None)
            record = {
                k: v
                for k, v in job.items()
                if k not in ("folder", "output", "logs", "process", "cancel")
            }
            (job["folder"] / "record.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8"
            )
            with self.lock:
                if self.active_id == job["id"]:
                    self.active_id = None

    def stop(self, jid):
        self.snapshot(jid)
        job = self.jobs[jid]
        job["cancel"] = True
        if job["process"] and job["process"].poll() is None:
            job["process"].terminate()
        return {"ok": True}

    def snapshot(self, jid=None, after=0):
        """返回用于轮询的任务进度及受控日志窗口；文件下载通过单独的产物接口处理。"""
        if not jid:
            return None
        if jid not in self.jobs:
            raise TrainingError("任务不存在。", 404)
        job = self.jobs[jid]
        after = max(0, min(after, len(job["logs"])))
        result = {k: job[k] for k in ("id", "kind", "state", "message", "dataset_name")}
        result.update(
            logs=job["logs"][after : after + 500],
            next_cursor=min(after + 500, len(job["logs"])),
            has_more=after + 500 < len(job["logs"]),
            elapsed_seconds=round(
                (job["finished_at"] or time.time()) - job["started_at"], 1
            ),
        )
        for name, key in [
            ("metrics.json", "metrics"),
            ("split_counts.json", "splits"),
            ("native_verification.json", "verification"),
        ]:
            path = job["output"] / name
            if path.is_file():
                try:
                    result[key] = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    pass
        if "verification" in result:
            result["verification"] = {
                k: v
                for k, v in result["verification"].items()
                if k not in ("target_compiler", "target_archive")
            }
        result["groups"] = sorted(
            {
                p.name.split("_")[0]
                for p in (job["output"] / "image_preview").glob("*.png")
            }
        )
        return result

    def current(self):
        result = {kind: self.snapshot(jid) for kind, jid in self.latest.items()}
        saved = max(
            (
                j
                for j in self.jobs.values()
                if j["kind"] == "training" and j["state"] == "completed"
            ),
            key=lambda j: j["started_at"],
            default=None,
        )
        result["saved"] = self.snapshot(saved["id"]) if saved else None
        return result

    def artifact(self, jid, name):
        self.snapshot(jid)
        if name not in (
            "metrics.json",
            "history.json",
            "split_counts.json",
            "data_audit.json",
            "export_check.json",
        ) and not re.fullmatch(r"\d{4}_CH[134]\.png", name):
            raise TrainingError("文件不存在。", 404)
        path = (
            self.jobs[jid]["output"]
            / ("image_preview" if name.endswith(".png") else "")
            / name
        )
        if not path.is_file():
            raise TrainingError("文件尚未生成。", 404)
        return path

    def archive(self, jid):
        self.snapshot(jid)
        job = self.jobs[jid]
        if job["state"] != "completed":
            raise TrainingError("任务完成后才能下载。", 409)
        path = self.runtime / (jid + "_result.zip")
        if not path.is_file():
            allowed = {
                "model.keras",
                "architecture.json",
                "weights.npz",
                "preprocess.npz",
                "golden_vectors.npz",
                "metrics.json",
                "history.json",
                "predictions.csv",
                "data_audit.json",
                "split_counts.json",
                "export_check.json",
                "native_verification.json",
                "window_manifest.json",
                "dataset.npz",
            }
            with ZipFile(path, "w", ZIP_DEFLATED) as archive:
                for file in job["output"].rglob("*"):
                    relative = file.relative_to(job["output"])
                    if file.is_file() and (
                        file.name in allowed
                        or relative.parts[0] in ("dsp", "image_preview")
                    ):
                        archive.write(file, relative)
        return path
