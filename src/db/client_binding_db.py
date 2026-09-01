"""
协会客户端绑定 / 激活码 / 消耗日志 DB 访问层。

设计文档：docs/tools/association-client-design.md §2
- client_activation_codes：激活码（一次性，绑定租户）
- client_bindings：激活后的长期绑定凭证（access_token 鉴权）
- client_usage_logs：客户端 LLM/OCR 调用消耗明细 + 计费（×10 系数同事务扣减租户余额）
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
    """客户端积分膨胀系数（标准积分 × 此系数 = 客户端实扣，默认10倍）。"""
    try:
        return float(getattr(settings.client, "credit_multiplier", 10.0) or 10.0)
    except Exception:
        return 10.0

# 激活码字符集（去除易混淆字符 0/O/1/I/l）
_ACTIVATION_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_ACTIVATION_CODE_PREFIX = "AC-"
_ACTIVATION_CODE_LENGTH = 12

# BOSS 本地工具计费在 client_usage_logs 中的固定标记（stage / binding_id 哨兵）
BOSS_TOOL_USAGE_STAGE = "boss_tool"
BOSS_TOOL_BINDING_SENTINEL = "boss-local-runtime"


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


# 台账 detail.arguments 序列化上限（字符）：boss 参数（筛选条件/职位名/姓名等）量级很小，
# 上限只为防异常调用方塞入超大 payload 撑爆台账行；超长降级为截断文本（保证 detail 仍合法 JSON）
TOOL_USAGE_ARGS_MAX_CHARS = 1000


def _compact_arguments(arguments: Any) -> Any:
    """参数快照规整：原始 JSON 值直接放行；序列化失败降级为文本；超限降级为 {_truncated: 文本}。"""
    if arguments is None:
        return None
    try:
        text = json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(arguments)  # 不可 JSON 序列化（异常调用方）：转文本，保证 detail 仍是合法 JSON
    if len(text) <= TOOL_USAGE_ARGS_MAX_CHARS:
        return arguments
    return {"_truncated": text[:TOOL_USAGE_ARGS_MAX_CHARS] + "…(截断)"}


def _build_tool_usage_detail(
    *,
    invocation_id: Optional[str],
    device_id: Optional[str],
    command: str,
    arguments: Any,
    user_id: Optional[str],
) -> str:
    """boss_tool 台账 detail JSON：{invocation_id, device_id, command, arguments, user_id}。

    command/arguments 让计费行免 join invocation 表即可回答"调了哪个命令、什么参数"
    （P2 §4.2）；arguments 经 _compact_arguments 截断防超大 payload。
    """
    return json.dumps(
        {
            "invocation_id": invocation_id,
            "device_id": device_id,
            "command": command,
            "arguments": _compact_arguments(arguments),
            "user_id": user_id,
        },
        ensure_ascii=False,
    )


class ClientUsageLogDB:
    """客户端消耗日志 DB 访问层（含计费 ×10 同事务扣减）。"""

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
        """记录一次 LLM 调用消耗，同事务扣减租户余额（×10 系数）。

        Returns:
            {"raw_credit_cost": float, "credit_cost": float, "balance_after": float}
            balance_after 为 None 时表示未查到余额。
        """
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cached_tokens = int(usage.get("cached_tokens") or usage.get("cached_input_tokens") or 0)
        cache_creation_tokens = int(usage.get("cache_creation_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))

        # 标准积分（复用现有计费函数）
        raw_credit = calculate_credit_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=model,
            cached_input_tokens=cached_tokens,
            cache_creation_input_tokens=cache_creation_tokens,
        )
        # 客户端 ×10 系数，2 位小数向上取整
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
    def insert_tool_usage_row(
        cursor,
        *,
        tenant_id: str,
        tool_name: str,
        credit_cost: float,
        invocation_id: Optional[str] = None,
        device_id: Optional[str] = None,
        session_id: Optional[str] = None,
        status: str = "success",
        user_id: Optional[str] = None,
        arguments: Any = None,
    ) -> Optional[float]:
        """在调用方事务内落一行 boss_tool 台账并同事务扣减租户余额，返回 balance_after。

        供 repository.write_result 结果落库同事务计费复用（2026-09-01 计费时机迁移，
        docs/design/billing/client-billing-integration-design.md §4.1）：调用方负责
        commit/回滚与租户缓存失效。租户不存在时仍落台账行（对账可见）但余额不动，
        返回 None，调用方必须告警。

        detail 形状见 _build_tool_usage_detail（含命令/参数/user 归属）。
        """
        credit_cost = math.ceil(float(credit_cost) * 100) / 100
        if credit_cost <= 0:
            return None
        detail = _build_tool_usage_detail(
            invocation_id=invocation_id,
            device_id=device_id,
            command=tool_name,
            arguments=arguments,
            user_id=user_id,
        )
        cursor.execute(
            """INSERT INTO client_usage_logs
               (tenant_id, binding_id, session_id, stage, status,
                model, provider, raw_credit_cost, credit_cost, detail)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                tenant_id, BOSS_TOOL_BINDING_SENTINEL, session_id, BOSS_TOOL_USAGE_STAGE, status,
                tool_name, "boss-recruiting", credit_cost, credit_cost, detail,
            ),
        )
        cursor.execute(
            "UPDATE tenants SET credit_balance = credit_balance - %s WHERE tenant_id = %s "
            "RETURNING credit_balance",
            (credit_cost, tenant_id),
        )
        row = cursor.fetchone()
        if row and row["credit_balance"] is not None:
            return float(row["credit_balance"])
        return None

    @staticmethod
    def record_tool_usage(
        *,
        tenant_id: str,
        tool_name: str,
        credit_cost: float,
        invocation_id: Optional[str] = None,
        device_id: Optional[str] = None,
        session_id: Optional[str] = None,
        status: str = "success",
        user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """记录一次 BOSS 本地工具调用消耗，同事务扣减租户余额（协会同款台账）。

        复用 client_usage_logs：stage='boss_tool'，model=工具名，provider='boss-recruiting'；
        工具调用没有 client_bindings 行，binding_id 用固定哨兵 'boss-local-runtime'，
        invocation_id/device_id 放 detail JSON 供对账（local_tool_invocations.credit_cost 反向回写）。

        Returns:
            {"credit_cost": float, "balance_after": Optional[float]}
        """
        credit_cost = math.ceil(float(credit_cost) * 100) / 100
        if credit_cost <= 0:
            return {"credit_cost": 0.0, "balance_after": None}

        balance_after: Optional[float] = None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            balance_after = ClientUsageLogDB.insert_tool_usage_row(
                cursor,
                tenant_id=tenant_id,
                tool_name=tool_name,
                credit_cost=credit_cost,
                invocation_id=invocation_id,
                device_id=device_id,
                session_id=session_id,
                status=status,
                user_id=user_id,
            )
            conn.commit()
        if balance_after is None:
            # UPDATE tenants 影响 0 行：租户不存在（如测试租户污染），余额未扣——当场告警
            logger.error(
                f"后端日志：本地工具计费扣减失败（租户不存在，余额未扣） "
                f"tenant={tenant_id} tool={tool_name} invocation={invocation_id}"
            )

        # 失效租户缓存（确保余额阻断读到最新值）
        try:
            invalidate_tenant_cache(tenant_id)
        except Exception as e:
            logger.warning(f"BOSS工具扣费后失效租户缓存失败 tenant={tenant_id}: {e}")

        logger.info(
            f"BOSS工具计费 tenant={tenant_id} tool={tool_name} invocation={invocation_id} "
            f"session={session_id} user={user_id} cost={credit_cost} balance_after={balance_after}"
        )
        return {"credit_cost": credit_cost, "balance_after": balance_after}

    @staticmethod
    def record_client_report(
        *,
        tenant_id: str,
        binding_id: str,
        client_name: str,
        command: str,
        kind: str = "action",
        quantity: int = 1,
        credit_cost: float,
        client_ref_id: Optional[str] = None,
        session_id: Optional[str] = None,
        arguments: Optional[dict] = None,
        occurred_at: Optional[str] = None,
        extra_detail: Optional[dict] = None,
    ) -> dict[str, Any]:
        """C 模式标准上报落账（幂等）：写 client_usage_logs + 同事务扣减租户余额。

        客户端通用用量上报（P4 §4.4）：客户端只报事实（命令/参数摘要/次数），
        金额由服务端价目表计算后经 credit_cost 传入。幂等：client_ref_id 在租户内
        唯一（部分唯一索引兜底），重复上报返回首次结果且不重复扣费。
        extra_detail 仅作补充存档且整体 ≤1500 字符（超长丢弃），事实保留键
        （command/kind/quantity/arguments/client_ref_id/occurred_at）恒以服务端值为准，
        客户端 extra_detail 无法覆盖（防审计对账被伪造字段误导）。

        Returns:
            {"credit_cost": float, "balance_after": Optional[float], "duplicate": bool}
        """
        credit_cost = math.ceil(float(credit_cost) * 100) / 100
        if extra_detail:
            try:
                if len(json.dumps(extra_detail, ensure_ascii=False)) > 1500:
                    extra_detail = {"_truncated": "detail 超长已丢弃"}
            except (TypeError, ValueError):
                extra_detail = None  # 不可序列化的补充字段直接丢弃，不影响落账
        detail = json.dumps(
            {
                # 客户端补充字段在前，服务端事实保留键在后覆盖——客户端不可伪造计费事实
                **(extra_detail or {}),
                "command": command,
                "kind": kind,
                "quantity": quantity,
                "arguments": _compact_arguments(arguments),
                "client_ref_id": client_ref_id,
                "occurred_at": occurred_at,
                # C 模式无服务端用户身份：置 None 防客户端伪造 user_id 误导明细页归属
                "user_id": None,
            },
            ensure_ascii=False,
        )
        stage = f"{client_name}_{kind}"[:50]
        balance_after: Optional[float] = None

        with get_db_connection() as conn:
            cursor = conn.cursor()

            if client_ref_id:
                cursor.execute(
                    """SELECT credit_cost FROM client_usage_logs
                       WHERE tenant_id = %s AND client_ref_id = %s""",
                    (tenant_id, client_ref_id),
                )
                existing = cursor.fetchone()
                if existing:
                    conn.commit()
                    return {"credit_cost": float(existing["credit_cost"] or 0),
                            "balance_after": None, "duplicate": True}

            try:
                cursor.execute(
                    """INSERT INTO client_usage_logs
                       (tenant_id, binding_id, client_name, session_id, client_ref_id,
                        stage, status, model, provider, raw_credit_cost, credit_cost, detail)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        tenant_id, binding_id, client_name, session_id, client_ref_id,
                        stage, "success", command, "client-report",
                        credit_cost, credit_cost, detail,
                    ),
                )
                if credit_cost > 0:
                    cursor.execute(
                        "UPDATE tenants SET credit_balance = credit_balance - %s "
                        "WHERE tenant_id = %s RETURNING credit_balance",
                        (credit_cost, tenant_id),
                    )
                    row = cursor.fetchone()
                    if row and row["credit_balance"] is not None:
                        balance_after = float(row["credit_balance"])
                conn.commit()
            except Exception as e:
                # 并发同 ref_id 落到唯一索引：回滚后按幂等重复处理
                # （pgcode 23505 = unique_violation，不依赖异常消息文案）
                conn.rollback()
                if client_ref_id and getattr(e, "pgcode", None) == "23505":
                    cursor.execute(
                        """SELECT credit_cost FROM client_usage_logs
                           WHERE tenant_id = %s AND client_ref_id = %s""",
                        (tenant_id, client_ref_id),
                    )
                    existing = cursor.fetchone()
                    if existing:
                        return {"credit_cost": float(existing["credit_cost"] or 0),
                                "balance_after": None, "duplicate": True}
                raise

        if balance_after is None and credit_cost > 0:
            # UPDATE tenants 影响 0 行：租户不存在（如测试租户污染），余额未扣——当场告警
            logger.error(
                f"后端日志：客户端上报计费扣减失败（租户不存在，余额未扣） "
                f"tenant={tenant_id} command={command} ref={client_ref_id}"
            )

        try:
            invalidate_tenant_cache(tenant_id)
        except Exception as e:
            logger.warning(f"客户端上报扣费后失效租户缓存失败 tenant={tenant_id}: {e}")

        logger.info(
            f"客户端上报计费 tenant={tenant_id} binding={binding_id} command={command} "
            f"quantity={quantity} session={session_id} ref={client_ref_id} "
            f"cost={credit_cost} balance_after={balance_after}"
        )
        return {"credit_cost": credit_cost, "balance_after": balance_after, "duplicate": False}

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
        created_at: Optional[datetime] = None,
    ) -> None:
        """记录非 LLM 调用（OCR/WebSearch/客户端遥测日志），credit_cost=0。

        created_at：事件真实发生时间（客户端遥测是批量 flush 的，用 flush
        时刻当事件时间会把整批行标成同一秒，真机对账时时间线严重失真）；
        None 时回退 DB 默认当前时间。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO client_usage_logs
                   (tenant_id, binding_id, session_id, association_name, stage, status, detail, credit_cost, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 0, COALESCE(%s, NOW()))""",
                (
                    tenant_id, binding_id, session_id, association_name, stage, status,
                    json.dumps(detail, ensure_ascii=False) if detail else None,
                    created_at,
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
