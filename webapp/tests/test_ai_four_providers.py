"""Offline regression: official endpoints, payload dialects and account isolation."""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_providers import (
    AIError,
    ProviderClient,
    KIMI_MODELS,
    QWEN_MODELS,
    DOUBAO_MODELS,
    official_base,
    thinking_parameters,
)
from ai_service import AIService
from ai_fixture_support import FakeProvider, FakeSimulations, FakeSerial

KEY = "fixture-four-providers-not-a-real-key"


class FourProviderTests(unittest.TestCase):
    def service(self):
        return AIService(FakeSimulations(), FakeSerial(), FakeProvider())

    def test_all_three_scopes_work_for_each_provider_and_restore(self):
        for provider in ("deepseek", "doubao", "kimi", "qwen"):
            with self.subTest(provider=provider):
                ai = self.service()
                sid, _ = ai.connect("", provider, KEY)
                ai.select(sid, "fixture-vision")
                ids = []
                for scope in ("general", "tracking", "capacitance"):
                    thread = ai.new_thread(sid, scope)
                    ids.append(thread["id"])
                    sent = ai.send(sid, {"thread_id": thread["id"], "text": "测试问题"})
                    events = "".join(ai.events(ai.job(sid, sent["job_id"])))
                    self.assertIn('"state": "completed"', events)
                    self.assertEqual(len(ai.thread(sid, thread["id"])["messages"]), 2)
                ai.disconnect(sid)
                sid, _ = ai.connect("", provider, KEY)
                ai.select(sid, "fixture-vision")
                self.assertTrue(
                    all(len(ai.thread(sid, id)["messages"]) == 2 for id in ids)
                )

    def test_qwen_uses_enable_thinking_and_excludes_reasoning_in_replay(self):
        ai = self.service()
        sid, _ = ai.connect("", "qwen", KEY)
        ai.select(sid, "fixture-vision")
        t = ai.new_thread(sid, "general")
        for _ in range(2):
            sent = ai.send(
                sid, {"thread_id": t["id"], "text": "测试", "thinking": True}
            )
            list(ai.events(ai.job(sid, sent["job_id"])))
        payload = ai.provider.payloads[-1]
        self.assertTrue(payload["enable_thinking"])
        self.assertNotIn("thinking", payload)
        self.assertTrue(
            all(
                "reasoning_content" not in m and "encrypted_content" not in m
                for m in payload["messages"]
            )
        )
        self.assertTrue(ai.thread(sid, t["id"])["messages"][-1]["reasoning"])

    def test_kimi_always_on_and_switchable_thinking_dialects(self):
        for m in KIMI_MODELS:
            params = thinking_parameters("kimi", m, False)
            if m["id"].startswith("kimi-k3"):
                self.assertEqual(params, {"reasoning_effort": "high"})
            elif m["id"].startswith("kimi-k2.7"):
                self.assertEqual(params, {})
            else:
                self.assertEqual(params, {"thinking": {"type": "disabled"}})
        self.assertEqual(
            thinking_parameters("kimi", KIMI_MODELS[-1], True)["thinking"]["keep"],
            "all",
        )

    def test_kimi_token_budget_and_reasoning_preserved_across_tool_turns(self):
        ai = self.service()
        sid, _ = ai.connect("", "kimi", KEY)
        ai.select(sid, "fixture-vision")
        t = ai.new_thread(sid, "tracking")
        s = ai.send(sid, {"thread_id": t["id"], "text": "计算", "allow_tools": True})
        list(ai.events(ai.job(sid, s["job_id"])))
        payload = ai.provider.payloads[-1]
        self.assertEqual(payload["max_completion_tokens"], 16384)
        self.assertNotIn("max_tokens", payload)
        self.assertTrue(
            any(
                m.get("reasoning_content")
                for m in payload["messages"]
                if m["role"] == "assistant"
            )
        )

    def test_catalogue_versions_and_retired_models(self):
        self.assertGreaterEqual(len(KIMI_MODELS), 6)
        self.assertGreaterEqual(len(QWEN_MODELS), 24)
        self.assertGreaterEqual(len(DOUBAO_MODELS), 8)
        for models in (KIMI_MODELS, QWEN_MODELS, DOUBAO_MODELS):
            self.assertEqual(len(models), len({m["id"] for m in models}))
        self.assertNotIn("kimi-k2.5", [m["id"] for m in KIMI_MODELS])
        self.assertNotIn(
            "doubao-seed-2-0-lite-260215", [m["id"] for m in DOUBAO_MODELS]
        )

    def test_allowlisted_regions_and_workspace_hosts(self):
        self.assertEqual(
            official_base("kimi", {"region": "intl"}), "https://api.moonshot.ai/v1"
        )
        self.assertEqual(official_base("kimi"), "https://api.moonshot.cn/v1")
        self.assertEqual(
            official_base("qwen", {"region": "ap-southeast-1"}),
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        )
        self.assertEqual(
            official_base("qwen", {"region": "us-east-1", "workspace": "llm-demo"}),
            "https://llm-demo.us-east-1.maas.aliyuncs.com/compatible-mode/v1",
        )
        for invalid in (
            {"region": "evil.com"},
            {"workspace": "evil.com/x"},
            {"base": "https://evil.invalid"},
            {"region": "ap-northeast-1"},
        ):
            with self.assertRaises(AIError):
                official_base("qwen", invalid)

    def test_same_key_in_different_regions_cannot_read_history(self):
        ai = self.service()
        sid, _ = ai.connect("", "qwen", KEY, {"region": "cn-beijing"})
        ai.select(sid, "fixture-vision")
        t = ai.new_thread(sid, "general")
        other, _ = ai.connect("", "qwen", KEY, {"region": "ap-southeast-1"})
        ai.select(other, "fixture-vision")
        with self.assertRaises(AIError):
            ai.thread(other, t["id"])

    def test_custom_model_and_refresh_supported_for_all_providers(self):
        for provider in ("deepseek", "doubao", "kimi", "qwen"):
            ai = self.service()
            sid, _ = ai.connect("", provider, KEY)
            ai.select(
                sid,
                "custom-official-model",
                {"vision": False, "thinking": False, "tools": True},
            )
            self.assertEqual(
                ai.refresh_models(sid)["selected"]["id"], "custom-official-model"
            )
            self.assertNotIn(KEY, json.dumps(ai.status(sid)))
            with self.assertRaises(AIError):
                ai.select(sid, "https://evil.invalid?a=1", {})

    def test_kimi_live_catalogue_flags_and_verification_are_non_inference(self):
        calls = []

        class Reply:
            status_code = 200
            content = b"{}"

            def json(self):
                return {
                    "data": [
                        {"id": "kimi-k3.1-pro"},
                        {"id": "kimi-k2.5"},
                        {
                            "id": "kimi-future",
                            "supports_image_in": True,
                            "supports_reasoning": True,
                        },
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
        models = provider.models("kimi", KEY, {"region": "intl"})
        self.assertEqual(len(models), 2)
        self.assertTrue(models[0]["thinking_required"])
        self.assertTrue(models[1]["vision"])
        provider.verify("kimi", KEY, models[0], {"region": "intl"})
        self.assertTrue(
            all(c[0] == ("GET", "https://api.moonshot.ai/v1/models") for c in calls)
        )

    def test_qwen_probe_uses_correct_region_and_disables_thinking(self):
        calls = []

        class Reply:
            status_code = 200
            content = b"{}"

            def json(self):
                return {}

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def request(self, *args, **kwargs):
                calls.append((args, kwargs))
                return Reply()

        provider = ProviderClient(Client)
        provider.verify("qwen", KEY, QWEN_MODELS[0], {"region": "us-east-1"})
        self.assertEqual(
            calls[0][0][1],
            "https://dashscope-us.aliyuncs.com/compatible-mode/v1/chat/completions",
        )
        self.assertFalse(calls[0][1]["json"]["enable_thinking"])
        self.assertNotIn("thinking", calls[0][1]["json"])

    def test_connection_identifier_changes_without_exposing_session_or_key(self):
        ai = self.service()
        sid, status = ai.connect("", "qwen", KEY)
        first = status["connection_id"]
        self.assertNotEqual(first, sid)
        ai.select(sid, "fixture-vision")
        self.assertEqual(ai.status(sid)["connection_id"], first)
        sid, status = ai.connect(sid, "qwen", KEY)
        self.assertNotEqual(status["connection_id"], first)
        self.assertNotIn(KEY, json.dumps(status))
        self.assertNotIn(sid, json.dumps(status))

    def test_kimi_live_capabilities_override_curated_defaults(self):
        class Reply:
            status_code = 200
            content = b"{}"

            def json(self):
                return {
                    "data": [
                        {
                            "id": "kimi-k3.1-pro",
                            "supports_image_in": False,
                            "supports_tool_calls": False,
                        },
                        {"id": "kimi-k3-next", "supports_reasoning": True},
                    ]
                }

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def request(self, *args, **kwargs):
                return Reply()

        models = ProviderClient(Client).models("kimi", KEY)
        self.assertFalse(models[0]["vision"])
        self.assertFalse(models[0]["tools"])
        self.assertTrue(models[1]["thinking_required"])

    def test_restored_thread_uses_current_model_capabilities(self):
        ai = self.service()
        sid, _ = ai.connect("", "kimi", KEY)
        ai.select(sid, "fixture-vision")
        t = ai.new_thread(sid, "tracking")
        state = ai.session(sid, True)
        state["selected"]["vision"] = False
        self.assertFalse(ai._thread(state, t["id"])["model"]["vision"])
        state["selected"]["thinking_required"] = True
        self.assertTrue(ai._thread(state, t["id"])["model"]["thinking_required"])


if __name__ == "__main__":
    unittest.main()
