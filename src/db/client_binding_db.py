"""
协会客户端绑定 / 激活码 / 消耗日志 DB 访问层。

设计文档：docs/tools/association-client-design.md §2
- client_activation_codes：激活码（一次性，绑定租户）
- client_bindings：激活后的长期绑定凭证（access_token 鉴权）
- client_usage_logs：客户端 LLM/OCR 调用消耗明细 + 计费（×5 系数同事务扣减租户余额）
"""

from __future__ import annotations

import json
import math
import secrets
from datetime import datetime
from typing import Any, Optional

import bcrypt
from loguru import logger

from src.config.settings import settings
from src.core.cache_utils import CacheKeys, delete_cached, get_cached, invalidate_tenant_cache, set_cached
from src.db.database import get_db_connection
from src.services.billing import calculate_credit_cost


def _client_credit_multiplier() -> float:
    """客户端积分膨胀系数（标准积分 × 此系数 = 客户端实扣，默认5倍）。"""
    try:
        return float(getattr(settings.client, "credit_multiplier", 5.0) or 5.0)
    except Exception:
        return 5.0

# 激活码字符集（去除易混淆字符 0/O/1/I/l）
_ACTIVATION_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_ACTIVATION_CODE_PREFIX = "AC-"
_ACTIVATION_CODE_LENGTH = 12


class ClientActivationCodeDB:
    """激活码 DB 访问层。"""

    @staticmethod
    def generate_code() -> str:
        """生成激活码明文：AC- + 12 位去混淆字符。"""
        return _ACTIVATION_CODE_PREFIX + "".join(
            secrets.choice(_ACTIVATION_CODE_ALPHABET) for _ in range(_ACTIVATION_CODE_LENGTH)
        )

    @staticmethod
    def hash_code(code: str) -> str:
        """bcrypt 哈希激活码。"""
        return bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def verify_code(code: str, code_hash: str) -> bool:
        """校验激活码与哈希是否匹配。"""
        try:
            return bcrypt.checkpw(code.encode("utf-8"), code_hash.encode("utf-8"))
        except Exception:
            return False

    @staticmethod
    def create(
        *,
        tenant_id: str,
        client_name: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        max_uses: int = 1,
    ) -> dict[str, Any]:
        """生成并入库一条激活码，返回含明文 code 的记录（明文仅此一次返回）。"""
        code = ClientActivationCodeDB.generate_code()
        code_hash = ClientActivationCodeDB.hash_code(code)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO client_activation_codes
                   (code, code_hash, tenant_id, client_name, expires_at, max_uses, used_count, status)
                   VALUES (%s, %s, %s, %s, %s, %s, 0, 'unused')
                   RETURNING id, code, code_hash, tenant_id, client_name, status, max_uses, used_count, expires_at, created_at""",
                (code, code_hash, tenant_id, client_name, expires_at, max_uses),
            )
            row = dict(cursor.fetchone())
            conn.commit()
        logger.info(f"激活码创建 tenant={tenant_id} client_name={client_name} id={row['id']}")
        return row

    @staticmethod
    def get_by_code(code: str) -> Optional[dict[str, Any]]:
        """按激活码明文查询记录。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM client_activation_codes WHERE code = %s",
                (code,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_id(code_id: int) -> Optional[dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM client_activation_codes WHERE id = %s",
                (code_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM client_activation_codes WHERE tenant_id = %s ORDER BY created_at DESC",
                (tenant_id,),
            )
            return [dict(r) for r in cursor.fetchall()]

    @staticmethod
    def mark_used(code_id: int, machine_id: str) -> None:
        """标记激活码已使用一次；达到 max_uses 时置 status='used'。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE client_activation_codes
                   SET used_count = used_count + 1,
                       status = CASE WHEN used_count + 1 >= max_uses THEN 'used' ELSE status END,
                       activated_at = COALESCE(activated_at, CURRENT_TIMESTAMP),
                       activated_machine = COALESCE(activated_machine, %s),
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (machine_id, code_id),
            )
            conn.commit()

    @staticmethod
    def disable(code_id: int) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE client_activation_codes SET status = 'disabled', updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (code_id,),
            )
            conn.commit()


class ClientBindingDB:
    """客户端绑定 DB 访问层。"""

    # Redis 缓存 key 前缀：client_token:{access_token}
    _CACHE_PREFIX = "client_token"

    @staticmethod
    def _new_binding_id() -> str:
        return "cb_" + secrets.token_hex(16)

    @staticmethod
    def _new_access_token() -> str:
        return secrets.token_urlsafe(48)

    @staticmethod
    def create(
        *,
        tenant_id: str,
        activation_code_id: Optional[int] = None,
        client_name: Optional[str] = None,
        machine_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """创建绑定记录，返回含明文 access_token 的记录（明文仅此一次返回）。"""
        binding_id = ClientBindingDB._new_binding_id()
        access_token = ClientBindingDB._new_access_token()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO client_bindings
                   (binding_id, tenant_id, activation_code_id, client_name, machine_id, access_token, status)
                   VALUES (%s, %s, %s, %s, %s, %s, 'active')
                   RETURNING id, binding_id, tenant_id, activation_code_id, client_name, machine_id,
                             access_token, status, created_at""",
                (binding_id, tenant_id, activation_code_id, client_name, machine_id, access_token),
            )
            row = dict(cursor.fetchone())
            conn.commit()
        logger.info(f"客户端绑定创建 binding_id={binding_id} tenant={tenant_id}")
        return row

    @staticmethod
    def get_by_token(access_token: str) -> Optional[dict[str, Any]]:
        """按 access_token 查询绑定（Redis 缓存，TTL 300s）。仅返回 status='active' 的记录。"""
        cached = get_cached(ClientBindingDB._CACHE_PREFIX, access_token)
        if cached is not None:
            return cached
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, binding_id, tenant_id, activation_code_id, client_name,
                          machine_id, access_token, status, expires_at
                   FROM client_bindings
                   WHERE access_token = %s AND status = 'active'""",
                (access_token,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            set_cached(ClientBindingDB._CACHE_PREFIX, access_token, value=d, ttl=300)
            return d

    @staticmethod
    def get_by_id(binding_id: str) -> Optional[dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM client_bindings WHERE binding_id = %s", (binding_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM client_bindings WHERE tenant_id = %s ORDER BY created_at DESC",
                (tenant_id,),
            )
            return [dict(r) for r in cursor.fetchall()]

    @staticmethod
    def update_last_seen(binding_id: str) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE client_bindings SET last_seen_at = CURRENT_TIMESTAMP WHERE binding_id = %s",
                (binding_id,),
            )
            conn.commit()

    @staticmethod
    def disable(binding_id: str) -> None:
        """禁用绑定（踢下线），同时清缓存。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE client_bindings SET status = 'disabled', updated_at = CURRENT_TIMESTAMP WHERE binding_id = %s",
                (binding_id,),
            )
            conn.commit()
        # 清缓存（需查 token，但禁用后按 token 查自然查不到；这里保守地按 binding 查 token 再清）
        binding = ClientBindingDB.get_by_id(binding_id)
        if binding:
            delete_cached(ClientBindingDB._CACHE_PREFIX, binding["access_token"])

    @staticmethod
    def rotate_token(binding_id: str) -> Optional[dict[str, Any]]:
        """轮换 access_token，返回含新明文 token 的记录。"""
        new_token = ClientBindingDB._new_access_token()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 先清旧 token 缓存
            cursor.execute("SELECT access_token FROM client_bindings WHERE binding_id = %s", (binding_id,))
            old = cursor.fetchone()
            if not old:
                return None
            delete_cached(ClientBindingDB._CACHE_PREFIX, old["access_token"])
            cursor.execute(
                """UPDATE client_bindings SET access_token = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE binding_id = %s RETURNING *""",
                (new_token, binding_id),
            )
            row = dict(cursor.fetchone())
            conn.commit()
        return row


class ClientUsageLogDB:
    """客户端消耗日志 DB 访问层（含计费 ×5 同事务扣减）。"""

    @staticmethod
    def record_llm_usage(
        *,
        tenant_id: str,
        binding_id: str,
        model: Optional[str],
        provider: Optional[str],
        usage: dict[str, int],
        session_id: Optional[str] = None,
        association_name: Optional[str] = None,
        stage: str = "llm",
        status: str = "success",
    ) -> dict[str, float]:
        """记录一次 LLM 调用消耗，同事务扣减租户余额（×5 系数）。

        Returns:
            {"raw_credit_cost": float, "credit_cost": float, "balance_after": float}
            balance_after 为 None 时表示未查到余额。
        """
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cached_tokens = int(usage.get("cached_tokens") or usage.get("cached_input_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))

        # 标准积分（复用现有计费函数）
        raw_credit = calculate_credit_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=model,
            cached_input_tokens=cached_tokens,
        )
        # 客户端 ×5 系数，2 位小数向上取整
        credit_cost = math.ceil(raw_credit * _client_credit_multiplier() * 100) / 100

        balance_after: Optional[float] = None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO client_usage_logs
                   (tenant_id, binding_id, session_id, association_name, stage, status,
                    model, provider, prompt_tokens, completion_tokens, cached_tokens,
                    total_tokens, raw_credit_cost, credit_cost)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    tenant_id, binding_id, session_id, association_name, stage, status,
                    model, provider, prompt_tokens, completion_tokens, cached_tokens,
                    total_tokens, raw_credit, credit_cost,
                ),
            )
            # 同事务原子扣减租户余额
            if credit_cost and credit_cost > 0:
                cursor.execute(
                    "UPDATE tenants SET credit_balance = credit_balance - %s WHERE tenant_id = %s "
                    "RETURNING credit_balance",
                    (credit_cost, tenant_id),
                )
                row = cursor.fetchone()
                if row:
                    balance_after = float(row["credit_balance"]) if row["credit_balance"] is not None else None
            conn.commit()

        # 失效租户缓存（确保余额阻断读到最新值）
        if credit_cost and credit_cost > 0:
            try:
                invalidate_tenant_cache(tenant_id)
            except Exception as e:
                logger.warning(f"客户端扣费后失效租户缓存失败 tenant={tenant_id}: {e}")

        logger.info(
            f"客户端LLM计费 tenant={tenant_id} binding={binding_id} "
            f"model={model} tokens={total_tokens} raw={raw_credit} cost={credit_cost} "
            f"balance_after={balance_after}"
        )
        return {"raw_credit_cost": raw_credit, "credit_cost": credit_cost, "balance_after": balance_after}

    @staticmethod
    def record_non_llm_usage(
        *,
        tenant_id: str,
        binding_id: str,
        stage: str,
        session_id: Optional[str] = None,
        association_name: Optional[str] = None,
        status: str = "success",
        detail: Optional[dict] = None,
    ) -> None:
        """记录非 LLM 调用（OCR/WebSearch），credit_cost=0。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO client_usage_logs
                   (tenant_id, binding_id, session_id, association_name, stage, status, detail, credit_cost)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 0)""",
                (
                    tenant_id, binding_id, session_id, association_name, stage, status,
                    json.dumps(detail, ensure_ascii=False) if detail else None,
                ),
            )
            conn.commit()

    @staticmethod
    def get_tenant_summary(tenant_id: str) -> dict[str, float]:
        """租户今日/本周/总计消耗。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT
                    COALESCE(SUM(CASE WHEN created_at >= CURRENT_DATE THEN credit_cost ELSE 0 END), 0)::float AS today,
                    COALESCE(SUM(CASE WHEN created_at >= date_trunc('week', CURRENT_DATE) THEN credit_cost ELSE 0 END), 0)::float AS week,
                    COALESCE(SUM(credit_cost), 0)::float AS total
                   FROM client_usage_logs WHERE tenant_id = %s""",
                (tenant_id,),
            )
            row = cursor.fetchone()
            if not row:
                return {"today": 0.0, "week": 0.0, "total": 0.0}
            return {"today": float(row["today"] or 0), "week": float(row["week"] or 0), "total": float(row["total"] or 0)}

    @staticmethod
    def list(
        *,
        tenant_id: Optional[str] = None,
        binding_id: Optional[str] = None,
        session_id: Optional[str] = None,
        stage: Optional[str] = None,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """平台管理员视角的消耗/日志列表（按 created_at DESC）。

        同表覆盖两类行：LLM 计费行（stage=llm/purpose, credit_cost>0）+
        客户端遥测/日志行（stage=run_*/wenxin_browser/..., credit_cost=0）。
        """
        where_clauses: list[str] = []
        params: list[Any] = []
        if tenant_id:
            where_clauses.append("tenant_id = %s")
            params.append(tenant_id)
        if binding_id:
            where_clauses.append("binding_id = %s")
            params.append(binding_id)
        if session_id:
            where_clauses.append("session_id = %s")
            params.append(session_id)
        if stage:
            where_clauses.append("stage = %s")
            params.append(stage)
        if status:
            where_clauses.append("status = %s")
            params.append(status)
        if date_from:
            where_clauses.append("created_at >= %s")
            params.append(f"{date_from} 00:00:00")
        if date_to:
            where_clauses.append("created_at <= %s")
            params.append(f"{date_to} 23:59:59")
        where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

        if page < 1:
            page = 1
        if page_size < 1 or page_size > 200:
            page_size = 20
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) AS cnt FROM client_usage_logs WHERE {where_sql}", params)
            total = int(cursor.fetchone()["cnt"] or 0)
            cursor.execute(
                f"""SELECT id, tenant_id, binding_id, session_id, association_name, stage, status,
                          model, provider, total_tokens, raw_credit_cost, credit_cost, error_code,
                          detail, created_at
                   FROM client_usage_logs
                   WHERE {where_sql}
                   ORDER BY created_at DESC
                   LIMIT %s OFFSET %s""",
                (*params, page_size, offset),
            )
            items = [dict(r) for r in cursor.fetchall()]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def recent_errors(*, hours: int = 24, limit: int = 100, tenant_id: Optional[str] = None) -> list[dict[str, Any]]:
        """近 N 小时的错误/告警行（跨租户，供「及时发现」仪表）。"""
        if hours < 1:
            hours = 24
        if limit < 1 or limit > 500:
            limit = 100
        where_clauses: list[str] = ["status IN ('error', 'warning')", "created_at >= NOW() - %s * INTERVAL '1 hour'"]
        params: list[Any] = [hours]
        if tenant_id:
            where_clauses.append("tenant_id = %s")
            params.append(tenant_id)
        where_sql = " AND ".join(where_clauses)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""SELECT id, tenant_id, binding_id, session_id, association_name, stage, status,
                          error_code, detail, created_at
                   FROM client_usage_logs
                   WHERE {where_sql}
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (*params, limit),
            )
            return [dict(r) for r in cursor.fetchall()]
