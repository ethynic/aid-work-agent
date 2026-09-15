"""wechat_mp 渠道配置的敏感字段加密/解密/脱敏 codec（设计 §4）。

tenant_channel_configs.config JSON 列中，wechat_mp 类型配置含以下敏感字段：
  - ``secret``（公众号 AppSecret，P3 接口通道用，字段先留好）
  - ``encoding_aes_key``（回调安全模式 AES 解密，43 字符 Base64）
  - ``callback_token``（回调验签 Token，创建时由服务端生成）

这些字段必须 Fernet 加密后才能写入 DB（复用公共模块 src.core.secret_crypto）。
非敏感字段（appid / original_id / enabled / sync_interval_hours / 三态字段等）保持明文。

与 RPA credential_codec 的差异：本 codec **不带 listen_mode 副作用**，
且敏感字段**不允许清空**（缺失/空串/掩码/null 一律保留旧值，由 channel_config_db 处理）。

调用约定：
- channel_config_db.create / update 写入前调 encrypt_sensitive_fields
- 服务端内部读取（回调验签/AES 解密）后调 decrypt_sensitive_fields 拿明文
- API 响应给前端时调 mask_sensitive_fields（仅展示掩码）
"""

from typing import Any, Dict

from src.core import secret_crypto

# 敏感字段白名单（写入前必须加密，读取时按需解密/脱敏）
SENSITIVE_KEYS = (
    "secret",
    "encoding_aes_key",
    "callback_token",
)


def encrypt_sensitive_fields(plain_config: Dict[str, Any]) -> Dict[str, Any]:
    """对配置 dict 中的敏感字段做 Fernet 加密，返回新的 dict（明文字段原样保留）。

    写入 DB 前调用。空值跳过（不加密 None / 空串），保留原值由调用方按需处理。
    已经是密文的值（``gAAAAA`` 前缀）跳过二次加密。
    """
    result: Dict[str, Any] = dict(plain_config or {})
    for key in SENSITIVE_KEYS:
        val = result.get(key)
        if isinstance(val, str) and val and not secret_crypto.looks_like_ciphertext(val):
            result[key] = secret_crypto.encrypt_secret(val)
    return result


def decrypt_sensitive_fields(encrypted_config: Dict[str, Any]) -> Dict[str, Any]:
    """解密配置 dict 中的敏感字段，返回含明文的新 dict。

    服务端内部读取后调用（回调验签 / AES 解密等需要明文的场景）。
    """
    result: Dict[str, Any] = dict(encrypted_config or {})
    for key in SENSITIVE_KEYS:
        val = result.get(key)
        if isinstance(val, str) and val and secret_crypto.looks_like_ciphertext(val):
            try:
                result[key] = secret_crypto.decrypt_secret(val).decode("utf-8")
            except Exception:
                # 解密失败置空（上游使用时给出更明确的错误），避免批量读取因单条失败崩
                result[key] = ""
    return result


def mask_sensitive_fields(plain_or_encrypted_config: Dict[str, Any]) -> Dict[str, Any]:
    """把敏感字段变成 ``***xxxxx`` 掩码，用于 API 响应给前端。

    明文取末 4 位掩码；密文统一显示 ``***``（仅表示「已设置」）；空返回空串。
    """
    result: Dict[str, Any] = dict(plain_or_encrypted_config or {})
    for key in SENSITIVE_KEYS:
        val = result.get(key)
        if not isinstance(val, str) or not val:
            result[key] = ""
        elif secret_crypto.looks_like_ciphertext(val):
            result[key] = "***"
        else:
            result[key] = secret_crypto.mask_value(val)
    return result
