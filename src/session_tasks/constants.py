"""端侧会话任务通用协议常量（C1）。

权威契约：docs/design/desktop-automation/edge-session-task-design.md §4/§9/§13。
本模块只放跨文件共享的枚举/错误码/默认值，不放业务逻辑。
"""
from __future__ import annotations

# 场景 key（由 weixin_conversation 提供；此处仅作默认值，避免反向 import）
DEFAULT_SCENARIO_KEY = "weixin.conversation.v1"

# 云端任务状态（设计 §4 状态表）
STATUS_DRAFT = "draft"
STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_HUMAN_REQUIRED = "human_required"
STATUS_BLOCKED = "blocked"
STATUS_COMPLETED = "completed"
STATUS_STOPPED = "stopped"

TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_STOPPED)
# 占用唯一约束覆盖的"未终结已发布"状态（draft 不占用会话）
OCCUPYING_STATUSES = (STATUS_ACTIVE, STATUS_PAUSED, STATUS_HUMAN_REQUIRED, STATUS_BLOCKED)

# 端侧 phase（设计 §4；由 Runtime 上报，云端仅记录投影）
PHASES = (
    "ready",
    "observing",
    "decision_pending",
    "send_ready",
    "executing",
    "waiting_peer",
    "waiting_schedule",
    "sync_pending",
    "blocked",
)

# 决策类型（设计 §9/§13.2）
DECISION_KIND_OPENING = "opening"
DECISION_KIND_REPLY = "reply"
DECISION_KIND_COMPLETION_REVIEW = "completion_review"
DECISION_KINDS = (DECISION_KIND_OPENING, DECISION_KIND_REPLY, DECISION_KIND_COMPLETION_REVIEW)

# 开场白合成批次保留值（普通批次 batch_id 必须是 UUID 字符串，禁用该值）
OPENING_BATCH_ID = "opening"

# 设备必须协商的**通用**能力（B1.2 九处 #1，设计 §4.2：场景发送能力由描述器
# required_send_capability 按 task.scenario_key 拼接，见 service._required_capabilities；
# 共享锁/写后证据随场景发送协议自带，不单列能力名）
REQUIRED_DEVICE_CAPABILITIES = ("session_task_v1", "session_observer_v1")

# 租约默认（设计 §4：lease=60s、renew=20s，配置项可调）
DEFAULT_LEASE_SECONDS = 60
DEFAULT_RENEW_SECONDS = 20

# 受控文本用途（设计 §13.3）
TEXT_PURPOSES = ("spec", "message", "decision", "event", "summary")

# 事件批上限（设计 §9 events 接口）
EVENTS_MAX_RECORDS = 100
EVENTS_MAX_BYTES = 256 * 1024

# 稳定错误码（envelope code，沿用项目命名风格）
ERR_VALIDATION_FAILED = "VALIDATION_FAILED"
ERR_NOT_FOUND = "NOT_FOUND"
ERR_CONFLICT = "CONFLICT"
ERR_CONVERSATION_IN_USE = "CONVERSATION_IN_USE"
ERR_STALE_ASSIGNMENT = "STALE_ASSIGNMENT"
ERR_LEASE_EXPIRED = "LEASE_EXPIRED"
ERR_EVENT_GAP = "EVENT_GAP"
ERR_EVENT_PAYLOAD_CONFLICT = "EVENT_PAYLOAD_CONFLICT"
ERR_CAPABILITY_MISSING = "CAPABILITY_MISSING"
ERR_FEATURE_DISABLED = "FEATURE_DISABLED"
ERR_CONFIRMATION_INVALID = "CONFIRMATION_INVALID"
ERR_BUDGET_EXCEEDED = "TASK_BUDGET_EXHAUSTED"
ERR_INSUFFICIENT_TENANT_CREDIT = "INSUFFICIENT_TENANT_CREDIT"
ERR_IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
ERR_CRYPTO_UNAVAILABLE = "CRYPTO_UNAVAILABLE"


class SessionTaskError(Exception):
    """服务层异常基类：message 面向用户（中文），code 为稳定错误码。"""

    def __init__(self, message: str, code: str = ERR_VALIDATION_FAILED, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class NotFoundError(SessionTaskError):
    """资源不存在或无权访问（跨租户/非属主统一 404，不泄露存在性）。"""

    def __init__(self, message: str = "任务不存在或无权访问"):
        super().__init__(message, ERR_NOT_FOUND, 404)


class ConflictError(SessionTaskError):
    def __init__(self, message: str, code: str = ERR_CONFLICT):
        super().__init__(message, code, 409)
