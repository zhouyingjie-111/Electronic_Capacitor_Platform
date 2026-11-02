# -*- coding: utf-8 -*-
"""在 VS Code 的 TI MCU NNC Python 环境中运行。仅编译，不训练、不修改 CCS 工程。"""
from pathlib import Path
from datetime import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

# ====================== 用户配置 ======================
# 最新5->8->4->2模型；直接编译full_static，不再额外标准化或反标准化。
MODEL_DIR = Path(
    os.environ.get(
        "ANN_MODEL_DIR",
        str(
            Path(__file__).resolve().parents[2]
            / "OutputData/ann_up_down_5in2out_retrained_ultralight"
        ),
    )
)
MODEL_NAME = os.environ.get("ANN_MODEL_FILE", "ann_up_down_5in2out_full_static.onnx")
CGT_DIR = Path(os.environ.get("TI_CGT_ROOT", ""))
# 必须可写、纯英文且不含空格；每次运行创建独立子目录，保留日志。
BUILD_ROOT = Path(
    os.environ.get("ANN_BUILD_ROOT", str(Path(tempfile.gettempdir()) / "ann_compile"))
)
# ====================================================


def validate_artifacts(artifact_dir):
    library = artifact_dir / "mod.a"
    header = artifact_dir / "tvmgen_default.h"
    for path in (library, header):
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"编译未生成有效文件：{path}")
    text = header.read_text(encoding="utf-8", errors="replace")
    if "#define TVMGEN_DEFAULT_TI_NPU_SOFT" not in text:
        raise RuntimeError("生成头文件未标记 CPU/soft 模型，请检查编译日志。")
    for token in ("tvmgen_default_run", "raw_input"):
        if token not in text:
            raise RuntimeError(f"生成接口缺少 {token}，暂不替换原工程。")
    return library, header


def main():
    print(f"当前 Python：{sys.executable}", flush=True)
    model = MODEL_DIR / MODEL_NAME
    compiler = CGT_DIR / "bin" / "cl2000.exe"
    if not model.is_file():
        raise FileNotFoundError(f"找不到模型：{model}")
    if not compiler.is_file():
        raise FileNotFoundError(
            f"找不到编译器：{compiler}\n请在 config.json 设置 TI_CGT_ROOT。"
        )
    for path in (CGT_DIR, BUILD_ROOT):
        if not str(path).isascii() or any(c.isspace() for c in str(path)):
            raise ValueError(f"TI 工具工作路径必须为纯英文且不含空格：{path}")
    if importlib.util.find_spec("tvm") is None:
        raise RuntimeError(
            "当前 Python 未安装 TI MCU NNC。\n"
            "在 VS Code 按 Ctrl+Shift+P，选择 Python: Select Interpreter，\n"
            "切换至之前成功生成 mod.a 的 TI MCU NNC 环境，再运行本脚本。\n"
            "普通训练环境不一定有 NNC；不要安装同名通用 tvm 包替代 TI 版本。"
        )

    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ann_cpu_", dir=str(BUILD_ROOT)))
    shutil.copy2(model, work / "model.onnx")
    artifacts = work / "artifacts_cpu"
    artifacts.mkdir()
    env = os.environ.copy()
    env["PATH"] = str(CGT_DIR / "bin") + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    # NNC 或交叉编译器创建的临时文件也使用英文路径。
    temp_dir = work / "temp"
    temp_dir.mkdir()
    env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(temp_dir)

    options = (
        "--float_support=fpu32 --abi=eabi -O3 --opt_for_speed=5 "
        "--c99 -v28 -ml -mt --gen_func_subsections "
        f"-I{CGT_DIR.as_posix()}/include -I."
    )
    # 使用当前 Python 环境的 TI TVMC，避免 PATH 中同名命令指向其他环境。
    command = [
        sys.executable,
        "-m",
        "tvm.driver.tvmc",
        "compile",
        "--target=c, ti-npu type=soft",
        "--target-c-mcpu=c28",
        "model.onnx",
        "-o",
        "artifacts_cpu/mod.a",
        "--cross-compiler=cl2000",
        f"--cross-compiler-options={options}",
    ]
    log = work / "compile.log"
    print(f"编译工作目录：{work}", flush=True)
    print("开始 CPU float32 编译，模型优化等级 O3……", flush=True)
    with log.open("w", encoding="utf-8") as stream:
        stream.write("Python: " + sys.executable + "\n")
        stream.write("Command: " + subprocess.list2cmdline(command) + "\n")
        stream.flush()
        with subprocess.Popen(
            command,
            cwd=str(work),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        ) as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                stream.write(line)
                stream.flush()
            return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(
            f"编译失败，返回码 {return_code}。\n完整日志：{log}\n"
            "若提示 No module named tvm.driver.tvmc 或 ti-npu 不支持，"
            "请确认当前环境安装的是 TI MCU NNC 2.1.2，而非通用 Apache TVM。"
        )
    library, header = validate_artifacts(artifacts)

    # 不覆盖旧编译结果；仅将成功生成的整套文件复制回模型目录。
    output = MODEL_DIR / (
        "dsp_cpu_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + work.name
    )
    output.mkdir()
    shutil.copytree(artifacts, output / "artifacts_cpu")
    shutil.copy2(log, output / "compile.log")
    for name in ("ann_test_vectors.h", "deployment_manifest.json"):
        source = MODEL_DIR / name
        if source.is_file():
            shutil.copy2(source, output / name)
    (output / "build_settings.json").write_text(
        json.dumps(
            {
                "source_model": str(model),
                "python": sys.executable,
                "compiler": str(compiler),
                "target": "c, ti-npu type=soft",
                "command": command,
                "work_directory": str(work),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("\n编译成功。以下两个文件须成套替换原 CCS 工程中的同名文件：")
    print(output / "artifacts_cpu" / library.name)
    print(output / "artifacts_cpu" / header.name)
    print("保留原 nn_cpu_test.c/.h 与控制逻辑；输出结构成员名以新头文件为准。")
    print("不要把生成的 lib*.c 与 mod.a 同时加入工程，否则可能重复定义。")
    print("本脚本未修改 CCS 工程，也未执行板上验证。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\n操作未完成：{exc}", file=sys.stderr, flush=True)
        sys.exit(1)
