"""No real credentials, network access, MATLAB execution or hardware required."""

import io
import json
import sys
import threading
import unittest
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor_public"))
from flask import Flask, Blueprint
from PIL import Image
from werkzeug.datastructures import FileStorage
from ai_service import AIService
from ai_providers import AIError, ProviderClient
from ai_routes import register_ai_routes
from ai_attachments import load_attachment, safe_zip
from ai_tools import calculate
from ai_history import MemoryHistory
from ai_fixture_support import FakeProvider, FakeSimulations, FakeSerial


def upload(name, data):
    return FileStorage(stream=io.BytesIO(data), filename=name)


def png():
    out = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(out, format="PNG")
    return out.getvalue()


class AIServiceTests(unittest.TestCase):
    def setUp(self):
        self.provider = FakeProvider()
        self.sim = FakeSimulations()
        self.ai = AIService(self.sim, FakeSerial(), self.provider)
        self.sid, _ = self.ai.connect("", "deepseek", "fixture-key-not-a-real-secret")
        self.ai.select(self.sid, "fixture-vision")
        self.thread = self.ai.new_thread(self.sid, "tracking")

    def send(self, **kwargs):
        data = {"thread_id": self.thread["id"], "text": "测试问题", **kwargs}
        result = self.ai.send(self.sid, data)
        job = self.ai.job(self.sid, result["job_id"])
        events = "".join(self.ai.events(job))
        return self.ai.thread(self.sid, self.thread["id"])["messages"][-1], events

    def test_access_requires_key_then_model(self):
        with self.assertRaises(AIError):
            self.ai.new_thread("", "general")
        sid, state = self.ai.connect("", "doubao", "fixture-key-not-a-real-secret")
        self.assertFalse(state["ready"])
        with self.assertRaises(AIError):
            self.ai.new_thread(sid, "general")
        self.assertNotIn("fixture-key", json.dumps(state))

    def test_scope_prompts_and_history_are_separate(self):
        self.send()
        tracking = self.provider.payloads[-1]["messages"][0]["content"]
        self.assertIn("DTgiven", tracking)
        self.assertIn("Vs1", tracking)
        general = self.ai.new_thread(self.sid, "general")
        self.assertNotEqual(general["id"], self.thread["id"])
        self.assertEqual(
            self.ai.status(self.sid, "general")["threads"][0]["id"], general["id"]
        )
        self.assertNotIn(
            self.thread["id"],
            json.dumps(self.ai.status(self.sid, "general")["threads"]),
        )

    def test_reasoning_and_encrypted_blocks_replayed_without_key(self):
        answer, events = self.send()
        self.assertEqual(answer["state"], "completed")
        self.assertIn("分析摘要", answer["reasoning"])
        self.assertIn('"event": "finish"', events)
        self.send(text="继续")
        previous = next(
            m
            for m in self.provider.payloads[-1]["messages"]
            if m["role"] == "assistant"
        )
        self.assertEqual(previous["encrypted_content"], "fixture-encrypted-block")
        self.assertIn("分析摘要", previous["reasoning_content"])
        self.assertNotIn("fixture-key", json.dumps(self.provider.payloads))
        self.assertNotIn(
            "encrypted_content", json.dumps(self.ai.thread(self.sid, self.thread["id"]))
        )

    def test_incomplete_stream_is_not_claimed_complete(self):
        answer, _ = self.send(text="不完整")
        self.assertEqual(answer["state"], "error")
        self.assertIn("部分响应", answer["content"])

    def test_image_and_csv_are_actually_in_request(self):
        image = self.ai.upload(self.sid, upload("粘贴图片.png", png()))
        file = self.ai.upload(self.sid, upload("测试.csv", b"t,v\n0,2\n1,4\n"))
        self.send(attachments=[image["id"], file["id"]])
        blocks = self.provider.payloads[-1]["messages"][-1]["content"]
        self.assertTrue(
            any(
                b.get("image_url", {})
                .get("url", "")
                .startswith("data:image/png;base64,")
                for b in blocks
            )
        )
        self.assertTrue(any("t,v" in b.get("text", "") for b in blocks))

    def test_session_isolation_and_model_capability(self):
        image = self.ai.upload(self.sid, upload("image.png", png()))
        sid, _ = self.ai.connect("", "deepseek", "another-fixture-key-only")
        self.ai.select(sid, "fixture-vision")
        with self.assertRaises(AIError):
            self.ai.attachment(sid, image["id"])
        self.ai.select(self.sid, "fixture-text")
        thread = self.ai.new_thread(self.sid, "tracking")
        with self.assertRaises(AIError):
            self.ai.send(
                self.sid,
                {
                    "thread_id": thread["id"],
                    "text": "分析",
                    "attachments": [image["id"]],
                },
            )
        with self.assertRaises(AIError):
            self.ai.thread(self.sid, self.thread["id"])

    def test_tool_round_preserves_full_protocol(self):
        answer, _ = self.send(allow_tools=True)
        self.assertEqual(answer["tools"][0]["name"], "engineering_calculator")
        self.assertGreater(answer["tools"][0]["result"]["value"], 700)
        messages = self.provider.payloads[1]["messages"]
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertIn("encrypted_content", messages[-2])
        self.assertIn("reasoning_content", messages[-2])

    def test_simulation_only_starts_after_explicit_confirmation(self):
        answer, _ = self.send(text="准备仿真", allow_tools=True)
        draft = answer["tools"][0]["result"]["draft"]
        self.assertEqual(self.sim.calls, [])
        job = self.ai.execute_draft(self.sid, draft["id"])
        self.assertEqual(len(self.sim.calls), 1)
        self.assertEqual(self.sim.calls[0][1], 0.01)
        with self.assertRaises(AIError):
            self.ai.execute_draft(self.sid, draft["id"])
        saved = self.ai.thread(self.sid, self.thread["id"])["messages"][-1]["tools"][0][
            "result"
        ]["draft"]
        self.assertEqual(saved["job_id"], job["id"])

    def test_capacitance_does_not_run_tracking_model(self):
        state = self.ai.session(self.sid, True)
        thread = {"scope": "capacitance", "id": "cap"}
        with self.assertRaises(AIError):
            self.ai.tools.run("prepare_simulation", {"control": "pi"}, state, thread)
        self.assertEqual(self.sim.calls, [])

    def test_cannot_read_unowned_results_or_execute_unknown_program(self):
        state = self.ai.session(self.sid, True)
        with self.assertRaises(AIError):
            self.ai.tools.run(
                "get_simulation_result", {"job_id": "other"}, state, {"id": "x"}
            )
        with self.assertRaises(AIError):
            self.ai.tools.run("shell", {"code": "anything"}, state, {"id": "x"})

    def test_calculator_rejects_code_and_bad_values(self):
        self.assertAlmostEqual(calculate("sqrt(4)+sin(pi/2)")["value"], 3)
        for expression in (
            '__import__("os").system("x")',
            'open("x")',
            "1/0",
            "2**100000",
            "(1).__class__",
        ):
            with self.subTest(expression=expression), self.assertRaises(AIError):
                calculate(expression)

    def test_csv_tool_requires_sent_attachment(self):
        item = self.ai.upload(self.sid, upload("data.csv", b"x,y\n1,2\n3,4\n"))
        state = self.ai.session(self.sid, True)
        thread = state["threads"][self.thread["id"]]
        with self.assertRaises(AIError):
            self.ai.tools.run(
                "analyze_uploaded_csv", {"attachment_id": item["id"]}, state, thread
            )
        self.send(attachments=[item["id"]])
        result = self.ai.tools.run(
            "analyze_uploaded_csv", {"attachment_id": item["id"]}, state, thread
        )
        self.assertEqual(result["columns"][0]["mean"], 2)

    def test_stop_and_retry(self):
        result = self.ai.send(
            self.sid, {"thread_id": self.thread["id"], "text": "慢速测试"}
        )
        self.ai.stop(self.sid, result["job_id"])
        job = self.ai.job(self.sid, result["job_id"])
        "".join(self.ai.events(job))
        self.assertEqual(job["assistant"]["state"], "stopped")
        answer, _ = self.send(retry=True)
        self.assertEqual(
            len(self.ai.thread(self.sid, self.thread["id"])["messages"]), 2
        )

    def test_disconnect_purges_secret_but_retains_saved_history(self):
        self.ai.upload(self.sid, upload("image.png", png()))
        state = self.ai.session(self.sid)
        self.ai.disconnect(self.sid)
        self.assertEqual(state["key"], "")
        self.assertEqual(len(state["attachments"]), 1)
        self.assertFalse(self.ai.status(self.sid)["connected"])

    def test_attachment_parsers_and_limits(self):
        self.assertEqual(load_attachment(upload("demo.m", b"x=1;"))["text"], "x=1;")
        with self.assertRaises(AIError):
            load_attachment(upload("bad.png", b"not an image"))
        with self.assertRaises(AIError):
            load_attachment(upload("run.exe", b"MZ"))
        with self.assertRaises(AIError):
            load_attachment(upload("big.txt", b"a" * (8 * 1024**2 + 1)))
        output = io.BytesIO()
        with ZipFile(output, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<doc><p xmlns="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><t>hello</t></p></doc>',
            )
        self.assertIn(
            "hello", load_attachment(upload("test.docx", output.getvalue()))["text"]
        )


class AIRoutesTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config["AI_HISTORY_STORE"] = MemoryHistory()
        portal = Blueprint("project", __name__)
        self.ai = register_ai_routes(app, portal, FakeSimulations(), FakeSerial())
        self.ai.provider = FakeProvider()
        app.register_blueprint(portal)
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_key_cookie_and_csrf_guards(self):
        self.assertEqual(
            self.client.post(
                "/api/project/ai/connect",
                json={"provider": "deepseek", "key": "fixture-key-not-a-real-secret"},
                headers={"Origin": "https://evil.invalid"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                "/api/project/ai/status",
                environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
            ).status_code,
            403,
        )
        result = self.client.post(
            "/api/project/ai/connect",
            json={"provider": "deepseek", "key": "fixture-key-not-a-real-secret"},
        )
        self.assertEqual(result.status_code, 200)
        cookie = result.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("fixture-key", cookie + result.get_data(as_text=True))
        self.assertEqual(
            self.client.post(
                "/api/project/ai/select", json={"model": "fixture-vision"}
            ).status_code,
            200,
        )
        thread = self.client.post(
            "/api/project/ai/threads", json={"scope": "general"}
        ).json
        job = self.client.post(
            "/api/project/ai/messages", json={"thread_id": thread["id"], "text": "test"}
        ).json
        events = self.client.get(
            "/api/project/ai/jobs/" + job["job_id"] + "/events"
        ).get_data(as_text=True)
        self.assertIn('"event": "finish"', events)
        self.assertEqual(
            self.client.post("/api/project/ai/disconnect", json={}).status_code, 200
        )
        self.assertEqual(
            self.client.get("/api/project/ai/threads/" + thread["id"]).status_code, 401
        )

    def test_upload_clear_stop_and_simulation_routes(self):
        self.client.post(
            "/api/project/ai/connect",
            json={"provider": "deepseek", "key": "fixture-key-not-a-real-secret"},
        )
        self.client.post("/api/project/ai/select", json={"model": "fixture-vision"})
        item = self.client.post(
            "/api/project/ai/upload", data={"file": (io.BytesIO(png()), "paste.png")}
        ).json
        self.assertEqual(
            self.client.get("/api/project/ai/attachments/" + item["id"]).status_code,
            200,
        )
        self.assertEqual(
            self.client.delete(
                "/api/project/ai/attachments/" + item["id"], json={}
            ).status_code,
            200,
        )
        thread = self.client.post(
            "/api/project/ai/threads", json={"scope": "tracking"}
        ).json
        self.assertEqual(
            self.client.get("/api/project/ai/threads/" + thread["id"]).status_code, 200
        )
        job = self.client.post(
            "/api/project/ai/messages",
            json={"thread_id": thread["id"], "text": "准备仿真", "allow_tools": True},
        ).json
        self.client.get("/api/project/ai/jobs/" + job["job_id"] + "/events").get_data()
        saved = self.client.get("/api/project/ai/threads/" + thread["id"]).json
        draft = saved["messages"][-1]["tools"][0]["result"]["draft"]["id"]
        result = self.client.post("/api/project/ai/drafts/" + draft + "/run", json={})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(
            self.client.get(
                "/api/project/ai/simulations/" + result.json["id"]
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/project/ai/jobs/" + job["job_id"] + "/stop", json={}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.delete(
                "/api/project/ai/threads/" + thread["id"], json={}
            ).status_code,
            200,
        )


class ProviderTests(unittest.TestCase):
    def test_official_urls_and_doubao_model_probe(self):
        calls = []

        class Reply:
            status_code = 200
            content = b"{}"

            def json(self):
                return {
                    "data": [
                        {
                            "id": "flash",
                            "name": "Flash",
                            "input_modalities": ["text", "image"],
                        }
                    ]
                }

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def request(self, *args, **kwargs):
                calls.append((args, kwargs))
                return Reply()

        provider = ProviderClient(Client)
        self.assertTrue(provider.models("deepseek", "secret")[0]["vision"])
        self.assertEqual(calls[0][0][1], "https://api.deepseek.com/models")
        catalogue = provider.models("doubao", "secret")
        self.assertEqual(len(calls), 1)
        provider.verify("doubao", "secret", catalogue[0])
        self.assertEqual(
            calls[-1][0][1], "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
        )
        self.assertEqual(calls[-1][1]["json"]["max_tokens"], 1)

    def test_provider_sse_preserves_reasoning_and_encryption(self):
        class Reply:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def close(self):
                pass

            def iter_lines(self):
                return iter(
                    [
                        ": ping",
                        'data: {"choices":[{"delta":{"reasoning_content":"summary"}}]}',
                        "",
                        'data: {"choices":[{"delta":{"encrypted_content":"opaque"}}]}',
                        "",
                        "data: [DONE]",
                        "",
                    ]
                )

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def stream(self, *args, **kwargs):
                return Reply()

        packets = list(
            ProviderClient(Client).stream(
                "doubao", "secret", {}, threading.Event(), lambda fn: None
            )
        )
        self.assertEqual(
            packets[1]["choices"][0]["delta"]["encrypted_content"], "opaque"
        )


if __name__ == "__main__":
    unittest.main()
