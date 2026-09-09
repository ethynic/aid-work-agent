"""微信营销自动化枚举与常量（R41/R42）

- scenario_key 固定 weixin.fixed_content.v1；操作名固定 weixin_message_send_v2（v2 契约）。
- 状态值与数据库存储值一致（backend_dev.md 枚举规范）。
- 证据命名空间：结构性校验要求 evidence_ref 形如
  ``weixin-evidence:<request_id>:<seq>``（中段精确等于本次 request_id）；
  真实证据存在性校验属可插拔校验器接口（R43），结构性通过不等于真实验证。
"""

# ==================== 场景与操作 ====================

SCENARIO_KEY = "weixin.fixed_content.v1"
PROVIDER_KEY = "weixin"
OPERATION_MESSAGE_SEND = "weixin_message_send_v2"

# v2 payload_ref 规范（R41）：da:<scenario_key>:<revision_ref>:<block_position>
PAYLOAD_REF_PREFIX = "da:"
EVIDENCE_NAMESPACE = "weixin-evidence"

# ==================== automations 状态 ====================

AUTOMATION_STATUS_DRAFT = "draft"
AUTOMATION_STATUS_ACTIVE = "active"
AUTOMATION_STATUS_PAUSED = "paused"
AUTOMATION_STATUS_ARCHIVED = "archived"
AUTOMATION_STATUSES = (
    AUTOMATION_STATUS_DRAFT,
    AUTOMATION_STATUS_ACTIVE,
    AUTOMATION_STATUS_PAUSED,
    AUTOMATION_STATUS_ARCHIVED,
)

# ==================== revisions 状态 ====================

REVISION_STATUS_DRAFT = "draft"
REVISION_STATUS_PUBLISHED = "published"
REVISION_STATUS_SUPERSEDED = "superseded"
REVISION_STATUSES = (REVISION_STATUS_DRAFT, REVISION_STATUS_PUBLISHED, REVISION_STATUS_SUPERSEDED)

# ==================== content blocks ====================

BLOCK_KIND_TEXT = "text"
BLOCK_KIND_LINK = "link"
BLOCK_KIND_IMAGE = "image"
BLOCK_KINDS = (BLOCK_KIND_TEXT, BLOCK_KIND_LINK, BLOCK_KIND_IMAGE)

# 正文长度上限（验收矩阵「500 边界」：>500 拒绝；换行拒绝）
BLOCK_TEXT_MAX_LENGTH = 500
BLOCK_TEXT_FORBIDDEN_CHARS = ("\n", "\r", "\x00")

# ==================== group_bindings 状态机 ====================

# pending：已录入未核验（P2 允许存在）；complete：绑定核验通过（可执行发送）；
# disabled：人工停用。verify/probe 集成在 P3 交付（R39 范围裁决）
GROUP_BINDING_STATE_PENDING = "pending"
GROUP_BINDING_STATE_COMPLETE = "complete"
GROUP_BINDING_STATE_DISABLED = "disabled"
GROUP_BINDING_STATES = (GROUP_BINDING_STATE_PENDING, GROUP_BINDING_STATE_COMPLETE, GROUP_BINDING_STATE_DISABLED)

# ==================== account_bindings 状态 ====================

ACCOUNT_BINDING_STATUS_ACTIVE = "active"
ACCOUNT_BINDING_STATUS_DISABLED = "disabled"
ACCOUNT_BINDING_STATUSES = (ACCOUNT_BINDING_STATUS_ACTIVE, ACCOUNT_BINDING_STATUS_DISABLED)

# ==================== assets 状态 ====================

ASSET_STATUS_ACTIVE = "active"
ASSET_STATUS_PENDING_DELETE = "pending_delete"
ASSET_STATUSES = (ASSET_STATUS_ACTIVE, ASSET_STATUS_PENDING_DELETE)

# ==================== 审计 action（bs_weixin_marketing_audit_events）====================

AUDIT_AUTOMATION_CREATED = "automation_created"
AUDIT_DRAFT_UPDATED = "draft_updated"
AUDIT_AUTOMATION_PUBLISHED = "automation_published"
AUDIT_AUTOMATION_PAUSED = "automation_paused"
AUDIT_AUTOMATION_RESUMED = "automation_resumed"
AUDIT_AUTOMATION_ARCHIVED = "automation_archived"
AUDIT_MANUAL_RUN_REQUESTED = "manual_run_requested"
AUDIT_RUN_CANCEL_REQUESTED = "run_cancel_requested"
AUDIT_DELIVERY_RESOLVED = "delivery_resolved"
AUDIT_DELIVERY_RETRIED = "delivery_retried"

ACTOR_TYPE_USER = "user"
ACTOR_TYPE_SYSTEM = "system"

# ==================== 默认配置（config.py 覆盖入口）====================

DEFAULT_MAX_BLOCKS = 20
# interval 触发最小间隔秒数（max_interval_frequency：频率上限的倒数形式）
DEFAULT_MIN_INTERVAL_SECONDS = 300
DEFAULT_DISPATCH_BATCH_SIZE = 20
DEFAULT_RETENTION_DAYS = 90
DEFAULT_QUOTA_WINDOW_SECONDS = 3600
DEFAULT_QUOTA_TENANT_LIMIT = 100
DEFAULT_QUOTA_TASK_LIMIT = 30
DEFAULT_QUOTA_TARGET_LIMIT = 10
DEFAULT_QUOTA_ACCOUNT_LIMIT = 60
