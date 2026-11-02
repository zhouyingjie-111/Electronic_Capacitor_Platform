"""Verify actual exported C on the Windows host and compile it for C28x.

Host build uses a test-only CRT shim and Python log1p callback because the
available MATLAB clang has no Windows SDK. DSP source is not altered.
Target compile checks source/API compatibility, not board execution/timing.
"""

import argparse
import ctypes as ct
import json
from pathlib import Path
import subprocess
import os
import shutil
import numpy as np
from pipeline import json_write


def run(args, cwd=None):
    env = os.environ.copy()
    if cwd is not None:
        env["TMP"] = env["TEMP"] = "."
    r = subprocess.run(
        [str(a) for a in args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode:
        raise RuntimeError(f"{args}\n{r.stdout}\n{r.stderr}")
    return r.stdout + r.stderr


def main():
    p = argparse.ArgumentParser()
    p.add_argument("model_dir", type=Path)
    p.add_argument(
        "--clang", default=os.environ.get("CAP_CLANG", shutil.which("clang") or "")
    )
    p.add_argument(
        "--cl2000", default=os.environ.get("CAP_CL2000", shutil.which("cl2000") or "")
    )
    p.add_argument(
        "--target-build-dir",
        type=Path,
        default=Path(__file__).resolve().parents[4] / "webapp/runtime/cap_manual_build",
    )
    args = p.parse_args()
    out = args.model_dir.resolve()
    dsp = out / "dsp"
    build = out / "verification"
    build.mkdir(exist_ok=True)
    shim = build / "host_shim"
    shim.mkdir(exist_ok=True)
    (shim / "stdint.h").write_text(
        "typedef unsigned short uint16_t;\n", encoding="ascii"
    )
    (shim / "string.h").write_text(
        "void *memset(void *, int, unsigned long long);\n", encoding="ascii"
    )
    (shim / "math.h").write_text(
        "float test_log1pf(float);\n#define log1pf test_log1pf\n"
        "#define isfinite(x) ((x)==(x) && (x)<=3.402823466e38f && (x)>=-3.402823466e38f)\n",
        encoding="ascii",
    )
    (shim / "runtime.c").write_text(
        "int _fltused=0;\nstatic float (*log_callback)(float);\n"
        "void SetLogCallback(float (*fn)(float)) { log_callback=fn; }\n"
        "float test_log1pf(float x) { return log_callback(x); }\n"
        "void *memset(void *p,int v,unsigned long long n) {"
        "volatile unsigned char *q=(volatile unsigned char*)p; "
        "while(n--) *q++=(unsigned char)v; return p; }\n",
        encoding="ascii",
    )
    objects = []
    for src in (dsp / "cap_model.c", dsp / "cap_weights.c", shim / "runtime.c"):
        obj = build / (src.stem + ".obj")
        run(
            [
                args.clang,
                "-ffreestanding",
                "-fno-builtin",
                "-O2",
                "-I",
                shim,
                "-I",
                dsp,
                "-c",
                src,
                "-o",
                obj,
            ]
        )
        objects.append(obj)
    dll = build / "cap_host.dll"
    lld = Path(args.clang).with_name("lld.exe")
    exports = [
        "SetLogCallback",
        "Cap_Reset",
        "Cap_Push",
        "Cap_MakeImage",
        "Cap_GetImage",
        "Cap_PredictImage",
        "Cap_Finish",
    ]
    run(
        [
            lld,
            "-flavor",
            "link",
            "/dll",
            "/noentry",
            "/nodefaultlib",
            "/machine:x64",
            f"/out:{dll}",
            *[f"/export:{s}" for s in exports],
            *objects,
        ]
    )
    lib = ct.CDLL(str(dll))
    callback_type = ct.CFUNCTYPE(ct.c_float, ct.c_float)
    callback = callback_type(lambda x: float(np.log1p(np.float32(x))))
    lib.SetLogCallback.argtypes = [callback_type]
    lib.SetLogCallback(callback)
    ptr = ct.POINTER(ct.c_float)
    lib.Cap_Push.argtypes = [ct.c_float] * 3
    lib.Cap_Push.restype = ct.c_int
    lib.Cap_GetImage.restype = ptr
    lib.Cap_PredictImage.argtypes = [ptr, ptr]
    lib.Cap_Finish.argtypes = [ptr]
    data = np.load(out / "golden_vectors.npz")
    feature_errors = []
    pred_errors = []
    predictions = []
    for samples, image, expected in zip(
        data["samples"], data["images"], data["predictions"]
    ):
        lib.Cap_Reset()
        for i, row in enumerate(samples):
            assert lib.Cap_Push(*map(float, row)) == (1 if i == 1023 else 0)
        assert lib.Cap_MakeImage() == 0
        feature = (
            np.ctypeslib.as_array(lib.Cap_GetImage(), shape=(576,))
            .copy()
            .reshape(6, 32, 3)
        )
        feature_errors.append(float(np.max(np.abs(feature - image))))
        result = np.empty(8, np.float32)
        assert lib.Cap_PredictImage(lib.Cap_GetImage(), result.ctypes.data_as(ptr)) == 0
        pred_errors.append(np.abs(result - expected))
        predictions.append(result)
    max_err = np.max(pred_errors, axis=0)
    assert max(feature_errors) < 2e-4, max(feature_errors)
    assert np.all(max_err[:4] < 0.02) and np.all(max_err[4:] < 2e-5), max_err
    # Genuine API edge cases: incomplete and nonfinite input must be rejected.
    lib.Cap_Reset()
    assert lib.Cap_Finish(result.ctypes.data_as(ptr)) == -1
    assert lib.Cap_Push(float("nan"), 0, 0) == -1
    assert lib.Cap_Push(0, 0, 0) == -1
    # Test the public one-call Finish path too.
    lib.Cap_Reset()
    for row in data["samples"][-1]:
        lib.Cap_Push(*map(float, row))
    assert lib.Cap_Finish(result.ctypes.data_as(ptr)) == 0
    assert np.max(np.abs(result - np.array(predictions[-1]))) < 1e-5
    print("Host C parity passed", max(feature_errors), max_err.tolist(), flush=True)
    # Legacy TI tools cannot reliably reopen their generated command files
    # under a Chinese path. Compile copies in an explicitly writable ASCII
    # scratch directory; deliver the archive back in the requested Model dir.
    target = args.target_build_dir.resolve()
    if not str(target).isascii():
        raise ValueError("--target-build-dir must contain only ASCII characters")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("cap_model.c", "cap_model.h", "cap_weights.c", "cap_weights.h"):
        shutil.copyfile(dsp / name, target / name)
    target_log = []
    for src in (dsp / "cap_model.c", dsp / "cap_weights.c"):
        target_log.append(
            run(
                [
                    args.cl2000,
                    "-v28",
                    "-ml",
                    "-mt",
                    "--float_support=fpu32",
                    "--abi=eabi",
                    "--c99",
                    "-O2",
                    f"--include_path={Path(args.cl2000).parent.parent/'include'}",
                    "--include_path=.",
                    "--obj_directory=.",
                    "--temp_directory=.",
                    "--compile_only",
                    src.name,
                ],
                cwd=target,
            )
        )
    archive = dsp / "cap_monitor_model.lib"
    ar = Path(args.cl2000).with_name("ar2000.exe")
    # r replaces existing named members, preserving repeatability.
    target_log.append(
        run([ar, "rq", archive.name, "cap_model.obj", "cap_weights.obj"], cwd=target)
    )
    shutil.copyfile(target / archive.name, archive)
    (build / "target_compile.log").write_text("\n".join(target_log), encoding="utf-8")
    json_write(
        out / "native_verification.json",
        dict(
            windows_tested=len(data["samples"]),
            max_feature_error=max(feature_errors),
            max_prediction_abs_error=max_err.tolist(),
            api_edge_cases="passed",
            host_math="test-only numpy float32 log1p callback; target RTS math still needs board parity",
            target_compiler=str(args.cl2000),
            target_compile="passed",
            target_archive=str(archive),
            board_status="NOT run on DSP; no linker-memory/timing/interrupt validation",
        ),
    )
    print(
        json.dumps(
            dict(
                feature_error=max(feature_errors),
                prediction_error=max_err.tolist(),
                target_compile="passed",
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
