"""Reject the C28 default software FC path even when the offload report says FC=3."""

import ast
import re
import tempfile
import unittest
from pathlib import Path

COMPILER = (
    Path(__file__).resolve().parents[2]
    / "project_data/Code/python/compile_ann_npu_ultralight.py"
)


class HardwareGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(COMPILER.read_text(encoding="utf-8"))
        function = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "verify_hardware"
        )
        namespace = {"re": re}
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]), str(COMPILER), "exec"
            ),
            namespace,
        )
        cls.verify = staticmethod(namespace["verify_hardware"])

    def fixture(self, folder, source):
        (folder / "artifacts").mkdir()
        (folder / "artifacts/tvmgen_default.h").write_text(
            "#define TVMGEN_DEFAULT_TI_NPU\n"
        )
        (folder / "artifacts/mod.a").write_bytes(b"test only")
        (folder / "compile.log").write_text("INFO: FC 3\n")
        (folder / "artifacts/lib1.c").write_text(source)

    def test_c28_software_fc_with_hardware_header_and_offload_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            self.fixture(
                work,
                "TINIE_start();\n"
                + "\n".join(
                    f"tvmgen_default_ti_npu_fullyConnected_{i}(); TINIE_fullyConnected();"
                    for i in range(3)
                ),
            )
            with self.assertRaises(RuntimeError):
                self.verify(work)

    def test_hardware_layer_state_transitions_are_required(self):
        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            self.fixture(
                work,
                "TINIE_start();\n"
                + "\n".join(
                    f"tvmgen_default_ti_npu_fullyConnectedNHWC_{i}();" for i in range(3)
                ),
            )
            with self.assertRaises(RuntimeError):
                self.verify(work)

    def test_actual_hardware_microcode_artifacts_are_accepted(self):
        work = Path(__file__).resolve().parents[1] / "runtime/npu_hardware_corrected"
        if not work.exists():
            self.skipTest("Local hardware compilation artifacts not installed")
        self.assertIn("TVMGEN_DEFAULT_TI_NPU", self.verify(work))

    def test_future_compilation_explicitly_disables_c28_software_fc(self):
        source = COMPILER.read_text(encoding="utf-8")
        # 检查赋值语义，不依赖引号和空格；格式化不应改变此硬件核验约束。
        tree = ast.parse(source)
        self.assertTrue(
            any(
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and node.value.value == "true"
                and any(
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "env"
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "FORCE_NO_TI_C28_SOFT_OFFLOAD"
                    for target in node.targets
                )
                for node in ast.walk(tree)
            )
        )


if __name__ == "__main__":
    unittest.main()
