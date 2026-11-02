import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capacitance_data import inventory, unpack_dataset, gain_overrides
from capacitance_training import CapTrainingRunner
from training_runner import TrainingError
from scope_waveforms import read_waveform
from simulation_runner import SimulationRunner, SimulationRequestError
from test_simulation_runner import FakeEngine

LABELS = {"0000": [505.2] * 4 + [0.32] * 4}


def csv_text(unit):
    return (
        "Sample Interval,0.000001\nRecord Length,100000\nVertical Units,"
        + unit
        + "\nTIME,CH\n"
        + "\n".join(f"{i*1e-6},1" for i in range(12))
        + "\n"
    )


def dataset_zip(extra=None):
    raw = io.BytesIO()
    with ZipFile(raw, "w") as z:
        for channel in (1, 3, 4):
            z.writestr(
                f"scope/tek0000CH{channel}-10ms.csv",
                csv_text("V" if channel == 1 else "A"),
            )
        if extra:
            z.writestr(*extra)
    return raw.getvalue()


class CapacitanceTests(unittest.TestCase):
    def test_scope_dataset_preview_and_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            preview, labels = unpack_dataset(
                dataset_zip(), Path(folder) / "data", LABELS
            )
            self.assertEqual(len(preview["files"]), 3)
            self.assertEqual(len(preview["rows"]), 8)
            self.assertEqual(preview["labels"], LABELS)
            self.assertEqual(preview["columns"][1], "幅值 / V")
            current = inventory(Path(folder) / "data", labels, "tek0000CH3-10ms.csv")
            self.assertEqual(current["columns"][1], "幅值 / A")
            with self.assertRaises(TrainingError):
                inventory(Path(folder) / "data", labels, "../outside.csv")

    def test_dataset_zip_traversal_labels_and_unexecuted_attachments(self):
        with tempfile.TemporaryDirectory() as folder:
            for case, extra in enumerate([("../outside.csv", "bad"), ("labels.json", '{"0000":[1]}')]):
                with self.assertRaises(TrainingError):
                    unpack_dataset(
                        dataset_zip(extra), Path(folder) / f"invalid-{case}", LABELS
                    )
            dest = Path(folder) / "ok"
            unpack_dataset(
                dataset_zip(("run.py", "raise RuntimeError()")), dest, LABELS
            )
            self.assertFalse((dest / "run.py").exists())

    def test_model_input_waveform_id_and_unsupported_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p / "model_input_1.bin").write_bytes(struct.pack("<dddd", 0, 2, 0.01, 3))
            (p / "index.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "model_input",
                            "name": "模型输入",
                            "start": 0,
                            "end": 0.01,
                            "channels": [
                                {
                                    "file": "model_input_1.bin",
                                    "id": 1,
                                    "label": "CH1",
                                    "group": 1,
                                    "count": 2,
                                }
                            ],
                        }
                    ]
                )
            )
            self.assertEqual(
                read_waveform(p, "model_input")["channels"][0]["values"], [2, 3]
            )
            with self.assertRaises(ValueError):
                read_waveform(p, "../model_input")

    def test_cap_model_uses_single_scope_and_only_pulse_parameters(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            source = p / "cnn.slx"
            source.write_bytes(b"source")
            engine = FakeEngine()
            original_bridge = engine.portal_simulation

            def single_scope(action, config, **kwargs):
                result = original_bridge(action, config, **kwargs)
                if action == "prepare":
                    value = json.loads(result)
                    value["scopes"] = {"id": "model_input", "name": "模型输入"}
                    return json.dumps(value)
                return result

            engine.portal_simulation = single_scope
            runner = SimulationRunner(
                {
                    "capacitance": {
                        "path": source,
                        "label": "电容辨识",
                        "scopes": [{"id": "model_input", "name": "模型输入"}],
                    }
                },
                p / "runs",
                lambda: engine,
            )
            with self.assertRaises(SimulationRequestError):
                runner.start("capacitance", 0.01, {"P_V": 0.2})
            job = runner.start("capacitance", 0.01)
            deadline = time.monotonic() + 5
            while (
                runner.snapshot(job["id"])["finished_at"] is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            self.assertEqual(runner.snapshot(job["id"])["state"], "completed")
            self.assertEqual(
                runner.snapshot(job["id"])["scopes"],
                [{"id": "model_input", "name": "模型输入"}],
            )
            self.assertEqual(
                engine.config["scopes"], [{"id": "model_input", "name": "模型输入"}]
            )
            self.assertEqual(engine.config["parameters"], {"P": 22, "TP": 50e-6})
            self.assertEqual(engine.config["block_parameters"], [])
            self.assertEqual(source.read_bytes(), b"source")

    def test_prepare_job_snapshots_data_and_preserves_training_parameters(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            code = root / "Code/python/scope_cap"
            code.mkdir(parents=True)
            data = root / "data"
            unpack_dataset(dataset_zip(), data, LABELS)
            config = {
                "data_dir": "../../../data",
                "labels": LABELS,
                "epochs": 300,
                "learning_rate": 0.001,
            }
            (code / "config.json").write_text(json.dumps(config))
            runner = CapTrainingRunner(root, root / "runtime", sys.executable)
            calls = []

            def process(job, args):
                calls.append(args)
                (job["output"] / "dataset.npz").write_bytes(b"prepared")

            with patch.object(runner, "_process", side_effect=process):
                job = runner.start("prepare")
                deadline = time.monotonic() + 3
                while (
                    runner.snapshot(job["id"])["state"] in ("queued", "preparing")
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
            self.assertEqual(runner.snapshot(job["id"])["state"], "completed")
            self.assertIn("--prepare-only", calls[0])
            saved = json.loads(
                (runner.jobs[job["id"]]["folder"] / "config.json").read_text()
            )
            self.assertEqual(saved["epochs"], 300)
            self.assertEqual(saved["learning_rate"], 0.001)
            self.assertNotEqual(saved["data_dir"], str(data))
            self.assertEqual(json.loads((code / "config.json").read_text()), config)
            self.assertTrue(runner.archive(job["id"]).is_file())

    def test_failed_job_does_not_offer_successful_download(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            code = root / "Code/python/scope_cap"
            code.mkdir(parents=True)
            unpack_dataset(dataset_zip(), root / "data", LABELS)
            (code / "config.json").write_text(
                json.dumps({"data_dir": "../../../data", "labels": LABELS})
            )
            runner = CapTrainingRunner(root, root / "runtime", sys.executable)
            with patch.object(
                runner, "_process", side_effect=RuntimeError("test failure")
            ):
                job = runner.start("training")
                deadline = time.monotonic() + 3
                while (
                    runner.snapshot(job["id"])["state"] in ("queued", "training")
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
            self.assertEqual(runner.snapshot(job["id"])["state"], "failed")
            with self.assertRaises(TrainingError):
                runner.archive(job["id"])


if __name__ == "__main__":
    unittest.main()
