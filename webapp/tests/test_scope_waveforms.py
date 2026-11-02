import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scope_waveforms import read_waveform


class ScopeWaveformTests(unittest.TestCase):
    def fixture(self, directory):
        folder = Path(directory)
        count = 20000
        data = np.column_stack((np.arange(count) * 1e-7, np.zeros(count)))
        data[10001, 1] = 22
        data[10002, 1] = -4
        data[11000, 1] = np.nan
        data.astype("<f8").tofile(folder / "current_1.bin")
        (folder / "index.json").write_text(
            json.dumps(
                [
                    {
                        "id": "current",
                        "name": "电流",
                        "start": 0,
                        "end": float(data[-1, 0]),
                        "channels": [
                            {
                                "id": 1,
                                "label": "I1",
                                "group": 1,
                                "count": count,
                                "file": "current_1.bin",
                            }
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )
        return folder, data

    def test_peak_preservation_and_zoom_uses_original_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            folder, raw = self.fixture(directory)
            full = read_waveform(folder, "current", limit=128)["channels"][0]
            self.assertIn(22, full["values"])
            self.assertIn(-4, full["values"])
            self.assertIn(None, full["values"])
            self.assertLessEqual(len(full["time"]), 128)
            zoom = read_waveform(
                folder, "current", start=0.001, end=0.001001, limit=128
            )["channels"][0]
            self.assertEqual(zoom["sample_count"], len(raw))
            self.assertLess(len(zoom["time"]), 20)
            self.assertIn(22, zoom["values"])
            self.assertIn(-4, zoom["values"])
            self.assertEqual(sorted(zoom["time"]), zoom["time"])

    def test_validation_and_incomplete_data(self):
        with tempfile.TemporaryDirectory() as directory:
            folder, _ = self.fixture(directory)
            for options in (
                {"start": float("nan")},
                {"end": float("inf")},
                {"start": 1, "end": 0},
                {"limit": True},
                {"limit": 10000},
            ):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    read_waveform(folder, "current", **options)
            with self.assertRaises(ValueError):
                read_waveform(folder, "../current")
            (folder / "current_1.bin").write_bytes(b"partial")
            with self.assertRaises(ValueError):
                read_waveform(folder, "current")


class MatlabScopeMetadataTests(unittest.TestCase):
    fixture = ScopeWaveformTests.fixture
    """Regressions for large MATLAB exports and the tracking scope allowlist."""

    def test_integral_scientific_count_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            folder, raw = self.fixture(directory)
            path = folder / "index.json"
            index = json.loads(path.read_text(encoding="utf-8"))
            index[0]["channels"][0]["count"] = float(len(raw))
            path.write_text(json.dumps(index), encoding="utf-8")
            result = read_waveform(folder, "current")["channels"][0]
            self.assertEqual(result["sample_count"], len(raw))
            self.assertIsInstance(result["sample_count"], int)
            self.assertIn(22, result["values"])
            # MATLAB can emit scientific notation once sample counts grow.
            path.write_text(path.read_text().replace("20000.0", "2e4"), encoding="utf-8")
            self.assertEqual(read_waveform(folder, "current")["channels"][0]["sample_count"], 20000)

    def test_invalid_sample_counts_are_rejected_without_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            folder, _ = self.fixture(directory)
            path = folder / "index.json"
            index = json.loads(path.read_text(encoding="utf-8"))
            for count in (True, 20000.5, -1, 0, float("nan"), float("inf"), "20000", None):
                with self.subTest(count=count):
                    index[0]["channels"][0]["count"] = count
                    path.write_text(json.dumps(index), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        read_waveform(folder, "current")

    def test_tracking_scope_uses_the_same_peak_preserving_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            folder, _ = self.fixture(directory)
            index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
            index[0].update(id="tracking", name="追踪情况")
            index[0]["channels"][0]["file"] = "tracking_1.bin"
            (folder / "current_1.bin").rename(folder / "tracking_1.bin")
            (folder / "index.json").write_text(json.dumps(index), encoding="utf-8")
            result = read_waveform(folder, "tracking", limit=128)
            self.assertEqual(result["name"], "追踪情况")
            self.assertIn(22, result["channels"][0]["values"])
