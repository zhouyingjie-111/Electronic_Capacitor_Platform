import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serial_service import (
    SerialService,
    SerialError,
    decode_frame,
    pack_frame,
    TRACK_FIELDS,
    crc16,
)


def tracking(seq=1):
    return pack_frame(
        [
            "TRK",
            1,
            seq,
            100,
            22,
            50,
            201,
            202,
            270,
            20,
            4,
            4,
            1,
            0.42,
            0.41,
            0,
            1,
            0,
            3.1,
            22,
        ]
    )


class FakePort:
    def __init__(self, **kwargs):
        self.input = bytearray()
        self.output = bytearray()
        self.opened = False
        self.settings = kwargs

    def open(self):
        self.opened = True

    def close(self):
        self.opened = False

    def reset_input_buffer(self):
        self.input.clear()

    @property
    def in_waiting(self):
        return len(self.input)

    def read(self, n):
        if not self.input:
            time.sleep(0.001)
            return b""
        result = bytes(self.input[:n])
        del self.input[:n]
        return result

    def write(self, data):
        self.output.extend(data)
        return len(data)


class SerialProtocolTests(unittest.TestCase):
    def test_cap_monitor_status_and_tracking_remain_separate(self):
        link = SerialService()
        link.ingest(tracking())
        link.ingest(
            pack_frame(
                [
                    "CAPMON",
                    1,
                    1,
                    100,
                    60,
                    2,
                    20,
                    3,
                    25000,
                    4000,
                    50000,
                    1024,
                    100000,
                    5,
                    1,
                    0,
                    3,
                ]
            )
        )
        link.ingest(
            pack_frame(["CAPSTAT", 1, 1, 100, 5, 1, 0, 3, 1024, 0, 100000, 50000])
        )
        state = link.snapshot()
        self.assertEqual(state["stats"]["invalid_frames"], 0)
        self.assertEqual(state["latest"]["CAPMON"]["data"]["inference_us"], 25000)
        self.assertEqual(state["latest"]["TRK"]["data"]["inference_us"], 22)
        self.assertEqual(state["latest"]["CAPSTAT"]["data"]["sample_count"], 1024)
        bad = [
            "CAPMON",
            1,
            2,
            100,
            60,
            2,
            20,
            3,
            25000,
            4000,
            50000,
            1025,
            100000,
            5,
            1,
            0,
            3,
        ]
        with self.assertRaises(ValueError):
            decode_frame(pack_frame(bad))

    def test_shared_feedback_rate_and_cap_commands_across_clients(self):
        fake = FakePort()
        link = SerialService(
            lambda: [SimpleNamespace(device="COM_TEST", description="test")],
            lambda **kw: fake,
        )
        self.addCleanup(link.disconnect)
        link.connect("COM_TEST", 115200)
        self.assertEqual(
            link.snapshot()["feedback"]["text"], "串口已连接，等待设备数据。"
        )
        result = link.send("RATE 20")
        self.assertEqual(link.snapshot()["upload_rate"], 10)
        link.ingest(pack_frame(["ACK", 1, result["sequence"], "RATE", "OK"]))
        self.assertEqual(link.snapshot()["upload_rate"], 20)
        self.assertEqual(link.snapshot()["feedback"]["text"], "设备应答：RATE · OK")
        link._last_command_at = 0
        link.send("CAPTURE")
        self.assertIn(b"CAPTURE", fake.output)
        link._last_command_at -= 5
        self.assertTrue(link.snapshot()["feedback"]["error"])
        link.disconnect()
        self.assertEqual(link.snapshot()["feedback"]["text"], "串口已断开。")

    def test_two_pages_share_one_device_and_connection_feedback(self):
        from flask import Flask
        from project_portal import register_project_portal

        app = Flask(__name__)
        app.secret_key = "serial-sharing-test"
        register_project_portal(app)
        link = app.extensions["project_serial"]
        fake = FakePort()
        link._ports = lambda: [SimpleNamespace(device="COM_TEST", description="test")]
        link._factory = lambda **kw: fake
        self.addCleanup(link.disconnect)
        tracking_client, cap_client = app.test_client(), app.test_client()
        first = tracking_client.post(
            "/api/project/serial/connect", json={"port": "COM_TEST", "baud": 115200}
        ).get_json()
        other = cap_client.get("/api/project/serial/status").get_json()
        self.assertTrue(other["connected"])
        self.assertEqual(first["connection_id"], other["connection_id"])
        self.assertEqual(first["feedback"], other["feedback"])
        response = cap_client.post(
            "/api/project/serial/send", json={"command": "CAPSTATUS"}
        ).get_json()
        link.ingest(pack_frame(["ACK", 1, response["sequence"], "CAPSTATUS", "OK"]))
        self.assertEqual(
            tracking_client.get("/api/project/serial/status").get_json()["feedback"][
                "text"
            ],
            "设备应答：CAPSTATUS · OK",
        )
        self.assertIn(
            "设备应答：CAPSTATUS · OK",
            cap_client.get("/project/capacitance?section=experiments").get_data(
                as_text=True
            ),
        )
        tracking_client.post("/api/project/serial/send", json={"command": "SET PWM 1"})
        self.assertTrue(
            cap_client.get("/api/project/serial/status").get_json()["feedback"]["error"]
        )
        cap_client.post("/api/project/serial/disconnect", json={})
        final = tracking_client.get("/api/project/serial/status").get_json()
        self.assertFalse(final["connected"])
        self.assertEqual(final["feedback"]["text"], "串口已断开。")

    def test_crc_known_vector_and_only_used_ann_output(self):
        self.assertEqual(crc16(b"123456789"), 0x29B1)
        frame = decode_frame(tracking())
        self.assertEqual(set(frame["data"]), set(TRACK_FIELDS))
        self.assertEqual(frame["data"]["dt_given"], 0.42)
        self.assertNotIn("dssample", str(frame).lower())
        self.assertEqual(frame["data"]["direction_state"], 0)

    def test_corrupt_nonfinite_unknown_and_wrong_version_rejected(self):
        for packet in [
            tracking().replace(b"270", b"271"),
            b"not a frame",
            pack_frame(["TRK", 2, 1, 0]),
            pack_frame(["BAD", 1, 1]),
            tracking().replace(b".42", b"nan"),
            b"x" * 513,
        ]:
            with (
                self.subTest(packet=packet),
                self.assertRaises((ValueError, UnicodeError)),
            ):
                decode_frame(packet)
        bad = ["TRK", 1, 1, 0] + [0] * len(TRACK_FIELDS)
        bad[4] = "nan"
        with self.assertRaises(ValueError):
            decode_frame(pack_frame(bad))

    def test_capacitance_is_routed_separately_and_ack_decoded(self):
        service = SerialService()
        service.ingest(tracking())
        service.ingest(
            pack_frame(["CAP", 1, 1, 100, 500, 320, 501, 321, 502, 322, 503, 323])
        )
        service.ingest(pack_frame(["ACK", 1, 3, "PING", "OK"]))
        state = service.snapshot()
        self.assertEqual(state["latest"]["CAP"]["data"]["esr4"], 323)
        self.assertEqual(state["latest"]["TRK"]["data"]["p_peak"], 22)
        self.assertEqual(state["events"][-1]["command"], "PING")

    def test_bounded_history_cursor_and_dropped_frames(self):
        service = SerialService()
        for n in range(600):
            service.ingest(tracking(n))
        self.assertEqual(len(service.snapshot()["events"]), 500)
        self.assertEqual(len(service.snapshot(599)["events"]), 1)
        service.ingest(tracking(603))
        self.assertEqual(service.snapshot()["stats"]["sequence_gaps"], 3)
        service.ingest(b"invalid")
        self.assertEqual(service.snapshot()["stats"]["invalid_frames"], 1)

    def test_fragmented_receive_and_commands_with_no_reset_lines(self):
        fake = FakePort()
        ports = lambda: [
            SimpleNamespace(
                device="COM_TEST", description="XDS110 Application/User UART"
            )
        ]
        service = SerialService(ports, lambda **kw: (fake.settings.update(kw) or fake))
        self.addCleanup(service.disconnect)
        with self.assertRaises(SerialError):
            service.connect("COM_UNKNOWN", 115200)
        state = service.connect("COM_TEST", 115200)
        self.assertTrue(state["connected"])
        self.assertFalse(fake.dtr)
        self.assertFalse(fake.rts)
        raw = tracking()
        for part in [raw[:12], raw[12:40], raw[40:]]:
            fake.input.extend(part)
            time.sleep(0.005)
        deadline = time.monotonic() + 1
        while "TRK" not in service.snapshot()["latest"] and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertIn("TRK", service.snapshot()["latest"])
        for bad in ["SET PWM 1", "RATE 100", "STREAM 1\nSET P 4", "", None]:
            with self.assertRaises(SerialError):
                service.send(bad)
        service.send("PING")
        self.assertEqual(bytes(fake.output), pack_frame(["CMD", 1, 1, "PING"]))
        service.disconnect()
        with self.assertRaises(SerialError):
            service.send("SNAP")

    def test_json_api_rejects_foreign_origin_and_remote_clients(self):
        from flask import Flask
        from project_portal import register_project_portal

        app = Flask(__name__)
        app.secret_key = "test"
        register_project_portal(app)
        client = app.test_client()
        self.assertEqual(
            client.post(
                "/api/project/serial/disconnect",
                json={},
                headers={"Origin": "http://other.test"},
            ).status_code,
            403,
        )
        self.assertEqual(
            client.get(
                "/api/project/serial/status",
                environ_overrides={"REMOTE_ADDR": "192.168.1.2"},
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                "/api/project/serial/send", data={"command": "PING"}
            ).status_code,
            415,
        )
        self.assertEqual(
            client.post("/api/project/serial/connect", json=[]).status_code, 400
        )
        self.assertEqual(
            client.post(
                "/api/project/serial/send", json={"command": "SET PI 1"}
            ).status_code,
            400,
        )


if __name__ == "__main__":
    unittest.main()
