from pathlib import Path
import shutil
import hashlib
import numpy as np
from pipeline import json_write


def literal(v):
    # Scientific notation ensures valid C float constants, including zero.
    return f"{float(v):.9e}f"


def c_array(name, array):
    flat = np.asarray(array, np.float32).ravel()
    body = ",\n".join(
        "    " + ", ".join(literal(v) for v in flat[i : i + 6])
        for i in range(0, len(flat), 6)
    )
    return f"const float {name}[{len(flat)}] = {{\n{body}\n}};\n"


def reference_cnn(image, weights):
    # A separate slow scalar-order implementation, matching the C kernel.
    def conv(a, w, b, top, oh):
        ih, iw, ci = a.shape
        co = len(b)
        result = np.empty((oh, iw // 2, co), np.float32)
        for y in range(oh):
            for x in range(iw // 2):
                acc = b.copy()
                for ky in range(3):
                    iy = y * 2 + ky - top
                    if not 0 <= iy < ih:
                        continue
                    for kx in range(3):
                        ix = x * 2 + kx
                        if ix >= iw:
                            continue
                        for c in range(ci):
                            acc += a[iy, ix, c] * w[ky, kx, c, :]
                result[y, x] = np.maximum(acc, 0)
        return result

    a = conv(image, weights["cap_conv1_w"], weights["cap_conv1_b"], 0, 3)
    a = conv(a, weights["cap_conv2_w"], weights["cap_conv2_b"], 1, 2)
    pooled = np.zeros(16, np.float32)
    for row in a.reshape(-1, 16):
        pooled += row
    pooled /= 16
    acc = weights["cap_dense_b"].copy()
    for c in range(16):
        acc += pooled[c] * weights["cap_dense_w"][c]
    return acc * weights["cap_label_std"] + weights["cap_label_mean"]


def export(model, norm, mean, std, images, raw, pred, out, cfg):
    dest = out / "dsp"
    dest.mkdir(exist_ok=True)
    weights = {}
    for layer, name in (
        ("conv1", "conv1"),
        ("conv2", "conv2"),
        ("regression", "dense"),
    ):
        w, b = model.get_layer(layer).get_weights()
        weights[f"cap_{name}_w"] = w
        weights[f"cap_{name}_b"] = b
    weights.update({f"cap_{k}": v for k, v in norm.items()})
    weights.update(cap_label_mean=mean, cap_label_std=std)
    np.savez(out / "weights.npz", **weights)
    (out / "architecture.json").write_text(model.to_json(), encoding="utf-8")
    source = '#include "cap_weights.h"\n\n' + "\n".join(
        c_array(k, v) for k, v in weights.items()
    )
    header = "#ifndef SCOPE_CAP_WEIGHTS_H\n#define SCOPE_CAP_WEIGHTS_H\n"
    header += "\n".join(
        f"extern const float {k}[{v.size}];" for k, v in weights.items()
    )
    header += "\n#endif\n"
    (dest / "cap_weights.c").write_text(source, encoding="ascii")
    (dest / "cap_weights.h").write_text(header, encoding="ascii")
    for name in ("cap_model.c", "cap_model.h"):
        shutil.copyfile(Path(__file__).with_name(name), dest / name)
    # Golden vectors use already calibrated physical values (scope correction
    # applied), so DSP must not add the correction a second time.
    vector = '#include "cap_model.h"\n'
    vector += c_array("cap_test_samples", raw[-1])
    vector += c_array("cap_test_image", images[-1])
    vector += c_array("cap_test_expected", pred[-1])
    (dest / "cap_golden.c").write_text(vector, encoding="ascii")
    np.savez(out / "golden_vectors.npz", samples=raw, images=images, predictions=pred)
    ref = np.array([reference_cnn(a, weights) for a in images])
    err = np.max(np.abs(ref - pred), axis=0)
    if np.any(err[:4] > 0.01) or np.any(err[4:] > 1e-5):
        raise AssertionError(f"Python scalar C-order parity failed: {err}")
    json_write(
        out / "export_check.json",
        {
            "check": "TensorFlow versus independent scalar C-order Python kernels; not board execution",
            "max_abs_error_per_output": err.tolist(),
            "weights_sha256": hashlib.sha256(source.encode("ascii")).hexdigest(),
            "network_parameter_count": 1528,
            "float_weight_bytes_8bit": 6112,
            "cnn_MAC_upper_bound": 28928,
            "preprocessing_and_model_contract": {
                "physical_units": ["V", "A", "A"],
                "output_units": ["uF"] * 4 + ["Ohm"] * 4,
                "input_layout": "HWC",
                "shape": [6, 32, 3],
                "sample_dt_s": 1e-5,
                "samples_per_window": 1024,
                "wavelet": "5-level Haar D1..D5,A5",
                "conv1_padding_TBLR": [0, 1, 0, 1],
                "conv2_padding_TBLR": [1, 1, 0, 1],
                "scope_voltage_offset_v": cfg["voltage_add_v"],
                "scope_decimation": "offline resample_poly Kaiser8.6; target hardware requires appropriate analog filtering",
                "deployment_status": "C source exported; target compile and board validation separate",
            },
        },
    )
