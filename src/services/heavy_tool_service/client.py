"""轻容器侧代理客户端：同步长调用重工具服务（设计 §4.1/§8）。

重试口径（范围强制限定，§4.1）：
- 仅「连接建立阶段失败」（DNS 解析失败 / connect 拒绝 / connect 超时）重试一次——
  多副本 DNS round-robin 无健康感知，重解析即落到健康副本；
- 已送达请求的读超时**严禁重试**：modify 等原地写类操作双执行会损坏用户文件。

busy 语义（§8）：信号量满时重容器返回结构化 busy，客户端透传（error_code=busy、
message 带「稍后重试」引导），不做自动立即重试（防重试烧 LLM 轮次积分）。
"""

from typing import Any, Dict, Optional

import httpx
from loguru import logger

from . import config


class HeavyToolBusy(Exception):
    """重工具服务繁忙（error_code=busy）：由调用方转成中性失败 + 稍后重试引导。"""

    def __init__(self, message: str = "重工具服务繁忙，请稍后重试"):
        super().__init__(message)
        self.message = message


class HeavyToolUnavailable(Exception):
    """重容器不可用（连接失败重试后仍失败 / 非 busy 的 5xx）：按中性失败处理。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def serialize_context(context) -> Dict[str, Any]:
    """ToolExecutionContext 可序列化子集（设计 §4.1/§4.2）。

    两个字段不进 payload：llm_gateway（远程段零 LLM 消费）与
    knowledge_search_inventory（B2 前核对，预期不需要）。
    """
    if context is None:
        return {}
    return {
        "tenant_id": context.tenant_id,
        "user_id": context.user_id,
        "session_id": context.session_id,
        "channel": context.channel,
        "subagent_id": context.subagent_id,
        "chat_record_id": context.chat_record_id,
        "agent_execution_id": context.agent_execution_id,
        "tool_call_id": context.tool_call_id,
        "profile_id": context.profile_id,
        "instance_id": context.instance_id,
        "skill_workdir": context.skill_workdir,
    }


async def execute(
    *,
    tool: str,
    handler: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    context: Any = None,
    timeout_seconds: int = config.DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """调用重工具服务执行一个 handler；返回工具 execute 原始返回（透传）。

    抛 HeavyToolBusy / HeavyToolUnavailable 由调用方按中性失败口径处理；
    重容器业务级失败（success=false，如参数非法、文件缺失）作为普通 dict 返回，
    与本地执行错误路径同构（§10 验收 1）。
    """
    from src.tools.context import current_tool_execution_context

    payload = {
        "tool": tool,
        "handler": handler,
        "params": params or {},
        "context": serialize_context(context or current_tool_execution_context()),
        "timeout_seconds": timeout_seconds,
    }
    headers = {}
    token = config.service_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{config.heavy_tools_url()}/internal/heavy-tools/execute"
    # 读超时 = 任务超时 + 缓冲（排队/序列化余量）；连接超时单独收紧，
    # 连接失败才允许重试一次（§4.1）。
    timeout = httpx.Timeout(timeout_seconds + 30.0, connect=5.0)
    last_error: Optional[str] = None
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
            return _handle_response(response, tool, handler)
        except httpx.ConnectError as error:
            last_error = f"connect failed: {error}"
        except httpx.ConnectTimeout as error:
            last_error = f"connect timeout: {error}"
        except HeavyToolBusy:
            raise
        except httpx.ReadTimeout as error:
            # 已送达请求的读超时：严禁重试（防双执行），按不可用处理
            logger.warning("HeavyToolClient 读超时（不重试） tool={} handler={}: {}",
                           tool, handler, error)
            raise HeavyToolUnavailable("重工具服务响应超时，处理未完成") from error
        except httpx.HTTPError as error:
            last_error = f"http error: {type(error).__name__}: {error}"
    raise HeavyToolUnavailable(f"重工具服务不可用（{last_error}）")


def _handle_response(response: httpx.Response, tool: str, handler: Optional[str]) -> Dict[str, Any]:
    if response.status_code == 429:
        body = _safe_json(response)
        raise HeavyToolBusy(
            (body or {}).get("error") or "重工具服务繁忙，请稍后重试")
    if response.status_code == 401:
        raise HeavyToolUnavailable("重工具服务认证失败")
    if response.status_code >= 500:
        body = _safe_json(response) or {}
        raise HeavyToolUnavailable(
            body.get("error") or f"重工具服务错误（HTTP {response.status_code}）")
    if response.status_code == 400:
        body = _safe_json(response) or {}
        # 路径校验等请求级拒绝：透传为业务失败（success=false），LLM 可自纠
        return {"success": False, "error": body.get("error") or "请求被重工具服务拒绝"}
    try:
        body = response.json()
    except Exception as error:
        raise HeavyToolUnavailable(f"重工具服务响应异常: {error}") from error
    if not isinstance(body, dict) or "success" not in body:
        raise HeavyToolUnavailable("重工具服务响应格式异常")
    # 解包 result：返回工具 execute 原始返回（业务级失败与本地执行错误路径同构）
    result = body.get("result")
    if not isinstance(result, dict):
        return {"success": False,
                "error": body.get("error") or "重工具服务响应缺少 result"}
    return result


def _safe_json(response: httpx.Response) -> Optional[Dict[str, Any]]:
    try:
        body = response.json()
        return body if isinstance(body, dict) else None
    except Exception:
        return None


async def cancel(tool_call_id: str) -> bool:
    """尽力而为的协作取消（§4.3）；失败仅记日志，不影响调用方语义。"""
    token = config.service_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{config.heavy_tools_url()}/internal/heavy-tools/cancel",
                json={"tool_call_id": tool_call_id}, headers=headers)
        return response.status_code == 200
    except Exception as error:
        logger.debug("HeavyToolClient cancel 失败 tool_call_id={}: {}", tool_call_id, error)
        return False
