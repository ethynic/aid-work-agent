"""desktop_automation 枚举、常量与规范编码（P1-A，阶段宪章 R9-R12）

- effect/phase 模型：R10 —— delivery 聚合层 effect ∈ none|applied|unknown；
  Provider/Runtime phase includes submitted for server-authorized name chat only.
  verified means delivery evidence; submitted means command completion, not delivery.
- 触发键规范编码：R11 —— 外部 ID / request_id 先 sha256 再 base64url，杜绝分隔符碰撞。
- quota scope 固定顺序：R9 —— tenant < task < target < account < resource
  （按裁决显式列表实现，许可事务内按此顺序逐层加行锁）。
- 锁顺序：R12 —— subject(task) → schedule → occurrence/run，
  全部 FOR UPDATE（扫描路径 subject 用 SKIP LOCKED，发布/暂停用阻塞锁）。
"""

import base64
import hashlib
from datetime import datetime, timezone
from typing import Optional

# ==================== subject ====================

SUBJECT_KIND_TASK = "task"
SUBJECT_KIND_REVISION = "revision"
SUBJECT_KINDS = (SUBJECT_KIND_TASK, SUBJECT_KIND_REVISION)

# task subject 状态
TASK_STATUS_ACTIVE = "active"
TASK_STATUS_PAUSED = "paused"
# revision subject 状态
REVISION_STATUS_PUBLISHED = "published"
REVISION_STATUS_SUPERSEDED = "superseded"

# ==================== schedule ====================

SCHEDULE_KIND_TIME = "time"
SCHEDULE_KIND_EVENT = "event"
SCHEDULE_KINDS = (SCHEDULE_KIND_TIME, SCHEDULE_KIND_EVENT)

SCHEDULE_STATUS_ACTIVE = "active"
SCHEDULE_STATUS_PAUSED = "paused"
SCHEDULE_STATUS_FINISHED = "finished"

# miss 策略：默认 skip_overlap（新槽记 skipped，不积压无界队列，【计划 §3.1】）
MISS_POLICY_SKIP_OVERLAP = "skip_overlap"
MISS_POLICY_CATCH_UP_LATEST = "catch_up_latest"
MISS_POLICIES = (MISS_POLICY_SKIP_OVERLAP, MISS_POLICY_CATCH_UP_LATEST)

# ==================== occurrence ====================

TRIGGER_KIND_TIME = "time"
TRIGGER_KIND_EVENT = "event"
TRIGGER_KIND_MANUAL = "manual"
TRIGGER_KINDS = (TRIGGER_KIND_TIME, TRIGGER_KIND_EVENT, TRIGGER_KIND_MANUAL)

OCCURRENCE_STATUS_OPEN = "open"          # 已接纳（可能带 run）
OCCURRENCE_STATUS_SKIPPED = "skipped"    # skip_overlap 等原因未生成可执行 run

# ==================== run（含 §5.4 聚合终态） ====================

RUN_STATE_PENDING = "pending"
RUN_STATE_RUNNING = "running"
RUN_STATE_WAITING_DEVICE = "waiting_device"
RUN_TERMINAL_STATES = (
    "succeeded",
    "failed",
    "cancelled",
    "partial",
    "unknown",
    "expired",
)

# ==================== delivery ====================

# delivery 执行状态
DELIVERY_STATE_PENDING = "pending"
DELIVERY_STATE_DISPATCHED = "dispatched"          # 已 enqueue attempt，等待设备
DELIVERY_STATE_SUCCEEDED = "succeeded"            # applied + verified
DELIVERY_STATE_FAILED = "failed"
DELIVERY_STATE_UNKNOWN = "unknown"
DELIVERY_STATE_EXPIRED = "expired"
DELIVERY_STATE_SKIPPED = "skipped"                # 前序 unknown/取消后不再执行
DELIVERY_TERMINAL_STATES = (
    DELIVERY_STATE_SUCCEEDED,
    DELIVERY_STATE_FAILED,
    DELIVERY_STATE_UNKNOWN,
    DELIVERY_STATE_EXPIRED,
    DELIVERY_STATE_SKIPPED,
)

# effect（delivery 聚合层，R10）
EFFECT_NONE = "none"
EFFECT_APPLIED = "applied"
EFFECT_UNKNOWN = "unknown"
DELIVERY_EFFECTS = (EFFECT_NONE, EFFECT_APPLIED, EFFECT_UNKNOWN)

# phase（Provider/Runtime 状态，R10）
PHASE_PREPARED = "prepared"
PHASE_MAY_HAVE_STARTED = "may_have_started"
PHASE_VERIFIED = "verified"
PHASE_SUBMITTED = "submitted"  # Authorized name-chat command completed; delivery unverified.
PHASE_UNKNOWN = "unknown"
OPERATION_PHASES = (
    PHASE_PREPARED,
    PHASE_MAY_HAVE_STARTED,
    PHASE_VERIFIED,
    PHASE_SUBMITTED,
    PHASE_UNKNOWN,
)

# ==================== invocation（v2） ====================

# local_tool_invocations.business_kind：v2 场景执行链路标识（旧行 NULL = 聊天 proxy 链路）
BUSINESS_KIND_DESKTOP_AUTOMATION = "desktop_automation"

# v2 统一操作描述 protocol_version（R15）
OPERATION_PROTOCOL_V2 = 2

# invocation.write_phase（§2.1：许可发放后置 may_have_started）
WRITE_PHASE_NONE = "none"
WRITE_PHASE_MAY_HAVE_STARTED = "may_have_started"

# ==================== permit ====================

PERMIT_STATE_ISSUED = "issued"
PERMIT_STATE_CONSUMED = "consumed"
PERMIT_STATE_EXPIRED = "expired"
PERMIT_STATES = (PERMIT_STATE_ISSUED, PERMIT_STATE_CONSUMED, PERMIT_STATE_EXPIRED)

# ==================== quota（R9 固定顺序） ====================

QUOTA_SCOPE_TENANT = "tenant"
QUOTA_SCOPE_TASK = "task"
QUOTA_SCOPE_TARGET = "target"
QUOTA_SCOPE_ACCOUNT = "account"
QUOTA_SCOPE_RESOURCE = "resource"

# R9 裁决的固定加锁顺序（显式列表；许可事务内按 (顺序号, scope_id) 升序逐层 FOR UPDATE）
QUOTA_SCOPE_ORDER = (
    QUOTA_SCOPE_TENANT,
    QUOTA_SCOPE_TASK,
    QUOTA_SCOPE_TARGET,
    QUOTA_SCOPE_ACCOUNT,
    QUOTA_SCOPE_RESOURCE,
)
QUOTA_SCOPE_RANK = {name: idx for idx, name in enumerate(QUOTA_SCOPE_ORDER)}

# ==================== outbox / event / audit ====================

OUTBOX_STATE_PENDING = "pending"
OUTBOX_STATE_PROCESSING = "processing"
OUTBOX_STATE_DONE = "done"

EVENT_SOURCE_STATUS_ACTIVE = "active"
EVENT_SOURCE_STATUS_DISABLED = "disabled"

EVENT_STATE_RECEIVED = "received"
EVENT_STATE_PROCESSING = "processing"
EVENT_STATE_PROCESSED = "processed"


# ==================== 触发键规范编码（R11） ====================


def _b64url_sha256(text: str) -> str:
    """sha256 后 base64url 编码（去 padding）：外部 ID/request_id 无分隔符碰撞"""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _utc_seconds(dt: datetime) -> str:
    """ISO8601 UTC 秒精度（触发键分量；naive 输入按 UTC 解释）"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def time_trigger_key(revision_ref: str, scheduled_for_utc: datetime) -> str:
    """时间触发键：time:{revision_ref}:{scheduled_for_utc(ISO8601 UTC，秒精度)}"""
    return f"time:{revision_ref}:{_utc_seconds(scheduled_for_utc)}"


def event_trigger_key(source_ref: str, external_event_id: str) -> str:
    """事件触发键：event:{source_ref}:{base64url(sha256(external_event_id))}"""
    return f"event:{source_ref}:{_b64url_sha256(external_event_id)}"


def manual_trigger_key(request_id: str) -> str:
    """手动触发键：manual:{base64url(sha256(request_id))}；不改时间 schedule.next_fire_at"""
    return f"manual:{_b64url_sha256(request_id)}"


def parse_time_trigger_key(key: str) -> Optional[datetime]:
    """从时间触发键解析 scheduled_for_utc（对账/测试用；非时间键返回 None）"""
    if not key.startswith("time:"):
        return None
    parts = key.split(":", 2)
    if len(parts) != 3:
        return None
    try:
        return datetime.strptime(parts[2], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
