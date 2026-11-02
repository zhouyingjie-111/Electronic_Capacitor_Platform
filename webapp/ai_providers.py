"""Official provider endpoints and capability-aware Chat Completions adapters."""

# 扩展提供商时在本模块定义模型能力、消息转换与流式解析，网页只消费统一事件。密钥始终来自用户当前会话。

import json
import re
import time


class AIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "base": "https://api.deepseek.com",
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    "doubao": {
        "name": "豆包 · 火山方舟",
        "base": "https://ark.cn-beijing.volces.com/api/v3",
        "key_url": "https://console.volcengine.com/ark/region:cn-beijing/apiKey",
    },
    "kimi": {
        "name": "Kimi · 月之暗面",
        "base": "https://api.moonshot.cn/v1",
        "key_url": "https://platform.moonshot.cn/console/api-keys",
    },
    "qwen": {
        "name": "千问 · 阿里云百炼",
        "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_url": "https://help.aliyun.com/zh/model-studio/get-api-key",
    },
}

QWEN_REGIONS = {
    "cn-beijing": "dashscope.aliyuncs.com",
    "ap-southeast-1": "dashscope-intl.aliyuncs.com",
    "us-east-1": "dashscope-us.aliyuncs.com",
    "cn-hongkong": "cn-hongkong.dashscope.aliyuncs.com",
    "ap-northeast-1": None,
    "eu-central-1": None,
}


def connection_options(provider, value=None):
    """Accept region/workspace labels, never an arbitrary destination URL."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise AIError("连接设置格式不正确。")
    if provider == "qwen":
        if set(value) - {"region", "workspace"}:
            raise AIError("千问连接设置不正确。")
        region = value.get("region", "cn-beijing")
        workspace = value.get("workspace", "")
        if not isinstance(region, str) or region not in QWEN_REGIONS:
            raise AIError("请选择有效的百炼地域。")
        if not isinstance(workspace, str) or (
            workspace
            and not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?", workspace
            )
        ):
            raise AIError("业务空间 ID 只能包含英文字母、数字及连字符。")
        if not workspace and not QWEN_REGIONS[region]:
            raise AIError("此地域需要填写业务空间 ID。")
        return {"region": region, "workspace": workspace}
    if provider == "kimi":
        if set(value) - {"region"} or value.get("region", "cn") not in ("cn", "intl"):
            raise AIError("请选择有效的 Kimi 平台。")
        return {"region": value.get("region", "cn")}
    if value:
        raise AIError("当前服务商不支持这些连接设置。")
    return {}


def official_base(provider, connection=None):
    config = connection_options(provider, connection)
    if provider == "kimi":
        return (
            "https://api.moonshot."
            + ("ai" if config["region"] == "intl" else "cn")
            + "/v1"
        )
    if provider == "qwen":
        host = (
            (config["workspace"] + "." + config["region"] + ".maas.aliyuncs.com")
            if config["workspace"]
            else QWEN_REGIONS[config["region"]]
        )
        return "https://" + host + "/compatible-mode/v1"
    return PROVIDERS[provider]["base"]


def thinking_parameters(provider, model, enabled):
    if not model.get("thinking"):
        return {}
    if provider == "qwen":
        return {"enable_thinking": bool(enabled)}
    if provider == "kimi":
        name = model["id"]
        if name.startswith("kimi-k3"):
            return {"reasoning_effort": "high"}
        if name.startswith("kimi-k2.7"):
            return {}  # Always on; disabled is an API error.
        return {
            "thinking": {
                "type": "enabled" if enabled else "disabled",
                **({"keep": "all"} if enabled else {}),
            }
        }
    if provider == "deepseek" and model["id"] == "deepseek-reasoner":
        return {}
    return {"thinking": {"type": "enabled" if enabled else "disabled"}}


# Official catalogue, reviewed 2026-10-28. Availability is probed with the user's
# key; this is not presented as a list of models already activated for that key.
DOUBAO_MODELS = [
    {"id": "doubao-seed-2-1-pro-260915", "name": "Doubao Seed 2.1 Pro · 260915"},
    {"id": "doubao-seed-2-1-lite-260915", "name": "Doubao Seed 2.1 Lite · 260915"},
    {"id": "doubao-seed-evolving", "name": "Doubao Seed Evolving · 滚动版本"},
    {"id": "doubao-seed-2-1-pro-260628", "name": "Doubao Seed 2.1 Pro · 260628"},
    {"id": "doubao-seed-2-1-turbo-260628", "name": "Doubao Seed 2.1 Turbo · 260628"},
    {"id": "doubao-seed-2-0-lite-260428", "name": "Doubao Seed 2.0 Lite · 260428"},
    {"id": "doubao-seed-2-0-mini-260428", "name": "Doubao Seed 2.0 Mini · 260428"},
    {
        "id": "doubao-seed-2-0-pro-260215",
        "name": "Doubao Seed 2.0 Pro · 260215（存量版，11月24日下线）",
    },
]
for _model in DOUBAO_MODELS:
    _model.update(vision=True, thinking=True, tools=True, catalogue=True)

KIMI_MODELS = [
    {"id": "kimi-k3.1-pro", "name": "Kimi K3.1 Pro"},
    {"id": "kimi-k3.1-pro-highspeed", "name": "Kimi K3.1 Pro · Highspeed"},
    {"id": "kimi-k3", "name": "Kimi K3"},
    {"id": "kimi-k2.7-code", "name": "Kimi K2.7 Code"},
    {"id": "kimi-k2.7-code-highspeed", "name": "Kimi K2.7 Code · Highspeed"},
    {"id": "kimi-k2.6", "name": "Kimi K2.6"},
]
for _model in KIMI_MODELS:
    _model.update(
        vision=True,
        thinking=True,
        tools=True,
        catalogue=True,
        thinking_required=_model["id"] != "kimi-k2.6",
    )

# Official chat-capable versions; snapshots are selectable separately.
# Other tasks (embedding, speech, image generation) do not belong in chat menus.
QWEN_MODELS = []
for _id in (
    "qwen3.8-max",
    "qwen3.8-max-0902",
    "qwen3.8-flash",
    "qwen3.7-plus",
    "qwen3.7-plus-2026-05-26",
    "qwen3.7-flash",
    "qwen3.7-flash-2026-07-15",
    "qwen3.7-max",
    "qwen3.7-max-2026-06-08",
    "qwen3.6-plus",
    "qwen3.6-plus-2026-04-02",
    "qwen3.6-flash",
    "qwen3.6-flash-2026-04-16",
    "qwen3.5-plus",
    "qwen3.5-plus-2026-04-20",
    "qwen3.5-plus-2026-02-15",
    "qwen3.5-flash",
    "qwen3.5-flash-2026-02-23",
    "qwen-plus",
    "qwen-plus-latest",
    "qwen-flash",
    "qwen-flash-2025-07-28",
    "qwen-max",
    "qwen3-coder-flash",
):
    QWEN_MODELS.append(
        {
            "id": _id,
            "name": _id,
            "vision": _id.startswith(("qwen3.8", "qwen3.7", "qwen3.6", "qwen3.5")),
            "thinking": _id not in ("qwen-max", "qwen3-coder-flash"),
            "tools": True,
            "catalogue": True,
        }
    )

CATALOGUES = {"doubao": DOUBAO_MODELS, "kimi": KIMI_MODELS, "qwen": QWEN_MODELS}


def public_failure(status):
    if status == 401:
        return AIError("API Key 验证失败，请检查密钥与服务商是否对应。", 401)
    if status == 402:
        return AIError("服务商账户额度不足，请检查余额或计费状态。", 402)
    if status == 403:
        return AIError("当前密钥没有该模型的访问权限，请检查模型开通和密钥权限。", 403)
    if status == 404:
        return AIError("模型或推理接入点不可用，请核对版本 ID。", 400)
    if status == 429:
        return AIError("服务商限流或配额不足，请稍后重试。", 429)
    if status in (400, 422):
        return AIError("服务商拒绝了请求，请核对模型能力、附件及上下文长度。", 400)
    return AIError("服务商暂时无法响应，请稍后重试。", 502)


class ProviderClient:
    def __init__(self, client_factory=None):
        self.client_factory = client_factory

    def _client(self):
        # Lazy import lets offline tests run independently of the host's TLS DLLs.
        if self.client_factory:
            return self.client_factory()
        import httpx

        return httpx.Client(
            timeout=httpx.Timeout(120, connect=15, write=30, pool=15),
            follow_redirects=False,
            trust_env=False,
        )

    def request(self, provider, key, method, path, payload=None, connection=None):
        try:
            with self._client() as client:
                response = client.request(
                    method,
                    official_base(provider, connection) + path,
                    headers={
                        "Authorization": "Bearer " + key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                if response.status_code >= 300:
                    raise public_failure(response.status_code)
                if len(response.content) > 2 * 1024**2:
                    raise AIError("服务商响应过大。", 502)
                return response.json()
        except AIError:
            raise
        except Exception:
            raise AIError(
                "无法连接服务商，请检查网络；不会将密钥写入日志。", 502
            ) from None

    def models(self, provider, key, connection=None):
        if provider in ("doubao", "qwen"):
            return [dict(model) for model in CATALOGUES[provider]]
        result = self.request(provider, key, "GET", "/models", connection=connection)
        data = result.get("data", [])
        if not isinstance(data, list):
            raise AIError("服务商模型列表格式异常。", 502)
        models = []
        for value in data[:500]:
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                continue
            if provider == "kimi":
                known = next(
                    (dict(m) for m in KIMI_MODELS if m["id"] == value["id"]), None
                )
                if known:
                    for flag, source in (
                        ("vision", "supports_image_in"),
                        ("thinking", "supports_reasoning"),
                        ("tools", "supports_tool_calls"),
                    ):
                        if type(value.get(source)) is bool:
                            known[flag] = value[source]
                    known["thinking_required"] = (
                        known["thinking_required"] and known["thinking"]
                    )
                    known["catalogue"] = False
                    models.append(known)
                    continue
                # Discontinued versions are not offered, even if a stale list
                # includes them. Unknown future versions use explicit metadata.
                if value["id"].startswith("moonshot-v1") or value["id"] in (
                    "kimi-k2.5",
                    "kimi-latest",
                    "kimi-thinking-preview",
                    "kimi-k2-thinking",
                    "kimi-k2-thinking-turbo",
                    "kimi-k2-0905-preview",
                    "kimi-k2-0711-preview",
                    "kimi-k2-turbo-preview",
                ):
                    continue
            modes = value.get("input_modalities", [])
            model = {
                "id": value["id"],
                "name": value.get("name") or value["id"],
                "vision": "image" in modes or bool(value.get("supports_image_in")),
                "thinking": provider == "deepseek"
                or bool(value.get("supports_reasoning")),
                "tools": provider == "deepseek"
                or bool(value.get("supports_tool_calls")),
                "context_window": value.get(
                    "context_window", value.get("context_length")
                ),
                "catalogue": False,
            }
            if provider == "kimi" and value["id"].startswith(("kimi-k3", "kimi-k2.7")):
                model.update(thinking=True, thinking_required=True)
            models.append(model)
        if not models:
            raise AIError("此密钥没有返回可用模型。", 403)
        return models

    def verify(self, provider, key, model, connection=None):
        if provider == "kimi":
            result = self.request(
                provider, key, "GET", "/models", connection=connection
            )
            if not any(
                m.get("id") == model["id"]
                for m in result.get("data", [])
                if isinstance(m, dict)
            ):
                raise AIError("当前 Kimi 平台未返回此模型，请核对平台与模型 ID。")
        elif provider in CATALOGUES or model.get("custom"):
            self.request(
                provider,
                key,
                "POST",
                "/chat/completions",
                {
                    "model": model["id"],
                    "messages": [{"role": "user", "content": "回复OK"}],
                    "max_tokens": 1,
                    "stream": False,
                    **thinking_parameters(provider, model, False),
                },
                connection=connection,
            )

    def stream(self, provider, key, payload, stop, register_close, connection=None):
        """Normalize SSE JSON packets while preserving encrypted reasoning blocks."""
        started = time.monotonic()
        try:
            with self._client() as client:
                with client.stream(
                    "POST",
                    official_base(provider, connection) + "/chat/completions",
                    headers={
                        "Authorization": "Bearer " + key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as response:
                    if response.status_code >= 300:
                        raise public_failure(response.status_code)
                    register_close(response.close)
                    event_data = []
                    for line in response.iter_lines():
                        if stop.is_set():
                            return
                        if time.monotonic() - started > 600:
                            raise AIError(
                                "本轮响应时间较长，已停止等待，请缩小问题后重试。", 504
                            )
                        if len(line) > 1024**2:
                            raise AIError("服务商数据块过大。", 502)
                        if line.startswith("data:"):
                            event_data.append(line[5:].lstrip())
                        if line == "" and event_data:
                            raw = "\n".join(event_data)
                            event_data = []
                            if raw == "[DONE]":
                                return
                            try:
                                packet = json.loads(raw)
                            except ValueError:
                                raise AIError("服务商流式响应格式异常。", 502) from None
                            if "error" in packet:
                                raise AIError(
                                    "服务商返回了流式错误，请检查模型与配额。", 502
                                )
                            yield packet
                    if event_data and "\n".join(event_data) != "[DONE]":
                        yield json.loads("\n".join(event_data))
        except AIError:
            raise
        except Exception:
            if not stop.is_set():
                raise AIError("模型连接中断，可保留当前内容后重试。", 502) from None
        finally:
            register_close(None)
