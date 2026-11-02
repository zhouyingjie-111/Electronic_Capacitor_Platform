"""Durability, account isolation, binary output and authenticated downloads."""

import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flask import Blueprint, Flask
from werkzeug.datastructures import FileStorage
from ai_files import create_file
from ai_history import LocalHistory, MemoryHistory
from ai_service import AIService
from ai_routes import register_ai_routes
from ai_providers import AIError
from ai_fixture_support import FakeProvider, FakeSimulations, FakeSerial
from test_ai_service import png

KEY = "fixture-key-not-a-real-secret"


class DurableHistoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.ai, self.sid = self.open_service()
        self.thread = self.ai.new_thread(self.sid, "tracking")

    def open_service(self, key=KEY, provider="deepseek"):
        service = AIService(
            FakeSimulations(),
            FakeSerial(),
            FakeProvider(),
            LocalHistory(self.folder.name),
        )
        sid, _ = service.connect("", provider, key)
        service.select(sid, "fixture-vision")
        return service, sid

    def send(self, text="保存测试", **options):
        result = self.ai.send(
            self.sid, {"thread_id": self.thread["id"], "text": text, **options}
        )
        list(self.ai.events(self.ai.job(self.sid, result["job_id"])))
        return self.ai.thread(self.sid, self.thread["id"])["messages"][-1]

    def test_reconnect_same_key_restores_without_reusing_cookie(self):
        self.send()
        self.ai.disconnect(self.sid)
        sid, _ = self.ai.connect("", "deepseek", KEY)
        self.ai.select(sid, "fixture-vision")
        self.assertEqual(len(self.ai.thread(sid, self.thread["id"])["messages"]), 2)
        self.assertEqual(
            self.ai.status(sid, "tracking")["active_thread"], self.thread["id"]
        )

    def test_two_sessions_share_history_and_updates(self):
        sid, _ = self.ai.connect("", "deepseek", KEY)
        self.ai.select(sid, "fixture-vision")
        self.send()
        self.assertEqual(len(self.ai.thread(sid, self.thread["id"])["messages"]), 2)
        self.ai.disconnect(sid)
        self.assertTrue(self.ai.status(self.sid)["ready"])

    def test_real_subprocess_restart_recovers_encrypted_history(self):
        self.send()
        script = """import sys,json
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from ai_service import AIService
from ai_history import LocalHistory
from ai_fixture_support import FakeProvider,FakeSimulations,FakeSerial
a=AIService(FakeSimulations(),FakeSerial(),FakeProvider(),LocalHistory(sys.argv[3]))
s,_=a.connect('','deepseek',sys.argv[4]);a.select(s,'fixture-vision')
print(json.dumps({'count':len(a.thread(s,sys.argv[5])['messages'])}))
"""
        output = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(ROOT),
                str(ROOT / "tests"),
                self.folder.name,
                KEY,
                self.thread["id"],
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(output.stdout)["count"], 2)
        for file in Path(self.folder.name).iterdir():
            data = file.read_bytes()
            self.assertNotIn(KEY.encode(), data)
            self.assertNotIn("保存测试".encode(), data)

    def test_protocol_and_image_survive_restart(self):
        item = self.ai.upload(
            self.sid, FileStorage(stream=io.BytesIO(png()), filename="粘贴.png")
        )
        self.send(attachments=[item["id"]], allow_tools=True)
        service, sid = self.open_service()
        saved = service.session(sid, True)["threads"][self.thread["id"]]["messages"]
        self.assertTrue(
            saved[0]["content"][1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            )
        )
        self.assertEqual(saved[1]["encrypted_content"], "fixture-encrypted-block")
        self.assertTrue(saved[1]["tool_calls"])
        self.assertIn("reasoning_content", saved[1])
        self.assertEqual(service.attachment(sid, item["id"])["raw"], png())

    def test_different_key_provider_and_model_do_not_read_history(self):
        self.send()
        for key, provider in [("another-key-for-tests", "deepseek"), (KEY, "doubao")]:
            service, sid = self.open_service(key, provider)
            with self.assertRaises(AIError):
                service.thread(sid, self.thread["id"])
        self.ai.select(self.sid, "fixture-text")
        with self.assertRaises(AIError):
            self.ai.thread(self.sid, self.thread["id"])
        self.ai.select(self.sid, "fixture-vision")
        self.assertEqual(
            self.ai.status(self.sid, "tracking")["active_thread"], self.thread["id"]
        )

    def test_old_conversations_are_not_silently_evicted(self):
        for _ in range(13):
            self.ai.new_thread(self.sid, "tracking")
        self.assertEqual(len(self.ai.status(self.sid, "tracking")["threads"]), 14)
        self.assertEqual(self.ai.thread(self.sid, self.thread["id"])["messages"], [])

    def test_generated_file_and_export_survive_restart(self):
        answer = self.send("生成文件")
        file = answer["tools"][0]["result"]["file"]
        service, sid = self.open_service()
        raw = service.generated_file(sid, file["id"])["raw"]
        self.assertEqual(hashlib.sha256(raw).hexdigest(), file["sha256"])
        self.assertIn("电流", raw.decode("utf-8-sig"))
        exported = service.export_answer(sid, self.thread["id"], answer["id"], "pdf")
        self.assertTrue(
            service.generated_file(sid, exported["id"])["raw"].startswith(b"%PDF-")
        )

    def test_disk_failure_is_visible_and_not_claimed_saved(self):
        self.ai.store.save_thread = lambda *args: (_ for _ in ()).throw(
            OSError("test disk full")
        )
        answer = self.send()
        self.assertEqual(answer["state"], "completed")
        saved = self.ai.thread(self.sid, self.thread["id"])
        self.assertTrue(saved["storage_error"])

    def test_legacy_import_is_read_only_deduplicated_and_durable(self):
        archive = {
            "id": "legacy-test",
            "provider": "deepseek",
            "model": {"id": "fixture-vision"},
            "scope": "tracking",
            "title": "旧记录",
            "files": [],
            "messages": [
                {"role": "user", "content": "旧问题"},
                {"role": "assistant", "content": "旧回答", "reasoning": "旧摘要"},
            ],
        }
        result = self.ai.import_legacy(self.sid, archive)
        again = self.ai.import_legacy(self.sid, archive)
        self.assertEqual(result, again)
        service, sid = self.open_service()
        thread = service.thread(sid, result["thread_id"])
        self.assertEqual(thread["messages"][1]["content"], "旧回答")
        internal = service.session(sid, True)["threads"][result["thread_id"]][
            "messages"
        ]
        self.assertTrue(all(m["role"] == "user" for m in internal))

    def test_interrupted_generation_is_marked_stopped_on_restore(self):
        state = self.ai.session(self.sid, True)
        thread = state["threads"][self.thread["id"]]
        thread["busy"] = True
        thread["messages"] = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "tool_calls": []},
        ]
        thread["public"] = [
            {
                "id": "partial",
                "role": "assistant",
                "content": "已生成部分",
                "reasoning": "",
                "state": "responding",
            }
        ]
        self.ai._save_thread(state, thread)
        service, sid = self.open_service()
        restored = service.thread(sid, thread["id"])
        self.assertEqual(restored["messages"][0]["state"], "stopped")
        self.assertIsNone(restored["job_id"])
        self.assertEqual(
            len(service.session(sid, True)["threads"][thread["id"]]["messages"]), 1
        )


class GeneratedFilesTests(unittest.TestCase):
    def test_office_files_are_real_ooxml_and_do_not_execute_formulas(self):
        for suffix, required in [
            ("docx", "word/document.xml"),
            ("xlsx", "xl/worksheets/sheet1.xml"),
        ]:
            file = create_file(
                "结果." + suffix, '参数,数值\n电流,22\n危险,=HYPERLINK("x")', "thread"
            )
            with ZipFile(io.BytesIO(file["raw"])) as archive:
                for name in archive.namelist():
                    ET.fromstring(archive.read(name))
                root = ET.fromstring(archive.read(required))
                self.assertIn("电流", "".join(root.itertext()))
                if suffix == "xlsx":
                    self.assertEqual(
                        list(
                            root.iter(
                                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}f"
                            )
                        ),
                        [],
                    )

    def test_pdf_is_readable_and_chinese_text_extracts(self):
        from pypdf import PdfReader

        file = create_file(
            "建议.pdf", "# 电力电子建议\n负载电流22 A，边沿时间50 μs。", "thread"
        )
        pdf = PdfReader(io.BytesIO(file["raw"]))
        self.assertIn("负载电流22", pdf.pages[0].extract_text())

    def test_csv_json_and_filename_validation(self):
        csv = create_file("../结果.csv", "x,y\n=BAD(),-2\n", "t")
        self.assertEqual(csv["name"], "结果.csv")
        self.assertIn("'=BAD()", csv["raw"].decode("utf-8-sig"))
        for name, content in [("bad.exe", "text"), ("bad.json", "not json")]:
            with self.assertRaises(AIError):
                create_file(name, content, "t")

    def test_authenticated_binary_download_headers_and_bytes(self):
        app = Flask(__name__)
        app.config["AI_HISTORY_STORE"] = MemoryHistory()
        portal = Blueprint("project", __name__)
        service = register_ai_routes(app, portal, FakeSimulations(), FakeSerial())
        service.provider = FakeProvider()
        app.register_blueprint(portal)
        client = app.test_client()
        client.post(
            "/api/project/ai/connect", json={"provider": "deepseek", "key": KEY}
        )
        client.post("/api/project/ai/select", json={"model": "fixture-vision"})
        thread = client.post("/api/project/ai/threads", json={"scope": "general"}).json
        job = client.post(
            "/api/project/ai/messages",
            json={"thread_id": thread["id"], "text": "生成文件"},
        ).json
        client.get("/api/project/ai/jobs/" + job["job_id"] + "/events").get_data()
        answer = client.get("/api/project/ai/threads/" + thread["id"]).json["messages"][
            -1
        ]
        file = answer["tools"][0]["result"]["file"]
        response = client.get(file["url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["X-Content-SHA256"],
            hashlib.sha256(response.data).hexdigest(),
        )
        self.assertIn("attachment;", response.headers["Content-Disposition"])
        self.assertIn("filename*=", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        outsider = app.test_client()
        self.assertEqual(outsider.get(file["url"]).status_code, 401)
        outsider.post(
            "/api/project/ai/connect",
            json={"provider": "deepseek", "key": "a-different-fixture-key"},
        )
        outsider.post("/api/project/ai/select", json={"model": "fixture-vision"})
        self.assertEqual(outsider.get(file["url"]).status_code, 404)
        exported = client.post(
            "/api/project/ai/threads/"
            + thread["id"]
            + "/messages/"
            + answer["id"]
            + "/file",
            json={"format": "docx"},
        ).json
        self.assertTrue(client.get(exported["url"]).data.startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
