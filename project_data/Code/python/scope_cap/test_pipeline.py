"""Small deterministic tests for preprocessing and split isolation."""

import unittest
from pathlib import Path
import json
import numpy as np
from pipeline import downsample, haar_energy, fit_image_transform, make_images


class PreprocessingTests(unittest.TestCase):
    def test_downsampling_rejects_alias(self):
        t = np.arange(100000) * 1e-6
        x = np.sin(2 * np.pi * 10000 * t) + np.sin(2 * np.pi * 90000 * t)
        _, y, good, factor = downsample(0, 1e-6, x, 1e-5)
        desired = np.sin(2 * np.pi * 10000 * np.arange(len(y)) * 1e-5)
        self.assertEqual(factor, 10)
        self.assertLess(np.sqrt(np.mean((y[good] - desired[good]) ** 2)), 0.01)

    def test_missing_samples_excluded_with_filter_support(self):
        x = np.zeros(10000)
        x[5000] = float("inf")
        _, y, good, _ = downsample(0, 1e-6, x, 1e-5)
        self.assertTrue(np.isfinite(y).all())
        self.assertFalse(good[488:513].any())

    def test_voltage_dc_invariance_and_energy(self):
        rng = np.random.default_rng(42)
        x = rng.normal(size=(1024, 3)).astype(np.float32)
        e = haar_energy(x)
        y = x.copy()
        y[:, 0] += 200
        np.testing.assert_allclose(e, haar_energy(y), atol=1e-4, rtol=1e-3)
        const = np.ones((1024, 3), np.float32)
        e = haar_energy(const)
        self.assertTrue(np.all(e[:5] == 0))
        self.assertTrue(np.all(e[-1, :, 0] == 0))
        np.testing.assert_allclose(e[-1, :, 1:], 32, rtol=1e-6)

    def test_image_contract(self):
        rng = np.random.default_rng(10)
        e = np.array([haar_energy(rng.normal(size=(1024, 3))) for _ in range(5)])
        image = make_images(e, fit_image_transform(e))
        self.assertEqual(image.shape, (5, 6, 32, 3))
        self.assertTrue(np.isfinite(image).all())
        self.assertTrue(np.all((image >= 0) & (image <= 1)))

    def test_actual_manifest_no_split_overlap(self):
        path = (
            Path(__file__).resolve().parents[3]
            / "OutputData/scope_cap_v1/window_manifest.json"
        )
        if not path.exists():
            self.skipTest("Run preparation first")
        records = json.loads(path.read_text())
        for a in records:
            for b in records:
                if a["group"] == b["group"] and a["split"] != b["split"]:
                    self.assertTrue(
                        a["end_s"] < b["start_s"] or b["end_s"] < a["start_s"]
                    )


if __name__ == "__main__":
    unittest.main()
