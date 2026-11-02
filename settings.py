"""Portable configuration. Environment variables override local config.json.

Only path/tool options are accepted here. API keys are entered in the browser
and are deliberately absent from configuration and the source release.
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OPTIONS = {
    "HOST",
    "PORT",
    "ANN_PYTHON",
    "CAP_PYTHON",
    "MATLAB_ROOT",
    "TI_CGT_ROOT",
    "CAP_CLANG",
    "CAP_CL2000",
    "DELTA_PROJECT_ROOT",
}
PATH_OPTIONS = OPTIONS - {"HOST", "PORT"}


def load_config(root=ROOT, environment=None):
    """返回规范化配置，不修改进程环境，便于独立测试。

    环境变量优先于本机文件；相对路径始终以项目目录为基准。未配置的
    外部工具交由调用服务处理，基础网页不因此要求安装训练或仿真环境。
    """
    root = Path(root).resolve()
    path = root / "config.json"
    try:
        local = (
            json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        )
    except (OSError, ValueError) as error:
        raise ValueError(
            "无法读取 config.json，请检查 JSON 格式和文件权限。"
        ) from error
    if not isinstance(local, dict) or set(local) - OPTIONS:
        raise ValueError("config.json 含未知配置项，请参考 config.example.json。")
    values = {"HOST": "127.0.0.1", "PORT": "5001", "DELTA_PROJECT_ROOT": "project_data"}
    supplied = os.environ if environment is None else environment
    for source in (local, {key: supplied[key] for key in OPTIONS if key in supplied}):
        for key, value in source.items():
            if value is None or value == "":
                continue
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ValueError(f"{key} 必须为文本，PORT 也可为整数。")
            if key != "PORT" and not isinstance(value, str):
                raise ValueError(f"{key} 必须为文本。")
            values[key] = str(value).strip()
    try:
        port = int(values["PORT"])
    except ValueError as error:
        raise ValueError("PORT 必须为 1–65535 的整数。") from error
    if not 1 <= port <= 65535:
        raise ValueError("PORT 必须为 1–65535 的整数。")
    values["PORT"] = str(port)
    if not values["HOST"]:
        raise ValueError("HOST 不能为空。")
    for key in PATH_OPTIONS & values.keys():
        candidate = Path(values[key]).expanduser()
        values[key] = str(
            (root / candidate).resolve() if not candidate.is_absolute() else candidate
        )
    return values


def configure():
    """在导入业务服务前调用；环境优先级在 load_config 中统一处理。"""
    os.environ.update(load_config())
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
