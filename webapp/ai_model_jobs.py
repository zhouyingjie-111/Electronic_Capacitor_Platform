"""Asynchronous model construction, sharing the existing software-task lock."""

import json
import logging
from pathlib import Path
import threading
import time
import uuid

from ai_model_builder import operations

LOG = logging.getLogger(__name__)


def start_build(runner, plan):
    from simulation_runner import SimulationRequestError

    with runner.lock:
        if runner.active_id:
            raise SimulationRequestError("已有软件任务正在进行，请等待完成。", 409)
        job_id = uuid.uuid4().hex
        folder = runner.runtime_dir / job_id
        folder.mkdir(parents=True)
        runner.jobs[job_id] = {
            "id": job_id,
            "operation": "build_model",
            "label": plan["title"],
            "state": "starting",
            "message": "正在连接建模环境…",
            "progress": 5,
            "started_at": time.time(),
            "finished_at": None,
            "simulation_started": False,
            "diagram_ready": False,
            "block_count": len(plan["blocks"]),
            "connection_count": len(plan["connections"]),
            "_folder": folder,
            "_plan": plan,
            "_stop": threading.Event(),
        }
        runner.active_id = job_id
        threading.Thread(
            target=_build, args=(runner, job_id), daemon=True, name="portal-model-build"
        ).start()
        return runner.snapshot(job_id)


def _build(runner, job_id):
    from simulation_runner import MATLAB_HELPERS

    job = runner.jobs[job_id]
    plan = job["_plan"]
    folder = job["_folder"]
    try:
        if runner.engine is not None:
            try:
                runner.engine.eval("1;", nargout=0)
            except Exception:
                runner.engine = None
        if runner.engine is None:
            runner.engine = runner.engine_factory()
        engine = runner.engine
        if not engine.which("library.settingsLookup", nargout=1):
            toolkit = Path.home() / ".matlab/agentic-toolkits/simulink"
            if not (toolkit / "satk_initialize.p").is_file():
                raise RuntimeError("MathWorks toolkit unavailable")
            engine.addpath(str(toolkit), nargout=0)
            engine.satk_initialize(nargout=0)
        engine.addpath(str(MATLAB_HELPERS), nargout=0)
        if job["_stop"].is_set():
            runner._update(job_id, state="stopped", message="已取消创建。")
            return
        runner._update(
            job_id,
            state="building",
            progress=30,
            message="正在搭建模块、连接信号并检查结构…",
        )
        name = "assistant_model_" + job_id[:12]
        path = folder / (name + ".slx")
        result = json.loads(
            engine.portal_build_model(
                json.dumps(
                    {
                        "model_name": name,
                        "model_path": str(path.resolve()),
                        "variables": plan["variables"],
                        "operations": json.dumps(
                            operations(plan), ensure_ascii=False, separators=(",", ":")
                        ),
                    },
                    ensure_ascii=False,
                ),
                nargout=1,
            )
        )
        (folder / "build-report.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if (
            not result.get("built")
            or result.get("simulation_started") is not False
            or result.get("simulation_status") != "stopped"
            or not path.is_file()
        ):
            raise RuntimeError(
                "Model construction or structural check did not complete"
            )
        runner._update(
            job_id,
            state="built",
            progress=100,
            message="模型已创建，连接检查通过。",
            simulation_status="stopped",
            _model_name=name,
            _model_path=path,
            structure_report=result.get("structure_report", ""),
            check_report=result.get("check_report", ""),
        )
    except Exception:
        LOG.exception("Model build failed: %s", job_id)
        runner._update(
            job_id,
            state="failed",
            message="模型未完成创建，请检查模块参数、MATLAB连接及工具包依赖后重新生成方案。",
        )
    finally:
        runner._update(job_id, finished_at=time.time())
        with runner.lock:
            if runner.active_id == job_id:
                runner.active_id = None
