"""微信营销自动化服务层（automations 生命周期 / 手动 run / 查询 / resolve-retry）

P2-A 交付模块+服务层（API 路由在 P2-A2）。发布/暂停/恢复/归档在一个事务内完成
weixin 业务行 + 底座 subject/schedule/run 的状态推进（R40 发布不可变、R46 CAS+epoch）；
subject/schedule 的行级操作复用 desktop_automation 的游标级函数，本模块不复制其语义。

锁顺序（继承 R12 并前置业务行）：automations 行 → task subject → schedule → occurrence/run
→ delivery → 在途 invocation（R49 取消路径；write_authorize 侧为 subject → run →
invocation → delivery → quota，两序无交叉死锁）。
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import psycopg2
from loguru import logger
from psycopg2.extras import Json

from src.db.database import get_db_connection
from src.desktop_automation import audit as da_audit
from src.desktop_automation import deliveries as da_deliveries
from src.desktop_automation import occurrences as da_occurrences
from src.desktop_automation import runs as da_runs
from src.desktop_automation import subjects as da_subjects
from src.desktop_automation.adapters import (
    AdapterContext,
    TrustedAdapterRegistry,
)
from src.desktop_automation.constants import (
    BUSINESS_KIND_DESKTOP_AUTOMATION,
    EFFECT_NONE,
    PHASE_PREPARED,
    TASK_STATUS_ACTIVE,
    TRIGGER_KIND_MANUAL,
    manual_trigger_key,
)
from src.desktop_automation.executor import (
    DEFAULT_OPERATION_DEADLINE_SECONDS,
    build_v2_operation_arguments,
    derive_resource_key,
)
from src.weixin_marketing import content, triggers
from src.weixin_marketing.adapters import WeixinFixedContentAdapter
from src.weixin_marketing.config import get_weixin_marketing_config, tenant_allowed
from src.weixin_marketing.constants import (
    ACTOR_TYPE_USER,
    AUDIT_AUTOMATION_ARCHIVED,
    AUDIT_AUTOMATION_CREATED,
    AUDIT_AUTOMATION_PAUSED,
    AUDIT_AUTOMATION_PUBLISHED,
    AUDIT_AUTOMATION_RESUMED,
    AUDIT_DELIVERY_RESOLVED,
    AUDIT_DELIVERY_RETRIED,
    AUDIT_DRAFT_UPDATED,
    AUDIT_MANUAL_RUN_REQUESTED,
    AUDIT_RUN_CANCEL_REQUESTED,
    AUTOMATION_STATUS_ACTIVE,
    AUTOMATION_STATUS_ARCHIVED,
    AUTOMATION_STATUS_DRAFT,
    AUTOMATION_STATUS_PAUSED,
    REVISION_STATUS_DRAFT,
    REVISION_STATUS_PUBLISHED,
    REVISION_STATUS_SUPERSEDED,
    SCENARIO_KEY,
)
from src.weixin_marketing.models import (
    AutomationCreateInput,
    DeliveryResolveInput,
    DraftUpdateInput,
    PublishInput,
    TriggerConfig,
    VersionedActionInput,
    parse_blocks,
    parse_trigger,
)

# 自动化状态机：按目标动作限定来源状态（publish 单独走 publish()）
_PAUSE_FROM = {AUTOMATION_STATUS_ACTIVE}
_RESUME_FROM = {AUTOMATION_STATUS_PAUSED}
_ARCHIVE_FROM = {AUTOMATION_STATUS_DRAFT, AUTOMATION_STATUS_ACTIVE, AUTOMATION_STATUS_PAUSED}

_AUTOMATION_COLUMNS = (
    "id, tenant_id, user_id, name, status, active_revision_id, draft_revision_id, "
    "owner_scope, version, pause_reason, created_at, updated_at"
)
_REVISION_COLUMNS = (
    "id, tenant_id, automation_id, user_id, revision_no, executor_type, status, trigger_json, "
    "policy_json, group_binding_id, content_hash, authorized_by, authorization_source, "
    "source_message_id, published_at, created_at, updated_at"
)


# ==================== 错误类型（API 层映射 404/409/422/429）====================


class WeixinMarketingError(Exception):
    """服务层基类（message 中文说明；API 层映射 envelope error code）"""


class NotFoundError(WeixinMarketingError):
    """跨租户/不存在/非属主统一 404（猜别人的 ID 统一 404）"""


class ConflictError(WeixinMarketingError):
    """版本 CAS 冲突/状态不允许/重复许可（409）"""


class TenantNotAllowedError(ConflictError):
    """租户不在 weixin_marketing.tenant_allowlist 白名单（409 TENANT_NOT_ALLOWED；
    CR-P1-2：与 dispatch 域时间槽接纳的 tenant_allowed 门控语义对齐）"""


class RetryEvidenceRequiredError(ConflictError):
    """未知效果重试缺人工证据（409 RETRY_EVIDENCE_REQUIRED，R52：须先经 resolve
    记录 decision='confirmed_not_sent' 方可重试）"""


class WeixinValidationError(WeixinMarketingError):
    """配置/语义校验失败（422），携带 field_errors"""


class ConfigurationError(WeixinMarketingError):
    """模块配置缺失（适配器未注册：enabled=false 或受信注册点未执行）——API 层 503/409 语义"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ==================== 游标级行访问 ====================


def _lock_automation_on(cursor, tenant_id: str, automation_id: str) -> Optional[Dict[str, Any]]:
    cursor.execute(
        f"SELECT {_AUTOMATION_COLUMNS} FROM bs_weixin_marketing_automations "
        "WHERE tenant_id = %s AND id = %s FOR UPDATE",
        (tenant_id, automation_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _get_automation_on(cursor, tenant_id: str, automation_id: str) -> Optional[Dict[str, Any]]:
    cursor.execute(
        f"SELECT {_AUTOMATION_COLUMNS} FROM bs_weixin_marketing_automations "
        "WHERE tenant_id = %s AND id = %s",
        (tenant_id, automation_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def get_automation_row(tenant_id: str, automation_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        return _get_automation_on(cursor, tenant_id, automation_id)


def _get_revision_on(cursor, tenant_id: str, revision_id: str) -> Optional[Dict[str, Any]]:
    cursor.execute(
        f"SELECT {_REVISION_COLUMNS} FROM bs_weixin_marketing_revisions "
        "WHERE tenant_id = %s AND id = %s",
        (tenant_id, revision_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def get_revision_row(tenant_id: str, revision_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        return _get_revision_on(cursor, tenant_id, revision_id)


def _list_revision_blocks_on(cursor, tenant_id: str, revision_id: str) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT position, kind, text_content, url, asset_id, payload_hash
        FROM bs_weixin_marketing_content_blocks
        WHERE tenant_id = %s AND revision_id = %s
        ORDER BY position
        """,
        (tenant_id, revision_id),
    )
    return [dict(r) for r in cursor.fetchall()]


def _list_run_deliveries_on(cursor, tenant_id: str, run_id: str) -> List[Dict[str, Any]]:
    """持锁事务内读 run deliveries（避免跨连接快照；与底座列集一致）"""
    cursor.execute(
        """
        SELECT id, tenant_id, run_id, scenario_key, task_ref, revision_ref, user_id, position,
               operation, provider_key, target_ref, target_handle, target_version,
               payload_ref, payload_hash, state, effect, phase
        FROM desktop_automation_deliveries
        WHERE tenant_id = %s AND run_id = %s
        ORDER BY position
        """,
        (tenant_id, run_id),
    )
    return [dict(r) for r in cursor.fetchall()]


def _insert_weixin_audit_on(
    cursor,
    tenant_id: str,
    action: str,
    *,
    user_id: Optional[str] = None,
    automation_id: Optional[str] = None,
    run_id: Optional[str] = None,
    from_version: Optional[int] = None,
    to_version: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
    actor_type: str = ACTOR_TYPE_USER,
) -> None:
    """追加微信业务审计行（details 只存受控引用/摘要，不写正文/群名）"""
    cursor.execute(
        """
        INSERT INTO bs_weixin_marketing_audit_events
            (tenant_id, user_id, automation_id, run_id, action, actor_type, actor_id,
             from_version, to_version, details_redacted)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            tenant_id, user_id,
            str(automation_id) if automation_id else None,
            str(run_id) if run_id else None,
            action, actor_type, user_id, from_version, to_version,
            Json(details or {}),
        ),
    )


def _blocks_to_storable(blocks_spec: List[Any]) -> List[Dict[str, Any]]:
    """Pydantic 块 → 存储字典（互斥字段 NULL 化，匹配 DB CHECK）"""
    storable = []
    for block in blocks_spec:
        kind = block.type
        storable.append(
            {
                "kind": kind,
                "text_content": block.text_content if kind == "text" else None,
                "url": block.url if kind == "link" else None,
                "asset_id": block.asset_id if kind == "image" else None,
            }
        )
    return storable


def _rewrite_draft_blocks_on(
    cursor, tenant_id: str, user_id: str, revision_id: str, blocks_spec: List[Any]
) -> List[Dict[str, Any]]:
    """草稿 revision 内容块整体重写（发布后不可变，仅 draft 允许重写）"""
    cursor.execute(
        "DELETE FROM bs_weixin_marketing_content_blocks "
        "WHERE tenant_id = %s AND revision_id = %s",
        (tenant_id, revision_id),
    )
    frozen = content.freeze_blocks(_blocks_to_storable(blocks_spec))
    for block in frozen:
        cursor.execute(
            """
            INSERT INTO bs_weixin_marketing_content_blocks
                (tenant_id, revision_id, user_id, position, kind, text_content, url, asset_id, payload_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant_id, revision_id, user_id, block["position"], block["kind"],
                block["text_content"], block["url"], block["asset_id"], block["payload_hash"],
            ),
        )
    return frozen


def _adapter() -> WeixinFixedContentAdapter:
    """require 语义（P2-2）：未注册即抛 ConfigurationError，不静默自建兜底——
    enabled=false 下服务层写路径 fail-loud，读路径不经本函数"""
    adapter = TrustedAdapterRegistry.get(SCENARIO_KEY)
    if adapter is None:
        raise ConfigurationError(
            f"场景 {SCENARIO_KEY} 适配器未注册（weixin_marketing.enabled=false 或受信注册点未执行）"
        )
    return adapter


def _adapter_ctx(tenant_id: str, user_id: str, automation_id: str, revision_id: str) -> AdapterContext:
    return AdapterContext(
        tenant_id=tenant_id, user_id=user_id, scenario_key=SCENARIO_KEY,
        task_ref=str(automation_id), revision_ref=str(revision_id),
    )


# ==================== 服务 ====================


class WeixinMarketingService:
    """微信营销自动化服务（无状态；同步 psycopg2，FastAPI 层 asyncio.to_thread）"""

    # ---------- 创建 / 查询 ----------

    def create_automation(
        self,
        tenant_id: str,
        user_id: str,
        payload: AutomationCreateInput,
        *,
        idempotency: Any = None,
    ) -> Dict[str, Any]:
        """创建草稿：automation(draft) + revision(draft, no=1) + 冻结内容块；无发送副作用。

        R51：业务写入、审计与幂等完成记录（idempotency.write_on）同一事务提交——
        「业务已提交而响应未保存」不可达；无幂等上下文时行为不变。
        """
        triggers.validate_blocks(_blocks_to_storable(parse_blocks(
            [b.model_dump() for b in payload.blocks]
        )))
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bs_weixin_marketing_automations
                    (tenant_id, user_id, name, status, owner_scope, version)
                VALUES (%s, %s, %s, 'draft', 'owner', 1)
                RETURNING id
                """,
                (tenant_id, user_id, payload.name),
            )
            automation_id = str(cursor.fetchone()["id"])
            revision_id = self._create_revision_on(
                cursor, tenant_id, user_id, automation_id, 1,
                trigger=payload.trigger, blocks=list(payload.blocks),
                group_binding_id=payload.group_binding_id, policy=payload.policy,
            )
            cursor.execute(
                "UPDATE bs_weixin_marketing_automations SET draft_revision_id = %s, "
                "updated_at = NOW() WHERE tenant_id = %s AND id = %s",
                (revision_id, tenant_id, automation_id),
            )
            _insert_weixin_audit_on(
                cursor, tenant_id, AUDIT_AUTOMATION_CREATED, user_id=user_id,
                automation_id=automation_id, to_version=1,
                details={"revision_id": revision_id},
            )
            detail = self._automation_detail_on(cursor, tenant_id, automation_id, user_id)
            if idempotency is not None:
                idempotency.write_on(cursor, status_code=200, data=detail)
            conn.commit()
        logger.info(
            f"后端日志：weixin_marketing 创建草稿 tenant={tenant_id} automation={automation_id}"
        )
        return detail

    def _create_revision_on(
        self,
        cursor,
        tenant_id: str,
        user_id: str,
        automation_id: str,
        revision_no: int,
        *,
        trigger: TriggerConfig,
        blocks: List[Any],
        group_binding_id: str,
        policy: Dict[str, Any],
    ) -> str:
        trigger_json = trigger.model_dump(mode="json")
        storable = _blocks_to_storable(blocks)
        frozen = content.freeze_blocks(storable)
        cursor.execute(
            """
            INSERT INTO bs_weixin_marketing_revisions
                (tenant_id, automation_id, user_id, revision_no, executor_type, status,
                 trigger_json, policy_json, group_binding_id, content_hash)
            VALUES (%s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s)
            RETURNING id
            """,
            (
                tenant_id, automation_id, user_id, revision_no, SCENARIO_KEY,
                Json(trigger_json), Json(policy or {}), group_binding_id,
                content.content_hash_of(frozen),
            ),
        )
        revision_id = str(cursor.fetchone()["id"])
        for block in frozen:
            cursor.execute(
                """
                INSERT INTO bs_weixin_marketing_content_blocks
                    (tenant_id, revision_id, user_id, position, kind, text_content, url, asset_id, payload_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id, revision_id, user_id, block["position"], block["kind"],
                    block["text_content"], block["url"], block["asset_id"], block["payload_hash"],
                ),
            )
        return revision_id

    def list_automations(
        self,
        tenant_id: str,
        user_id: str,
        *,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        trigger_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        filters = ["tenant_id = %s", "user_id = %s"]
        params: List[Any] = [tenant_id, user_id]
        if keyword:
            filters.append("name ILIKE %s")
            params.append(f"%{keyword}%")
        if status:
            filters.append("status = %s")
            params.append(status)
        if trigger_type:
            # P2-A2（R46 API 表）：按当前生效 revision（草稿优先，回退 active）的
            # 触发类型过滤；服务层最小增参，语义与详情页 draft_trigger 一致
            filters.append(
                "COALESCE(draft_revision_id, active_revision_id) IN ("
                "SELECT id FROM bs_weixin_marketing_revisions "
                "WHERE tenant_id = %s AND trigger_json->>'type' = %s)"
            )
            params.extend([tenant_id, trigger_type])
        where = " AND ".join(filters)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COUNT(*) AS c FROM bs_weixin_marketing_automations WHERE {where}",
                tuple(params),
            )
            total = int(cursor.fetchone()["c"])
            cursor.execute(
                f"SELECT {_AUTOMATION_COLUMNS} FROM bs_weixin_marketing_automations "
                f"WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (*params, page_size, (page - 1) * page_size),
            )
            items = [dict(r) for r in cursor.fetchall()]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def get_automation_detail(
        self, tenant_id: str, automation_id: str, user_id: str
    ) -> Dict[str, Any]:
        """配置、版本、最近结果摘要（不含正文——仅块元数据 + hash）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            return self._automation_detail_on(cursor, tenant_id, automation_id, user_id)

    @staticmethod
    def _automation_detail_on(
        cursor, tenant_id: str, automation_id: str, user_id: str
    ) -> Dict[str, Any]:
        """详情组装（游标级，R51：幂等事务内于 commit 前构建响应，见 create_automation）"""
        automation = _get_automation_on(cursor, tenant_id, automation_id)
        if automation is None or automation.get("user_id") != user_id:
            raise NotFoundError("自动化任务不存在")
        cursor.execute(
            f"SELECT {_REVISION_COLUMNS} FROM bs_weixin_marketing_revisions "
            "WHERE tenant_id = %s AND automation_id = %s ORDER BY revision_no DESC",
            (tenant_id, automation_id),
        )
        revisions = [dict(r) for r in cursor.fetchall()]
        cursor.execute(
            """
            SELECT id, state, created_at, finished_at FROM desktop_automation_runs
            WHERE tenant_id = %s AND scenario_key = %s AND task_ref = %s
            ORDER BY created_at DESC LIMIT 5
            """,
            (tenant_id, SCENARIO_KEY, automation_id),
        )
        recent_runs = [dict(r) for r in cursor.fetchall()]
        return {
            "automation": automation,
            "revisions": [
                {k: v for k, v in r.items() if k not in ("trigger_json", "policy_json")}
                for r in revisions
            ],
            "draft_trigger": next(
                (r["trigger_json"] for r in revisions if r["status"] == REVISION_STATUS_DRAFT), None
            ),
            "recent_runs": recent_runs,
        }

    def load_revision_config(self, tenant_id: str, revision_ref: str) -> Dict[str, Any]:
        """冻结 revision → executor 编译输入（未来 run_dispatch worker 调用）。

        P2-10：revision 状态 guard——仅 published/superseded 可编译（draft 拒绝），
        executor 不应驱动未发布配置。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            revision = _get_revision_on(cursor, tenant_id, revision_ref)
            if revision is None:
                raise NotFoundError("revision 不存在")
            if revision.get("status") not in (REVISION_STATUS_PUBLISHED, REVISION_STATUS_SUPERSEDED):
                raise ConflictError(
                    f"revision 状态不可编译: {revision.get('status')}（仅已发布冻结版本）"
                )
            blocks = _list_revision_blocks_on(cursor, tenant_id, revision_ref)
        return {
            # P2-3：自描述 revision_ref——executor.prepare_claimed_run 据此断言
            # 配置与被领取 run 一致（R50 定向领取的双保险）
            "revision_ref": revision_ref,
            "trigger": revision.get("trigger_json") or {},
            "blocks": content.compile_blocks_for_revision(revision_ref, blocks),
            "group_binding_id": str(revision.get("group_binding_id") or ""),
            "policy": revision.get("policy_json") or {},
        }

    # ---------- 草稿编辑（CAS）----------

    def update_draft(
        self, tenant_id: str, automation_id: str, user_id: str, payload: DraftUpdateInput
    ) -> Dict[str, Any]:
        """草稿编辑（If-Match/version CAS，冲突 409）。

        - 存在草稿 revision → 原地重写（发布前可反复编辑）；
        - 无草稿且任务 active/paused → 以本次提交内容新建下一号草稿（发布后迭代）；
        - 归档任务不可编辑。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            automation = _lock_automation_on(cursor, tenant_id, automation_id)
            if automation is None or automation.get("user_id") != user_id:
                raise NotFoundError("自动化任务不存在")
            if automation["version"] != payload.expected_version:
                raise ConflictError(
                    f"版本冲突：期望 {payload.expected_version}，实际 {automation['version']}"
                )
            draft_id = automation.get("draft_revision_id")
            new_draft_id: Optional[str] = None
            if draft_id:
                revision = _get_revision_on(cursor, tenant_id, str(draft_id))
                if revision is None or revision["status"] != REVISION_STATUS_DRAFT:
                    raise ConflictError("草稿 revision 已不可编辑")
                trigger = payload.trigger or parse_trigger(dict(revision.get("trigger_json") or {}))
                if payload.blocks is not None:
                    blocks_spec = list(payload.blocks)
                else:
                    stored_blocks = _list_revision_blocks_on(cursor, tenant_id, str(draft_id))
                    # P2-A2 修复：按 kind 只带互斥字段（None 补齐会被严格模型
                    # extra=forbid 拒绝，导致仅改 name/trigger 的局部更新 500）
                    blocks_spec = parse_blocks([
                        {
                            key: value
                            for key, value in (
                                ("type", b["kind"]),
                                ("text_content", b.get("text_content")),
                                ("url", b.get("url")),
                                ("asset_id", str(b["asset_id"]) if b.get("asset_id") else None),
                            )
                            if value is not None
                        }
                        for b in stored_blocks
                    ])
                group_binding_id = (
                    payload.group_binding_id or str(revision.get("group_binding_id") or "")
                )
                policy = payload.policy if payload.policy is not None else (revision.get("policy_json") or {})
                if not blocks_spec:
                    raise WeixinValidationError("内容块不能为空")
                frozen = _rewrite_draft_blocks_on(cursor, tenant_id, user_id, str(draft_id), blocks_spec)
                cursor.execute(
                    """
                    UPDATE bs_weixin_marketing_revisions
                    SET trigger_json = %s, policy_json = %s, group_binding_id = %s,
                        content_hash = %s, updated_at = NOW()
                    WHERE tenant_id = %s AND id = %s
                    """,
                    (
                        Json(trigger.model_dump(mode="json")), Json(policy or {}),
                        group_binding_id, content.content_hash_of(frozen),
                        tenant_id, str(draft_id),
                    ),
                )
            else:
                # 发布后迭代：以完整提交新建下一号草稿（缺任一字段即 422）
                if automation["status"] not in (AUTOMATION_STATUS_ACTIVE, AUTOMATION_STATUS_PAUSED):
                    raise ConflictError("无可编辑草稿（已归档）")
                if payload.trigger is None or payload.blocks is None or not payload.group_binding_id:
                    raise WeixinValidationError("新建草稿需提供完整 trigger/blocks/group_binding_id")
                cursor.execute(
                    """
                    SELECT COALESCE(MAX(revision_no), 0) + 1 AS next_no
                    FROM bs_weixin_marketing_revisions
                    WHERE tenant_id = %s AND automation_id = %s
                    """,
                    (tenant_id, automation_id),
                )
                next_no = int(cursor.fetchone()["next_no"])
                new_draft_id = self._create_revision_on(
                    cursor, tenant_id, user_id, automation_id, next_no,
                    trigger=payload.trigger, blocks=list(payload.blocks),
                    group_binding_id=payload.group_binding_id,
                    policy=payload.policy or {},
                )
                cursor.execute(
                    "UPDATE bs_weixin_marketing_automations SET draft_revision_id = %s "
                    "WHERE tenant_id = %s AND id = %s",
                    (new_draft_id, tenant_id, automation_id),
                )
            if payload.name is not None:
                cursor.execute(
                    "UPDATE bs_weixin_marketing_automations SET name = %s WHERE tenant_id = %s AND id = %s",
                    (payload.name, tenant_id, automation_id),
                )
            new_version = automation["version"] + 1
            cursor.execute(
                "UPDATE bs_weixin_marketing_automations SET version = %s, updated_at = NOW() "
                "WHERE tenant_id = %s AND id = %s",
                (new_version, tenant_id, automation_id),
            )
            _insert_weixin_audit_on(
                cursor, tenant_id, AUDIT_DRAFT_UPDATED, user_id=user_id,
                automation_id=automation_id, run_id=None,
                from_version=automation["version"], to_version=new_version,
                details={"revision_id": str(new_draft_id or draft_id), "new_draft": bool(new_draft_id)},
            )
            conn.commit()
        return self.get_automation_detail(tenant_id, automation_id, user_id)

    # ---------- 静态校验（无发送）----------

    def validate_automation(
        self, tenant_id: str, automation_id: str, user_id: str, *, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """静态校验 + 未来 5 次触发预览（无任何发送副作用）"""
        now = _aware(now or utcnow())
        with get_db_connection() as conn:
            cursor = conn.cursor()
            automation = _get_automation_on(cursor, tenant_id, automation_id)
            if automation is None or automation.get("user_id") != user_id:
                raise NotFoundError("自动化任务不存在")
            revision_id = str(
                automation.get("draft_revision_id") or automation.get("active_revision_id") or ""
            )
            revision = _get_revision_on(cursor, tenant_id, revision_id) if revision_id else None
            if revision is None:
                raise ConflictError("无可校验的 revision")
            blocks = _list_revision_blocks_on(cursor, tenant_id, revision_id)
        errors: List[Dict[str, str]] = []
        warnings: List[Dict[str, str]] = []
        trigger_raw = revision.get("trigger_json") or {}
        next_fires: List[str] = []
        try:
            trigger = parse_trigger(dict(trigger_raw))
            next_fires = [
                f.isoformat() for f in triggers.preview_next_fires(trigger, count=5, now=now)
            ]
            triggers.compile_trigger_specs(trigger)  # 门控/频率上限严校验
        except ValueError as e:
            errors.append({"field": "trigger", "message": str(e)})
        try:
            triggers.validate_blocks(blocks)
        except ValueError as e:
            errors.append({"field": "blocks", "message": str(e)})
        binding_id = str(revision.get("group_binding_id") or "")
        if not binding_id:
            errors.append({"field": "group_binding_id", "message": "缺少目标群绑定"})
        else:
            cursor_binding = None
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT state FROM bs_weixin_marketing_group_bindings "
                    "WHERE tenant_id = %s AND id = %s",
                    (tenant_id, binding_id),
                )
                row = cursor.fetchone()
                cursor_binding = dict(row) if row else None
            if cursor_binding is None:
                errors.append({"field": "group_binding_id", "message": "目标群绑定不存在"})
            elif cursor_binding["state"] != "complete":
                errors.append(
                    {"field": "group_binding_id", "message": f"群绑定状态不可发送: {cursor_binding['state']}"}
                )
        if trigger_raw.get("type") == "once":
            run_at = trigger_raw.get("run_at")
            if run_at:
                fire = datetime.fromisoformat(str(run_at).replace("Z", "+00:00"))
                if _aware(fire) < now:
                    warnings.append({"field": "trigger", "message": "一次性触发时刻已过期"})
        return {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
            "next_fires": next_fires,
            "revision_id": revision_id,
        }

    # ---------- 发布（R40 发布不可变，单事务）----------

    def publish(
        self,
        tenant_id: str,
        automation_id: str,
        user_id: str,
        payload: PublishInput,
        *,
        idempotency: Any = None,
    ) -> Dict[str, Any]:
        """发布事务：CAS → 草稿冻结校验（适配器 validate）→ subject 注册 + schedules
        编译同事务 → revision 置 published（不可变）→ automation active。
        R51：幂等完成记录（idempotency.write_on）并入本事务（同 commit）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            automation = _lock_automation_on(cursor, tenant_id, automation_id)
            if automation is None or automation.get("user_id") != user_id:
                raise NotFoundError("自动化任务不存在")
            if automation["status"] == AUTOMATION_STATUS_ARCHIVED:
                raise ConflictError("已归档任务不可发布")
            if automation["status"] == AUTOMATION_STATUS_PAUSED:
                # P2-6 裁决：paused 下 publish 拒绝（产品稳定性优先，提示先 resume；
                # 草稿编辑不受影响——update_draft 在 paused 仍可新建草稿）
                raise ConflictError("任务已暂停，请先 resume 后再发布")
            if automation["version"] != payload.expected_version:
                raise ConflictError(
                    f"版本冲突：期望 {payload.expected_version}，实际 {automation['version']}"
                )
            revision_id = str(payload.revision_id or automation.get("draft_revision_id") or "")
            if not revision_id:
                # P2-A2（R46 409 语义）：草稿已被发布（无 draft 指针）是状态冲突而非
                # 不存在——重复发布应 409 提示，而非误导性的 404
                raise ConflictError("无草稿 revision 可发布（草稿已发布或不存在，请先编辑新草稿）")
            revision = _get_revision_on(cursor, tenant_id, revision_id) if revision_id else None
            if revision is None or str(revision.get("automation_id")) != str(automation_id):
                raise NotFoundError("revision 不存在")
            if revision["status"] != REVISION_STATUS_DRAFT:
                raise ConflictError("仅草稿 revision 可发布（发布后不可变）")
            blocks = _list_revision_blocks_on(cursor, tenant_id, revision_id)

            adapter = _adapter()
            ctx = _adapter_ctx(tenant_id, user_id, automation_id, revision_id)
            validation = adapter.validate_revision(
                ctx,
                {
                    "trigger": revision.get("trigger_json") or {},
                    "blocks": blocks,
                    "group_binding_id": revision.get("group_binding_id"),
                },
            )
            if not validation.ok:
                raise WeixinValidationError(f"发布校验失败: {validation.reason}")
            # 冻结指纹复核（存储行与重算不一致即拒绝，防草稿窗口期外被改）
            mismatch = content.verify_stored_blocks_list(blocks)
            if mismatch:
                raise ConflictError(f"内容块冻结 hash 不一致: {mismatch}")
            if revision.get("content_hash") != content.content_hash_of(blocks):
                raise ConflictError("revision 内容指纹不一致，拒绝发布")

            # ---- 底座 subject 注册 + schedules（与业务行同事务，R12 锁序）----
            task_ref = str(automation_id)
            task_row = da_subjects.lock_task_subject(
                cursor, tenant_id, SCENARIO_KEY, task_ref, skip_locked=False
            )
            prev_revision_ref = task_row["active_revision_ref"] if task_row else None
            epoch_row = da_subjects._upsert_task_subject(
                cursor, tenant_id, SCENARIO_KEY, task_ref, user_id,
                active_revision_ref=revision_id, status=TASK_STATUS_ACTIVE,
            )
            epoch = epoch_row["authorization_epoch"]
            da_subjects._upsert_revision_subject(
                cursor, tenant_id, SCENARIO_KEY, task_ref, revision_id, user_id
            )
            if prev_revision_ref and prev_revision_ref != revision_id:
                cursor.execute(
                    "UPDATE desktop_automation_subjects SET status = 'superseded', updated_at = NOW() "
                    "WHERE tenant_id = %s AND scenario_key = %s AND kind = 'revision' AND ref = %s",
                    (tenant_id, SCENARIO_KEY, prev_revision_ref),
                )
                cursor.execute(
                    "UPDATE bs_weixin_marketing_revisions SET status = %s, updated_at = NOW() "
                    "WHERE tenant_id = %s AND id = %s",
                    (REVISION_STATUS_SUPERSEDED, tenant_id, prev_revision_ref),
                )
            cursor.execute(
                """
                UPDATE desktop_automation_schedules
                SET status = 'paused', updated_at = NOW()
                WHERE tenant_id = %s AND scenario_key = %s AND task_ref = %s AND revision_ref <> %s
                  AND status = 'active'
                """,
                (tenant_id, SCENARIO_KEY, task_ref, revision_id),
            )
            for spec in validation.schedule_specs:
                da_subjects._insert_schedule(
                    cursor, tenant_id, SCENARIO_KEY, task_ref, revision_id, user_id, spec
                )
            da_audit.insert_audit(
                cursor, tenant_id, "revision_published", "task_subject", task_ref,
                user_id=user_id, scenario_key=SCENARIO_KEY,
                detail={
                    "revision_ref": revision_id,
                    "prev_revision_ref": prev_revision_ref,
                    "authorization_epoch": epoch,
                    "schedule_count": len(validation.schedule_specs),
                },
            )

            # ---- 业务行终态推进 ----
            cursor.execute(
                """
                UPDATE bs_weixin_marketing_revisions
                SET status = 'published', published_at = NOW(), authorized_by = %s,
                    authorization_source = %s, updated_at = NOW()
                WHERE tenant_id = %s AND id = %s
                """,
                (user_id, payload.authorization_source, tenant_id, revision_id),
            )
            new_version = automation["version"] + 1
            cursor.execute(
                """
                UPDATE bs_weixin_marketing_automations
                SET status = 'active', active_revision_id = %s, draft_revision_id = NULL,
                    version = %s, updated_at = NOW()
                WHERE tenant_id = %s AND id = %s
                """,
                (revision_id, new_version, tenant_id, automation_id),
            )
            _insert_weixin_audit_on(
                cursor, tenant_id, AUDIT_AUTOMATION_PUBLISHED, user_id=user_id,
                automation_id=automation_id,
                from_version=automation["version"], to_version=new_version,
                details={
                    "revision_id": revision_id,
                    "authorization_epoch": epoch,
                    "authorization_source": payload.authorization_source,
                },
            )
            publish_result = {
                "automation_id": automation_id,
                "revision_id": revision_id,
                "revision_no": revision["revision_no"],
                "authorization_epoch": epoch,
                "version": new_version,
            }
            if idempotency is not None:
                idempotency.write_on(cursor, status_code=200, data=publish_result)
            conn.commit()
        logger.info(
            f"后端日志：weixin_marketing 发布 tenant={tenant_id} automation={automation_id} "
            f"revision={revision_id} epoch={epoch}"
        )
        return publish_result

    # ---------- pause / resume / archive（CAS + epoch + 撤销未开始工作）----------

    def _transition(
        self,
        tenant_id: str,
        automation_id: str,
        user_id: str,
        payload: VersionedActionInput,
        *,
        target_status: str,
        audit_action: str,
        cancel_open_runs: bool,
        allowed_from: set,
    ) -> Dict[str, Any]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            automation = _lock_automation_on(cursor, tenant_id, automation_id)
            if automation is None or automation.get("user_id") != user_id:
                raise NotFoundError("自动化任务不存在")
            if automation["version"] != payload.expected_version:
                raise ConflictError(
                    f"版本冲突：期望 {payload.expected_version}，实际 {automation['version']}"
                )
            if automation["status"] not in allowed_from:
                raise ConflictError(
                    f"状态不允许迁移: {automation['status']} → {target_status}"
                )
            task_ref = str(automation_id)
            epoch: Optional[int] = None
            if target_status == AUTOMATION_STATUS_ACTIVE:
                epoch = self._resume_subject_on(cursor, tenant_id, task_ref)
            else:
                epoch = self._pause_subject_on(cursor, tenant_id, task_ref)
            cancelled_runs = 0
            if cancel_open_runs:
                cancelled_runs = self._cancel_open_runs_on(
                    cursor, tenant_id, task_ref,
                    reason=f"automation_{target_status}",
                )
            new_version = automation["version"] + 1
            cursor.execute(
                """
                UPDATE bs_weixin_marketing_automations
                SET status = %s, version = %s, pause_reason = %s, updated_at = NOW()
                WHERE tenant_id = %s AND id = %s
                """,
                (
                    target_status, new_version,
                    payload.reason if target_status == AUTOMATION_STATUS_PAUSED else None,
                    tenant_id, automation_id,
                ),
            )
            _insert_weixin_audit_on(
                cursor, tenant_id, audit_action, user_id=user_id,
                automation_id=automation_id,
                from_version=automation["version"], to_version=new_version,
                details={
                    "authorization_epoch": epoch,
                    "cancelled_runs": cancelled_runs,
                    "reason": payload.reason,
                },
            )
            conn.commit()
        logger.info(
            f"后端日志：weixin_marketing 状态迁移 tenant={tenant_id} automation={automation_id} "
            f"{automation['status']} → {target_status} epoch={epoch} cancelled_runs={cancelled_runs}"
        )
        return {
            "automation_id": automation_id,
            "status": target_status,
            "version": new_version,
            "authorization_epoch": epoch,
            "cancelled_runs": cancelled_runs,
        }

    @staticmethod
    def _pause_subject_on(cursor, tenant_id: str, task_ref: str) -> Optional[int]:
        """task subject 暂停 + epoch+1（授权撤销）+ schedules 暂停（与 subjects.pause_task
        同语义；此处须并入业务事务，故游标级复用其锁定入口）"""
        task_row = da_subjects.lock_task_subject(
            cursor, tenant_id, SCENARIO_KEY, task_ref, skip_locked=False
        )
        if task_row is None:
            return None
        cursor.execute(
            """
            UPDATE desktop_automation_subjects
            SET status = 'paused', authorization_epoch = authorization_epoch + 1, updated_at = NOW()
            WHERE tenant_id = %s AND scenario_key = %s AND kind = 'task' AND ref = %s
            RETURNING authorization_epoch
            """,
            (tenant_id, SCENARIO_KEY, task_ref),
        )
        row = cursor.fetchone()
        cursor.execute(
            """
            UPDATE desktop_automation_schedules
            SET status = 'paused', updated_at = NOW()
            WHERE tenant_id = %s AND scenario_key = %s AND task_ref = %s AND status = 'active'
            """,
            (tenant_id, SCENARIO_KEY, task_ref),
        )
        da_audit.insert_audit(
            cursor, tenant_id, "task_paused", "task_subject", task_ref,
            user_id=task_row.get("owner_id"), scenario_key=SCENARIO_KEY,
            detail={"authorization_epoch": row["authorization_epoch"] if row else None},
        )
        return row["authorization_epoch"] if row else None

    @staticmethod
    def _resume_subject_on(cursor, tenant_id: str, task_ref: str) -> Optional[int]:
        task_row = da_subjects.lock_task_subject(
            cursor, tenant_id, SCENARIO_KEY, task_ref, skip_locked=False
        )
        if task_row is None:
            return None
        revision_ref = task_row.get("active_revision_ref")
        cursor.execute(
            """
            UPDATE desktop_automation_subjects
            SET status = 'active', authorization_epoch = authorization_epoch + 1, updated_at = NOW()
            WHERE tenant_id = %s AND scenario_key = %s AND kind = 'task' AND ref = %s
            RETURNING authorization_epoch
            """,
            (tenant_id, SCENARIO_KEY, task_ref),
        )
        row = cursor.fetchone()
        cursor.execute(
            """
            UPDATE desktop_automation_schedules
            SET status = 'active', updated_at = NOW()
            WHERE tenant_id = %s AND scenario_key = %s AND task_ref = %s
              AND revision_ref = %s AND status = 'paused'
            """,
            (tenant_id, SCENARIO_KEY, task_ref, revision_ref),
        )
        da_audit.insert_audit(
            cursor, tenant_id, "task_resumed", "task_subject", task_ref,
            user_id=task_row.get("owner_id"), scenario_key=SCENARIO_KEY,
            detail={"authorization_epoch": row["authorization_epoch"] if row else None},
        )
        return row["authorization_epoch"] if row else None

    @staticmethod
    def _terminal_state_for_cancel(rows: List[Dict[str, Any]]) -> str:
        """取消语义聚合（§5.4）：全部未提交 → cancelled；已有已提交条目 → partial
        （在途条目照实保留，迟到结果仍可落账，不撤回不重发）。"""
        any_started = any(da_runs.delivery_is_started(d) for d in rows)
        if any_started:
            return da_runs.compute_run_terminal_state(rows, deadline_exceeded=True) or "partial"
        return da_runs.compute_run_terminal_state(rows, cancelled=True) or "cancelled"

    @staticmethod
    def _cancel_open_runs_on(cursor, tenant_id: str, task_ref: str, *, reason: str) -> int:
        """撤销未开始工作：pending/running run → 剩余条目 skipped，按 §5.4 落终态。

        已提交（may_have_started 及之后）条目照实保留——不撤回、不重发（R48）；
        在途 invocation 逐条请求取消（R49，与 cancel_run 同语义：queued→cancelled、
        claimed/running→cancel_requested，Runtime 经 cancel 标志停止未开始输入）。

        CR-P1-1：候选为非锁定读；循环内对每个 run 先行租户域 FOR UPDATE 并复验仍
        pending/running（终态跳过）再动 deliveries/invocations——锁序与 cancel_run
        （run→deliveries→invocations）严格同序，pause/archive × cancel_run 并发无
        AB-BA 死锁窗口。返回实际撤销数（候选可能已被并发路径收敛）。
        """
        cursor.execute(
            """
            SELECT id, user_id FROM desktop_automation_runs
            WHERE tenant_id = %s AND scenario_key = %s AND task_ref = %s
              AND state IN ('pending', 'running')
            ORDER BY created_at
            """,
            (tenant_id, SCENARIO_KEY, task_ref),
        )
        candidates = [dict(r) for r in cursor.fetchall()]
        cancelled = 0
        for run in candidates:
            run_id = str(run["id"])
            # 锁序第一环：run 行 FOR UPDATE + 复验未终态（候选可能已被 cancel_run/
            # 回调/回收并发收敛——终态即跳过，不重复改判）
            cursor.execute(
                "SELECT id, user_id, state FROM desktop_automation_runs "
                "WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, run_id),
            )
            locked = cursor.fetchone()
            if locked is None or locked["state"] not in ("pending", "running"):
                continue
            da_deliveries.skip_remaining_deliveries(cursor, run_id, tenant_id, reason)
            cancel_requested = WeixinMarketingService._request_cancel_run_invocations_on(
                cursor, tenant_id, run_id
            )
            rows = _list_run_deliveries_on(cursor, tenant_id, run_id)
            state = WeixinMarketingService._terminal_state_for_cancel(rows)
            da_runs.finish_run(cursor, run_id, tenant_id, state, {"reason": reason})
            da_audit.insert_audit(
                cursor, tenant_id, "run_cancelled_by_scenario", "run", run_id,
                user_id=locked.get("user_id"), scenario_key=SCENARIO_KEY,
                detail={
                    "reason": reason, "state": state,
                    "cancel_requested_invocations": cancel_requested,
                },
            )
            cancelled += 1
        return cancelled

    def pause(self, tenant_id: str, automation_id: str, user_id: str, payload: VersionedActionInput):
        return self._transition(
            tenant_id, automation_id, user_id, payload,
            target_status=AUTOMATION_STATUS_PAUSED,
            audit_action=AUDIT_AUTOMATION_PAUSED,
            cancel_open_runs=True,
            allowed_from=_PAUSE_FROM,
        )

    def resume(self, tenant_id: str, automation_id: str, user_id: str, payload: VersionedActionInput):
        return self._transition(
            tenant_id, automation_id, user_id, payload,
            target_status=AUTOMATION_STATUS_ACTIVE,
            audit_action=AUDIT_AUTOMATION_RESUMED,
            cancel_open_runs=False,
            allowed_from=_RESUME_FROM,
        )

    def archive(self, tenant_id: str, automation_id: str, user_id: str, payload: VersionedActionInput):
        return self._transition(
            tenant_id, automation_id, user_id, payload,
            target_status=AUTOMATION_STATUS_ARCHIVED,
            audit_action=AUDIT_AUTOMATION_ARCHIVED,
            cancel_open_runs=True,
            allowed_from=_ARCHIVE_FROM,
        )

    # ---------- 手动 run ----------

    def manual_run(
        self,
        tenant_id: str,
        automation_id: str,
        user_id: str,
        *,
        request_id: str,
        now: Optional[datetime] = None,
        idempotency: Any = None,
    ) -> Dict[str, Any]:
        """手动触发（202 语义）：manual 键幂等，不改时间 schedule。

        CR-P1-2：入口租户白名单预检（allowlist 非空且租户不在列 → 409
        TENANT_NOT_ALLOWED，与 dispatch 域时间槽接纳门控对齐；空列表不限制）。
        R51：接纳（task subject 锁 → occurrence/run/outbox）+ 业务审计 + 幂等完成
        记录同一事务提交（游标级复用底座 admit_occurrence/lock_task_subject，
        语义与 occurrences.accept_manual_trigger 一致）——「业务已提交而响应未保存」
        不可达；同 key 重试经 manual 触发键去重复用原 occurrence/run。
        """
        now = _aware(now or utcnow())
        if not tenant_allowed(get_weixin_marketing_config(), tenant_id):
            raise TenantNotAllowedError(
                "租户未在 weixin_marketing.tenant_allowlist 白名单内，拒绝手动运行"
            )
        trigger_key = manual_trigger_key(request_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            automation = _get_automation_on(cursor, tenant_id, automation_id)
            if automation is None or automation.get("user_id") != user_id:
                raise NotFoundError("自动化任务不存在")
            if automation["status"] != AUTOMATION_STATUS_ACTIVE:
                raise ConflictError(f"任务状态不允许手动运行: {automation['status']}")
            revision_id = str(automation.get("active_revision_id") or "")
            revision = _get_revision_on(cursor, tenant_id, revision_id) if revision_id else None
            if revision is None or revision["status"] != REVISION_STATUS_PUBLISHED:
                raise ConflictError("无已发布 revision，拒绝手动运行")
            # R12 锁序：task subject（阻塞 FOR UPDATE）→ occurrence/run
            task_row = da_subjects.lock_task_subject(
                cursor, tenant_id, SCENARIO_KEY, str(automation_id), skip_locked=False
            )
            if task_row is None or task_row["status"] != TASK_STATUS_ACTIVE:
                raise ConflictError("手动触发被拒绝: task_not_active")
            occurrence_id, created = da_occurrences.admit_occurrence(
                cursor,
                tenant_id=tenant_id,
                scenario_key=SCENARIO_KEY,
                task_ref=str(automation_id),
                revision_ref=task_row["active_revision_ref"],
                user_id=user_id,
                trigger_kind=TRIGGER_KIND_MANUAL,
                trigger_key=trigger_key,
                due_at=now,
                expires_at=None,
                scheduled_for=now,
                authorization_epoch=task_row["authorization_epoch"],
            )
            if not created:
                existing = da_occurrences.get_occurrence_by_trigger_key_on(
                    cursor, tenant_id, SCENARIO_KEY, str(automation_id), trigger_key
                )
                occurrence_id = str(existing["id"]) if existing else None
            run_id = None
            if occurrence_id:
                cursor.execute(
                    "SELECT id FROM desktop_automation_runs "
                    "WHERE tenant_id = %s AND occurrence_id = %s",
                    (tenant_id, occurrence_id),
                )
                row = cursor.fetchone()
                run_id = str(row["id"]) if row else None
            _insert_weixin_audit_on(
                cursor, tenant_id, AUDIT_MANUAL_RUN_REQUESTED, user_id=user_id,
                automation_id=automation_id, run_id=run_id,
                details={"request_id": request_id, "created": created,
                         "occurrence_id": occurrence_id},
            )
            result = {
                "occurrence_id": occurrence_id,
                "run_id": run_id,
                "created": created,
            }
            if idempotency is not None:
                idempotency.write_on(cursor, status_code=202, data=result)
            conn.commit()
        return result

    # ---------- runs 查询 / 取消 ----------

    def list_runs(
        self,
        tenant_id: str,
        user_id: str,
        *,
        automation_id: Optional[str] = None,
        state: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        filters = ["tenant_id = %s", "scenario_key = %s", "user_id = %s"]
        params: List[Any] = [tenant_id, SCENARIO_KEY, user_id]
        if automation_id:
            filters.append("task_ref = %s")
            params.append(str(automation_id))
        if state:
            filters.append("state = %s")
            params.append(state)
        where = " AND ".join(filters)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COUNT(*) AS c FROM desktop_automation_runs WHERE {where}",
                tuple(params),
            )
            total = int(cursor.fetchone()["c"])
            cursor.execute(
                f"""
                SELECT id, occurrence_id, scenario_key, task_ref, revision_ref, user_id, state,
                       device_id, due_at, expires_at, result_json, created_at, finished_at
                FROM desktop_automation_runs WHERE {where}
                ORDER BY created_at DESC LIMIT %s OFFSET %s
                """,
                (*params, page_size, (page - 1) * page_size),
            )
            items = [dict(r) for r in cursor.fetchall()]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def get_run_detail(self, tenant_id: str, run_id: str, user_id: str) -> Dict[str, Any]:
        run = da_runs.get_run(run_id, tenant_id)
        if run is None or run.get("scenario_key") != SCENARIO_KEY or run.get("user_id") != user_id:
            raise NotFoundError("运行记录不存在")
        deliveries = da_deliveries.list_run_deliveries(run_id, tenant_id)
        return {
            "run": {k: v for k, v in run.items()},
            "deliveries": [
                {k: v for k, v in d.items() if k in (
                    "id", "position", "operation", "target_ref", "payload_ref", "payload_hash",
                    "state", "effect", "phase", "created_at", "updated_at", "finished_at",
                )}
                for d in deliveries
            ],
        }

    @staticmethod
    def _request_cancel_run_invocations_on(cursor, tenant_id: str, run_id: str) -> int:
        """run 名下在途 delivery 的非终态 invocation 逐条请求取消（R49，游标级）。

        语义同 local_tools.repository.request_cancel（不嵌套取池连接，并入取消事务）：
        queued→cancelled（终态，effect=none，设备永远领不到——claim_next 只取 queued）；
        claimed/running→cancel_requested（Runtime 经 progress 的 cancel 标志停止）。
        返回请求取消的 invocation 数。
        """
        cursor.execute(
            """
            SELECT DISTINCT i.id
            FROM local_tool_invocations i
            JOIN desktop_automation_attempts a
              ON a.invocation_id = i.id AND a.tenant_id = i.tenant_id
            JOIN desktop_automation_deliveries d
              ON d.id = a.delivery_id AND d.tenant_id = a.tenant_id
            WHERE i.tenant_id = %s AND d.run_id = %s
              AND i.state IN ('queued', 'claimed', 'running')
            ORDER BY i.id
            """,
            (tenant_id, run_id),
        )
        inv_ids = [str(r["id"]) for r in cursor.fetchall()]
        for inv_id in inv_ids:
            cursor.execute(
                """
                UPDATE local_tool_invocations
                SET state = 'cancelled', effect = 'none', finished_at = NOW()
                WHERE id = %s AND tenant_id = %s AND state = 'queued'
                """,
                (inv_id, tenant_id),
            )
            cursor.execute(
                """
                UPDATE local_tool_invocations
                SET state = 'cancel_requested'
                WHERE id = %s AND tenant_id = %s AND state IN ('claimed', 'running')
                """,
                (inv_id, tenant_id),
            )
        return len(inv_ids)

    def cancel_run(self, tenant_id: str, run_id: str, user_id: str) -> Dict[str, Any]:
        """请求停止尚未提交条目（202 语义，R49 单事务，锁序 run→deliveries→invocations）：
        未开始 delivery 置 skipped（不再执行）；在途 invocation 逐条请求取消
        （queued→cancelled 终态、claimed/running→cancel_requested，Runtime 经 cancel
        标志停）；已提交（may_have_started 及之后）条目照实回收（§5.4 partial，
        迟到结果仍可落账，不撤回不重发）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM desktop_automation_runs WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant_id, run_id),
            )
            row = cursor.fetchone()
            run = dict(row) if row else None
            if run is None or run.get("scenario_key") != SCENARIO_KEY or run.get("user_id") != user_id:
                raise NotFoundError("运行记录不存在")
            if run["state"] in da_runs.RUN_TERMINAL_STATES:
                conn.commit()
                return {"run_id": run_id, "state": run["state"], "changed": False}
            da_deliveries.skip_remaining_deliveries(cursor, run_id, tenant_id, "run_cancelled")
            cancel_requested = self._request_cancel_run_invocations_on(cursor, tenant_id, run_id)
            rows = _list_run_deliveries_on(cursor, tenant_id, run_id)
            state = self._terminal_state_for_cancel(rows)
            da_runs.finish_run(cursor, run_id, tenant_id, state, {"reason": "run_cancelled"})
            _insert_weixin_audit_on(
                cursor, tenant_id, AUDIT_RUN_CANCEL_REQUESTED, user_id=user_id,
                automation_id=run.get("task_ref"), run_id=run_id,
                details={"state": state, "cancel_requested_invocations": cancel_requested},
            )
            conn.commit()
        logger.info(
            f"后端日志：weixin_marketing 取消 run tenant={tenant_id} run={run_id} "
            f"state={state} cancel_requested={cancel_requested}"
        )
        return {"run_id": run_id, "state": state, "changed": True}

    # ---------- deliveries resolve / retry（R45）----------

    def resolve_delivery(
        self, tenant_id: str, delivery_id: str, user_id: str, payload: DeliveryResolveInput
    ) -> Dict[str, Any]:
        """人工结论 + 证据说明（独立审计留痕；不覆盖机器 effect/phase——R10 分离）。

        R52：payload.decision='confirmed_not_sent'（必须附 note）记录「人工确认未发送
        并授权重试」决定——末次 attempt 非机器安全（非 none+prepared）的 delivery
        重试前必须已有该决定；机器安全路径（none+prepared）仅 confirm 即可。
        """
        delivery = da_deliveries.get_delivery(delivery_id, tenant_id)
        if delivery is None or delivery.get("scenario_key") != SCENARIO_KEY or delivery.get("user_id") != user_id:
            raise NotFoundError("投递记录不存在")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            _insert_weixin_audit_on(
                cursor, tenant_id, AUDIT_DELIVERY_RESOLVED, user_id=user_id,
                automation_id=delivery.get("task_ref"), run_id=delivery.get("run_id"),
                details={
                    "delivery_id": delivery_id,
                    "verdict": payload.verdict,
                    "decision": payload.decision,
                    "note": payload.note,
                    "machine_state": delivery.get("state"),
                    "machine_effect": delivery.get("effect"),
                    "machine_phase": delivery.get("phase"),
                },
            )
            conn.commit()
        return {
            "delivery_id": delivery_id,
            "verdict": payload.verdict,
            "decision": payload.decision,
            "machine_state": delivery.get("state"),
            "machine_effect": delivery.get("effect"),
        }

    def retry_delivery(
        self, tenant_id: str, delivery_id: str, user_id: str, *, confirm: bool = False,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """人工重试（R45/R52）：校验「无活跃许可（issued 且 deadline 未过）+ 原 invocation
        终态 + 可重试状态 + 显式人工决定 + 重试证据门槛」后，经 predecessor_attempt_id
        建新 attempt。

        R52 前置三分支：
        ①机器安全路径：末次 attempt effect=none 且 phase=prepared（含 NULL，从未开始）
          → 仅需 confirm；
        ②未知效果路径（其余 effect/phase 组合）：必须先经 deliveries resolve 记录
          decision='confirmed_not_sent'（人工确认未发送并授权重试，晚于末次 attempt），
          缺证据 → 409 RETRY_EVIDENCE_REQUIRED；
        ③既有前置保留（全部 invocation 终态 + 无活跃许可 + 可重试状态）。

        P2-A CR 修复（P1-2）：attempt 创建 + delivery 状态回置并入持 delivery 行锁的
        同一事务；attempt_no 在锁内 MAX+1 计算并同步生成 per-attempt dedupe 键；
        状态回置条件化（WHERE state IN failed/unknown/expired，防已 succeeded 被回置）；
        UNIQUE(tenant,delivery,attempt_no) 冲突捕获转 409（并发双击恰一成功）。
        enqueue 保持独立连接提交（invocations 表与 delivery 行锁无冲突；dedupe 键
        确定性，冲突回滚后重试复用同键不放大孤儿）。
        """
        now = _aware(now or utcnow())
        if not confirm:
            raise WeixinValidationError("重试需显式人工决定（confirm=true）")
        # ---- 锁外只读预载（run/适配器/目标解析；无行锁持有）----
        delivery_pre = da_deliveries.get_delivery(delivery_id, tenant_id)
        if delivery_pre is None or delivery_pre.get("scenario_key") != SCENARIO_KEY \
                or delivery_pre.get("user_id") != user_id:
            raise NotFoundError("投递记录不存在")
        run = da_runs.get_run(str(delivery_pre["run_id"]), tenant_id)
        if run is None:
            raise NotFoundError("run 不存在")
        adapter = _adapter()
        ctx = _adapter_ctx(tenant_id, user_id, run["task_ref"], run["revision_ref"])
        resolution = adapter.resolve_target(ctx, str(delivery_pre.get("target_ref") or ""))
        if not resolution.ok:
            raise ConflictError(f"目标不可解析: {resolution.reason}")
        request_id = str(uuid.uuid4())
        deadline_at = now + timedelta(seconds=DEFAULT_OPERATION_DEADLINE_SECONDS)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            delivery = da_deliveries.lock_delivery(cursor, delivery_id, tenant_id)
            if delivery is None or delivery.get("scenario_key") != SCENARIO_KEY \
                    or delivery.get("user_id") != user_id:
                conn.rollback()
                raise NotFoundError("投递记录不存在")
            if delivery["state"] not in ("failed", "unknown", "expired"):
                conn.rollback()
                raise ConflictError(f"投递状态不可重试: {delivery['state']}")
            # 活跃许可判重（与底座 permits 新语义一致：仅 issued 且 deadline 未过阻断）
            cursor.execute(
                """
                SELECT 1 FROM local_tool_operation_permits
                WHERE tenant_id = %s AND delivery_id = %s
                  AND state = 'issued' AND deadline >= NOW()
                LIMIT 1
                """,
                (tenant_id, delivery_id),
            )
            if cursor.fetchone() is not None:
                conn.rollback()
                raise ConflictError("存在未消费的执行许可，拒绝重试（PERMIT_ISSUED）")
            cursor.execute(
                """
                SELECT id, invocation_id, attempt_no, effect, phase, safe_to_retry,
                       created_at, finished_at
                FROM desktop_automation_attempts
                WHERE tenant_id = %s AND delivery_id = %s
                ORDER BY attempt_no DESC LIMIT 1
                """,
                (tenant_id, delivery_id),
            )
            latest = cursor.fetchone()
            if latest is None:
                conn.rollback()
                raise ConflictError("无历史 attempt，不可重试")
            latest = dict(latest)
            # 原 invocation 终态校验（同事务游标，不嵌套取池连接——P2-4 顺手项）
            cursor.execute(
                "SELECT state FROM local_tool_invocations WHERE tenant_id = %s AND id = %s",
                (tenant_id, str(latest["invocation_id"])),
            )
            inv_row = cursor.fetchone()
            from src.local_tools import repository as lt_repository

            if inv_row is None or inv_row["state"] not in lt_repository.TERMINAL_STATES:
                conn.rollback()
                raise ConflictError("原 invocation 未终态，拒绝重试")

            # ---- R52 重试证据门槛（复审三轮收紧：机器安全必须显式 safe_to_retry=true；
            # 未知效果须人工决定 + 受信停止确认双证据）----
            machine_safe = (
                latest.get("effect") == EFFECT_NONE
                and (latest.get("phase") is None or latest.get("phase") == PHASE_PREPARED)
                and latest.get("safe_to_retry") is True
            )
            if not machine_safe:
                # P2-2：证据时间边界收紧——末次 attempt 的 finished_at（NULL 回退
                # created_at）且严格大于：attempt 在途期间的 resolve 不算证据，
                # 人工确认必须晚于未知效果落定
                evidence_floor = latest.get("finished_at") or latest["created_at"]
                cursor.execute(
                    """
                    SELECT 1 FROM bs_weixin_marketing_audit_events
                    WHERE tenant_id = %s AND action = 'delivery_resolved'
                      AND details_redacted->>'delivery_id' = %s
                      AND details_redacted->>'decision' = 'confirmed_not_sent'
                      AND created_at > %s
                    LIMIT 1
                    """,
                    (tenant_id, delivery_id, evidence_floor),
                )
                has_human_decision = cursor.fetchone() is not None
                # 受信停止确认：绑定原 attempt/invocation 的设备通道迟到回执
                # （operation_result_late 仅能经 claim 鉴权写入，detail 携带完整回执
                # 字段）。effect=none + phase∈(NULL,prepared) + safe_to_retry=true 的
                # 迟到更正 = 旧 Runtime 以设备身份证明「该操作从未提交且已放弃」。
                # 当前 Runtime 无此更正流程 → 该门在 P2 实际关闭（fail-closed），
                # 待 P0/P3 交付运行时停止确认后自动放行。
                cursor.execute(
                    """
                    SELECT 1 FROM desktop_automation_audit_events
                    WHERE tenant_id = %s
                      AND kind = 'operation_result_late'
                      AND aggregate_type = 'attempt'
                      AND aggregate_ref = %s
                      AND detail->>'invocation_id' = %s
                      AND detail->>'effect' = 'none'
                      AND (detail->>'phase' IS NULL OR detail->>'phase' = 'prepared')
                      AND detail->>'safe_to_retry' = 'true'
                      AND created_at > %s
                    LIMIT 1
                    """,
                    (
                        tenant_id, str(latest["id"]), str(latest["invocation_id"]),
                        evidence_floor,
                    ),
                )
                has_stop_confirmation = cursor.fetchone() is not None
                if not (has_human_decision and has_stop_confirmation):
                    conn.rollback()
                    missing = []
                    if not has_human_decision:
                        missing.append("resolve 记录 decision=confirmed_not_sent（人工确认未发送）")
                    if not has_stop_confirmation:
                        missing.append(
                            "绑定原 invocation 的受信停止确认（设备通道迟到回执 "
                            "effect=none/phase=prepared/safe_to_retry=true；当前 Runtime "
                            "无此流程，未知效果重试在真机停止确认能力交付前关闭）"
                        )
                    raise RetryEvidenceRequiredError(
                        "缺重试证据：未知效果（末次 attempt 非机器证明的从未开始）须同时具备——"
                        + "；".join(missing)
                    )

            # ---- 锁内 attempt_no（MAX+1）→ 同步生成 per-attempt dedupe 键 ----
            next_attempt_no = int(latest["attempt_no"]) + 1
            from src.local_tools.service import LocalInvocationService

            # P2-5 死 invocation 防绑：enqueue 按 dedupe 键复用既有行时校验
            # state='queued'——同键残留非 queued（被取消/卡死，如上一轮重试崩溃
            # 留下的孤儿）则换新 request_id 重建新 invocation（键 per-attempt 递增），
            # 不把新 attempt 绑进死 invocation（否则 delivery 永久 dispatched）
            invocation = None
            key_attempt_no = next_attempt_no
            for _ in range(5):
                arguments = build_v2_operation_arguments(
                    operation=delivery["operation"],
                    provider_key=delivery["provider_key"],
                    target_ref=delivery["target_ref"],
                    target_handle=resolution.target_handle,
                    target_version=resolution.target_version or delivery.get("target_version"),
                    payload_ref=delivery.get("payload_ref"),
                    payload_hash=delivery.get("payload_hash"),
                    request_id=request_id,
                    delivery_id=str(delivery["id"]),
                    authorization_revision=run.get("revision_ref"),
                    authorization_epoch=run.get("authorization_epoch"),
                    resource_key=derive_resource_key(str(run.get("device_id") or "")),
                    deadline_at=deadline_at,
                )
                candidate = LocalInvocationService().enqueue(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    device_id=str(run.get("device_id") or ""),
                    tool_name=delivery["operation"],
                    arguments=arguments,
                    provider_key=delivery["provider_key"],
                    business_kind=BUSINESS_KIND_DESKTOP_AUTOMATION,
                    business_ref={
                        "delivery_id": str(delivery["id"]),
                        "run_id": str(run["id"]),
                        "occurrence_id": str(run.get("occurrence_id") or ""),
                        "scenario_key": SCENARIO_KEY,
                        "task_ref": run["task_ref"],
                        "revision_ref": run["revision_ref"],
                    },
                    # R45：per-attempt dedupe——底座 executor 首派键已是 per-attempt
                    # 形态 delivery:{id}:a:{attempt_no}，重试链续接同一键空间，
                    # 以 attempt_no 区分（CR 复核后更正：非两套不相交键空间）；
                    # 键被死 invocation 占据时递增重建（P2-5）
                    dedupe_key=f"delivery:{delivery['id']}:a:{key_attempt_no}",
                    deadline_at=deadline_at,
                    authorization_epoch=run.get("authorization_epoch"),
                )
                if candidate.get("state") == "queued":
                    invocation = candidate
                    break
                request_id = str(uuid.uuid4())
                key_attempt_no += 1
            if invocation is None:
                conn.rollback()
                raise ConflictError(
                    "重试无法获得可执行 invocation（同 delivery 连续死键残留），请稍后重试"
                )
            effective_request_id = (invocation.get("arguments_json") or {}).get("request_id") or request_id

            # ---- attempt 创建（UNIQUE 冲突 = 并发重试，恰一胜出）----
            from src.desktop_automation import attempts as da_attempts

            try:
                attempt_id = da_attempts.create_attempt(
                    cursor, tenant_id, delivery, str(invocation["id"]), effective_request_id,
                    predecessor_attempt_id=str(latest["id"]),
                )
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                raise ConflictError("并发重试冲突：该投递已有新的 attempt（恰好一次生效）")
            # 条件回置：仅 failed/unknown/expired 可回置为在途（防已 succeeded 被回置）；
            # 底座 mark_dispatched 只迁移 pending，重试链来自终态须显式回置——
            # effect/phase 由新 operation-result 覆盖，历史判定保留在 attempt 行
            cursor.execute(
                """
                UPDATE desktop_automation_deliveries
                SET state = 'dispatched', updated_at = NOW()
                WHERE id = %s AND tenant_id = %s
                  AND state IN ('failed', 'unknown', 'expired')
                """,
                (delivery_id, tenant_id),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                raise ConflictError("投递状态已变化，拒绝重试")
            _insert_weixin_audit_on(
                cursor, tenant_id, AUDIT_DELIVERY_RETRIED, user_id=user_id,
                automation_id=delivery.get("task_ref"), run_id=delivery.get("run_id"),
                details={
                    "delivery_id": delivery_id,
                    "attempt_id": attempt_id,
                    "predecessor_attempt_id": str(latest["id"]),
                    "attempt_no": next_attempt_no,
                    "invocation_id": str(invocation["id"]),
                    # R52：本次重试走的分支（machine_safe=仅 confirm；否则引用了
                    # resolve 的 confirmed_not_sent 决定）
                    "machine_safe": machine_safe,
                },
            )
            conn.commit()
        logger.info(
            f"后端日志：weixin_marketing 人工重试 tenant={tenant_id} delivery={delivery_id} "
            f"attempt={attempt_id} predecessor={latest['id']}"
        )
        return {
            "delivery_id": delivery_id,
            "attempt_id": attempt_id,
            "attempt_no": next_attempt_no,
            "predecessor_attempt_id": str(latest["id"]),
            "invocation_id": str(invocation["id"]),
            "request_id": effective_request_id,
        }
