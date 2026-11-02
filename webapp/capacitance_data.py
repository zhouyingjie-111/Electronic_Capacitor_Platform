"""Scope data contracts shared by the capacitance UI and background jobs."""

import csv
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from training_runner import TrainingError

OUTPUTS = [
    "C1_uF",
    "C2_uF",
    "C3_uF",
    "C4_uF",
    "ESR1_Ohm",
    "ESR2_Ohm",
    "ESR3_Ohm",
    "ESR4_Ohm",
]
PATTERN = re.compile(r"tek(\d{4})CH([134])-(10|4)ms\.csv", re.I)


def gain_overrides(path):
    """Use saved SIDs, rather than guessed block paths, for run-only PI overrides."""
    result = []
    mapping = {
        "PID Controller1": ("P_V", "I_V"),
        "PID Controller4": ("P_V", "I_V"),
        "PID Controller3": ("P_I", "I_I"),
        "PID Controller5": ("P_I", "I_I"),
    }
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("simulink/systems/system_root.xml"))
        for block in root.iter("Block"):
            if block.get("Name") in mapping:
                for parameter, variable in zip(("P", "I"), mapping[block.get("Name")]):
                    result.append(
                        {
                            "sid": block.get("SID"),
                            "parameter": parameter,
                            "variable": variable,
                        }
                    )
    return result


def csv_head(path):
    meta = {}
    rows = []
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)
        for index, row in enumerate(reader):
            if index > 100:
                raise TrainingError("CSV 缺少 TIME 表头。")
            if row and row[0].strip().upper() == "TIME":
                break
            if len(row) >= 2 and row[0]:
                meta[row[0].strip()] = row[1].strip()
        else:
            raise TrainingError("CSV 缺少 TIME 表头。")
        for row in reader:
            if len(row) < 2:
                continue
            rows.append(row[:2])
            if len(rows) == 8:
                break
    try:
        dt = float(meta["Sample Interval"])
        count = float(meta["Record Length"])
        if not math.isfinite(dt) or dt <= 0 or not math.isfinite(count) or count < 1024:
            raise ValueError()
    except (KeyError, ValueError):
        raise TrainingError("CSV 的采样间隔或记录长度不正确。") from None
    return meta, rows


def validate_labels(labels):
    if not isinstance(labels, dict) or not 1 <= len(labels) <= 30:
        raise TrainingError("标签须为组号到八个参数的映射。")
    for group, values in labels.items():
        if (
            not isinstance(group, str)
            or not re.fullmatch(r"\d{4}", group)
            or not isinstance(values, list)
            or len(values) != 8
        ):
            raise TrainingError("每组标签须包含四个容值和四个 ESR。")
        if any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 < v < 1e9
            for v in values
        ):
            raise TrainingError("标签须为正的有限数值，容值单位 μF，ESR 单位 Ω。")
    return labels


def inventory(directory, labels, filename=None):
    directory = Path(directory)
    items = []
    groups = set()
    full = set()
    for path in sorted(directory.glob("*.csv")):
        match = PATTERN.fullmatch(path.name)
        if not match:
            continue
        group, channel, view = match.groups()
        channel = int(channel)
        groups.add(group)
        if group not in labels:
            raise TrainingError("组号 " + group + " 缺少标签，请提供 labels.json。")
        meta, _ = csv_head(path)
        unit = "V" if channel == 1 else "A"
        if meta.get("Vertical Units") != unit:
            raise TrainingError(path.name + " 的通道单位应为 " + unit + "。")
        if view == "10":
            full.add((group, channel))
        items.append(
            {
                "name": path.name,
                "group": group,
                "channel": "CH" + str(channel),
                "view": view + " ms",
                "dt_us": float(meta["Sample Interval"]) * 1e6,
                "declared_samples": int(float(meta["Record Length"])),
                "use": "训练与审计" if view == "10" else "仅审计",
                "unit": unit,
            }
        )
    if not items:
        raise TrainingError("没有找到示波器 CSV 文件。")
    if any((group, ch) not in full for group in groups for ch in (1, 3, 4)):
        raise TrainingError("每个组号须提供 CH1、CH3、CH4 三个完整的 -10ms.csv 文件。")
    selected = filename or items[0]["name"]
    if selected not in {i["name"] for i in items}:
        raise TrainingError("数据文件不存在。", 404)
    meta, rows = csv_head(directory / selected)
    return {
        "files": items,
        "groups": sorted(groups),
        "selected": selected,
        "columns": ["TIME / s", "幅值 / " + meta["Vertical Units"]],
        "rows": rows,
        "labels": {g: labels[g] for g in sorted(groups)},
    }


def unpack_dataset(raw, destination, default_labels):
    destination = Path(destination)
    destination.mkdir(parents=True)
    selected = {}
    labels = None
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > 200 or sum(i.file_size for i in infos) > 512 * 1024**2:
                raise TrainingError("压缩包文件过多或展开后超过 512 MB。")
            for item in infos:
                name = item.filename.replace("\\", "/")
                parts = PurePosixPath(name)
                if (
                    parts.is_absolute()
                    or ".." in parts.parts
                    or ":" in name
                    or (item.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise TrainingError("压缩包包含无效路径。")
                if item.is_dir():
                    continue
                basename = parts.name
                if basename == "labels.json":
                    if labels is not None or item.file_size > 20000:
                        raise TrainingError("labels.json 重复或过大。")
                    labels = validate_labels(
                        json.loads(archive.read(item).decode("utf-8-sig"))
                    )
                elif PATTERN.fullmatch(basename):
                    if basename.lower() in selected or item.file_size > 150 * 1024**2:
                        raise TrainingError("数据文件重复或单文件超过 150 MB。")
                    selected[basename.lower()] = basename
                    with (
                        archive.open(item) as source,
                        (destination / basename).open("wb") as target,
                    ):
                        import shutil

                        shutil.copyfileobj(source, target)
                # Other attachments, including nested ZIPs, are never executed or expanded.
    except TrainingError:
        raise
    except Exception:
        raise TrainingError("ZIP 或标签 JSON 无法读取。") from None
    labels = validate_labels(labels or default_labels)
    return inventory(destination, labels), labels
