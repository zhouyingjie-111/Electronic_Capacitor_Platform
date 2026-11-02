"""Range queries over immutable float64 scope samples, with peak-preserving reduction."""

# 波形存储：每行两个 little-endian float64，分别为时间和值。只读 memmap 避免全量载入，通过保留桶内极值实现网页降采样。

import json
import math
import re
from pathlib import Path

import numpy as np


def reduce_indices(values, limit):
    if len(values) <= limit:
        return np.arange(len(values))
    # Keep both extremes and endpoints of each bucket, in their original time order.
    step = math.ceil(len(values) / max(1, limit // 5))
    indices = []
    for first in range(0, len(values), step):
        last = min(len(values), first + step)
        bucket = values[first:last]
        finite = np.isfinite(bucket)
        indices.extend((first, last - 1))
        if finite.any():
            clean = np.where(finite, bucket, np.nan)
            indices.extend(
                (first + int(np.nanargmin(clean)), first + int(np.nanargmax(clean)))
            )
        if not finite.all():
            indices.append(first + int(np.flatnonzero(~finite)[0]))
    return np.unique(indices)


def sample_count(value):
    """Normalize exact MATLAB JSON numbers without truncating invalid counts."""
    if isinstance(value, bool):
        raise ValueError("波形采样点数不合法。")
    if isinstance(value, int):
        count = value
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        count = int(value)
    else:
        raise ValueError("波形采样点数不合法。")
    if count <= 0:
        raise ValueError("波形采样点数不合法。")
    return count


def read_waveform(folder, scope_id, start=None, end=None, limit=3000):
    if scope_id not in {"voltage", "current", "switch", "tracking", "model_input"}:
        raise ValueError("示波器不存在。")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 128 <= limit <= 8000
    ):
        raise ValueError("波形显示点数须在 128～8000 之间。")
    folder = Path(folder)
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    if isinstance(index, dict):
        index = [index]
    entry = next((item for item in index if item["id"] == scope_id), None)
    if entry is None:
        raise ValueError("本次仿真没有该示波器的波形数据。")
    start = entry["start"] if start is None else float(start)
    end = entry["end"] if end is None else float(end)
    if not math.isfinite(start) or not math.isfinite(end) or start >= end:
        raise ValueError("请选择有效的时间范围。")
    rows = entry["channels"]
    if isinstance(rows, dict):
        rows = [rows]
    result = {
        "id": entry["id"],
        "name": entry["name"],
        "bounds": [entry["start"], entry["end"]],
        "range": [start, end],
        "channels": [],
    }
    for row in rows:
        filename = row["file"]
        if not re.fullmatch(rf"{scope_id}_\d+\.bin", filename):
            raise ValueError("波形文件索引不合法。")
        path = folder / filename
        count = sample_count(row.get("count"))
        if path.stat().st_size != count * 16:
            raise ValueError("波形数据不完整，请重新仿真。")
        data = np.memmap(path, dtype="<f8", mode="r", shape=(count, 2))
        left = max(0, int(np.searchsorted(data[:, 0], start, side="left")) - 1)
        right = min(len(data), int(np.searchsorted(data[:, 0], end, side="right")) + 1)
        window = data[left:right]
        indices = reduce_indices(window[:, 1], limit)
        samples = window[indices]
        result["channels"].append(
            {
                "id": row["id"],
                "label": row["label"],
                "group": row["group"],
                "sample_count": count,
                "visible_count": len(window),
                "time": samples[:, 0].tolist(),
                "values": [
                    float(v) if math.isfinite(v) else None for v in samples[:, 1]
                ],
            }
        )
    return result
