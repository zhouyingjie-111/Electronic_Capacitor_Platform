import json
import struct
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from simulation_runner import SimulationRunner, SimulationRequestError


class FakeEngine:
    def __init__(self, fail=False, wait=False, wait_prepare=False, truncated=False):
        self.calls = []
        self.counter = 0
        self.fail = fail
        self.wait = wait
        self.wait_prepare = wait_prepare
        self.truncated = truncated
        self.preparing = threading.Event()
        self.finish_prepare = threading.Event()
        self.initializing = threading.Event()
        self.resume = threading.Event()

    def addpath(self, *args, **kwargs):
        pass

    def portal_simulation(self, action, config, **kwargs):
        self.calls.append(action)
        if action == "prepare":
            self.config = json.loads(config)
            diagram = Path(self.config["diagram_path"])
            if self.truncated:
                diagram.write_bytes(b"\x89PNG\r\n\x1a\n")
            else:
                Image.new("RGB", (32, 20), "white").save(diagram)
            self.preparing.set()
            if self.wait_prepare:
                self.finish_prepare.wait(3)
            return json.dumps(
                {
                    "start_time": 0,
                    "stop_time": self.config["duration"] or 0.8,
                    "scopes": [
                        {"id": "voltage", "name": "电压"},
                        {"id": "current", "name": "电流"},
                        {"id": "switch", "name": "开关"},
                    ],
                }
            )
        if action == "start":
            self.initializing.set()
            if self.wait:
                self.resume.wait(3)
            if self.fail:
                raise RuntimeError(r"C:\private\model.slx initialization failed")
        if action == "status":
            self.counter += 1
            end_time = self.config["duration"] or 0.8
            return json.dumps(
                {
                    "state": "running" if self.counter == 1 else "completed",
                    "simulation_time": end_time / 2 if self.counter == 1 else end_time,
                    "start_time": 0,
                    "stop_time": end_time,
                }
            )
        if action == "open_scope":
            self.opened = json.loads(config)
            return json.dumps({"opened": True})
        if action == "export_waveforms":
            folder = Path(self.config["waveform_dir"])
            folder.mkdir()
            (folder / "voltage_1.bin").write_bytes(struct.pack("<dddd", 0, 0, 0.01, 22))
            (folder / "index.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "voltage",
                            "name": "电压",
                            "start": 0,
                            "end": 0.01,
                            "channels": [
                                {
                                    "id": 1,
                                    "label": "V1",
                                    "group": 1,
                                    "count": 2,
                                    "file": "voltage_1.bin",
                                }
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            return json.dumps({"ready": True})


class SimulationRunnerTests(unittest.TestCase):
    def make_runner(self, folder, engine):
        model = Path(folder) / "test.slx"
        model.write_bytes(b"original-model")
        return SimulationRunner(
            {"pi": {"path": model, "label": "PI 控制"}},
            Path(folder) / "runs",
            lambda: engine,
        )

    def wait_until_finished(self, runner, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            snapshot = runner.snapshot(job_id)
            if snapshot["finished_at"] is not None:
                return snapshot
            time.sleep(0.02)
        self.fail("worker did not complete")

    def test_real_time_ratio_and_success_and_source_preservation(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = FakeEngine()
            runner = self.make_runner(folder, engine)
            job = runner.start("pi")
            self.assertNotIn("_folder", job)
            self.assertEqual(runner._progress(0.4, 0, 0.8), 50)
            self.assertEqual(runner._progress(0.8, 0, 0.8), 99.9)
            done = self.wait_until_finished(runner, job["id"])
            self.assertEqual(done["state"], "completed")
            self.assertEqual(done["progress"], 100)
            self.assertTrue(done["diagram_ready"])
            self.assertEqual((done["diagram_width"], done["diagram_height"]), (32, 20))
            self.assertTrue(done["diagram_version"])
            self.assertEqual(
                (Path(folder) / "test.slx").read_bytes(), b"original-model"
            )
            self.assertEqual(
                engine.calls,
                ["prepare", "start", "status", "status", "export_waveforms", "release"],
            )
            self.assertTrue(done["waveforms_ready"])
            self.assertEqual(
                runner.waveform(job["id"], "voltage")["channels"][0]["values"], [0, 22]
            )
            self.assertNotIn(folder, json.dumps(done))

    def test_failure_never_reports_success_or_leaks_file_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = self.make_runner(folder, FakeEngine(fail=True))
            with self.assertLogs("simulation_runner", level="ERROR"):
                job = runner.start("pi")
                done = self.wait_until_finished(runner, job["id"])
            self.assertEqual(done["state"], "failed")
            self.assertIsNone(done["progress"])
            self.assertNotIn("private", json.dumps(done))
            self.assertIn("初始化失败", done["message"])

    def test_serial_execution_and_cooperative_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = FakeEngine(wait=True)
            runner = self.make_runner(folder, engine)
            job = runner.start("pi")
            self.assertTrue(engine.initializing.wait(2))
            with self.assertRaises(SimulationRequestError) as busy:
                runner.start("pi")
            self.assertEqual(busy.exception.status, 409)
            self.assertEqual(runner.stop(job["id"])["state"], "stopping")
            engine.resume.set()
            done = self.wait_until_finished(runner, job["id"])
            self.assertEqual(done["state"], "stopped")
            self.assertNotEqual(done["progress"], 100)
            self.assertIn("stop", engine.calls)

    def test_no_placeholder_or_user_path_can_be_run(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = self.make_runner(folder, FakeEngine())
            for control in ("custom", "../test.slx", "ann"):
                with self.assertRaises(SimulationRequestError):
                    runner.start(control)
            self.assertIsNone(runner.snapshot())

    def test_custom_parameters_and_ann_code_are_forwarded_to_isolated_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = FakeEngine()
            runner = self.make_runner(folder, engine)
            runner.models["custom"] = runner.models["pi"]
            job = runner.start("custom", 0.01, {"D_rise": 3, "D_fall": 4, "P": 23})
            done = self.wait_until_finished(runner, job["id"])
            self.assertEqual(engine.config["parameters"]["P"], 23)
            self.assertEqual(engine.config["parameters"]["D_rise"], 3)
            self.assertNotEqual(
                Path(engine.config["model_path"]), Path(folder) / "test.slx"
            )
            self.assertEqual(
                Path(engine.config["model_path"]).read_bytes(), b"original-model"
            )
            self.assertTrue(done["scopes_ready"])
            self.assertEqual(len(done["scopes"]), 3)
            runner.open_scope(job["id"], "current")
            self.assertEqual(engine.opened["name"], "电流")
            self.assertTrue(engine.opened["model"].startswith("portal_custom_"))
            with self.assertRaises(SimulationRequestError):
                runner.open_scope(job["id"], "../unknown")
            runner.models["ann"] = runner.models["pi"]
            code = "function y = f(p,t,vc,vg,d)\ny = p*0;\nend"
            job = runner.start("ann", 0.01, ann_code=code)
            self.wait_until_finished(runner, job["id"])
            self.assertEqual(
                Path(engine.config["ann_code_path"]).read_text(encoding="utf-8"), code
            )

    def test_invalid_parameters_or_function_never_start_matlab(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = FakeEngine()
            runner = self.make_runner(folder, engine)
            runner.models["ann"] = runner.models["custom"] = runner.models["pi"]
            for parameters in (
                [],
                {"TP": 0},
                {"P": float("nan")},
                {"P_V": -1},
                {"P_I": True},
                {"P": 10**400},
                {"path": "other.slx"},
                {"D_rise": 1},
            ):
                with (
                    self.subTest(parameters=parameters),
                    self.assertRaises(SimulationRequestError),
                ):
                    runner.start("pi", 0.01, parameters)
            with self.assertRaises(SimulationRequestError):
                runner.start("custom", 0.01)
            for code in (
                "",
                "function [a,b] = f(p,t,v,g,d)\nend",
                "function y = f(p)\nend",
                42,
            ):
                with self.subTest(code=code), self.assertRaises(SimulationRequestError):
                    runner.start("ann", 0.01, ann_code=code)
            self.assertEqual(engine.calls, [])

    def test_duration_is_forwarded_without_changing_model(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = FakeEngine()
            runner = self.make_runner(folder, engine)
            job = runner.start("pi", 0.002)
            done = self.wait_until_finished(runner, job["id"])
            self.assertEqual(engine.config["duration"], 0.002)
            self.assertEqual(done["requested_duration"], 0.002)
            self.assertEqual(done["stop_time"], 0.002)
            self.assertEqual(done["state"], "completed")
            self.assertEqual(
                (Path(folder) / "test.slx").read_bytes(), b"original-model"
            )

    def test_invalid_duration_never_starts_matlab(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = FakeEngine()
            runner = self.make_runner(folder, engine)
            for invalid in (
                0,
                -1,
                float("nan"),
                float("inf"),
                True,
                "0.8",
                [],
                {},
                10**400,
            ):
                with self.subTest(value=repr(invalid)):
                    with self.assertRaises(SimulationRequestError):
                        runner.start("pi", invalid)
            self.assertEqual(engine.calls, [])
            self.assertIsNone(runner.snapshot())

    def test_existing_png_is_not_published_while_export_is_in_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = FakeEngine(wait_prepare=True)
            runner = self.make_runner(folder, engine)
            job = runner.start("pi")
            self.assertTrue(engine.preparing.wait(2))
            self.assertTrue(Path(engine.config["diagram_path"]).exists())
            self.assertFalse(runner.snapshot(job["id"])["diagram_ready"])
            with self.assertRaises(SimulationRequestError):
                runner.diagram(job["id"])
            engine.finish_prepare.set()
            done = self.wait_until_finished(runner, job["id"])
            self.assertTrue(done["diagram_ready"])
            self.assertTrue(runner.diagram(job["id"]).is_file())

    def test_truncated_png_is_rejected_instead_of_displayed(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = FakeEngine(truncated=True)
            runner = self.make_runner(folder, engine)
            with self.assertLogs("simulation_runner", level="ERROR"):
                job = runner.start("pi")
                done = self.wait_until_finished(runner, job["id"])
            self.assertFalse(done["diagram_ready"])
            self.assertEqual(done["state"], "failed")
            self.assertNotIn("start", engine.calls)
