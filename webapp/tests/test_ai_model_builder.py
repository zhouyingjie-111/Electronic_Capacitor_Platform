import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_model_builder import validate_plan, operations
from ai_providers import AIError
from ai_service import AIService
from simulation_runner import SimulationRunner, SimulationRequestError
from ai_fixture_support import FakeProvider, FakeSerial
from model_build_fixture import buck_plan


class BuilderTests(unittest.TestCase):
    def test_valid_graph_and_operation_refs(self):
        plan = validate_plan(buck_plan())
        ops = operations(plan)
        self.assertEqual(len(plan["blocks"]), 11)
        self.assertEqual(len(plan["connections"]), 13)
        self.assertEqual(ops[-1]["target"], "#InductorCurrent.y1 -> #VoltageCurrent.u2")

    def test_rejects_executable_parameters_and_paths(self):
        for value in (
            "system('command')",
            "eval(1)",
            "../model",
            "1;system(1)",
            "Inf",
            "1/0",
            "1e309",
            "unknown",
            "[1 2]",
        ):
            plan = buck_plan()
            plan["blocks"][0]["parameters"][0]["value"] = value
            with self.subTest(value=value), self.assertRaises(AIError):
                validate_plan(plan)
        for kind in ("MATLABFcn", "S-Function", "SubSystem", "simulink/Sinks/Scope"):
            plan = buck_plan()
            plan["blocks"][0]["type"] = kind
            with self.assertRaises(AIError):
                validate_plan(plan)
        plan = buck_plan()
        plan["blocks"][0]["parameters"].append({"name": "OpenFcn", "value": "bad"})
        with self.assertRaises(AIError):
            validate_plan(plan)
        plan = buck_plan()
        plan["variables"][0]["name"] = "end"
        with self.assertRaises(AIError):
            validate_plan(plan)

    def test_rejects_duplicate_dangling_and_bad_ports(self):
        for change in ("missing", "duplicate", "unknown", "port", "name", "count"):
            plan = buck_plan()
            if change == "missing":
                plan["connections"].pop()
            if change == "duplicate":
                plan["connections"].append(dict(plan["connections"][0]))
            if change == "unknown":
                plan["connections"][0]["source"] = "absent"
            if change == "port":
                plan["connections"][0]["target_port"] = True
            if change == "name":
                plan["blocks"][0]["name"] = "../../outside"
            if change == "count":
                plan["blocks"][0]["parameters"].append({"name": "Value", "value": "3"})
            with self.subTest(change=change), self.assertRaises(AIError):
                validate_plan(plan)

    def test_new_model_worker_and_authenticated_persistent_download(self):
        with tempfile.TemporaryDirectory() as folder:

            class Engine:
                def which(self, *args, **kwargs):
                    return "installed"

                def addpath(self, *args, **kwargs):
                    pass

                def portal_simulation(self, *args, **kwargs):
                    raise AssertionError("Must not simulate")

                def portal_build_model(self, raw, **kwargs):
                    self.config = json.loads(raw)
                    with ZipFile(self.config["model_path"], "w") as z:
                        z.writestr("model.xml", "<Model/>")
                    return json.dumps(
                        {
                            "built": True,
                            "simulation_started": False,
                            "simulation_status": "stopped",
                            "structure_report": "11 blocks",
                            "check_report": "status: healthy",
                        }
                    )

            engine = Engine()
            runner = SimulationRunner({}, Path(folder), lambda: engine)
            service = AIService(runner, FakeSerial(), FakeProvider())
            sid, _ = service.connect("", "deepseek", "builder-test-key")
            service.select(sid, "fixture-vision")
            thread = service.new_thread(sid, "general")
            state = service.session(sid, True)
            result = service.tools.run(
                "prepare_model_build", buck_plan(), state, thread
            )
            self.assertIsNone(runner.active_id)
            self.assertEqual(runner.jobs, {})
            draft = result["draft"]
            state["threads"][thread["id"]]["public"].append(
                {
                    "id": "answer",
                    "role": "assistant",
                    "content": "",
                    "tools": [{"name": "prepare_model_build", "result": result}],
                }
            )
            job = service.execute_draft(sid, draft["id"])
            deadline = time.monotonic() + 3
            while (
                runner.snapshot(job["id"])["finished_at"] is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            job = service.software_task(sid, job["id"])
            self.assertEqual(job["state"], "built")
            self.assertFalse(job["simulation_started"])
            self.assertIsNone(runner.latest_id)
            self.assertIsNone(runner.active_id)
            item = service.generated_file(sid, job["file"]["id"])
            self.assertTrue(item["raw"].startswith(b"PK"))
            self.assertTrue(item["name"].endswith(".slx"))
            self.assertEqual(len(state["artifacts"]), 1)
            service.software_task(sid, job["id"])
            self.assertEqual(len(state["artifacts"]), 1)
            self.assertEqual(service.execute_draft(sid, draft["id"])["id"], job["id"])
            other, _ = service.connect("", "deepseek", "other-builder-key")
            service.select(other, "fixture-vision")
            with self.assertRaises(AIError):
                service.software_task(other, job["id"])
            with self.assertRaises(AIError):
                service.generated_file(other, job["file"]["id"])
            self.assertIn(
                "file",
                service.thread(sid, thread["id"])["messages"][-1]["tools"][0]["result"][
                    "draft"
                ],
            )
            restored = AIService(runner, FakeSerial(), FakeProvider(), service.store)
            restored_sid, _ = restored.connect("", "deepseek", "builder-test-key")
            restored.select(restored_sid, "fixture-vision")
            saved = restored.thread(restored_sid, thread["id"])["messages"][-1][
                "tools"
            ][0]["result"]["draft"]["file"]
            self.assertEqual(
                restored.generated_file(restored_sid, saved["id"])["raw"], item["raw"]
            )

    def test_partial_build_never_claims_success(self):
        with tempfile.TemporaryDirectory() as folder:

            class Engine:
                def which(self, *a, **k):
                    return "installed"

                def addpath(self, *a, **k):
                    pass

                def portal_build_model(self, *a, **k):
                    return json.dumps({"built": False, "check_report": "unconnected"})

            runner = SimulationRunner({}, Path(folder), lambda: Engine())
            job = runner.build_model(validate_plan(buck_plan()))
            deadline = time.monotonic() + 3
            while (
                runner.snapshot(job["id"])["finished_at"] is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertEqual(runner.snapshot(job["id"])["state"], "failed")
            self.assertIsNone(runner.active_id)
            runner.active_id = "busy"
            with self.assertRaises(SimulationRequestError):
                runner.build_model(validate_plan(buck_plan()))


if __name__ == "__main__":
    unittest.main()
