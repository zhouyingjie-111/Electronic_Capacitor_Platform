"""配置优先级及无效输入回归，不访问硬件或收费 API。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from settings import load_config


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Windows temp paths may use an 8.3 alias; compare canonical paths.
        self.root = Path(self.temp.name).resolve()

    def write(self, value):
        (self.root / "config.json").write_text(json.dumps(value), encoding="utf-8")

    def test_defaults_are_project_relative(self):
        self.assertEqual(
            load_config(self.root, {})["DELTA_PROJECT_ROOT"],
            str(self.root / "project_data"),
        )

    def test_environment_overrides_file(self):
        self.write({"PORT": 5002, "ANN_PYTHON": "tools/python.exe"})
        result = load_config(self.root, {"PORT": "5003"})
        self.assertEqual(result["PORT"], "5003")
        self.assertEqual(result["ANN_PYTHON"], str(self.root / "tools/python.exe"))

    def test_rejects_unknown_options(self):
        self.write({"unknown": "value"})
        with self.assertRaises(ValueError):
            load_config(self.root, {})

    def test_invalid_ports(self):
        for value in (0, 65536, True, "bad", "1.5"):
            with self.subTest(value=value):
                self.write({"PORT": value})
                with self.assertRaises(ValueError):
                    load_config(self.root, {})

    def test_bad_json(self):
        for value in ("[]", "{"):
            (self.root / "config.json").write_text(value, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(self.root, {})

    def test_non_object_database_requests(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app import app

        for route in (
            "/api/db/file/record",
            "/api/db/cleanup/delete",
            "/api/db/cleanup/scan",
        ):
            self.assertEqual(app.test_client().post(route, json=[1]).status_code, 400)


class ConsoleEncodingTests(unittest.TestCase):
    def test_english_windows_console_is_reconfigured(self):
        import subprocess
        root = Path(__file__).resolve().parents[2]
        script = "import sys; sys.stdout.reconfigure(encoding='cp1252'); import settings; settings.configure(); print('网页启动检查')"
        result = subprocess.run([sys.executable, '-c', script], cwd=root, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('网页启动检查', result.stdout.decode('utf-8'))
