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
