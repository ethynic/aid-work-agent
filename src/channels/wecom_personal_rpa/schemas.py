"""企业微信个人账号 RPA 渠道线协议（Pydantic 权威模型）

本模块是服务端与 C# 客户端之间所有线上 JSON 协议的**唯一权威定义**。
下游实现 agent（auth / router / message / action_client / adapter / connection / management）
必须直接 import 本模块的模型，禁止重复定义同名字段。

字段名、字段类型、字面量取值一经锁定即视为契约，不得在不升 protocol_version 的前提下变更。

相关文档：docs/system/wecom-personal-rpa-protocol.md
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

# ===========================================================================
# 请求头常量（鉴权 / 防重放）
# ===========================================================================

# 客户端身份标识（注册时由服务端分配）
HEADER_CLIENT_ID = "X-Client-Id"
# Unix 秒级时间戳，服务端按 TIMESTAMP_TOLERANCE_SECONDS 容忍偏移
HEADER_TIMESTAMP = "X-Timestamp"
# 一次性随机串，服务端在 NONCE_TTL_SECONDS 内拒绝重复
HEADER_NONCE = "X-Nonce"
# HMAC-SHA256 签名
HEADER_SIGNATURE = "X-Signature"

# 时间戳容忍窗口（秒）。超过该窗口的请求一律拒绝，不论方向。
TIMESTAMP_TOLERANCE_SECONDS = 300
# nonce 防重放保留时长（秒）。与 Redis nonce set 的 TTL 对齐。
NONCE_TTL_SECONDS = 600

# 签名串构造（常量时间比较，禁止明文输出到日志 / 错误响应）：
#   sig = hex_lowercase(
#       hmac_sha256(
#           key   = client_secret_bytes,
#           msg   = client_id + timestamp + nonce + raw_body,
#                   （全部按 ASCII / UTF-8 原始字节拼接，不加分隔符）
#       )
#   )
# - raw_body 必须是收到的原始请求体字节，不得做任何 re-serialize。
# - client_secret 在服务端以 encrypted_secret 形式存储，解密后参与签名。
SIGNATURE_ALGORITHM = "HMAC-SHA256"


# ===========================================================================
# 入站回调信封（客户端 POST /callback 的 JSON body 顶层）
# ===========================================================================

EventType = Literal["message", "status", "action_result"]


class RpaCallbackEnvelope(BaseModel):
    """客户端上报事件的顶层信封。

    event_id 由客户端生成且必须全局稳定（同一逻辑事件重传时携带相同 event_id），
    服务端按 ``wecom_personal_rpa:{tenant_id}:{event_id}`` 在 channel_message_dedup
    表去重（详见 docs/system/wecom-personal-rpa-protocol.md §A）。
    """

    event_id: str = Field(..., description="客户端生成的全局稳定事件 ID，用于幂等去重")
    client_id: str = Field(..., description="发起上报的 RPA 客户端 ID（注册分配）")
    account_id: str = Field(..., description="事件归属的个人企微账号 ID")
    event_type: EventType = Field(..., description="事件类型，决定 payload 的结构")
    occurred_at: datetime = Field(..., description="事件在客户端发生的本地时间")
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="事件体，结构由 event_type 决定（见 RpaMessagePayload / RpaStatusPayload / RpaActionResultPayload）",
    )


# ===========================================================================
# event_type=message 的 payload
# ===========================================================================

ConversationType = Literal["internal_user", "internal_group", "external_user", "external_group"]
InboundMessageType = Literal["text", "image", "file", "voice", "video", "link"]


class RpaAttachment(BaseModel):
    """入站消息附件（图片 / 文件 / 语音 / 视频）。"""

    type: str = Field(..., description="附件类型：image / file / voice / video / link")
    url: str = Field(default="", description="附件下载地址（可由客户端短期签名托管，或由服务端后续解析）")
    name: Optional[str] = Field(default=None, description="附件文件名")
    size: Optional[int] = Field(default=None, description="附件字节数")
    mime_type: Optional[str] = Field(default=None, description="附件 MIME 类型")


class RpaMessagePayload(BaseModel):
    """入站聊天消息体。"""

    conversation_id: str = Field(..., description="客户端本地会话标识，用于路由")
    conversation_type: ConversationType = Field(..., description="会话类型，决定群/单聊、内外部语义")
    sender_display_name: str = Field(..., description="发送人显示名（可能重名，仅用于展示）")
    sender_stable_id: Optional[str] = Field(
        default=None,
        description="发送人稳定 ID（external_userid / userid / room_id），首版可为空",
    )
    message_type: InboundMessageType = Field(..., description="消息内容类型")
    text: Optional[str] = Field(default=None, description="文本内容（message_type=text 时必填，其余可空）")
    attachments: List[RpaAttachment] = Field(
        default_factory=list,
        description="附件列表（图片/文件/语音/视频），文本消息可为空",
    )


# ===========================================================================
# event_type=status 的 payload
# ===========================================================================

AccountStatus = Literal[
    "online",
    "offline",
    "need_login",
    "qr_expired",
    "account_limited",
    "desktop_locked",
    "window_not_visible",
    "paused",
    "recovering",
]


class RpaStatusPayload(BaseModel):
    """账号 / 桌面健康状态上报。"""

    status: AccountStatus = Field(..., description="账号当前状态枚举")
    account_display_name: Optional[str] = Field(default=None, description="账号显示名（在线 / 扫码时上报）")
    detail: Optional[str] = Field(default=None, description="状态补充说明（脱敏后文本，不含密钥/路径）")
    qr_image_ref: Optional[str] = Field(
        default=None,
        description="二维码短期上传引用（短期凭证，不得长期存储；日志中禁止打印）",
    )


# ===========================================================================
# event_type=action_result 的 payload
# ===========================================================================


class RpaActionResultPayload(BaseModel):
    """客户端执行服务端下发 action 后的回执。

    服务端按 ``wecom_personal_rpa:{tenant_id}:{action_result_id}`` 去重，
    防止网络重试导致重复记录。
    """

    request_id: str = Field(..., description="对应 ActionEnvelope.request_id")
    action_result_id: str = Field(..., description="客户端生成的回执唯一 ID，用于幂等去重")
    action_index: int = Field(..., description="该回执对应 actions 列表的下标（从 0 开始）")
    action_type: str = Field(..., description="回执对应的 action.type（send_text/send_image/...）")
    success: bool = Field(..., description="是否执行成功")
    error_code: Optional[str] = Field(default=None, description="失败时的错误码（见错误语义表）")
    error_message: Optional[str] = Field(
        default=None,
        description="失败时的可读说明，需脱敏（不含密钥/绝对路径）"
    )
    executed_at: datetime = Field(..., description="客户端实际执行完成时间")


# ===========================================================================
# 出站 actions（服务端 → 客户端）
# ===========================================================================


class SendTextAction(BaseModel):
    """发送文本消息。"""

    type: Literal["send_text"] = "send_text"
    text: str = Field(..., description="待发送的文本内容")


class SendImageAction(BaseModel):
    """发送图片。file_url 必须为短期签名 URL。"""

    type: Literal["send_image"] = "send_image"
    file_url: str = Field(..., description="图片短期签名下载 URL")
    filename: Optional[str] = Field(default=None, description="建议文件名（可空）")


class SendFileAction(BaseModel):
    """发送文件。file_url 必须为短期签名 URL。"""

    type: Literal["send_file"] = "send_file"
    file_url: str = Field(..., description="文件短期签名下载 URL")
    filename: str = Field(..., description="文件名（必填，企微文件发送需要）")


class NoopAction(BaseModel):
    """空动作：仅记录、不自动回复。"""

    type: Literal["noop"] = "noop"


class HandoffAction(BaseModel):
    """转人工：客户端暂停对应会话或账号。"""

    type: Literal["handoff"] = "handoff"
    reason: Optional[str] = Field(default=None, description="转人工原因（可空）")


# 判别联合：出站动作集合
RpaAction = Union[
    SendTextAction,
    SendImageAction,
    SendFileAction,
    NoopAction,
    HandoffAction,
]


class ActionEnvelope(BaseModel):
    """服务端下发给客户端的动作信封。

    - 在线客户端：通过 WebSocket 直接推送本信封。
    - 离线客户端：作为一行写入 wecom_rpa_action_outbox，由客户端拉取执行。
    """

    request_id: str = Field(..., description="本次下发的请求 ID（与回执 RpaActionResultPayload.request_id 对应）")
    session_id: str = Field(..., description="服务端会话 ID（wecom_personal_rpa:{account_id}:{route_key}）")
    account_id: str = Field(..., description="目标账号 ID")
    conversation_id: str = Field(..., description="客户端侧会话标识，用于定位企微会话窗口")
    actions: List[RpaAction] = Field(..., description="顺序执行的动作列表")


# ===========================================================================
# 配置下发
# ===========================================================================


class RpaRateLimits(BaseModel):
    """服务端下发的限速策略。"""

    per_minute: int = Field(default=5, description="单账号每分钟最大自动发送条数")
    per_day: int = Field(default=100, description="单账号每日最大自动发送条数")
    consecutive_failure_pause: int = Field(
        default=2,
        description="同一会话连续失败 N 次后自动暂停",
    )


class RpaConfigResponse(BaseModel):
    """GET /config 返回的客户端运行配置。"""

    protocol_version: str = Field(..., description="当前服务端线协议版本（与 RpaCallbackEnvelope 对齐）")
    min_client_version: str = Field(..., description="允许继续托管的最小客户端版本，低于此版本必须暂停")
    paused: bool = Field(default=False, description="服务端是否已整体暂停该客户端")
    paused_scope: Optional[Literal["tenant", "account", "conversation"]] = Field(
        default=None,
        description="暂停范围：租户级 / 账号级 / 会话级",
    )
    rate_limits: RpaRateLimits = Field(default_factory=RpaRateLimits, description="限速策略")
    server_time: datetime = Field(..., description="服务端当前时间，供客户端校准时钟漂移")
    # 回调路径定位参数（客户端据此拼接 callback / ws 路径）
    client_id: Optional[str] = Field(
        default=None,
        description="当前客户端 ID（与 X-Client-Id 一致），客户端用于构造 callback/ws 路径",
    )
    tenant_id: Optional[str] = Field(
        default=None,
        description="客户端归属租户 ID，用于构造 callback/ws 路径 t/{tenant_id}/...",
    )
    config_id: Optional[str] = Field(
        default=None,
        description="tenant_channel_configs 表记录 ID，用于构造 callback/ws 路径 .../callback/{config_id}",
    )


# ===========================================================================
# 错误响应
# ===========================================================================

ErrorCode = Literal[
    "auth_failed",
    "client_disabled",
    "account_paused",
    "conversation_needs_review",
    "agent_timeout",
    "unsupported_action",
    "bad_request",
    "internal_error",
]


class RpaErrorResponse(BaseModel):
    """所有 4xx / 5xx 错误统一信封。

    注意：``debug`` 字段在序列化前必须经过脱敏（参考 .claude/rules/backend_dev.md
    的 sanitize_error_info 思路），剔除 secret / token / 签名 / 绝对路径。
    schema 层只标注约束，实际脱敏由 auth / management 实现层负责。
    """

    error: ErrorCode = Field(..., description="错误码，客户端据此决定停止 / 暂停 / 重试 / 转人工")
    message: str = Field(..., description="面向客户端的可读错误说明（中文）")
    debug: Optional[str] = Field(
        default=None,
        description="调试信息（必须脱敏：剔除 secret/token/signature/绝对路径），生产环境可为空",
    )


# ===========================================================================
# 协议版本常量
# ===========================================================================

# 当前线协议版本。任何 breaking change 必须递进。
PROTOCOL_VERSION = "1.0.0"
