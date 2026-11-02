import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_providers import AIError
from ai_tools import calculate
from ai_service import AIService
from ai_fixture_support import FakeProvider, FakeSimulations, FakeSerial


class CalculatorContextTests(unittest.TestCase):
    def test_supported_mathematical_and_scalar_code_formats(self):
        for expression in (
            "2^3",
            "2 .^ 3",
            "```python\nmath.sqrt(64)\n```",
            "`np.sqrt(64)`",
            "２ × ４",
            "16 ÷ 2",
            "L=4; C=16; f=math.sqrt(L*C); print(f)",
        ):
            with self.subTest(expression=expression):
                self.assertEqual(calculate(expression)["value"], 8)
        self.assertAlmostEqual(calculate("sin(π/2)")["value"], 1)

    def test_scripts_commands_and_unsafe_expressions_still_rejected(self):
        for expression in (
            'open_system("x")',
            'new_system("x")',
            'sim("x")',
            "import os",
            'x = __import__("os")',
            'os.system("whoami")',
            "(1).__class__",
            "[1,2]",
            "pi=1",
            "sqrt=2",
            "while True: pass",
            "1/0",
            "x=1e309",
            "2**100000",
        ):
            with self.subTest(expression=expression), self.assertRaises(AIError):
                calculate(expression)

    def test_general_tools_are_independent_of_project_simulation(self):
        provider = FakeProvider()
        ai = AIService(FakeSimulations(), FakeSerial(), provider)
        sid, _ = ai.connect("", "deepseek", "fixture-context-key-not-real")
        ai.select(sid, "fixture-vision")
        for scope in ("general", "tracking", "capacitance"):
            thread = ai.new_thread(sid, scope)
            s = ai.send(
                sid,
                {
                    "thread_id": thread["id"],
                    "text": "打开Simulink",
                    "allow_tools": True,
                },
            )
            list(ai.events(ai.job(sid, s["job_id"])))
            tools = {t["function"]["name"] for t in provider.payloads[-1]["tools"]}
            self.assertIn("open_project_model", tools)
            self.assertEqual("prepare_simulation" in tools, scope == "tracking")
            draft = ai.thread(sid, thread["id"])["messages"][-1]["tools"][0]["result"][
                "draft"
            ]
            self.assertEqual(
                draft["control"],
                {"general": "blank", "tracking": "pi", "capacitance": "capacitance"}[
                    scope
                ],
            )

    def test_identical_failed_calculation_is_not_reexecuted(self):
        class RepeatProvider(FakeProvider):
            def stream(self, provider, key, payload, stop, close, connection=None):
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-" + str(len(payload["messages"])),
                                        "type": "function",
                                        "function": {
                                            "name": "engineering_calculator",
                                            "arguments": json.dumps(
                                                {"expression": "1/("}
                                            ),
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }

        ai = AIService(FakeSimulations(), FakeSerial(), RepeatProvider())
        sid, _ = ai.connect("", "deepseek", "fixture-repeat-key-not-real")
        ai.select(sid, "fixture-vision")
        thread = ai.new_thread(sid, "general")
        with patch.object(ai.tools, "run", wraps=ai.tools.run) as calls:
            s = ai.send(
                sid, {"thread_id": thread["id"], "text": "计算", "allow_tools": True}
            )
            list(ai.events(ai.job(sid, s["job_id"])))
        self.assertEqual(calls.call_count, 1)
        cards = ai.thread(sid, thread["id"])["messages"][-1]["tools"]
        self.assertEqual(len(cards), 4)
        self.assertTrue(cards[1]["result"]["repeated"])
        self.assertIn("hint", cards[1]["result"])


if __name__ == "__main__":
    unittest.main()
