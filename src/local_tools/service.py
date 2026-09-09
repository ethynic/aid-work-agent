"""LocalInvocationService：本地操作通道统一入口（R16，【计划 §4】）

聊天代理（proxy_tool）与场景执行器（desktop_automation.executor）都经本服务
enqueue/get/cancel；后台不直接调用 _dispatch_and_wait() 私有轮询方法，不制造聊天
session 伪装执行。source_session_id 仅审计字段。

- enqueue 生成 request_id（v2 操作描述幂等分量）、写 dedupe_key；
  UNIQUE(tenant_id, business_kind, dedupe_key) 幂等：冲突返回已有 invocation。
- 同步 psycopg2 实现，FastAPI async 层用 asyncio.to_thread 包裹。
"""

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger

from src.local_tools import repository


class LocalInvocationService:
    """本地 invocation 统一服务（无状态，方法级事务）"""

    def enqueue(
        self,
        *,
        tenant_id: str,
        user_id: str,
        device_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        session_id: Optional[str] = None,
        provider_key: Optional[str] = None,
        business_kind: Optional[str] = None,
        business_ref: Optional[Dict[str, Any]] = None,
        dedupe_key: Optional[str] = None,
        deadline_at: Optional[datetime] = None,
        authorization_epoch: Optional[int] = None,
    ) -> Dict[str, Any]:
        """创建/复用 invocation（state=queued），返回 invocation 行（含 id）。

        dedupe_key 提供（business_kind 非空）时按 UNIQUE(tenant_id,business_kind,dedupe_key)
        幂等：冲突返回已有 invocation（同键重投不产生新行）。
        """
        if (dedupe_key is None) != (business_kind is None):
            raise ValueError("dedupe_key 与 business_kind 必须成对提供")
        # 前 6 参按既有位置签名传递（proxy 链路的会话归属断言依赖末位位置参数）
        invocation_id = repository.create_invocation(
            tenant_id, user_id, device_id, tool_name, arguments, session_id,
            provider_key=provider_key,
            business_kind=business_kind,
            business_ref=business_ref,
            dedupe_key=dedupe_key,
            deadline_at=deadline_at,
            authorization_epoch=authorization_epoch,
        )
        row = repository.get_invocation(invocation_id, tenant_id)
        if row is None:
            row = {"id": invocation_id, "state": "queued"}
        else:
            # create 返回的 id 是权威（get 行仅补全其余列；幂等冲突路径下二者一致）
            row = {**row, "id": invocation_id}
        logger.info(
            f"后端日志：LocalInvocationService enqueue tenant={tenant_id} "
            f"invocation={invocation_id} tool={tool_name} "
            f"business_kind={business_kind} dedupe_key={dedupe_key}"
        )
        return row

    def get(self, invocation_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """按 id+租户查询 invocation（新列一并返回）"""
        return repository.get_invocation(invocation_id, tenant_id)

    def cancel(self, invocation_id: str, tenant_id: str) -> bool:
        """请求取消（queued 直接终态 cancelled；claimed/running → cancel_requested）"""
        return repository.request_cancel(invocation_id, tenant_id)


def new_request_id() -> str:
    """v2 操作描述 request_id 生成（防重分量；触发键哈希编码复用同一值）"""
    return str(uuid.uuid4())
