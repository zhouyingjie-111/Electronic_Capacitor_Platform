"""Local serial transport and versioned, CRC checked monitoring frames."""

# 串口约定：全站共享一个连接与接收线程。按帧类型分别缓存，CRC 校验成功才更新数据；页面切换不重新连接。

import atexit
from collections import deque
import math
from pathlib import Path
import re
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor_public"))
import serial
from serial.tools import list_ports


class SerialError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def crc16(data):
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = (
                ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
            )
    return crc


def pack_frame(parts):
    body = ",".join(str(part) for part in parts).encode("ascii")
    return b"@" + body + f"*{crc16(body):04X}\n".encode("ascii")


TRACK_FIELDS = (
    "p_peak",
    "t_rise_fall_us",
    "vc1",
    "vg1",
    "vin",
    "io",
    "il1",
    "il2",
    "ic",
    "dt_given",
    "pi_duty",
    "direction_state",
    "ann_ready",
    "ann_active",
    "isr_us",
    "inference_us",
)
CAP_MONITOR_FIELDS = (
    "vc1",
    "ic",
    "io",
    "isr_us",
    "inference_us",
    "preprocess_us",
    "latency_us",
    "sample_count",
    "sample_rate_hz",
    "state",
    "valid",
    "fault",
    "warning",
)
CAP_STATUS_FIELDS = (
    "state",
    "valid",
    "fault",
    "warning",
    "sample_count",
    "error_count",
    "sample_rate_hz",
    "latency_us",
)


def decode_frame(raw):
    """Reject partial, oversized, corrupted, nonfinite and unknown-schema data."""
    if len(raw) > 512:
        raise ValueError("帧过长")
    text = raw.decode("ascii").strip("\r\n")
    if not text.startswith("@") or "*" not in text:
        raise ValueError("非协议帧")
    body, checksum = text[1:].rsplit("*", 1)
    if not re.fullmatch(r"[0-9A-Fa-f]{4}", checksum) or crc16(
        body.encode("ascii")
    ) != int(checksum, 16):
        raise ValueError("校验失败")
    values = body.split(",")
    if len(values) < 3 or values[1] != "1":
        raise ValueError("协议版本不匹配")
    kind = values[0]
    if kind in ("TRK", "CAP", "CAPMON", "CAPSTAT"):
        fields = {
            "TRK": TRACK_FIELDS,
            "CAP": ("c1", "esr1", "c2", "esr2", "c3", "esr3", "c4", "esr4"),
            "CAPMON": CAP_MONITOR_FIELDS,
            "CAPSTAT": CAP_STATUS_FIELDS,
        }[kind]
        size = 4 + len(fields)
        if len(values) != size:
            raise ValueError("字段数量不匹配")
        seq, millis = int(values[2]), int(values[3])
        if not 0 <= seq <= 0xFFFFFFFF or not 0 <= millis <= 0xFFFFFFFF:
            raise ValueError("序号或时间非法")
        numbers = [float(v) if v else None for v in values[4:]]
        if any(v is not None and not math.isfinite(v) for v in numbers):
            raise ValueError("非有限数值")
        if kind == "TRK":
            data = dict(zip(TRACK_FIELDS, numbers))
            if any(
                data[k] not in (0, 1)
                for k in ("direction_state", "ann_ready", "ann_active")
            ):
                raise ValueError("状态位非法")
        else:
            data = dict(zip(fields, numbers))
            if kind in ("CAPMON", "CAPSTAT"):
                if data["state"] not in range(7) or data["valid"] not in (0, 1):
                    raise ValueError("辨识状态非法")
                for key in ("fault", "warning", "sample_count"):
                    v = data[key]
                    if (
                        v is None
                        or v < 0
                        or not v.is_integer()
                        or v > (1024 if key == "sample_count" else 65535)
                    ):
                        raise ValueError("辨识状态字段非法")
        return {"type": kind, "sequence": seq, "device_ms": millis, "data": data}
    if kind == "ACK" and 5 <= len(values) <= 6:
        return {
            "type": kind,
            "sequence": int(values[2]),
            "command": values[3],
            "result": ",".join(values[4:]),
        }
    raise ValueError("未知数据类型")


class SerialService:
    def __init__(self, port_provider=None, serial_factory=None):
        self._ports = port_provider or list_ports.comports
        self._factory = serial_factory or serial.Serial
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._io = None
        self._thread = None
        self._stop = threading.Event()
        self._events = deque(maxlen=500)
        self._event_id = 0
        self._command_seq = 0
        self._last_command_at = 0
        self._connection_id = 0
        self._last_sequence = {}
        self._latest = {}
        self._received = self._sent = self._invalid = self._gaps = 0
        self._port = ""
        self._baud = 115200
        self._error = ""
        self._feedback = {
            "text": "选择 XDS110 的 Application/User UART，通信格式为 8N1。",
            "error": False,
        }
        self._upload_rate = 10
        self._pending_command = None
        self._requested_rate = None
        atexit.register(self.disconnect)

    def ports(self):
        return [
            {
                "device": p.device,
                "description": p.description or p.device,
                "recommended": "XDS110" in (p.description or "").upper()
                and (
                    "UART" in (p.description or "").upper()
                    or "APPLICATION" in (p.description or "").upper()
                ),
            }
            for p in self._ports()
        ]

    def set_feedback(self, text, error=False):
        with self._lock:
            self._feedback = {"text": str(text), "error": bool(error)}

    def connect(self, port, baud):
        """建立单一串口连接并启动接收线程；重复连接和无效参数通过 SerialError 返回给页面。"""
        if not isinstance(port, str) or port not in {p["device"] for p in self.ports()}:
            raise SerialError("请选择当前电脑已识别的串口。")
        if isinstance(baud, bool) or baud not in (9600, 19200, 38400, 57600, 115200):
            raise SerialError("请选择有效波特率。")
        with self._lock:
            if self._io:
                raise SerialError("串口已经连接，请先断开。", 409)
            try:
                # Open only the explicitly selected port; never toggle reset lines.
                device = self._factory(
                    port=None,
                    baudrate=baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.05,
                    write_timeout=0.2,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False,
                )
                device.dtr = False
                device.rts = False
                device.port = port
                device.open()
                device.reset_input_buffer()
            except (OSError, serial.SerialException, ValueError):
                try:
                    device.close()
                except (UnboundLocalError, OSError, serial.SerialException):
                    pass
                raise SerialError(
                    "无法打开串口，请检查连接及是否被其他软件占用。"
                ) from None
            self._io = device
            self._port, self._baud, self._error = port, baud, ""
            self._feedback = {"text": "串口已连接，等待设备数据。", "error": False}
            self._received = self._sent = self._invalid = self._gaps = 0
            self._last_command_at = 0
            self._pending_command = None
            self._latest.clear()
            self._last_sequence.clear()
            self._events.clear()
            self._connection_id += 1
            self._stop = threading.Event()
            self._thread = threading.Thread(
                target=self._read, args=(device, self._stop), daemon=True
            )
            self._thread.start()
        return self.snapshot()

    def disconnect(self):
        """停止接收并释放串口；页面共用该状态，不为每个功能建立独立连接。"""
        with self._lock:
            device, thread = self._io, self._thread
            self._stop.set()
            self._io = None
            if device:
                self._feedback = {"text": "串口已断开。", "error": False}
        if device:
            with self._write_lock:
                try:
                    device.close()
                except (OSError, serial.SerialException):
                    pass
        if thread and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        return self.snapshot()

    def _read(self, device, stop):
        buffer = bytearray()
        dropping = False
        try:
            while not stop.is_set():
                chunk = device.read(min(max(device.in_waiting, 1), 4096))
                for value in chunk:
                    if value == 10:
                        if not dropping and buffer:
                            self.ingest(bytes(buffer))
                        buffer.clear()
                        dropping = False
                    elif not dropping:
                        if len(buffer) >= 512:
                            buffer.clear()
                            dropping = True
                            with self._lock:
                                self._invalid += 1
                        else:
                            buffer.append(value)
        except (OSError, serial.SerialException, TypeError):
            if not stop.is_set():
                with self._lock:
                    self._error = "串口连接已中断，请检查设备后重新连接。"
                    if self._io is device:
                        self._io = None
                try:
                    device.close()
                except (OSError, serial.SerialException):
                    pass

    def ingest(self, raw):
        """Shared parser entry for transport and deterministic protocol tests."""
        try:
            frame = decode_frame(raw)
        except (ValueError, UnicodeError, OverflowError):
            with self._lock:
                self._invalid += 1
            return
        with self._lock:
            self._received += len(raw) + 1
            frame["received_at"] = time.time()
            frame["raw"] = raw.decode("ascii").rstrip()
            self._event_id += 1
            frame["id"] = self._event_id
            self._events.append(frame)
            if frame["type"] in ("TRK", "CAP", "CAPMON", "CAPSTAT"):
                kind, seq = frame["type"], frame["sequence"]
                previous = self._last_sequence.get(kind)
                if previous is not None:
                    delta = (seq - previous) & 0xFFFFFFFF
                    if 1 < delta < 0x80000000:
                        self._gaps += delta - 1
                self._last_sequence[kind] = seq
                self._latest[kind] = frame
            elif frame["type"] == "ACK":
                if self._pending_command and frame["sequence"] == self._pending_command:
                    if (
                        frame["command"] == "RATE"
                        and frame["result"] == "OK"
                        and self._requested_rate
                    ):
                        self._upload_rate = self._requested_rate
                    self._requested_rate = None
                    self._pending_command = None
                self._feedback = {
                    "text": "设备应答：" + frame["command"] + " · " + frame["result"],
                    "error": frame["result"] != "OK",
                }

    def send(self, command):
        """仅允许协议白名单命令，通过同一写锁发送，避免两个页面交错写入串口。"""
        if not isinstance(command, str) or not re.fullmatch(
            r"(PING|SNAP|CAPTURE|CAPSTATUS|STREAM [01]|RATE (1|2|5|10|20))", command
        ):
            raise SerialError(
                "支持 PING、SNAP、CAPTURE、CAPSTATUS、STREAM 0/1、RATE 1/2/5/10/20。"
            )
        with self._write_lock:
            with self._lock:
                if not self._io:
                    raise SerialError("请先连接串口。", 409)
                now = time.monotonic()
                if now - self._last_command_at < 0.2:
                    raise SerialError("指令发送较快，请稍后再试。", 429)
                device = self._io
                self._command_seq = (self._command_seq + 1) & 0xFFFFFFFF
                parts = ["CMD", 1, self._command_seq, *command.split(" ")]
                packet = pack_frame(parts)
            try:
                if device.write(packet) != len(packet):
                    raise serial.SerialTimeoutException()
            except (OSError, serial.SerialException):
                raise SerialError("串口发送失败，请检查连接。") from None
            with self._lock:
                self._last_command_at = time.monotonic()
                self._pending_command = parts[2]
                self._sent += len(packet)
                self._feedback = {"text": "指令已发送，等待设备应答。", "error": False}
                self._requested_rate = (
                    int(command.split()[1]) if command.startswith("RATE ") else None
                )
                self._event_id += 1
                self._events.append(
                    {
                        "id": self._event_id,
                        "type": "TX",
                        "raw": packet.decode().strip(),
                        "received_at": time.time(),
                        "command": command,
                    }
                )
        return {"ok": True, "sequence": parts[2]}

    def snapshot(self, after=0):
        """返回游标 after 之后的事件与最新状态副本；网页轮询不会取走其他页面的数据。"""
        with self._lock:
            if self._pending_command and time.monotonic() - self._last_command_at > 4:
                self._feedback = {
                    "text": "未收到设备应答，请检查板端程序、波特率与串口跳线。",
                    "error": True,
                }
                self._pending_command = None
            events = [dict(e) for e in self._events if e["id"] > after]
            return {
                "connected": self._io is not None,
                "port": self._port,
                "baud": self._baud,
                "error": self._error,
                "connection_id": self._connection_id,
                "cursor": self._event_id,
                "events": events,
                "latest": dict(self._latest),
                "feedback": dict(self._feedback),
                "upload_rate": self._upload_rate,
                "stats": {
                    "received_bytes": self._received,
                    "sent_bytes": self._sent,
                    "invalid_frames": self._invalid,
                    "sequence_gaps": self._gaps,
                },
            }
