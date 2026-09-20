"""BOSS 候选人绑定服务（B2，设计 §5.2）。

- bs_boss_conversation_bindings 查询面/管理面：list / create-pending / invalidate /
  unblock（owner-only + expected_block_epoch CAS；解阻 ≠ 任务恢复——human_required
  任务仍须既有显式 resume）；
- verified 仅接纳 Provider 真机双锚唯一命中证据（B2 无真机链路，本模块不提供
  任何写 verified 的接口；测试经受控 fixture 直接落库，生产链路 B3 接入）；
- 跨日归一（P2-B 教训）：rate_trigger_date 非当日（Asia/Shanghai）→ 原子重置
  count=0 **并清 last_rate_decision_id**（漏清会让新一天的旧 decision 被误判
  "已计数"）；
- 登录指纹：HMAC-SHA256 摘要只存 hash（login_fingerprint_hash），锚点不落日志。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.session_tasks.constants import SessionTaskError

from .constants import BUSINESS_TIMEZONE

_VERIFICATION_STATUSES = ("pending", "verified", "invalid", "expired")

# 频控触发计数/同步阻断列（gate/authorize/settle 共用的列集）
RATE_COLUMNS = (
    "rate_trigger_date", "rate_trigger_count", "last_rate_decision_id",
    "automation_blocked", "automation_block_reason", "automation_block_epoch",
    "automation_blocked_at",
)


def _now_shanghai_date_sql() -> str:
    return f"(clock_timestamp() AT TIME ZONE '{BUSINESS_TIMEZONE}')::date"


def _require_uuid(value: str, field: str) -> str:
    """UUID 预校验（非阻断 c，六审）：非法 id 直接受控 400，而非落到 DB 类型错误
    500。返回规范化字符串形态（写入/查询统一）。"""
    import uuid as _uuid

    try:
        return str(_uuid.UUID(str(value or "")))
    except (ValueError, TypeError, AttributeError):
        raise SessionTaskError(f"{field} 必须是合法 UUID", "VALIDATION_FAILED", 400) from None


def create_pending_binding(
    tenant_id: str,
    user_id: str,
    *,
    device_id: str,
    account_scope_id: str,
    candidate_name: str,
    job_id: str,
    resume_id: Optional[int] = None,
    login_fingerprint_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """创建 pending 绑定（从既有"已沟通"数据导入；verified 只能来自真机证据）。"""
    candidate_name = str(candidate_name or "").strip()
    job_id = str(job_id or "").strip()
    if not candidate_name or len(candidate_name) > 200:
        raise SessionTaskError("candidate_name 必填且不超过 200 字", "VALIDATION_FAILED", 400)
    if not job_id:
        raise SessionTaskError("job_id 必填", "VALIDATION_FAILED", 400)
    device_id = _require_uuid(device_id, "device_id")
    account_scope_id = _require_uuid(account_scope_id, "account_scope_id")
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT tenant_id, user_id, status FROM local_tool_devices WHERE id=%s",
            (str(device_id),),
        )
        device = cursor.fetchone()
        if device is None or str(device["tenant_id"]) != tenant_id or str(device["user_id"]) != user_id:
            raise SessionTaskError("设备不存在或不属于当前用户", "NOT_FOUND", 404)
        if resume_id is not None:
            cursor.execute(
                "SELECT id FROM bs_recruiting_operator_resumes WHERE id=%s AND tenant_id=%s",
                (int(resume_id), tenant_id),
            )
            if cursor.fetchone() is None:
                raise SessionTaskError("简历不存在或不属于当前租户", "NOT_FOUND", 404)
        cursor.execute(
            """
            INSERT INTO bs_boss_conversation_bindings
                (tenant_id, user_id, device_id, account_scope_id, candidate_name, job_id,
                 resume_id, identity_version, verification_status, login_fingerprint_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 'pending', %s)
            RETURNING id, identity_version, verification_status, created_at
            """,
            (tenant_id, user_id, str(device_id), str(account_scope_id), candidate_name,
             job_id, resume_id, login_fingerprint_hash),
        )
        row = dict(cursor.fetchone())
        conn.commit()
    return {"conversation_binding_id": str(row["id"]), **row}


def list_bindings(tenant_id: str, user_id: str, device_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    """属主查询绑定（不含加密证据/指纹摘要；列表只暴露状态与版本）。

    P2-1（七审）：device_id 过滤值非法时受控 400（不落到 DB 类型错误 500）。"""
    from src.db.database import get_db_connection

    where = "tenant_id=%s AND user_id=%s"
    params: List[Any] = [tenant_id, user_id]
    if device_id:
        where += " AND device_id=%s"
        params.append(_require_uuid(device_id, "device_id"))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT id, device_id, account_scope_id, candidate_name, job_id, resume_id,
                   identity_version, verification_status, verified_at, expires_at,
                   automation_blocked, automation_block_epoch, created_at
            FROM bs_boss_conversation_bindings WHERE {where}
            ORDER BY created_at DESC LIMIT %s
            """,
            (*params, max(1, min(int(limit), 200))),
        )
        return [dict(r) for r in cursor.fetchall()]


def invalidate_binding(tenant_id: str, user_id: str, binding_id: str) -> Dict[str, Any]:
    """属主标记 invalid（人工失效；不改变阻断状态）。"""
    binding_id = _require_uuid(binding_id, "binding_id")
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE bs_boss_conversation_bindings SET verification_status='invalid', updated_at=NOW()
            WHERE tenant_id=%s AND id=%s AND user_id=%s
            RETURNING id
            """,
            (tenant_id, str(binding_id), user_id),
        )
        row = cursor.fetchone()
        conn.commit()
    if row is None:
        raise SessionTaskError("绑定不存在或无权访问", "NOT_FOUND", 404)
    return {"conversation_binding_id": str(row["id"]), "verification_status": "invalid"}


def unblock_binding(
    tenant_id: str, user_id: str, binding_id: str, *, expected_block_epoch: int
) -> Dict[str, Any]:
    """人工解阻（B4 提前交付，设计 §5.5.5 冻结语义）：

    - owner-only：user_id 必须匹配绑定属主；
    - CAS：expected_block_epoch 必须等于当前值——旧请求/并发解阻败者 409，禁止
      覆盖较新阻断；
    - 成功：清除 blocked/reason/time **并将 block epoch 再递增**（请求必须携带
      新 epoch 才能再次解阻）+ 脱敏审计；
    - 解阻 ≠ 任务恢复：human_required 任务仍须既有显式 resume（本函数不触任务行）。
    """
    if not isinstance(expected_block_epoch, int) or isinstance(expected_block_epoch, bool) or expected_block_epoch < 0:
        raise SessionTaskError("expected_block_epoch 必须是非负整数", "VALIDATION_FAILED", 400)
    binding_id = _require_uuid(binding_id, "binding_id")
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE bs_boss_conversation_bindings
            SET automation_blocked=FALSE, automation_block_reason=NULL, automation_blocked_at=NULL,
                automation_block_epoch=automation_block_epoch+1, updated_at=NOW()
            WHERE tenant_id=%s AND id=%s AND user_id=%s
              AND automation_blocked=TRUE AND automation_block_epoch=%s
            RETURNING id, automation_block_epoch
            """,
            (tenant_id, str(binding_id), user_id, int(expected_block_epoch)),
        )
        row = cursor.fetchone()
        if row is None:
            # 归因：不存在/非属主 vs epoch CAS 失败 vs 未阻断
            cursor.execute(
                "SELECT id, user_id, automation_blocked, automation_block_epoch "
                "FROM bs_boss_conversation_bindings WHERE tenant_id=%s AND id=%s",
                (tenant_id, str(binding_id)),
            )
            current = cursor.fetchone()
            conn.rollback()
            if current is None or str(current["user_id"]) != user_id:
                raise SessionTaskError("绑定不存在或无权访问", "NOT_FOUND", 404)
            if not current["automation_blocked"]:
                raise SessionTaskError("绑定未处于阻断状态", "CONFLICT", 409)
            raise SessionTaskError(
                "阻断代已前进（expected_block_epoch 与当前不一致），禁止清除较新阻断",
                "CONFLICT", 409,
            )
        new_epoch = int(row["automation_block_epoch"])
        from src.desktop_automation import audit

        audit.insert_audit(
            cursor, tenant_id, "boss_binding_unblock", "binding", str(row["id"]),
            user_id=user_id, scenario_key=None,
            detail={"expected_block_epoch": int(expected_block_epoch), "new_block_epoch": new_epoch},
        )
        conn.commit()
    return {"conversation_binding_id": str(binding_id), "automation_blocked": False, "automation_block_epoch": new_epoch}


def normalize_rate_day(cursor, tenant_id: str, binding_id: str, binding_row: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """跨日归一（guard 持锁路径共用）：rate_trigger_date 非当日（Asia/Shanghai）→
    原子重置 rate_trigger_count=0 **并清 last_rate_decision_id**（P2-B 教训）。

    binding_row 提供时复用已锁行数据判断（免重复读）；仍以 UPDATE 的 WHERE 条件
    原子执行（date 不同的行才被更新）。返回归一后的 {date, count}（DB 侧当日口径）。"""
    cursor.execute(f"SELECT {_now_shanghai_date_sql()} AS d")
    today = cursor.fetchone()["d"]
    stored_date = binding_row.get("rate_trigger_date") if binding_row else None
    if stored_date is None or str(stored_date) != str(today):
        cursor.execute(
            """
            UPDATE bs_boss_conversation_bindings
            SET rate_trigger_date=%s, rate_trigger_count=0, last_rate_decision_id=NULL, updated_at=NOW()
            WHERE tenant_id=%s AND id=%s
              AND (rate_trigger_date IS NULL OR rate_trigger_date <> %s)
            """,
            (today, tenant_id, str(binding_id), today),
        )
        binding_row = None  # 行数据已变，调用方需重读
    return {"date": today, "reset": stored_date is None or str(stored_date) != str(today)}


# ---------------------------------------------------------------------------
# BindingResolver 契约实现（描述器 binding_resolver；B1.2 九处 #5/#7 查询面）
# ---------------------------------------------------------------------------


def _load_binding_row(cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, tenant_id, user_id, device_id, account_scope_id,
               account_scope_id AS account_binding_id,
               candidate_name, job_id,
               resume_id, identity_version, verification_status, login_fingerprint_hash,
               encrypted_identity_evidence, verified_at, expires_at,
               rate_trigger_date, rate_trigger_count, last_rate_decision_id,
               automation_blocked, automation_block_reason, automation_block_epoch,
               automation_blocked_at, created_at, updated_at
        FROM bs_boss_conversation_bindings WHERE tenant_id=%s AND id=%s
        """,
        (tenant_id, str(binding_id)),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def is_binding_verified_valid(row: Optional[Dict[str, Any]]) -> bool:
    """verified 且验证字段完整且未过期（expires_at NULL 不视为无限有效）。"""
    from datetime import datetime, timezone

    if not row or row.get("verification_status") != "verified":
        return False
    if int(row.get("identity_version") or 0) < 1:
        return False
    if not row.get("verified_at") or not row.get("encrypted_identity_evidence") or row.get("expires_at") is None:
        return False
    expires_at = row["expires_at"]
    expires_at = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


class BossBindingResolver:
    """BOSS 绑定查询面（BindingResolver 契约；写操作仍由通用层/本模块管理面持有）。"""

    def get_binding_by_id(self, cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        return _load_binding_row(cursor, tenant_id, binding_id)

    def get_runtime_identity(self, cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        """运行时身份字段（claim 响应身份段）：identity_version + 指纹摘要存在性
        （摘要本身不下发，仅 expected_login_fingerprint_hash 由服务端读取下发）。"""
        cursor.execute(
            """
            SELECT id, identity_version, login_fingerprint_hash, verification_status, expires_at
            FROM bs_boss_conversation_bindings WHERE tenant_id=%s AND id=%s
            """,
            (tenant_id, str(binding_id)),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        result = dict(row)
        result["conversation_label"] = result.pop("verification_status")
        return result

    def is_valid_for_allocation(self, conn, tenant_id: str, binding_id: str) -> bool:  # noqa: ANN001
        return is_binding_verified_valid(self.get_binding_by_id(conn.cursor(), tenant_id, binding_id))

    def resolve_draft_targets(self, conn, tenant_id: str, user_id: str, device_id: str, resolution_invocation_id: str):  # noqa: ANN001
        from src.session_tasks.scenario_descriptor import ScenarioDescriptorError

        raise ScenarioDescriptorError("BOSS 场景不支持名称定位路径（models 层已约束互斥）")

    def ensure_valid_for_publish(self, binding: Dict[str, Any]) -> None:  # noqa: ANN001
        from src.session_tasks.constants import ERR_VALIDATION_FAILED

        if not is_binding_verified_valid(binding):
            raise SessionTaskError(
                "BOSS 候选人绑定尚未通过真机验证或已过期（pending/invalid/expired 不可发布）",
                ERR_VALIDATION_FAILED, 409,
            )

    def runtime_target_policy(self, binding_row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        return None

    def account_identity_version(self, binding_row: Optional[Dict[str, Any]]) -> int:  # noqa: ANN001
        # 登录态即身份（account_identity_version=0，设计 §1 账号模式）
        return 0

    def list_bindings(self, tenant_id: str, user_id: str, device_id: str, limit: int):  # noqa: ANN001
        return list_bindings(tenant_id, user_id, device_id, limit)

    def create_binding(self, tenant_id: str, user_id: str, device_id: str,
                       account_binding_id: str, binding_type: str, label: str):  # noqa: ANN001
        """通用绑定路由（B1.2 POST /api/weixin-conversation/bindings 场景分派）到
        BOSS create-pending 的有损映射：binding_type=job_id、label=candidate_name、
        account_binding_id=account_scope_id。生产主入口是专属 API
        /api/boss-conversation/bindings（可携带 resume_id/指纹）。

        binding_type 不是合法 job 标识（空）时受控 400（不猜测语义）。"""
        if not str(binding_type or "").strip() or not str(label or "").strip():
            raise SessionTaskError(
                "BOSS 绑定创建需 job_id(binding_type) 与 candidate_name(label)；"
                "请使用 /api/boss-conversation/bindings",
                "VALIDATION_FAILED", 400,
            )
        return create_pending_binding(
            tenant_id, user_id,
            device_id=device_id, account_scope_id=account_binding_id,
            candidate_name=label, job_id=binding_type,
        )
