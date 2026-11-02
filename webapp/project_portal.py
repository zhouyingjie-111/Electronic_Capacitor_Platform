"""台达杯 2.0 网页内容、结果展示与本地项目文件入口。

这里仅组织网页和读取现有结果，不导入训练框架或自动运行训练脚本。
两个历史 Flask 入口共用此模块，避免页面和文件定位再次分叉。
"""

# 维护入口：注册功能路由、资源白名单及共享服务。新增功能应增加独立服务模块，避免在请求线程运行训练或仿真。

import csv
import json
import os
import subprocess
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from simulation_runner import SimulationRunner, SimulationRequestError
from training_runner import TrainingRunner, TrainingError, preview_csv
from serial_service import SerialService, SerialError
from ai_routes import register_ai_routes


WEBAPP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WEBAPP_DIR.parent
DELTA_ROOT = Path(
    os.environ.get("DELTA_PROJECT_ROOT", str(PROJECT_ROOT / "project_data"))
).resolve()
MODEL_VERSIONS = {
    "standard": ("标准网络 · 48 / 48 / 24", "ann_up_down_5in2out_final"),
    "light": ("轻量网络 · 32 / 32 / 16", "ann_up_down_5in2out_final_light"),
    "ultralight": ("双隐藏层网络 · 8 / 4", "ann_up_down_5in2out_final_ultralight"),
    "ultralight_plus": (
        "单隐藏层网络 · 6",
        "ann_up_down_5in2out_final_ultralight_plus",
    ),
}

RESOURCES = {
    "training": ("Code/python/ANN_5IN2OUT_ultralight.py", "快速追踪 ANN 训练代码"),
    "preprocessing": ("Code/python/Data_Process_noL_final.py", "仿真数据预处理代码"),
    "acquisition": ("Code/Matlab/Copy_2_of_BuckData.m", "批量仿真与占空比寻优代码"),
    "summary": ("OutputData/仿真结果汇总.csv", "快速追踪汇总数据集"),
    "raw_sample": ("InputData/仿真结果_0001.csv", "原始仿真样本 0001"),
    "cpu_compile": ("Code/python/compile_ann_cpu_ultralight.py", "模型编译代码"),
    "inference": (
        "OutputData/ann_up_down_5in2out_final_ultralight/ann_predict_matlab.m",
        "MATLAB 前向推理代码",
    ),
    "pi_model": ("Simulink/DualBuckPPS_PI.slx", "PI 控制仿真模型"),
    "ann_model": ("Simulink/DualBuckPPS_ANN.slx", "ANN 控制仿真模型"),
    "custom_model": ("Simulink/DualBuckPPS_define.slx", "自定义控制仿真模型"),
    "capacitance_model": ("Simulink/DualBuckPPS_CNN.slx", "电容参数辨识仿真模型"),
    "reference_model": ("Simulink/DualBuckPPS22016.slx", "基准控制与数据采集模型"),
    "search_model": (
        "Simulink/DualBuckPPS22016_define.slx",
        "瞬态占空比自定义寻优模型",
    ),
}
for version, (label, folder) in MODEL_VERSIONS.items():
    for kind, filename, title in [
        ("metrics", "metrics_summary.json", "测试集指标"),
        ("manifest", "deployment_manifest.json", "模型部署记录"),
        ("prediction", "test_true_vs_predicted.png", "预测值与实际值对比"),
        ("comparison", "test_prediction_comparison.png", "预测误差对比"),
        ("loss", "loss_curve.png", "训练损失曲线"),
    ]:
        RESOURCES[f"{version}_{kind}"] = (
            f"OutputData/{folder}/{filename}",
            f"{label} · {title}",
        )

CARDS = [
    {
        "key": "tracking",
        "icon": "⚡",
        "title": "智能化脉冲负载电流快速追踪",
        "nav_title": "快速跟踪",
        "description": "基于轻量化 ANN 与 PI 协同控制，结合模型训练、仿真验证与实验波形，提高脉冲负载电流跟踪速度并减小瞬态超调。",
        "button": "进入快速追踪",
    },
    {
        "key": "capacitance",
        "icon": "◉",
        "title": "智能化电容参数无侵入识别",
        "nav_title": "无侵入识别",
        "description": "结合 Haar 小波与轻量化 CNN，识别固定位置电容的容值与 ESR，并通过板上输出和实验误差分析判断器件状态。",
        "button": "进入电容参数识别",
    },
    {
        "key": "chat",
        "icon": "💬",
        "title": "智能对话",
        "nav_title": "智能对话",
        "description": "围绕电力电子设计、控制、器件与实验问题展开交流，结合工程计算、文件分析及 Simulink 工具辅助研究。",
        "button": "进入智能对话",
    },
]
PAGE_INFO = {card["key"]: card for card in CARDS}
PAGE_SECTIONS = {
    "tracking": [
        ("background", "背景介绍"),
        ("algorithm", "算法与数据"),
        ("simulation", "仿真验证"),
        ("experiments", "实验结果"),
    ],
    "capacitance": [
        ("background", "背景介绍"),
        ("algorithm", "算法与数据"),
        ("simulation", "仿真验证"),
        ("experiments", "实验结果"),
    ],
}


def resource_path(resource_id):
    if resource_id not in RESOURCES:
        abort(404)
    path = (DELTA_ROOT / RESOURCES[resource_id][0]).resolve()
    if not path.is_relative_to(DELTA_ROOT) or not path.is_file():
        abort(404, description="对应项目文件暂不可用，请检查文件是否存在。")
    return path


def read_text(path):
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def model_metrics():
    versions = []
    for key, (label, folder) in MODEL_VERSIONS.items():
        path = DELTA_ROOT / "OutputData" / folder / "metrics_summary.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(read_text(path))
            versions.append(
                {
                    "key": key,
                    "label": label,
                    **data,
                    "test_metrics": {"DTgiven": data["test_metrics"]["DTgiven"]},
                }
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return versions


def csv_preview(path, limit=50):
    rows = list(csv.reader(read_text(path).splitlines()))
    if not rows:
        return {"columns": [], "rows": [], "total": 0}
    columns = [(i, name) for i, name in enumerate(rows[0]) if name.strip()]
    return {
        "columns": [name for _, name in columns],
        "rows": [
            [row[i] if i < len(row) else "" for i, _ in columns]
            for row in rows[1 : limit + 1]
        ],
        "total": len(rows) - 1,
    }


def figure_sizes():
    """预留图片显示空间，避免延迟加载时页面上下跳动。"""
    try:
        metadata = json.loads(
            (WEBAPP_DIR / "asset_sources.json").read_text(encoding="utf-8")
        )
        sizes = {
            item["file"]: item["size"] for item in metadata["figures"] if "size" in item
        }
    except (OSError, ValueError, KeyError, TypeError):
        sizes = {}
    sizes["tracking_ann_mapping.svg"] = [1120, 410]
    return sizes


def register_project_portal(app):
    portal = Blueprint(
        "project",
        __name__,
        template_folder="templates",
        static_folder="static/project",
        static_url_path="/project-static",
    )
    simulations = SimulationRunner(
        {
            control: {
                "path": DELTA_ROOT / RESOURCES[resource][0],
                "label": label,
                "dependency_dirs": [
                    DELTA_ROOT / "Code/Matlab",
                    DELTA_ROOT / "OutputData/ann_up_down_5in2out_final_ultralight",
                ],
            }
            for control, resource, label in [
                ("pi", "pi_model", "PI 控制"),
                ("ann", "ann_model", "ANN 控制"),
                ("custom", "custom_model", "自定义控制"),
            ]
        },
        WEBAPP_DIR / "runtime/simulations",
    )
    cap_model = app.config.get(
        "CAPACITANCE_SIMULINK_MODEL", DELTA_ROOT / RESOURCES["capacitance_model"][0]
    )
    if cap_model:
        cap_path = Path(cap_model).resolve()
        if (
            cap_path.is_relative_to(DELTA_ROOT)
            and cap_path.is_file()
            and cap_path.suffix.lower() == ".slx"
        ):
            simulations.models["capacitance"] = {
                "path": cap_path,
                "label": "电容参数辨识",
                "scopes": [{"id": "model_input", "name": "模型输入"}],
            }
    app.extensions["project_simulations"] = simulations
    training = TrainingRunner(DELTA_ROOT, WEBAPP_DIR / "runtime/training")
    app.extensions["project_training"] = training
    from capacitance_training import CapTrainingRunner

    cap_training = CapTrainingRunner(DELTA_ROOT, WEBAPP_DIR / "runtime/cap_training")
    app.extensions["project_cap_training"] = cap_training
    serial_link = SerialService()
    app.extensions["project_serial"] = serial_link
    register_ai_routes(app, portal, simulations, serial_link)

    @portal.context_processor
    def serial_ui_context():
        state = serial_link.snapshot(after=0xFFFFFFFFFFFFFFFF)
        return {
            "serial_initial": {
                key: state[key]
                for key in ("connected", "port", "baud", "feedback", "upload_rate")
            }
        }

    @portal.errorhandler(SerialError)
    def serial_error(error):
        if error.status not in (403, 415) and request.path.startswith(
            "/api/project/serial/"
        ):
            serial_link.set_feedback(str(error), True)
        return jsonify({"error": str(error)}), error.status

    @portal.before_request
    def local_serial_requests():
        if request.path.startswith("/api/project/serial/"):
            from urllib.parse import urlsplit

            if request.remote_addr not in ("127.0.0.1", "::1"):
                raise SerialError("串口通信仅支持本机访问。", 403)
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                raise SerialError("不允许跨站访问串口。", 403)
            if request.method == "POST" and not request.is_json:
                raise SerialError("请使用有效的通信请求。", 415)

    @portal.get("/api/project/serial/ports")
    def serial_ports():
        return jsonify({"ports": serial_link.ports()})

    @portal.get("/api/project/serial/status")
    def serial_status():
        response = jsonify(
            serial_link.snapshot(max(0, request.args.get("after", 0, type=int)))
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @portal.post("/api/project/serial/connect")
    def serial_connect():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise SerialError("请选择串口和波特率。")
        return jsonify(serial_link.connect(data.get("port"), data.get("baud", 115200)))

    @portal.post("/api/project/serial/disconnect")
    def serial_disconnect():
        return jsonify(serial_link.disconnect())

    @portal.post("/api/project/serial/send")
    def serial_send():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise SerialError("请输入通信指令。")
        return jsonify(serial_link.send(data.get("command")))

    @portal.errorhandler(TrainingError)
    def training_error(error):
        return jsonify({"error": str(error)}), error.status

    @portal.before_request
    def local_cap_requests():
        if request.path.startswith(
            ("/api/project/cap-training/", "/api/project/training/")
        ):
            from urllib.parse import urlsplit

            if request.remote_addr not in ("127.0.0.1", "::1"):
                raise TrainingError("辨识任务仅支持本机访问。", 403)
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                raise TrainingError("不允许跨站请求。", 403)

    @portal.get("/api/project/cap-training/preview")
    def cap_preview():
        return jsonify(
            cap_training.preview(
                request.args.get("dataset", "default"), request.args.get("file")
            )
        )

    @portal.post("/api/project/cap-training/upload/<kind>")
    def cap_upload(kind):
        return jsonify(cap_training.upload(request.files.get("file"), kind))

    @portal.post("/api/project/cap-training/start")
    def cap_start():
        data = request.get_json(silent=True)
        if (
            not isinstance(data, dict)
            or set(data) - {"kind", "dataset", "model"}
            or any(not isinstance(v, str) for v in data.values())
        ):
            raise TrainingError("任务参数不正确。")
        return (
            jsonify(
                cap_training.start(
                    data.get("kind", "training"),
                    data.get("dataset", "default"),
                    data.get("model", "latest"),
                )
            ),
            202,
        )

    @portal.get("/api/project/cap-training/current")
    def cap_current():
        return jsonify(cap_training.current())

    @portal.get("/api/project/cap-training/<jid>")
    def cap_status(jid):
        return jsonify(
            cap_training.snapshot(jid, request.args.get("after", 0, type=int))
        )

    @portal.post("/api/project/cap-training/<jid>/stop")
    def cap_stop(jid):
        return jsonify(cap_training.stop(jid))

    @portal.get("/api/project/cap-training/<jid>/artifact/<name>")
    def cap_artifact(jid, name):
        return send_file(cap_training.artifact(jid, name))

    @portal.get("/api/project/cap-training/<jid>/chart/<kind>.svg")
    def cap_chart(jid, kind):
        from capacitance_charts import chart

        cap_training.snapshot(jid)
        return Response(
            chart(
                cap_training.jobs[jid]["output"],
                kind,
                request.args.get("target", "C2_uF"),
            ),
            mimetype="image/svg+xml",
        )

    @portal.get("/api/project/cap-training/<jid>/download")
    def cap_download(jid):
        return send_file(
            cap_training.archive(jid),
            as_attachment=True,
            download_name="capacitance_results.zip",
        )

    @portal.post("/api/project/training/upload/<kind>")
    def training_upload(kind):
        if kind not in {"dataset", "model"}:
            abort(404)
        if request.content_length and request.content_length > 101 * 1024**2:
            raise TrainingError("上传文件过大。", 413)
        return jsonify(training.upload(request.files.get("file"), kind))

    @portal.get("/api/project/training/preview")
    def training_preview():
        return jsonify(preview_csv(training.dataset(request.args.get("dataset"))))

    @portal.post("/api/project/training/start")
    def training_start():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or any(
            not isinstance(value, str) for value in data.values()
        ):
            raise TrainingError("请选择有效的数据和模型。")
        return jsonify(training.start(data.get("dataset", "default"))), 202

    @portal.post("/api/project/training/compile")
    def training_compile():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or any(
            not isinstance(value, str) for value in data.values()
        ):
            raise TrainingError("请选择有效的编译模型。")
        return (
            jsonify(
                training.start(
                    dataset_token=data.get("dataset", "default"),
                    compile_token=data.get("model"),
                    compile_only=True,
                    target=data.get("target", "standard"),
                )
            ),
            202,
        )

    @portal.get("/api/project/training/current")
    def training_current():
        current = training.snapshot()
        compilation = (
            training.snapshot(training.latest_compilation)
            if training.latest_compilation
            else None
        )
        if (
            compilation
            and training.latest_training
            and training.jobs[compilation["id"]]["started_at"]
            < training.jobs[training.latest_training]["started_at"]
        ):
            compilation = None
        if (
            current
            and current["kind"] == "training"
            and current["state"] in {"queued", "training"}
        ):
            compilation = None
        response = jsonify(
            {
                "job": current,
                "training_job": (
                    training.snapshot(training.latest_training)
                    if training.latest_training
                    else None
                ),
                "compile_job": compilation,
                "has_npu_source": training.npu_source()[0] is not None,
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @portal.get("/api/project/training/<job_id>")
    def training_status(job_id):
        response = jsonify(
            training.snapshot(job_id, request.args.get("after", 0, type=int))
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @portal.get("/api/project/training/<job_id>/artifact/<name>")
    def training_artifact(job_id, name):
        return send_file(training.artifact(job_id, name))

    @portal.get("/api/project/training/<job_id>/download")
    def training_download(job_id):
        kind = request.args.get("kind")
        path = training.archive(job_id, kind)
        label = (
            "ANN_NPU_results.zip"
            if training.jobs[job_id].get("target") == "npu"
            else "ANN_compile_results.zip"
        )
        return send_file(
            path,
            as_attachment=True,
            download_name="ANN_model.zip" if path.name == "ann_model.zip" else label,
        )

    @portal.errorhandler(SimulationRequestError)
    def simulation_error(error):
        return jsonify({"error": str(error)}), error.status

    @portal.post("/api/project/simulation/start")
    def start_simulation():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or not isinstance(data.get("control"), str):
            raise SimulationRequestError("请选择仿真控制方式。")
        return (
            jsonify(
                simulations.start(
                    data["control"],
                    data.get("duration"),
                    data.get("parameters"),
                    data.get("ann_code"),
                )
            ),
            202,
        )

    @portal.get("/api/project/simulation/ann-function")
    def simulation_ann_function():
        response = jsonify(simulations.ann_function())
        response.headers["Cache-Control"] = "no-store"
        return response

    @portal.post("/api/project/simulation/<job_id>/scope/<scope_id>/open")
    def simulation_open_scope(job_id, scope_id):
        return jsonify(simulations.open_scope(job_id, scope_id))

    @portal.get("/api/project/simulation/<job_id>/scope/<scope_id>/waveform")
    def simulation_waveform(job_id, scope_id):
        try:
            start = float(request.args["start"]) if "start" in request.args else None
            end = float(request.args["end"]) if "end" in request.args else None
            points = int(request.args.get("points", "3000"))
        except (ValueError, OverflowError):
            raise SimulationRequestError("波形时间范围或显示点数不合法。") from None
        response = jsonify(simulations.waveform(job_id, scope_id, start, end, points))
        response.headers["Cache-Control"] = "no-store"
        return response

    @portal.get("/api/project/simulation/current")
    def current_simulation():
        module = request.args.get("module")
        if module in ("tracking", "capacitance"):
            eligible = [
                j
                for j in simulations.jobs.values()
                if not j.get("operation")
                and (j.get("control") == "capacitance") == (module == "capacitance")
            ]
            latest = (
                max(eligible, key=lambda j: j["started_at"])["id"] if eligible else None
            )
            job = simulations.snapshot(latest) if latest else None
        else:
            job = simulations.snapshot()
        response = jsonify({"job": job})
        response.headers["Cache-Control"] = "no-store"
        return response

    @portal.get("/api/project/simulation/<job_id>")
    def simulation_status(job_id):
        response = jsonify(simulations.snapshot(job_id))
        response.headers["Cache-Control"] = "no-store"
        return response

    @portal.get("/api/project/simulation/<job_id>/diagram")
    def simulation_diagram(job_id):
        response = send_file(
            simulations.diagram(job_id),
            mimetype="image/png",
            conditional=False,
            etag=False,
        )
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @portal.post("/api/project/simulation/<job_id>/stop")
    def stop_simulation(job_id):
        return jsonify(simulations.stop(job_id))

    @app.context_processor
    def project_context():
        return {
            "project_cards": CARDS,
            "project_name": "双通道互济型电子电容",
            "figure_sizes": figure_sizes(),
            "chat_url": url_for("project.ai_page"),
        }

    @portal.get("/project/<page>")
    def page(page):
        # 保留旧地址的访问能力，将内容定位到所属模块。
        if page == "simulation":
            return redirect(
                url_for(
                    "project.page",
                    page="tracking",
                    section="simulation",
                    _anchor="simulation-validation",
                )
            )
        if page == "experiments":
            target = (
                "capacitance"
                if request.args.get("module") == "capacitance"
                else "tracking"
            )
            anchor = (
                "capacitance-results" if target == "capacitance" else "tracking-results"
            )
            return redirect(
                url_for(
                    "project.page", page=target, section="experiments", _anchor=anchor
                )
            )
        if page not in PAGE_INFO or page == "chat":
            abort(404)
        section = request.args.get("section", "background")
        if section not in dict(PAGE_SECTIONS[page]):
            abort(404)
        versions = (
            model_metrics() if page == "tracking" and section == "algorithm" else []
        )
        summary = (
            preview_csv(resource_path("summary"), 8)
            if page == "tracking" and section == "algorithm"
            else None
        )
        return render_template(
            "project/detail.html",
            page=page,
            info=PAGE_INFO[page],
            section=section,
            sections=PAGE_SECTIONS[page],
            versions=versions,
            summary=summary,
            cap_summary=(
                cap_training.preview()
                if page == "capacitance" and section == "algorithm"
                else None
            ),
        )

    @portal.get("/project/resource/<resource_id>")
    def resource(resource_id):
        if resource_id in {"pi_model", "ann_model"}:
            return redirect(
                url_for("project.page", page="tracking", section="simulation")
            )
        path = resource_path(resource_id)
        if request.args.get("download") == "1":
            return send_file(path, as_attachment=True, download_name=path.name)
        if path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}:
            return send_file(path)
        table = csv_preview(path) if path.suffix.lower() == ".csv" else None
        content = (
            read_text(path)
            if path.suffix.lower() in {".py", ".m", ".json", ".txt"}
            else None
        )
        return render_template(
            "project/resource.html",
            title=RESOURCES[resource_id][1],
            resource_id=resource_id,
            path=str(path),
            filename=path.name,
            content=content,
            table=table,
            size=path.stat().st_size,
        )

    @portal.post("/api/project/open/<resource_id>")
    def open_resource(resource_id):
        if resource_id in {"pi_model", "ann_model"}:
            return (
                jsonify(
                    simulations.start("pi" if resource_id == "pi_model" else "ann")
                ),
                202,
            )
        path = resource_path(resource_id)
        if os.name != "nt":
            return (
                jsonify({"error": "本机打开功能需要 Windows 环境，请下载文件后打开。"}),
                400,
            )
        try:
            if path.suffix.lower() in {".py", ".m", ".json", ".txt"}:
                # Windows 的 .py 默认关联可能执行脚本，代码入口只在编辑器中查看。
                subprocess.Popen(["notepad.exe", str(path)])
            else:
                os.startfile(str(path))
            return jsonify(
                {"ok": True, "message": "已发送至本机默认应用", "path": str(path)}
            )
        except OSError as exc:
            return jsonify({"error": f"无法打开文件：{exc}"}), 400

    @app.before_request
    def retire_old_design_pages():
        if request.path.startswith("/api/eda/"):
            return jsonify({"error": "原设计入口已移除，请从主界面进入对应功能。"}), 410
        if request.path in {"/schematic_editor", "/custom_schematic_editor"}:
            return redirect(
                url_for(
                    "project.page",
                    page="tracking",
                    section="simulation",
                    _anchor="simulation-validation",
                )
            )
        if request.path in {"/api/run/start", "/api/run/once", "/api/custom_params"}:
            return (
                jsonify(
                    {
                        "error": "旧版参数设计任务已停用，请使用新页面中的项目代码与模型入口。"
                    }
                ),
                410,
            )

    def legacy_run_page():
        task = request.args.get("task")
        if task in {"run_sim", "run_sim_def", "eda"}:
            return redirect(
                url_for(
                    "project.page",
                    page="tracking",
                    section="simulation",
                    _anchor="simulation-validation",
                )
            )
        if task == "experiments":
            return redirect(
                url_for(
                    "project.page",
                    page="tracking",
                    section="experiments",
                    _anchor="tracking-results",
                )
            )
        return redirect(
            url_for(
                "project.page",
                page="capacitance" if task == "capacitance" else "tracking",
            )
        )

    app.view_functions["run_page"] = legacy_run_page
    app.register_blueprint(portal)


def run_local_server(app):
    """可直接启动的本地入口，不依赖 OpenAI 或训练环境。"""
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    print(f"平台网页：http://127.0.0.1:{port}", flush=True)
    try:
        from waitress import serve
    except ImportError:
        app.run(host=host, port=port, debug=False, threaded=True)
    else:
        serve(app, host=host, port=port, threads=8)
