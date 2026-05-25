"""
AI 电话外呼工具 — Mock 实现

对外呼 API 做 Mock 封装。工具接口先行设计，后端 API 由用户后续实现替换 Mock。

替换方法：修改 _execute_call() 方法，将 Mock 返回替换为真实 HTTP 调用。
"""

import uuid
from typing import Dict, Any, Optional

from pydantic import BaseModel, Field
from loguru import logger

from src.tools.base import BaseTool


class AICallInput(BaseModel):
    phone: str = Field(..., description="被叫号码（手机或固话）")
    lead_id: Optional[str] = Field(None, description="关联的线索 ID（用于记录跟进）")
    call_purpose: str = Field(
        ...,
        description="外呼目的: first_contact / followup / appointment_reminder / satisfaction_survey"
    )
    script_hint: Optional[str] = Field(None, description="给 AI 的话术提示")
    max_duration: Optional[int] = Field(180, description="最大通话时长（秒），默认 180")
    callback_url: Optional[str] = Field(None, description="通话结果回调 URL")


class AICallTool(BaseTool):
    name = "ai_call"
    description = "AI 电话外呼工具，自动拨打线索电话进行初步接触或跟进"
    display_name = "AI外呼"
    category = "phone"
    InputModel = AICallInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        phone = kwargs.get("phone", "")
        lead_id = kwargs.get("lead_id")
        call_purpose = kwargs.get("call_purpose", "")
        script_hint = kwargs.get("script_hint", "")
        max_duration = kwargs.get("max_duration", 180)
        callback_url = kwargs.get("callback_url")

        if not phone:
            return {"success": False, "error": "被叫号码不能为空"}

        valid_purposes = ["first_contact", "followup", "appointment_reminder", "satisfaction_survey"]
        if call_purpose and call_purpose not in valid_purposes:
            return {"success": False, "error": f"无效外呼目的 '{call_purpose}'，有效值: {valid_purposes}"}

        call_id = f"call_{uuid.uuid4().hex[:12]}"

        result = self._execute_call(
            phone=phone,
            call_id=call_id,
            call_purpose=call_purpose,
            script_hint=script_hint,
            max_duration=max_duration,
            callback_url=callback_url,
            lead_id=lead_id,
        )

        logger.info(f"AI外呼: call_id={call_id}, phone={phone}, purpose={call_purpose}, status={result.get('status')}")

        return result

    def _execute_call(
        self,
        phone: str,
        call_id: str,
        call_purpose: str,
        script_hint: str = "",
        max_duration: int = 180,
        callback_url: str = None,
        lead_id: str = None,
    ) -> Dict[str, Any]:
        """Mock 实现 — 用户后续替换为真实 HTTP 调用"""

        return {
            "success": True,
            "call_id": call_id,
            "status": "initiated",
            "phone": phone,
            "lead_id": lead_id,
            "call_purpose": call_purpose,
            "message": "Mock: 外呼已模拟发起，等待接听",
            "mock": True,
        }
