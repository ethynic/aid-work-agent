"""wechat_mp 渠道配置的敏感字段加密/解密/脱敏 codec（设计 §4）。

tenant_channel_configs.config JSON 列中，wechat_mp 类型配置含以下敏感字段：
  - ``secret``（公众号 AppSecret，P3 接口通道用，字段先留好）
  - ``encoding_aes_key``（回调安全模式 AES 解密，43 字符 Base64）
  - ``callback_token``（回调验签 Token，默认服务端生成；WP11 起支持 3~32 位字母数字自定义）
  - ``list_session_token``（清单源扫码会话 token，WP13；bizlogin 登录后从 redirect_url 提取）
  - ``list_session_cookie``（清单源扫码会话 cookie 全量串，WP13；httpx jar 序列化 "k=v; ..."）

这些字段必须 Fernet 加密后才能写入 DB（复用公共模块 src.core.secret_crypto）。
非敏感字段保持明文，其中 WP13 清单源新增：
  - ``list_session_at`` / ``list_session_expire_at``（扫码时间 / 会话预计过期时间≈+96h，ISO 字符串）
  - ``list_account_nickname``（登录号身份，用于展示与防错绑）
  - ``list_sync_status``（active / expiring / expired / account_error）
  - ``list_sync_mode``（auto_all 全部自动入库 / manual 新文章落 pending_manual 待勾选）
  - ``list_last_sync_at``（最近一轮清单对账完成时间）
WP13-r1 新增（设计 §3.4 首次回填上限）：
  - ``list_sync_max_articles``（首次回填子篇数上限，int 1~500 默认 100，租户偏好——
    解绑不清除，语义对齐 sync_interval_hours；读写两侧均经 clamp_list_sync_max_articles 钳制）
  - ``list_backfill_done``（bool，首次回填已完成标记；运行时状态，解绑清除，
    重绑（重扫码，含换号）一律重置 False 重新按上限回填）
其余非敏感字段（appid / original_id / enabled / sync_interval_hours / 三态字段等）保持明文。

与 RPA credential_codec 的差异：本 codec **不带 listen_mode 副作用**，
且敏感字段**不允许清空**（缺失/空串/掩码/null 一律保留旧值，由 channel_config_db 处理）。

调用约定：
- channel_config_db.create / update 写入前调 encrypt_sensitive_fields
- 服务端内部读取（回调验签/AES 解密）后调 decrypt_sensitive_fields 拿明文
- API 响应给前端时调 mask_sensitive_fields（仅展示掩码）
"""

import re
from typing import Any, Dict, Optional

from loguru import logger

from src.core import secret_crypto

# 敏感字段白名单（写入前必须加密，读取时按需解密/脱敏）
# WP13：清单源扫码会话凭据（token/cookie）等同 AppSecret 级敏感字段，
# 加密存储 + API 响应一律掩码，channel_config_db 的「敏感字段不允许清空」
# 保护与 update_config_field 写入护栏自动覆盖这两个键。
SENSITIVE_KEYS = (
    "secret",
    "encoding_aes_key",
    "callback_token",
    "list_session_token",
    "list_session_cookie",
)

# WP13 清单源明文字段（状态/展示用，不含凭据；仅作文档化清单，便于排查）
# 注：list_backfill_done 属运行时状态，解绑时随本清单清除（重绑重新按上限回填）；
# list_sync_max_articles 是租户偏好（语义同 sync_interval_hours），解绑保留不在此列。
LIST_PLAIN_FIELDS = (
    "list_session_at",
    "list_session_expire_at",
    "list_account_nickname",
    "list_sync_status",
    "list_sync_mode",
    "list_last_sync_at",
    "list_backfill_done",
)

# WP13-r1 首次回填上限（设计 §3.4）：默认 100，合法范围 1~500，按子篇计数
LIST_SYNC_MAX_ARTICLES_DEFAULT = 100
LIST_SYNC_MAX_ARTICLES_MIN = 1
LIST_SYNC_MAX_ARTICLES_MAX = 500

# 公众平台「服务器配置」Token 规则：3~32 位字母或数字（与公众平台后台校验口径一致）
_CALLBACK_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9]{3,32}$")


def is_valid_callback_token(token: Any) -> bool:
    """校验自定义回调 Token 是否符合公众平台规则（3~32 位字母数字）。

    WP11：callback_token 由「只能服务端生成」放宽为「可选自定义 + 留空服务端生成」，
    自定义值必须满足本规则；API 层转 400，DB 层兜底抛 ValueError。
    """
    return isinstance(token, str) and bool(_CALLBACK_TOKEN_PATTERN.match(token))


def clamp_list_sync_max_articles(value: Any) -> int:
    """钳制首次回填上限到 1~500（默认 100），越界/非法值钳到边界并记日志（WP13-r1）。

    读写两侧共用（ChannelConfigDB.create/update 写入前 + service 对账读取时），
    保证存量脏数据与任意写入路径都被规整，service 拿到的必是合法 int。
    """
    normalized: Optional[int]
    try:
        if value is None or value == "":
            normalized = None  # 缺省：直接取默认值（正常路径，不告警）
        else:
            number = int(value)  # bool 是 int 子类：True→1 落在界内，可接受
            normalized = number
    except (TypeError, ValueError):
        normalized = None
    if normalized is None:
        if value is not None:
            logger.bind(module="wechat_mp").warning(
                "wechat_mp list_sync_max_articles 非法值（取默认 {}）", LIST_SYNC_MAX_ARTICLES_DEFAULT
            )
        return LIST_SYNC_MAX_ARTICLES_DEFAULT
    if normalized < LIST_SYNC_MAX_ARTICLES_MIN:
        logger.bind(module="wechat_mp").warning(
            "wechat_mp list_sync_max_articles={} 低于下限，钳为 {}",
            normalized, LIST_SYNC_MAX_ARTICLES_MIN,
        )
        return LIST_SYNC_MAX_ARTICLES_MIN
    if normalized > LIST_SYNC_MAX_ARTICLES_MAX:
        logger.bind(module="wechat_mp").warning(
            "wechat_mp list_sync_max_articles={} 超过硬顶，钳为 {}",
            normalized, LIST_SYNC_MAX_ARTICLES_MAX,
        )
        return LIST_SYNC_MAX_ARTICLES_MAX
    return normalized


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
