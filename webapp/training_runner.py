# 任务约定：训练与编译分别执行，上传数据先做快照；日志经清理后供网页轮询，失败保留已有产物，不伪造成功状态。

import sys

"""Local ANN training/compilation jobs; Flask never imports the ML runtime."""
import csv
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid
import zipfile


REQUIRED_COLUMNS = (
    "P_peak",
    "T_rise_fall",
    "VC1_UP",
    "VG1_UP",
    "VC1_DOWN",
    "VG1_DOWN",
    "D_T_UP_given",
    "D_T_DOWN_given",
    "PWM_S_UP_first",
    "PWM_S_DOWN_first",
)
ACTIVE = {"queued", "training", "compiling"}


class TrainingError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def preview_csv(path, limit=8, validate=False):
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise TrainingError("CSV 编码无法读取，请使用 UTF-8 或 GBK。")
    try:
        reader = csv.reader(io.StringIO(text))
        header = [name.strip() for name in next(reader)]
        rows = [row for row in reader if any(value.strip() for value in row)]
    except (StopIteration, csv.Error):
        raise TrainingError("CSV 为空或格式不正确。") from None
    if len(rows) > 100000:
        raise TrainingError("数据不能超过 100000 行。")
    if validate:
        missing = [name for name in REQUIRED_COLUMNS if name not in header]
        if missing:
            raise TrainingError("缺少必要列：" + "、".join(missing))
        if len(set(name for name in header if name)) != len(
            [name for name in header if name]
        ):
            raise TrainingError("CSV 不能包含重复列名。")
        indices = [header.index(name) for name in REQUIRED_COLUMNS]
        cases = set()
        for number, row in enumerate(rows, 2):
            try:
                values = [float(row[index]) for index in indices]
                if not all(math.isfinite(value) for value in values):
                    raise ValueError()
            except (ValueError, IndexError):
                raise TrainingError(
                    f"第 {number} 行必要列包含空值或非有限数值。"
                ) from None
            if values[0] <= 0 or values[1] <= 0:
                raise TrainingError(f"第 {number} 行脉冲峰值和边沿时间必须大于 0。")
            if not all(0 <= value <= 1 for value in values[6:]):
                raise TrainingError(f"第 {number} 行瞬态占空比必须在 0～1 之间。")
            cases.add(tuple(values[:6]))
        if len(cases) < 5:
            raise TrainingError(
                "至少需要 5 组不同的有效工况，才能划分训练、验证和测试集。"
            )
    column_indices = [
        (index, name)
        for index, name in enumerate(header)
        if name and name not in {"仿真文件", "仿真编号"}
    ]
    time_index = header.index("T_rise_fall") if "T_rise_fall" in header else None

    def in_range(row):
        try:
            return (
                time_index is not None
                and 4e-5 - 1e-12 <= float(row[time_index]) <= 6e-5 + 1e-12
            )
        except (ValueError, IndexError):
            return False

    display = [row for row in rows if in_range(row)][:limit]
    return {
        "columns": [name for _, name in column_indices],
        "rows": [
            [row[index] if index < len(row) else "" for index, _ in column_indices]
            for row in display
        ],
        "total": len(rows),
    }


class TrainingRunner:
    def __init__(self, delta_root, runtime_dir, python=None):
        self.delta_root, self.runtime = Path(delta_root), Path(runtime_dir)
        self.python = python or os.environ.get("ANN_PYTHON", sys.executable)
        self.script = self.delta_root / "Code/python/ANN_5IN2OUT_ultralight.py"
        self.compiler = self.delta_root / "Code/python/compile_ann_cpu_ultralight.py"
        self.npu_compiler = (
            self.delta_root / "Code/python/compile_ann_npu_ultralight.py"
        )
        self.default_csv = self.delta_root / "OutputData/仿真结果汇总.csv"
        self.jobs, self.uploads = {}, {}
        self.lock = threading.RLock()
        self.active_id = self.latest_id = self.latest_training = (
            self.latest_compilation
        ) = None
        self._restore()

    def _restore(self):
        for record in sorted(
            self.runtime.glob("*/record.json"), key=lambda path: path.stat().st_mtime
        ):
            try:
                data = json.loads(record.read_text(encoding="utf-8"))
                if data["id"] != record.parent.name or not re.fullmatch(
                    r"[a-f0-9]{32}", data["id"]
                ):
                    continue
                if data["state"] in ACTIVE:
                    continue
                data.update(
                    folder=record.parent,
                    process=None,
                    logs=[
                        clean
                        for line in (record.parent / "progress.log")
                        .read_text(encoding="utf-8")
                        .splitlines()
                        if (
                            clean := self._public_log(
                                line, accelerator=data.get("target") == "npu"
                            )
                        )
                        is not None
                    ],
                )
                self.jobs[data["id"]] = data
                self.latest_id = data["id"]
                if data["training_completed"] and self._trained_model(data):
                    self.latest_training = data["id"]
                if data["kind"] == "compile":
                    self.latest_compilation = data["id"]
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def upload(self, file, kind):
        suffix, maximum = (
            (".csv", 20 * 1024**2) if kind == "dataset" else (".onnx", 100 * 1024**2)
        )
        if not file or Path(file.filename or "").suffix.lower() != suffix:
            raise TrainingError("请选择 " + suffix + " 文件。")
        data = file.stream.read(maximum + 1)
        if not data or len(data) > maximum:
            raise TrainingError(f"文件不能为空，且不能超过 {maximum // 1024**2} MB。")
        token = uuid.uuid4().hex
        directory = self.runtime / "uploads" / token
        directory.mkdir(parents=True)
        path = directory / ("dataset.csv" if kind == "dataset" else "model.onnx")
        path.write_bytes(data)
        preview = preview_csv(path, validate=True) if kind == "dataset" else None
        with self.lock:
            self.uploads[token] = {"path": path, "kind": kind}
        return {
            "token": token,
            "name": Path(file.filename.replace("\\", "/")).name,
            "preview": preview,
        }

    def dataset(self, token=None):
        if not token or token == "default":
            return self.default_csv
        with self.lock:
            upload = self.uploads.get(token)
        if not upload or upload["kind"] != "dataset":
            raise TrainingError("所选数据已失效，请重新上传。")
        return upload["path"]

    @staticmethod
    def _public_log(text, accelerator=False):
        # Public pages show progress, not local directory locations.
        text = re.sub(r"[A-Za-z]:[\\/][^\r\n]*", "[本地文件]", text.rstrip())
        if not accelerator and re.search(
            r"(?i)(?<![a-z])(?:CPU|NPU)(?![a-z])|CPUExecutionProvider", text
        ):
            return None
        if re.search(
            r"DSsample|PWM_S_first|Generated model/deployment files:|^\s+-\s", text
        ):
            return None
        return text

    @staticmethod
    def _trained_model(job):
        for name in (
            "ann_up_down_5in2out_full_static.onnx",
            "ann_up_down_5in1out_full_static.onnx",
        ):
            model = job["folder"] / "output" / name
            if model.is_file():
                return model
        return None

    def _log(self, job, text):
        text = self._public_log(text, accelerator=job.get("target") == "npu")
        if text is None:
            return
        with self.lock:
            job["logs"].append(text)
        with (job["folder"] / "progress.log").open("a", encoding="utf-8") as log:
            log.write(text + "\n")

    def _set(self, job, **values):
        with self.lock:
            job.update(values)

    def npu_source(self):
        previous = self.jobs.get(self.latest_training)
        model = self._trained_model(previous) if previous else None
        if model and model.is_file():
            return model, previous["folder"] / "dataset.csv"
        saved = (
            self.delta_root
            / "OutputData/ann_up_down_5in2out_final_ultralight/ann_up_down_5in2out_full_static.onnx"
        )
        return (saved, self.default_csv) if saved.is_file() else (None, None)

    def start(
        self,
        dataset_token="default",
        compile_token=None,
        compile_only=False,
        target="standard",
    ):
        """校验数据/模型和目标，登记独立任务后启动后台线程；返回初始快照，不阻塞请求。"""
        if target not in ("standard", "npu") or target == "npu" and not compile_only:
            raise TrainingError("请选择有效的编译方式。")
        if not Path(self.python).is_file():
            raise TrainingError("训练环境暂不可用，请检查 Python 配置。", 409)
        dataset = None if compile_only else self.dataset(dataset_token)
        calibration = self.dataset(dataset_token) if target == "npu" else None
        if dataset:
            preview_csv(dataset, validate=True)
        with self.lock:
            if self.active_id:
                raise TrainingError("已有训练或编译任务正在进行，请等待完成。", 409)
            if compile_only and compile_token:
                upload = self.uploads.get(compile_token)
                if not upload or upload["kind"] != "model":
                    raise TrainingError("编译模型已失效，请重新上传 ONNX 文件。")
                model = upload["path"]
            elif compile_only:
                if target == "npu":
                    model, associated = self.npu_source()
                    if (
                        dataset_token == "default"
                        and associated
                        and associated.is_file()
                    ):
                        calibration = associated
                else:
                    previous = self.jobs.get(self.latest_training)
                    model = self._trained_model(previous) if previous else None
                if not model or not model.is_file():
                    raise TrainingError("请先完成训练，或上传完整的 ONNX 模型。", 409)
            else:
                model = None
            if calibration:
                preview_csv(calibration, validate=True)
            job_id = uuid.uuid4().hex
            folder = self.runtime / job_id
            folder.mkdir(parents=True)
            job = {
                "id": job_id,
                "state": "queued",
                "message": "正在准备任务…",
                "logs": [],
                "folder": folder,
                "started_at": time.time(),
                "finished_at": None,
                "training_completed": False,
                "compilation_completed": False,
                "process": None,
                "metrics": None,
                "kind": "compile" if compile_only else "training",
                "target": target,
            }
            self.jobs[job_id] = job
            self.active_id = self.latest_id = job_id
            if compile_only:
                self.latest_compilation = job_id
        threading.Thread(
            target=self._run,
            args=(job, dataset, model, calibration),
            daemon=True,
            name="portal-ann",
        ).start()
        return self.snapshot(job_id)

    def _process(self, job, command, env):
        options = (
            {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        )
        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            text=True,
            bufsize=1,
            env=env,
            cwd=str(job["folder"]),
            **options,
        ) as process:
            self._set(job, process=process)
            for line in process.stdout:
                self._log(job, line)
            code = process.wait()
            self._set(job, process=None)
            if code:
                raise RuntimeError(f"程序退出码：{code}，请查看上方日志。")

    def _run(self, job, dataset, compile_model, calibration=None):
        """执行输入快照、训练或目标编译；仅在产物通过核验后标记成功，失败保留日志和已有文件。"""
        output = job["folder"] / "output"
        output.mkdir()
        env = os.environ.copy()
        env.update(
            PYTHONIOENCODING="utf-8",
            PYTHONUNBUFFERED="1",
            PYTHONDONTWRITEBYTECODE="1",
            MPLBACKEND="Agg",
            OMP_NUM_THREADS="1",
            MKL_NUM_THREADS="1",
        )
        try:
            if dataset:
                # Snapshot the input so replacing an uploaded/default CSV cannot change an active run.
                import shutil

                snapshot = job["folder"] / "dataset.csv"
                shutil.copy2(dataset, snapshot)
                env.update(
                    ANN_CSV_PATH=str(snapshot),
                    ANN_OUTPUT_DIR=str(output),
                    ANN_STREAM_LOG="1",
                )
                self._set(job, state="training", message="正在训练 ANN 模型…")
                self._log(
                    job, "开始训练：原有三随机种子、验证集选模与 Early Stopping。"
                )
                self._process(job, [self.python, "-u", str(self.script)], env)
                metrics = json.loads(
                    (output / "metrics_summary.json").read_text(encoding="utf-8")
                )
                if "DTgiven" not in metrics["test_metrics"]:
                    raise RuntimeError("训练结果缺少瞬态占空比指标。")
                self._set(job, training_completed=True, metrics=metrics)
                with self.lock:
                    self.latest_training = job["id"]
                self._set(
                    job, state="completed", message="模型训练完成，下面请进行编译。"
                )
                self._log(job, "模型训练完成，下面请进行编译。")
                return
            self._log(job, "开始编译所选模型。")
            self._set(job, state="compiling", message="正在校验 ONNX 并编译模型…")
            # Uploaded models are parsed as data; no pickle, Python or shell input is accepted.
            self._process(
                job,
                [
                    self.python,
                    "-u",
                    str(Path(__file__).with_name("validate_ann_onnx.py")),
                    str(compile_model),
                ],
                env,
            )
            import shutil

            compile_input = job["folder"] / "compile_input"
            compile_input.mkdir()
            shutil.copy2(compile_model, compile_input / "model.onnx")
            for name in ("ann_test_vectors.h", "deployment_manifest.json"):
                if (compile_model.parent / name).is_file():
                    shutil.copy2(compile_model.parent / name, compile_input / name)
            if job.get("target") == "npu":
                snapshot = job["folder"] / "calibration.csv"
                shutil.copy2(calibration, snapshot)
                self._set(job, message="正在进行 TI 量化与 NPU 加速编译…")
                self._process(
                    job,
                    [
                        self.python,
                        "-u",
                        str(self.npu_compiler),
                        "--model",
                        str(compile_input / "model.onnx"),
                        "--csv",
                        str(snapshot),
                        "--output",
                        str(compile_input / "dsp_npu"),
                        "--cgt",
                        os.environ.get("TI_CGT_ROOT", ""),
                    ],
                    env,
                )
                result = compile_input / "dsp_npu"
                if not (result / "artifacts/mod.a").is_file():
                    raise RuntimeError("未生成 NPU 编译模型库。")
                report = json.loads(
                    (result / "npu_report.json").read_text(encoding="utf-8")
                )
                if not report.get("hardware_verified"):
                    raise RuntimeError("NPU 硬件映射未通过核验。")
                self._set(
                    job,
                    state="completed",
                    message="NPU 编译完成，待上板验证。",
                    compilation_completed=True,
                    npu_report=report,
                )
                self._log(job, "NPU 编译完成；实际推理耗时与控制中断影响需要上板验证。")
                return
            import tempfile

            env.update(
                ANN_MODEL_DIR=str(compile_input),
                ANN_MODEL_FILE="model.onnx",
                ANN_BUILD_ROOT=str(Path(tempfile.gettempdir()) / "ann_portal_builds"),
            )
            self._process(job, [self.python, "-u", str(self.compiler)], env)
            if not list(compile_input.glob("dsp_cpu_*/artifacts_cpu/mod.a")):
                raise RuntimeError("未找到有效的编译产物。")
            self._set(
                job, state="completed", message="编译完成。", compilation_completed=True
            )
            self._log(job, "编译完成。")
        except Exception as error:
            self._log(job, str(error))
            self._set(
                job,
                state=(
                    "compile_failed"
                    if job["training_completed"] or not dataset
                    else "training_failed"
                ),
                message=(
                    "编译未完成，训练结果已保留，可重试编译。"
                    if job["training_completed"]
                    else "任务未完成，请查看运行日志。"
                ),
            )
        finally:
            self._set(job, finished_at=time.time(), process=None)
            try:
                record = {
                    key: value
                    for key, value in job.items()
                    if key not in {"folder", "process", "logs"}
                }
                (job["folder"] / "record.json").write_text(
                    json.dumps(record, ensure_ascii=False), encoding="utf-8"
                )
            finally:
                with self.lock:
                    if self.active_id == job["id"]:
                        self.active_id = None

    def snapshot(self, job_id=None, after=0):
        """分页返回公开日志和任务状态，不将内部进程对象或本机路径泄露到网页。"""
        with self.lock:
            job_id = job_id or self.latest_id
            if not job_id:
                return None
            if job_id not in self.jobs:
                raise TrainingError("任务不存在。", 404)
            job = self.jobs[job_id]
            after = max(0, min(after, len(job["logs"])))
            lines = job["logs"][after : after + 500]

            def json_safe(value):
                if isinstance(value, float) and not math.isfinite(value):
                    return None
                if isinstance(value, dict):
                    return {
                        key: json_safe(item)
                        for key, item in value.items()
                        if not re.search(r"(?i)(?<![a-z])(?:cpu|npu)(?![a-z])", key)
                    }
                if isinstance(value, list):
                    return [json_safe(item) for item in value]
                return value

            return {
                key: json_safe(job[key])
                for key in (
                    "id",
                    "state",
                    "message",
                    "kind",
                    "training_completed",
                    "compilation_completed",
                    "metrics",
                )
            } | {
                "target": job.get("target", "standard"),
                "npu_report": job.get("npu_report"),
                "logs": lines,
                "next_cursor": after + len(lines),
                "has_more": after + len(lines) < len(job["logs"]),
                "has_training_output": self.latest_training is not None,
                "elapsed_seconds": round(
                    (job["finished_at"] or time.time()) - job["started_at"], 1
                ),
            }

    def artifact(self, job_id, name):
        self.snapshot(job_id)
        if name not in {
            "loss_curve.png",
            "test_prediction_comparison.png",
            "test_true_vs_predicted.png",
            "metrics_summary.json",
        }:
            raise TrainingError("文件不存在。", 404)
        path = self.jobs[job_id]["folder"] / "output" / name
        if not self.jobs[job_id]["training_completed"] or not path.is_file():
            raise TrainingError("训练结果尚未生成。", 404)
        return path

    def archive(self, job_id, kind=None):
        """只打包所属任务的模型或编译产物；任务未完成时拒绝下载未核验结果。"""
        self.snapshot(job_id)
        job = self.jobs[job_id]
        if job["state"] in ACTIVE:
            raise TrainingError("任务完成后才能下载。", 409)
        kind = kind or ("model" if job["kind"] == "training" else "compile")
        if kind not in {"model", "compile"}:
            raise TrainingError("文件不存在。", 404)
        if not job[
            "training_completed" if kind == "model" else "compilation_completed"
        ]:
            raise TrainingError("尚无可下载的模型结果。", 409)
        path = job["folder"] / (
            "ann_model.zip" if kind == "model" else "ann_compile.zip"
        )
        if not path.is_file():
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                for subdir in ("output",) if kind == "model" else ("compile_input",):
                    for file in (job["folder"] / subdir).rglob("*"):
                        if file.is_file() and (
                            kind == "model"
                            or file.relative_to(job["folder"] / subdir)
                            .parts[0]
                            .startswith(("dsp_cpu_", "dsp_npu"))
                        ):
                            archive.write(file, file.relative_to(job["folder"]))
        return path
