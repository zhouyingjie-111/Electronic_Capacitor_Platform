"""Receive optional CAP1 UART frames. Also usable offline via --input frames.txt.
Hardware serial mode requires pyserial; parsing/offline mode does not.
"""

import argparse, csv, math
from pathlib import Path
from datetime import datetime, timezone

FIELDS = [
    "sequence",
    "valid",
    "fault",
    "warning",
    "C1_uF",
    "C2_uF",
    "C3_uF",
    "C4_uF",
    "ESR1_Ohm",
    "ESR2_Ohm",
    "ESR3_Ohm",
    "ESR4_Ohm",
    "sample_rate_Hz",
    "preprocess_us",
    "inference_us",
    "max_task_step_us",
    "model_id",
]


def parse_frame(line):
    line = line.strip()
    body, hexsum = line.rsplit("*", 1)
    checksum = 0
    for b in body.encode("ascii"):
        checksum ^= b
    if len(hexsum) != 2 or int(hexsum, 16) != checksum:
        raise ValueError("checksum mismatch")
    parts = body.split(",")
    if len(parts) != 18 or parts[0] != "CAP1":
        raise ValueError("bad frame shape/header")
    result = dict(zip(FIELDS, parts[1:]))
    for name in FIELDS[:4]:
        result[name] = int(result[name])
    for name in FIELDS[4:-1]:
        result[name] = float(result[name])
        if not math.isfinite(result[name]):
            raise ValueError("nonfinite field")
    return result


def main():
    p = argparse.ArgumentParser()
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--port", help="e.g. COM5")
    source.add_argument("--input", type=Path, help="offline captured ASCII frame file")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "Data/cap_results.csv",
    )
    a = p.parse_args()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    if a.port:
        import serial

        stream = serial.Serial(a.port, a.baud, timeout=1)
    else:
        stream = a.input.open("rb")
    needs_header = not a.output.exists() or a.output.stat().st_size == 0
    with (
        stream,
        a.output.open("a", newline="", encoding="utf-8-sig") as f,
        a.output.with_suffix(".raw.log").open("a", encoding="utf-8") as raw,
    ):
        writer = csv.DictWriter(f, fieldnames=["pc_time_utc", *FIELDS])
        if needs_header:
            writer.writeheader()
        try:
            while True:
                data = stream.readline()
                if not data:
                    if a.input:
                        break
                    continue
                text = data.decode("ascii", errors="replace").strip()
                now = datetime.now(timezone.utc).isoformat()
                raw.write(now + " " + text + "\n")
                raw.flush()
                try:
                    record = parse_frame(text)
                except (ValueError, UnicodeError) as e:
                    print("Rejected frame:", e)
                    continue
                writer.writerow(dict(pc_time_utc=now, **record))
                f.flush()
                print("VALID" if record["valid"] else "INVALID", record)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
