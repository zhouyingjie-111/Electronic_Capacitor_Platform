import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import zipfile

from werkzeug.datastructures import FileStorage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from training_runner import TrainingRunner, TrainingError, preview_csv, REQUIRED_COLUMNS


def csv_bytes():
    lines = [",".join(REQUIRED_COLUMNS)]
    for index, duration in enumerate((3e-5, 6e-5, 9e-5, 4e-5, 2e-5, 5e-5)):
        lines.append(f"{18+index},{duration},{70+index},190,{40+index},220,.1,.4,.2,.3")
    return ("\n".join(lines) + "\n").encode()


class StubRunner(TrainingRunner):
    def __init__(self, root, compile_fails=False):
        super().__init__(root, Path(root) / "jobs", python=sys.executable)
        self.default_csv.parent.mkdir(parents=True)
        self.default_csv.write_bytes(csv_bytes())
        self.commands = []
        self.compile_fails = compile_fails

    def _process(self, job, command, env):
        self.commands.append((command, dict(env)))
        if Path(command[2]).name == self.script.name:
            output = Path(env["ANN_OUTPUT_DIR"])
            (output / "ann_up_down_5in2out_full_static.onnx").write_bytes(b"model")
            (output / "metrics_summary.json").write_text(
                json.dumps(
                    {
                        "model": {"output_columns": ["DTgiven", "DSsample"]},
                        "test_metrics": {"DTgiven": {"r2": float("nan")}},
                    }
                )
            )
            self._log(job, "Epoch 250 | train=0.1 | val=0.2")
        elif Path(command[2]).name == self.compiler.name:
            if self.compile_fails:
                raise RuntimeError("compiler failed")
            folder = Path(env["ANN_MODEL_DIR"]) / "dsp_cpu_test/artifacts_cpu"
            folder.mkdir(parents=True)
            (folder / "mod.a").write_bytes(b"archive")
        elif Path(command[2]).name == self.npu_compiler.name:
            folder = Path(command[command.index("--output") + 1])
            (folder / "artifacts").mkdir(parents=True)
            (folder / "artifacts/mod.a").write_bytes(b"npu archive")
            (folder / "npu_report.json").write_text(
                json.dumps(
                    {"hardware_verified": not self.compile_fails, "hardware_layers": 3}
                )
            )
            self._log(job, "NPU hardware layers: 3")
        self._log(job, "Output directory: C:/private/project/model")


class TrainingTests(unittest.TestCase):
    def wait(self, runner, job):
        deadline = time.time() + 3
        while runner.active_id and time.time() < deadline:
            time.sleep(0.01)
        self.assertIsNone(runner.active_id)
        return runner.snapshot(job["id"])

    def test_preview_filters_40_to_60us_and_retains_source_order(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.csv"
            lines = csv_bytes().decode().splitlines()
            data = (
                "仿真文件,仿真编号,"
                + lines[0]
                + "\n"
                + "\n".join(
                    f"case{i}.csv,{i}," + row for i, row in enumerate(lines[1:])
                )
            ).encode()
            path.write_bytes(data)
            preview = preview_csv(path, validate=True)
            self.assertEqual([row[0] for row in preview["rows"]], ["19", "21", "23"])
            self.assertEqual(path.read_bytes(), data)
            self.assertNotIn("仿真文件", preview["columns"])
            self.assertNotIn("仿真编号", preview["columns"])

    def test_upload_requires_valid_csv_and_uses_generated_storage_name(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = StubRunner(folder)
            with self.assertRaises(TrainingError):
                runner.upload(
                    FileStorage(stream=io.BytesIO(b"a,b\n1,2"), filename="bad.csv"),
                    "dataset",
                )
            result = runner.upload(
                FileStorage(
                    stream=io.BytesIO(csv_bytes()), filename="../../source.csv"
                ),
                "dataset",
            )
            self.assertEqual(result["name"], "source.csv")
            self.assertTrue(
                runner.dataset(result["token"]).is_relative_to(runner.runtime)
            )
            with self.assertRaises(TrainingError):
                runner.dataset("../../source.csv")

    def test_training_stops_before_compile_and_downloads_are_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = StubRunner(folder)
            done = self.wait(runner, runner.start())
            self.assertEqual(done["state"], "completed")
            self.assertFalse(done["compilation_completed"])
            self.assertTrue(done["training_completed"])
            self.assertIsNone(done["metrics"]["test_metrics"]["DTgiven"]["r2"])
            json.dumps(done, allow_nan=False)
            self.assertNotIn("C:/private", "\n".join(done["logs"]))
            self.assertEqual(runner.default_csv.read_bytes(), csv_bytes())
            self.assertEqual(len(runner.commands), 1)
            self.assertEqual(done["logs"][-1], "模型训练完成，下面请进行编译。")
            with zipfile.ZipFile(runner.archive(done["id"], "model")) as archive:
                self.assertTrue(
                    all(name.startswith("output/") for name in archive.namelist())
                )
            with self.assertRaises(TrainingError):
                runner.archive(done["id"], "compile")
            self.assertEqual(
                runner.snapshot(done["id"], done["next_cursor"])["logs"], []
            )
            recovered = TrainingRunner(folder, runner.runtime, python=sys.executable)
            self.assertEqual(recovered.snapshot()["id"], done["id"])
            self.assertEqual(recovered.snapshot()["state"], "completed")
            self.assertTrue(recovered.snapshot()["has_training_output"])
            compiled = self.wait(runner, runner.start(compile_only=True))
            self.assertEqual(compiled["state"], "completed")
            env = runner.commands[-1][1]
            self.assertEqual(
                (Path(env["ANN_MODEL_DIR"]) / env["ANN_MODEL_FILE"]).read_bytes(),
                b"model",
            )
            with zipfile.ZipFile(runner.archive(compiled["id"], "compile")) as archive:
                self.assertTrue(
                    any(name.endswith("mod.a") for name in archive.namelist())
                )
                self.assertFalse(
                    any(
                        name.startswith("output/") or name.endswith(".onnx")
                        for name in archive.namelist()
                    )
                )

    def test_compilation_failure_preserves_training_and_can_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = StubRunner(folder, compile_fails=True)
            trained = self.wait(runner, runner.start())
            done = self.wait(runner, runner.start(compile_only=True))
            self.assertEqual(done["state"], "compile_failed")
            self.assertFalse(done["compilation_completed"])
            self.assertTrue(runner.archive(trained["id"], "model").is_file())
            runner.compile_fails = False
            retry = self.wait(runner, runner.start(compile_only=True))
            self.assertEqual(retry["state"], "completed")

    def test_uploaded_compile_model_does_not_execute_uploaded_code(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = StubRunner(folder)
            with self.assertRaises(TrainingError):
                runner.upload(
                    FileStorage(stream=io.BytesIO(b"print(1)"), filename="model.py"),
                    "model",
                )
            token = runner.upload(
                FileStorage(
                    stream=io.BytesIO(b"uploaded model"), filename="custom.onnx"
                ),
                "model",
            )["token"]
            done = self.wait(
                runner, runner.start(compile_token=token, compile_only=True)
            )
            self.assertEqual(done["state"], "completed")
            self.assertEqual(len(runner.commands), 2)
            self.assertEqual(
                Path(runner.commands[0][0][-1]).read_bytes(), b"uploaded model"
            )

    def test_public_logs_exclude_hardware_and_other_output_without_hiding_inputs(self):
        for line in (
            "Device : cpu",
            "CPU/DSP deployment export finished (NO NPU)",
            "INFO:ti-npu type=soft",
            "DSsample metrics",
            "  - ann_up_down_5in2out.onnx",
        ):
            self.assertIsNone(TrainingRunner._public_log(line))
        self.assertEqual(
            TrainingRunner._public_log("raw input -> predicted DTgiven"),
            "raw input -> predicted DTgiven",
        )

    def test_npu_compilation_snapshots_calibration_and_restores_download(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = StubRunner(folder)
            self.wait(runner, runner.start())
            before = runner.default_csv.read_bytes()
            done = self.wait(runner, runner.start(compile_only=True, target="npu"))
            self.assertEqual(done["state"], "completed")
            self.assertEqual(done["npu_report"]["hardware_layers"], 3)
            self.assertIn("NPU hardware layers: 3", done["logs"])
            command = runner.commands[-1][0]
            self.assertEqual(
                Path(command[command.index("--csv") + 1]).read_bytes(), before
            )
            self.assertEqual(runner.default_csv.read_bytes(), before)
            with zipfile.ZipFile(runner.archive(done["id"])) as archive:
                self.assertTrue(
                    any(
                        "dsp_npu/artifacts/mod.a" in name for name in archive.namelist()
                    )
                )
            restored = TrainingRunner(
                folder, runner.runtime, python=sys.executable
            ).snapshot(done["id"])
            self.assertEqual(restored["target"], "npu")
            self.assertIn("NPU hardware layers: 3", restored["logs"])

    def test_npu_rejects_unverified_hardware_output_and_invalid_target(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = StubRunner(folder)
            self.wait(runner, runner.start())
            runner.compile_fails = True
            done = self.wait(runner, runner.start(compile_only=True, target="npu"))
            self.assertEqual(done["state"], "compile_failed")
            self.assertFalse(done["compilation_completed"])
            with self.assertRaises(TrainingError):
                runner.archive(done["id"])
            with self.assertRaises(TrainingError):
                runner.start(compile_only=True, target="arbitrary")

    def test_restored_joint_training_matches_original_hyperparameters_and_training_function(
        self,
    ):
        import ast

        root = Path(__file__).resolve().parents[2]
        original = ast.parse(
            (root / "webapp/tests/fixtures/training_baseline.py").read_text(
                encoding="utf-8-sig"
            )
        )
        restored = ast.parse(
            (root / "project_data/Code/python/ANN_5IN2OUT_ultralight.py").read_text(
                encoding="utf-8-sig"
            )
        )
        for name in (
            "train_model",
            "split_raw_dataframe",
            "build_scaler",
            "make_feature_matrix_from_raw",
        ):
            first = next(
                node
                for node in original.body
                if isinstance(node, ast.FunctionDef) and node.name == name
            )
            second = next(
                node
                for node in restored.body
                if isinstance(node, ast.FunctionDef) and node.name == name
            )
            self.assertEqual(ast.dump(first), ast.dump(second), name)

        def settings(tree):
            return {
                node.targets[0].id: ast.dump(node.value)
                for node in tree.body
                if isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name)
            }

        first, second = settings(original), settings(restored)
        for name in (
            "EPOCHS",
            "LEARNING_RATE",
            "BATCH_SIZE",
            "RANDOM_SEED",
            "TRAINING_SEEDS",
            "HIDDEN_DIMS",
            "DROPOUT",
            "WEIGHT_DECAY",
            "EARLY_STOPPING_PATIENCE",
            "SCHEDULER_PATIENCE",
            "MODEL_OUTPUT_COLUMNS",
            "UP_TARGET_COLUMNS",
            "DOWN_TARGET_COLUMNS",
        ):
            self.assertEqual(first[name], second[name], name)


if __name__ == "__main__":
    unittest.main()
