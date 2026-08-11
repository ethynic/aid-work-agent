"""本地代理工具（LocalToolProxy）：云端 LLM 工具 → 用户本机 Runtime 执行

设计：docs/design/recruiting/recruiting-cli-agent-integration-design.md §4/§11/§14
实施规格：docs/plans/recruiting/m05-implementation-spec.md §3

7 个 boss_* 工具全部为 LOCAL_REQUIRED：execute() 不直接操作 BOSS，
而是经「设备闸门 → 创建 invocation → 轮询 events/state → 终态映射」
驱动本机 Runtime 执行，进度事件推入 agent 注入的 _progress_queue。

安全约束：
- 设备闸门在 create_invocation 之前（repository.create_invocation 本身不校验
  设备归属，属 M0.3 CR 遗留，本层必须先校验 selected+active+在线+provider）
- 授权上限由 Pydantic InputModel 硬校验（greet le=3 / accept le=1）
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

from loguru import logger
from pydantic import BaseModel, Field

from src.local_tools import catalog, repository
from src.tools.base import BaseTool, ExecutionTarget

ONLINE_THRESHOLD_SECONDS = 30  # last_seen_at 距今 ≤30s 视为在线（与 api.py 一致）
POLL_INTERVAL_SECONDS = 0.5
UNKNOWN_EFFECT_NOTICE = "实际效果未知，禁止重试，请提示用户人工检查"

# effect 附加提示（message 已含则不重复）
_TERMINAL_SUCCEEDED = "succeeded"


class LocalToolProxyTool(BaseTool):
    """LOCAL_REQUIRED 本地代理工具基类：云端创建 invocation，本机 Runtime 执行"""

    execution_target = ExecutionTarget.LOCAL_REQUIRED
    category = "local_boss"
    provider_key = "boss-recruiting"
    timeout_seconds = 180  # 默认 3 分钟；写动作（greet/accept）子类改为 10 分钟

    async def execute(self, **kwargs) -> Dict[str, Any]:
        tenant_id = kwargs.get("_trusted_tenant_id")
        user_id = kwargs.get("_trusted_user_id")
        progress_queue = kwargs.get("_progress_queue")

        if not tenant_id or not user_id:
            return {"success": False, "code": "NO_IDENTITY",
                    "message": "无法确定用户身份，请重新登录后再试"}

        # 参数级业务校验（如 boss_filter 至少一个条件）
        args = {k: v for k, v in kwargs.items() if not k.startswith("_")}
        args_error = self._validate_args(args)
        if args_error:
            return {"success": False, "code": "INVALID_ARGS", "message": args_error}

        # 1. 设备闸门（不通过则不创建 invocation）
        try:
            device, gate_error = await self._find_ready_device(tenant_id, user_id)
        except Exception as e:
            # 表未迁移（relation does not exist）等基础设施异常不应把 psycopg2 原文抛给用户
            logger.error(f"后端日志：本地工具设备闸门查询失败 tool={self.name}: {e}", exc_info=True)
            return {"success": False, "code": "DEVICE_UNAVAILABLE",
                    "message": "本地工具服务未就绪（云端未完成初始化），请联系管理员"}
        if device is None:
            return {"success": False, "code": "DEVICE_UNAVAILABLE", "message": gate_error}

        # 2. 创建 invocation
        invocation_id = await asyncio.to_thread(
            repository.create_invocation,
            tenant_id, user_id, str(device["id"]), self.name, args,
        )
        logger.info(
            f"后端日志：本地工具 invocation 已创建 id={invocation_id} "
            f"tool={self.name} device={device['id']} tenant={tenant_id}"
        )
        self._push_progress(progress_queue, {
            "type": "started",
            "invocation_id": invocation_id,
            "text": f"⏳ 已下发到本机执行：{self.display_name}",
        })

        # 3. 轮询 events + state 至终态/超时
        seq_cursor = 0
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self.timeout_seconds
        while True:
            events = await asyncio.to_thread(repository.list_events, invocation_id, seq_cursor)
            for event in events:
                seq_cursor = max(seq_cursor, event["seq"])
                self._push_progress(progress_queue, {
                    "type": "progress",
                    "invocation_id": invocation_id,
                    "text": self._format_event_text(event),
                })

            invocation = await asyncio.to_thread(
                repository.get_invocation, invocation_id, tenant_id
            )
            if invocation and invocation["state"] in repository.TERMINAL_STATES:
                return self._map_terminal(invocation)

            if loop.time() >= deadline:
                # TIMEOUT 是 proxy 本地码：云端状态机由 request_cancel 推进，不受影响
                await asyncio.to_thread(repository.request_cancel, invocation_id, tenant_id)
                logger.warning(
                    f"后端日志：本地工具 invocation 超时 id={invocation_id} tool={self.name}，已请求取消"
                )
                return {
                    "success": False,
                    "code": "TIMEOUT",
                    "message": f"等待本机执行超时（约 {self.timeout_seconds // 60} 分钟）。"
                               "请确认本机 Runtime 在线后重试",
                    "effect": None,
                    "data": None,
                    "invocation_id": invocation_id,
                }

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    # ==================== 设备闸门 ====================

    async def _find_ready_device(
        self, tenant_id: str, user_id: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """查 selected + active + 在线（≤30s）+ capabilities 含本工具 provider 的设备。

        返回 (device, None) 或 (None, 中文引导文案)。任一条件不满足都不允许创建 invocation。
        """
        devices = await asyncio.to_thread(repository.list_devices, tenant_id, user_id)
        if not devices:
            return None, "未找到已配对的本地设备。请点击左侧菜单栏底部的用户名，在弹出菜单中选择『本地工具』，在打开的页面生成配对码并启动本机 Runtime 完成绑定，然后再试"

        selected = [d for d in devices if d.get("selected") and d.get("status") == "active"]
        if not selected:
            return None, "尚未选定本地设备。请点击左侧菜单栏底部的用户名，在弹出菜单中选择『本地工具』，在页面中选定一台已配对的设备，然后再试"

        device = selected[0]
        last_seen = device.get("last_seen_at")
        online = bool(
            last_seen and (datetime.now() - last_seen).total_seconds() <= ONLINE_THRESHOLD_SECONDS
        )
        if not online:
            return None, "本机 Runtime 当前离线。请在本机启动 Runtime 并保持运行，然后再试"

        provider_key = catalog.get_provider_key_for_device(device.get("capabilities_json"))
        if not provider_key or not catalog.is_tool_allowed(provider_key, self.name):
            return None, "选定设备不支持 BOSS 招聘操作。请确认本机 Runtime 已启用 boss-recruiting 能力后再试"

        return device, None

    # ==================== 终态映射 ====================

    def _map_terminal(self, invocation: Dict[str, Any]) -> Dict[str, Any]:
        """云端终态 → 工具结果 {success, code, message, effect, data, invocation_id}"""
        state = invocation.get("state")
        result_json = invocation.get("result_json") or {}
        effect = invocation.get("effect")
        error_code = invocation.get("error_code") or result_json.get("code")
        error_message = invocation.get("error_message") or result_json.get("message")
        base = {
            "effect": effect,
            "data": result_json.get("data"),
            "invocation_id": str(invocation.get("id")),
        }

        if state == _TERMINAL_SUCCEEDED:
            result = {
                "success": True,
                "code": None,
                "message": result_json.get("message") or f"{self.display_name}执行成功",
                **base,
            }
        elif state == "cancelled" or error_code == "CANCELLED":
            result = {"success": False, "code": "CANCELLED",
                      "message": "操作已取消", **base}
        elif state == "unknown":
            # 租约过期/执行结果无法确认：必须 fail-loud，禁止重试
            msg = error_message or "本机执行结果无法确认"
            result = {"success": False, "code": "EXECUTION_UNKNOWN",
                      "message": f"{msg}；{UNKNOWN_EFFECT_NOTICE}", **base}
        elif state == "expired":
            result = {"success": False, "code": "EXPIRED",
                      "message": f"本机执行租约过期；{UNKNOWN_EFFECT_NOTICE}", **base}
        else:
            # failed：DESKTOP_NOT_INTERACTIVE / CHROME_UNAVAILABLE / NOT_LOGGED_IN /
            # WRONG_PAGE / PAYWALL / UI_CHANGED / BUSY 等中文文案直传（已是用户可读）
            result = {"success": False, "code": error_code or "FAILED",
                      "message": error_message or "本机执行失败", **base}

        if effect == "unknown" and UNKNOWN_EFFECT_NOTICE not in result["message"]:
            result["message"] = f"{result['message']}；{UNKNOWN_EFFECT_NOTICE}"
        return result

    # ==================== 进度 ====================

    @staticmethod
    def _push_progress(progress_queue: Optional[asyncio.Queue], event: Dict[str, Any]) -> None:
        if progress_queue is not None:
            progress_queue.put_nowait(event)

    def _format_event_text(self, event: Dict[str, Any]) -> str:
        message = (event.get("message") or "").strip()
        current, total = event.get("current"), event.get("total")
        if not message:
            message = f"{self.display_name}进行中"
        if current is not None and total:
            return f"⏳ {message} {current}/{total}"
        return f"⏳ {message}"

    # ==================== 子类钩子 ====================

    def _validate_args(self, args: Dict[str, Any]) -> Optional[str]:
        """参数级业务校验，返回中文错误文案；None 表示通过"""
        return None


# ==================== 7 个 BOSS 工具 ====================


class BossFilterInput(BaseModel):
    experience: Optional[str] = Field(None, description="经验要求，如「3-5年」")
    educations: Optional[List[str]] = Field(None, description="学历要求列表，如 [\"本科\", \"硕士\"]")
    salary: Optional[str] = Field(None, description="薪资范围，如「10-20K」")


class BossFilterTool(LocalToolProxyTool):
    name = "boss_filter"
    display_name = "BOSS 筛选牛人"
    description = "在用户本机 BOSS 直聘「推荐」页设置筛选条件（经验/学历/薪资，至少一项）。仅改变页面筛选，无外部副作用"
    InputModel = BossFilterInput

    def _validate_args(self, args: Dict[str, Any]) -> Optional[str]:
        if not any(args.get(k) for k in ("experience", "educations", "salary")):
            return "请至少提供一个筛选条件（经验、学历或薪资）"
        return None


class BossClearFilterTool(LocalToolProxyTool):
    name = "boss_clear_filter"
    display_name = "BOSS 清空筛选"
    description = "清空用户本机 BOSS 直聘「推荐」页的筛选条件。仅改变页面筛选，无外部副作用"

    class InputModel(BaseModel):
        pass


class BossGotoInput(BaseModel):
    target: Literal["recommend", "chat"] = Field(
        ..., description="目标页面：recommend=推荐牛人页，chat=沟通聊天页"
    )


class BossGotoTool(LocalToolProxyTool):
    name = "boss_goto"
    display_name = "BOSS 切换页面"
    description = "切换用户本机 BOSS 直聘页面（recommend 推荐页 / chat 聊天页）。跨页面操作前必须先切换，无外部副作用"
    InputModel = BossGotoInput


class BossGreetInput(BaseModel):
    limit: int = Field(1, ge=1, le=3, description="打招呼人数，单次最多 3 人（授权上限，不可突破）")


class BossGreetTool(LocalToolProxyTool):
    name = "boss_greet"
    display_name = "BOSS 打招呼"
    description = "在用户本机 BOSS 直聘「推荐」页向牛人发起打招呼。外部可见写动作，单次最多 3 人，需用户在对话中明确授权数量"
    InputModel = BossGreetInput
    timeout_seconds = 600


class BossAcceptResumeInput(BaseModel):
    limit: int = Field(1, ge=1, le=1, description="接收简历份数，单次固定 1 份（授权上限，不可突破）")
    preview: bool = Field(True, description="是否先预览简历再接收")


class BossAcceptResumeTool(LocalToolProxyTool):
    name = "boss_accept_resume"
    display_name = "BOSS 接收简历"
    description = "在用户本机 BOSS 直聘「沟通」页接收当前候选人简历。外部可见写动作，单次固定 1 份，需用户明确授权"
    InputModel = BossAcceptResumeInput
    timeout_seconds = 600


class BossRejectCurrentTool(LocalToolProxyTool):
    name = "boss_reject_current"
    display_name = "BOSS 标记不合适"
    description = "在用户本机 BOSS 直聘「沟通」页将当前候选人标记为不合适。外部可见写动作，每次固定当前 1 人，需用户明确授权"

    class InputModel(BaseModel):
        pass


class BossInterviewDemoInput(BaseModel):
    remark: Optional[str] = Field(None, max_length=140, description="面试备注（可选，最多 140 字）")


class BossInterviewDemoTool(LocalToolProxyTool):
    name = "boss_interview_demo"
    display_name = "BOSS 约面试演示"
    description = "在用户本机 BOSS 直聘「沟通」页演示填写约面试信息。只填写不发送，不属于外部写动作"
    InputModel = BossInterviewDemoInput


LOCAL_PROXY_TOOL_CLASSES = (
    BossFilterTool,
    BossClearFilterTool,
    BossGotoTool,
    BossGreetTool,
    BossAcceptResumeTool,
    BossRejectCurrentTool,
    BossInterviewDemoTool,
)

LOCAL_PROXY_TOOL_NAMES = frozenset(cls.name for cls in LOCAL_PROXY_TOOL_CLASSES)
