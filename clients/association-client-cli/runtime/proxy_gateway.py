"""
ProxyLLMGateway —— 客户端 CLI 的 LLM 网关适配器。

将 llm_gateway.chat() 调用代理到服务端 /api/client/v1/llm/chat，
服务端集中计费（×5 系数扣减租户余额）。

设计文档：docs/tools/association-client-design.md §3.3
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

import httpx


class NoCreditError(Exception):
    """余额不足，客户端必须停止任务。"""

    def __init__(self, message: str = "积分余额不足", balance_after: Optional[float] = None):
        super().__init__(message)
        self.balance_after = balance_after


class UnauthorizedError(Exception):
    """access_token 无效或绑定已禁用。"""


class ProxyLLMGateway:
    """
    LLM Gateway 适配器：接口与 src.llm.gateway.LLMGateway 一致（chat/get_model_name/get_provider_name），
    内部走 HTTP 调用服务端代理端点。

    用法：
        gateway = ProxyLLMGateway(server_url, access_token)
        providers = ProjectAssociationProviders(repository_root=ROOT, gateway=gateway)
    """

    def __init__(
        self,
        server_url: str,
        access_token: str,
        timeout: float = 120.0,
    ):
        self.server_url = server_url.rstrip("/")
        self.access_token = access_token
        self._timeout = timeout
        self._client = httpx.Client(timeout=timeout)
        # 最近一次 billing 信息（供进度上报读取）
        self.last_billing: Optional[dict[str, Any]] = None
        # 当前上下文（由 CLI 设置，用于 billing 事件补全 association/stage）
        self.current_association: str = ""
        self.current_stage: str = ""

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list] = None,
        tool_choice: Optional[Any] = None,
        temperature: float = 0,
        max_tokens: int = 4000,
        response_format: Optional[dict[str, Any]] = None,
        purpose: str = "unknown",
        **kwargs,
    ) -> dict[str, Any]:
        """
        与 LLMGateway.chat 签名兼容，返回标准化 response dict。

        Returns:
            {"content": str, "usage": {...}, "model": str, "provider": str}
        Raises:
            NoCreditError: 余额不足（402）
            UnauthorizedError: 令牌无效（401）
            httpx.HTTPStatusError: 其他 HTTP 错误
        """
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "purpose": purpose,
        }
        if response_format:
            payload["response_format"] = response_format

        try:
            resp = self._client.post(
                f"{self.server_url}/api/client/v1/llm/chat",
                json=payload,
                headers=self._headers(),
            )
        except httpx.ConnectError as exc:
            raise ConnectionError(f"无法连接服务端 {self.server_url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError(f"服务端请求超时（{self._timeout}s）: {exc}") from exc

        if resp.status_code == 402:
            data = {}
            try:
                data = resp.json()
            except Exception:
                pass
            raise NoCreditError(
                data.get("detail", "积分余额不足，请充值"),
                balance_after=None,
            )
        if resp.status_code == 401:
            raise UnauthorizedError("access_token 无效或客户端绑定已禁用")
        resp.raise_for_status()

        data = resp.json()

        # 记录 billing 信息（供进度上报读取，过程中不输出到 stdout）
        self.last_billing = data.get("billing")

        return {
            "content": data.get("content", ""),
            "usage": data.get("usage", {}),
            "model": data.get("model"),
            "provider": data.get("provider"),
        }

    def get_model_name(self) -> str:
        return "proxy"

    def get_provider_name(self) -> str:
        return "proxy"

    def close(self) -> None:
        self._client.close()


def emit_billing_event(
    billing: Optional[dict[str, Any]],
    association: str = "",
    stage: str = "",
) -> None:
    """将 billing 事件输出为 NDJSON 行（供 Electron 解析）。

    输出格式：
        {"event":"billing","association":"...","stage":"...","raw_credit_cost":0.4,"credit_cost":2.0,"balance_after":4498.0}
    """
    if not billing:
        return
    event = {
        "event": "billing",
        "association": association,
        "stage": stage,
        "raw_credit_cost": billing.get("raw_credit_cost", 0),
        "credit_cost": billing.get("credit_cost", 0),
        "balance_after": billing.get("balance_after"),
        "model": "",
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    sys.stdout.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()
