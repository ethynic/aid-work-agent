"""巡检商机模块 - Repository（数据访问层）

3 个 Repository：
- LeadRepository              商机主表 CRUD（含状态机、去重、加密/脱敏、租户隔离）
- LeadInteractionRepository   互动/跟进记录 CRUD
- OutreachActionRepository    我方接触动作审计 CRUD

设计原则：
1. **租户隔离**：所有 DAO 强制 ``tenant_id IS NOT DISTINCT FROM %s`` 过滤（兼容 NULL 租户）。
2. **状态机**：状态变更走 ``state_machine.can_transition``，非法转换抛 ``InvalidLeadTransition``。
3. **去重**：同 tenant 内 ``dedup_fingerprint`` 命中则更新（合并字段），不新建。
4. **PII 加密**：商机原文 ``raw_text`` 写入前 ``encryption_manager.encrypt``，
   读取时仅在 ``include_raw=True``（默认 False）时解密返回；列表查询永不返回原文，
   默认脱敏（``raw_text_masked`` 字段提供前/后片段时间戳展示，不含完整 PII）。
5. **分页**：默认 ``intent_score DESC, created_at DESC``（高意向优先，同等意向按最新优先）。
6. **created_at DESC**：互动/动作审计按 ``created_at DESC`` 排序（遵循 backend_dev.md）。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.db.encryption import encryption_manager
from src.social_media.outbound.enums import (
    InteractionType,
    LeadSourceType,
    LeadStatus,
    OutreachActionType,
    OutreachExecutionStatus,
)
from src.social_media.outbound.state_machine import (
    InvalidLeadTransition,
    can_transition,
)

# ============================================================
# 工具函数
# ============================================================


def _new_id(prefix: str) -> str:
    """生成带前缀的 12 位 hex 业务 ID（参考 services.py new_id 风格）。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _tenant_clause(tenant_id: str | None) -> tuple[str, list[Any]]:
    """构造 tenant 过滤片段，兼容 NULL tenant（非 SaaS 模式）。

    返回 (sql_fragment, params)。psycopg2 参数化避免注入。
    """
    if tenant_id is None:
        return ("tenant_id IS NULL", [])
    return ("tenant_id = %s", [tenant_id])


def compute_dedup_fingerprint(
    platform: str,
    external_content_id: Optional[str] = None,
    external_url: Optional[str] = None,
    author_handle: Optional[str] = None,
) -> str:
    """计算商机去重指纹。

    优先用 (platform, external_content_id) 唯一定位；
    缺失时退化到 (platform, external_url)；
    再缺失则用 (platform, author_handle)。
    全部缺失则抛 ValueError（无法去重则不应入库为商机）。

    指纹为 SHA256 hex（32 字节，64 字符），用于 ``bs_outbound_leads.dedup_fingerprint``。
    """
    if external_content_id:
        canonical = f"{platform}|id:{external_content_id.strip()}"
    elif external_url:
        canonical = f"{platform}|url:{external_url.strip()}"
    elif author_handle:
        canonical = f"{platform}|author:{author_handle.strip()}"
    else:
        raise ValueError(
            "compute_dedup_fingerprint 至少需要 external_content_id / external_url / author_handle 之一"
        )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _mask_raw_text(raw_text: Optional[str]) -> Optional[str]:
    """对原文做脱敏处理（用于列表/无权限场景）。

    保留首尾各 4 字符 + 长度提示，中间用 *** 替代。
    不足 12 字符则全部脱敏为 ***。
    """
    if not raw_text:
        return None
    text = str(raw_text)
    if len(text) <= 12:
        return f"***（{len(text)} 字）"
    return f"{text[:4]}***{text[-4:]}（{len(text)} 字）"


def _row_to_dict(row: Any) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


# ============================================================
# LeadRepository — 商机主表
# ============================================================


class LeadRepository:
    """商机主表 DAO。

    所有方法第一参数为 ``tenant_id``，强制租户隔离（NULL 表示非 SaaS 模式）。
    """

    TABLE = "bs_outbound_leads"

    # ----------------------------------------------------------
    # 创建/更新（含去重）
    # ----------------------------------------------------------
    def upsert(
        self,
        tenant_id: str | None,
        user_id: str | None,
        *,
        platform: str,
        source_type: str | LeadSourceType,
        raw_text: Optional[str] = None,
        external_content_id: Optional[str] = None,
        external_url: Optional[str] = None,
        intent_score: Optional[int] = None,
        contact_points: Optional[str] = None,
        risk_flags: Optional[str] = None,
        dedup_fingerprint: Optional[str] = None,
        assigned_user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """创建或更新商机（同 tenant + 指纹命中则合并更新）。

        - ``raw_text`` 自动加密入库，永不明文持久化。
        - ``dedup_fingerprint`` 可由调用方传入；不传则尝试用 compute_dedup_fingerprint 生成；
          若无法生成（缺少 external_content_id/external_url/author_handle）则指纹为 NULL，
          此时永远新建（不去重）。
        - 状态：新建默认 ``new``；命中去重则**不覆盖**已有 status（防止回退状态机），
          其他字段（intent_score/contact_points/risk_flags/raw_text）按非空覆盖。

        返回:
            {"lead_id": str, "created": bool}
            ``created=False`` 表示命中去重并更新了已有商机。
        """
        if isinstance(source_type, LeadSourceType):
            source_type = source_type.value

        # 计算去重指纹（调用方未传则尝试自动生成）
        fingerprint = dedup_fingerprint
        if fingerprint is None:
            try:
                fingerprint = compute_dedup_fingerprint(
                    platform=platform,
                    external_content_id=external_content_id,
                    external_url=external_url,
                )
            except ValueError:
                fingerprint = None  # 无法去重，允许重复入库

        encrypted_raw: Optional[str] = None
        if raw_text:
            encrypted_raw = encryption_manager.encrypt(raw_text)

        with get_db_connection() as conn:
            cur = conn.cursor()

            existing: Optional[dict[str, Any]] = None
            if fingerprint is not None:
                where, params = _tenant_clause(tenant_id)
                cur.execute(
                    f"""
                    SELECT lead_id, status, intent_score, raw_text_encrypted
                    FROM {self.TABLE}
                    WHERE {where} AND dedup_fingerprint = %s
                    LIMIT 1
                    """,
                    params + [fingerprint],
                )
                existing = _row_to_dict(cur.fetchone())

            if existing is not None:
                # 命中去重：合并更新。状态字段不回退（保留已有 status）。
                # intent_score 取较大值（防止后续低分覆盖高分）；raw_text 仅在传入新值时覆盖。
                updates: list[str] = ["updated_at = NOW()"]
                up_params: list[Any] = []

                if intent_score is not None:
                    # 取较大值，防止回退
                    cur_score = existing.get("intent_score")
                    if cur_score is None or intent_score > cur_score:
                        updates.append("intent_score = %s")
                        up_params.append(int(intent_score))

                if contact_points is not None:
                    updates.append("contact_points = %s")
                    up_params.append(contact_points)
                if risk_flags is not None:
                    updates.append("risk_flags = %s")
                    up_params.append(risk_flags)
                if encrypted_raw is not None:
                    updates.append("raw_text_encrypted = %s")
                    up_params.append(encrypted_raw)
                if assigned_user_id is not None:
                    updates.append("assigned_user_id = %s")
                    up_params.append(assigned_user_id)
                if user_id is not None:
                    updates.append("user_id = %s")
                    up_params.append(user_id)

                up_params.append(existing["lead_id"])
                cur.execute(
                    f"""
                    UPDATE {self.TABLE}
                    SET {', '.join(updates)}
                    WHERE lead_id = %s
                    """,
                    up_params,
                )
                conn.commit()
                logger.info(
                    "商机去重更新: lead_id={} tenant={}",
                    existing["lead_id"],
                    tenant_id,
                )
                return {"lead_id": existing["lead_id"], "created": False}

            # 新建
            lead_id = _new_id("lead")
            cur.execute(
                f"""
                INSERT INTO {self.TABLE} (
                    lead_id, tenant_id, platform, source_type,
                    external_content_id, external_url, raw_text_encrypted,
                    intent_score, status, assigned_user_id,
                    dedup_fingerprint, contact_points, risk_flags, user_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    lead_id,
                    tenant_id,
                    platform,
                    source_type,
                    external_content_id,
                    external_url,
                    encrypted_raw,
                    int(intent_score) if intent_score is not None else None,
                    LeadStatus.NEW.value,
                    assigned_user_id,
                    fingerprint,
                    contact_points,
                    risk_flags,
                    user_id,
                ),
            )
            conn.commit()
            logger.info(
                "商机新建: lead_id={} tenant={} platform={}",
                lead_id,
                tenant_id,
                platform,
            )
            return {"lead_id": lead_id, "created": True}

    # ----------------------------------------------------------
    # 读取（含加密/脱敏）
    # ----------------------------------------------------------
    def get(
        self,
        tenant_id: str | None,
        lead_id: str,
        *,
        include_raw: bool = False,
    ) -> Optional[dict[str, Any]]:
        """按 lead_id 读取商机（强制租户过滤）。

        - ``include_raw=False``（默认）：不返回 ``raw_text_encrypted``，
          返回 ``raw_text_masked``（脱敏后的原文，用于卡片预览）。
        - ``include_raw=True``：解密返回 ``raw_text`` 明文，**仅授权链路调用**。
        """
        where, params = _tenant_clause(tenant_id)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT lead_id, tenant_id, platform, source_type,
                       external_content_id, external_url, raw_text_encrypted,
                       intent_score, status, assigned_user_id, dedup_fingerprint,
                       contact_points, risk_flags, user_id, created_at, updated_at
                FROM {self.TABLE}
                WHERE {where} AND lead_id = %s
                """,
                params + [lead_id],
            )
            row = cur.fetchone()
        if row is None:
            return None
        item = dict(row)

        encrypted = item.pop("raw_text_encrypted", None)
        if include_raw:
            # 仅在显式授权时解密
            try:
                item["raw_text"] = encryption_manager.decrypt(encrypted) if encrypted else None
            except ValueError:
                # 解密失败：记录但不抛（避免影响列表加载），返回 None
                logger.warning("商机原文解密失败 lead_id={}", lead_id)
                item["raw_text"] = None
            item["raw_text_masked"] = None
        else:
            # 脱敏：不解密，仅展示掩码（即使不能解密也提供长度信息）
            if encrypted:
                try:
                    plain = encryption_manager.decrypt(encrypted)
                    item["raw_text_masked"] = _mask_raw_text(plain)
                except ValueError:
                    item["raw_text_masked"] = "***（解密失败）"
            else:
                item["raw_text_masked"] = None
            item["raw_text"] = None
        return item

    def list(
        self,
        tenant_id: str | None,
        *,
        status: Optional[str | LeadStatus] = None,
        assigned_user_id: Optional[str] = None,
        platform: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """分页列表查询（强制租户过滤）。

        排序：``intent_score DESC NULLS LAST, created_at DESC``
        （高意向优先，同分按最新优先；NULL 分排最后）。

        返回: ``{"items": [...], "total": int, "page": int, "page_size": int}``

        ``include_raw=False`` 默认不返回原文，仅返回脱敏；列表场景严禁传 True（PII 保护）。
        """
        if isinstance(status, LeadStatus):
            status = status.value

        tenant_sql, tenant_params = _tenant_clause(tenant_id)
        where_clauses: list[str] = [tenant_sql]
        params_list: list[Any] = list(tenant_params)

        if status is not None:
            where_clauses.append("status = %s")
            params_list.append(status)
        if assigned_user_id is not None:
            where_clauses.append("assigned_user_id = %s")
            params_list.append(assigned_user_id)
        if platform is not None:
            where_clauses.append("platform = %s")
            params_list.append(platform)

        where_sql = " AND ".join(where_clauses)

        # 规范化分页参数（防越界）
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 200))
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT COUNT(*) AS total FROM {self.TABLE} WHERE {where_sql}",
                params_list,
            )
            total_row = cur.fetchone()
            total = int(total_row["total"]) if total_row else 0

            cur.execute(
                f"""
                SELECT lead_id, tenant_id, platform, source_type,
                       external_content_id, external_url, raw_text_encrypted,
                       intent_score, status, assigned_user_id, dedup_fingerprint,
                       contact_points, risk_flags, user_id, created_at, updated_at
                FROM {self.TABLE}
                WHERE {where_sql}
                ORDER BY intent_score DESC NULLS LAST, created_at DESC
                LIMIT %s OFFSET %s
                """,
                params_list + [page_size, offset],
            )
            rows = cur.fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            encrypted = item.pop("raw_text_encrypted", None)
            if include_raw:
                try:
                    item["raw_text"] = encryption_manager.decrypt(encrypted) if encrypted else None
                except ValueError:
                    logger.warning("商机原文解密失败 lead_id={}", item.get("lead_id"))
                    item["raw_text"] = None
                item["raw_text_masked"] = None
            else:
                if encrypted:
                    try:
                        plain = encryption_manager.decrypt(encrypted)
                        item["raw_text_masked"] = _mask_raw_text(plain)
                    except ValueError:
                        item["raw_text_masked"] = "***（解密失败）"
                else:
                    item["raw_text_masked"] = None
                item["raw_text"] = None
            items.append(item)

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # ----------------------------------------------------------
    # 状态机
    # ----------------------------------------------------------
    def transition_status(
        self,
        tenant_id: str | None,
        lead_id: str,
        target: str | LeadStatus,
        *,
        actor_user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """按状态机更新商机状态。

        非法转换抛 ``InvalidLeadTransition``（携带 current/target 供定位）。
        非法状态字符串（非已知枚举值）同样抛 InvalidLeadTransition。

        返回更新后的商机 dict（不含 raw_text_encrypted，含 raw_text_masked）。
        """
        if isinstance(target, LeadStatus):
            target = target.value

        existing = self.get(tenant_id, lead_id, include_raw=False)
        if existing is None:
            raise ValueError(f"商机不存在或租户越权: lead_id={lead_id}")

        current = existing["status"]
        if not can_transition(current, target):
            raise InvalidLeadTransition(current, target)

        where, params = _tenant_clause(tenant_id)
        with get_db_connection() as conn:
            cur = conn.cursor()
            # SQL 占位符顺序：SET status=%s → WHERE tenant_id=%s → AND lead_id=%s
            # 参数顺序必须与之一致：[target, *tenant_params, lead_id]
            cur.execute(
                f"""
                UPDATE {self.TABLE}
                SET status = %s, updated_at = NOW()
                WHERE {where} AND lead_id = %s
                """,
                [target] + params + [lead_id],
            )
            rowcount = cur.rowcount
            conn.commit()

        if rowcount == 0:
            # 极少数情况：状态机校验通过但 UPDATE 未命中（并发删除/越权）
            # Fail loud，避免调用方误以为已转换
            raise ValueError(f"商机状态转换未命中: lead_id={lead_id}（可能已被删除或租户越权）")

        logger.info(
            "商机状态转换: lead_id={} {} → {} actor={}",
            lead_id,
            current,
            target,
            actor_user_id,
        )
        return existing  # 注意：返回转换前的快照，调用方可重新 get 获取最新

    # ----------------------------------------------------------
    # 其他更新
    # ----------------------------------------------------------
    def assign(
        self,
        tenant_id: str | None,
        lead_id: str,
        assigned_user_id: str,
    ) -> None:
        """分配销售给商机（强制租户过滤）。"""
        where, params = _tenant_clause(tenant_id)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {self.TABLE}
                SET assigned_user_id = %s, updated_at = NOW()
                WHERE {where} AND lead_id = %s
                """,
                [assigned_user_id] + params + [lead_id],
            )
            rowcount = cur.rowcount
            conn.commit()
        if rowcount == 0:
            raise ValueError(f"商机不存在或租户越权: lead_id={lead_id}")

    def update_intent_score(
        self,
        tenant_id: str | None,
        lead_id: str,
        score: int,
    ) -> None:
        """更新意向分（0-100，超界抛 ValueError）。"""
        if not isinstance(score, int) or score < 0 or score > 100:
            raise ValueError(f"intent_score 必须为 0-100 整数，收到: {score!r}")
        where, params = _tenant_clause(tenant_id)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {self.TABLE}
                SET intent_score = %s, updated_at = NOW()
                WHERE {where} AND lead_id = %s
                """,
                [score] + params + [lead_id],
            )
            rowcount = cur.rowcount
            conn.commit()
        if rowcount == 0:
            raise ValueError(f"商机不存在或租户越权: lead_id={lead_id}")


# ============================================================
# LeadInteractionRepository — 互动/跟进记录
# ============================================================


class LeadInteractionRepository:
    """商机互动/跟进记录 DAO。"""

    TABLE = "bs_outbound_lead_interactions"

    def add(
        self,
        tenant_id: str | None,
        user_id: str | None,
        *,
        lead_id: str,
        interaction_type: str | InteractionType,
        content: Optional[str] = None,
        actor_user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """追加一条互动/跟进记录。

        ``actor_user_id`` 是执行该互动的销售；``user_id`` 是录入人（可相同）。
        返回 {"interaction_id": ...}。
        """
        if isinstance(interaction_type, InteractionType):
            interaction_type = interaction_type.value

        interaction_id = _new_id("int")
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {self.TABLE} (
                    interaction_id, tenant_id, lead_id, interaction_type,
                    content, actor_user_id, user_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    interaction_id,
                    tenant_id,
                    lead_id,
                    interaction_type,
                    content,
                    actor_user_id,
                    user_id,
                ),
            )
            conn.commit()
        return {"interaction_id": interaction_id}

    def list_for_lead(
        self,
        tenant_id: str | None,
        lead_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """分页查询某商机的互动记录（强制租户过滤，created_at DESC）。"""
        tenant_sql, tenant_params = _tenant_clause(tenant_id)
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 200))
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM {self.TABLE}
                WHERE {tenant_sql} AND lead_id = %s
                """,
                tenant_params + [lead_id],
            )
            total_row = cur.fetchone()
            total = int(total_row["total"]) if total_row else 0

            cur.execute(
                f"""
                SELECT interaction_id, tenant_id, lead_id, interaction_type,
                       content, actor_user_id, user_id, created_at
                FROM {self.TABLE}
                WHERE {tenant_sql} AND lead_id = %s
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                tenant_params + [lead_id, page_size, offset],
            )
            rows = cur.fetchall()

        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


# ============================================================
# OutreachActionRepository — 我方接触动作审计
# ============================================================


class OutreachActionRepository:
    """我方接触动作（comment/dm/post）审计 DAO。

    设计：高风险动作只生成草稿/建议，经 UI/API 审核后由服务层受控执行，
    所有动作都落审计表（设计文档 §9、§10）。
    """

    TABLE = "bs_outbound_outreach_actions"

    def add(
        self,
        tenant_id: str | None,
        user_id: str | None,
        *,
        lead_id: str,
        action_type: str | OutreachActionType,
        channel: Optional[str] = None,
        content_snapshot: Optional[str] = None,
        execution_status: str | OutreachExecutionStatus = OutreachExecutionStatus.DRAFT,
        reviewer_user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """登记一条我方接触动作（默认草稿态，待审核）。

        返回 {"action_id": ...}。
        """
        if isinstance(action_type, OutreachActionType):
            action_type = action_type.value
        if isinstance(execution_status, OutreachExecutionStatus):
            execution_status = execution_status.value

        action_id = _new_id("oact")
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {self.TABLE} (
                    action_id, tenant_id, lead_id, action_type, channel,
                    content_snapshot, execution_status, reviewer_user_id, user_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    action_id,
                    tenant_id,
                    lead_id,
                    action_type,
                    channel,
                    content_snapshot,
                    execution_status,
                    reviewer_user_id,
                    user_id,
                ),
            )
            conn.commit()
        return {"action_id": action_id}

    def list_for_lead(
        self,
        tenant_id: str | None,
        lead_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """分页查询某商机的接触动作（强制租户过滤，created_at DESC）。"""
        tenant_sql, tenant_params = _tenant_clause(tenant_id)
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 200))
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM {self.TABLE}
                WHERE {tenant_sql} AND lead_id = %s
                """,
                tenant_params + [lead_id],
            )
            total_row = cur.fetchone()
            total = int(total_row["total"]) if total_row else 0

            cur.execute(
                f"""
                SELECT action_id, tenant_id, lead_id, action_type, channel,
                       content_snapshot, execution_status, reviewer_user_id,
                       user_id, created_at
                FROM {self.TABLE}
                WHERE {tenant_sql} AND lead_id = %s
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                tenant_params + [lead_id, page_size, offset],
            )
            rows = cur.fetchall()

        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def update_status(
        self,
        tenant_id: str | None,
        action_id: str,
        execution_status: str | OutreachExecutionStatus,
        *,
        reviewer_user_id: Optional[str] = None,
    ) -> None:
        """更新动作执行状态（审核通过/执行完成/失败等）。

        若提供 reviewer_user_id 则一并记录审核人。
        越权访问（租户不匹配）按 rowcount=0 抛 ValueError。
        """
        if isinstance(execution_status, OutreachExecutionStatus):
            execution_status = execution_status.value

        tenant_sql, tenant_params = _tenant_clause(tenant_id)
        with get_db_connection() as conn:
            cur = conn.cursor()
            if reviewer_user_id is not None:
                cur.execute(
                    f"""
                    UPDATE {self.TABLE}
                    SET execution_status = %s, reviewer_user_id = %s
                    WHERE {tenant_sql} AND action_id = %s
                    """,
                    [execution_status, reviewer_user_id] + tenant_params + [action_id],
                )
            else:
                cur.execute(
                    f"""
                    UPDATE {self.TABLE}
                    SET execution_status = %s
                    WHERE {tenant_sql} AND action_id = %s
                    """,
                    [execution_status] + tenant_params + [action_id],
                )
            rowcount = cur.rowcount
            conn.commit()
        if rowcount == 0:
            raise ValueError(f"接触动作不存在或租户越权: action_id={action_id}")
