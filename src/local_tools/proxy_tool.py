"""本地代理工具（LocalToolProxy）：云端 LLM 工具 → 用户本机 Runtime 执行

设计：docs/design/recruiting/recruiting-cli-agent-integration-design.md §4/§11/§14
实施规格：docs/plans/recruiting/m05-implementation-spec.md §3

boss_* 代理工具为 LOCAL_REQUIRED：execute() 不直接操作 BOSS，
而是经「设备闸门 → 创建 invocation → 轮询 events/state → 终态映射」
驱动本机 Runtime 执行，进度事件推入 agent 注入的 _progress_queue。
例外（混合模式）：boss_jobs_list 覆写 execute 为纯云端逻辑（直查职位管理库，
不查设备、不建 invocation），仅为复用 SUBAGENT tools.allowed 按名称交集的注册机制
而留在 LOCAL_PROXY_TOOL_CLASSES（注册只看名称，execute 自决）；返回 jobs 数组外，
非空时附 data.options 编号选择元数据（key/label/description，设计 §5.1，供
LLM/前端直接渲染编号选择列表）。

其中 boss_resume_detail / boss_resume_batch 额外做云端后处理：CLI 成功结果（截图+OCR payload）
在工具层直接落简历库（batch 逐份落库），只把紧凑摘要返回给 LLM（图片字节不进上下文）。

计费（2026-09-01 时机迁移，docs/design/billing/client-billing-integration-design.md §4.1）：
按次计费已从本模块轮询侧迁到 repository.write_result（Runtime 回写结果权威落库点，
同事务计费，会话断开/轮询死亡不再漏计费）。本模块只负责：扣费前余额预检（NO_CREDIT
不建 invocation）、把 invocation.credit_cost 实扣金额附进 LLM 可见的 data、弹层自愈
专项费（无独立 invocation，仍走 record_tool_usage 直记）。

安全约束：
- 设备闸门在 create_invocation 之前（repository.create_invocation 本身不校验
  设备归属，属 M0.3 CR 遗留，本层必须先校验 selected+active+在线+provider）
- 授权上限由 Pydantic InputModel 硬校验（greet le=3 / accept le=1）
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

from loguru import logger
from pydantic import BaseModel, Field, field_validator

from src.config.settings import settings
from src.db.client_binding_db import ClientUsageLogDB
from src.local_tools import catalog, repository
from src.local_tools.pricing import overlay_heal_price, tool_credit_price
from src.services import (
    overlay_heal_service,
    recruiting_job_service,
    recruiting_match_service,
    recruiting_notify_service,
    recruiting_resume_service,
    recruiting_resume_timeline_service,
)
from src.tools.base import BaseTool, ExecutionTarget

ONLINE_THRESHOLD_SECONDS = 30  # last_seen_at 距今 ≤30s 视为在线（与 api.py 一致）
POLL_INTERVAL_SECONDS = 0.5
UNKNOWN_EFFECT_NOTICE = "实际效果未知，禁止重试，请提示用户人工检查"

# effect 附加提示（message 已含则不重复）
_TERMINAL_SUCCEEDED = "succeeded"

# 弹层自愈（overlay heal，2026-08-31）：触发错误码——弹层遮挡的典型症状。
# EXECUTION_UNKNOWN（写后结果不明）/ WRONG_PAGE（前置不满足）等绝不自愈重试
HEALABLE_ERROR_CODES = frozenset({"UI_CHANGED", "BUSY"})
# 自愈专项费在台账中的 tool_name（非真实工具；仅在自愈真正救回操作时收取）
OVERLAY_HEAL_TOOL_NAME = "boss_overlay_heal"


class LocalToolProxyTool(BaseTool):
    """LOCAL_REQUIRED 本地代理工具基类：云端创建 invocation，本机 Runtime 执行"""

    # 不进自动目录：Assembly 仅按子智能体 allowed 列表注册（子类继承此标记）
    catalog = False
    execution_target = ExecutionTarget.LOCAL_REQUIRED
    category = "local_boss"
    provider_key = "boss-recruiting"
    timeout_seconds = 180  # 默认 3 分钟；写动作（greet/accept）子类改为 10 分钟
    # 弹层自愈主体标记：False 的工具失败后不再触发自愈（overlay 原语自身防递归）
    heal_eligible = True

    async def execute(self, **kwargs) -> Dict[str, Any]:
        tenant_id = kwargs.get("_trusted_tenant_id")
        user_id = kwargs.get("_trusted_user_id")
        session_id = kwargs.get("_session_id")
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
            logger.opt(exception=True).error(f"后端日志：本地工具设备闸门查询失败 tool={self.name}: {e}")
            return {"success": False, "code": "DEVICE_UNAVAILABLE",
                    "message": "本地工具服务未就绪（云端未完成初始化），请联系管理员"}
        if device is None:
            return {"success": False, "code": "DEVICE_UNAVAILABLE", "message": gate_error}

        # 2. 计费预检（仅收费工具：SaaS 模式下余额 ≤0 阻断，不建 invocation、设备不出工）
        credit_price = self._tool_credit_price()
        if credit_price > 0:
            blocked_message = await self._tenant_credit_blocked(tenant_id)
            if blocked_message:
                return {"success": False, "code": "NO_CREDIT", "message": blocked_message,
                        "effect": None, "data": None, "invocation_id": None}

        # 3. 下发 + 轮询终态
        result = await self._dispatch_and_wait(tenant_id, user_id, session_id, device, args, progress_queue)

        # 4. 弹层自愈：失败且为可自愈码（UI_CHANGED/BUSY，弹层遮挡的典型症状）→ 关闭弹层后重试一次
        if not result.get("success") and result.get("code") in HEALABLE_ERROR_CODES and self.heal_eligible:
            result = await self._heal_overlay(tenant_id, user_id, session_id, device, args, progress_queue, result)
        return result

    async def _dispatch_and_wait(
        self,
        tenant_id: str,
        user_id: str,
        session_id: Optional[str],
        device: Dict[str, Any],
        args: Dict[str, Any],
        progress_queue: Optional[asyncio.Queue],
    ) -> Dict[str, Any]:
        """创建 invocation + 轮询 events/state 至终态/超时：终态映射（计费在 write_result 落库侧）"""
        invocation_id = await asyncio.to_thread(
            repository.create_invocation,
            tenant_id, user_id, str(device["id"]), self.name, args, session_id,
        )
        logger.info(
            f"后端日志：本地工具 invocation 已创建 id={invocation_id} "
            f"tool={self.name} device={device['id']} tenant={tenant_id} session={session_id}"
        )
        self._push_progress(progress_queue, {
            "type": "started",
            "invocation_id": invocation_id,
            "text": f"⏳ 已下发到本机执行：{self.display_name}",
        })

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
                result = self._map_terminal(invocation)
                return self._attach_credit_cost(invocation, result)

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

    # ==================== 计费（落库侧 write_result 同事务计费，本模块只预检/附带金额） ====================

    def _tool_credit_price(self) -> float:
        """当前工具单次积分价格（取价统一走 pricing 模块，与 write_result 落库计费同源）"""
        return tool_credit_price(self.name)

    async def _tenant_credit_blocked(self, tenant_id: str) -> Optional[str]:
        """扣费前余额预检（语义同 main._check_tenant_credit_blocked）：返回阻断文案或 None。

        租户不存在：不阻断；余额 ≤0：阻断（invocation 不创建，设备不出工）；
        检查异常：不阻断避免误伤（与对话入口同一容错取向）。
        """
        try:
            from src.saas.db.tenant_db import TenantDB
            tenant = await asyncio.to_thread(TenantDB.get_by_id, tenant_id)
            if not tenant:
                return None
            if float(tenant.get("credit_balance") or 0) <= 0:
                logger.warning(
                    f"后端日志：租户 {tenant_id} 积分余额耗尽，阻断本地工具调用 tool={self.name}"
                )
                return "积分余额已耗尽，无法执行该操作，请联系管理员充值后再试"
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：本地工具计费预检异常 tool={self.name}: {e}")
            return None
        return None

    @staticmethod
    def _attach_credit_cost(invocation: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        """把 write_result 落库侧同事务计费的实扣金额附进 LLM 可见的 data。

        仅成功结果且实扣金额存在（>0）时附加；credit_cost 为 NULL（计费降级/历史行）
        或 0（免费工具）时保持 data 原形，不给上下文添噪音。
        """
        credit_cost = invocation.get("credit_cost")
        if not result.get("success") or not credit_cost:
            return result
        data = result.get("data")
        if isinstance(data, dict):
            return {**result, "data": {**data, "credit_cost": float(credit_cost)}}
        return result

    # ==================== 弹层自愈（overlay heal，2026-08-31） ====================

    async def _heal_overlay(
        self,
        tenant_id: str,
        user_id: str,
        session_id: Optional[str],
        device: Dict[str, Any],
        args: Dict[str, Any],
        progress_queue: Optional[asyncio.Queue],
        original_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """失败后的弹层自愈：导出候选 → 启发式/LLM 选关闭控件 → 关闭 → 重试原操作一次。

        设计（docs/design/recruiting/boss-overlay-heal-design.md）：
        - 仅 UI_CHANGED/BUSY 触发（execute 已保证）；EXECUTION_UNKNOWN 副作用不明绝不重试
        - 自愈任何环节失败都返回原结果（data.heal 附加说明），绝不掩盖原始错误
        - 关闭控件双重白名单校验（本服务 + CLI 端），LLM 只能挑关闭语义控件，绝不误点领取/开通
        - 重试成功才收自愈专项费 overlay_heal_price（LLM 成本），原工具费按重试结果正常计
        """
        cfg = settings.boss_tool_billing
        if not cfg.overlay_heal_enabled:
            return original_result
        logger.warning(
            f"后端日志：本地工具失败且疑似弹层遮挡，触发弹层自愈 tool={self.name} "
            f"code={original_result.get('code')}"
        )
        self._push_progress(progress_queue, {
            "type": "progress",
            "text": "检测到页面异常（疑似弹层遮挡），正在尝试智能识别关闭…",
        })
        try:
            inspect_result = await BossOverlayInspectTool().execute(
                _trusted_tenant_id=tenant_id, _trusted_user_id=user_id, _progress_queue=progress_queue)
            inspect_data = (inspect_result.get("data") or {}) if inspect_result.get("success") else {}
            candidates = inspect_data.get("candidates")
            icon_candidates = inspect_data.get("icon_candidates")
            if not candidates and not icon_candidates:
                logger.info(f"后端日志：弹层自愈放弃（候选清单为空/导出失败）tool={self.name}")
                return self._with_heal_info(original_result, dismissed_text=None, llm_used=False)

            dismiss_text = overlay_heal_service.pick_heuristic(candidates or [], icon_candidates)
            llm_used = False
            if not dismiss_text:
                llm_used = True
                dismiss_text = await overlay_heal_service.pick_dismiss_text_with_llm(
                    tenant_id, user_id, candidates or [], icon_candidates)
            if not dismiss_text:
                self._push_progress(progress_queue, {
                    "type": "progress",
                    "text": "未识别到可安全关闭的弹层，请人工查看页面",
                })
                return self._with_heal_info(original_result, dismissed_text=None, llm_used=llm_used)

            dismiss_result = await BossOverlayDismissTool().execute(
                _trusted_tenant_id=tenant_id, _trusted_user_id=user_id,
                _progress_queue=progress_queue, text=dismiss_text)
            if not dismiss_result.get("success"):
                self._push_progress(progress_queue, {
                    "type": "progress",
                    "text": f"弹层「{dismiss_text}」关闭失败，请人工查看页面",
                })
                return self._with_heal_info(original_result, dismissed_text=dismiss_text,
                                            llm_used=llm_used, dismissed=False)

            self._push_progress(progress_queue, {
                "type": "progress",
                "text": f"已关闭弹层「{dismiss_text}」，正在重试：{self.display_name}",
            })
            retry_result = await self._dispatch_and_wait(
                tenant_id, user_id, session_id, device, args, progress_queue)
            healed = bool(retry_result.get("success"))
            if healed:
                heal_price = self._heal_price()
                if heal_price > 0:
                    try:
                        await asyncio.to_thread(
                            ClientUsageLogDB.record_tool_usage,
                            tenant_id=tenant_id,
                            tool_name=OVERLAY_HEAL_TOOL_NAME,
                            credit_cost=heal_price,
                            device_id=str(device["id"]),
                            invocation_id=retry_result.get("invocation_id"),
                            session_id=session_id,
                            user_id=user_id,
                        )
                    except Exception as e:  # noqa: BLE001 计费失败不影响自愈结果
                        logger.opt(exception=True).error(
                            f"后端日志：弹层自愈计费落账失败 tool={self.name}: {e}")
            else:
                self._push_progress(progress_queue, {
                    "type": "progress",
                    "text": "弹层已关闭但重试仍失败，请人工查看页面",
                })
            return self._with_heal_info(retry_result, dismissed_text=dismiss_text,
                                        llm_used=llm_used, dismissed=True, healed=healed)
        except Exception as e:  # noqa: BLE001 自愈异常绝不吞掉原错误
            logger.opt(exception=True).error(
                f"后端日志：弹层自愈异常（返回原错误）tool={self.name}: {e}")
            return self._with_heal_info(original_result, dismissed_text=None, llm_used=False)

    @staticmethod
    def _with_heal_info(
        result: Dict[str, Any],
        dismissed_text: Optional[str],
        llm_used: bool,
        dismissed: Optional[bool] = None,
        healed: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """把自愈过程信息附进 data.heal（data 为 None/dict 时合并；其余形状保持原样）"""
        info: Dict[str, Any] = {"attempted": True, "dismissed_text": dismissed_text, "llm_used": llm_used}
        if dismissed is not None:
            info["dismissed"] = dismissed
        if healed is not None:
            info["healed"] = healed
        data = result.get("data")
        if data is None or isinstance(data, dict):
            return {**result, "data": {**(data or {}), "heal": info}}
        return result

    def _heal_price(self) -> float:
        return overlay_heal_price()

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


# ==================== 8 个 BOSS 工具 ====================


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


class BossFilterOptionsTool(LocalToolProxyTool):
    name = "boss_filter_options"
    display_name = "BOSS 查询筛选可选档位"
    description = (
        "在用户本机 BOSS 直聘「推荐牛人」页只读探查筛选面板的全部可选档位"
        "（经验/学历/薪资各行选项），读完自动收起面板。用于把用户口语化筛选要求"
        "（如 15k-20k、5年以上、本科及以上）映射成页面实际存在的精确档位后再调 boss_filter"
    )

    class InputModel(BaseModel):
        pass


class BossGotoTool(LocalToolProxyTool):
    name = "boss_goto"
    display_name = "BOSS 切换页面"
    description = "切换用户本机 BOSS 直聘页面（recommend 推荐页 / chat 聊天页）。跨页面操作前必须先切换，无外部副作用"
    InputModel = BossGotoInput


class BossGreetInput(BaseModel):
    limit: int = Field(1, ge=1, le=3, description="打招呼人数，单次最多 3 人（授权上限，不可突破）")
    names: Optional[List[str]] = Field(
        None,
        min_length=1,
        max_length=3,
        description=(
            "定向打招呼：matched 候选人姓名清单（1-3 人）。传入后 CLI 先配对卡片姓名再点击，"
            "只向姓名精确匹配的候选人打招呼，配对失败的卡片一律跳过（宁可不打，不能打错）；"
            "结果按实际打过的人返回（greeted_names / missing_names），汇报必须以此为准"
        ),
    )

    @field_validator("names")
    @classmethod
    def _normalize_names(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """定向名单规整：strip 非空校验 + 去重（保持顺序）后透传 CLI，云端不解析姓名语义"""
        if v is None:
            return v
        stripped = [name.strip() for name in v]
        if any(not name for name in stripped):
            raise ValueError("names 中每个姓名都必须是非空字符串")
        deduped = list(dict.fromkeys(stripped))
        if not deduped:
            raise ValueError("names 去重后为空")
        return deduped


class BossGreetTool(LocalToolProxyTool):
    name = "boss_greet"
    display_name = "BOSS 打招呼"
    description = (
        "在用户本机 BOSS 直聘「推荐」页向牛人发起打招呼。外部可见写动作，单次最多 3 人，"
        "需用户在对话中明确授权数量。定向模式：传 names 候选人姓名清单时先匹配卡片姓名再点击，"
        "只向名单内的人打招呼（配对失败的卡片跳过），打给谁以返回的 greeted_names 为准，"
        "missing_names 是滚到底也没找到的人"
    )
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


# ============== 面试邀约企微通知（两点式，Phase 1，设计 recruiting-interview-notify §2） ==============


class BossInterviewNotifyCandidate(BaseModel):
    """候选人条目：pre 用 name/score/highlight/time（拟时间），done 用 name/time（实际时间）"""

    name: str = Field(..., min_length=1, max_length=30, description="候选人姓名")
    score: Optional[int] = Field(None, ge=0, le=100, description="匹配分 0-100（pre 模板展示）")
    highlight: Optional[str] = Field(None, max_length=100, description="候选人亮点一句话（pre 模板展示）")
    time: Optional[str] = Field(None, max_length=50, description="面试时间（pre=拟安排 / done=实际安排）")


class BossInterviewNotifyInput(BaseModel):
    kind: Literal["pre", "done"] = Field(
        ..., description="通知类型：pre=邀约前知会（拟邀名单+分数亮点+拟时间）/ done=邀约后通报（实际名单+面试时间）"
    )
    job_name: str = Field(..., min_length=1, max_length=50, description="职位名称")
    candidates: List[BossInterviewNotifyCandidate] = Field(
        ..., min_length=1, max_length=10, description="候选人名单（1-10 人，姓名与时间来自对话上下文）"
    )
    note: Optional[str] = Field(None, max_length=200, description="备注（pre 模板附加说明，可选）")


class BossInterviewNotifyTool(LocalToolProxyTool):
    """面试邀约企微通知（混合模式：纯云端发送，不查设备、不建 invocation）。

    同 boss_jobs_list 的混合模式：覆写 execute 为纯云端逻辑（读通知配置 → 调
    recruiting_notify_service.push_interview_notify 发企微群机器人 → 写留痕），
    仅为复用 SUBAGENT tools.allowed 名称交集注册机制而留在 LOCAL_PROXY_TOOL_CLASSES。

    设计调整（2026-08-19）：boss_interview_demo 入参只有 remark，无候选人名/日期，
    候选人名只存在于 agent 对话上下文——因此事前知会与事后通报统一为本工具的
    两种 kind（pre/done），由 SUBAGENT 链路规定调用；通知失败不阻塞邀约。
    """

    name = "boss_interview_notify"
    display_name = "BOSS 面试邀约企微通知"
    description = (
        "向企微群机器人发送面试邀约通知（只发群知会，不操作 BOSS、不碰设备）。"
        "kind=pre 事前知会：用户同意邀面后、执行邀约前调用，传拟邀候选人名单"
        "（name/score/highlight）与拟安排时间（time）；kind=done 事后通报："
        "boss_interview_demo 逐人完成后调用，传实际邀约名单与面试时间。"
        "通知失败不阻塞邀约（工具仍返回 success，message 会说明失败原因，转告用户后继续）"
    )
    # 纯云端一次 HTTP 推送，留短 timeout；不走基类设备转发路
    timeout_seconds = 30

    InputModel = BossInterviewNotifyInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        tenant_id = kwargs.get("_trusted_tenant_id")
        if not tenant_id:
            return {"success": False, "code": "NO_IDENTITY",
                    "message": "无法确定用户身份，请重新登录后再试"}
        kind = kwargs.get("kind")
        job_name = kwargs.get("job_name") or ""
        # ToolExecutor 会按 InputModel 规范化参数（executor.py 强转），嵌套模型
        # candidates 到达这里是 BossInterviewNotifyCandidate 实例而非 dict——
        # 服务层按 dict 取值（c.get），此处统一 model_dump 为 dict（exclude_none
        # 与服务层 _normalize_candidates 只留非 None 键的语义一致）
        candidates = [
            c.model_dump(exclude_none=True) if isinstance(c, BaseModel) else c
            for c in (kwargs.get("candidates") or [])
        ]
        # 单候选人推送 → 按姓名解析简历 id 关联留痕（简历详情页「企微通知留痕」联动，2026-09-01）。
        # 多候选人不解析（留痕列语义：单候选人才关联）；解析失败/无简历 → None，绝不阻塞推送
        resume_id: Optional[int] = None
        if len(candidates) == 1:
            name = str((candidates[0] or {}).get("name") or "").strip()
            if name:
                try:
                    resume_id = await asyncio.to_thread(_find_resume_id_by_name, tenant_id, name)
                except Exception as e:  # noqa: BLE001 简历解析失败不影响通知推送
                    logger.opt(exception=True).error(
                        f"后端日志：boss_interview_notify 解析候选人简历失败（不阻塞推送）: {e}"
                    )
                    resume_id = None
        try:
            result = await recruiting_notify_service.push_interview_notify(
                tenant_id,
                kind=kind,
                job_name=job_name,
                candidates=candidates,
                note=kwargs.get("note"),
                resume_id=resume_id,
            )
        except Exception as e:  # noqa: BLE001 通知失败不阻塞邀约：转用户可读文案
            logger.opt(exception=True).error(f"后端日志：boss_interview_notify 推送异常: {e}")
            result = {"pushed": False, "error": f"通知服务异常: {type(e).__name__}"}

        pushed = bool(result.get("pushed"))
        if pushed:
            message = "事前知会已发送至企微群" if kind == "pre" else "面试邀约通报已发送至企微群"
        elif result.get("reason") == "未启用":
            message = "企微通知未启用（可在通知设置开启），本次未发送群通知"
        else:
            message = f"通知发送失败（不影响邀约，可继续）：{result.get('error') or result.get('reason') or '未知原因'}"

        data = {"pushed": pushed, "log_id": result.get("log_id")}
        return {"success": True, "code": None, "message": message, "data": data}


# ============== 职位切换与云端职位库（要求驱动闭环 Phase 3，设计 §5） ==============


class BossListJobsTool(LocalToolProxyTool):
    name = "boss_list_jobs"
    display_name = "BOSS 列出页面职位"
    description = (
        "在用户本机 BOSS 直聘「推荐牛人」页点开职位下拉，列出当前招聘者在 BOSS 页面上的全部职位"
        "（职位名/城市/薪资/是否待开放，只读，读完自动收起）。用于 boss_select_job 前确认页面职位的"
        "精确名（用户口述可能不精确）并避开待开放职位。与 boss_jobs_list（查云端「职位管理」职位库）"
        "区分：本工具查的是 BOSS 页面上实际发布的职位"
    )
    timeout_seconds = 180

    class InputModel(BaseModel):
        pass


class BossSelectJobInput(BaseModel):
    job_name: str = Field(
        ..., min_length=1, max_length=100,
        description="目标职位名（精确名，建议先用 boss_list_jobs 确认）",
    )


class BossSelectJobTool(LocalToolProxyTool):
    name = "boss_select_job"
    display_name = "BOSS 切换招聘职位"
    description = (
        "在用户本机 BOSS 直聘「推荐牛人」页把当前招聘职位切换为指定职位名（页面写动作，无对外消息副作用）。"
        "job_name 必须是 boss_list_jobs 返回的精确职位名（不做模糊匹配，0 个或多个匹配都报错）；"
        "待开放（pending）职位会被拒绝（切到未发布职位会致页面异常）；切换后校验职位框已变更，未生效报错"
    )
    InputModel = BossSelectJobInput
    timeout_seconds = 300


def _job_option_description(job: Dict[str, Any], resume_count: int, matched_count: int) -> str:
    """拼 boss_jobs_list options.description（设计 §5.1 选择交互）。

    要求三维度：experience / educations（顿号连接）/ salary 按序以「/」连接，
    三维度全缺（含 job_requirements 为 null）时写「要求未配置」；
    再接「 · 简历 N · 匹配 M」统计。keywords/notes 不进 description（不参与筛选）。
    """
    reqs = job.get("job_requirements") or {}
    dims = []
    experience = (reqs.get("experience") or "").strip()
    if experience:
        dims.append(experience)
    educations = [e.strip() for e in (reqs.get("educations") or []) if (e or "").strip()]
    if educations:
        dims.append("、".join(educations))
    salary = (reqs.get("salary") or "").strip()
    if salary:
        dims.append(salary)
    req_part = f"要求 {'/'.join(dims)}" if dims else "要求未配置"
    return f"{req_part} · 简历 {resume_count} · 匹配 {matched_count}"


class BossJobsListTool(LocalToolProxyTool):
    """云端查询「职位管理」职位库（混合模式，设计 §5.1 数据层）。

    覆写 execute 为纯云端逻辑：不查设备、不建 invocation、不轮询（与 BossSendToTool
    话术模式的云端分支同思路）。仍注册在 LOCAL_PROXY_TOOL_CLASSES——消费方
    src/core/agent.py 按 SUBAGENT tools.allowed 名称交集注册，注册只看名称，
    execute 自决是否走本机转发，两种模式互不干扰。

    Phase 4（设计 §5.1 选择交互）：非空 active 时 data 附 options 数组（与 jobs 同序，
    key=job_id / label=job_name / description=要求三维度+简历/匹配统计），供 LLM/前端
    直接渲染编号选择列表；空 active 不带 options 键。
    """

    name = "boss_jobs_list"
    display_name = "BOSS 查询职位库"
    description = (
        "查询云端「职位管理」里的在招职位（status=active，暂停职位不返回）：每个职位返回 "
        "job_id/job_name/match_threshold/job_requirements（经验/学历/薪资三档位，可直接作为 "
        "boss_filter 入参）/resume_count/matched_count（该职位简历数与匹配数），"
        "并附与 jobs 同序的 data.options（key=job_id/label=job_name/description=要求与统计摘要）"
        "——data.options 可直接用于向用户渲染编号选择列表。"
        "「筛选简历」入口先用它确认职位与要求；查 BOSS 页面职位请用 boss_list_jobs，两者区分"
    )
    # 云端直查不轮询 invocation，timeout 仅对基类转发路有意义；留短值防误走转发
    timeout_seconds = 30

    class InputModel(BaseModel):
        pass

    async def execute(self, **kwargs) -> Dict[str, Any]:
        tenant_id = kwargs.get("_trusted_tenant_id")
        if not tenant_id:
            return {"success": False, "code": "NO_IDENTITY",
                    "message": "无法确定用户身份，请重新登录后再试"}
        try:
            # list_jobs 已附 resume_count/matched_count（内部调共享 count_job_resumes），
            # 无需再单独查一次统计（Phase 5 下沉共享后消除重复查询）
            jobs = await asyncio.to_thread(recruiting_job_service.list_jobs, tenant_id)
        except Exception as e:  # noqa: BLE001 基础设施异常转用户可读文案，不把 psycopg2 原文抛给 LLM
            logger.opt(exception=True).error(f"后端日志：boss_jobs_list 查询职位库失败: {e}")
            return {"success": False, "code": "FAILED",
                    "message": "职位库查询失败，请稍后重试或联系管理员"}

        items = [
            {
                "job_id": job["id"],
                "job_name": job["job_name"],
                "status": job["status"],
                "match_threshold": job.get("match_threshold"),
                "job_requirements": job.get("job_requirements"),
                "resume_count": job.get("resume_count", 0),
                "matched_count": job.get("matched_count", 0),
            }
            for job in jobs
            if job.get("status") == "active"
        ]
        if not items:
            return {
                "success": True,
                "code": None,
                "message": "职位管理里还没有在招（active）职位。请先到「职位管理」页面创建职位并维护职位要求与话术",
                "data": {"jobs": []},
            }
        names = "、".join(j["job_name"] for j in items)
        # 编号选择元数据（设计 §5.1）：与 jobs 同序，LLM 按序号渲染「1. label · description」
        options = [
            {
                "key": item["job_id"],
                "label": item["job_name"],
                "description": _job_option_description(
                    item, item["resume_count"], item["matched_count"]
                ),
            }
            for item in items
        ]
        return {
            "success": True,
            "code": None,
            "message": f"当前在招职位 {len(items)} 个：{names}",
            "data": {"jobs": items, "options": options},
        }


# ============== 简历入库后自动评分（简历-职位匹配设计 §3，Phase 2） ==============


async def _evaluate_resume_match_safely(tenant_id: str, resume_id: int) -> Dict[str, Any]:
    """调评分服务并吞掉一切异常（评分绝不影响工具成功返回，失败不阻塞设计 §3）。

    服务层本身不抛异常，此处兜底防御（如 DB 读简历阶段意外错误），返回失败说明 dict。
    """
    try:
        return await recruiting_match_service.evaluate_and_update(tenant_id, resume_id)
    except Exception as e:  # noqa: BLE001 评分是增强信息，任何异常都不拖垮入库结果
        logger.opt(exception=True).error(f"后端日志：简历评分异常 resume_id={resume_id}: {e}")
        return {"resume_id": resume_id, "score": None, "note": f"评分异常: {e}"}


def _apply_match_fields(summary: Dict[str, Any], match_result: Dict[str, Any]) -> None:
    """把评分结果并入紧凑摘要：增加 match_score / match_status / match_summary 三键。

    评分失败（score None，含未关联职位跳过）时 match_score=null 且附 match_note「未评分」。
    """
    summary["match_score"] = match_result.get("match_score")
    summary["match_status"] = match_result.get("match_status")
    summary["match_summary"] = match_result.get("match_summary")
    if summary["match_score"] is None:
        summary["match_note"] = "未评分"


class BossResumeDetailTool(LocalToolProxyTool):
    """BOSS 读取简历入库：CLI 截图+OCR 结果在云端工具层直接落库，图片字节绝不进 LLM 上下文。

    注意：工具实例是共享单例（tool_registry.register(tool_cls())），
    禁止把每次调用的状态存 self；tenant/user 一律从 kwargs 的 _trusted_* 取。
    """
    name = "boss_resume_detail"
    display_name = "BOSS 读取简历入库"
    description = (
        "在用户本机 BOSS 直聘「沟通」页读取当前候选人简历详情（截图+OCR），"
        "结果自动存入简历库，返回紧凑摘要（不含图片与 OCR 全文）"
    )

    class InputModel(BaseModel):
        candidate_name: Optional[str] = Field(
            None,
            max_length=30,
            description=(
                "必传：当前会话候选人的姓名（姓名唯一来源=非 OCR，"
                "CLI 会与简历 OCR 文本交叉校验，不符会报错）"
            ),
        )

    timeout_seconds = 600

    async def execute(self, **kwargs) -> Dict[str, Any]:
        tenant_id = kwargs.get("_trusted_tenant_id")
        user_id = kwargs.get("_trusted_user_id")
        result = await super().execute(**kwargs)

        # CLI 失败/非 success：message/code 透传（message 已带失败原因），不落库。
        # data 一律置 None：CLI 失败结果 data 形状未约定（可能夹带部分截图 base64），
        # 「图片字节绝不进 LLM 上下文」的保证必须覆盖所有返回路径
        if not result.get("success"):
            return {**result, "data": None}

        # 成功：结果 payload（invocation result_json.data）直接落库（同步 DB 调用放线程池）
        payload = result.get("data") or {}
        try:
            record = await asyncio.to_thread(
                recruiting_resume_service.create_resume_record_from_tool_result,
                tenant_id, user_id, payload, "boss",
            )
        except recruiting_resume_service.ResumePayloadError as e:
            # fail-loud：payload 不符契约，不落任何库/盘数据
            logger.error(f"后端日志：boss_resume_detail 结果入库失败 payload 不符契约: {e}")
            return {
                "success": False,
                "code": "RESUME_PAYLOAD_INVALID",
                "message": f"{e}",
                "effect": result.get("effect"),
                "data": None,
                "invocation_id": result.get("invocation_id"),
            }
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：boss_resume_detail 结果入库失败: {e}")
            return {
                "success": False,
                "code": "RESUME_STORE_FAILED",
                "message": "简历读取成功但入库失败，请稍后重试或联系管理员",
                "effect": result.get("effect"),
                "data": None,
                "invocation_id": result.get("invocation_id"),
            }

        # 返回给 LLM 的 data 只含紧凑摘要（recruiting-operator 上下文预算仅 8000 token，
        # 图片字节/OCR 全文绝不进上下文，完整内容到简历库页面看）。
        # job_warning 为服务层职位关联解析的临时字段（0 命中时「未关联职位」提示）
        summary = {
            "resume_id": record["id"],
            "candidate_name": record.get("candidate_name"),
            "job_name": record.get("job_name"),
            "job_id": record.get("job_id"),
            "image_count": len(record.get("images") or []),
            "ocr_char_count": len(record.get("ocr_text") or ""),
        }
        message = f"简历已存入简历库：{summary['candidate_name']}"
        if summary["job_name"]:
            message += f" · {summary['job_name']}"
        message += f" · {summary['image_count']} 张截图"
        if record.get("job_warning"):
            summary["warning"] = record["job_warning"]
            message += f"；{record['job_warning']}"
        # 落库成功后自动评分（设计 §3）：失败不阻塞，评分异常绝不影响工具成功返回
        match_result = await _evaluate_resume_match_safely(tenant_id, record["id"])
        _apply_match_fields(summary, match_result)
        # 基类在 effect=unknown 时已给 message 追加「实际效果未知」提示，覆写摘要时必须保留
        if UNKNOWN_EFFECT_NOTICE in (result.get("message") or ""):
            message = f"{message}；{UNKNOWN_EFFECT_NOTICE}"
        return {**result, "data": summary, "message": message}


class BossResumeBatchTool(LocalToolProxyTool):
    """BOSS 批量读取简历入库：CLI 批量 payload 逐份落库，图片字节绝不进 LLM 上下文。

    与 BossResumeDetailTool 同语义的批量版：CLI（boss_resume_batch）在推荐牛人页逐个点开
    卡片读取简历，返回 data.resumes 契约 payload 数组 + data.failures；云端逐份入库，
    单份异常捕获记入 failures（不中断循环，与 CLI 侧 failures 合并语义），只返回紧凑摘要列表。

    注意：工具实例是共享单例（tool_registry.register(tool_cls())），
    禁止把每次调用的状态存 self；tenant/user 一律从 kwargs 的 _trusted_* 取。
    """
    name = "boss_resume_batch"
    display_name = "BOSS 批量读取简历入库"
    description = (
        "在用户本机 BOSS 直聘「推荐」页逐个点开牛人卡片批量读取简历（截图+OCR），"
        "结果逐份自动存入简历库，返回紧凑摘要列表（不含图片与 OCR 全文）"
    )

    class InputModel(BaseModel):
        limit: Optional[int] = Field(
            None,
            ge=1,
            le=3,
            description="读取份数上限：默认 1，单次最多 3 份（授权上限，不可突破）",
        )

    timeout_seconds = 600

    async def execute(self, **kwargs) -> Dict[str, Any]:
        tenant_id = kwargs.get("_trusted_tenant_id")
        user_id = kwargs.get("_trusted_user_id")
        result = await super().execute(**kwargs)

        # CLI 失败/非 success：message/code 透传，不落库；data 一律置 None（图片字节绝不进上下文）
        if not result.get("success"):
            return {**result, "data": None}

        data = result.get("data") or {}
        resumes = data.get("resumes")
        if not isinstance(resumes, list) or not resumes:
            return {
                "success": False,
                "code": "RESUME_PAYLOAD_INVALID",
                "message": "CLI 批量结果缺少 resumes 数组或为空（未读取到任何简历）",
                "effect": result.get("effect"),
                "data": None,
                "invocation_id": result.get("invocation_id"),
            }
        # CLI 侧单份失败（打开超时/读取失败等）与云端入库失败合并到同一 failures 列表
        failures: List[Dict[str, Any]] = [
            f for f in (data.get("failures") or []) if isinstance(f, dict)
        ]

        # 逐份入库：单份异常捕获记录，不中断循环（尽量多收简历）
        summaries: List[Dict[str, Any]] = []
        for idx, payload in enumerate(resumes):
            name = payload.get("candidate_name") if isinstance(payload, dict) else None
            try:
                record = await asyncio.to_thread(
                    recruiting_resume_service.create_resume_record_from_tool_result,
                    tenant_id, user_id, payload, "boss",
                )
            except recruiting_resume_service.ResumePayloadError as e:
                logger.error(f"后端日志：boss_resume_batch 第 {idx + 1} 份入库失败 payload 不符契约: {e}")
                failures.append({"name": name, "error": f"{e}"})
                continue
            except Exception as e:
                logger.opt(exception=True).error(f"后端日志：boss_resume_batch 第 {idx + 1} 份入库失败: {e}")
                failures.append({"name": name, "error": "简历入库失败（数据库或存储异常）"})
                continue
            summaries.append({
                "resume_id": record["id"],
                "candidate_name": record.get("candidate_name"),
                "job_name": record.get("job_name"),
                "job_id": record.get("job_id"),
                "image_count": len(record.get("images") or []),
                "ocr_char_count": len(record.get("ocr_text") or ""),
            })
            if record.get("job_warning"):
                summaries[-1]["warning"] = record["job_warning"]

        # 全部失败 → fail-loud；部分/全部成功 → success=True（失败信息在 failures）
        if not summaries:
            return {
                "success": False,
                "code": "RESUME_STORE_FAILED",
                "message": f"批量读取 {len(resumes)} 份简历但全部入库失败，请稍后重试或联系管理员",
                "effect": result.get("effect"),
                "data": None,
                "invocation_id": result.get("invocation_id"),
            }

        # 逐份自动评分（设计 §3）：gather 并行，单份失败/异常吞掉（helper 已兜底）不影响其余与工具返回
        match_results = await asyncio.gather(
            *[_evaluate_resume_match_safely(tenant_id, s["resume_id"]) for s in summaries]
        )
        for s, match_result in zip(summaries, match_results):
            _apply_match_fields(s, match_result)

        # 返回给 LLM 的 data 只含紧凑摘要 + failures（绝不含 base64/OCR 全文，
        # recruiting-operator 上下文预算仅 8000 token，完整内容到简历库页面看）
        names = "、".join(s["candidate_name"] or "?" for s in summaries)
        message = f"已存入简历库 {len(summaries)} 份：{names}"
        if failures:
            first = failures[0]
            message += f"；{len(failures)} 份失败（第一个：{first.get('name') or '未知姓名'}—{first.get('error')}）"
        # 基类在 effect=unknown 时已给 message 追加「实际效果未知」提示，覆写摘要时必须保留
        if UNKNOWN_EFFECT_NOTICE in (result.get("message") or ""):
            message = f"{message}；{UNKNOWN_EFFECT_NOTICE}"
        return {**result, "data": {"resumes": summaries, "failures": failures}, "message": message}


# ============== 话术发送闭环（职位管理 → boss_send_to / boss_send_current，2026-08-17） ==============

# 简历摘录上限（字符）：供 LLM 填话术 {{占位符}} 用，够提炼亮点且省上下文
SCRIPT_RESUME_EXCERPT_CHARS = 600


def _resolve_job_script(
    tenant_id: str, job_name: Optional[str], title: str
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """在「职位管理」里定位话术（标题精确 → 包含兜底）。返回 (script, error)。

    话术只在所属职位下解析，绝不跨职位（简历-职位匹配设计 §4.3）：
    - 必须先定位唯一职位：job_name 必传（精确匹配，包含兜底需唯一命中）；
      租户恰好只有一个职位时可省略 job_name
    - 定位不到职位（无职位 / 名字不存在 / 多职位未传 / 包含匹配多义）→ 报错并列出现有职位名
    - 定位到职位后只在该职位的 scripts 里找（标题精确 → 包含兜底，逻辑不变）
    """
    jobs = recruiting_job_service.list_jobs(tenant_id)
    if not jobs:
        return None, "职位管理里还没有职位，请先在「职位管理」页面添加职位与话术"

    all_names = "、".join(j["job_name"] for j in jobs)
    if job_name:
        matched = [j for j in jobs if j["job_name"] == job_name] or [
            j for j in jobs if job_name in j["job_name"]
        ]
        if not matched:
            return None, f"职位管理中没有职位「{job_name}」，现有：{all_names}"
        if len(matched) > 1:
            names = "、".join(j["job_name"] for j in matched)
            return None, f"职位「{job_name}」匹配到多个职位（{names}），请传完整职位名"
    elif len(jobs) == 1:
        matched = jobs
    else:
        return None, f"职位管理里有 {len(jobs)} 个职位，必须传 job_name 定位话术所属职位；现有：{all_names}"

    job = matched[0]
    detail = recruiting_job_service.get_job(tenant_id, job["id"])
    scripts = (detail or {}).get("scripts", [])
    for s_ in scripts:  # 标题精确
        if s_.get("title") == title:
            return {"job_name": job["job_name"], "category": s_.get("category", ""),
                    "title": s_.get("title", ""), "content": s_.get("content", "")}, None
    for s_ in scripts:  # 标题包含兜底
        if title in s_.get("title", ""):
            return {"job_name": job["job_name"], "category": s_.get("category", ""),
                    "title": s_.get("title", ""), "content": s_.get("content", "")}, None
    available = [s_.get("title", "") for s_ in scripts]
    return None, f"职位「{job['job_name']}」里没找到话术「{title}」，可选：{'、'.join(available) or '（无）'}"


# key_info 截断上限（防 OCR 注入文本借道评分提炼结果进上下文，Phase 2 CR 遗留）：
# str 字段 ≤200 字、数组每项 ≤50 字、数组 ≤8 项
KEY_INFO_STR_MAX_CHARS = 200
KEY_INFO_LIST_ITEM_MAX_CHARS = 50
KEY_INFO_LIST_MAX_ITEMS = 8


def _truncate_key_info(key_info: Any) -> Optional[Dict[str, Any]]:
    """key_info 长度截断：str 字段 ≤200 字、数组每项 ≤50 字、数组 ≤8 项。

    key_info 源自评分 LLM 对 OCR 正文的提炼，可能夹带超长原文片段；截断防注入文本借道。
    保留 str/数字/数组（仅 str/数字项）/null 值，其余类型丢弃；非 dict 输入返回 None。
    """
    if not isinstance(key_info, dict):
        return None
    truncated: Dict[str, Any] = {}
    for key, val in key_info.items():
        if isinstance(val, str):
            truncated[key] = val[:KEY_INFO_STR_MAX_CHARS]
        elif isinstance(val, list):
            items: List[Any] = []
            for item in val[:KEY_INFO_LIST_MAX_ITEMS]:
                if isinstance(item, str):
                    items.append(item[:KEY_INFO_LIST_ITEM_MAX_CHARS])
                elif isinstance(item, (int, float)) and not isinstance(item, bool):
                    items.append(item)
            truncated[key] = items
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            truncated[key] = val
        elif val is None:
            truncated[key] = None
    return truncated


def _resume_match_brief(tenant_id: str, candidate_name: str) -> Optional[Dict[str, Any]]:
    """按姓名定位简历（同名多条取最新），取评分结果 + OCR 摘录。无简历返回 None。

    返回 {resume_id, match_score, key_info（截断版）, ocr_excerpt}；
    match_score / key_info 为 None 表示该简历未评分（评分失败留 NULL 的设计 §3 兜底路径）。
    """
    data = recruiting_resume_service.list_resumes(
        tenant_id, keyword=candidate_name, page=1, page_size=5
    )
    items = data.get("items") or []
    exact = [r for r in items if r.get("candidate_name") == candidate_name] or items
    if not exact:
        return None
    full = recruiting_resume_service.get_resume(tenant_id, exact[0]["id"])
    if not full:
        return None
    return {
        "resume_id": full.get("id"),
        "match_score": full.get("match_score"),
        "key_info": _truncate_key_info(full.get("key_info")),
        "ocr_excerpt": (full.get("ocr_text") or "")[:SCRIPT_RESUME_EXCERPT_CHARS] or None,
    }


def _find_resume_id_by_name(tenant_id: str, candidate_name: str) -> Optional[int]:
    """按姓名定位简历 id（list_resumes 关键词搜索 + 精确名优先 + 同名取最新），无简历返回 None。

    _resume_match_brief 的轻量版：boss_send_to 发送成功后回写沟通记录只用 resume_id，
    不取评分/摘录，避免多查一次 get_resume。
    """
    data = recruiting_resume_service.list_resumes(
        tenant_id, keyword=candidate_name, page=1, page_size=5
    )
    items = data.get("items") or []
    # 精确同名优先；列表按 created_at DESC 返回，exact[0] 即「精确名优先 + 同名取最新」
    exact = [r for r in items if r.get("candidate_name") == candidate_name] or items
    return exact[0]["id"] if exact else None


async def _writeback_send_to_comm_log(kwargs: Dict[str, Any], result: Dict[str, Any]) -> None:
    """boss_send_to 真发送成功后，尽力把本次 message 回写为该候选人简历的沟通记录（2026-09-01）。

    触发条件（全部满足才写）：工具结果 success + 设备真发送（data.sent=true）+ 非试跑
    （data.dry_run=false）+ 受信租户身份非空 + to / message 非空。
    落库：direction='out' / channel='boss' / content=message 全文 / user_id=受信用户（可空）/
    occurred_at 缺省（NOW）。简历库无该姓名 → 静默跳过（debug 留痕）。
    回写是「发送成功后的留痕」：任何异常（DB 不可用等）只 warning 吞掉，绝不改变发送结果的
    success / 返回值，也不向 data 加键（不改结果契约）。
    """
    try:
        if not result.get("success"):
            return
        data = result.get("data") or {}
        if not (isinstance(data, dict) and data.get("sent") and not data.get("dry_run")):
            return
        tenant_id = kwargs.get("_trusted_tenant_id")
        candidate_name = (kwargs.get("to") or "").strip()
        message = (kwargs.get("message") or "").strip()
        if not tenant_id or not candidate_name or not message:
            return
        resume_id = await asyncio.to_thread(_find_resume_id_by_name, tenant_id, candidate_name)
        if not resume_id:
            # 简历库里没有该姓名：静默跳过（发送已成功，不能因无简历报错）
            logger.debug(
                f"后端日志：boss_send_to 回写沟通记录跳过（简历库无该姓名）"
                f"tenant={tenant_id} to={candidate_name}"
            )
            return
        await asyncio.to_thread(
            recruiting_resume_timeline_service.create_comm_log,
            tenant_id, resume_id, "out", "boss", message,
            kwargs.get("_trusted_user_id"),
        )
    except Exception as e:
        logger.opt(exception=True).warning(
            f"后端日志：boss_send_to 发送成功但沟通记录回写失败（不影响发送结果）"
            f"to={kwargs.get('to')}: {e}"
        )


class BossSendToTool(LocalToolProxyTool):
    """向指定联系人发消息（外部写动作）。会话打开复用统一切换链路（already/搜索/列表兜底+身份校验，
    与 boss_open_chat 同源）；话术模式：script_title 引用「职位管理」话术，
    返回话术原文 + 该候选人简历摘录（SCRIPT_NEEDS_FILL），LLM 填好 {{占位符}} 后带 message 重调完成发送。"""

    name = "boss_send_to"
    display_name = "BOSS 向联系人发消息"
    description = (
        "在用户本机 BOSS 直聘「沟通」页打开指定联系人的会话（统一会话切换：已在目标会话零点击/"
        "搜索找人/会话列表兜底，头部身份校验防串会话，返回 via 路径），逐字输入消息并发送（外部写动作；"
        "dry_run=true 只输入不发送）。话术模式：不传 message 而传 script_title（「职位管理」里的话术标题）时，"
        "返回话术原文与该候选人简历摘录（code=SCRIPT_NEEDS_FILL），把 {{占位符}} 替换成具体内容后，"
        "再带完整 message 调用本工具完成发送。前置：当前在沟通页（不在时自动跳转）。"
    )
    timeout_seconds = 300

    class InputModel(BaseModel):
        to: str = Field(..., min_length=1, max_length=30, description="联系人姓名（搜索关键词）")
        message: Optional[str] = Field(None, max_length=2000, description="最终消息全文（话术占位符已替换完毕）")
        script_title: Optional[str] = Field(
            None, max_length=100, description="「职位管理」里的话术标题（话术模式，与 message 二选一）"
        )
        job_name: Optional[str] = Field(
            None, max_length=100, description="话术所属职位名（租户有多个职位时必传定位；仅一个职位时可省略）"
        )
        dry_run: bool = Field(False, description="只输入不发送（测试链路，默认 false 真发送）")

    async def execute(self, **kwargs) -> Dict[str, Any]:
        tenant_id = kwargs.get("_trusted_tenant_id")
        script_title = (kwargs.get("script_title") or "").strip()
        if script_title:
            if kwargs.get("message"):
                return {"success": False, "code": "INVALID_ARGS",
                        "message": "script_title（话术模式）与 message 只能二选一：要么传 script_title 取话术填占位符，要么直接传最终 message"}
            if not tenant_id:
                return {"success": False, "code": "NO_IDENTITY", "message": "无法确定用户身份，请重新登录后再试"}
            script, err = await asyncio.to_thread(
                _resolve_job_script, tenant_id, kwargs.get("job_name"), script_title
            )
            if err:
                return {"success": False, "code": "NOT_FOUND", "message": err}
            brief = await asyncio.to_thread(_resume_match_brief, tenant_id, kwargs["to"])
            result: Dict[str, Any] = {
                "success": False,
                "code": "SCRIPT_NEEDS_FILL",
                "message": (
                    f"话术已定位（{script['job_name']}·{script['category']}·{script['title']}）："
                    "请把 content 里的 {{占位符}} 替换为具体内容——优先用 key_info.highlights"
                    "（评分时已提炼），其次 resume_excerpt，均无证据时据实说明或询问用户，严禁编造；"
                    "确认最终文案并征得用户同意后，带完整 message 重新调用本工具发送"
                ),
                "script": script,
                # 评分结果恒返回（未评分/无简历为 null，设计 §5 升级）
                "match_score": None,
                "key_info": None,
            }
            if brief:
                result["match_score"] = brief["match_score"]
                result["key_info"] = brief["key_info"]
                if brief["ocr_excerpt"]:
                    result["resume_excerpt"] = brief["ocr_excerpt"]
                else:
                    result["resume_hint"] = (
                        f"简历库有「{kwargs['to']}」的简历但 OCR 正文为空，无法提供摘录。"
                        "占位符必须有真实证据：改用无占位符的话术，或会话中确有其信息时据实填写——严禁编造"
                    )
            else:
                result["resume_hint"] = (
                    f"简历库暂无「{kwargs['to']}」的简历。占位符必须有真实证据，三选一："
                    "① 改用无占位符的话术（如 开场·技术栈匹配 / 开场·活跃候选人）；"
                    "② 先用 boss_resume_detail 读取其简历入库后再填；"
                    "③ 会话中确有其信息时据实填写——严禁凭空编造亮点"
                )
            return result
        if not (kwargs.get("message") or "").strip():
            return {"success": False, "code": "INVALID_ARGS",
                    "message": "缺少 message（最终消息全文）；或改用 script_title 话术模式"}
        # 走到这里必为 message 模式（话术模式在前面已 return），设备结果 data={to, via, sent, dry_run}
        result = await super().execute(**kwargs)
        # 发送成功后尽力回写沟通记录（真发送才写；失败只留日志，绝不影响发送结果）
        await _writeback_send_to_comm_log(kwargs, result)
        return result


class BossSendCurrentTool(LocalToolProxyTool):
    """向当前会话发消息（外部写动作）。话术模式同 BossSendToTool（无候选人姓名，不带简历摘录）。

    不做发送后沟通记录自动回写：设备结果 data 仅 {sent, dry_run}，无联系人姓名，
    无法定位简历（2026-09-01 决策，暂不回写）。
    """

    name = "boss_send_current"
    display_name = "BOSS 向当前会话发消息"
    description = (
        "在用户本机 BOSS 直聘「沟通」页向当前已选会话逐字输入消息并发送（外部写动作；dry_run 只输入不发送）。"
        "话术模式：不传 message 而传 script_title（「职位管理」里的话术标题）时，返回话术原文"
        "（code=SCRIPT_NEEDS_FILL），把 {{占位符}} 替换后带完整 message 重调完成发送。"
        "前置：当前在沟通页且已选中会话（右侧有发送按钮）。"
    )
    timeout_seconds = 300

    class InputModel(BaseModel):
        message: Optional[str] = Field(None, max_length=2000, description="最终消息全文（话术占位符已替换完毕）")
        script_title: Optional[str] = Field(
            None, max_length=100, description="「职位管理」里的话术标题（话术模式，与 message 二选一）"
        )
        job_name: Optional[str] = Field(
            None, max_length=100, description="话术所属职位名（租户有多个职位时必传定位；仅一个职位时可省略）"
        )
        dry_run: bool = Field(False, description="只输入不发送（测试链路，默认 false 真发送）")

    async def execute(self, **kwargs) -> Dict[str, Any]:
        tenant_id = kwargs.get("_trusted_tenant_id")
        script_title = (kwargs.get("script_title") or "").strip()
        if script_title:
            if kwargs.get("message"):
                return {"success": False, "code": "INVALID_ARGS",
                        "message": "script_title（话术模式）与 message 只能二选一"}
            if not tenant_id:
                return {"success": False, "code": "NO_IDENTITY", "message": "无法确定用户身份，请重新登录后再试"}
            script, err = await asyncio.to_thread(
                _resolve_job_script, tenant_id, kwargs.get("job_name"), script_title
            )
            if err:
                return {"success": False, "code": "NOT_FOUND", "message": err}
            return {
                "success": False,
                "code": "SCRIPT_NEEDS_FILL",
                "message": (
                    f"话术已定位（{script['job_name']}·{script['category']}·{script['title']}）："
                    "请结合当前会话上下文把 {{占位符}} 替换为具体内容，征得用户同意后带完整 message 重新调用本工具发送"
                ),
                "script": script,
            }
        if not (kwargs.get("message") or "").strip():
            return {"success": False, "code": "INVALID_ARGS",
                    "message": "缺少 message（最终消息全文）；或改用 script_title 话术模式"}
        return await super().execute(**kwargs)


# ============== 沟通会话读取与切换（只读，2026-08-27 随 boss-cli read-chat/open-chat 新增） ==============


class BossReadChatInput(BaseModel):
    contact: Optional[str] = Field(
        None, min_length=1, max_length=30,
        description="可选：联系人姓名，校验当前打开的会话是否为该联系人；不匹配时报错并列出可用联系人，绝不自动切换会话",
    )


class BossReadChatTool(LocalToolProxyTool):
    """读取当前会话消息流 + 全部未读会话清单（只读，设备执行单次快照）。"""

    name = "boss_read_chat"
    display_name = "BOSS 读取会话消息"
    description = (
        "读取用户本机 BOSS 直聘「沟通」页当前会话的消息流（谁发了什么：me/them/system + 正文 + 时间 + 我方已读状态）"
        "与左侧全部未读会话清单（姓名/未读条数/最后一条预览）及总未读徽章。纯只读，不点击、不切换会话；"
        "传 contact 时校验当前会话身份，不匹配报错。前置：已在沟通页（不在时先 boss_goto chat）且已打开一个会话"
    )
    InputModel = BossReadChatInput


class BossOpenChatInput(BaseModel):
    contact: str = Field(
        ..., min_length=1, max_length=30,
        description="联系人姓名（精确，与头部/会话列表姓名 trim 全等）",
    )


class BossOpenChatTool(LocalToolProxyTool):
    """切换到指定联系人会话（无外部写副作用）：already 零点击 / search 搜索优先 / list 会话列表兜底。"""

    name = "boss_open_chat"
    display_name = "BOSS 打开会话"
    description = (
        "在用户本机 BOSS 直聘「沟通」页切换到指定联系人的会话（不发消息，无外部写副作用）。"
        "已在目标会话时零点击返回；优先搜索找人进入对话，失败回退点击左侧会话列表项（视口外自动滚动），"
        "返回 via=already/search/list 告知实际路径。打开后用 boss_read_chat 读取消息。"
        "前置：已在沟通页（不在时先 boss_goto chat）。操作借用真实鼠标约 3-10 秒，期间勿动鼠标"
    )
    InputModel = BossOpenChatInput


# ============== 弹层自愈原语（2026-08-31，仅供云端自愈编排内部调用，不进 SUBAGENT 白名单） ==============


class BossOverlayInspectTool(LocalToolProxyTool):
    """导出主文档文本节点清单（只读）：弹层识别原料，是否弹层/点哪个的判断在云端做。"""

    name = "boss_overlay_inspect"
    display_name = "BOSS 导出弹层候选"
    description = (
        "采集用户本机 BOSS 直聘页面主文档全部文本节点（text/坐标/class），供上层判断是否存在"
        "遮挡弹层并定位关闭控件。纯只读单次快照，不点击不输入。仅供弹层自愈编排内部调用"
    )
    heal_eligible = False  # overlay 原语自身失败不再递归自愈

    class InputModel(BaseModel):
        pass


class BossOverlayDismissInput(BaseModel):
    text: str = Field(
        ..., min_length=1, max_length=20,
        description="关闭控件的精确文本（必须在关闭语义白名单内，与页面文本 trim 全等）",
    )


class BossOverlayDismissTool(LocalToolProxyTool):
    """按白名单关闭文案点击弹层关闭控件并校验消失（页面内 UI 状态变化，无外部业务副作用）。"""

    name = "boss_overlay_dismiss"
    display_name = "BOSS 关闭弹层"
    description = (
        "点击关闭当前页面最上层的弹窗/引导弹层：按传入的关闭控件文本定位并真实鼠标点击，"
        "点击后校验弹层消失。仅接受关闭语义白名单文案（关闭/知道了/以后再说/取消/跳过/× 等，"
        "非白名单直接拒绝），绝不点击领取/开通类按钮。仅供弹层自愈编排内部调用"
    )
    heal_eligible = False

    InputModel = BossOverlayDismissInput


LOCAL_PROXY_TOOL_CLASSES = (
    BossFilterTool,
    BossClearFilterTool,
    BossFilterOptionsTool,
    BossGotoTool,
    BossGreetTool,
    BossAcceptResumeTool,
    BossRejectCurrentTool,
    BossInterviewDemoTool,
    BossInterviewNotifyTool,
    BossListJobsTool,
    BossSelectJobTool,
    BossJobsListTool,
    BossResumeDetailTool,
    BossResumeBatchTool,
    BossSendToTool,
    BossSendCurrentTool,
    BossReadChatTool,
    BossOpenChatTool,
    BossOverlayInspectTool,
    BossOverlayDismissTool,
)

LOCAL_PROXY_TOOL_NAMES = frozenset(cls.name for cls in LOCAL_PROXY_TOOL_CLASSES)
