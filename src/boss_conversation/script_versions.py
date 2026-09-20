"""BOSS 话术版本服务（B2，设计 §5.3 冻结）：append-only 不可变版本表。

- bs_boss_reply_script_versions 仅本模块写入（append-only：无 UPDATE/DELETE 路径）；
- content_hash = 模板规范化字节 sha256（normalize_template_bytes 口径）；
- version_no 同 lineage 递增（首个版本 1，并发发布由 UNIQUE(tenant,lineage,version_no)
  兜底，冲突方重读重试）；
- slot_schema 经唯一入口 normalize_slot_schema() 规范化（P1 八审：槽位名/数量/
  BossSlotField 字段校验 + 补齐默认值 required=true、description=""，与发布契约
  BossScriptRef 的 Pydantic 默认一致）后以规范化 JSON（排序键紧凑形态）存储，
  发布校验对 DB 与 spec 使用同一函数精确比较——保证"省略可选字段的创建"与
  "Pydantic 补全默认值的 spec"是同一冻结形态；
- 发布校验（设计 §5.3 "发布冻结 {script_version_id, content_hash, frozen_template,
  slot_schema}"；line 155 规范化精确匹配条款）：spec 白名单逐条核对本租户已发布
  版本存在、content_hash 一致、frozen_template 规范化哈希与 content_hash 一致
  （spec 冻结副本纵深防御）、slot_schema 规范化后精确相等（P1-B 七审：防引用
  真实版本但篡改槽位约束，如必需改可选）；
- 模板占位符 ⊆ slot_schema 白名单在创建时即校验（P1 八审：未知占位符受控 400，
  不创建永远不能发布/使用的版本）；
- 不修改 bs_recruiting_operator_job_scripts 与既有 CRUD（从既有话术发布为版本是
  读取复制，不是写回）。
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

from src.session_tasks.constants import SessionTaskError

from .models import template_content_hash

_SLOT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# 与发布契约 BossScriptRef.slot_schema 的 Field(max_length=20) 一致（无下限，
# 无槽位话术 schema={} 合法）
_SLOT_SCHEMA_MAX = 20
# 版本号唯一冲突重试上限（并发同 lineage 发布；P2-2 八审：变异验证需可注入）
VERSION_NO_CONFLICT_RETRIES = 5


class ScriptVersionError(SessionTaskError):
    """话术版本操作非法（受控 message/status 由 SessionTaskError 承载）。"""


def normalize_slot_schema(slot_schema: Any) -> Dict[str, Any]:  # noqa: ANN001
    """slot_schema 唯一规范化入口（P1 八审，设计 line 155 条款）：校验槽位名
    （^[a-z][a-z0-9_]{0,63}$，与占位符语法一致）、数量（≤20，与 BossScriptRef
    max_length 一致；无下限，无槽位话术 {} 合法）、每个槽位经 BossSlotField 校验
    （extra=forbid 拒绝额外字段），输出**补齐默认值**的 plain dict
    （required 默认 true、description 默认 ""，与 Pydantic 默认一致）。

    create_version 存储与 verify_spec_scripts_published 比较共用——两侧同函数
    规范化，省略可选字段的创建与 Pydantic 补全默认值的 spec 是同一形态。

    顶层 None → {} 仅为 create_version 参数缺省的人体工学；publish 的 DB 侧在
    调用本函数**之前**显式拒绝非 dict（P1 九审：JSON null/数组/字符串不得经此
    分支静默当空 schema）。每个槽位定义仅接受 dict（九审非阻断 1）。"""
    if slot_schema is None:
        return {}
    if not isinstance(slot_schema, dict):
        raise ScriptVersionError("slot_schema 必须是对象", "VALIDATION_FAILED", 400)
    if len(slot_schema) > _SLOT_SCHEMA_MAX:
        raise ScriptVersionError(
            f"slot_schema 槽位数量超过 {_SLOT_SCHEMA_MAX} 上限", "VALIDATION_FAILED", 400
        )
    from pydantic import ValidationError

    from .models import BossSlotField

    normalized: Dict[str, Any] = {}
    for key, value in slot_schema.items():
        if not isinstance(key, str) or not _SLOT_KEY_RE.fullmatch(key):
            raise ScriptVersionError(
                f"槽位名必须匹配 ^[a-z][a-z0-9_]{{0,63}}$: {key}", "VALIDATION_FAILED", 400
            )
        # 非阻断 1（九审）：槽位定义仅接受 dict（None 不再被 `or {}` 静默当空对象
        # ——发布契约 BossSlotField 会拒绝 None，两侧必须同形态）
        if not isinstance(value, dict):
            raise ScriptVersionError(
                f"槽位 {key} 定义必须是对象", "VALIDATION_FAILED", 400
            )
        try:
            field = BossSlotField.model_validate(value)
        except ValidationError as exc:
            raise ScriptVersionError(
                f"槽位 {key} 定义非法: {exc.errors()[:1]}", "VALIDATION_FAILED", 400
            ) from exc
        normalized[key] = {"required": bool(field.required), "description": str(field.description or "")}
    return normalized


def _canonical_slot_schema(slot_schema: Any) -> str:  # noqa: ANN001
    """slot_schema 规范化形态（P1-B 七审）：**已规范化** dict 的排序键紧凑 JSON。
    调用方契约——create_version 传入 normalize_slot_schema 的产物（存储形态），
    publish 比较两侧均先过 normalize_slot_schema 再进本函数（P1 八审：同一函数
    校验+补默认值，省略可选字段的创建与 Pydantic 补全默认值的 spec 同形态）。"""
    return json.dumps(slot_schema or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def create_version(
    tenant_id: str,
    user_id: str,
    *,
    lineage_id: Optional[str] = None,
    template: str,
    slot_schema: Optional[Dict[str, Any]] = None,
    source_script_id: Optional[str] = None,
    source_job_id: Optional[str] = None,
    source_job_name: str,
) -> Dict[str, Any]:
    """新建话术版本（append-only）。lineage_id 缺省新建 lineage；version_no 同
    lineage 递增；模板非空且哈希自洽。返回版本行（dict）。

    非阻断 d（六审）：显式 lineage 必须已存在（此前 MAX() COALESCE 使不存在
    lineage 被静默建成 v1）；并发同 lineage 发布由 UNIQUE(tenant,lineage,version_no)
    兜底，冲突方在 SAVEPOINT 内重读 MAX 重试（有限次），超限受控 409。
    P1（八审）：slot_schema 经 normalize_slot_schema 规范化（校验+补默认值）后
    存储；模板占位符 ⊆ 规范化 schema 在创建时校验（未知占位符受控 400，不创建
    永远不能发布的版本）。"""
    template = str(template or "")
    if not template.strip():
        raise ScriptVersionError("话术模板不能为空", "VALIDATION_FAILED", 400)
    if len(template) > 2000:
        raise ScriptVersionError("话术模板超过 2000 字符上限", "VALIDATION_FAILED", 400)
    # P1（八审）：唯一入口规范化（校验槽位名/数量/BossSlotField 字段 + 补默认值），
    # 存储与后续 publish 比较同形态
    normalized_schema = normalize_slot_schema(slot_schema)
    # 模板占位符 ⊆ 规范化 schema：未知占位符在创建时受控拒绝（设计 §5.3/§5.4
    # 白名单语法；避免创建后发布必 409 的死版本）
    from .render import find_unknown_placeholders

    unknown = find_unknown_placeholders(template, normalized_schema)
    if unknown:
        raise ScriptVersionError(
            f"模板含未知占位符 {unknown[:3]}（不在 slot_schema 白名单内）",
            "VALIDATION_FAILED", 400,
        )
    from psycopg2 import IntegrityError

    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        if lineage_id:
            lineage_id = str(lineage_id)
            cursor.execute(
                """
                SELECT 1 FROM bs_boss_reply_script_versions
                WHERE tenant_id=%s AND lineage_id=%s LIMIT 1
                """,
                (tenant_id, lineage_id),
            )
            if cursor.fetchone() is None:
                raise ScriptVersionError("lineage 不存在", "NOT_FOUND", 404)
        else:
            lineage_id = str(uuid.uuid4())
        content_hash = template_content_hash(template)
        created: Optional[Dict[str, Any]] = None
        last_conflict: Optional[Exception] = None
        for _attempt in range(max(1, int(VERSION_NO_CONFLICT_RETRIES))):
            cursor.execute(
                """
                SELECT COALESCE(MAX(version_no), 0) AS cur FROM bs_boss_reply_script_versions
                WHERE tenant_id=%s AND lineage_id=%s
                """,
                (tenant_id, lineage_id),
            )
            version_no = int(cursor.fetchone()["cur"]) + 1
            cursor.execute("SAVEPOINT sv_create_version")
            try:
                cursor.execute(
                    """
                    INSERT INTO bs_boss_reply_script_versions
                        (tenant_id, user_id, lineage_id, version_no, content_hash, template,
                         slot_schema, source_script_id, source_job_id, source_job_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, lineage_id, version_no, content_hash, created_at
                    """,
                    (
                        tenant_id, user_id, lineage_id, version_no, content_hash, template,
                        _canonical_slot_schema(normalized_schema),
                        source_script_id, source_job_id, str(source_job_name or ""),
                    ),
                )
                created = dict(cursor.fetchone())
                cursor.execute("RELEASE SAVEPOINT sv_create_version")
                break
            except IntegrityError as exc:  # noqa: PERF203 并发发布同 lineage → 重读重试
                cursor.execute("ROLLBACK TO SAVEPOINT sv_create_version")
                last_conflict = exc
                continue
        if created is None:
            raise ScriptVersionError(
                "话术版本并发发布冲突（同 lineage 版本号竞争），请重试", "CONFLICT", 409
            ) from last_conflict
        conn.commit()
    # UUID 列统一字符串化（API/测试直接可 JSON 化）
    created["id"] = str(created["id"])
    created["lineage_id"] = str(created["lineage_id"])
    created["template"] = template
    return created


def publish_from_recruiting_script(
    tenant_id: str, user_id: str, *, job_id: str, script_id: str
) -> Dict[str, Any]:
    """从既有 recruiting 话术发布为 BOSS 版本（只读复制，不改 bs_recruiting_operator_job_scripts）。

    job 归属/话术归属校验在读取时完成（跨租户/跨 job 引用一律 404）。
    """
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT j.id AS job_id, j.job_name, s.id AS script_id, s.content, s.category
            FROM bs_recruiting_operator_job_scripts s
            JOIN bs_recruiting_operator_jobs j ON j.tenant_id = s.tenant_id AND j.id = s.job_id
            WHERE s.tenant_id=%s AND s.id=%s AND s.job_id=%s
            """,
            (tenant_id, str(script_id), str(job_id)),
        )
        row = cursor.fetchone()
    if row is None:
        raise ScriptVersionError("职位话术不存在或不属于当前租户", "NOT_FOUND", 404)
    return create_version(
        tenant_id, user_id,
        template=str(row["content"] or ""),
        source_script_id=str(row["script_id"]),
        source_job_id=str(row["job_id"]),
        source_job_name=str(row["job_name"] or ""),
    )


def get_version(tenant_id: str, version_id: str) -> Optional[Dict[str, Any]]:
    """按 id 取版本行（租户过滤；不存在返回 None）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tenant_id, user_id, lineage_id, version_no, content_hash, template,
                   slot_schema, source_script_id, source_job_id, source_job_name, created_at
            FROM bs_boss_reply_script_versions WHERE tenant_id=%s AND id=%s
            """,
            (tenant_id, str(version_id)),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_versions(tenant_id: str, lineage_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """版本列表（lineage 可选过滤；version_no 升序；不含模板全文）。"""
    from src.db.database import get_db_connection

    where = "tenant_id=%s"
    params: List[Any] = [tenant_id]
    if lineage_id:
        where += " AND lineage_id=%s"
        params.append(str(lineage_id))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT id, lineage_id, version_no, content_hash, source_job_name, created_at
            FROM bs_boss_reply_script_versions WHERE {where}
            ORDER BY created_at, version_no LIMIT %s
            """,
            (*params, max(1, min(int(limit), 500))),
        )
        return [dict(r) for r in cursor.fetchall()]


def verify_spec_scripts_published(tenant_id: str, spec: Dict[str, Any], conn=None) -> None:
    """发布校验（设计 §5.3，适配器授权复判/测试共用）：spec.scripts 白名单逐条——
    ① 本租户已发布版本存在；② content_hash 与版本行一致；③ frozen_template 规范化
    哈希 == content_hash（spec 冻结副本纵深防御）；④ slot_schema 规范化后与版本行
    **精确相等**（P1-B 七审：可引用真实版本但篡改槽位约束——如必需槽位 required
    true→false——使渲染按可选缺值空串发送、绕过必需证据约束，一律拒绝）。
    任一不满足抛 SessionTaskError 409。

    conn 提供时在调用方事务/游标上执行（读已提交可见性一致）；缺省自开连接。
    """
    scripts = (spec or {}).get("scripts") or []
    if not scripts:
        raise ScriptVersionError("spec 缺少话术白名单", "VALIDATION_FAILED", 400)

    def _run(cursor) -> None:  # noqa: ANN001
        for ref in scripts:
            version_id = str(ref.get("script_version_id") or "")
            content_hash = str(ref.get("content_hash") or "")
            cursor.execute(
                "SELECT id, content_hash, slot_schema FROM bs_boss_reply_script_versions "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, version_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ScriptVersionError(
                    f"话术版本不存在或未发布: {version_id}", "CONFLICT", 409
                )
            if str(row["content_hash"]) != content_hash:
                raise ScriptVersionError(
                    f"话术版本 content_hash 与已发布版本不一致: {version_id}", "CONFLICT", 409
                )
            frozen_template = str(ref.get("frozen_template") or "")
            if template_content_hash(frozen_template) != content_hash:
                # spec 冻结副本被篡改/损坏（设计 §5.3 纵深防御）
                raise ScriptVersionError(
                    f"spec 冻结模板与 content_hash 不一致: {version_id}", "CONFLICT", 409
                )
            # P1-B（七审）/P1（九审）：slot_schema 冻结副本精确校验——DB 与 spec
            # 双侧同一归一化入口。DB 侧**不预先降级**：JSON null/数组/字符串等非法
            # 存量数据 → 受控 409 CONFLICT（非面向用户输入的 400），不得静默当空
            # schema（否则损坏行 + spec {} 会错误通过发布）；dict 内字段非法的历史
            # 脏数据（归一化 400）同样转 409；仅兼容合法 dict 中省略 required/
            # description 的历史数据（normalize 补默认值）。
            db_raw = row["slot_schema"]
            if not isinstance(db_raw, dict):
                raise ScriptVersionError(
                    f"DB 话术版本 slot_schema 非法（JSON null/数组/标量）: {version_id}",
                    "CONFLICT", 409,
                )
            try:
                db_canonical = _canonical_slot_schema(normalize_slot_schema(db_raw))
            except ScriptVersionError as exc:
                raise ScriptVersionError(
                    f"DB 话术版本 slot_schema 非法: {version_id}", "CONFLICT", 409
                ) from exc
            if db_canonical != _canonical_slot_schema(normalize_slot_schema(ref.get("slot_schema"))):
                raise ScriptVersionError(
                    f"spec slot_schema 与已发布版本不一致: {version_id}", "CONFLICT", 409
                )

    if conn is not None:
        _run(conn.cursor())
        return
    from src.db.database import get_db_connection

    with get_db_connection() as conn2:
        _run(conn2.cursor())
