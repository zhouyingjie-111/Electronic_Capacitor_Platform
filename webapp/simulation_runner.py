"""Run allowlisted local Simulink models without blocking Flask requests."""

# 仿真边界：只运行允许的模型；参数由请求校验后传给 MATLAB 桥接层。网页查询进度与结果，不在 HTTP 请求中等待仿真。

import importlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import uuid


ACTIVE_STATES = {
    "starting",
    "loading",
    "initializing",
    "running",
    "paused",
    "stopping",
    "exporting",
    "building",
}
TERMINAL_STATES = {"completed", "stopped", "failed", "opened", "built"}
MATLAB_HELPERS = Path(__file__).resolve().parent / "simulink_bridge"
LOG = logging.getLogger(__name__)
DEFAULT_PARAMETERS = {
    "P": 22.0,
    "TP": 50e-6,
    "P_V": 0.15,
    "I_V": 0.5,
    "P_I": 0.2,
    "I_I": 2.0,
}
CAPACITANCE_PARAMETERS = {"P": 22.0, "TP": 50e-6}
SCOPE_NAMES = {
    "voltage": "电压",
    "current": "电流",
    "switch": "开关",
    "model_input": "模型输入",
}


class SimulationRequestError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def import_engine():
    """Use the installed engine, or the engine already built by this MATLAB installation."""
    try:
        return importlib.import_module("matlab.engine")
    except ImportError:
        pass
    executable = shutil.which("matlab")
    roots = []
    if os.environ.get("MATLAB_ROOT"):
        roots.append(Path(os.environ["MATLAB_ROOT"]))
    if executable:
        roots.append(Path(executable).resolve().parents[1])
    for root in roots:
        candidate = root / "extern/engines/python/build/lib"
        if (candidate / "matlab/engine/_arch.txt").is_file():
            sys.path.insert(0, str(candidate))
            return importlib.import_module("matlab.engine")
    raise RuntimeError("MATLAB Engine for Python is unavailable")


class SimulationRunner:
    def __init__(self, models, runtime_dir, engine_factory=None):
        self.models = models
        self.runtime_dir = Path(runtime_dir)
        self.engine_factory = engine_factory or self._connect
        self.engine = None
        self.lock = threading.RLock()
        self.jobs = {}
        self.active_id = None
        self.latest_id = None

    @staticmethod
    def _connect():
        engine = import_engine()
        shared = "PowerElectronicsPortal"
        if shared in engine.find_matlab():
            return engine.connect_matlab(shared)
        pending = engine.start_matlab("-desktop", background=True)
        connection = pending.result(timeout=180)
        connection.eval(
            "matlab.engine.shareEngine('PowerElectronicsPortal');", nargout=0
        )
        return connection

    def ann_function(self):
        from simulation_model import ann_function

        try:
            return ann_function(self.models["ann"]["path"])
        except Exception as error:
            raise SimulationRequestError("ANN 函数暂时无法读取。", 409) from error

    def build_model(self, plan):
        from ai_model_jobs import start_build

        return start_build(self, plan)

    def open_model(self, control):
        """Open an allowlisted model copy. Never enter the simulation pipeline."""
        if control not in self.models and control not in ("blank", "capacitance"):
            raise SimulationRequestError("请选择可用的仿真模型。")
        source = (
            Path(self.models[control]["path"])
            if control in self.models and control != "blank"
            else None
        )
        if source is not None and not source.is_file():
            raise SimulationRequestError("对应模型暂不可用。", 409)
        # Model load/open callbacks could run arbitrary user code. Refuse such
        # models for this viewing operation rather than executing those callbacks.
        from zipfile import ZipFile
        import xml.etree.ElementTree as ET

        if source is not None:
            with ZipFile(source) as archive:
                for name in archive.namelist():
                    if name.endswith(".xml"):
                        for item in ET.fromstring(archive.read(name)).iter("P"):
                            if (
                                item.get("Name")
                                in (
                                    "PreLoadFcn",
                                    "PostLoadFcn",
                                    "LoadFcn",
                                    "OpenFcn",
                                    "MaskInitialization",
                                )
                                and (item.text or "").strip()
                            ):
                                raise SimulationRequestError(
                                    "模型包含加载或打开回调，暂不支持仅打开预览。", 409
                                )
        with self.lock:
            if self.active_id:
                raise SimulationRequestError("已有软件任务正在进行，请等待完成。", 409)
            job_id = uuid.uuid4().hex
            folder = self.runtime_dir / job_id
            folder.mkdir(parents=True)
            self.jobs[job_id] = {
                "id": job_id,
                "control": control,
                "label": (
                    self.models[control]["label"]
                    if source
                    else ("电容辨识工作区" if control == "capacitance" else "Simulink")
                ),
                "blank_workspace": source is None,
                "operation": "open_model",
                "state": "starting",
                "message": "正在连接 MATLAB/Simulink…",
                "progress": None,
                "started_at": time.time(),
                "finished_at": None,
                "simulation_started": False,
                "simulation_status": None,
                "simulation_time": None,
                "diagram_ready": False,
                "diagram_version": None,
                "_folder": folder,
                "_stop": threading.Event(),
                "_source": source,
            }
            self.active_id = job_id
            threading.Thread(
                target=self._open_model,
                args=(job_id,),
                daemon=True,
                name="portal-open-model",
            ).start()
            return self.snapshot(job_id)

    def _open_model(self, job_id):
        job = self.jobs[job_id]
        folder = job["_folder"]
        try:
            if self.engine is not None:
                try:
                    self.engine.eval("1;", nargout=0)
                except Exception:
                    self.engine = None
            if self.engine is None:
                self.engine = self.engine_factory()
            if job["_stop"].is_set():
                self._update(job_id, state="stopped", message="已取消打开模型。")
                return
            self._update(job_id, state="loading", message="正在打开模型，未启动仿真…")
            name = "portal_" + job["control"] + "_" + job_id[:12]
            model_copy = folder / (name + ".slx")
            if job["_source"] is not None:
                shutil.copy2(job["_source"], model_copy)
            self.engine.addpath(str(MATLAB_HELPERS), nargout=0)
            result = json.loads(
                self.engine.portal_open_model(
                    json.dumps(
                        {
                            "model_path": (
                                str(model_copy.resolve()) if job["_source"] else ""
                            ),
                            "model_name": name,
                        },
                        ensure_ascii=False,
                    ),
                    nargout=1,
                )
            )
            if (
                not result.get("opened")
                or result.get("simulation_status") != "stopped"
                or result.get("simulation_started") is not False
            ):
                raise RuntimeError(
                    "Model viewing did not confirm an idle, visible model"
                )
            self._update(
                job_id,
                state="opened",
                message="",
                simulation_status=result["simulation_status"],
                simulation_time=result.get("simulation_time"),
                _model_name=result["model"],
            )
        except Exception:
            LOG.exception("Could not open model %s without simulation", job_id)
            self._update(
                job_id,
                state="failed",
                message="模型打开失败，未提交仿真任务；请检查 MATLAB 连接及模型依赖。",
            )
        finally:
            self._update(job_id, finished_at=time.time())
            with self.lock:
                if self.active_id == job_id:
                    self.active_id = None

    def start(self, control, duration=None, parameters=None, ann_code=None):
        """校验控制方式、时长、参数和代码接口，再启动后台 MATLAB 任务；不修改磁盘上的原模型。"""
        if (
            control not in ("pi", "ann", "custom", "capacitance")
            or control not in self.models
        ):
            raise SimulationRequestError("请选择可用的仿真模型。")
        if duration is not None:
            if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                raise SimulationRequestError(
                    "仿真时长必须是大于 0 的有限数值，单位为秒。"
                )
            try:
                duration = float(duration)
            except OverflowError:
                raise SimulationRequestError(
                    "仿真时长必须是大于 0 的有限数值，单位为秒。"
                ) from None
            if not math.isfinite(duration) or duration <= 0:
                raise SimulationRequestError(
                    "仿真时长必须是大于 0 的有限数值，单位为秒。"
                )
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            raise SimulationRequestError("仿真参数格式不正确。")
        defaults = (
            CAPACITANCE_PARAMETERS if control == "capacitance" else DEFAULT_PARAMETERS
        )
        allowed = set(defaults) | (
            {"D_rise", "D_fall"} if control == "custom" else set()
        )
        if set(parameters) - allowed:
            raise SimulationRequestError("包含不支持的仿真参数。")
        values = {**defaults, **parameters}
        if control == "custom" and not {"D_rise", "D_fall"} <= set(parameters):
            raise SimulationRequestError("请输入 D_rise 和 D_fall。")
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SimulationRequestError(f"{name} 必须为有限数值。")
            try:
                valid = math.isfinite(value)
            except OverflowError:
                valid = False
            if not valid or value < 0 or (name in {"P", "TP"} and value == 0):
                raise SimulationRequestError(
                    f"{name} 数值不合法；P、TP 须大于 0，其余参数须非负。"
                )
        if ann_code is not None:
            if (
                control != "ann"
                or not isinstance(ann_code, str)
                or not ann_code.strip()
                or len(ann_code.encode("utf-8")) > 100 * 1024
            ):
                raise SimulationRequestError(
                    "仅 ANN 控制可替换函数，代码不能为空且不能超过 100 KB。"
                )
            signature = re.search(
                r"^\s*function\s+(?:\[\s*(\w+)\s*\]|(\w+))\s*=\s*\w+\s*\(([^)]*)\)",
                ann_code,
            )
            if not signature or len(signature.group(3).split(",")) != 5:
                raise SimulationRequestError(
                    "ANN 函数须保持 5 个输入、1 个输出的接口。"
                )
        model = self.models[control]
        if not Path(model["path"]).is_file():
            raise SimulationRequestError("对应仿真模型暂不可用。", 409)
        with self.lock:
            if self.active_id:
                raise SimulationRequestError(
                    "已有仿真任务正在进行，请等待完成或先停止。", 409
                )
            job_id = uuid.uuid4().hex
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            folder = self.runtime_dir / job_id
            folder.mkdir()
            self.jobs[job_id] = {
                "id": job_id,
                "control": control,
                "label": model["label"],
                "state": "starting",
                "message": "正在连接 MATLAB…",
                "progress": None,
                "simulation_time": None,
                "start_time": None,
                "stop_time": None,
                "requested_duration": duration,
                "diagram_version": None,
                "parameters": values,
                "scopes": [],
                "scopes_ready": False,
                "waveforms_ready": False,
                "waveforms_message": "",
                "_ann_code": ann_code,
                "diagram_width": None,
                "diagram_height": None,
                "started_at": time.time(),
                "finished_at": None,
                "diagram_ready": False,
                "_folder": folder,
                "_stop": threading.Event(),
            }
            self.active_id = self.latest_id = job_id
            threading.Thread(
                target=self._run, args=(job_id,), daemon=True, name="portal-simulation"
            ).start()
            return self.snapshot(job_id)

    def open_scope(self, job_id, scope_id):
        """只打开本任务已登记的示波器，不接受任意 MATLAB 命令。"""
        with self.lock:
            job = self.snapshot(job_id)
            if scope_id not in SCOPE_NAMES:
                raise SimulationRequestError("示波器不存在。", 404)
            if self.active_id or job["state"] != "completed" or not job["scopes_ready"]:
                raise SimulationRequestError("请等待仿真完成后打开示波器。", 409)
            if not any(scope["id"] == scope_id for scope in job["scopes"]):
                raise SimulationRequestError("本次仿真未找到该示波器。", 404)
            try:
                self.engine.portal_simulation(
                    "open_scope",
                    json.dumps(
                        {
                            "model": self.jobs[job_id]["_model_name"],
                            "name": SCOPE_NAMES[scope_id],
                        },
                        ensure_ascii=False,
                    ),
                    nargout=1,
                )
            except Exception as error:
                LOG.exception("Could not open scope %s", scope_id)
                raise SimulationRequestError(
                    "示波器打开失败，请确认 MATLAB 中本次仿真仍可用。", 409
                ) from error
            return {
                "message": f"已打开{SCOPE_NAMES[scope_id]}示波器，可在示波器窗口使用鼠标缩放、平移查看波形。"
            }

    def waveform(self, job_id, scope_id, start=None, end=None, limit=3000):
        """读取本任务已导出的示波器数据，限制范围和点数；由 scope_waveforms 负责峰值保留。"""
        job = self.snapshot(job_id)
        if scope_id not in SCOPE_NAMES:
            raise SimulationRequestError("示波器不存在。", 404)
        if not job.get("waveforms_ready"):
            raise SimulationRequestError(
                job.get("waveforms_message") or "网页波形尚未就绪，请等待仿真完成。",
                409,
            )
        from scope_waveforms import read_waveform

        try:
            return read_waveform(
                self.jobs[job_id]["_folder"] / "waveforms", scope_id, start, end, limit
            )
        except (ValueError, OverflowError) as error:
            raise SimulationRequestError(str(error)) from error
        except OSError as error:
            raise SimulationRequestError(
                "波形文件无法读取，请重新仿真。", 409
            ) from error

    def _update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)

    def snapshot(self, job_id=None):
        """锁内复制公开状态，用于前端进度轮询；内部路径和引擎对象不进入响应。"""
        with self.lock:
            job_id = job_id or self.latest_id
            if not job_id:
                return None
            if job_id not in self.jobs:
                raise SimulationRequestError("仿真任务不存在。", 404)
            job = self.jobs[job_id]
            result = {
                key: value for key, value in job.items() if not key.startswith("_")
            }
            end = job["finished_at"] or time.time()
            result["elapsed_seconds"] = round(max(0, end - job["started_at"]), 1)
            result["diagram_ready"] = (
                job["diagram_ready"] and (job["_folder"] / "model.png").is_file()
            )
            return result

    def diagram(self, job_id):
        snapshot = self.snapshot(job_id)
        path = self.jobs[job_id]["_folder"] / "model.png"
        if not snapshot["diagram_ready"] or not path.is_file():
            raise SimulationRequestError("正在生成模型画面。", 404)
        return path

    def stop(self, job_id):
        with self.lock:
            self.snapshot(job_id)
            if self.jobs[job_id]["state"] in ACTIVE_STATES:
                self.jobs[job_id]["_stop"].set()
                self._update(job_id, state="stopping", message="正在请求停止仿真…")
            return self.snapshot(job_id)

    @staticmethod
    def _progress(current, start, stop):
        if (
            not all(
                isinstance(x, (int, float)) and math.isfinite(x)
                for x in (current, start, stop)
            )
            or stop <= start
        ):
            return None
        # 100% is reserved for a confirmed successful completion.
        return round(max(0, min(99.9, (current - start) / (stop - start) * 100)), 1)

    def _run(self, job_id):
        job = self.jobs[job_id]
        folder = job["_folder"]
        stage = "starting"
        try:
            if self.engine is None:
                self.engine = self.engine_factory()
            engine = self.engine
            if job["_stop"].is_set():
                self._update(job_id, state="stopped", message="已取消仿真。")
                return
            stage = "loading"
            self._update(
                job_id, state=stage, message="正在打开 Simulink 模型并生成画面…"
            )
            engine.addpath(str(MATLAB_HELPERS), nargout=0)
            model = self.models[job["control"]]
            # Separate model name per job keeps source files and past scope data intact.
            model_name = "portal_" + job["control"] + "_" + job_id[:12]
            model_copy = folder / (model_name + ".slx")
            shutil.copy2(model["path"], model_copy)
            self._update(job_id, _model_name=model_name)
            if job["_ann_code"] is not None:
                (folder / "ann_function.m").write_text(
                    job["_ann_code"], encoding="utf-8"
                )
            configuration = {
                "model_path": str(model_copy.resolve()),
                "source_dir": str(Path(model["path"]).resolve().parent),
                "parameters": job["parameters"],
                "ann_code_path": (
                    str(folder / "ann_function.m")
                    if job["_ann_code"] is not None
                    else ""
                ),
                "waveform_dir": str(folder / "waveforms"),
                "diagram_path": str(folder / "model.png"),
                "cache_dir": str(self.runtime_dir / "_cache" / job["control"]),
                "duration": job["requested_duration"],
                "dependency_dirs": [
                    str(Path(p).resolve())
                    for p in model.get("dependency_dirs", [])
                    if Path(p).is_dir()
                ],
                "scopes": model.get(
                    "scopes",
                    [
                        {"id": key, "name": SCOPE_NAMES[key]}
                        for key in ("voltage", "current", "switch")
                    ],
                ),
                "block_parameters": model.get("block_parameters", []),
            }
            prepared = json.loads(
                engine.portal_simulation(
                    "prepare", json.dumps(configuration, ensure_ascii=False), nargout=1
                )
            )
            scope_list = prepared.get("scopes", [])
            # MATLAB jsonencode emits a single struct as an object, not an array.
            if isinstance(scope_list, dict):
                scope_list = [scope_list]
            self._update(job_id, scopes=scope_list)
            # File existence alone is not readiness: MATLAB may still be writing the PNG.
            # Validate the complete file before publishing any model image endpoint.
            from PIL import Image

            diagram = folder / "model.png"
            with Image.open(diagram) as picture:
                picture.verify()
            with Image.open(diagram) as picture:
                picture.load()
                width, height = picture.size
            stat = diagram.stat()
            self._update(
                job_id,
                diagram_ready=True,
                diagram_width=width,
                diagram_height=height,
                diagram_version=f"{stat.st_mtime_ns}-{stat.st_size}",
                start_time=prepared["start_time"],
                stop_time=prepared["stop_time"],
            )
            if job["_stop"].is_set():
                engine.portal_simulation("stop", "", nargout=0)
                self._update(job_id, state="stopped", message="已取消仿真。")
                return
            stage = "initializing"
            self._update(job_id, state=stage, message="模型已打开，正在编译与初始化…")
            engine.portal_simulation("start", "", nargout=0)
            while True:
                if job["_stop"].is_set():
                    engine.portal_simulation("stop", "", nargout=0)
                    self._update(job_id, state="stopped", message="仿真已停止。")
                    break
                status = json.loads(engine.portal_simulation("status", "", nargout=1))
                current, start, stop = (
                    status.get(k)
                    for k in ("simulation_time", "start_time", "stop_time")
                )
                state = status["state"]
                self._update(
                    job_id,
                    simulation_time=current,
                    start_time=start,
                    stop_time=stop,
                    progress=self._progress(current, start, stop),
                )
                if state == "completed":
                    self._update(
                        job_id,
                        state="exporting",
                        progress=99.9,
                        message="仿真计算已完成，正在整理示波器波形…",
                    )
                    try:
                        exported = json.loads(
                            engine.portal_simulation("export_waveforms", "", nargout=1)
                        )
                        if (
                            not exported.get("ready")
                            or not (folder / "waveforms/index.json").is_file()
                        ):
                            raise RuntimeError(
                                "Scope export did not publish a complete index"
                            )
                        self._update(job_id, waveforms_ready=True)
                    except Exception:
                        LOG.exception("Could not export waveform data for %s", job_id)
                        self._update(
                            job_id,
                            waveforms_message="本次波形记录未完成，可打开本机示波器，或重新仿真后查看网页波形。",
                        )
                    self._update(
                        job_id,
                        state=state,
                        progress=100,
                        message="仿真已完成。",
                        scopes_ready=bool(job["scopes"]),
                    )
                    break
                if state == "stopped":
                    self._update(
                        job_id, state=state, message="仿真在到达结束时间前停止。"
                    )
                    break
                if state == "failed":
                    raise RuntimeError(status.get("diagnostic", "Simulation failed"))
                stage = "running"
                message = "仿真已暂停。" if state == "paused" else "正在仿真…"
                self._update(job_id, state=state, message=message)
                time.sleep(0.6)
        except Exception:
            import traceback

            diagnostic = traceback.format_exc()
            (folder / "diagnostic.log").write_text(diagnostic, encoding="utf-8")
            LOG.error("Simulation job %s failed: %s", job_id, diagnostic)
            message = {
                "starting": "MATLAB 连接失败，请确认本机 MATLAB 与 Simulink 可用后重试。",
                "loading": "模型打开失败，请查看 MATLAB 中的诊断信息。",
                "initializing": "模型编译或初始化失败，请查看 MATLAB 中的诊断信息。",
                "running": "仿真运行失败，请查看 MATLAB 中的诊断信息。",
            }[stage]
            self._update(job_id, state="failed", message=message)
            if self.engine is not None:
                try:
                    self.engine.portal_simulation("stop", "", nargout=0)
                except Exception:
                    self.engine = None
        finally:
            if self.engine is not None:
                try:
                    self.engine.portal_simulation("release", "", nargout=0)
                except Exception:
                    LOG.exception("Could not release simulation %s", job_id)
            self._update(job_id, finished_at=time.time())
            with self.lock:
                if self.active_id == job_id:
                    self.active_id = None
