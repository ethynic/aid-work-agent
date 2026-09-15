"""Resolve a name through the selected Runtime; no WeChat account identity."""
import asyncio
from datetime import datetime
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.tools.base import BaseTool, ExecutionTarget
from src.tools.context import current_tool_execution_context


class NameResolveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_name: str = Field(min_length=1, max_length=128)

    @field_validator("target_name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("联系人名称无效")
        return value


class WeixinNameResolveTool(BaseTool):
    name = "weixin_name_resolve"
    display_name = "定位微信联系人"
    category = "weixin"
    execution_target = ExecutionTarget.LOCAL_REQUIRED
    description = "在已选Runtime当前登录的微信中按名称唯一精确定位联系人，不发送消息。无需微信号或账号绑定。成功返回device_id及resolution_invocation_id，供session_task_prepare创建自动聊天任务。"
    InputModel = NameResolveInput

    async def execute(self, **kwargs):
        from src.local_tools import repository, catalog
        from src.local_tools.service import LocalInvocationService
        context = current_tool_execution_context()
        if not context or not context.tenant_id or not context.user_id:
            return {"success": False, "code": "NO_IDENTITY"}
        try:
            args = NameResolveInput.model_validate(kwargs)
        except ValueError:
            return {"success": False, "code": "INVALID_ARGUMENT"}
        devices = await asyncio.to_thread(repository.list_devices, context.tenant_id, context.user_id)
        selected = [d for d in devices if d.get("selected") and d.get("status") == "active"]
        if len(selected) != 1:
            return {"success": False, "code": "DEVICE_UNAVAILABLE"}
        device = selected[0]
        seen = device.get("last_seen_at")
        now = datetime.now(seen.tzinfo) if seen and seen.tzinfo else datetime.now()
        if not seen or not 0 <= (now-seen).total_seconds() <= 30 or "weixin" not in catalog.get_provider_keys_for_device(device.get("capabilities_json")):
            return {"success": False, "code": "DEVICE_UNAVAILABLE"}
        service = LocalInvocationService()
        invocation = await asyncio.to_thread(service.enqueue, tenant_id=context.tenant_id, user_id=context.user_id,
            device_id=str(device["id"]), tool_name=self.name, arguments=args.model_dump(), provider_key="weixin")
        invocation_id = str(invocation["id"])
        deadline = monotonic() + 180
        while monotonic() < deadline:
            row = await asyncio.to_thread(service.get, invocation_id, context.tenant_id)
            if row and row["state"] == "succeeded":
                return {"success": True, "device_id": str(device["id"]), "resolution_invocation_id": invocation_id,
                        "data": (row.get("result_json") or {}).get("data")}
            if row and row["state"] in ("failed", "cancelled", "unknown"):
                return {"success": False, "code": row.get("error_code") or row["state"], "invocation_id": invocation_id}
            await asyncio.sleep(0.5)
        return {"success": False, "code": "RESOLUTION_PENDING", "invocation_id": invocation_id}
