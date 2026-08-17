"""本地代理工具（LocalToolProxy）：云端 LLM 工具 → 用户本机 Runtime 执行

设计：docs/design/recruiting/recruiting-cli-agent-integration-design.md §4/§11/§14
实施规格：docs/plans/recruiting/m05-implementation-spec.md §3

9 个 boss_* 工具全部为 LOCAL_REQUIRED：execute() 不直接操作 BOSS，
而是经「设备闸门 → 创建 invocation → 轮询 events/state → 终态映射」
驱动本机 Runtime 执行，进度事件推入 agent 注入的 _progress_queue。

其中 boss_resume_detail / boss_resume_batch 额外做云端后处理：CLI 成功结果（截图+OCR payload）
在工具层直接落简历库（batch 逐份落库），只把紧凑摘要返回给 LLM（图片字节不进上下文）。

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
from src.services import recruiting_job_service, recruiting_resume_service
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
                "候选人姓名（会话上下文已知时建议传入，更可靠）；"
                "缺省 CLI 从 OCR 首行自动识别，识别失败会报错要求传参"
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
            logger.error(f"后端日志：boss_resume_detail 结果入库失败: {e}", exc_info=True)
            return {
                "success": False,
                "code": "RESUME_STORE_FAILED",
                "message": "简历读取成功但入库失败，请稍后重试或联系管理员",
                "effect": result.get("effect"),
                "data": None,
                "invocation_id": result.get("invocation_id"),
            }

        # 返回给 LLM 的 data 只含紧凑摘要（recruiting-operator 上下文预算仅 8000 token，
        # 图片字节/OCR 全文绝不进上下文，完整内容到简历库页面看）
        summary = {
            "resume_id": record["id"],
            "candidate_name": record.get("candidate_name"),
            "job_name": record.get("job_name"),
            "image_count": len(record.get("images") or []),
            "ocr_char_count": len(record.get("ocr_text") or ""),
        }
        message = f"简历已存入简历库：{summary['candidate_name']}"
        if summary["job_name"]:
            message += f" · {summary['job_name']}"
        message += f" · {summary['image_count']} 张截图"
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
                logger.error(f"后端日志：boss_resume_batch 第 {idx + 1} 份入库失败: {e}", exc_info=True)
                failures.append({"name": name, "error": "简历入库失败（数据库或存储异常）"})
                continue
            summaries.append({
                "resume_id": record["id"],
                "candidate_name": record.get("candidate_name"),
                "job_name": record.get("job_name"),
                "image_count": len(record.get("images") or []),
                "ocr_char_count": len(record.get("ocr_text") or ""),
            })

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
    """在「职位管理」里定位话术（标题精确 → 包含兜底）。返回 (script, error)；job_name 缺省遍历全部职位。"""
    jobs = recruiting_job_service.list_jobs(tenant_id)
    if not jobs:
        return None, "职位管理里还没有职位，请先在「职位管理」页面添加职位与话术"
    matched = jobs
    if job_name:
        matched = [j for j in jobs if j["job_name"] == job_name] or [
            j for j in jobs if job_name in j["job_name"]
        ]
        if not matched:
            return None, f"职位管理中没有职位「{job_name}」，现有：{'、'.join(j['job_name'] for j in jobs)}"
    for job in matched:
        detail = recruiting_job_service.get_job(tenant_id, job["id"])
        scripts = (detail or {}).get("scripts", [])
        for s_ in scripts:
            if s_.get("title") == title:
                return {"job_name": job["job_name"], "category": s_.get("category", ""),
                        "title": s_.get("title", ""), "content": s_.get("content", "")}, None
    for job in matched:  # 包含兜底
        detail = recruiting_job_service.get_job(tenant_id, job["id"])
        for s_ in (detail or {}).get("scripts", []):
            if title in s_.get("title", ""):
                return {"job_name": job["job_name"], "category": s_.get("category", ""),
                        "title": s_.get("title", ""), "content": s_.get("content", "")}, None
    available = []
    for job in matched:
        detail = recruiting_job_service.get_job(tenant_id, job["id"])
        available.extend(s_.get("title", "") for s_ in (detail or {}).get("scripts", []))
    scope = f"职位「{job_name}」" if job_name else "职位管理"
    return None, f"{scope}里没找到话术「{title}」，可选：{'、'.join(available) or '（无）'}"


def _resume_ocr_excerpt(tenant_id: str, candidate_name: str) -> Optional[str]:
    """按姓名取简历库 OCR 正文开头（同名多条取最新）。无简历返回 None。"""
    data = recruiting_resume_service.list_resumes(
        tenant_id, keyword=candidate_name, page=1, page_size=5
    )
    items = data.get("items") or []
    exact = [r for r in items if r.get("candidate_name") == candidate_name] or items
    if not exact:
        return None
    full = recruiting_resume_service.get_resume(tenant_id, exact[0]["id"])
    text = (full or {}).get("ocr_text") or ""
    return text[:SCRIPT_RESUME_EXCERPT_CHARS] or None


class BossSendToTool(LocalToolProxyTool):
    """搜索找人发消息（外部写动作）。话术模式：script_title 引用「职位管理」话术，
    返回话术原文 + 该候选人简历摘录（SCRIPT_NEEDS_FILL），LLM 填好 {{占位符}} 后带 message 重调完成发送。"""

    name = "boss_send_to"
    display_name = "BOSS 搜索找人发消息"
    description = (
        "在用户本机 BOSS 直聘「沟通」页搜索联系人姓名并进入对话，逐字输入消息并发送（外部写动作；"
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
            None, max_length=100, description="话术所属职位名（多职位时定位；缺省遍历全部职位）"
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
            excerpt = await asyncio.to_thread(_resume_ocr_excerpt, tenant_id, kwargs["to"])
            result: Dict[str, Any] = {
                "success": False,
                "code": "SCRIPT_NEEDS_FILL",
                "message": (
                    f"话术已定位（{script['job_name']}·{script['category']}·{script['title']}）："
                    "请把 content 里的 {{占位符}} 替换为具体内容（参考 resume_excerpt 提炼），"
                    "确认最终文案并征得用户同意后，带完整 message 重新调用本工具发送"
                ),
                "script": script,
            }
            if excerpt:
                result["resume_excerpt"] = excerpt
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
        return await super().execute(**kwargs)


class BossSendCurrentTool(LocalToolProxyTool):
    """向当前会话发消息（外部写动作）。话术模式同 BossSendToTool（无候选人姓名，不带简历摘录）。"""

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
            None, max_length=100, description="话术所属职位名（多职位时定位；缺省遍历全部职位）"
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


LOCAL_PROXY_TOOL_CLASSES = (
    BossFilterTool,
    BossClearFilterTool,
    BossFilterOptionsTool,
    BossGotoTool,
    BossGreetTool,
    BossAcceptResumeTool,
    BossRejectCurrentTool,
    BossInterviewDemoTool,
    BossResumeDetailTool,
    BossResumeBatchTool,
    BossSendToTool,
    BossSendCurrentTool,
)

LOCAL_PROXY_TOOL_NAMES = frozenset(cls.name for cls in LOCAL_PROXY_TOOL_CLASSES)
