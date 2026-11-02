"""Release regressions: portable resources, upload limits and voltage source."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "webapp"))


class ReleaseTests(unittest.TestCase):
    def test_tracking_hides_control_isr_time_and_keeps_inference(self):
        from app import app

        response = app.test_client().get("/project/tracking?section=experiments")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("控制中断耗时", html)
        self.assertIn('data-serial-value="inference_us"', html)

    def test_current_app_accepts_declared_dataset_size(self):
        from app import app

        self.assertGreaterEqual(app.config["MAX_CONTENT_LENGTH"], 200 * 1024**2)

    def test_cap_voltage_source_is_separate_from_tracking(self):
        firmware = ROOT / "firmware/PI_ANN_CNN_SCI_Web_Monitor/portal_sci.c"
        if not firmware.is_file():
            self.skipTest("独立网页包不含 DSP 固件，固件通道检查由完整仓库执行。")
        code = firmware.read_text(encoding="utf-8")
        self.assertIn("capMonitorValues[0]=sample->Vc1;", code)
        self.assertNotIn("capMonitorValues[0]=sample->Vs1;", code)
        self.assertIn("values[2]=sample->Vs1; values[3]=sample->Vc1;", code)

    def test_cap_voltage_browser_mapping(self):
        js = (ROOT / "webapp/static/project/serial.js").read_text(encoding="utf-8")
        self.assertIn("vc1:frame.data.vg1", js)

    def test_no_retired_remote_control_routes(self):
        from app import app

        routes = {rule.rule for rule in app.url_map.iter_rules()}
        self.assertFalse(any(path.startswith("/api/input/") for path in routes))
        self.assertNotIn("/api/remote_control/verify", routes)

    def test_invalid_database_pagination_does_not_crash(self):
        from app import app

        response = app.test_client().get(
            "/api/db/file/data?file=InputData/仿真结果_0001.csv&page=no&per_page=no"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["success"])
