"""Scope CSV -> anti-aliased 100 kHz -> Haar energy images.

All paths in config.json are relative to that file. No raw file is modified.
4ms crops are audited but excluded, as they belong to the same acquisitions.
"""

from pathlib import Path
import csv
import hashlib
import json
import re
import numpy as np
from scipy.signal import resample_poly

NAMES = [
    "C1_uF",
    "C2_uF",
    "C3_uF",
    "C4_uF",
    "ESR1_Ohm",
    "ESR2_Ohm",
    "ESR3_Ohm",
    "ESR4_Ohm",
]


def load_config(path):
    path = Path(path).resolve()
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for key in ("data_dir", "output_dir"):
        cfg[key] = str((path.parent / cfg[key]).resolve())
    # This revision intentionally uses one fixed, tested deployment contract.
    assert (
        cfg["window_samples"],
        cfg["wavelet_levels"],
        cfg["image_columns"],
        cfg["channel_order"],
    ) == (1024, 5, 32, [1, 3, 4])
    assert cfg["target_dt_s"] == 1e-5
    assert abs(sum(cfg["split_fractions"]) - 1) < 1e-9
    return cfg


def json_write(path, obj):
    Path(path).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def read_scope(path):
    meta = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        for skip, row in enumerate(csv.reader(f), 1):
            if row and row[0].strip().upper() == "TIME":
                break
            if len(row) >= 2 and row[0]:
                meta[row[0].strip()] = row[1].strip()
        else:
            raise ValueError(f"No TIME header: {path}")
    a = np.loadtxt(path, delimiter=",", skiprows=skip, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] != 2 or len(a) < 2:
        raise ValueError(f"Expected TIME,channel numeric columns: {path}")
    t, x = a[:, 0], a[:, 1]
    if not np.isfinite(t).all() or np.any(np.diff(t) <= 0):
        raise ValueError(f"Nonmonotone or invalid time axis: {path}")
    # Exported decimal time stamps are rounded. Use declared dt only when
    # the complete measured time span agrees, rather than trusting filenames.
    dt = float(meta["Sample Interval"])
    residual = np.abs(t - (t[0] + np.arange(len(t)) * dt))
    if residual.max() > 0.55 * dt:
        raise ValueError(f"Time axis inconsistent with Sample Interval: {path}")
    valid = np.isfinite(x)
    stat = dict(
        file=Path(path).name,
        samples=len(t),
        dt_s=dt,
        span_s=float(t[-1] - t[0]),
        start_s=float(t[0]),
        end_s=float(t[-1]),
        timestamp_rounding_max_s=float(residual.max()),
        vertical_unit=meta.get("Vertical Units"),
        nonfinite_samples=int((~valid).sum()),
        finite_min=float(x[valid].min()),
        finite_max=float(x[valid].max()),
        sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    )
    return t[0], dt, x, stat


def downsample(t0, dt, x, target_dt):
    ratio = target_dt / dt
    factor = int(round(ratio))
    if factor < 1 or not np.isclose(ratio, factor, atol=1e-5):
        raise ValueError(f"Unsupported noninteger rate conversion: {dt}")
    bad = np.flatnonzero(~np.isfinite(x))
    # Replacements allow FIR calculation, but ALL affected output samples
    # are subsequently excluded; repaired values are never training targets.
    clean = np.where(np.isfinite(x), x, 0.0)
    y = resample_poly(clean, 1, factor, window=("kaiser", 8.6))
    good = np.ones(len(y), dtype=bool)
    # scipy's default polyphase FIR has half-length 10*factor input samples.
    # Extra margin covers interpolation onto the common channel time grid.
    margin = 12
    good[:margin] = False
    good[-margin:] = False
    for i in bad:
        j = i // factor
        good[max(0, j - margin) : min(len(good), j + margin + 1)] = False
    return t0 + np.arange(len(y)) * target_dt, y, good, factor


def haar_energy(window):
    """Float32 Haar D1..D5,A5; fixed 32 bins. Return [6,32,3].

    Voltage first-sample subtraction limits cancellation. A5 is centered
    again by the full-window mean; details are DC-invariant. Currents retain DC.
    """
    a = np.asarray(window, dtype=np.float32).copy()
    if a.shape != (1024, 3) or not np.isfinite(a).all():
        raise ValueError("Expected finite 1024 x 3 calibrated samples")
    a[:, 0] -= a[0, 0]
    vmean = np.mean(a[:, 0], dtype=np.float32)
    result = []
    scale = np.float32(1 / np.sqrt(2))
    for level in range(1, 6):
        even, odd = a[::2], a[1::2]
        detail = (even - odd) * scale
        a = (even + odd) * scale
        result.append(np.mean(detail.reshape(32, -1, 3) ** 2, axis=1, dtype=np.float32))
    a[:, 0] -= np.float32(np.sqrt(32)) * vmean
    result.append(a * a)
    return np.asarray(result, dtype=np.float32)


def fit_image_transform(energy):
    # Per-scale, per-channel training-only statistics; never window min/max.
    scale = np.maximum(np.median(energy, axis=(0, 2)), 1e-8).astype(np.float32)
    log = np.log1p(energy / scale[None, :, None, :])
    low = np.quantile(log, 0.01, axis=(0, 2)).astype(np.float32)
    high = np.quantile(log, 0.99, axis=(0, 2)).astype(np.float32)
    span = np.maximum(high - low, 1e-4).astype(np.float32)
    return {"energy_scale": scale, "image_low": low, "image_span": span}


def make_images(energy, norm):
    log = np.log1p(energy / norm["energy_scale"][None, :, None, :])
    image = (log - norm["image_low"][None, :, None, :]) / norm["image_span"][
        None, :, None, :
    ]
    # Float grayscale in [0,1]. PNG is an 8-bit preview, not training input.
    return np.clip(image, 0, 1).astype(np.float32)


def prepare(cfg, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(Path(cfg["data_dir"]).glob("*.csv"))
    pattern = re.compile(r"tek(\d{4})CH([134])-(10|4)ms\.csv$", re.I)
    parsed = {}
    inventory = []
    for path in files:
        m = pattern.fullmatch(path.name)
        if not m:
            continue
        group, ch, view = m.group(1), int(m.group(2)), m.group(3)
        if group not in cfg["labels"]:
            raise ValueError(f"Unknown label group: {group}")
        t0, dt, x, stat = read_scope(path)
        unit = "V" if ch == 1 else "A"
        if stat["vertical_unit"] != unit:
            raise ValueError(f"{path}: expected {unit}, got {stat['vertical_unit']}")
        stat["used"] = view == "10"
        stat["exclusion_reason"] = (
            None if view == "10" else "same-acquisition zoom/crop"
        )
        inventory.append(stat)
        if view == "10":
            if ch == 1:
                x = x + cfg["voltage_add_v"]
            tt, yy, good, factor = downsample(t0, dt, x, cfg["target_dt_s"])
            stat["decimation_factor"] = factor
            parsed[group, ch] = (tt, yy, good)
        print(
            f"read {path.name}: n={stat['samples']}, dt={dt:g}, bad={stat['nonfinite_samples']}",
            flush=True,
        )
    json_write(out / "data_audit.json", inventory)
    energies, labels, splits, records, raw_windows = [], [], [], [], []
    counts = {}
    for group, target in cfg["labels"].items():
        streams = [parsed[group, ch] for ch in cfg["channel_order"]]
        start = max(a[0][0] for a in streams)
        end = min(a[0][-1] for a in streams)
        time = (
            start
            + np.arange(int(np.floor((end - start) / cfg["target_dt_s"])) + 1)
            * cfg["target_dt_s"]
        )
        data = np.empty((len(time), 3))
        good = np.ones(len(time), bool)
        for ch, (tt, yy, valid) in enumerate(streams):
            data[:, ch] = np.interp(time, tt, yy)
            idx = np.searchsorted(tt, time, side="right")
            left, right = np.clip(idx - 1, 0, len(tt) - 1), np.clip(idx, 0, len(tt) - 1)
            good &= valid[left] & valid[right]
        # Split the acquisition time BEFORE sliding windows; no crossing.
        edges = np.rint(np.r_[0, np.cumsum(cfg["split_fractions"])] * len(data)).astype(
            int
        )
        counts[group] = {}
        np.savez_compressed(
            out / f"resampled_{group}.npz",
            time_s=time,
            signal=data.astype(np.float32),
            valid=good,
        )
        for si, name in enumerate(("train", "validation", "test")):
            guard = cfg["split_guard_samples"]
            begin, stop = edges[si] + guard, edges[si + 1] - guard
            stride = cfg["train_stride"] if si == 0 else cfg["eval_stride"]
            kept = discarded = 0
            for j in range(begin, stop - cfg["window_samples"] + 1, stride):
                if not good[j : j + 1024].all():
                    discarded += 1
                    continue
                raw = data[j : j + 1024].astype(np.float32)
                energies.append(haar_energy(raw))
                labels.append(target)
                splits.append(si)
                records.append(
                    dict(
                        group=group,
                        split=name,
                        start_s=float(time[j]),
                        end_s=float(time[j + 1023]),
                        start_index=j,
                        acquisition_id=group,
                    )
                )
                raw_windows.append(raw)
                kept += 1
            counts[group][name] = dict(
                kept=kept,
                invalid_discarded=discarded,
                start_index=int(begin),
                stop_index=int(stop),
            )
            if kept == 0:
                raise ValueError(
                    f"{group}/{name}: no valid windows; inspect invalid samples / split configuration"
                )
    energy, y, split = (
        np.array(energies),
        np.array(labels, np.float32),
        np.array(splits),
    )
    norm = fit_image_transform(energy[split == 0])
    images = make_images(energy, norm)
    np.savez_compressed(
        out / "dataset.npz",
        images=images,
        labels=y,
        split=split,
        raw_windows=np.array(raw_windows),
        **norm,
    )
    json_write(out / "window_manifest.json", records)
    json_write(out / "split_counts.json", counts)
    # Preserve individual channel grayscale previews, no plotting axes/color map.
    from PIL import Image

    preview = out / "image_preview"
    preview.mkdir(exist_ok=True)
    for group in cfg["labels"]:
        i = next(i for i, rec in enumerate(records) if rec["group"] == group)
        for ch, name in enumerate(("CH1", "CH3", "CH4")):
            Image.fromarray(np.rint(images[i, :, :, ch] * 255).astype("uint8")).resize(
                (512, 96), resample=Image.Resampling.NEAREST
            ).save(preview / f"{group}_{name}.png")
    return images, y, split, norm, records, np.array(raw_windows)
