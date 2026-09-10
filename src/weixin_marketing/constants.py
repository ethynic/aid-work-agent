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

# 只读探测工具（P3-A1：搜索/核验/预检经 local_tool 队列下发的设备侧工具名）
TOOL_WEIXIN_CHAT_SEARCH = "weixin_chat_search"
TOOL_WEIXIN_PROBE = "weixin_probe"

# test-send 的独立 task_ref 后缀（R54①）：试发 run 不占用生产 task subject，
# 适配器据此切换为 wxm:test:* 独立配额 scope；生产 task_ref 为纯 UUID 不会撞后缀
TEST_TASK_REF_SUFFIX = ":test"

# P3-A1 只读操作在 local_tool_invocations 的 business_kind（读链路：旧 /result 回传）
BUSINESS_KIND_GROUP_SEARCH = "weixin_group_search"
BUSINESS_KIND_BINDING_VERIFY = "weixin_binding_verify"
BUSINESS_KIND_DEVICE_PREFLIGHT = "weixin_device_preflight"

# 绑定核验证据命名空间：identity_evidence_ref = <namespace>:<verify 搜索 invocation id>
BINDING_EVIDENCE_NAMESPACE = "weixin-bind-evidence"

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

# pending：已录入未核验（= P3 契约的 pending_verification，verify 端点的唯一来源态）；
# complete：绑定核验通过（可执行发送，= verified 终态）；rejected：核验否决
# （P3：同名多命中候选冲突等身份依据被驳斥，终态）；disabled：人工停用。
GROUP_BINDING_STATE_PENDING = "pending"
GROUP_BINDING_STATE_COMPLETE = "complete"
GROUP_BINDING_STATE_REJECTED = "rejected"
GROUP_BINDING_STATE_DISABLED = "disabled"
GROUP_BINDING_STATES = (
    GROUP_BINDING_STATE_PENDING,
    GROUP_BINDING_STATE_COMPLETE,
    GROUP_BINDING_STATE_REJECTED,
    GROUP_BINDING_STATE_DISABLED,
)
# verify 允许的来源态（P3-A1：pending_verification → complete(verified)/rejected）
GROUP_BINDING_VERIFIABLE_STATES = (GROUP_BINDING_STATE_PENDING,)

# ==================== account_bindings 状态 ====================

ACCOUNT_BINDING_STATUS_ACTIVE = "active"
ACCOUNT_BINDING_STATUS_DISABLED = "disabled"
ACCOUNT_BINDING_STATUSES = (ACCOUNT_BINDING_STATUS_ACTIVE, ACCOUNT_BINDING_STATUS_DISABLED)

# ==================== assets 状态 ====================

# 素材删除为硬删（行+文件，R57），无软删中间态
ASSET_STATUS_ACTIVE = "active"

# P4-A：素材存储场景子目录（storage/tenants/{tid}/weixin-marketing/）
ASSET_STORAGE_SCENE = "weixin-marketing"

# MIME 实测白名单：PIL 解码头字节得到的 format → 规范 MIME 与扩展名。
# 客户端声明的 Content-Type 不作为存储依据（伪 mime 以实测为准）。
ASSET_MIME_BY_PIL_FORMAT = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
}
ASSET_EXTENSION_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}

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
# P3-A1 工作台动作（details 只存引用/摘要，不写群名/正文原文）
AUDIT_TEST_SEND_REQUESTED = "test_send_requested"
AUDIT_GROUP_SEARCH_REQUESTED = "group_search_requested"
AUDIT_GROUP_BINDING_CREATED = "group_binding_created"
AUDIT_GROUP_BINDING_VERIFIED = "group_binding_verified"
AUDIT_GROUP_BINDING_REJECTED = "group_binding_rejected"
AUDIT_DEVICE_PREFLIGHT_REQUESTED = "device_preflight_requested"
# P4-A 素材动作（details 只存引用/摘要，不写图片内容）
AUDIT_ASSET_UPLOADED = "asset_uploaded"
AUDIT_ASSET_DELETED = "asset_deleted"
AUDIT_ASSET_CLEANED = "asset_cleaned"

ACTOR_TYPE_USER = "user"
ACTOR_TYPE_SYSTEM = "system"

# ==================== 默认配置（config.py 覆盖入口）====================

DEFAULT_MAX_BLOCKS = 20
# interval 触发最小间隔秒数（max_interval_frequency：频率上限的倒数形式）
DEFAULT_MIN_INTERVAL_SECONDS = 300
DEFAULT_DISPATCH_BATCH_SIZE = 20
DEFAULT_RETENTION_DAYS = 90
# P4-A 素材上传约束（可经 yaml weixin_marketing 节覆盖）
DEFAULT_ASSET_MAX_BYTES = 10 * 1024 * 1024  # 单图 10 MiB
DEFAULT_ASSET_MAX_PIXELS = 25_000_000  # 宽×高上限（25 MP）
DEFAULT_ASSETS_CLEANUP_INTERVAL_SECONDS = 3600  # 过期素材清理 tick 间隔
DEFAULT_ASSETS_CLEANUP_BATCH = 100
DEFAULT_QUOTA_WINDOW_SECONDS = 3600
DEFAULT_QUOTA_TENANT_LIMIT = 100
DEFAULT_QUOTA_TASK_LIMIT = 30
DEFAULT_QUOTA_TARGET_LIMIT = 10
DEFAULT_QUOTA_ACCOUNT_LIMIT = 60

# P5 R59③：payloads/occurrences 数据保留期（data_retention_days 可配）与清理节奏
DEFAULT_DATA_RETENTION_DAYS = 30
DEFAULT_RETENTION_CLEANUP_INTERVAL_SECONDS = 86400
DEFAULT_RETENTION_CLEANUP_BATCH = 500
# P5 R59③：磁盘孤儿素材扫描（对照表行，孤立文件仅告警不动删；开关默认关）
DEFAULT_ASSETS_ORPHAN_SCAN_ENABLED = False
DEFAULT_ASSETS_ORPHAN_SCAN_INTERVAL_SECONDS = 86400
