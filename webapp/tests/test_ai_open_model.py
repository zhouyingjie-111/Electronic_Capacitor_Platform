import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from zipfile import ZipFile
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_providers import AIError
from ai_service import AIService
from ai_fixture_support import FakeProvider, FakeSimulations, FakeSerial
from simulation_runner import SimulationRunner, SimulationRequestError


def make_model(path, callback=""):
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "simulink/blockdiagram.xml",
            '<Model><P Name="PostLoadFcn">' + callback + "</P></Model>",
        )


class OpenModelTests(unittest.TestCase):
    def test_open_worker_never_enters_simulation_pipeline(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.slx"
            make_model(source)
            before = source.read_bytes()

            class Engine:
                def __init__(self):
                    self.calls = []

                def addpath(self, *args, **kwargs):
                    pass

                def portal_simulation(self, *args, **kwargs):
                    raise AssertionError("Must never call simulation bridge")

                def portal_open_model(self, config, **kwargs):
                    self.calls.append("open_only")
                    self.config = json.loads(config)
                    return json.dumps(
                        {
                            "model": "view_test",
                            "opened": True,
                            "simulation_status": "stopped",
                            "simulation_time": 0,
                            "simulation_started": False,
                        }
                    )

            engine = Engine()
            runner = SimulationRunner(
                {"pi": {"path": source, "label": "PI 控制"}},
                Path(folder) / "runs",
                lambda: engine,
            )
            result = runner.open_model("pi")
            deadline = time.monotonic() + 3
            while (
                runner.snapshot(result["id"])["finished_at"] is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            result = runner.snapshot(result["id"])
            self.assertEqual(result["state"], "opened")
            self.assertFalse(result["simulation_started"])
            self.assertEqual(result["simulation_status"], "stopped")
            self.assertFalse(result["diagram_ready"])
            self.assertEqual(source.read_bytes(), before)
            self.assertNotEqual(Path(engine.config["model_path"]), source)
            self.assertEqual(engine.calls, ["open_only"])
            self.assertIsNone(runner.latest_id)
            self.assertIsNone(runner.active_id)

    def test_open_rejects_callbacks_unlisted_models_and_busy_runner(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.slx"
            make_model(source, "dangerous_callback")
            runner = SimulationRunner(
                {"pi": {"path": source, "label": "PI"}},
                Path(folder) / "runs",
                lambda: None,
            )
            with self.assertRaises(SimulationRequestError):
                runner.open_model("pi")
            with self.assertRaises(SimulationRequestError):
                runner.open_model("../../other")
            make_model(source)
            runner.active_id = "busy"
            with self.assertRaises(SimulationRequestError):
                runner.open_model("pi")

    def test_open_dialogue_draft_does_not_start_anything_until_clicked(self):
        class Runner(FakeSimulations):
            def open_model(self, control):
                self.calls.append(("open_only", control))
                self.jobs["view"] = {"id": "view", "state": "opened"}
                return dict(self.jobs["view"])

            def start(self, *args, **kwargs):
                raise AssertionError("Simulation is prohibited")

        runner = Runner()
        service = AIService(runner, FakeSerial(), FakeProvider())
        sid, _ = service.connect("", "deepseek", "fixture-open-model-not-real-key")
        service.select(sid, "fixture-vision")
        thread = service.new_thread(sid, "general")
        sent = service.send(
            sid,
            {
                "thread_id": thread["id"],
                "text": "仅打开 PI 模型，不运行仿真",
                "allow_tools": True,
            },
        )
        list(service.events(service.job(sid, sent["job_id"])))
        card = service.thread(sid, thread["id"])["messages"][-1]["tools"][0]
        self.assertEqual(card["name"], "open_project_model")
        self.assertEqual(runner.calls, [])
        self.assertNotIn("duration", card["result"]["draft"])
        result = service.execute_draft(sid, card["result"]["draft"]["id"])
        self.assertEqual(result["state"], "opened")
        self.assertEqual(runner.calls, [("open_only", "blank")])
        self.assertEqual(
            service.execute_draft(sid, card["result"]["draft"]["id"])["state"], "opened"
        )
        self.assertEqual(runner.calls, [("open_only", "blank"), ("open_only", "blank")])

    def test_capacitance_opens_its_own_workspace_instead_of_tracking(self):
        service = AIService(FakeSimulations(), FakeSerial(), FakeProvider())
        sid, _ = service.connect("", "deepseek", "fixture-open-model-not-real-key")
        service.select(sid, "fixture-vision")
        thread = service.new_thread(sid, "capacitance")
        state = service.session(sid, True)
        card = service.tools.run("open_project_model", {"control": "pi"}, state, thread)
        self.assertEqual(card["draft"]["control"], "capacitance")

    def test_blank_workspace_never_copies_project_model(self):
        with tempfile.TemporaryDirectory() as folder:

            class Engine:
                def addpath(self, *args, **kwargs):
                    pass

                def portal_open_model(self, config, **kwargs):
                    self.config = json.loads(config)
                    return json.dumps(
                        {
                            "model": self.config["model_name"],
                            "opened": True,
                            "simulation_status": "stopped",
                            "simulation_time": 0,
                            "simulation_started": False,
                        }
                    )

            engine = Engine()
            runner = SimulationRunner({}, Path(folder), lambda: engine)
            for control in ("blank", "capacitance"):
                job = runner.open_model(control)
                deadline = time.monotonic() + 3
                while (
                    runner.snapshot(job["id"])["finished_at"] is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                self.assertEqual(runner.snapshot(job["id"])["state"], "opened")
                self.assertEqual(engine.config["model_path"], "")
                self.assertTrue(runner.snapshot(job["id"])["blank_workspace"])
            self.assertFalse(list(Path(folder).rglob("*.slx")))

    def test_closed_matlab_connection_is_replaced_when_opening_again(self):
        with tempfile.TemporaryDirectory() as folder:

            class Closed:
                def eval(self, *args, **kwargs):
                    raise RuntimeError("Engine closed")

            class Engine:
                def addpath(self, *args, **kwargs):
                    pass

                def portal_open_model(self, config, **kwargs):
                    return json.dumps(
                        {
                            "model": "new_workspace",
                            "opened": True,
                            "simulation_status": "stopped",
                            "simulation_time": 0,
                            "simulation_started": False,
                        }
                    )

            replacement = Engine()
            runner = SimulationRunner({}, Path(folder), lambda: replacement)
            runner.engine = Closed()
            job = runner.open_model("blank")
            deadline = time.monotonic() + 3
            while (
                runner.snapshot(job["id"])["finished_at"] is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertEqual(runner.snapshot(job["id"])["state"], "opened")
            self.assertIs(runner.engine, replacement)


if __name__ == "__main__":
    unittest.main()
