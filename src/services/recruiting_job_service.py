"""招聘操作智能体职位库服务层

镜像 src/services/recruiting_resume_service.py 的分层约定：
- 表 bs_recruiting_operator_jobs（职位）+ bs_recruiting_operator_job_scripts（职位沟通话术）
- tenant_id 显式传参，不依赖 saas context / HTTP Request
- 校验失败抛 JobServiceError（ValueError 子类，中文消息），由 API 层转 400
- DB 访问 get_db_connection + %s 参数化，所有查询带 tenant_id 过滤（租户隔离规范）

数据模型：
- 职位 = 职位名称 + 备注（tech stack / 团队说明等），tenant_id+job_name 唯一
- 话术 = 招聘 HR 在 BOSS 上与候选人聊天的常用模板，固定四分类（初次开场/了解摸底/
  追问细节/邀约推进），content 支持 {{占位符}}（复制后手动替换）
- 不做任何预置数据：空职位库是合法状态（付费租户自行创建职位与话术）

调用方：HTTP API（src/api/recruiting_operator.py，前端「职位库」业务页）
"""
from __future__ import annotations

import json
import uuid as uuid_module
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from loguru import logger

from src.db.database import get_db_connection

# 话术分类（固定四值，顺序即前端分组展示顺序）
SCRIPT_CATEGORIES = ("初次开场", "了解摸底", "追问细节", "邀约推进")

# 职位状态（active=正常可选/筛选 / paused=暂停存档；仅本库展示控制，不与 BOSS 页面同步）
JOB_STATUSES = ("active", "paused")

# match_threshold 默认值与取值范围（简历评分 0-100，>= 阈值才算 matched）
DEFAULT_MATCH_THRESHOLD = 70

# job_requirements 允许的键（结构校验用；档位值不做硬校验，BOSS 档位动态，
# Phase 3 由 boss_filter_options 校准兜底，见设计 §2.1）
_REQUIREMENTS_STR_KEYS = ("experience", "salary", "notes")
_REQUIREMENTS_LIST_KEYS = ("educations", "keywords")


class JobServiceError(ValueError):
    """职位库服务校验错误（中文消息，API 层转 400）"""


# ============== 建表（幂等） ==============

def init_recruiting_job_tables(conn) -> None:
    """幂等建职位库两表（bs_recruiting_operator_jobs / bs_recruiting_operator_job_scripts）。

    由 src/db/database.py 启动初始化与集成测试调用（conn 由调用方管理事务）。
    2026-08-17 简历-职位匹配 Phase 1：jobs 增加 status / match_threshold / job_requirements
    三列——新库 CREATE 直接带列，老库用 ALTER ADD COLUMN IF NOT EXISTS 幂等补齐
    （存量行自动取默认值 active/70/NULL）。
    """
    cursor = conn.cursor()

    # bs_recruiting_operator_jobs：职位（tenant_id+job_name 唯一）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bs_recruiting_operator_jobs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id TEXT NOT NULL,
            job_name TEXT NOT NULL,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            match_threshold INT NOT NULL DEFAULT 70,
            job_requirements JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, job_name)
        )
    """)
    # 老库幂等加列（新库 CREATE 已带列，此处 no-op）
    cursor.execute(
        "ALTER TABLE bs_recruiting_operator_jobs ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'"
    )
    cursor.execute(
        "ALTER TABLE bs_recruiting_operator_jobs ADD COLUMN IF NOT EXISTS match_threshold INT NOT NULL DEFAULT 70"
    )
    cursor.execute(
        "ALTER TABLE bs_recruiting_operator_jobs ADD COLUMN IF NOT EXISTS job_requirements JSONB"
    )
    # bs_recruiting_operator_job_scripts：职位沟通话术（删职位级联删话术）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bs_recruiting_operator_job_scripts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id TEXT NOT NULL,
            job_id UUID NOT NULL REFERENCES bs_recruiting_operator_jobs(id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            sort_order INT NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_bs_roj_tenant
        ON bs_recruiting_operator_jobs(tenant_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_bs_rojs_tenant
        ON bs_recruiting_operator_job_scripts(tenant_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_bs_rojs_job
        ON bs_recruiting_operator_job_scripts(job_id, sort_order)
    """)

    logger.info("recruiting_operator 职位库表已就绪 (bs_recruiting_operator_jobs / bs_recruiting_operator_job_scripts)")


def ensure_tables() -> None:
    """幂等建表（自开连接版，服务函数入口兜底调用）"""
    with get_db_connection() as conn:
        init_recruiting_job_tables(conn)
        conn.commit()


# ============== 内部辅助 ==============

def _to_uuid(value: Any, field: str) -> str:
    """把路径参数转为规范 UUID 字符串，非法抛 JobServiceError（避免 DB 层 DataError 500）"""
    try:
        return str(uuid_module.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise JobServiceError(f"{field} 格式非法: {value}")


def _parse_json_field(val: Any) -> Any:
    """把 JSONB 字段从 str 解析为 dict/list（PostgreSQL JSONB 一般已是 dict，兜底）"""
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return None
    return val


def _row_to_job(row) -> Dict[str, Any]:
    """职位行转 dict，UUID 转字符串、JSONB 兜底解析（前端友好）"""
    item = dict(row)
    if item.get("id") is not None:
        item["id"] = str(item["id"])
    item["job_requirements"] = _parse_json_field(item.get("job_requirements"))
    return item


def _validate_job_status(value: Optional[str]) -> Optional[str]:
    """职位状态校验（active/paused），非法抛 JobServiceError"""
    if value is None:
        return None
    if value not in JOB_STATUSES:
        raise JobServiceError(f"职位状态非法: {value} not in {JOB_STATUSES}")
    return value


def _validate_match_threshold(value: Optional[int]) -> Optional[int]:
    """匹配阈值校验（0-100 整数），非法抛 JobServiceError"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise JobServiceError(f"match_threshold 必须为整数，当前为 {type(value).__name__}")
    if not 0 <= value <= 100:
        raise JobServiceError(f"match_threshold 取值必须在 0-100，当前为 {value}")
    return value


def _validate_job_requirements(value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """job_requirements 结构校验（仅键白名单 + 类型；档位值不硬校验，见设计 §2.1）。

    允许键：experience(str) / educations(list[str]) / salary(str) / keywords(list[str]) /
    notes(str)，全部可缺省；类型不符或未知键抛 JobServiceError。
    返回清洗后的 dict（空字符串/null 项剔除，可为 {}），由调用方决定存 None 还是 {}。
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise JobServiceError("job_requirements 必须为对象")
    cleaned: Dict[str, Any] = {}
    for key, val in value.items():
        if key in _REQUIREMENTS_STR_KEYS:
            if val is None or (isinstance(val, str) and not val.strip()):
                continue
            if not isinstance(val, str):
                raise JobServiceError(f"job_requirements.{key} 必须为字符串，当前为 {type(val).__name__}")
            cleaned[key] = val.strip()
        elif key in _REQUIREMENTS_LIST_KEYS:
            if val is None or (isinstance(val, list) and not val):
                continue
            if not isinstance(val, list) or not all(
                isinstance(i, str) and i.strip() for i in val
            ):
                raise JobServiceError(f"job_requirements.{key} 必须为字符串数组，当前为 {val!r}")
            cleaned[key] = [i.strip() for i in val]
        else:
            raise JobServiceError(
                f"job_requirements 不支持的字段: {key}（允许：experience/educations/salary/keywords/notes）"
            )
    return cleaned


def _row_to_script(row) -> Dict[str, Any]:
    """话术行转 dict，UUID 转字符串（前端友好）"""
    item = dict(row)
    if item.get("id") is not None:
        item["id"] = str(item["id"])
    if item.get("job_id") is not None:
        item["job_id"] = str(item["job_id"])
    return item


def _validate_script_fields(
    *,
    category: Optional[str] = None,
    title: Optional[str] = None,
    content: Optional[str] = None,
) -> None:
    """话术字段校验（仅校验传了的字段），非法抛 JobServiceError"""
    if category is not None and category not in SCRIPT_CATEGORIES:
        raise JobServiceError(f"话术分类非法: {category} not in {SCRIPT_CATEGORIES}")
    if title is not None and not title.strip():
        raise JobServiceError("话术标题不能为空")
    if content is not None and not content.strip():
        raise JobServiceError("话术内容不能为空")


def _load_job_scripts(conn, tenant_id: str, job_id: str) -> List[Dict[str, Any]]:
    """加载职位的全部话术（按 sort_order, created_at 升序，租户隔离）"""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, tenant_id, job_id, category, title, content, sort_order, created_at, updated_at
        FROM bs_recruiting_operator_job_scripts
        WHERE job_id = %s AND tenant_id = %s
        ORDER BY sort_order ASC, created_at ASC
        """,
        (job_id, tenant_id),
    )
    return [_row_to_script(row) for row in cursor.fetchall()]


def _attach_script_views(job: Dict[str, Any], scripts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """给职位 dict 挂 scripts / script_count / script_groups（固定分类顺序，未知分类排尾部）"""
    job["scripts"] = scripts
    job["script_count"] = len(scripts)
    by_category: Dict[str, List[Dict[str, Any]]] = {}
    for script in scripts:
        by_category.setdefault(script["category"], []).append(script)
    ordered = [c for c in SCRIPT_CATEGORIES if c in by_category] + \
              [c for c in by_category if c not in SCRIPT_CATEGORIES]
    job["script_groups"] = [{"category": c, "scripts": by_category[c]} for c in ordered]
    return job


# ============== 职位 CRUD ==============

def count_job_resumes(tenant_id: str) -> Dict[str, Dict[str, int]]:
    """统计各职位已入库简历数与已匹配数（按 job_id 关联，租户隔离）。

    未关联职位（job_id 为 NULL）的简历不计入任何职位；matched = match_status='matched'。
    调用方：list_jobs（前端列表「简历 N · 匹配 M」统计）与 boss_jobs_list 工具
    （设计 §5.1 选择交互数据层）。2026-08-17 Phase 5：从 src/local_tools/proxy_tool.py
    下沉共享（原 _count_job_resumes，行为不变）。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT job_id,
                   COUNT(*) AS resume_count,
                   COUNT(*) FILTER (WHERE match_status = 'matched') AS matched_count
            FROM bs_recruiting_operator_resumes
            WHERE tenant_id = %s AND job_id IS NOT NULL
            GROUP BY job_id
            """,
            (tenant_id,),
        )
        return {
            str(row["job_id"]): {
                "resume_count": row["resume_count"],
                "matched_count": row["matched_count"],
            }
            for row in cursor.fetchall()
        }


def list_jobs(tenant_id: str) -> List[Dict[str, Any]]:
    """职位列表（按 created_at DESC，含话术数与已用分类、简历数与匹配数）；空职位库返回空列表"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tenant_id, job_name, notes, status, match_threshold, job_requirements,
                   created_at, updated_at
            FROM bs_recruiting_operator_jobs
            WHERE tenant_id = %s
            ORDER BY created_at DESC
            """,
            (tenant_id,),
        )
        jobs = [_row_to_job(row) for row in cursor.fetchall()]

        # 话术统计（job_id+category 分组，分类顺序在 Python 内按 SCRIPT_CATEGORIES 归位）
        cursor.execute(
            """
            SELECT job_id, category, COUNT(*) AS cnt
            FROM bs_recruiting_operator_job_scripts
            WHERE tenant_id = %s
            GROUP BY job_id, category
            """,
            (tenant_id,),
        )
        stats: Dict[str, Dict[str, int]] = {}
        for row in cursor.fetchall():
            stats.setdefault(str(row["job_id"]), {})[row["category"]] = row["cnt"]

    # 简历统计（Phase 5：列表带「简历 N · 匹配 M」）
    resume_stats = count_job_resumes(tenant_id)

    for job in jobs:
        per_cat = stats.get(job["id"], {})
        job["script_count"] = sum(per_cat.values())
        job["categories"] = [c for c in SCRIPT_CATEGORIES if c in per_cat] + \
                            [c for c in per_cat if c not in SCRIPT_CATEGORIES]
        job_stats = resume_stats.get(job["id"], {})
        job["resume_count"] = job_stats.get("resume_count", 0)
        job["matched_count"] = job_stats.get("matched_count", 0)
    return jobs


def get_job(tenant_id: str, job_id: str) -> Optional[Dict[str, Any]]:
    """职位详情（含全部话术平铺 scripts + 按分类分组 script_groups），不存在返回 None"""
    job_uuid = _to_uuid(job_id, "job_id")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tenant_id, job_name, notes, status, match_threshold, job_requirements,
                   created_at, updated_at
            FROM bs_recruiting_operator_jobs
            WHERE id = %s AND tenant_id = %s
            """,
            (job_uuid, tenant_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        job = _row_to_job(row)
        scripts = _load_job_scripts(conn, tenant_id, job_uuid)
    return _attach_script_views(job, scripts)


def create_job(
    tenant_id: str,
    *,
    job_name: str,
    notes: Optional[str] = None,
    status: Optional[str] = None,
    match_threshold: Optional[int] = None,
    job_requirements: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """创建职位（tenant_id+job_name 唯一，重名抛 JobServiceError），返回含空话术的完整详情。

    新字段（简历-职位匹配 Phase 1）：status（active/paused，缺省 active）、
    match_threshold（0-100，缺省 70）、job_requirements（结构校验，缺省 NULL）。
    """
    name = (job_name or "").strip()
    if not name:
        raise JobServiceError("职位名称不能为空")
    status_val = _validate_job_status(status) or "active"
    threshold_val = _validate_match_threshold(match_threshold)
    if threshold_val is None:
        threshold_val = DEFAULT_MATCH_THRESHOLD
    requirements_val = _validate_job_requirements(job_requirements)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM bs_recruiting_operator_jobs WHERE tenant_id = %s AND job_name = %s",
            (tenant_id, name),
        )
        if cursor.fetchone():
            raise JobServiceError(f"职位已存在: {name}")
        try:
            cursor.execute(
                """
                INSERT INTO bs_recruiting_operator_jobs
                    (tenant_id, job_name, notes, status, match_threshold, job_requirements)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, tenant_id, job_name, notes, status, match_threshold,
                          job_requirements, created_at, updated_at
                """,
                (
                    tenant_id, name, notes, status_val, threshold_val,
                    psycopg2.extras.Json(requirements_val) if requirements_val else None,
                ),
            )
            row = cursor.fetchone()
            conn.commit()
        except psycopg2.IntegrityError:
            # 并发重名兜底（唯一约束），转为业务 400
            conn.rollback()
            raise JobServiceError(f"职位已存在: {name}")

    logger.info(f"职位创建: tenant={tenant_id}, job={name}, status={status_val}, threshold={threshold_val}")
    return _attach_script_views(_row_to_job(row), [])


def update_job(
    tenant_id: str,
    job_id: str,
    *,
    job_name: Optional[str] = None,
    notes: Optional[str] = None,
    status: Optional[str] = None,
    match_threshold: Optional[int] = None,
    job_requirements: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """更新职位（仅传的字段，updated_at=NOW()），返回更新后详情（含话术）；不存在返回 None。

    可更新字段：job_name / notes / status（active/paused）/ match_threshold（0-100）/
    job_requirements（结构校验，传 {} 清空为 NULL）。
    重名 / 空名称 / 非法取值 / 无待更新字段抛 JobServiceError。
    """
    job_uuid = _to_uuid(job_id, "job_id")
    if all(v is None for v in (job_name, notes, status, match_threshold, job_requirements)):
        raise JobServiceError("无待更新字段")

    name: Optional[str] = None
    if job_name is not None:
        name = job_name.strip()
        if not name:
            raise JobServiceError("职位名称不能为空")
    status_val = _validate_job_status(status)
    threshold_val = _validate_match_threshold(match_threshold)
    requirements_val = _validate_job_requirements(job_requirements)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        if name is not None:
            cursor.execute(
                """
                SELECT 1 FROM bs_recruiting_operator_jobs
                WHERE tenant_id = %s AND job_name = %s AND id <> %s
                """,
                (tenant_id, name, job_uuid),
            )
            if cursor.fetchone():
                raise JobServiceError(f"职位已存在: {name}")

        sets: list = []
        params: list = []
        if name is not None:
            sets.append("job_name = %s")
            params.append(name)
        if notes is not None:
            sets.append("notes = %s")
            params.append(notes)
        if status_val is not None:
            sets.append("status = %s")
            params.append(status_val)
        if threshold_val is not None:
            sets.append("match_threshold = %s")
            params.append(threshold_val)
        if requirements_val is not None:
            sets.append("job_requirements = %s")
            # 传 {} 表示清空要求（存 NULL）
            params.append(psycopg2.extras.Json(requirements_val) if requirements_val else None)
        sets.append("updated_at = NOW()")
        params.extend([job_uuid, tenant_id])
        cursor.execute(
            f"UPDATE bs_recruiting_operator_jobs SET {', '.join(sets)} "
            "WHERE id = %s AND tenant_id = %s "
            "RETURNING id, tenant_id, job_name, notes, status, match_threshold, "
            "job_requirements, created_at, updated_at",
            params,
        )
        row = cursor.fetchone()
        if not row:
            return None
        scripts = _load_job_scripts(conn, tenant_id, job_uuid)
        conn.commit()

    return _attach_script_views(_row_to_job(row), scripts)


def delete_job(tenant_id: str, job_id: str) -> bool:
    """删除职位（物理删，级联删其全部话术；关联简历 job_id 由 FK 置空保留），返回是否删除成功"""
    job_uuid = _to_uuid(job_id, "job_id")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 先显式删话术再删职位（FK 也配了 ON DELETE CASCADE，双保险）
        cursor.execute(
            "DELETE FROM bs_recruiting_operator_job_scripts WHERE job_id = %s AND tenant_id = %s",
            (job_uuid, tenant_id),
        )
        script_rows = cursor.rowcount
        cursor.execute(
            "DELETE FROM bs_recruiting_operator_jobs WHERE id = %s AND tenant_id = %s",
            (job_uuid, tenant_id),
        )
        deleted = cursor.rowcount > 0
        conn.commit()

    if deleted:
        logger.info(f"职位删除: tenant={tenant_id}, job_id={job_uuid}, 级联话术 {script_rows} 条")
    return deleted


# ============== 话术 CRUD ==============

def create_script(
    tenant_id: str,
    job_id: str,
    *,
    category: str,
    title: str,
    content: str,
    sort_order: int = 0,
) -> Optional[Dict[str, Any]]:
    """给职位添加话术，返回话术记录；职位不存在（或非本租户）返回 None。

    分类 / 标题 / 内容非法抛 JobServiceError。
    """
    job_uuid = _to_uuid(job_id, "job_id")
    _validate_script_fields(category=category, title=title, content=content)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM bs_recruiting_operator_jobs WHERE id = %s AND tenant_id = %s",
            (job_uuid, tenant_id),
        )
        if not cursor.fetchone():
            return None
        cursor.execute(
            """
            INSERT INTO bs_recruiting_operator_job_scripts
                (tenant_id, job_id, category, title, content, sort_order)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, tenant_id, job_id, category, title, content, sort_order, created_at, updated_at
            """,
            (tenant_id, job_uuid, category, title.strip(), content.strip(), sort_order or 0),
        )
        row = cursor.fetchone()
        conn.commit()

    logger.info(f"话术创建: tenant={tenant_id}, job_id={job_uuid}, category={category}, title={title.strip()}")
    return _row_to_script(row)


def update_script(
    tenant_id: str,
    script_id: str,
    *,
    category: Optional[str] = None,
    title: Optional[str] = None,
    content: Optional[str] = None,
    sort_order: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """更新话术（仅传的字段，updated_at=NOW()），不存在返回 None。

    分类 / 标题 / 内容非法或无待更新字段抛 JobServiceError。
    """
    script_uuid = _to_uuid(script_id, "script_id")
    _validate_script_fields(category=category, title=title, content=content)
    if category is None and title is None and content is None and sort_order is None:
        raise JobServiceError("无待更新字段")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        sets: list = []
        params: list = []
        if category is not None:
            sets.append("category = %s")
            params.append(category)
        if title is not None:
            sets.append("title = %s")
            params.append(title.strip())
        if content is not None:
            sets.append("content = %s")
            params.append(content.strip())
        if sort_order is not None:
            sets.append("sort_order = %s")
            params.append(sort_order)
        sets.append("updated_at = NOW()")
        params.extend([script_uuid, tenant_id])
        cursor.execute(
            f"UPDATE bs_recruiting_operator_job_scripts SET {', '.join(sets)} "
            "WHERE id = %s AND tenant_id = %s "
            "RETURNING id, tenant_id, job_id, category, title, content, sort_order, created_at, updated_at",
            params,
        )
        row = cursor.fetchone()
        if not row:
            return None
        conn.commit()

    return _row_to_script(row)


def delete_script(tenant_id: str, script_id: str) -> bool:
    """删除话术（物理删），返回是否删除成功"""
    script_uuid = _to_uuid(script_id, "script_id")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM bs_recruiting_operator_job_scripts WHERE id = %s AND tenant_id = %s",
            (script_uuid, tenant_id),
        )
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
