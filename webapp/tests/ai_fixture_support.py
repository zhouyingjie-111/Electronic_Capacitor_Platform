"""Explicitly synthetic provider for offline regression and browser tests only."""

import copy
import json
import threading
import time
from pathlib import Path


class FakeProvider:
    def __init__(self, delay=0, catalogue=False):
        self.payloads = []
        self.verified = []
        self.delay = delay
        self.catalogue = catalogue

    def models(self, provider, key, connection=None):
        from ai_providers import AIError

        if key.startswith("invalid"):
            raise AIError("API Key 验证失败。", 401)
        models = [
            dict(
                id="fixture-vision",
                name="模拟视觉模型 · 非服务商实测",
                vision=True,
                thinking=True,
                tools=True,
            ),
            dict(
                id="fixture-text",
                name="模拟文本模型 · 非服务商实测",
                vision=False,
                thinking=True,
                tools=True,
            ),
        ]
        if self.catalogue:
            from ai_providers import CATALOGUES

            return [
                {**m, "name": "模拟验证 · " + m["name"]}
                for m in CATALOGUES.get(provider, [])
            ] + models
        return models

    def verify(self, provider, key, model, connection=None):
        self.verified.append((provider, model["id"]))

    def stream(self, provider, key, payload, stop, register_close, connection=None):
        self.payloads.append(copy.deepcopy(payload))
        register_close(lambda: None)
        last = payload["messages"][-1]
        question = next(
            m for m in reversed(payload["messages"]) if m["role"] == "user"
        )["content"][0]["text"]

        def chunk(delta, finish=None):
            return {"choices": [{"delta": delta, "finish_reason": finish}]}

        yield chunk(
            {"reasoning_content": "模拟接口返回的分析摘要：先核对单位与边界条件。"}
        )
        yield chunk({"encrypted_content": "fixture-encrypted-block"})
        if "不完整" in question:
            yield chunk({"content": "部分响应"})
            return
        if "慢速" in question:
            for index in range(150):
                if stop.is_set():
                    return
                time.sleep(0.04)
                yield chunk({"content": "测试片段 " + str(index) + "。"})
        elif (
            payload.get("tools")
            and "重复错误测试" in question
            and sum(m["role"] == "tool" for m in payload["messages"]) < 2
        ):
            yield chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "bad-call-" + str(len(payload["messages"])),
                            "type": "function",
                            "function": {
                                "name": "engineering_calculator",
                                "arguments": json.dumps({"expression": "1/("}),
                            },
                        }
                    ]
                },
                "tool_calls",
            )
            return
        elif (
            payload.get("tools")
            and last["role"] != "tool"
            and ("文件" in question or len(payload["tools"]) > 1)
        ):
            name = (
                "create_download_file"
                if "文件" in question
                else (
                    "open_project_model"
                    if "打开" in question
                    else (
                        "prepare_simulation"
                        if "仿真" in question
                        else "engineering_calculator"
                    )
                )
            )
            args = (
                {
                    "filename": (
                        "工程测试.xlsx"
                        if "Excel" in question or "XLSX" in question
                        else "工程测试.csv"
                    ),
                    "content": "参数,数值\n电流,22\n边沿时间_us,50\n",
                }
                if name == "create_download_file"
                else (
                    {"control": "pi"}
                    if name == "open_project_model"
                    else (
                        {
                            "control": "pi",
                            "duration": 0.01,
                            "parameters": {"P": 22, "TP": 50e-6},
                        }
                        if name == "prepare_simulation"
                        else {"expression": "1/(2*pi*sqrt(100e-6*470e-6))"}
                    )
                )
            )
            if "表达式兼容测试" in question:
                args = {
                    "expression": "```python\nL=100e-6; C=470e-6; f=1/(2*math.pi*np.sqrt(L*C)); print(f)\n```"
                }
            if name == "open_project_model":
                args = {
                    "control": (
                        "ann"
                        if "ANN" in question
                        else "custom" if "自定义" in question else "pi"
                    )
                }
            yield chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "fixture-call",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ]
                },
                "tool_calls",
            )
            return
        else:
            answer = "以下为**模拟接口联调**，并非服务商实测。\n\n### 工程计算示例\n\n谐振频率为 $f_0=1/(2\\pi\\sqrt{LC})$。\n\n| 参数 | 数值 |\n|---|---:|\n| L | 100 μH |\n| C | 470 μF |\n\n```matlab\nf0 = 1/(2*pi*sqrt(100e-6*470e-6));\n```\n\n<script>untrusted()</script>\n\n![外部图片](https://example.invalid/track.png)\n"
            if last["role"] == "tool":
                answer = "工具已返回真实测试函数结果。\n\n" + answer
            if "打开" in question:
                answer = "请点击按钮打开。"
            if "重复错误测试" in question:
                answer = "表达式不完整，请补全括号后重试。"
            for index in range(0, len(answer), 25):
                if stop.is_set():
                    return
                if self.delay:
                    time.sleep(self.delay)
                yield chunk({"content": answer[index : index + 25]})
        yield chunk({}, "stop")
        yield {
            "choices": [],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 60,
                "total_tokens": 160,
            },
        }


class FakeSimulations:
    def __init__(self):
        self.models = {
            kind: {"path": Path("unused.slx"), "label": kind.upper() + " 模拟模型"}
            for kind in ("pi", "ann", "custom")
        }
        self.jobs = {}
        self.calls = []

    def start(self, control, duration, parameters):
        self.calls.append((control, duration, parameters))
        job = {
            "id": "fixture-simulation",
            "state": "completed",
            "progress": 100,
            "message": "模拟任务已完成（非真实仿真）",
            "diagram_ready": False,
            "waveforms_ready": False,
        }
        self.jobs[job["id"]] = job
        return dict(job)

    def snapshot(self, job_id):
        return dict(self.jobs[job_id])


class FakeSerial:
    def snapshot(self):
        return {"connected": False, "latest": {}}
