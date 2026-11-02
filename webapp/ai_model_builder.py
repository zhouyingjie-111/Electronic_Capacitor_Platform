"""Validate a bounded signal-flow graph before sending it to MathWorks model_edit.

No model-supplied MATLAB, callbacks, library paths or filesystem paths are accepted.
The graph is intentionally extensible; unsupported physical blocks fail explicitly.
"""

import ast
import math
import re

from ai_providers import AIError

IDENTIFIER = r"[A-Za-z][A-Za-z0-9_]{0,30}"
KEYWORDS = set(
    "break case catch classdef continue else elseif end for function global if otherwise parfor persistent return spmd switch try while".split()
)
BLOCKS = {
    "Constant": {"Value"},
    "Step": {"Time", "Before", "After", "SampleTime"},
    "Sin": {"Amplitude", "Frequency", "Phase", "Bias", "SampleTime"},
    "Gain": {"Gain"},
    "Sum": {"Inputs"},
    "Product": {"Inputs"},
    "Integrator": {"InitialCondition"},
    "UnitDelay": {"InitialCondition", "SampleTime"},
    "TransferFcn": {"Numerator", "Denominator"},
    "Saturation": {"UpperLimit", "LowerLimit"},
    "Switch": {"Criteria", "Threshold"},
    "Mux": {"Inputs"},
    "Scope": {"NumInputPorts"},
    "Inport": set(),
    "Outport": set(),
    "Terminator": set(),
    "Abs": set(),
    "Signum": set(),
}


def _expression(value, variables):
    if not isinstance(value, str) or not 1 <= len(value) <= 120:
        raise AIError("模块参数须为不超过120字符的数值或变量表达式。")
    try:
        tree = ast.parse(value.replace("^", "**"), mode="eval")
        if len(list(ast.walk(tree))) > 40:
            raise ValueError()

        def visit(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                result = node.value
            elif isinstance(node, ast.Name) and node.id in variables:
                result = variables[node.id]
            elif isinstance(node, ast.UnaryOp) and isinstance(
                node.op, (ast.UAdd, ast.USub)
            ):
                result = visit(node.operand) * (
                    -1 if isinstance(node.op, ast.USub) else 1
                )
            elif isinstance(node, ast.BinOp) and isinstance(
                node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
            ):
                a, b = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Pow) and abs(b) > 12:
                    raise ValueError()
                result = (
                    a + b
                    if isinstance(node.op, ast.Add)
                    else (
                        a - b
                        if isinstance(node.op, ast.Sub)
                        else (
                            a * b
                            if isinstance(node.op, ast.Mult)
                            else a / b if isinstance(node.op, ast.Div) else a ** b
                        )
                    )
                )
            else:
                raise ValueError()
            if (
                type(result) not in (int, float)
                or not math.isfinite(result)
                or abs(result) > 1e15
            ):
                raise ValueError()
            return result

        visit(tree.body)
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError):
        raise AIError(
            "模块参数只能包含已声明的数值变量、pi和基本四则运算；不接受函数、脚本或路径。"
        ) from None
    return value.strip()


def validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) != {
        "title",
        "description",
        "variables",
        "blocks",
        "connections",
    }:
        raise AIError("建模方案必须包含标题、说明、变量、模块和连接。")
    if not isinstance(plan["title"], str) or not 1 <= len(plan["title"]) <= 60:
        raise AIError("模型标题长度应为1至60字符。")
    if not isinstance(plan["description"], str) or len(plan["description"]) > 1200:
        raise AIError("模型说明最多1200字符。")
    if not isinstance(plan["variables"], list) or len(plan["variables"]) > 40:
        raise AIError("模型最多40个数值变量。")
    variables = {"pi": math.pi}
    normalized_vars = []
    for row in plan["variables"]:
        if not isinstance(row, dict) or set(row) != {"name", "value"}:
            raise AIError("变量格式不正确。")
        name, value = row["name"], row["value"]
        if (
            not isinstance(name, str)
            or not re.fullmatch(IDENTIFIER, name)
            or name in variables
            or name in KEYWORDS
        ):
            raise AIError("变量名称须为不重复的英文字母、数字和下划线，且以字母开头。")
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or abs(value) > 1e12
        ):
            raise AIError("变量必须是有限数值。")
        variables[name] = value
        normalized_vars.append(dict(row))
    blocks = plan["blocks"]
    if not isinstance(blocks, list) or not 2 <= len(blocks) <= 60:
        raise AIError("每次建模支持2至60个标准模块。")
    ids = {}
    names = set()
    normalized_blocks = []
    for row in blocks:
        if not isinstance(row, dict) or set(row) != {
            "id",
            "name",
            "type",
            "parameters",
        }:
            raise AIError("模块格式不正确。")
        for key in ("id", "name"):
            if (
                not isinstance(row[key], str)
                or not re.fullmatch(IDENTIFIER, row[key])
                or row[key] in KEYWORDS
            ):
                raise AIError(
                    "模块名称与ID须使用英文字母、数字和下划线，且以字母开头。"
                )
        if row["id"] in ids or row["name"].lower() in names:
            raise AIError("模块ID与名称不能重复。")
        kind = row["type"]
        if not isinstance(kind, str) or kind not in BLOCKS:
            raise AIError("当前自动建模支持标准信号与控制模块；不支持此模块类型。")
        if not isinstance(row["parameters"], list) or len(row["parameters"]) > 8:
            raise AIError("模块参数格式不正确。")
        params = {}
        for item in row["parameters"]:
            if not isinstance(item, dict) or set(item) != {"name", "value"}:
                raise AIError("模块参数格式不正确。")
            key, value = item["name"], item["value"]
            if not isinstance(key, str) or key not in BLOCKS[kind] or key in params:
                raise AIError("包含不支持或重复的模块参数。")
            if key == "Inputs" or key == "NumInputPorts":
                if not isinstance(value, str) or not (
                    re.fullmatch(r"[1-8]", value)
                    or kind in ("Sum", "Product")
                    and re.fullmatch(
                        r"[+\-]{1,8}" if kind == "Sum" else r"[*\/]{1,8}", value
                    )
                ):
                    raise AIError("输入端口数量应为1至8，求和/乘积也可使用运算符序列。")
            elif key == "Criteria":
                if value not in ("u2 >= Threshold", "u2 > Threshold", "u2 ~= 0"):
                    raise AIError("不支持此Switch条件。")
            elif key in ("Numerator", "Denominator"):
                if isinstance(value, str) and re.fullmatch(
                    r"\[\s*[-+0-9.eE\s,]+\s*\]", value
                ):
                    tokens = value[1:-1].replace(",", " ").split()
                    if not 1 <= len(tokens) <= 12:
                        raise AIError("传递函数系数最多12项。")
                    for token in tokens:
                        _expression(token, {})
                else:
                    value = _expression(value, variables)
            else:
                value = _expression(value, variables)
            params[key] = value
        # Explicit port numbering avoids ambiguous/unconnected workspace interfaces.
        if kind in ("Inport", "Outport"):
            params["Port"] = str(1 + sum(b["type"] == kind for b in normalized_blocks))
        count = params.get("Inputs", "2" if kind in ("Sum", "Product") else "2")
        inputs = int(count) if count.isdigit() else len(count)
        if kind in ("Constant", "Step", "Sin", "Inport"):
            inputs = 0
        elif kind == "Scope":
            inputs = int(params.get("NumInputPorts", "1"))
        elif kind == "Switch":
            inputs = 3
        elif kind not in ("Sum", "Product", "Mux"):
            inputs = 1
        outputs = 0 if kind in ("Scope", "Outport", "Terminator") else 1
        ids[row["id"]] = (inputs, outputs)
        names.add(row["name"].lower())
        normalized_blocks.append(
            {"id": row["id"], "name": row["name"], "type": kind, "params": params}
        )
    wires = plan["connections"]
    if not isinstance(wires, list) or not 1 <= len(wires) <= 120:
        raise AIError("每次建模支持1至120条连接。")
    destinations = set()
    sources = set()
    normalized_wires = []
    for row in wires:
        if not isinstance(row, dict) or set(row) != {
            "source",
            "source_port",
            "target",
            "target_port",
        }:
            raise AIError("连接格式不正确。")
        source, target, sp, tp = (
            row["source"],
            row["target"],
            row["source_port"],
            row["target_port"],
        )
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or source not in ids
            or target not in ids
        ):
            raise AIError("连接引用了不存在的模块。")
        if (
            type(sp) is not int
            or type(tp) is not int
            or not 1 <= sp <= ids[source][1]
            or not 1 <= tp <= ids[target][0]
        ):
            raise AIError("连接端口编号不正确。")
        if (target, tp) in destinations:
            raise AIError("每个输入端口只能连接一个信号源。")
        destinations.add((target, tp))
        sources.add((source, sp))
        normalized_wires.append(dict(row))
    for ref, (inputs, outputs) in ids.items():
        if any((ref, p) not in destinations for p in range(1, inputs + 1)) or any(
            (ref, p) not in sources for p in range(1, outputs + 1)
        ):
            raise AIError(
                "存在未连接端口，请补全连线或添加Terminator，再提交建模方案。"
            )
    return {
        "title": plan["title"],
        "description": plan["description"],
        "variables": normalized_vars,
        "blocks": normalized_blocks,
        "connections": normalized_wires,
    }


def operations(plan):
    result = [
        {
            "op": "add_block",
            "type": b["type"],
            "name": b["name"],
            "ref": b["id"],
            "params": b["params"],
        }
        for b in plan["blocks"]
    ]
    result.extend(
        {
            "op": "connect",
            "target": f"#{w['source']}.y{w['source_port']} -> #{w['target']}.u{w['target_port']}",
        }
        for w in plan["connections"]
    )
    return result


BUILD_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_model_build",
        "description": "根据用户需求设计并提交新Simulink模型的结构化搭建方案。用户点击创建后通过MathWorks model_edit真实搭建、自动排版、检查连接并提供SLX下载，不运行仿真。支持标准信号/控制系统和电路平均方程模型，不支持物理开关器件、Simscape、Stateflow、代码执行或修改已有模型。每个输入/输出必须连接；不用的输出接Terminator。模块类型："
        + ", ".join(BLOCKS)
        + "。各模块可配置参数："
        + "; ".join(k + ":" + ",".join(sorted(v)) for k, v in BLOCKS.items() if v)
        + "。参数为字符串数值、已声明变量的四则运算或传递函数数字系数数组；禁止函数调用和路径。Sum/ Product/Mux的Inputs为数量字符串或运算符序列；Scope的NumInputPorts为数量字符串。模块名/ID为英文且以字母开头。最多60模块、120连线。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "description", "variables", "blocks", "connections"],
            "properties": {
                "title": {"type": "string"},
                "description": {
                    "type": "string",
                    "description": "说明用途、方程、假设和适用范围，不把平均模型说成开关级模型。",
                },
                "variables": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "value": {"type": "number"},
                        },
                        "required": ["name", "value"],
                        "additionalProperties": False,
                    },
                },
                "blocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "name", "type", "parameters"],
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "type": {"type": "string", "enum": list(BLOCKS)},
                            "parameters": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "value": {"type": "string"},
                                    },
                                    "required": ["name", "value"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                    },
                },
                "connections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["source", "source_port", "target", "target_port"],
                        "additionalProperties": False,
                        "properties": {
                            "source": {"type": "string"},
                            "source_port": {"type": "integer"},
                            "target": {"type": "string"},
                            "target_port": {"type": "integer"},
                        },
                    },
                },
            },
        },
    },
}
