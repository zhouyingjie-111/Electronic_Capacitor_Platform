"""Quantize the portal's folded static ANN and compile real F28P55x NPU layers.

Does not load pickle/checkpoints, change float models, or modify a CCS project.
Usage: python compile_ann_npu_ultralight.py --model full_static.onnx --csv data.csv --output results
"""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import onnx
from onnx import numpy_helper
import onnxruntime as ort
import torch
from torch import nn


def session(path):
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def read_layers(path):
    """Accept only the exact folded Slice/threshold/Concat + three Gemm graph."""
    m = onnx.load(path, load_external_data=False)
    if any(
        t.external_data or t.data_location == onnx.TensorProto.EXTERNAL
        for t in m.graph.initializer
    ):
        raise ValueError("模型须为内嵌权重的单个 ONNX 文件。")
    onnx.checker.check_model(m)
    g = m.graph
    if len(g.input) != 1 or len(g.output) != 1 or g.input[0].name != "raw_input":
        raise ValueError("模型输入须为 raw_input。")
    if g.input[0].type.tensor_type.elem_type != 1 or [
        d.dim_value for d in g.input[0].type.tensor_type.shape.dim
    ] != [1, 5]:
        raise ValueError("模型须为 float32 静态输入 [1,5]。")
    nodes = {out: n for n in g.node for out in n.output}
    used = set()
    constants = {t.name: numpy_helper.to_array(t) for t in g.initializer}

    def node(name, op):
        n = nodes.get(name)
        if n is None or n.op_type != op or n.domain not in ("", "ai.onnx"):
            raise ValueError("请使用本页导出的 5→8→4 完整静态 ONNX 模型。")
        used.add(n.name or n.output[0])
        return n

    def attrs(n):
        return {a.name: onnx.helper.get_attribute_value(a) for a in n.attribute}

    def const(name):
        if name not in constants:
            n = node(name, "Constant")
            constants[name] = numpy_helper.to_array(attrs(n)["value"])
        return constants[name]

    def sliced(name, start, end):
        n = node(name, "Slice")
        if (
            n.input[0] != "raw_input"
            or len(n.input) != 5
            or [const(v).tolist() for v in n.input[1:]] != [[start], [end], [1], [1]]
        ):
            raise ValueError(
                "输入顺序须为 P_peak、T_rise_fall、VC1、VG1、direction_state。"
            )

    layers = []
    out = g.output[0].name
    for index in (2, 1, 0):
        n = node(out, "Gemm")
        a = attrs(n)
        if (
            a.get("transB", 0) != 1
            or a.get("transA", 0) != 0
            or a.get("alpha", 1) != 1
            or a.get("beta", 1) != 1
            or len(n.input) != 3
        ):
            raise ValueError("暂不支持该全连接层格式。")
        w = np.asarray(const(n.input[1]), dtype=np.float32)
        b = np.asarray(const(n.input[2]), dtype=np.float32)
        if (
            w.ndim != 2
            or b.shape != (w.shape[0],)
            or not np.isfinite(w).all()
            or not np.isfinite(b).all()
        ):
            raise ValueError("模型权重无效。")
        layers.insert(0, (w.copy(), b.copy()))
        out = n.input[0]
        if index:
            out = node(out, "Relu").input[0]
    if [w.shape for w, b in layers[:2]] != [(8, 5), (4, 8)] or layers[2][
        0
    ].shape not in ((1, 4), (2, 4)):
        raise ValueError("暂支持本页导出的 5→8→4 轻量化模型。")
    n = node(out, "Concat")
    if attrs(n).get("axis") != 1 or len(n.input) != 2:
        raise ValueError("输入拼接格式不匹配。")
    sliced(n.input[0], 0, 4)
    cast = node(n.input[1], "Cast")
    if attrs(cast).get("to") != 1:
        raise ValueError("方向标志类型不匹配。")
    threshold = node(cast.input[0], "GreaterOrEqual")
    sliced(threshold.input[0], 4, 5)
    if (
        const(threshold.input[1]).size != 1
        or float(const(threshold.input[1]).reshape(-1)[0]) != 0.5
    ):
        raise ValueError("方向标志阈值不匹配。")
    if len(used) != len(g.node):
        raise ValueError("模型含额外运算，请重新导出本页完整静态模型。")
    output = g.output[0].type.tensor_type
    if output.elem_type != 1 or [d.dim_value for d in output.shape.dim] != [
        1,
        layers[-1][0].shape[0],
    ]:
        raise ValueError("模型输出尺寸不匹配。")
    return layers


def dataset(path):
    raw = Path(path).read_bytes()
    if len(raw) > 20 * 1024**2:
        raise ValueError("校准 CSV 最大 20 MB。")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gb18030")
    columns = [
        "P_peak",
        "T_rise_fall",
        "VC1_UP",
        "VG1_UP",
        "VC1_DOWN",
        "VG1_DOWN",
        "D_T_UP_given",
        "D_T_DOWN_given",
    ]
    groups = {}
    for i, row in enumerate(csv.DictReader(text.splitlines())):
        if i >= 100000:
            raise ValueError("校准数据不能超过 100000 行。")
        values = [float(row[c]) for c in columns]
        if not np.isfinite(values).all() or values[0] <= 0 or values[1] <= 0:
            raise ValueError("校准数据包含无效数值。")
        groups.setdefault(tuple(values[:6]), []).append(values[6:])
    if len(groups) < 5:
        raise ValueError("至少需要 5 组不同工况。")
    keys = sorted(groups)
    n = len(keys)
    perm = np.random.default_rng(42).permutation(n)
    ntrain = round(n * 0.7)
    nval = round(n * 0.15)
    if nval <= 0:
        nval = 1
        ntrain -= 1
    if n - ntrain - nval <= 0:
        ntrain -= 1
    split = {
        int(k): (
            "calibration"
            if j < ntrain
            else "validation" if j < ntrain + nval else "comparison"
        )
        for j, k in enumerate(perm)
    }
    x = []
    truth = []
    kinds = []
    ids = []
    for i, key in enumerate(keys):
        p, t, vu, gu, vd, gd = key
        y = np.mean(groups[key], axis=0)
        for direction, vc, vg, value in [(0, vu, gu, y[0]), (1, vd, gd, y[1])]:
            x.append([p, t, vc, vg, direction])
            truth.append(value)
            kinds.append(split[i])
            ids.append(i)
    return (
        np.asarray(x, np.float32),
        np.asarray(truth, np.float32),
        np.asarray(kinds),
        ids,
    )


def metrics(pred, truth):
    d = np.asarray(pred, dtype=np.float64) - truth
    return {
        "mae": float(np.mean(abs(d))),
        "rmse": float(np.sqrt(np.mean(d * d))),
        "max_abs_error": float(max(abs(d))),
    }


def padded_model(layers, mean, std, ymean, ystd):
    widths = [16, 16, 16, layers[-1][0].shape[0]]
    modules = []
    for i, (weight, bias) in enumerate(layers):
        w = weight.astype(np.float64)
        b = bias.astype(np.float64)
        if i == 0:
            b = b + w @ mean
            w = w * std
        if i == 2:
            w = w / ystd[:, None]
            b = (b - ymean) / ystd
        layer = nn.Linear(widths[i], widths[i + 1])
        layer.weight.data.zero_()
        layer.bias.data.zero_()
        layer.weight.data[: len(b), : w.shape[1]] = torch.from_numpy(
            w.astype(np.float32)
        )
        layer.bias.data[: len(b)] = torch.from_numpy(b.astype(np.float32))
        modules.append(layer)
        if i < 2:
            modules.append(nn.ReLU())
    return nn.Sequential(*modules).eval()


def compile_model(work, cgt):
    env = os.environ.copy()
    env["PATH"] = str(cgt / "bin") + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["FORCE_NO_TI_C28_SOFT_OFFLOAD"] = "true"
    temp = work / "temp"
    temp.mkdir()
    env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(temp)
    options = f"--float_support=fpu32 --abi=eabi -O3 --opt_for_speed=5 --c99 -v28 -ml -mt --gen_func_subsections -I{cgt.as_posix()}/include -I."
    cmd = [
        sys.executable,
        "-m",
        "tvm.driver.tvmc",
        "compile",
        "--target=c, ti-npu",
        "--target-c-mcpu=c28",
        "model_npu.onnx",
        "-o",
        "artifacts/mod.a",
        "--cross-compiler=cl2000",
        "--cross-compiler-options=" + options,
    ]
    with (work / "compile.log").open("w", encoding="utf-8") as log:
        with subprocess.Popen(
            cmd,
            cwd=work,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
                log.flush()
            if process.wait():
                raise RuntimeError("TI NPU 编译失败，请查看日志。")
    return verify_hardware(work)


def verify_hardware(work):
    h = (work / "artifacts/tvmgen_default.h").read_text(encoding="utf-8")
    if not re.search(r"^\s*#define\s+TVMGEN_DEFAULT_TI_NPU\s*$", h, re.M) or re.search(
        r"^\s*#define\s+TVMGEN_DEFAULT_TI_NPU_SOFT\b", h, re.M
    ):
        raise RuntimeError("未生成硬件 NPU 模型，不能作为加速产物下载。")
    if not (work / "artifacts/mod.a").is_file():
        raise RuntimeError("缺少编译模型库。")
    log = (work / "compile.log").read_text(encoding="utf-8")
    sources = "\n".join(
        f.read_text(encoding="utf-8", errors="replace")
        for f in (work / "artifacts").rglob("*.c")
    )
    offloaded = re.search(r":\s*FC\s+(\d+)\s*$", log, re.M)
    calls = set(
        re.findall(r"tvmgen_default_ti_npu_fullyConnectedNHWC_(\d+)\s*\(", sources)
    )
    if (
        not offloaded
        or int(offloaded[1]) != 3
        or len(calls) != 3
        or "TINIE_start()" not in sources
        or sources.count("tinie_curr_layer += 1;") != 3
        or re.search(r"\bTINIE_fullyConnected\s*\(", sources)
    ):
        raise RuntimeError("全连接计算层未全部映射到 NPU，拒绝发布软件回退产物。")
    return h


def write_adapter(output, cgt, mean, std, ymean, ystd, raw, predicted, compare):
    def floats(values):
        return ", ".join(format(float(v), ".9e") + "f" for v in values)

    header = """#ifndef ANN_NPU_ADAPTER_H
#define ANN_NPU_ADAPTER_H
#include <stdint.h>
/* Background/main context only. Call after Board_init/NPU configuration.
 * start: 0 accepted, 1 busy, -1 invalid input, -2 launch failed.
 * poll: 1 result available, 0 pending/idle, -1 invalid pointer, -2 invalid result.
 * No waiting loop and no changes to existing control interrupts. */
int16_t ann_npu_start(const float raw_input[5]);
int16_t ann_npu_poll(float *dt_given);
uint16_t ann_npu_busy(void);
#endif
"""
    source = (
        """#include "ann_npu_adapter.h"
#include "artifacts/tvmgen_default.h"
#include <math.h>
#ifndef TVMGEN_DEFAULT_TI_NPU
#error A hardware NPU model library is required.
#endif
static float input_buffer[16];
static float output_buffer[2];
static uint16_t pending=0U;
static struct tvmgen_default_inputs inputs={input_buffer};
static struct tvmgen_default_outputs outputs={output_buffer};
"""
        + f"static const float mean[5]={{{floats(mean)}}};\nstatic const float std[5]={{{floats(std)}}};\n"
        + f"static const float output_mean={floats(ymean[:1])};\nstatic const float output_std={floats(ystd[:1])};\n"
        + """
uint16_t ann_npu_busy(void) { return pending; }
int16_t ann_npu_start(const float raw_input[5]) {
    uint16_t i;
    if (pending) return 1;
    if (!raw_input) return -1;
    for (i=0U;i<5U;i++) if (!isfinite(raw_input[i])) return -1;
    if (raw_input[0]<=0.0f || raw_input[1]<=0.0f) return -1;
    for (i=0U;i<4U;i++) input_buffer[i]=(raw_input[i]-mean[i])/std[i];
    input_buffer[4]=(raw_input[4]>=0.5f)?1.0f:0.0f;
    for (i=5U;i<16U;i++) input_buffer[i]=0.0f;
    for (i=0U;i<5U;i++) if (!isfinite(input_buffer[i])) return -1;
    pending=1U;
    if (tvmgen_default_run(&inputs,&outputs)!=0) { pending=0U; return -2; }
    return 0;
}
int16_t ann_npu_poll(float *dt_given) {
    float value;
    if (!dt_given) return -1;
    if (!pending || !tvmgen_default_finished) return 0;
    value=output_buffer[0]*output_std+output_mean;
    pending=0U;
    if (!isfinite(value)) return -2;
    *dt_given=value;
    return 1;
}
"""
    )
    (output / "ann_npu_adapter.h").write_text(header, encoding="utf-8")
    (output / "ann_npu_adapter.c").write_text(source, encoding="utf-8")
    vectors = raw[compare]
    values = predicted[compare, 0]
    (output / "ann_npu_test_vectors.h").write_text(
        "#ifndef ANN_NPU_TEST_VECTORS_H\n#define ANN_NPU_TEST_VECTORS_H\n"
        + f"#define ANN_NPU_TEST_COUNT {len(values)}U\nstatic const float ann_npu_test_input[{len(values)}][5]={{\n"
        + ",\n".join("{" + floats(row) + "}" for row in vectors)
        + "\n};\n"
        + f"static const float ann_npu_test_expected[{len(values)}]={{{floats(values)}}};\n#endif\n",
        encoding="utf-8",
    )
    (output / "README_NPU.md").write_text(
        """# F28P55x ANN NPU deployment

This package is compiled, not flashed or tested on hardware. It does not modify a CCS project.
All three dense layers must use NHWC hardware microcode and completion transitions; a hardware header macro or offloading report alone is insufficient. The compiler sets FORCE_NO_TI_C28_SOFT_OFFLOAD=true to disable the default C28 software FC fallback.
The model is an equivalent zero-padded 16→16→16→output representation of the original 5→8→4 network, followed by TI 8-bit PTQ. Normalization/de-normalization remain host arithmetic; dense computation runs on the NPU.

## Files and integration
- Add artifacts/mod.a, artifacts/tvmgen_default.h and ann_npu_adapter.c/.h as a matched set. Do not also compile artifacts/lib*.c alongside mod.a.
- Replace the old generated model library in a separate CCS configuration; both libraries use tvmgen_default symbols and must not be linked together.
- Use F28P55x, EABI, fpu32 and a compatible C2000Ware AI/NPU runtime. This build uses TI C2000 compiler 25.11.0.LTS and was not linked against an application.
- Initialize NPU clocks and interrupt with the installed C2000Ware f28p55x AI example/SysConfig before the first call. NPU completion uses its own interrupt; do not call this API from the control ISR or disable existing control interrupts. Global interrupt enable and priorities must be reviewed in the real application.
- Place .rodata.tvm in flash and .bss.noinit.tvm in available NPU-accessible global shared SRAM (RAMGS0/1/2/3). Merge these sections into the actual linker file after checking existing memory allocations; do not copy an entire example linker configuration over a control project.
- Call ann_npu_start(raw) from the background/main loop. Input order: P_peak, T_rise_fall (seconds), VC1, VG1, direction_state. Return 0 means accepted. Then call ann_npu_poll(&dt_given) in later background iterations; 1 means ready, 0 means pending/idle. Input/output buffers stay static while pending. Never busy-wait in a control interrupt. On a stalled result, use an application deadline and preserve the existing PI/fallback behavior; do not reset/relaunch an active NPU through this adapter.
- Only DTgiven is exposed by the adapter. Preserve existing duty clamps, edge timing, fallback and control update logic when integrating.
- ann_npu_test_vectors.h and comparison.csv provide real quantized ONNX golden outputs, not measured device outputs. Verify board parity, data range, end-to-end inference time, ISR jitter and deadlines before enabling the model in the control loop. For this small ANN, accelerator setup overhead can exceed the computation saved; speedup must be measured.

## Validation
The report separates floating prediction error, quantized prediction error and quantization drift. Calibration uses the training-side split only; the comparison split excludes calibration. For uploaded models, membership in their original training set cannot be proven. Original floating models and training settings are unchanged.

Official references:
- https://software-dl.ti.com/mctools/nnc/mcu/v2.1.0/compiling.html
- https://software-dl.ti.com/mctools/nnc/mcu/v2.1.0/ti-npu-qat.html
""",
        encoding="utf-8",
    )
    compiler = cgt / "bin/cl2000.exe"
    built = subprocess.run(
        [
            str(compiler),
            "--float_support=fpu32",
            "--abi=eabi",
            "--c99",
            "-v28",
            "-ml",
            "-mt",
            "--compile_only",
            "--include_path=.",
            "--include_path=" + str(cgt / "include"),
            "--output_file=ann_npu_adapter.obj",
            "ann_npu_adapter.c",
        ],
        cwd=output,
        capture_output=True,
        text=True,
        errors="replace",
    )
    (output / "adapter_compile.log").write_text(
        built.stdout + built.stderr, encoding="utf-8"
    )
    if built.returncode:
        raise RuntimeError("异步调用代码的目标编译未通过。")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--cgt", type=Path, default=os.environ.get("TI_CGT_ROOT", ""))
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(1)
    torch.manual_seed(42)
    layers = read_layers(a.model)
    raw, truth, kinds, ids = dataset(a.csv)
    cal = kinds == "calibration"
    compare = kinds == "comparison"
    features = raw.copy()
    features[:, 4] = features[:, 4] >= 0.5
    mean = features[cal].mean(0).astype(np.float64)
    std = features[cal].std(0).astype(np.float64)
    mean[4] = 0
    std[4] = 1
    std[std < 1e-12] = 1
    teacher = session(a.model)
    expected = np.concatenate(
        [teacher.run(None, {"raw_input": r[None]})[0] for r in raw]
    )
    ymean = expected[cal].mean(0).astype(np.float64)
    ystd = expected[cal].std(0).astype(np.float64)
    ystd[ystd < 1e-6] = 1
    inputs = np.zeros((len(raw), 16), np.float32)
    inputs[:, :5] = (features - mean.astype(np.float32)) / std.astype(np.float32)
    core = padded_model(layers, mean, std, ymean, ystd)
    with torch.no_grad():
        floating = core(torch.from_numpy(inputs)).numpy() * ystd + ymean
    error = float(np.max(abs(floating - expected)))
    if error > 3e-5:
        raise RuntimeError("等价变换核验未通过，不继续量化。")
    print(f"浮点等价核验通过，最大偏差 {error:.8g}。", flush=True)
    print(
        f"使用 {int(cal.sum())} 条训练侧样本进行 TI 8 位量化；{int(compare.sum())} 条留出样本用于对比。",
        flush=True,
    )
    from tinyml_torchmodelopt.quantization import TINPUTinyMLPTQFxModule

    q = TINPUTinyMLPTQFxModule(
        core,
        example_inputs=(torch.from_numpy(inputs[:1]),),
        total_epochs=2,
        output_int=False,
        verbose=False,
    )
    q.train()
    with torch.no_grad():
        for batch in np.array_split(inputs[cal], max(1, int(np.ceil(cal.sum() / 32)))):
            q(torch.from_numpy(batch))
    q.eval()
    q.convert()
    # The installed TI wrapper uses 4D broadcast constants for input/output
    # quantization by default. Keep this FC graph 2D so NNC can match FC+BNORM.
    with torch.no_grad():
        before = q(torch.from_numpy(inputs)).numpy().reshape(len(raw), -1)
    for module in q.module.modules():
        for name, value in list(module.named_buffers(recurse=False)):
            if value.ndim == 4 and value.shape[0] == 1 and value.shape[2:] == (1, 1):
                module._buffers[name] = value.reshape(1, -1)
        if (
            isinstance(getattr(module, "value", None), torch.Tensor)
            and module.value.ndim == 4
        ):
            module.value = module.value.reshape(1, -1)
    with torch.no_grad():
        qreference = q(torch.from_numpy(inputs)).numpy().reshape(len(raw), -1)
    if not np.array_equal(before, qreference):
        raise RuntimeError("全连接布局调整核验未通过。")
    work = Path(tempfile.mkdtemp(prefix="ann_npu_", dir=tempfile.gettempdir()))
    if not str(work).isascii() or " " in str(work):
        raise RuntimeError("TI 编译临时目录须为无空格英文路径。")
    q.export(
        torch.from_numpy(inputs[:1]),
        filename=str(work / "model_npu.onnx"),
        input_names=["npu_input"],
        output_names=["npu_prediction"],
        dynamo=False,
    )
    quant = session(work / "model_npu.onnx")
    qy = np.concatenate(
        [quant.run(None, {"npu_input": r[None]})[0].reshape(1, -1) for r in inputs]
    )
    export_error = float(np.max(abs(qy - qreference)))
    print(f"量化导出数值偏差：{export_error:.8g}", flush=True)
    if export_error > 1e-4:
        raise RuntimeError("量化模型导出核验未通过。")
    predicted = qy * ystd.astype(np.float32) + ymean.astype(np.float32)
    report = {
        "device": "F28P55x",
        "quantization": "TI PTQ 8-bit",
        "original_architecture": [5, 8, 4, layers[-1][0].shape[0]],
        "padded_architecture": [16, 16, 16, layers[-1][0].shape[0]],
        "calibration_samples": int(cal.sum()),
        "comparison_samples": int(compare.sum()),
        "float_equivalence_max_abs_error": error,
        "onnx_export_max_abs_error": export_error,
        "float_metrics": metrics(expected[compare, 0], truth[compare]),
        "quantized_metrics": metrics(predicted[compare, 0], truth[compare]),
        "quantization_delta": metrics(predicted[compare, 0], expected[compare, 0]),
        "board_validated": False,
        "latency_us": None,
        "model_sha256": hashlib.sha256(a.model.read_bytes()).hexdigest(),
        "dataset_sha256": hashlib.sha256(a.csv.read_bytes()).hexdigest(),
        "evaluation": "Comparison split excludes PTQ calibration; original model training membership is not independently proven for uploaded models.",
    }
    print("量化导出核验通过；开始硬件 NPU 编译。", flush=True)
    header = compile_model(work, a.cgt)
    report.update(
        hardware_verified=True,
        hardware_layers=3,
        total_dense_layers=3,
        adapter_compiled=True,
        force_no_ti_c28_soft_offload=True,
        hardware_verification="Three NHWC microcode layers and three completion transitions; software FC rejected.",
    )
    shutil.copytree(work / "artifacts", a.output / "artifacts", dirs_exist_ok=True)
    shutil.copy2(work / "model_npu.onnx", a.output / "model_npu.onnx")
    shutil.copy2(work / "compile.log", a.output / "compile.log")
    write_adapter(a.output, a.cgt, mean, std, ymean, ystd, raw, predicted, compare)
    shutil.copy2(Path(__file__), a.output / "compile_ann_npu_ultralight.py")
    np.savez(
        a.output / "normalization.npz",
        input_mean=mean.astype(np.float32),
        input_std=std.astype(np.float32),
        output_mean=ymean.astype(np.float32),
        output_std=ystd.astype(np.float32),
    )
    with (a.output / "comparison.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "split",
                "case_id",
                "P_peak",
                "T_rise_fall",
                "VC1",
                "VG1",
                "direction_state",
                "reference_DTgiven",
                "float_DTgiven",
                "quantized_DTgiven",
            ]
        )
        for i in range(len(raw)):
            writer.writerow(
                [kinds[i], ids[i], *raw[i], truth[i], expected[i, 0], predicted[i, 0]]
            )
    (a.output / "npu_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        "硬件 NPU 编译完成，DTgiven 留出对比："
        + json.dumps(report["quantized_metrics"]),
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("NPU 编译未完成：" + str(e), file=sys.stderr, flush=True)
        sys.exit(1)
