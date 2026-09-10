"""微信营销工作台服务（P3-A1，R54①）

四组能力（API 面见 weixin_marketing/api.py 追加段）：
- test-send：显式 block position + group_binding_id 只试发该条——独立 run/attempt
  审计（task_ref=<automation>:test，不占用生产 task subject/schedules，也不经
  dispatch worker：单条 delivery 由本服务直接编译派发，提交时即 running，调度器
  永远看不到 pending 试发 run）；配额走适配器 wxm:test:* 独立 scope（R54①）；
- group-searches：经 LocalInvocationService 入队只读 weixin_chat_search 到指定
  设备（202 异步，GET 轮询），候选带 target_ref，不自动建绑定（人工选择）；
- group-bindings：从搜索候选创建 pending（=pending_verification）绑定，verify 经
  local_tool 队列做只读核验（定位群 + 标题精确比对）：唯一精确命中→complete
  （=verified）、同名多命中→rejected（候选冲突）、设备离线/工具失败/零命中→
  409 VERIFY_FAILED（绑定保持 pending，可重试，服务端不伪造通过）；
- devices：属主设备列表（在线状态 + capabilities 的 weixin provider 可用性）与
  weixin_probe 预检（返回环境矩阵）。

幂等（R51 口径的边界说明）：
- test-send / group-bindings 的业务表写入、审计与幂等完成记录在同一事务提交；
- search / preflight / verify 的业务写入是 local_tool_invocations enqueue（repository
  层自带连接提交，本模块不复制其 SQL）——幂等完成记录在 enqueue 之后的短事务写入，
  「业务已提交而响应未保存」窗口存在，但 dedupe_key 由 Idempotency-Key 确定，崩溃
  残留 pending 占位经 TTL 接管重放时 enqueue 复用同键 invocation，收敛到同一资源，
  不产生重复副作用；
- 失败重试（V-P1）：失败路径 abandon 释放路由占位后，同 key 重试经 _enqueue_read_op
  的非终态复用判定 + 键尾尝试序号重建（:r2/:r3...），不复用已终态的旧失败
  invocation——「离线失败→设备恢复→同 key 重试成功」成立。

真机依赖（P0 门禁，诚实声明）：搜索/核验/预检的设备侧回包由测试以 fake 设备行为
驱动（claim/start/result 直调 + 读链路走旧 /result 语义的 write_result；claim 经
catalog provider 过滤，与生产 /runtime/claim 同构）。src/local_tools/catalog.py
已注册 weixin 受信 Provider（4 只读工具 + weixin_message_send_v2；修复轮经总工程
师授权落地）；真机身份验证（账号 anchor/群标题规范化/真实证据校验器/target_ref
设备侧语义）仍依赖 P0，本包不宣称真机通过。
"""

import time
import uuid as _uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.desktop_automation import attempts as da_attempts
from src.desktop_automation import deliveries as da_deliveries
from src.desktop_automation import occurrences as da_occurrences
from src.desktop_automation import quota as da_quota
from src.desktop_automation import runs as da_runs
from src.desktop_automation import subjects as da_subjects
from src.desktop_automation.adapters import AdapterContext
from src.desktop_automation.constants import (
    BUSINESS_KIND_DESKTOP_AUTOMATION,
    TASK_STATUS_ACTIVE,
    TRIGGER_KIND_MANUAL,
    manual_trigger_key,
)
from src.desktop_automation.executor import (
    DEFAULT_OPERATION_DEADLINE_SECONDS,
    build_v2_operation_arguments,
    derive_resource_key,
)
from src.local_tools import repository as lt_repository
from src.local_tools.security import sha256_hex
from src.local_tools.service import LocalInvocationService
from src.weixin_marketing import content
from src.weixin_marketing.config import get_weixin_marketing_config, tenant_allowed
from src.weixin_marketing.constants import (
    ACCOUNT_BINDING_STATUS_ACTIVE,
    AUTOMATION_STATUS_ACTIVE,
    BINDING_EVIDENCE_NAMESPACE,
    BLOCK_KIND_IMAGE,
    BUSINESS_KIND_BINDING_VERIFY,
    BUSINESS_KIND_DEVICE_PREFLIGHT,
    BUSINESS_KIND_GROUP_SEARCH,
    GROUP_BINDING_STATE_COMPLETE,
    GROUP_BINDING_STATE_PENDING,
    GROUP_BINDING_VERIFIABLE_STATES,
    AUDIT_DEVICE_PREFLIGHT_REQUESTED,
    AUDIT_GROUP_BINDING_CREATED,
    AUDIT_GROUP_BINDING_REJECTED,
    AUDIT_GROUP_BINDING_VERIFIED,
    AUDIT_GROUP_SEARCH_REQUESTED,
    AUDIT_TEST_SEND_REQUESTED,
    OPERATION_MESSAGE_SEND,
    PROVIDER_KEY,
    REVISION_STATUS_PUBLISHED,
    SCENARIO_KEY,
    TEST_TASK_REF_SUFFIX,
    TOOL_WEIXIN_CHAT_SEARCH,
    TOOL_WEIXIN_PROBE,
)
from src.weixin_marketing.models import (
    GroupBindingCreateInput,
    GroupSearchCreateInput,
    TestSendInput,
)
from src.weixin_marketing.service import (
    ConflictError,
    NotFoundError,
    PreflightFailedError,
    QuotaExceededError,
    TenantNotAllowedError,
    VerifyFailedError,
    WeixinValidationError,
    _adapter,
    _aware,
    _get_automation_on,
    _get_revision_on,
    _insert_weixin_audit_on,
    _list_revision_blocks_on,
    utcnow,
)

# ==================== 参数 ====================

# 只读操作（搜索/核验/预检）同步等待设备回包的时长与轮询间隔；服务在 to_thread 中
# 阻塞轮询，不占事件循环。测试可注入短值。
READ_OP_WAIT_SECONDS = 30
READ_OP_POLL_INTERVAL_SECONDS = 0.5
# 只读 invocation 截止（超时后由租约清扫/取消收敛，不无限占队）
READ_OP_DEADLINE_SECONDS = 300
# V-P1 粘滞修复：dedupe 键复用仅接受 state='queued' 的既有 invocation（P2-5 模式）；
# 非 queued（上次同 key 尝试的终态/取消残留）则键附加尝试序号重建，有界
READ_OP_RETRY_MAX_ATTEMPTS = 5
# 搜索结果候选条数上限（§6.1 完整候选集合 + 明确截断标志由设备侧回传）
SEARCH_RESULT_LIMIT = 50
# 试发 run 的租约/存活窗口：覆盖操作截止 + 余量，超时由 runs_reclaim 收敛
TEST_RUN_LEASE_SECONDS = DEFAULT_OPERATION_DEADLINE_SECONDS + 300

# invocation 状态 → 搜索/预检对外状态（R54①：pending/running/succeeded/failed）
_STATE_PENDING = ("queued",)
_STATE_RUNNING = ("claimed", "running", "cancel_requested")
_STATE_FAILED = ("failed", "cancelled", "unknown", "expired")


def _map_search_status(state: str) -> str:
    if state in _STATE_PENDING:
        return "pending"
    if state in _STATE_RUNNING:
        return "running"
    if state == "succeeded":
        return "succeeded"
    return "failed"


def _result_data(invocation: Dict[str, Any]) -> Dict[str, Any]:
    result = invocation.get("result_json") or {}
    data = result.get("data")
    return data if isinstance(data, dict) else {}


def _candidate_items(invocation: Dict[str, Any]) -> List[Dict[str, Any]]:
    """搜索回包候选（设备侧回传，服务端只透传并规整 target_ref 为字符串）"""
    items = _result_data(invocation).get("items")
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict)]


def _candidates_incomplete_reason(
    data: Dict[str, Any], items: List[Dict[str, Any]]
) -> Optional[str]:
    """P3 复审 P1-2：唯一性判定前的候选集合完整性门槛（§6.1 完整候选集合/明确截断标志）。

    判据（与 result_json.data 真实结构对齐——items/truncated 为设备回包字段）：
    - 回包带显式 truncated/complete 标志时按标志（任一指示截断即不完整，冲突取保守侧）；
    - 无任何标志时按条数启发式：返回条数 ≥ 本次请求 limit（SEARCH_RESULT_LIMIT）
      视为可能截断，不判 complete/rejected；
    - 仅条数 < limit 或显式 complete 才允许进入唯一性判定。
    返回阻断原因文案；None = 候选集合完整。
    """
    has_truncated = data.get("truncated") is not None
    has_complete = data.get("complete") is not None
    if bool(data.get("truncated")) or (has_complete and not bool(data.get("complete"))):
        return "候选集合被截断（工具回包带截断标志），请缩小群名搜索词后重试"
    if not (has_truncated or has_complete) and len(items) >= SEARCH_RESULT_LIMIT:
        return (
            f"候选集合可能不完整（达到上限 {SEARCH_RESULT_LIMIT}），"
            "请缩小群名搜索词后重试"
        )
    return None


def _normalize_title(value: Any) -> str:
    """标题比对规范化（P3 fake 口径：仅去首尾空白）。

    【微信P §6.1】允许的规范化仅限经 P0 probe 验证的 UI 人数后缀等——结论出来前
    不做任何剥离，宁可 0 命中走可重试失败，不做模糊匹配。
    """
    return str(value or "").strip()


def _weixin_capability_view(capabilities: Any) -> Dict[str, Any]:
    """capabilities_json → weixin provider 可用性视图（与 P1-B Runtime 上报格式对齐）。

    不向 Web 暴露原始 capability payload（local_tools api.py 设计 §0 同口径），
    只提取 weixin 是否在 providers 数组 / provider_manifests 内。
    """
    if not isinstance(capabilities, dict):
        return {"available": False, "provider_key": None}
    available = False
    protocol_version = None
    providers = capabilities.get("providers")
    if isinstance(providers, list):
        for entry in providers:
            key = entry if isinstance(entry, str) else (
                entry.get("provider_key") if isinstance(entry, dict) else None
            )
            if key == PROVIDER_KEY:
                available = True
    manifests = capabilities.get("provider_manifests")
    if isinstance(manifests, dict):
        manifest = manifests.get(PROVIDER_KEY)
        if isinstance(manifest, dict):
            available = True
            protocol_version = manifest.get("protocol_version")
    return {
        "available": available,
        "provider_key": PROVIDER_KEY if available else None,
        "protocol_version": protocol_version,
    }


def _load_device_on(cursor, tenant_id: str, user_id: str, device_id: str) -> Optional[Dict[str, Any]]:
    """按属主取设备行（跨租户/非属主/不存在统一 None → 调用方 404）"""
    try:
        _uuid.UUID(str(device_id))
    except (ValueError, TypeError, AttributeError):
        return None
    cursor.execute(
        """
        SELECT id, tenant_id, user_id, name, platform, runtime_version, status,
               capabilities_json, selected, last_seen_at, created_at
        FROM local_tool_devices
        WHERE id = %s AND tenant_id = %s AND user_id = %s
        """,
        (str(device_id), tenant_id, user_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _require_active_device(tenant_id: str, user_id: str, device_id: str) -> Dict[str, Any]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        device = _load_device_on(cursor, tenant_id, user_id, device_id)
    if device is None or device.get("status") != "active":
        raise NotFoundError("设备不存在、无权限或已撤销")
    return device


def _enqueue_read_op(
    *,
    tenant_id: str,
    user_id: str,
    device_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    business_kind: str,
    business_ref: Dict[str, Any],
    dedupe_key: str,
    now: datetime,
) -> Dict[str, Any]:
    """只读操作入队（LocalInvocationService 纯使用；provider 固定 weixin——
    claim 过滤保证仅 weixin 能力设备可领取）。

    V-P1 粘滞修复（retry_delivery P2-5 死键防绑同源问题，判定口径适配同步等待
    模型）：dedupe 键命中既有 invocation 时，**非终态**（queued/claimed/running/
    cancel_requested）即复用——活体 invocation 由本模块的等待循环观测终态；仅
    终态残留（同 key 上次尝试的 failed/cancelled/succeeded/unknown/expired——
    失败路径 abandon 释放路由占位后的重试形态）在键尾附加尝试序号重建
    （:r2/:r3...，有界 READ_OP_RETRY_MAX_ATTEMPTS 次），同 key 重试不再复用旧
    失败结果。不取严格 queued 判定：enqueue 的 INSERT 与回读之间设备可即时领取
    （claimed/running 也是活体），严格 queued 会误判死键产生冗余重建。
    连续死键残留时抛 ConflictError（与人工重试链同语义）。
    """
    service = LocalInvocationService()
    attempt_key = dedupe_key
    for retry_no in range(READ_OP_RETRY_MAX_ATTEMPTS):
        invocation = service.enqueue(
            tenant_id=tenant_id,
            user_id=user_id,
            device_id=device_id,
            tool_name=tool_name,
            arguments=arguments,
            provider_key=PROVIDER_KEY,
            business_kind=business_kind,
            business_ref=business_ref,
            dedupe_key=attempt_key,
            deadline_at=now + timedelta(seconds=READ_OP_DEADLINE_SECONDS),
        )
        if str(invocation.get("state") or "queued") not in lt_repository.TERMINAL_STATES:
            return invocation
        attempt_key = f"{dedupe_key}:r{retry_no + 2}"
    raise ConflictError(
        "只读操作无法获得可执行 invocation（同键连续死键残留），请稍后重试"
    )


def _wait_invocation_terminal(
    tenant_id: str,
    invocation_id: str,
    *,
    wait_seconds: Optional[float] = None,
    poll_interval_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """轮询至终态或超时（返回最后快照；调用方按状态判定）"""
    wait = READ_OP_WAIT_SECONDS if wait_seconds is None else wait_seconds
    interval = READ_OP_POLL_INTERVAL_SECONDS if poll_interval_seconds is None else poll_interval_seconds
    deadline = time.monotonic() + max(0.0, wait)
    invocation: Optional[Dict[str, Any]] = None
    while True:
        invocation = lt_repository.get_invocation(invocation_id, tenant_id)
        if invocation is None or invocation.get("state") in lt_repository.TERMINAL_STATES:
            return invocation or {}
        if time.monotonic() >= deadline:
            return invocation
        time.sleep(interval)


def _cancel_read_op(tenant_id: str, invocation_id: str) -> None:
    """等待超时后的收敛（queued→cancelled 终态；claimed/running→cancel_requested）"""
    try:
        LocalInvocationService().cancel(invocation_id, tenant_id)
    except Exception as e:  # noqa: BLE001 取消失败不掩盖主错误
        logger.opt(exception=True).warning(
            f"后端日志：weixin_marketing 只读操作超时取消失败 invocation={invocation_id}: {e}"
        )


class WeixinWorkbenchService:
    """工作台服务（无状态；同步 psycopg2，FastAPI 层 asyncio.to_thread）"""

    # ==================== test-send ====================

    def test_send(
        self,
        tenant_id: str,
        automation_id: str,
        user_id: str,
        payload: TestSendInput,
        *,
        request_id: str,
        now: Optional[datetime] = None,
        idempotency: Any = None,
    ) -> Dict[str, Any]:
        """试发（R54①）：显式 block + 绑定，只试发该条。

        - 独立审计：task_ref=<automation>:test 的独立 run + occurrence（manual
          trigger key 与 request_id 同源去重）+ weixin 审计行；不进生产 runs 列表
          （GET /runs?automation_id= 过滤生产 task_ref，试发 run 按 id 仍可查详情）；
        - 独立配额：适配器对 :test task_ref 产出 wxm:test:* scope（许可事务原子
          预留/落账），本函数入口再做只读预检（R19：reserved+used 判定）；
        - 不经 dispatch worker：delivery 单条直接编译派发，run 在创建事务内即置
          running + 租约，调度器不可见 pending 试发 run（防全量 revision 误编译）；
        - 写入三段式：①事务A（test subject/occurrence/run/delivery）→ ②invocation
          enqueue（独立连接，dedupe 键确定性）→ ③事务B（attempt+dispatched+审计+
          幂等 write_on，R51 同事务收尾）。①→③ 之间的崩溃窗口由「manual 触发键
          去重 + invocation dedupe 键」收敛：同 key TTL 接管重放复用同一
          occurrence/run/delivery/invocation，不产生重复副作用；②前崩溃则 run 由
          租约回收（runs_reclaim_tick）按 §5.4 收敛终态，不留永久 running 行；
        - 许可/执行/结果链与生产完全同构（write-authorize→operation-result→聚合）。
        """
        now = _aware(now or utcnow())
        if not tenant_allowed(get_weixin_marketing_config(), tenant_id):
            raise TenantNotAllowedError("租户未在 weixin_marketing.tenant_allowlist 白名单内，拒绝试发")
        adapter = _adapter()
        test_task_ref = f"{automation_id}{TEST_TASK_REF_SUFFIX}"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            automation = _get_automation_on(cursor, tenant_id, automation_id)
            if automation is None or automation.get("user_id") != user_id:
                raise NotFoundError("自动化任务不存在")
            if automation.get("status") != AUTOMATION_STATUS_ACTIVE:
                raise ConflictError(f"任务状态不允许试发: {automation.get('status')}")
            revision_id = str(automation.get("active_revision_id") or "")
            revision = _get_revision_on(cursor, tenant_id, revision_id) if revision_id else None
            if revision is None or revision.get("status") != REVISION_STATUS_PUBLISHED:
                raise ConflictError("无已发布 revision，拒绝试发（试发仅支持已发布内容）")
            blocks = _list_revision_blocks_on(cursor, tenant_id, revision_id)
            block = next(
                (b for b in blocks if int(b["position"]) == int(payload.block_position)), None
            )
            if block is None:
                raise WeixinValidationError(f"块不存在: position={payload.block_position}")
            if block.get("kind") == BLOCK_KIND_IMAGE:
                raise WeixinValidationError("图片块试发暂未开放（图片 operation 随 P4 交付）")
            binding = self._load_binding_on(cursor, tenant_id, payload.group_binding_id)
            if binding is None or str(binding.get("user_id")) != user_id:
                raise WeixinValidationError("群绑定不存在或不可用")
            if binding.get("state") != GROUP_BINDING_STATE_COMPLETE:
                raise WeixinValidationError(f"群绑定不可用于发送: state={binding.get('state')}")
            device_id = str(binding.get("device_id") or "")
            if not device_id:
                raise WeixinValidationError("群绑定未关联设备")
            device = _load_device_on(cursor, tenant_id, user_id, device_id)
            if device is None or device.get("status") != "active":
                raise WeixinValidationError("群绑定关联的设备不存在或已撤销")

        ctx = AdapterContext(
            tenant_id=tenant_id, user_id=user_id, scenario_key=SCENARIO_KEY,
            task_ref=test_task_ref, revision_ref=revision_id,
        )
        resolution = adapter.resolve_target(ctx, str(payload.group_binding_id))
        if not resolution.ok:
            raise ConflictError(f"试发目标不可解析: {resolution.reason}")
        decision = adapter.authorize_operation(
            ctx,
            operation=OPERATION_MESSAGE_SEND,
            target_ref=str(payload.group_binding_id),
            target_version=resolution.target_version,
            payload_hash=block.get("payload_hash"),
            authorization_revision=revision_id,
            authorization_epoch=None,
        )
        if not decision.allowed:
            raise ConflictError(f"试发授权拒绝: {decision.reason}")
        for spec in decision.quota_scopes:
            bucket = da_quota.get_bucket(
                tenant_id, spec.scope_type, spec.scope_id,
                da_quota.bucket_start_for(spec.window_seconds, now),
            )
            if bucket is not None and (
                int(bucket["reserved_count"]) + int(bucket["used_count"]) >= int(bucket["limit_count"])
            ):
                raise QuotaExceededError(
                    f"试发配额不足: {spec.scope_type}:{spec.scope_id}"
                )

        trigger_key = manual_trigger_key(f"test-send:{request_id}")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # R12 锁序第一环：test task subject（存在且指向当前 revision 则复用，不递增 epoch）
            task = da_subjects.lock_task_subject(
                cursor, tenant_id, SCENARIO_KEY, test_task_ref, skip_locked=False
            )
            if task is None or task["active_revision_ref"] != revision_id \
                    or task["status"] != TASK_STATUS_ACTIVE:
                epoch_row = da_subjects._upsert_task_subject(
                    cursor, tenant_id, SCENARIO_KEY, test_task_ref, user_id,
                    active_revision_ref=revision_id, status=TASK_STATUS_ACTIVE,
                )
                epoch = int(epoch_row["authorization_epoch"])
            else:
                epoch = int(task["authorization_epoch"])
            occurrence_id, created = self._admit_test_occurrence_on(
                cursor, tenant_id, user_id, test_task_ref, revision_id, trigger_key, now
            )
            run_id = self._ensure_test_run_on(
                cursor, tenant_id, user_id, occurrence_id, test_task_ref,
                revision_id, epoch, device_id, now,
            )
            run_row = {
                "tenant_id": tenant_id, "id": run_id, "scenario_key": SCENARIO_KEY,
                "task_ref": test_task_ref, "revision_ref": revision_id, "user_id": user_id,
            }
            op = {
                "position": int(payload.block_position),
                "operation": OPERATION_MESSAGE_SEND,
                "provider_key": PROVIDER_KEY,
                "target_ref": str(payload.group_binding_id),
                "target_handle": None,  # 执行期 resolve_target 现签（已预检通过）
                "target_version": None,
                "payload_ref": content.build_payload_ref(revision_id, int(payload.block_position)),
                "payload_hash": block.get("payload_hash"),
            }
            delivery_ids = da_deliveries.insert_deliveries(cursor, run_row, [op])
            if delivery_ids:
                delivery_id = delivery_ids[0]
            else:
                cursor.execute(
                    "SELECT id FROM desktop_automation_deliveries "
                    "WHERE tenant_id = %s AND run_id = %s ORDER BY position LIMIT 1",
                    (tenant_id, run_id),
                )
                delivery_id = str(cursor.fetchone()["id"])
            conn.commit()

        # ---- enqueue（独立连接提交，dedupe 键确定性——崩溃接管重放复用同 invocation）----
        op_request_id = str(_uuid.uuid4())
        deadline_at = now + timedelta(seconds=DEFAULT_OPERATION_DEADLINE_SECONDS)
        arguments = build_v2_operation_arguments(
            operation=OPERATION_MESSAGE_SEND,
            provider_key=PROVIDER_KEY,
            target_ref=str(payload.group_binding_id),
            target_handle=resolution.target_handle,
            target_version=resolution.target_version,
            payload_ref=op["payload_ref"],
            payload_hash=op["payload_hash"],
            request_id=op_request_id,
            delivery_id=delivery_id,
            authorization_revision=revision_id,
            authorization_epoch=epoch,
            resource_key=derive_resource_key(device_id),
            deadline_at=deadline_at,
        )
        invocation = LocalInvocationService().enqueue(
            tenant_id=tenant_id,
            user_id=user_id,
            device_id=device_id,
            tool_name=OPERATION_MESSAGE_SEND,
            arguments=arguments,
            provider_key=PROVIDER_KEY,
            business_kind=BUSINESS_KIND_DESKTOP_AUTOMATION,
            business_ref={
                "delivery_id": delivery_id,
                "run_id": run_id,
                "occurrence_id": occurrence_id,
                "scenario_key": SCENARIO_KEY,
                "task_ref": test_task_ref,
                "revision_ref": revision_id,
            },
            dedupe_key=f"delivery:{delivery_id}:a:1",
            deadline_at=deadline_at,
            authorization_epoch=epoch,
        )
        effective_request_id = (invocation.get("arguments_json") or {}).get("request_id") or op_request_id

        delivery_stub = {"id": delivery_id, "run_id": run_id, "user_id": user_id}
        with get_db_connection() as conn:
            cursor = conn.cursor()
            existing_attempt = da_attempts.get_attempt_by_invocation_on(
                cursor, str(invocation["id"]), tenant_id
            )
            if existing_attempt is not None:
                attempt_id = str(existing_attempt["id"])
            else:
                attempt_id = da_attempts.create_attempt(
                    cursor, tenant_id, delivery_stub, str(invocation["id"]), effective_request_id
                )
            da_deliveries.mark_dispatched(cursor, delivery_id, tenant_id)
            result = {
                "automation_id": str(automation_id),
                "run_id": run_id,
                "occurrence_id": occurrence_id,
                "delivery_id": delivery_id,
                "invocation_id": str(invocation["id"]),
                "attempt_id": attempt_id,
                "request_id": effective_request_id,
                "block_position": int(payload.block_position),
                "group_binding_id": str(payload.group_binding_id),
            }
            _insert_weixin_audit_on(
                cursor, tenant_id, AUDIT_TEST_SEND_REQUESTED, user_id=user_id,
                automation_id=automation_id, run_id=run_id,
                details={
                    "delivery_id": delivery_id,
                    "invocation_id": str(invocation["id"]),
                    "block_position": int(payload.block_position),
                    "group_binding_id": str(payload.group_binding_id),
                    "request_id": effective_request_id,
                    "occurrence_created": created,
                },
            )
            if idempotency is not None:
                idempotency.write_on(cursor, status_code=202, data=result)
            conn.commit()
        logger.info(
            f"后端日志：weixin_marketing 试发派发 tenant={tenant_id} automation={automation_id} "
            f"run={run_id} delivery={delivery_id} block={payload.block_position} "
            f"invocation={invocation['id']}"
        )
        return result

    @staticmethod
    def _admit_test_occurrence_on(
        cursor, tenant_id: str, user_id: str, task_ref: str,
        revision_id: str, trigger_key: str, now: datetime,
    ) -> tuple:
        """试发 occurrence（manual；无 outbox——试发不经 dispatch 派发，见类 docstring）"""
        cursor.execute(
            """
            INSERT INTO desktop_automation_occurrences
                (tenant_id, scenario_key, task_ref, revision_ref, user_id, trigger_kind,
                 trigger_key, scheduled_for, due_at, expires_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
            ON CONFLICT (tenant_id, scenario_key, task_ref, trigger_key) DO NOTHING
            RETURNING id
            """,
            (
                tenant_id, SCENARIO_KEY, task_ref, revision_id, user_id, TRIGGER_KIND_MANUAL,
                trigger_key, now, now, None,
            ),
        )
        row = cursor.fetchone()
        if row is not None:
            return str(row["id"]), True
        existing = da_occurrences.get_occurrence_by_trigger_key_on(
            cursor, tenant_id, SCENARIO_KEY, task_ref, trigger_key
        )
        return (str(existing["id"]) if existing else None), False

    @staticmethod
    def _ensure_test_run_on(
        cursor, tenant_id: str, user_id: str, occurrence_id: Optional[str],
        task_ref: str, revision_id: str, epoch: int, device_id: str, now: datetime,
    ) -> str:
        """试发 run：创建（或复用既有 run）并在**同一事务**内置 running + 租约。

        dispatch worker 只领 pending run——试发 run 提交时即 running，杜绝 worker
        以全量 revision 配置误编译（那会发送全部块）。
        """
        run_id: Optional[str] = None
        if occurrence_id:
            cursor.execute(
                "SELECT id, state FROM desktop_automation_runs "
                "WHERE tenant_id = %s AND occurrence_id = %s",
                (tenant_id, occurrence_id),
            )
            row = cursor.fetchone()
            run_id = str(row["id"]) if row else None
        if run_id is None:
            run_id = da_runs.create_run(
                cursor, tenant_id, occurrence_id, SCENARIO_KEY, task_ref, revision_id,
                user_id, due_at=now,
                expires_at=now + timedelta(seconds=TEST_RUN_LEASE_SECONDS + 300),
                authorization_epoch=epoch,
            )
        cursor.execute(
            """
            UPDATE desktop_automation_runs
            SET state = 'running', device_id = %s,
                lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                fence_token = fence_token + 1,
                claimed_at = COALESCE(claimed_at, NOW()), started_at = COALESCE(started_at, NOW())
            WHERE id = %s AND tenant_id = %s AND state = 'pending'
            """,
            (device_id, TEST_RUN_LEASE_SECONDS, run_id, tenant_id),
        )
        return run_id

    @staticmethod
    def _load_binding_on(cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:
        try:
            _uuid.UUID(str(binding_id))
        except (ValueError, TypeError, AttributeError):
            return None
        cursor.execute(
            """
            SELECT id, tenant_id, user_id, device_id, account_binding_id, label,
                   identity_evidence_ref, identity_version, state, verified_at
            FROM bs_weixin_marketing_group_bindings
            WHERE tenant_id = %s AND id = %s
            """,
            (tenant_id, str(binding_id)),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    # ==================== group-searches ====================

    def create_group_search(
        self,
        tenant_id: str,
        user_id: str,
        payload: GroupSearchCreateInput,
        *,
        dedupe_seed: str,
        now: Optional[datetime] = None,
        idempotency: Any = None,
    ) -> Dict[str, Any]:
        """创建异步群搜索任务（202）：入队 weixin_chat_search 到指定设备"""
        now = _aware(now or utcnow())
        device = _require_active_device(tenant_id, user_id, payload.device_id)
        device_id = str(device["id"])
        request_id = str(_uuid.uuid4())
        invocation = _enqueue_read_op(
            tenant_id=tenant_id,
            user_id=user_id,
            device_id=device_id,
            tool_name=TOOL_WEIXIN_CHAT_SEARCH,
            arguments={
                "keyword": payload.keyword,
                "request_id": request_id,
                "limit": SEARCH_RESULT_LIMIT,
            },
            business_kind=BUSINESS_KIND_GROUP_SEARCH,
            business_ref={"device_id": device_id},
            dedupe_key=f"group-search:{dedupe_seed}",
            now=now,
        )
        result = {
            "search_id": str(invocation["id"]),
            "device_id": device_id,
            "status": _map_search_status(str(invocation.get("state") or "queued")),
        }
        with get_db_connection() as conn:
            cursor = conn.cursor()
            _insert_weixin_audit_on(
                cursor, tenant_id, AUDIT_GROUP_SEARCH_REQUESTED, user_id=user_id,
                details={
                    "invocation_id": str(invocation["id"]),
                    "device_id": device_id,
                    "keyword_hash": sha256_hex(payload.keyword),
                },
            )
            if idempotency is not None:
                idempotency.write_on(cursor, status_code=202, data=result)
            conn.commit()
        return result

    def get_group_search(self, tenant_id: str, user_id: str, search_id: str) -> Dict[str, Any]:
        """搜索任务详情：pending/running/succeeded/failed + 候选 items（带 target_ref）"""
        invocation = lt_repository.get_invocation(search_id, tenant_id)
        if (
            invocation is None
            or invocation.get("business_kind") != BUSINESS_KIND_GROUP_SEARCH
            or str(invocation.get("user_id")) != user_id
        ):
            raise NotFoundError("搜索记录不存在")
        state = str(invocation.get("state") or "")
        result: Dict[str, Any] = {
            "search_id": str(invocation["id"]),
            "device_id": str(invocation.get("device_id") or ""),
            "status": _map_search_status(state),
            "created_at": invocation.get("created_at"),
            "finished_at": invocation.get("finished_at"),
        }
        if state == "succeeded":
            data = _result_data(invocation)
            result["items"] = _candidate_items(invocation)
            result["truncated"] = bool(data.get("truncated"))
        elif state in lt_repository.TERMINAL_STATES:
            result["error_code"] = invocation.get("error_code") or state
            result["error_message"] = invocation.get("error_message")
        return result

    # ==================== group-bindings ====================

    def create_group_binding(
        self,
        tenant_id: str,
        user_id: str,
        payload: GroupBindingCreateInput,
        *,
        idempotency: Any = None,
    ) -> Dict[str, Any]:
        """从搜索候选创建绑定（pending_verification；候选不自动建绑定的人工选择落点）"""
        search = lt_repository.get_invocation(payload.search_id, tenant_id)
        if (
            search is None
            or search.get("business_kind") != BUSINESS_KIND_GROUP_SEARCH
            or str(search.get("user_id")) != user_id
        ):
            raise NotFoundError("搜索记录不存在")
        if search.get("state") != "succeeded":
            raise WeixinValidationError("搜索未成功完成，不能从候选创建绑定")
        if str(search.get("device_id") or "") != payload.device_id:
            raise WeixinValidationError("候选不属于指定设备的搜索结果")
        items = _candidate_items(search)
        if not any(str(i.get("target_ref") or "") == payload.target_ref for i in items):
            raise WeixinValidationError("target_ref 不在该搜索的候选集合中")
        device = _require_active_device(tenant_id, user_id, payload.device_id)
        account_binding_id: Optional[str] = None
        if payload.account_binding_id:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, user_id, status FROM bs_weixin_marketing_account_bindings "
                    "WHERE tenant_id = %s AND id = %s",
                    (tenant_id, payload.account_binding_id),
                )
                row = cursor.fetchone()
            if row is None or str(row["user_id"]) != user_id:
                raise WeixinValidationError("账号绑定不存在或不可用")
            if row["status"] != ACCOUNT_BINDING_STATUS_ACTIVE:
                raise WeixinValidationError(f"账号绑定不可用: status={row['status']}")
            account_binding_id = payload.account_binding_id

        binding_id = str(_uuid.uuid4())
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bs_weixin_marketing_group_bindings
                    (id, tenant_id, user_id, device_id, account_binding_id, label,
                     identity_evidence_ref, identity_version, state)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, '0', %s)
                RETURNING id, created_at
                """,
                (
                    binding_id, tenant_id, user_id, str(device["id"]), account_binding_id,
                    payload.label, GROUP_BINDING_STATE_PENDING,
                ),
            )
            row = cursor.fetchone()
            _insert_weixin_audit_on(
                cursor, tenant_id, AUDIT_GROUP_BINDING_CREATED, user_id=user_id,
                details={
                    "binding_id": binding_id,
                    "search_id": payload.search_id,
                    "target_ref": payload.target_ref,
                    "device_id": str(device["id"]),
                },
            )
            result = {
                "binding_id": str(row["id"]),
                "state": GROUP_BINDING_STATE_PENDING,
                "device_id": str(device["id"]),
                "account_binding_id": account_binding_id,
                "label": payload.label,
                "target_ref": payload.target_ref,
                "search_id": payload.search_id,
                "created_at": row["created_at"],
            }
            if idempotency is not None:
                idempotency.write_on(cursor, status_code=200, data=result)
            conn.commit()
        return result

    def list_group_bindings(
        self,
        tenant_id: str,
        user_id: str,
        *,
        state: Optional[str] = None,
        device_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        filters = ["tenant_id = %s", "user_id = %s"]
        params: List[Any] = [tenant_id, user_id]
        if state:
            filters.append("state = %s")
            params.append(state)
        if device_id:
            filters.append("device_id = %s")
            params.append(device_id)
        where = " AND ".join(filters)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COUNT(*) AS c FROM bs_weixin_marketing_group_bindings WHERE {where}",
                tuple(params),
            )
            total = int(cursor.fetchone()["c"])
            cursor.execute(
                f"""
                SELECT id, device_id, account_binding_id, label, identity_evidence_ref,
                       identity_version, state, verified_at, created_at, updated_at
                FROM bs_weixin_marketing_group_bindings
                WHERE {where}
                ORDER BY created_at DESC LIMIT %s OFFSET %s
                """,
                (*params, page_size, (page - 1) * page_size),
            )
            items = [dict(r) for r in cursor.fetchall()]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def verify_group_binding(
        self,
        tenant_id: str,
        binding_id: str,
        user_id: str,
        *,
        dedupe_seed: str,
        now: Optional[datetime] = None,
        wait_seconds: Optional[float] = None,
        idempotency: Any = None,
    ) -> Dict[str, Any]:
        """绑定核验（只读，经 local_tool 队列）：定位群 + 标题精确比对。

        状态机：pending（=pending_verification）→ complete（唯一精确命中，=verified）/
        rejected（同名多命中，候选冲突终态）；设备离线/工具失败/超时/零精确命中 →
        抛 VerifyFailedError（绑定保持 pending，可重试）——不在服务端伪造通过。
        P3 复审 P1-2：唯一性判定前先过候选集合完整性门槛（§6.1 完整候选集合/明确
        截断标志）——显式 truncated/complete 标志按标志，无标志且条数 ≥ 请求 limit
        视为可能截断；不完整时不判 complete/rejected，保持 pending 可重试。
        证据引用 identity_evidence_ref = weixin-bind-evidence:<verify 搜索 invocation id>。
        """
        now = _aware(now or utcnow())
        with get_db_connection() as conn:
            cursor = conn.cursor()
            binding = self._load_binding_on(cursor, tenant_id, binding_id)
        if binding is None or str(binding.get("user_id")) != user_id:
            raise NotFoundError("群绑定不存在")
        if binding.get("state") not in GROUP_BINDING_VERIFIABLE_STATES:
            raise ConflictError(f"绑定状态不可核验: {binding.get('state')}")
        device_id = str(binding.get("device_id") or "")
        if not device_id:
            raise ConflictError("绑定未关联设备，不可核验")
        device = _require_active_device(tenant_id, user_id, device_id)

        keyword = str(binding.get("label") or "")
        invocation = _enqueue_read_op(
            tenant_id=tenant_id,
            user_id=user_id,
            device_id=str(device["id"]),
            tool_name=TOOL_WEIXIN_CHAT_SEARCH,
            arguments={
                "keyword": keyword,
                "request_id": str(_uuid.uuid4()),
                "limit": SEARCH_RESULT_LIMIT,
                "purpose": "binding_verify",
            },
            business_kind=BUSINESS_KIND_BINDING_VERIFY,
            business_ref={"binding_id": str(binding["id"]), "device_id": str(device["id"])},
            dedupe_key=f"binding-verify:{dedupe_seed}",
            now=now,
        )
        invocation_id = str(invocation["id"])
        final = _wait_invocation_terminal(tenant_id, invocation_id, wait_seconds=wait_seconds)
        state = str(final.get("state") or "")
        if state not in lt_repository.TERMINAL_STATES:
            _cancel_read_op(tenant_id, invocation_id)
            raise VerifyFailedError("设备未在时限内返回核验结果（设备离线或忙碌），可重试")
        if state != "succeeded":
            raise VerifyFailedError(
                f"核验工具执行失败（{final.get('error_code') or state}），绑定保持待核验，可重试"
            )
        data = _result_data(final)
        items = _candidate_items(final)
        # P1-2 完整性门槛：候选集合不完整/可能截断时禁止唯一性判定（同名或依据
        # 变化即阻断——§6.1），绑定保持 pending
        incomplete_reason = _candidates_incomplete_reason(data, items)
        if incomplete_reason:
            raise VerifyFailedError(f"{incomplete_reason}，绑定保持待核验，可重试")
        exact = [i for i in items if _normalize_title(i.get("title")) == _normalize_title(keyword)]
        evidence_ref = f"{BINDING_EVIDENCE_NAMESPACE}:{invocation_id}"
        if not exact:
            raise VerifyFailedError(
                "搜索结果中无标题精确一致的群（0 命中），绑定保持待核验，可重试"
            )
        if len(exact) > 1:
            new_state = "rejected"
            outcome = "rejected"
            reason = "candidate_conflict"
        else:
            new_state = GROUP_BINDING_STATE_COMPLETE
            outcome = "verified"
            reason = "unique_exact_match"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_weixin_marketing_group_bindings
                SET state = %s, identity_evidence_ref = %s, verified_at = NOW(),
                    updated_at = NOW()
                WHERE tenant_id = %s AND id = %s AND state = %s
                RETURNING verified_at
                """,
                (new_state, evidence_ref, tenant_id, str(binding["id"]), GROUP_BINDING_STATE_PENDING),
            )
            row = cursor.fetchone()
            if row is None:
                conn.rollback()
                raise ConflictError("绑定状态已变化（并发核验），请刷新后重试")
            _insert_weixin_audit_on(
                cursor, tenant_id,
                AUDIT_GROUP_BINDING_VERIFIED if outcome == "verified" else AUDIT_GROUP_BINDING_REJECTED,
                user_id=user_id,
                details={
                    "binding_id": str(binding["id"]),
                    "verify_invocation_id": invocation_id,
                    "identity_evidence_ref": evidence_ref,
                    "exact_matches": len(exact),
                    "reason": reason,
                },
            )
            result = {
                "binding_id": str(binding["id"]),
                "state": new_state,
                "result": outcome,
                "reason": reason,
                "verify_search_id": invocation_id,
                "identity_evidence_ref": evidence_ref,
                "exact_matches": len(exact),
                "verified_at": row["verified_at"],
            }
            if idempotency is not None:
                idempotency.write_on(cursor, status_code=200, data=result)
            conn.commit()
        logger.info(
            f"后端日志：weixin_marketing 绑定核验 tenant={tenant_id} binding={binding['id']} "
            f"result={outcome} exact={len(exact)} evidence={invocation_id}"
        )
        return result

    # ==================== devices ====================

    def list_devices(self, tenant_id: str, user_id: str) -> Dict[str, Any]:
        """属主设备列表 + 在线状态 + weixin provider 可用性（复用 local_tools 只读查询）"""
        devices = lt_repository.list_devices(tenant_id, user_id)
        # last_seen_at 为 naive TIMESTAMP（local_tool_devices 既有列），与 local_tools
        # api.py 同口径以本地时钟比较
        now_naive = datetime.now()
        items = []
        for d in devices:
            last_seen = d.get("last_seen_at")
            online = False
            if last_seen is not None:
                seen = last_seen.replace(tzinfo=None) if last_seen.tzinfo else last_seen
                online = (now_naive - seen).total_seconds() <= 30
            items.append(
                {
                    "device_id": str(d["id"]),
                    "name": d.get("name"),
                    "platform": d.get("platform"),
                    "runtime_version": d.get("runtime_version"),
                    "selected": bool(d.get("selected")),
                    "status": d.get("status"),
                    "online": online,
                    "last_seen_at": last_seen,
                    "created_at": d.get("created_at"),
                    "weixin": _weixin_capability_view(d.get("capabilities_json")),
                }
            )
        return {"items": items, "total": len(items)}

    def preflight_device(
        self,
        tenant_id: str,
        device_id: str,
        user_id: str,
        *,
        dedupe_seed: str,
        now: Optional[datetime] = None,
        wait_seconds: Optional[float] = None,
        idempotency: Any = None,
    ) -> Dict[str, Any]:
        """设备预检：经 local_tool 队列下发只读 weixin_probe，等待回包返回环境矩阵"""
        now = _aware(now or utcnow())
        device = _require_active_device(tenant_id, user_id, device_id)
        invocation = _enqueue_read_op(
            tenant_id=tenant_id,
            user_id=user_id,
            device_id=str(device["id"]),
            tool_name=TOOL_WEIXIN_PROBE,
            arguments={"request_id": str(_uuid.uuid4()), "purpose": "preflight"},
            business_kind=BUSINESS_KIND_DEVICE_PREFLIGHT,
            business_ref={"device_id": str(device["id"])},
            dedupe_key=f"device-preflight:{dedupe_seed}",
            now=now,
        )
        invocation_id = str(invocation["id"])
        final = _wait_invocation_terminal(tenant_id, invocation_id, wait_seconds=wait_seconds)
        state = str(final.get("state") or "")
        if state not in lt_repository.TERMINAL_STATES:
            _cancel_read_op(tenant_id, invocation_id)
            raise PreflightFailedError("设备未在时限内返回预检结果（设备离线或忙碌），可重试")
        if state != "succeeded":
            raise PreflightFailedError(
                f"预检工具执行失败（{final.get('error_code') or state}），可重试"
            )
        environment = _result_data(final)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            _insert_weixin_audit_on(
                cursor, tenant_id, AUDIT_DEVICE_PREFLIGHT_REQUESTED, user_id=user_id,
                details={"device_id": str(device["id"]), "invocation_id": invocation_id},
            )
            result = {
                "device_id": str(device["id"]),
                "invocation_id": invocation_id,
                "environment": environment,
                "finished_at": final.get("finished_at"),
            }
            if idempotency is not None:
                idempotency.write_on(cursor, status_code=200, data=result)
            conn.commit()
        return result
