"""企业微信个人账号 RPA 渠道数据库访问层

包含 5 张表的 CRUD：
- wecom_rpa_clients               客户端注册表
- wecom_rpa_accounts              个人企微账号表
- wecom_rpa_conversation_bindings 会话绑定表
- wecom_rpa_action_outbox         出站动作权威队列（在线/离线均先入库）
- wecom_rpa_audit_logs            审计日志

书写规范对齐 src/saas/db/channel_config_db.py：
- 使用 psycopg2 + ``with get_db_connection() as conn`` 上下文
- JSON 字段 json.dumps(ensure_ascii=False) 写入，读取时 json.loads
- 所有读取查询带 tenant_id 过滤，防止跨租户泄漏
- 返回 dict / None / list[dict]，不返回 ORM 对象
- 写入附带 user_id（无法确定时 None），created_at 由数据库默认填充

表结构见 deploy/init-postgres.sql 与 deploy/db_update.sql。
本模块只负责数据访问，不负责建表（建表在 SQL 迁移中幂等完成）。
"""

import json
import secrets
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection


def _new_id(prefix: str) -> str:
    """生成带前缀的业务 ID。"""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _parse_json_field(value: Optional[str], default: Any = None) -> Any:
    """安全解析 JSON 字段，失败时返回 default。"""
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ===========================================================================
# clients —— 客户端注册
# ===========================================================================


def create_client(
    tenant_id: str,
    client_id: str,
    name: str,
    encrypted_secret: str,
    min_version: str,
    user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """注册一个新客户端。encrypted_secret 必须是服务端加密后的密文。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO wecom_rpa_clients
                    (id, tenant_id, user_id, name, encrypted_secret, status, min_version)
                VALUES (%s, %s, %s, %s, %s, 'active', %s)
                """,
                (client_id, tenant_id, user_id, name, encrypted_secret, min_version),
            )
            conn.commit()
            logger.info(f"RPA client created: {client_id} (tenant={tenant_id})")
            return get_client(client_id)
        except Exception as e:
            logger.error(f"Failed to create RPA client {client_id}: {e}")
            return None


def get_client(client_id: str) -> Optional[Dict[str, Any]]:
    """按 client_id 读取客户端（跨租户可见，client_id 本身全局唯一）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM wecom_rpa_clients WHERE id = %s", (client_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def list_clients(tenant_id: str) -> List[Dict[str, Any]]:
    """列出租户下所有客户端。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM wecom_rpa_clients WHERE tenant_id = %s ORDER BY created_at DESC",
            (tenant_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def update_client_status(
    tenant_id: str,
    client_id: str,
    status: str,
) -> bool:
    """更新客户端状态（active/disabled）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE wecom_rpa_clients
            SET status = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND tenant_id = %s
            """,
            (status, client_id, tenant_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def update_last_seen(tenant_id: str, client_id: str) -> bool:
    """记录客户端最近一次心跳/请求时间。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE wecom_rpa_clients
            SET last_seen_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND tenant_id = %s
            """,
            (client_id, tenant_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def update_client_agent_base_url(
    tenant_id: str,
    client_id: str,
    agent_base_url: Optional[str],
) -> bool:
    """更新客户端回填的 agent_base_url（运维排查用，None 表示清除）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE wecom_rpa_clients
            SET agent_base_url = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND tenant_id = %s
            """,
            (agent_base_url, client_id, tenant_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def rotate_secret(
    tenant_id: str,
    client_id: str,
    new_encrypted_secret: str,
) -> bool:
    """轮换客户端密钥（密文存储）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE wecom_rpa_clients
            SET encrypted_secret = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND tenant_id = %s
            """,
            (new_encrypted_secret, client_id, tenant_id),
        )
        conn.commit()
        success = cursor.rowcount > 0
        if success:
            logger.info(f"RPA client secret rotated: {client_id}")
        return success


# ===========================================================================
# accounts —— 个人企微账号
# ===========================================================================


def upsert_account(
    tenant_id: str,
    client_id: str,
    account_id: str,
    display_name: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """新增或更新账号（首次发现该账号时插入，后续更新 display_name）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO wecom_rpa_accounts
                    (id, tenant_id, user_id, client_id, display_name, status)
                VALUES (%s, %s, %s, %s, %s, 'offline')
                ON CONFLICT (id) DO UPDATE
                    SET display_name = COALESCE(EXCLUDED.display_name, wecom_rpa_accounts.display_name),
                        client_id   = EXCLUDED.client_id,
                        updated_at  = CURRENT_TIMESTAMP
                """,
                (account_id, tenant_id, user_id, client_id, display_name),
            )
            conn.commit()
            return get_account(account_id)
        except Exception as e:
            logger.error(f"Failed to upsert RPA account {account_id}: {e}")
            return None


def get_account(account_id: str) -> Optional[Dict[str, Any]]:
    """读取账号信息。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM wecom_rpa_accounts WHERE id = %s", (account_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def list_accounts(tenant_id: str, client_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出租户（可按 client 过滤）的账号。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if client_id:
            cursor.execute(
                """
                SELECT * FROM wecom_rpa_accounts
                WHERE tenant_id = %s AND client_id = %s
                ORDER BY created_at DESC
                """,
                (tenant_id, client_id),
            )
        else:
            cursor.execute(
                "SELECT * FROM wecom_rpa_accounts WHERE tenant_id = %s ORDER BY created_at DESC",
                (tenant_id,),
            )
        return [dict(r) for r in cursor.fetchall()]


def set_account_status(
    tenant_id: str,
    account_id: str,
    status: str,
    paused_reason: Optional[str] = None,
) -> bool:
    """更新账号状态（online/offline/need_login/paused/...）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE wecom_rpa_accounts
            SET status = %s,
                paused_reason = %s,
                last_login_at = CASE WHEN %s = 'online' THEN CURRENT_TIMESTAMP ELSE last_login_at END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND tenant_id = %s
            """,
            (status, paused_reason, status, account_id, tenant_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def get_account_status(account_id: str) -> Optional[str]:
    """读取账号当前状态字符串。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM wecom_rpa_accounts WHERE id = %s", (account_id,))
        row = cursor.fetchone()
        return row["status"] if row else None


# ===========================================================================
# bindings —— 会话绑定
# ===========================================================================


def get_or_create_binding(
    tenant_id: str,
    account_id: str,
    conversation_type: str,
    display_name: str,
    search_key: str,
    stable_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """获取或创建会话绑定。

    首次见到某 (account_id, conversation) 时插入一条 pending 绑定，
    等待人工确认（status=active 后才允许自动发送）。
    """
    binding_id = _new_id("rpa_bind")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO wecom_rpa_conversation_bindings
                    (id, tenant_id, user_id, account_id, conversation_type,
                     display_name, search_key, stable_id, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                ON CONFLICT (account_id, search_key) DO UPDATE
                    SET display_name = COALESCE(wecom_rpa_conversation_bindings.display_name,
                                                EXCLUDED.display_name),
                        stable_id    = COALESCE(EXCLUDED.stable_id, wecom_rpa_conversation_bindings.stable_id),
                        updated_at   = CURRENT_TIMESTAMP
                RETURNING id
                """,
                (binding_id, tenant_id, user_id, account_id, conversation_type,
                 display_name, search_key, stable_id),
            )
            row = cursor.fetchone()
            conn.commit()
            actual_id = row["id"] if row else binding_id
            return get_binding(actual_id)
        except Exception as e:
            logger.error(f"Failed to upsert RPA binding (account={account_id}, key={search_key}): {e}")
            return None


def get_binding(binding_id: str) -> Optional[Dict[str, Any]]:
    """按主键读取绑定。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM wecom_rpa_conversation_bindings WHERE id = %s",
            (binding_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_bindings(tenant_id: str, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出租户（可按 account 过滤）的绑定。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if account_id:
            cursor.execute(
                """
                SELECT * FROM wecom_rpa_conversation_bindings
                WHERE tenant_id = %s AND account_id = %s
                ORDER BY created_at DESC
                """,
                (tenant_id, account_id),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM wecom_rpa_conversation_bindings
                WHERE tenant_id = %s ORDER BY created_at DESC
                """,
                (tenant_id,),
            )
        return [dict(r) for r in cursor.fetchall()]


def list_all_bindings_rich(
    tenant_id_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """平台管理员视角：跨租户列出所有 RPA 客户端，聚合账号数与绑定数。

    仅供 platform_admin 使用（调用方需自行鉴权）。

    数据源以 ``wecom_rpa_clients`` 为基准（一行一 client），确保
    「创建 client 后即可在列表看到，无需等待 client 真正连上来生成 binding」。

    status_filter 语义：
        - None / ''：全部 client（默认）
        - 'active' / 'disabled'：按 client.status 过滤
        - 'needs_review_only'：排除 status='active' 的 client（显示「需关注」）

    返回字段（dict）：
        client_id, tenant_id, client_name, client_status,
        agent_base_url, last_heartbeat_at（来自 clients.last_seen_at）,
        min_version, created_at, updated_at,
        account_count, binding_count, last_account_name
    """
    conditions = []
    params: List[Any] = []
    if tenant_id_filter:
        conditions.append("c.tenant_id = %s")
        params.append(tenant_id_filter)
    if status_filter:
        if status_filter == "needs_review_only":
            # 排除 active，显示其他状态（disabled 等）
            conditions.append("c.status <> 'active'")
        else:
            conditions.append("c.status = %s")
            params.append(status_filter)
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT  c.id              AS client_id,
                    c.tenant_id       AS tenant_id,
                    c.name            AS client_name,
                    c.status          AS client_status,
                    c.agent_base_url  AS agent_base_url,
                    c.last_seen_at    AS last_heartbeat_at,
                    c.min_version     AS min_version,
                    c.created_at      AS created_at,
                    c.updated_at      AS updated_at,
                    (SELECT COUNT(*) FROM wecom_rpa_accounts a WHERE a.client_id = c.id) AS account_count,
                    (SELECT COUNT(*)
                       FROM wecom_rpa_conversation_bindings b
                       JOIN wecom_rpa_accounts a ON a.id = b.account_id
                       WHERE a.client_id = c.id) AS binding_count,
                    (SELECT a.display_name
                       FROM wecom_rpa_accounts a
                       WHERE a.client_id = c.id
                       ORDER BY a.created_at DESC
                       LIMIT 1) AS last_account_name
            FROM wecom_rpa_clients c
            {where_clause}
            ORDER BY c.created_at DESC
            """,
            tuple(params),
        )
        return [dict(r) for r in cursor.fetchall()]


def set_binding_status(
    tenant_id: str,
    binding_id: str,
    status: str,
) -> bool:
    """更新绑定状态（pending/active/paused/invalid）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE wecom_rpa_conversation_bindings
            SET status = %s,
                last_verified_at = CASE WHEN %s = 'active' THEN CURRENT_TIMESTAMP ELSE last_verified_at END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND tenant_id = %s
            """,
            (status, status, binding_id, tenant_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def find_binding_by_search_key(
    tenant_id: str,
    account_id: str,
    search_key: str,
) -> Optional[Dict[str, Any]]:
    """按账号 + 搜索键定位绑定（重名识别时用）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM wecom_rpa_conversation_bindings
            WHERE tenant_id = %s AND account_id = %s AND search_key = %s
            """,
            (tenant_id, account_id, search_key),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def update_binding(
    tenant_id: str,
    binding_id: str,
    display_name: Optional[str] = None,
    monitor_user_names: Optional[List[str]] = None,
    monitor_user_ids: Optional[List[str]] = None,
) -> bool:
    """更新绑定可编辑字段（首版仅监控白名单）。

    None 表示"不修改该字段"；显式传空 list 表示"清除该字段"。
    PostgreSQL 数组字段直接传 Python list，psycopg2 自动适配。
    """
    sets: List[str] = []
    params: List[Any] = []
    if display_name is not None:
        normalized_display_name = display_name.strip()
        if not normalized_display_name:
            return False
        sets.append("display_name = %s")
        params.append(normalized_display_name)
    if monitor_user_names is not None:
        sets.append("monitor_user_names = %s")
        params.append(monitor_user_names)
    if monitor_user_ids is not None:
        sets.append("monitor_user_ids = %s")
        params.append(monitor_user_ids)
    if not sets:
        # 无字段需要更新：幂等成功
        return True
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.extend([binding_id, tenant_id])
    sql = (
        "UPDATE wecom_rpa_conversation_bindings SET "
        + ", ".join(sets)
        + " WHERE id = %s AND tenant_id = %s"
    )
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, tuple(params))
        conn.commit()
        return cursor.rowcount > 0


def list_bindings_by_client(
    tenant_id: str,
    client_id: str,
) -> List[Dict[str, Any]]:
    """列出某 client 下所有绑定（通过 account.client_id 关联）。

    供 /config 组装 monitor_users 白名单使用。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT b.* FROM wecom_rpa_conversation_bindings b
            JOIN wecom_rpa_accounts a ON a.id = b.account_id
            WHERE b.tenant_id = %s AND a.client_id = %s
            ORDER BY b.created_at DESC
            """,
            (tenant_id, client_id),
        )
        return [dict(r) for r in cursor.fetchall()]


# ===========================================================================
# outbox —— 出站动作权威队列（在线/离线均先入库）
# ===========================================================================


def enqueue_action(
    tenant_id: str,
    account_id: str,
    conversation_id: str,
    request_id: str,
    session_id: str,
    actions_json: str,
    dedup_key: str,
    reply_context_json: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """入队一条出站动作信封。

    actions_json 应为 ActionEnvelope.actions 序列化后的 JSON 字符串。
    dedup_key 由调用方按 ``wecom_personal_rpa:{tenant_id}:{request_id}`` 生成，
    数据库 UNIQUE 约束防止重复入队。
    """
    action_id = _new_id("rpa_act")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO wecom_rpa_action_outbox
                    (id, tenant_id, user_id, account_id, conversation_id,
                     request_id, session_id, actions, reply_context, status, attempts, dedup_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', 0, %s)
                ON CONFLICT (dedup_key) DO NOTHING
                RETURNING id
                """,
                (action_id, tenant_id, user_id, account_id, conversation_id,
                 request_id, session_id, actions_json, reply_context_json, dedup_key),
            )
            row = cursor.fetchone()
            conn.commit()
            if row is None:
                logger.info(f"RPA outbox dedup hit, skipped: {dedup_key}")
                return {
                    "id": None,
                    "tenant_id": tenant_id,
                    "account_id": account_id,
                    "request_id": request_id,
                    "status": "deduplicated",
                }
            return {
                "id": row["id"],
                "tenant_id": tenant_id,
                "account_id": account_id,
                "request_id": request_id,
                "status": "pending",
            }
        except Exception as e:
            logger.error(f"Failed to enqueue RPA action (request={request_id}): {e}")
    return None


def enqueue_inbound_archive_message(
    tenant_id: str, config_id: str, event_id: str, envelope_json: str
) -> bool:
    """可靠写入会话存档 inbox；重复 event_id 视为已投递。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO wecom_rpa_archive_inbox
                (tenant_id, config_id, event_id, envelope, status, attempts)
            VALUES (%s, %s, %s, %s::jsonb, 'pending', 0)
            ON CONFLICT (tenant_id, event_id) DO NOTHING
            """,
            (tenant_id, config_id, event_id, envelope_json),
        )
        conn.commit()
        return True


def claim_archive_inbox(tenant_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """领取待处理 inbox；回收进程崩溃后超过五分钟的 running 任务。"""
    claim_token = secrets.token_hex(16)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE wecom_rpa_archive_inbox
            SET status='running', attempts=attempts+1, claim_token=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE id IN (
                SELECT id FROM wecom_rpa_archive_inbox
                WHERE tenant_id=%s AND (
                    status='pending' OR
                    (status='running' AND updated_at < CURRENT_TIMESTAMP - INTERVAL '5 minutes') OR
                    (status='retryable' AND next_retry_at <= CURRENT_TIMESTAMP)
                )
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT %s
            ) RETURNING *
            """,
            (claim_token, tenant_id, max(1, min(limit, 100))),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        conn.commit()
        return rows


def mark_archive_inbox(
    id_: int, claim_token: str, status: str, error: Optional[str] = None
) -> bool:
    """仅由当前租约持有者完成或延迟重试 inbox，防止过期 worker 覆盖状态。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE wecom_rpa_archive_inbox
            SET status=%s, error_message=%s,
                next_retry_at=CASE WHEN %s='retryable' THEN CURRENT_TIMESTAMP + INTERVAL '30 seconds' ELSE NULL END,
                updated_at=CURRENT_TIMESTAMP,
                completed_at=CASE WHEN %s='succeeded' THEN CURRENT_TIMESTAMP ELSE completed_at END
            WHERE id=%s AND status='running' AND claim_token=%s
            """,
            (status, error, status, status, id_, claim_token),
        )
        conn.commit()
        return cursor.rowcount > 0


def heartbeat_archive_inbox(id_: int, claim_token: str) -> bool:
    """刷新当前 worker 租约，避免合法慢任务被超时回收并发执行。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE wecom_rpa_archive_inbox SET updated_at=CURRENT_TIMESTAMP
            WHERE id=%s AND status='running' AND claim_token=%s
            """,
            (id_, claim_token),
        )
        conn.commit()
        return cursor.rowcount > 0


def claim_pending(limit: int = 20) -> List[Dict[str, Any]]:
    """原子领取 pending 动作，标记为 running。

    多 worker 并发安全依赖 ``WHERE status = 'pending'`` 的行级过滤；
    生产环境如需更强一致可在外层加 advisory lock。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE wecom_rpa_action_outbox
            SET status = 'running',
                attempts = attempts + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN (
                SELECT id FROM wecom_rpa_action_outbox
                WHERE status = 'pending'
                  AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
                ORDER BY created_at
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            (limit,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.commit()
        for r in rows:
            r["actions"] = _parse_json_field(r.get("actions"), [])
            r["reply_context"] = _parse_json_field(r.get("reply_context"), None)
        return rows


def mark_outbox_status(
    action_id: str,
    status: str,
    error_message: Optional[str] = None,
    next_retry_at: Optional[datetime] = None,
) -> bool:
    """更新出站动作状态（succeeded/retryable/failed/paused）。

    - succeeded: 终态
    - retryable: 等待重试，需提供 next_retry_at
    - failed:    终态
    - paused:    等待人工恢复
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE wecom_rpa_action_outbox
            SET status = %s,
                error_message = %s,
                next_retry_at = %s,
                completed_at = CASE WHEN %s IN ('succeeded', 'failed') THEN CURRENT_TIMESTAMP ELSE completed_at END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (status, error_message, next_retry_at, status, action_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def list_outbox(
    tenant_id: str,
    account_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """列出（可过滤）出站动作，按 created_at DESC。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        conditions = ["tenant_id = %s"]
        params: List[Any] = [tenant_id]
        if account_id:
            conditions.append("account_id = %s")
            params.append(account_id)
        if status:
            conditions.append("status = %s")
            params.append(status)
        where_clause = " AND ".join(conditions)
        params.append(limit)
        cursor.execute(
            f"""
            SELECT * FROM wecom_rpa_action_outbox
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        for r in rows:
            r["actions"] = _parse_json_field(r.get("actions"), [])
            r["reply_context"] = _parse_json_field(r.get("reply_context"), None)
        return rows


def list_pending_outbox_for_client(
    tenant_id: str,
    client_id: str,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """安全列出某 active client 可见的待执行动作，按创建时间正序。

    账号归属在 SQL 内通过 accounts join 决定，不接受调用方传 account_id。
    retryable 仅在到达 next_retry_at 后重新可见；读取不改变状态。
    """
    safe_limit = max(1, min(int(limit), 100))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT o.*
            FROM wecom_rpa_action_outbox o
            INNER JOIN wecom_rpa_accounts a
                ON a.id = o.account_id
               AND a.tenant_id = o.tenant_id
            INNER JOIN wecom_rpa_clients c
                ON c.id = a.client_id
               AND c.tenant_id = o.tenant_id
            WHERE o.tenant_id = %s
              AND a.client_id = %s
              AND c.status = 'active'
              AND (
                    o.status = 'pending'
                    OR (o.status = 'retryable' AND o.next_retry_at <= CURRENT_TIMESTAMP)
                  )
            ORDER BY o.created_at ASC, o.id ASC
            LIMIT %s
            """,
            (tenant_id, client_id, safe_limit),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        for row in rows:
            row["actions"] = _parse_json_field(row.get("actions"), [])
            row["reply_context"] = _parse_json_field(row.get("reply_context"), None)
        return rows


def mark_outbox_result_for_client(
    tenant_id: str,
    client_id: str,
    request_id: str,
    action_index: int,
    success: bool,
    error_message: Optional[str] = None,
) -> bool:
    """按已鉴权 client 归属安全应用回执。

    多 action 信封只有最后一个 action 成功后才整体 succeeded；任一失败立即终态 failed。
    已终态或重复回执不再改写，确保幂等且不跨租户/客户端更新。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT o.id, o.actions, o.action_results
            FROM wecom_rpa_action_outbox o
            INNER JOIN wecom_rpa_accounts a
                ON a.id = o.account_id
               AND a.tenant_id = o.tenant_id
            WHERE o.tenant_id = %s
              AND a.client_id = %s
              AND o.request_id = %s
              AND o.status IN ('pending', 'running', 'retryable')
            FOR UPDATE OF o
            """,
            (tenant_id, client_id, request_id),
        )
        row = cursor.fetchone()
        if not row:
            return False
        actions = _parse_json_field(row.get("actions"), [])
        if action_index < 0 or action_index >= len(actions):
            return False
        action_results = _parse_json_field(row.get("action_results"), {})
        if not isinstance(action_results, dict):
            action_results = {}
        result_key = str(action_index)
        # 同一索引以首次合法回执为准，重复或冲突重报均不覆盖持久化结果。
        if result_key in action_results:
            return True
        action_results[result_key] = "succeeded" if success else "failed"
        all_succeeded = (
            len(action_results) == len(actions)
            and all(action_results.get(str(i)) == "succeeded" for i in range(len(actions)))
        )
        next_status = "failed" if not success else ("succeeded" if all_succeeded else None)
        cursor.execute(
            """
            UPDATE wecom_rpa_action_outbox
            SET action_results = %s::jsonb,
                status = COALESCE(%s, status),
                error_message = CASE WHEN %s = 'failed' THEN %s ELSE error_message END,
                completed_at = CASE WHEN %s IN ('succeeded', 'failed')
                                    THEN CURRENT_TIMESTAMP ELSE completed_at END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status IN ('pending', 'running', 'retryable')
            """,
            (
                json.dumps(action_results, ensure_ascii=False),
                next_status,
                next_status,
                error_message if not success else None,
                next_status,
                row["id"],
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


# ===========================================================================
# audit —— 审计日志
# ===========================================================================


def write_audit(
    tenant_id: str,
    client_id: Optional[str],
    account_id: Optional[str],
    category: str,
    payload_json: str,
    action_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """写一条审计日志。category 如 inbound_message / agent_reply / action_result / pause_resume。

    payload_json 由调用方序列化好（注意脱敏）。返回审计记录 ID。
    """
    audit_id = _new_id("rpa_audit")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO wecom_rpa_audit_logs
                    (id, tenant_id, user_id, client_id, account_id, action_id, category, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (audit_id, tenant_id, user_id, client_id, account_id, action_id, category, payload_json),
            )
            conn.commit()
            return audit_id
        except Exception as e:
            logger.error(f"Failed to write RPA audit (category={category}): {e}")
            return None


def list_audit(
    tenant_id: str,
    filters: Optional[Dict[str, Any]] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """列出审计日志。filters 支持 account_id / client_id / category / action_id 任一过滤。"""
    filters = filters or {}
    conditions = ["tenant_id = %s"]
    params: List[Any] = [tenant_id]
    for key in ("client_id", "account_id", "category", "action_id"):
        val = filters.get(key)
        if val:
            conditions.append(f"{key} = %s")
            params.append(val)
    where_clause = " AND ".join(conditions)
    params.append(limit)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT * FROM wecom_rpa_audit_logs
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        for r in rows:
            r["payload"] = _parse_json_field(r.get("payload"), {})
        return rows


# ===========================================================================
# 指标 / 告警聚合（只读，供 admin /metrics /alerts 端点使用）
# 对齐 src/saas/db/usage_log_db.py：%s 时间窗参数 + GROUP BY 聚合
# ===========================================================================


def get_audit_counts(tenant_id: str, since_dt: datetime) -> Dict[str, int]:
    """时间窗内按 category 计数的审计事件。返回 {category: count}。"""
    since_str = since_dt.strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT category, COUNT(*) AS cnt
            FROM wecom_rpa_audit_logs
            WHERE tenant_id = %s AND created_at >= %s
            GROUP BY category
            """,
            (tenant_id, since_str),
        )
        return {str(r["category"]): int(r["cnt"]) for r in cursor.fetchall()}


def get_outcome_status_counts(tenant_id: str, since_dt: datetime) -> Dict[str, int]:
    """时间窗内出站动作按 status 计数。返回 {status: count}。"""
    since_str = since_dt.strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT status, COUNT(*) AS cnt
            FROM wecom_rpa_action_outbox
            WHERE tenant_id = %s AND created_at >= %s
            GROUP BY status
            """,
            (tenant_id, since_str),
        )
        return {str(r["status"]): int(r["cnt"]) for r in cursor.fetchall()}


def get_client_liveness(tenant_id: str) -> List[Dict[str, Any]]:
    """全部客户端的存活信号：{id, name, status, last_seen_at}。last_seen_at 为 datetime|None。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, name, status, last_seen_at
            FROM wecom_rpa_clients
            WHERE tenant_id = %s
            """,
            (tenant_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def get_account_states(tenant_id: str) -> List[Dict[str, Any]]:
    """全部账号的当前状态：{id, client_id, display_name, status, last_login_at}。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, client_id, display_name, status, last_login_at
            FROM wecom_rpa_accounts
            WHERE tenant_id = %s
            """,
            (tenant_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def get_binding_status_counts(tenant_id: str) -> Dict[str, int]:
    """绑定按 status 计数（当前态）。返回 {status: count}。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT status, COUNT(*) AS cnt
            FROM wecom_rpa_conversation_bindings
            WHERE tenant_id = %s
            GROUP BY status
            """,
            (tenant_id,),
        )
        return {str(r["status"]): int(r["cnt"]) for r in cursor.fetchall()}


def get_recent_outcomes(
    tenant_id: str, limit: int = 50
) -> List[Dict[str, Any]]:
    """最近出站动作（按 created_at DESC），仅取连续失败检测所需字段。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT account_id, status, created_at
            FROM wecom_rpa_action_outbox
            WHERE tenant_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (tenant_id, limit),
        )
        return [dict(r) for r in cursor.fetchall()]
