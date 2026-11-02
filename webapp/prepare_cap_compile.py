"""Accept numeric model arrays only; rebuild C from the trusted packaged kernel."""

# 编译输入只接受形状、类型与有限值通过核验的数值数组；C 代码使用随项目提供的内核重新生成，不执行上传代码。

import sys
from pathlib import Path
import shutil
import numpy as np
from zipfile import ZipFile


def main(source, output, code):
    source, output, code = map(Path, (source, output, code))
    sys.path.insert(0, str(code))
    from export_c import c_array

    shapes = {
        "cap_conv1_w": (3, 3, 3, 8),
        "cap_conv1_b": (8,),
        "cap_conv2_w": (3, 3, 8, 16),
        "cap_conv2_b": (16,),
        "cap_dense_w": (16, 8),
        "cap_dense_b": (8,),
        "cap_energy_scale": (6, 3),
        "cap_image_low": (6, 3),
        "cap_image_span": (6, 3),
        "cap_label_mean": (8,),
        "cap_label_std": (8,),
    }
    for filename, maximum in [
        ("weights.npz", 1024**2),
        ("golden_vectors.npz", 128 * 1024**2),
    ]:
        with ZipFile(source / filename) as archive:
            if (
                len(archive.infolist()) > 20
                or sum(i.file_size for i in archive.infolist()) > maximum
            ):
                raise ValueError("数值模型包展开后过大。")
    with np.load(source / "weights.npz", allow_pickle=False) as data:
        if set(data.files) != set(shapes):
            raise ValueError("模型权重字段不匹配。")
        weights = {}
        for name, shape in shapes.items():
            a = data[name]
            if (
                a.shape != shape
                or a.dtype.kind not in "fi"
                or not np.isfinite(a).all()
                or np.max(np.abs(a)) > 1e12
            ):
                raise ValueError("模型权重形状或数值不正确。")
            if (
                name in ("cap_energy_scale", "cap_image_span", "cap_label_std")
                and not (a > 0).all()
            ):
                raise ValueError("归一化尺度须为正数。")
            weights[name] = a.astype(np.float32)
    with np.load(source / "golden_vectors.npz", allow_pickle=False) as data:
        arrays = {key: data[key] for key in ("samples", "images", "predictions")}
        count = len(arrays["samples"])
        for key, shape in [
            ("samples", (count, 1024, 3)),
            ("images", (count, 6, 32, 3)),
            ("predictions", (count, 8)),
        ]:
            a = arrays[key]
            if (
                not 1 <= count <= 2048
                or a.shape != shape
                or a.dtype.kind not in "fi"
                or not np.isfinite(a).all()
            ):
                raise ValueError("核验样本尺寸或数值不正确。")
            arrays[key] = a.astype(np.float32)
    dest = output / "dsp"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "cap_weights.c").write_text(
        '#include "cap_weights.h"\n'
        + "".join(c_array(k, v) for k, v in weights.items()),
        encoding="ascii",
    )
    (dest / "cap_weights.h").write_text(
        "#ifndef CAP_WEIGHTS_H\n#define CAP_WEIGHTS_H\n"
        + "".join(f"extern const float {k}[{v.size}];\n" for k, v in weights.items())
        + "#endif\n",
        encoding="ascii",
    )
    for name in ("cap_model.c", "cap_model.h"):
        shutil.copy2(code / name, dest / name)
    np.savez(output / "golden_vectors.npz", **arrays)
    np.savez(output / "weights.npz", **weights)
    print("模型权重、预处理常量及核验样本校验通过。", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
