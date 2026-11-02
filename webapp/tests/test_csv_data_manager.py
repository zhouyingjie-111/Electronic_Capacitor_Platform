"""在临时数据上验证维护功能，不修改项目数据。"""

import builtins
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "webapp"))
from csv_data_manager import CSVDataManager


class CSVDataManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("InputData", "OutputData", "Code"):
            (self.root / name).mkdir()
        self.path = self.root / "InputData/sample.csv"
        self.path.write_text(
            '编号,值,备注\n1,0.25,"两行\n文字"\n2,,hello\n3,4,world\n',
            encoding="utf-8-sig",
            newline="",
        )
        self.manager = CSVDataManager(self.root)

    def test_stats_without_numpy_or_pandas_and_real_parse_failures(self):
        (self.root / "OutputData/good.CSV").write_bytes("列,值\n中文,2\n".encode("gbk"))
        (self.root / "OutputData/broken.csv").write_text(
            'a,b\n"unclosed', encoding="utf-8"
        )
        (self.root / "Code/ignored.csv").write_text("broken", encoding="utf-8")
        original_import = builtins.__import__

        def without_numeric(name, *args, **kwargs):
            if name.split(".")[0] in {"numpy", "pandas"}:
                raise ImportError("numpy.core.multiarray failed to import")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=without_numeric):
            stats = self.manager.get_file_stats()
            problems = self.manager.check_corrupted_files()
        self.assertEqual(stats["InputData"]["total_rows"], 3)
        self.assertEqual(stats["OutputData"]["file_count"], 2)
        self.assertEqual(stats["OutputData"]["total_rows"], 1)
        self.assertEqual(stats["OutputData"]["error_count"], 1)
        self.assertEqual([p["path"] for p in problems], ["OutputData/broken.csv"])
        failed = next(f for f in stats["OutputData"]["files"] if "error" in f)
        self.assertIsNone(failed["rows"])

    def test_search_pagination_keeps_original_row_index(self):
        result = self.manager.get_csv_data(
            self.path, page=2, per_page=1, search_query="o"
        )
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["data"][0]["_index"], 2)
        self.assertEqual(result["data"][0]["值"], 4)
        self.assertIsNone(self.manager.get_csv_data(self.path)["data"][1]["值"])
        self.assertIn("error", self.manager.get_csv_data(self.path, page=0))

    def test_edit_add_delete_preserve_other_cells_and_original_header(self):
        self.assertTrue(
            self.manager.update_record(self.path, 1, {"值": "0.00120"})["success"]
        )
        self.assertTrue(
            self.manager.add_record(self.path, {"编号": "4", "备注": "新增"})["success"]
        )
        self.assertFalse(self.manager.delete_record(self.path, -1)["success"])
        self.assertTrue(self.manager.delete_record(self.path, 2)["success"])
        header, _, rows, _ = self.manager._read(self.path)
        self.assertEqual(header, ["编号", "值", "备注"])
        self.assertEqual(rows[0], ["1", "0.25", "两行\n文字"])
        self.assertEqual(rows[1][1], "0.00120")
        self.assertEqual(rows[-1], ["4", "", "新增"])

    def test_import_validates_before_writing_and_creates_backup(self):
        original = self.path.read_bytes()
        invalid = self.manager.import_csv(self.path, io.BytesIO(b"other\n1\n"))
        self.assertFalse(invalid["success"])
        self.assertEqual(self.path.read_bytes(), original)
        uploaded = io.BytesIO("备注,值,编号\n新数据,2.50,7\n".encode("utf-8"))
        self.assertTrue(self.manager.import_csv(self.path, uploaded)["success"])
        self.assertEqual(self.manager._read(self.path)[2], [["7", "2.50", "新数据"]])
        self.assertEqual(
            next(self.path.parent.glob("*.backup_*")).read_bytes(), original
        )

    def test_recursive_scope_and_reject_outside_delete(self):
        nested = self.root / "OutputData/model"
        nested.mkdir()
        (nested / "metrics.csv").write_text("a\n1\n", encoding="utf-8")
        outside = self.root / "Code/keep.csv"
        outside.write_text("a\n1\n", encoding="utf-8")
        for path in (self.path, outside, nested / "metrics.csv"):
            os.utime(path, (1, 1))
        files = self.manager.scan_old_files(30)
        self.assertEqual(len(files), 2)
        self.assertIsNone(self.manager.get_csv_file_path("InputData/../Code/keep.csv"))
        self.assertEqual(
            self.manager.get_csv_file_path("OutputData/model/metrics.csv"),
            str(nested / "metrics.csv"),
        )
        result = self.manager.delete_files([str(outside)])
        self.assertEqual(len(result["failed"]), 1)
        self.assertTrue(outside.exists())

    def test_empty_directories_and_duplicate_or_blank_columns(self):
        self.path.write_text("a,,a,Unnamed: 1\n1,2,3,4\n", encoding="utf-8")
        info = self.manager.get_csv_info(self.path)
        self.assertEqual(len(set(info["columns"])), 4)
        stats = self.manager.get_file_stats()
        self.assertEqual(stats["OutputData"]["file_count"], 0)
        self.assertEqual(stats["OutputData"]["total_rows"], 0)

    def test_both_flask_entry_points_serve_maintenance_apis(self):
        for key, path in [("release", ROOT / "webapp/app.py")]:
            with self.subTest(entry=key):
                spec = importlib.util.spec_from_file_location("csv_test_" + key, path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.app.extensions["csv_data_manager"] = self.manager
                client = module.app.test_client()
                self.assertEqual(
                    client.get("/api/db/stats").get_json()["stats"]["InputData"][
                        "total_rows"
                    ],
                    3,
                )
                response = client.get(
                    "/api/db/file/data", query_string={"file": "InputData/sample.csv"}
                )
                self.assertTrue(response.get_json()["success"])
                self.assertEqual(
                    client.get("/api/db/cleanup/check_corrupted").get_json()["count"], 0
                )
                html = client.get("/database_manager").get_data(as_text=True)
                self.assertIn("总记录数", html)
                self.assertNotIn("扫描整个项目目录", html)


if __name__ == "__main__":
    unittest.main()
