"""企业微信个人账号 RPA 渠道包

共享契约层：
- schemas.py：线上 JSON 协议的权威 Pydantic 模型与常量
- db.py：5 张服务端表的 CRUD 访问层

下游实现模块（auth / router / message / action_client / adapter / connection / management）
在阶段2并行实现，统一从本包导入 schemas 与 db 函数。
"""

from src.channels.wecom_personal_rpa import db, schemas

# 协议常量
from src.channels.wecom_personal_rpa.schemas import (
    HEADER_CLIENT_ID,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    NONCE_TTL_SECONDS,
    PROTOCOL_VERSION,
    SIGNATURE_ALGORITHM,
    TIMESTAMP_TOLERANCE_SECONDS,
)

# 入站
from src.channels.wecom_personal_rpa.schemas import (
    RpaAttachment,
    RpaCallbackEnvelope,
    RpaMessagePayload,
    RpaStatusPayload,
    RpaActionResultPayload,
)

# 出站
from src.channels.wecom_personal_rpa.schemas import (
    ActionEnvelope,
    HandoffAction,
    NoopAction,
    RpaAction,
    SendFileAction,
    SendImageAction,
    SendTextAction,
)

# 配置 / 错误
from src.channels.wecom_personal_rpa.schemas import (
    ErrorCode,
    RpaConfigResponse,
    RpaErrorResponse,
    RpaRateLimits,
)

__all__ = [
    # 子模块
    "db",
    "schemas",
    # 协议常量
    "HEADER_CLIENT_ID",
    "HEADER_NONCE",
    "HEADER_SIGNATURE",
    "HEADER_TIMESTAMP",
    "NONCE_TTL_SECONDS",
    "PROTOCOL_VERSION",
    "SIGNATURE_ALGORITHM",
    "TIMESTAMP_TOLERANCE_SECONDS",
    # 入站
    "RpaAttachment",
    "RpaCallbackEnvelope",
    "RpaMessagePayload",
    "RpaStatusPayload",
    "RpaActionResultPayload",
    # 出站
    "ActionEnvelope",
    "HandoffAction",
    "NoopAction",
    "RpaAction",
    "SendFileAction",
    "SendImageAction",
    "SendTextAction",
    # 配置 / 错误
    "ErrorCode",
    "RpaConfigResponse",
    "RpaErrorResponse",
    "RpaRateLimits",
]
