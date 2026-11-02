"""Auditable tools: arithmetic, uploaded data and confirmed project simulations."""

import ast
import csv
import io
import json
import math
import operator
import re
import time
import unicodedata
import uuid
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from ai_providers import AIError
from ai_model_builder import BUILD_TOOL, validate_plan


def tool(name, description, properties, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


TOOLS = [
    BUILD_TOOL,
    tool(
        "create_download_file",
        "把需要交付的内容生成为真实可下载文件。支持txt/md/csv/json/代码/docx/xlsx/pdf。表格xlsx/csv的content传CSV文本；docx/pdf传正文或Markdown。不要只编造下载链接。仅写资料文件，不执行程序。",
        {
            "filename": {
                "type": "string",
                "description": "含扩展名的文件名，如控制建议.docx",
            },
            "content": {
                "type": "string",
                "description": "完整文件内容；最多120000字符",
            },
        },
        ["filename", "content"],
    ),
    tool(
        "engineering_calculator",
        "仅做数值工程计算；支持常见数学表达式及简单数值变量赋值，三角函数输入为弧度。单位先换算为SI数值。不能用于打开软件、运行脚本或搭建模型。",
        {
            "expression": {
                "type": "string",
                "description": "如 1/(2*pi*sqrt(100e-6*470e-6))",
            }
        },
        ["expression"],
    ),
    tool(
        "analyze_uploaded_csv",
        "统计用户已上传CSV各数值列，返回样本数/最小/最大/均值/标准差。",
        {"attachment_id": {"type": "string"}},
        ["attachment_id"],
    ),
    tool(
        "inspect_project_model",
        "只读检查项目中登记的Simulink模型模块与示波器，不运行模型。",
        {"control": {"type": "string", "enum": ["pi", "ann", "custom"]}},
        ["control"],
    ),
    tool(
        "open_project_model",
        "打开当前助手对应的Simulink工作区。首页打开独立空白模型；快速追踪打开PI/ANN/自定义模型；辨识助手打开已配置辨识模型，未提供时打开独立空白辨识工作区。只生成打开按钮，不运行仿真。",
        {
            "control": {
                "type": "string",
                "enum": ["pi", "ann", "custom", "blank", "capacitance"],
            }
        },
    ),
    tool(
        "prepare_simulation",
        "准备Simulink仿真方案；仅生成确认卡，用户点击运行前不能声称已经执行。",
        {
            "control": {"type": "string", "enum": ["pi", "ann", "custom"]},
            "duration": {
                "type": "number",
                "description": "秒，默认0.01，范围0.000001至0.1",
            },
            "parameters": {
                "type": "object",
                "properties": {
                    key: {"type": "number"}
                    for key in [
                        "P",
                        "TP",
                        "P_V",
                        "I_V",
                        "P_I",
                        "I_I",
                        "D_rise",
                        "D_fall",
                    ]
                },
                "additionalProperties": False,
            },
        },
        ["control"],
    ),
    tool(
        "get_simulation_result",
        "读取当前会话中用户确认运行的仿真状态和原始波形统计；不会凭空生成实验结果。",
        {"job_id": {"type": "string"}},
        ["job_id"],
    ),
]


def calculate(expression):
    if not isinstance(expression, str) or len(expression) > 300:
        raise AIError("计算式过长。")
    expression = unicodedata.normalize("NFKC", expression).strip()
    fenced = re.fullmatch(
        r"```(?:python|matlab)?\s*\n(.*?)\n```", expression, re.S | re.I
    )
    if fenced:
        expression = fenced.group(1).strip()
    elif expression.startswith("`") and expression.endswith("`"):
        expression = expression[1:-1].strip()
    if re.search(
        r"\b(open_system|new_system|add_block|add_line|set_param|sim|eval|exec|__import__)\s*\(",
        expression,
    ):
        raise AIError(
            "计算工具不能打开软件或搭建模型，请改用当前助手的 Simulink 工具。"
        )
    expression = expression.translate(
        str.maketrans({"π": "pi", "×": "*", "÷": "/", "−": "-", "–": "-", "^": "**"})
    )
    expression = expression.replace(".*", "*").replace("./", "/").replace(".**", "**")
    expression = re.sub(
        r"\b(?:math|np|numpy)\.(sqrt|sin|cos|tan|log|log10|exp|fabs|pi|e)\b",
        r"\1",
        expression,
    )
    try:
        tree = ast.parse(expression, mode="exec")
    except SyntaxError:
        raise AIError(
            "请提供数值表达式，例如 1/(2*pi*sqrt(100e-6*470e-6))；参数先换算为SI数值，不要附带单位或说明。"
        ) from None
    if len(list(ast.walk(tree))) > 100:
        raise AIError("计算式过于复杂。")
    functions = {
        name: getattr(math, name)
        for name in ("sqrt", "sin", "cos", "tan", "log", "log10", "exp", "fabs")
    }
    functions.update(abs=abs, min=min, max=max)
    binary = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
    }
    variables = {"pi": math.pi, "e": math.e}

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = node.value
        elif isinstance(node, ast.Name) and node.id in variables:
            result = variables[node.id]
        elif isinstance(node, ast.UnaryOp) and isinstance(
            node.op, (ast.UAdd, ast.USub)
        ):
            result = visit(node.operand) * (1 if isinstance(node.op, ast.UAdd) else -1)
        elif isinstance(node, ast.BinOp) and type(node.op) in binary:
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError()
            result = binary[type(node.op)](left, right)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in functions
            and not node.keywords
            and 1 <= len(node.args) <= 8
        ):
            result = functions[node.func.id](*[visit(arg) for arg in node.args])
        else:
            raise ValueError()
        if (
            not isinstance(result, (int, float))
            or not math.isfinite(result)
            or abs(result) > 1e100
        ):
            raise ValueError()
        return result

    try:
        if not tree.body or len(tree.body) > 12:
            raise ValueError()
        for statement in tree.body:
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                name = statement.targets[0].id
                if (
                    not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", name)
                    or name in functions
                    or name in ("pi", "e")
                ):
                    raise ValueError()
                value = visit(statement.value)
                variables[name] = value
            elif isinstance(statement, ast.Expr):
                node = statement.value
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"
                    and len(node.args) == 1
                    and not node.keywords
                ):
                    node = node.args[0]
                value = visit(node)
            else:
                raise ValueError()
    except (ValueError, TypeError, OverflowError, ZeroDivisionError):
        raise AIError("计算式超出支持范围或数值不合法。") from None
    return {
        "expression": expression,
        "value": value,
        "engine": "Python 受限工程计算",
        "angle_unit": "rad",
    }


class AITools:
    def __init__(self, simulations):
        self.simulations = simulations

    def run(self, name, arguments, session, thread):
        if name == "prepare_model_build":
            plan = validate_plan(arguments)
            draft = {
                "id": uuid.uuid4().hex,
                "action": "build_model",
                "label": plan["title"],
                "plan": plan,
                "created_at": time.time(),
                "thread_id": thread["id"],
                "state": "pending",
            }
            session["drafts"][draft["id"]] = draft
            return {
                "status": "awaiting_user_confirmation",
                "draft": dict(draft),
                "message": "结构化方案已准备，用户点击创建后才会实际搭建新模型；不自动运行仿真。",
            }
        if name == "create_download_file":
            return {
                "file": self.file_writer(
                    session, thread, arguments.get("filename"), arguments.get("content")
                )
            }
        if name == "engineering_calculator":
            return calculate(arguments.get("expression"))
        if name == "analyze_uploaded_csv":
            item = session["attachments"].get(arguments.get("attachment_id"))
            used = {
                i
                for message in thread["public"]
                for i in message.get("attachment_ids", [])
            }
            if (
                not item
                or item["id"] not in used
                or not item["name"].lower().endswith(".csv")
            ):
                raise AIError("只能分析本会话已经发送的CSV附件。")
            rows = list(csv.reader(io.StringIO(item["text"])))
            if not rows:
                raise AIError("CSV没有数据。")
            columns = []
            for index, label in enumerate(rows[0][:64]):
                values = []
                for row in rows[1:20001]:
                    try:
                        value = float(row[index])
                    except (ValueError, IndexError):
                        continue
                    if math.isfinite(value) and abs(value) < 1e100:
                        values.append(value)
                if values:
                    mean = math.fsum(value / len(values) for value in values)
                    std = math.sqrt(
                        math.fsum(
                            ((value - mean) / max(1, abs(mean))) ** 2 / len(values)
                            for value in values
                        )
                    ) * max(1, abs(mean))
                    columns.append(
                        {
                            "name": label,
                            "count": len(values),
                            "min": min(values),
                            "max": max(values),
                            "mean": mean,
                            "std_population": std,
                        }
                    )
            return {
                "file": item["name"],
                "parsed_rows": len(rows) - 1,
                "columns": columns,
                "note": item["note"],
                "engine": "Python CSV 分析",
            }
        if name == "inspect_project_model":
            control = (
                "capacitance"
                if thread["scope"] == "capacitance"
                else arguments.get("control")
            )
            if control not in self.simulations.models:
                raise AIError("模型不存在。")
            model = self.simulations.models[control]
            blocks = []
            with ZipFile(model["path"]) as archive:
                for path in archive.namelist():
                    if (
                        not path.startswith("simulink/")
                        or not path.endswith(".xml")
                        or "systems/" not in path
                    ):
                        continue
                    root = ET.fromstring(archive.read(path))
                    for node in root.iter("Block"):
                        blocks.append(
                            {"name": node.get("Name"), "type": node.get("BlockType")}
                        )
            return {
                "model": model["label"],
                "block_count": len(blocks),
                "blocks": blocks[:100],
                "note": "只读结构检查，未运行仿真；上传模型不能在此执行。",
            }
        if name == "open_project_model":
            if thread["scope"] == "general":
                control = "blank"
                label = "Simulink"
            elif thread["scope"] == "capacitance":
                control = "capacitance"
                label = "电容辨识工作区"
            else:
                control = arguments.get("control", "pi")
                if (
                    control not in ("pi", "ann", "custom")
                    or control not in self.simulations.models
                ):
                    raise AIError("请选择快速追踪的 PI、ANN 或自定义模型。")
                label = self.simulations.models[control]["label"]
            draft = {
                "id": uuid.uuid4().hex,
                "action": "open_model",
                "control": control,
                "label": label,
                "created_at": time.time(),
                "thread_id": thread["id"],
                "state": "pending",
            }
            session["drafts"][draft["id"]] = draft
            return {
                "status": "awaiting_user_confirmation",
                "draft": dict(draft),
                "message": "已准备打开按钮，尚未执行。",
            }
        if name == "prepare_simulation":
            if thread["scope"] == "general":
                raise AIError(
                    "首页助手使用独立空白工作区，请在快速追踪助手中运行项目模型。"
                )
            from simulation_runner import DEFAULT_PARAMETERS, CAPACITANCE_PARAMETERS

            control = (
                "capacitance"
                if thread["scope"] == "capacitance"
                else arguments.get("control")
            )
            duration = arguments.get("duration", 0.01)
            if (
                thread["scope"] == "capacitance"
                and control not in self.simulations.models
            ):
                raise AIError("电容辨识模型暂不可用。")
            if thread["scope"] == "tracking" and control not in ("pi", "ann", "custom"):
                raise AIError("请选择快速追踪的仿真模型。")
            parameters = arguments.get("parameters", {})
            if (
                control not in self.simulations.models
                or type(duration) not in (int, float)
                or not math.isfinite(duration)
                or not 1e-6 <= duration <= 0.1
            ):
                raise AIError("请选择项目模型，仿真时长范围为0.000001至0.1秒。")
            if not isinstance(parameters, dict):
                raise AIError("仿真参数格式不正确。")
            defaults = (
                CAPACITANCE_PARAMETERS
                if control == "capacitance"
                else DEFAULT_PARAMETERS
            )
            allowed = set(defaults) | (
                {"D_rise", "D_fall"} if control == "custom" else set()
            )
            if set(parameters) - allowed:
                raise AIError("包含不支持的参数。")
            values = {**defaults, **parameters}
            for key, value in values.items():
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not 0 <= value <= 10000
                    or (key in ("P", "TP") and value <= 0)
                ):
                    raise AIError("仿真参数不合法。")
            if control == "custom" and not {"D_rise", "D_fall"} <= set(values):
                raise AIError("自定义模型需要D_rise和D_fall。")
            draft = {
                "id": uuid.uuid4().hex,
                "control": control,
                "duration": duration,
                "parameters": values,
                "label": self.simulations.models[control]["label"],
                "created_at": time.time(),
                "thread_id": thread["id"],
                "state": "pending",
            }
            session["drafts"][draft["id"]] = draft
            return {
                "status": "awaiting_user_confirmation",
                "draft": dict(draft),
                "message": "方案已准备，尚未执行；请用户点击运行此仿真。",
            }
        if name == "get_simulation_result":
            job_id = arguments.get("job_id")
            if job_id not in session["tool_jobs"]:
                raise AIError("只能读取本会话确认过的仿真任务。")
            state = self.simulations.snapshot(job_id)
            result = {
                key: state.get(key)
                for key in (
                    "id",
                    "label",
                    "state",
                    "message",
                    "progress",
                    "parameters",
                    "requested_duration",
                    "waveforms_ready",
                )
            }
            if state.get("waveforms_ready"):
                import numpy as np

                folder = self.simulations.jobs[job_id]["_folder"] / "waveforms"
                index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
                if isinstance(index, dict):
                    index = [index]
                summary = []
                for scope in index:
                    channels = scope["channels"]
                    channels = [channels] if isinstance(channels, dict) else channels
                    for row in channels:
                        filename = row["file"]
                        import re

                        if not re.fullmatch(
                            r"(voltage|current|switch|model_input)_\d+\.bin", filename
                        ):
                            continue
                        count = int(row["count"])
                        path = folder / filename
                        if (
                            count <= 0
                            or path.stat().st_size != count * 16
                            or count > 10000000
                        ):
                            continue
                        raw = np.memmap(path, dtype="<f8", mode="r", shape=(count, 2))
                        low = math.inf
                        high = -math.inf
                        total = 0.0
                        squares = 0.0
                        valid = 0
                        for start in range(0, count, 100000):
                            values = raw[start : start + 100000, 1]
                            values = values[np.isfinite(values)]
                            if not len(values):
                                continue
                            low = min(low, float(values.min()))
                            high = max(high, float(values.max()))
                            total += float(values.sum())
                            squares += float(np.dot(values, values))
                            valid += len(values)
                        if valid:
                            summary.append(
                                {
                                    "scope": scope["name"],
                                    "channel": row["label"],
                                    "samples": valid,
                                    "min": low,
                                    "max": high,
                                    "mean": total / valid,
                                    "rms": math.sqrt(squares / valid),
                                }
                            )
                result["waveform_statistics"] = summary
                result["note"] = (
                    "统计来自实际原始导出波形；未自动推断信号参考值或实验误差。"
                )
            return result
        raise AIError("未接入此工具。")

    def execute(self, draft, session):
        if draft.get("action") == "build_model" and draft["state"] == "submitted":
            return self.simulations.snapshot(draft["job_id"])
        if draft.get("action") == "open_model" and draft["state"] == "submitted":
            previous = self.simulations.snapshot(draft["job_id"])
            if previous["state"] in ("starting", "loading"):
                return previous
            if previous["state"] in ("opened", "failed", "stopped"):
                draft["state"] = "pending"
        if draft["state"] != "pending":
            raise AIError("此仿真方案已经提交。", 409)
        if time.time() - draft["created_at"] > 1800:
            raise AIError("仿真方案已过期，请重新生成。", 409)
        if draft.get("action") == "build_model":
            state = self.simulations.build_model(draft["plan"])
        elif draft.get("action") == "open_model":
            state = self.simulations.open_model(draft["control"])
        else:
            state = self.simulations.start(
                draft["control"], draft["duration"], draft["parameters"]
            )
        session["tool_jobs"].add(state["id"])
        draft["state"] = "submitted"
        draft["job_id"] = state["id"]
        return state
