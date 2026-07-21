"""外向账号托管登录态存储（B0.5 登录态持久化扩展）。

提供 ``storage_state``（Playwright 标准 cookies+localStorage dict）的加密持久化与检索，
是巡检 web 连接器（B2 协议 / B3 知乎 / B6 小红书）跨 run 维持登录态的存储后端。

设计要点（对齐设计 §10 / B0 可行性 §3）：

- **加密**：``storage_state`` 含敏感会话 cookie/localStorage，必须 ``encryption_manager`` 加密入库；
  密钥版本 ``v1`` 字段供后续轮换。
- **不出现在日志/审计/Agent 上下文**：本模块所有日志只记 account_id/tenant_id/status/元数据
  （cookie_count 等），绝不打印 storage_state 明文或密文；异常路径同样不泄露。
- **租户隔离**：所有读写强制 ``(tenant_id, account_id)`` 过滤；``tenant_id`` 为 ``None`` 时
  走 ``tenant_id IS NULL`` 分支（非 SaaS 模式）。
- **状态机**：``active`` → ``expired``（失效，如登录验证未通过 / cookie 过期）/
  ``revoked``（吊销，如账号解绑）。``load_storage_state`` 仅返回 ``active`` 会话的 state。
- **不破解不绕过**（§3 红线）：本模块只存取登录态，不做任何登录验证。注入后是否仍处登录态
  由调用方（B2 连接器 ``ensure_logged_in``）判定；失效时调用方调 ``mark_expired`` 并转人工。

表 DDL 见 ``init_outbound_account_sessions_table``（幂等，由主控者统一接线进 init 流程）。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.db.encryption import encryption_manager


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_TABLE = "bs_outbound_account_sessions"
_KEY_VERSION = "v1"  # 加密密钥版本；后续轮换时迁移历史数据并提升版本号

# 会话状态枚举（与前端枚举保持一致；后续接线时同步 frontend/src/api/enums.ts）
STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_REVOKED = "revoked"


# ---------------------------------------------------------------------------
# 表初始化（幂等；由主控者统一接线进 init 流程）
# ---------------------------------------------------------------------------

_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    account_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    platform TEXT,
    storage_state_encrypted TEXT NOT NULL,
    storage_state_key_version TEXT DEFAULT '{_KEY_VERSION}',
    status TEXT DEFAULT '{STATUS_ACTIVE}',
    last_used_at TIMESTAMP,
    expired_reason TEXT,
    cookie_count INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_INDEX_TENANT_STATUS = (
    f"CREATE INDEX IF NOT EXISTS idx_outbound_account_sessions_tenant_status "
    f"ON {_TABLE}(tenant_id, status, updated_at DESC)"
)


def init_outbound_account_sessions_table(conn) -> None:
    """幂等创建托管登录态表。

    由主控者合并时统一接入 init 流程（如 ``_init_postgresql`` 或 ``init_social_media_tables``），
    本模块不直接挂入中心 init，避免与并行智能体的商机池 3 表接线冲突。
    """
    cursor = conn.cursor()
    cursor.execute(_DDL)
    cursor.execute(_INDEX_TENANT_STATUS)
    logger.info("外向账号托管登录态表初始化完成: table={}", _TABLE)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _tenant_filter(tenant_id: str | None) -> tuple[str, list[Any]]:
    """统一 tenant_id 过滤片段。

    非空租户走 ``tenant_id = %s``；空租户（非 SaaS）走 ``tenant_id IS NULL``，
    与 ``social_accounts`` 等既有租户隔离表语义一致。
    """
    if tenant_id is None:
        return ("tenant_id IS NULL", [])
    return ("tenant_id = %s", [tenant_id])


def _state_to_cookie_count(storage_state: dict[str, Any]) -> int:
    """从 storage_state 提取 cookie 数量（非敏感元数据，可入库/展示）。"""
    try:
        cookies = storage_state.get("cookies") or []
        return len(cookies)
    except Exception:  # 防御性：storage_state 结构异常不阻断保存
        return 0


def _serialize_state(storage_state: dict[str, Any]) -> str:
    """storage_state dict → JSON 字符串（准备加密）。"""
    return json.dumps(storage_state, ensure_ascii=False, default=str)


def _encrypt(state_json: str) -> str:
    return encryption_manager.encrypt(state_json)


def _decrypt(encrypted: str) -> dict[str, Any]:
    """解密并解析 storage_state；失败时抛出，由调用方兜底返回 None。"""
    plain = encryption_manager.decrypt(encrypted)
    parsed = json.loads(plain)
    if not isinstance(parsed, dict):
        raise ValueError("storage_state 解密后非 dict")
    return parsed


@contextmanager
def _sanitized_error(operation: str, account_id: str):
    """统一异常隔离：确保 storage_state 明文/密文绝不进入异常链或日志。

    解密失败、DB 异常等都被吞掉（仅记 operation/account_id/error_type 级别日志）；
    调用方据此返回 ``None`` 或 ``False``，不抛出含敏感数据的异常。``load_storage_state``
    依赖此语义：任何异常都让函数自然 fallthrough 到隐式 ``return None``。
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - 故意宽口径，防止任何异常暴露 cookie
        # 不记录异常正文（可能含 cookie 片段）；只记操作与账号、异常类型。
        logger.warning(
            "外向登录态操作失败: operation={}, account_id={}, error_type={}",
            operation, account_id, type(exc).__name__,
        )
        # 不 re-raise：让调用方走 fallthrough 返回 None。


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------


def save_storage_state(
    *,
    account_id: str,
    tenant_id: Optional[str],
    user_id: Optional[str],
    platform: str,
    storage_state: dict[str, Any],
) -> None:
    """加密写入（upsert）storage_state。

    同一 ``(tenant_id, account_id)`` 重复保存时覆盖更新；状态恢复为 ``active``，
    清空 ``expired_reason``（重新登录后旧失效原因不再适用）。

    Args:
        account_id: 业务账号标识（对齐 ``social_accounts.account_id``，全局唯一）。
        tenant_id: 租户 ID；非 SaaS 模式传 ``None``。
        user_id: 触发保存的用户 ID（人工接管完成时为接管用户）；不可确定时传 ``None``。
        platform: 平台标识（如 ``zhihu`` / ``xiaohongshu``），便于按平台筛选。
        storage_state: Playwright 标准 storage_state dict（含 ``cookies`` / ``origins``）。
    """
    if not account_id:
        raise ValueError("account_id 不能为空")
    if not isinstance(storage_state, dict):
        raise ValueError("storage_state 必须是 dict")

    state_json = _serialize_state(storage_state)
    encrypted = _encrypt(state_json)
    cookie_count = _state_to_cookie_count(storage_state)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            INSERT INTO {_TABLE} (
                account_id, tenant_id, user_id, platform,
                storage_state_encrypted, storage_state_key_version,
                status, cookie_count, expired_reason, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, CURRENT_TIMESTAMP)
            ON CONFLICT (account_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                user_id = EXCLUDED.user_id,
                platform = EXCLUDED.platform,
                storage_state_encrypted = EXCLUDED.storage_state_encrypted,
                storage_state_key_version = EXCLUDED.storage_state_key_version,
                status = EXCLUDED.status,
                cookie_count = EXCLUDED.cookie_count,
                expired_reason = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                account_id, tenant_id, user_id, platform,
                encrypted, _KEY_VERSION,
                STATUS_ACTIVE, cookie_count,
            ),
        )
        conn.commit()
    # 元数据级日志（无敏感数据）
    logger.info(
        "外向登录态已保存: account_id={}, platform={}, cookie_count={}",
        account_id, platform, cookie_count,
    )


def load_storage_state(
    *, account_id: str, tenant_id: Optional[str]
) -> Optional[dict[str, Any]]:
    """读取并解密 storage_state；同时 touch ``last_used_at``。

    - 失效（``expired`` / ``revoked``）会话：返回 ``None``（调用方据此走重新登录流程）。
    - 解密失败或 DB 异常：返回 ``None``，绝不抛出含 cookie 明文的异常。

    Note: 本方法只负责读取；注入后是否仍处登录态由调用方验证（不破解/不绕过，§3 红线）。
    """
    if not account_id:
        return None

    tenant_clause, tenant_params = _tenant_filter(tenant_id)

    with _sanitized_error("load_storage_state", account_id):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT storage_state_encrypted, status FROM {_TABLE}
                WHERE account_id = %s AND {tenant_clause}
                """,
                [account_id, *tenant_params],
            )
            row = cursor.fetchone()
            if row is None:
                return None
            status = row.get("status") if isinstance(row, dict) else row["status"]
            if status != STATUS_ACTIVE:
                logger.info(
                    "外向登录态跳过加载（非 active）: account_id={}, status={}",
                    account_id, status,
                )
                return None
            encrypted = (
                row.get("storage_state_encrypted")
                if isinstance(row, dict)
                else row["storage_state_encrypted"]
            )
            # touch last_used_at（独立语句，失败不影响读取）
            try:
                cursor.execute(
                    f"""
                    UPDATE {_TABLE} SET last_used_at = CURRENT_TIMESTAMP
                    WHERE account_id = %s AND {tenant_clause}
                    """,
                    [account_id, *tenant_params],
                )
                conn.commit()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "外向登录态 touch last_used_at 失败（忽略）: account_id={}",
                    account_id,
                )
            return _decrypt(encrypted)


def mark_expired(
    *, account_id: str, tenant_id: Optional[str], reason: str
) -> bool:
    """标记会话失效（``status='expired'``），记录失效原因。

    典型场景：调用方注入 storage_state 后做登录态校验，判定仍处未登录
    （cookie 过期 / 平台风控 / 验证码挑战） → 调本方法 + 通知人工重新登录。

    Returns:
        True 表示状态已更新；False 表示未找到对应会话。
    """
    if not account_id:
        return False
    tenant_clause, tenant_params = _tenant_filter(tenant_id)
    reason_text = (reason or "")[:500]

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            UPDATE {_TABLE}
            SET status = %s, expired_reason = %s, updated_at = CURRENT_TIMESTAMP
            WHERE account_id = %s AND {tenant_clause}
            """,
            [STATUS_EXPIRED, reason_text, account_id, *tenant_params],
        )
        updated = cursor.rowcount > 0
        conn.commit()
    if updated:
        logger.info(
            "外向登录态标记失效: account_id={}, reason={}",
            account_id, reason_text or "unspecified",
        )
    return updated


def mark_revoked(*, account_id: str, tenant_id: Optional[str]) -> bool:
    """标记会话吊销（``status='revoked'``）。

    典型场景：账号解绑 / 用户主动清除托管登录态。吊销后 ``load_storage_state`` 返回 ``None``。
    物理删除由清理任务另行处理（保留审计轨迹）。

    Returns:
        True 表示状态已更新；False 表示未找到对应会话。
    """
    if not account_id:
        return False
    tenant_clause, tenant_params = _tenant_filter(tenant_id)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            UPDATE {_TABLE}
            SET status = %s, updated_at = CURRENT_TIMESTAMP
            WHERE account_id = %s AND {tenant_clause}
            """,
            [STATUS_REVOKED, account_id, *tenant_params],
        )
        updated = cursor.rowcount > 0
        conn.commit()
    if updated:
        logger.info("外向登录态已吊销: account_id={}", account_id)
    return updated


def get_session_meta(
    *, account_id: str, tenant_id: Optional[str]
) -> Optional[dict[str, Any]]:
    """读取会话元数据（不含 storage_state 加密内容）。

    供管理后台展示「托管账号 → 登录态健康度」面板。绝不返回敏感字段。
    """
    if not account_id:
        return None
    tenant_clause, tenant_params = _tenant_filter(tenant_id)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT account_id, tenant_id, platform, status, cookie_count,
                   storage_state_key_version, last_used_at, expired_reason,
                   created_at, updated_at
            FROM {_TABLE}
            WHERE account_id = %s AND {tenant_clause}
            """,
            [account_id, *tenant_params],
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row) if hasattr(row, "items") else dict(row)


__all__ = [
    "STATUS_ACTIVE",
    "STATUS_EXPIRED",
    "STATUS_REVOKED",
    "init_outbound_account_sessions_table",
    "save_storage_state",
    "load_storage_state",
    "mark_expired",
    "mark_revoked",
    "get_session_meta",
]
