"""wecom_personal_rpa 渠道配置的凭证加密/解密/脱敏 codec

tenant_channel_configs.config JSON 列中，wecom_personal_rpa 类型配置含 5 个敏感字段：
  - archive_secret（会话存档 secret，server 模式拉 API 用）
  - private_key（RSA 私钥 PEM，server 模式解密 encrypt_chat_msg 用）
  - token（回调验签，server 模式）
  - encoding_aes_key（回调 AES 解密，server 模式）
  - client_secret（客户端 HMAC 密钥，client 模式，第一期不开放但保留）

这些字段必须 Fernet 加密后才能写入 DB（复用 secret_crypto.encrypt_secret）。
非敏感字段（corp_id / listen_mode / last_seq / 时间戳等）保持明文。

调用约定：
- channel_config_db.create / update 写入前调 encrypt_sensitive_fields
- 服务端内部读后调 decrypt_sensitive_fields 拿明文
- API 响应给前端时调 mask_sensitive_fields（仅展示掩码）

相关设计：docs/system/wecom-personal-rpa-server-archive-listener-design.md §4.2 / §8
"""

import json
from typing import Any, Dict

from src.channels.wecom_personal_rpa import secret_crypto

# 敏感字段白名单（写入前必须加密，读取时按需解密/脱敏）
SENSITIVE_KEYS = (
    "archive_secret",
    "private_key",
    "token",
    "encoding_aes_key",
    "client_secret",
)

# 第一期强制 listen_mode='server'，后端忽略请求中的其他值（防 API 绕过）
FORCED_LISTEN_MODE = "server"


def _mask_value(value: str) -> str:
    """把明文转成 ``***xxxxx`` 形式的掩码（仅保留末尾 4 位）。"""
    if not value or not isinstance(value, str):
        return ""
    if len(value) <= 4:
        return "***"
    return f"***{value[-4:]}"


def encrypt_sensitive_fields(plain_config: Dict[str, Any]) -> Dict[str, Any]:
    """对配置 dict 中的敏感字段做 Fernet 加密，返回新的 dict（明文字段原样保留）。

    写入 DB 前调用。空值跳过（不加密 None / 空串），保留原值由调用方按需处理。

    同时强制设置 ``listen_mode = 'server'``（第一期 MVP 锁定，防 API 绕过）。
    """
    result: Dict[str, Any] = dict(plain_config or {})
    for key in SENSITIVE_KEYS:
        val = result.get(key)
        if isinstance(val, str) and val:
            # 已经是密文（旧记录读取后再写入的场景）：跳过二次加密
            if not _looks_like_ciphertext(val):
                result[key] = secret_crypto.encrypt_secret(val)
    # 强制 server：第一期 MVP 锁定
    result["listen_mode"] = FORCED_LISTEN_MODE
    return result


def decrypt_sensitive_fields(encrypted_config: Dict[str, Any]) -> Dict[str, Any]:
    """解密配置 dict 中的敏感字段，返回含明文的新 dict。

    服务端内部读取后调用（fetcher / poller / callback_handler / verify 等需要明文凭证的场景）。
    """
    result: Dict[str, Any] = dict(encrypted_config or {})
    for key in SENSITIVE_KEYS:
        val = result.get(key)
        if isinstance(val, str) and val and _looks_like_ciphertext(val):
            try:
                # secret_crypto.decrypt_secret 返回 bytes
                result[key] = secret_crypto.decrypt_secret(val).decode("utf-8")
            except Exception:
                # 解密失败保留原值（上游会在使用时抛出更明确的错误）
                # 不在此处吞掉异常的细节，但保证 list_by_tenant 等批量读取不会因单条失败崩
                result[key] = ""
    return result


def mask_sensitive_fields(plain_or_encrypted_config: Dict[str, Any]) -> Dict[str, Any]:
    """把敏感字段变成 ``***xxxxx`` 掩码，用于 API 响应给前端。

    会自动判断字段是明文还是密文：
      - 明文：取末 4 位生成掩码
      - 密文：直接返回 ``***``（无法解出明文，仅表示「已设置」）
      - 空：返回空串
    """
    result: Dict[str, Any] = dict(plain_or_encrypted_config or {})
    for key in SENSITIVE_KEYS:
        val = result.get(key)
        if not isinstance(val, str) or not val:
            result[key] = ""
        elif _looks_like_ciphertext(val):
            # 密文不脱敏（无法看到末尾），统一显示为 ***
            result[key] = "***"
        else:
            result[key] = _mask_value(val)
    return result


def _looks_like_ciphertext(value: str) -> bool:
    """启发式判断：Fernet 密文以 ``gAAAAA`` 开头（urlsafe base64 编码的版本前缀）。

    用来区分「明文」和「密文」，避免对密文做二次加密或对明文做解密。

    Fernet 格式：版本字节(0x80) + 时间戳(8B) + IV(16B) + 密文 + HMAC，
    base64 urlsafe encode 后总是以 ``gAAAAA`` 开头（前 6 个字符固定）。
    """
    return value.startswith("gAAAAA")


def is_server_mode(config: Dict[str, Any]) -> bool:
    """判断配置是否处于 server 模式。

    第一期 MVP：永远返回 True（后端强制覆盖 listen_mode='server'）。
    保留此函数是为了未来开放 client 模式时，调用方代码无需大改。
    """
    listen_mode = (config or {}).get("listen_mode", FORCED_LISTEN_MODE)
    return listen_mode == FORCED_LISTEN_MODE


def validate_required_fields(config: Dict[str, Any]) -> Dict[str, str]:
    """根据 listen_mode 动态校验必填字段，返回 ``{字段名: 中文描述}`` 形式的缺失字段映射。

    第一期仅 server 模式会被触发，client 分支保留为未来开放做准备。
    """
    config = config or {}
    listen_mode = config.get("listen_mode", FORCED_LISTEN_MODE)

    if listen_mode == "server":
        required = {
            "corp_id": "企业ID（CorpID，在「我的企业」页面获取）",
            "archive_secret": "会话存档 Secret（在「会话内容存档 → API 基本信息」获取）",
            "private_key": "RSA 私钥（在「会话内容存档 → 生成密钥对」下载 .pem 文件）",
            "token": "回调 Token（在「会话内容存档 → 接收消息服务器」配置）",
            "encoding_aes_key": "EncodingAESKey（43 字符 Base64，企微后台生成）",
        }
    else:
        # 第一期永远不会进入此分支（后端强制 server），保留为未来开放做准备
        required = {
            "corp_id": "企业ID（CorpID）",
            "client_secret": "客户端 HMAC 密钥",
        }

    return {k: v for k, v in required.items() if not config.get(k)}
