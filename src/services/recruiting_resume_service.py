"""招聘操作智能体简历库服务层

从 src/api/recruiting_operator.py 抽取的核心业务逻辑（API 变薄壳）：
- 常量与工具函数：状态/来源枚举、base64 图片落盘、日期解析、ILIKE 转义
- 简历 CRUD 服务函数（表 bs_recruiting_operator_resumes）
- CLI 结果契约适配（boss_resume_detail 工具 payload → 入库）

分层约定：
- tenant_id/user_id 显式传参，不依赖 saas context / HTTP Request
- 校验失败抛 ValueError（中文消息），由调用方（API 层转 400 / 工具层转失败结果）
- DB 访问照搬原 SQL（get_db_connection + %s 参数化），事务边界与原 API 一致

调用方：
- HTTP API（src/api/recruiting_operator.py，前端「简历库」业务页）
- 本地工具 boss_resume_detail（src/local_tools/proxy_tool.py，云端编排自动落库）

安全约束：图片字节绝不进 LLM 上下文（recruiting-operator 上下文预算仅 8000 token），
工具层只回紧凑摘要（resume_id/candidate_name/job_name/image_count/ocr_char_count）。
"""
from __future__ import annotations

import base64
import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg2.extras
from loguru import logger

from src.core.storage import ensure_tenant_storage_dir
from src.db.database import get_db_connection

# 状态枚举（前端中文标签：new新简历/viewed已查看/shortlisted有意向/interviewed已约面/rejected不合适）
RESUME_STATUSES = ("new", "viewed", "shortlisted", "interviewed", "rejected")
# 来源枚举（boss=CLI 入库 / manual=页面补录）
RESUME_SOURCES = ("boss", "manual")

# 单张简历图片大小上限 10MB（仅约束 base64 直传路，/api/upload 路由有自己的上限）
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
# base64 字符数预检上限（10MB 原始字节编码后的上界，含 padding 余量）。
# 解码前先做粗检，避免超限 payload 先整段解码造成内存放大；精确字节仍以解码后校验为准。
_MAX_BASE64_CHARS = (_MAX_IMAGE_BYTES * 4 // 3) + 8

# base64 直传落盘目录（storage/tenants/{tenant_id}/recruiting/，
# 文件名 file_{uuid12}{ext}，GET /api/files/{file_id} 的磁盘兜底扫描可长期服务）
_IMAGE_SCENE = "recruiting"

# data URL 前缀（data:image/png;base64,xxxx）
_DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w./+-]+);base64,(?P<data>.+)$", re.DOTALL)

# image/* MIME -> 落盘扩展名（白名单：仅保留 /api/files mime map 能正确服务的类型）
_MIME_EXT_MAP = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
}

# 列表页轻量字段（不含 ocr_text，全文可能很大；key_info 仅详情返回）
_LIST_COLUMNS = (
    "id, tenant_id, user_id, candidate_name, job_id, job_name, candidate_info, images, "
    "source, status, match_score, match_summary, match_status, "
    "remark, fetched_at, created_at, updated_at"
)


class ResumePayloadError(ValueError):
    """CLI 结果 payload 不符契约（boss_resume_detail 工具入库专用）。

    与 ValueError 兼容（isinstance 检查两可），但语义独立：
    工具层捕获后返回 RESUME_PAYLOAD_INVALID，且不落任何库/盘数据（fail-loud，禁止半截入库）。
    """


# ============== 建表（幂等） ==============

def init_recruiting_operator_tables(conn) -> None:
    """幂等建 recruiting_operator 表（bs_recruiting_operator_resumes）。

    保留在 src.api.recruiting_operator 模块的再导出（测试与 src/db/database.py 启动初始化沿用旧路径）。

    2026-08-17 简历-职位匹配 Phase 1 关联严密化：
    - 新库 CREATE 直接带 job_id / match_score / match_summary / match_status / key_info 列
      （job_id 外键引用 bs_recruiting_operator_jobs，删职位置空；启动顺序保证 jobs 表先建）
    - 老库用 ALTER ADD COLUMN IF NOT EXISTS 幂等补列 + DO 块幂等补 FK 约束 + 补索引
    - 存量回填：按 job_name 精确匹配（UNIQUE(tenant_id,job_name) 保证唯一命中）回填 job_id，
      仅命中 job_id IS NULL 的行（幂等）；匹配不上的保持 NULL 由前端提示
    """
    cursor = conn.cursor()

    # bs_recruiting_operator_resumes：简历库
    # images 存 [{file_id, name}] 有序多图；candidate_info 存学历/工作年限/期望薪资等灵活 key
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bs_recruiting_operator_resumes (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT,
            candidate_name TEXT,
            job_id UUID CONSTRAINT fk_bs_ror_job
                REFERENCES bs_recruiting_operator_jobs(id) ON DELETE SET NULL,
            job_name TEXT,
            candidate_info JSONB,
            images JSONB NOT NULL DEFAULT '[]'::jsonb,
            ocr_text TEXT,
            source TEXT NOT NULL DEFAULT 'boss',
            status TEXT NOT NULL DEFAULT 'new',
            match_score INT,
            match_summary TEXT,
            match_status TEXT,
            key_info JSONB,
            remark TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 老库幂等加列（新库 CREATE 已带列，此处 no-op；match_* / key_info Phase 1 只建列不写值）
    cursor.execute(
        "ALTER TABLE bs_recruiting_operator_resumes ADD COLUMN IF NOT EXISTS job_id UUID"
    )
    cursor.execute(
        "ALTER TABLE bs_recruiting_operator_resumes ADD COLUMN IF NOT EXISTS match_score INT"
    )
    cursor.execute(
        "ALTER TABLE bs_recruiting_operator_resumes ADD COLUMN IF NOT EXISTS match_summary TEXT"
    )
    cursor.execute(
        "ALTER TABLE bs_recruiting_operator_resumes ADD COLUMN IF NOT EXISTS match_status TEXT"
    )
    cursor.execute(
        "ALTER TABLE bs_recruiting_operator_resumes ADD COLUMN IF NOT EXISTS key_info JSONB"
    )
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_bs_ror_tenant
        ON bs_recruiting_operator_resumes(tenant_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_bs_ror_tenant_job
        ON bs_recruiting_operator_resumes(tenant_id, job_name)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_bs_ror_tenant_fetched
        ON bs_recruiting_operator_resumes(tenant_id, fetched_at)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_bs_ror_tenant_job_id
        ON bs_recruiting_operator_resumes(tenant_id, job_id)
    """)

    # 老库补 FK 约束 + 存量回填（jobs 表可能尚未创建——启动顺序 jobs 在前，此处兜底检查）
    cursor.execute(
        "SELECT to_regclass('bs_recruiting_operator_jobs') IS NOT NULL AS jobs_ready"
    )
    if cursor.fetchone()["jobs_ready"]:
        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'fk_bs_ror_job'
                      AND conrelid = 'bs_recruiting_operator_resumes'::regclass
                ) THEN
                    ALTER TABLE bs_recruiting_operator_resumes
                        ADD CONSTRAINT fk_bs_ror_job FOREIGN KEY (job_id)
                        REFERENCES bs_recruiting_operator_jobs(id) ON DELETE SET NULL;
                END IF;
            END $$;
        """)
        cursor.execute(
            """
            UPDATE bs_recruiting_operator_resumes r
            SET job_id = j.id
            FROM bs_recruiting_operator_jobs j
            WHERE r.tenant_id = j.tenant_id
              AND r.job_name = j.job_name
              AND r.job_id IS NULL
            """
        )
        if cursor.rowcount:
            logger.info(f"recruiting_operator 存量简历回填 job_id: {cursor.rowcount} 行")

    logger.info("recruiting_operator 表已就绪 (bs_recruiting_operator_resumes)")


# ============== 工具函数 ==============

def _parse_json_field(val: Any) -> Any:
    """把 JSONB 字段从 str 解析为 dict/list（PostgreSQL JSONB 一般已是 dict，但兜底）"""
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return None
    return val


def _row_to_resume(row) -> Dict[str, Any]:
    """行记录转 dict，JSONB 字段兜底解析（candidate_info/images/key_info），job_id 转字符串"""
    item = dict(row)
    item["candidate_info"] = _parse_json_field(item.get("candidate_info")) or {}
    item["images"] = _parse_json_field(item.get("images")) or []
    item["key_info"] = _parse_json_field(item.get("key_info"))
    if item.get("job_id") is not None:
        item["job_id"] = str(item["job_id"])
    return item


def _parse_date_bound(value: str, is_to: bool) -> datetime:
    """把日期筛选参数转为 TIMESTAMP。

    支持 YYYY-MM-DD（from 取当天 00:00:00，to 取当天 23:59:59）与完整 ISO 格式。
    格式非法抛 ValueError，由调用方转 400。
    """
    v = value.strip()
    try:
        if len(v) == 10:
            d = datetime.strptime(v, "%Y-%m-%d")
            return d.replace(hour=23, minute=59, second=59) if is_to else d
        return datetime.fromisoformat(v)
    except ValueError:
        raise ValueError(f"日期格式非法: {value}")


def _parse_fetched_at(value: str) -> datetime:
    """把创建请求里的 fetched_at（ISO 字符串）转为 datetime，非法抛 ValueError"""
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"获取日期格式非法: {value}")


def save_base64_image(tenant_id: str, data: str, name: Optional[str], mime_type: str) -> Dict[str, str]:
    """把 base64 图片落盘到租户 recruiting 目录，返回 {file_id, name}。

    - data 兼容 data:image/png;base64,xxxx 前缀写法
    - mime_type（或 data URL 前缀里的 mime）必须为 image/*
    - 单张上限 10MB
    - 文件名 file_{uuid12}{ext}，与 /api/files/{file_id} 磁盘兜底扫描（f.stem == file_id）对齐
    """
    data = (data or "").strip()
    effective_mime = (mime_type or "").strip().lower()
    m = _DATA_URL_RE.match(data)
    if m:
        if not effective_mime:
            effective_mime = m.group("mime").lower()
        data = m.group("data")
    if not effective_mime.startswith("image/"):
        raise ValueError(f"mime_type 必须为 image/* 类型，当前为 {effective_mime or '空'}")
    # 去掉换行等空白后再严格解码，避免静默丢字符
    cleaned = re.sub(r"\s+", "", data)
    # 解码前先按 base64 字符数粗检（≈原始字节的 4/3），超限直接拒绝，不进入解码
    if len(cleaned) > _MAX_BASE64_CHARS:
        raise ValueError("单张图片不能超过 10MB")
    try:
        raw = base64.b64decode(cleaned, validate=True)
    except Exception as e:
        raise ValueError(f"base64 解码失败: {e}")
    if not raw:
        raise ValueError("图片内容为空")
    if len(raw) > _MAX_IMAGE_BYTES:
        raise ValueError("单张图片不能超过 10MB")
    ext = _MIME_EXT_MAP.get(effective_mime)
    if not ext:
        raise ValueError(f"不支持的图片类型 {effective_mime}，仅支持 png/jpeg/gif")
    file_id = f"file_{uuid.uuid4().hex[:12]}"
    dir_path = ensure_tenant_storage_dir(tenant_id, _IMAGE_SCENE)
    file_path = os.path.join(dir_path, f"{file_id}{ext}")
    with open(file_path, "wb") as f:
        f.write(raw)
    return {"file_id": file_id, "name": name or f"{file_id}{ext}"}


# ============== 简历 CRUD 服务 ==============

def create_resume_record(
    tenant_id: str,
    user_id: Optional[str],
    *,
    candidate_name: str,
    job_name: Optional[str] = None,
    job_id: Optional[str] = None,
    candidate_info: Optional[Dict[str, Any]] = None,
    ocr_text: Optional[str] = None,
    images: Optional[List[Dict[str, str]]] = None,
    images_base64: Optional[List[Dict[str, str]]] = None,
    source: str = "manual",
    fetched_at: Optional[str] = None,
    remark: Optional[str] = None,
) -> Dict[str, Any]:
    """创建简历记录，返回完整记录 dict；校验失败抛 ValueError（中文消息）。

    - job_id：可选，硬关联职位（须为本租户职位；仅带 job_id 未带 job_name 时回填职位名显示冗余）
    - images：[{file_id, name}] 已上传引用路
    - images_base64：[{data, name, mime_type}] 直传路（服务端落盘转 file_id，优先合并）
    - fetched_at：ISO 字符串，缺省为当前时间
    """
    if source not in RESUME_SOURCES:
        raise ValueError(f"来源值非法: source={source} not in {RESUME_SOURCES}")
    if not (candidate_name or "").strip():
        raise ValueError("候选人姓名不能为空")

    # job_id 校验：格式非法 / 不属于本租户 → 拒绝入库（绝不静默降级）
    if job_id is not None:
        job_uuid, canonical_job_name = _lookup_job_id(tenant_id, job_id)
        if not job_name:
            job_name = canonical_job_name
    else:
        job_uuid = None

    # 两路图片合并：base64 直传先落盘（优先），再拼 /api/upload 引用。
    # 中途失败时清理本次已落盘文件，禁止半截入库留下孤儿文件。
    merged_images: List[Dict[str, str]] = []
    try:
        for item in (images_base64 or []):
            merged_images.append(
                save_base64_image(tenant_id, item.get("data"), item.get("name"), item.get("mime_type"))
            )
        for item in (images or []):
            file_id = (item.get("file_id") or "").strip()
            if not file_id:
                raise ValueError("images[].file_id 不能为空")
            merged_images.append({"file_id": file_id, "name": item.get("name") or file_id})
    except ValueError:
        _cleanup_image_files(tenant_id, merged_images)
        raise

    if fetched_at:
        fetched_at_dt = _parse_fetched_at(fetched_at)
    else:
        fetched_at_dt = None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_recruiting_operator_resumes
                (tenant_id, user_id, candidate_name, job_id, job_name, candidate_info, images,
                 ocr_text, source, status, remark, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'new', %s, COALESCE(%s, CURRENT_TIMESTAMP))
            RETURNING *
            """,
            (
                tenant_id, user_id, candidate_name.strip(), job_uuid, job_name,
                psycopg2.extras.Json(candidate_info) if candidate_info is not None else None,
                psycopg2.extras.Json(merged_images),
                ocr_text, source, remark, fetched_at_dt,
            ),
        )
        row = cursor.fetchone()
        conn.commit()

    logger.info(f"简历入库: tenant={tenant_id}, candidate={candidate_name}, "
                f"job={job_name}, job_id={job_uuid}, source={source}, images={len(merged_images)}")
    return _row_to_resume(row)


def list_resumes(
    tenant_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: Optional[str] = None,
    job_name: Optional[str] = None,
    job_id: Optional[str] = None,
    status: Optional[str] = None,
    fetched_at_from: Optional[str] = None,
    fetched_at_to: Optional[str] = None,
) -> Dict[str, Any]:
    """简历列表（分页 + 筛选，轻量不含 ocr_text，按 created_at DESC），返回 {total, items, page, page_size}。

    job_id 非空时按职位硬关联精确匹配（与 job_name 文本筛选可叠加）。
    状态/日期/job_id 格式非法抛 ValueError，由调用方转 400。
    """
    if status and status not in RESUME_STATUSES:
        raise ValueError(f"状态值非法: status={status} not in {RESUME_STATUSES}")
    fetched_from = _parse_date_bound(fetched_at_from, is_to=False) if fetched_at_from else None
    fetched_to = _parse_date_bound(fetched_at_to, is_to=True) if fetched_at_to else None
    # job_id 先转规范 UUID（非法格式 400 而非 DB 层 DataError 500），不做租户校验（筛选条件而已）
    job_uuid: Optional[str] = None
    if job_id:
        try:
            job_uuid = str(uuid.UUID(str(job_id)))
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"job_id 格式非法: {job_id}")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        conditions = ["tenant_id = %s"]
        params: list = [tenant_id]
        if keyword:
            # 转义用户输入里的 %/_/\\，避免被当通配符误匹配（尾随 \\ 会让 ILIKE 直接报错）
            escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append("candidate_name ILIKE %s ESCAPE '\\'")
            params.append(f"%{escaped}%")
        if job_name:
            conditions.append("job_name = %s")
            params.append(job_name)
        if job_uuid:
            conditions.append("job_id = %s")
            params.append(job_uuid)
        if status:
            conditions.append("status = %s")
            params.append(status)
        if fetched_from:
            conditions.append("fetched_at >= %s")
            params.append(fetched_from)
        if fetched_to:
            conditions.append("fetched_at <= %s")
            params.append(fetched_to)
        where = " AND ".join(conditions)

        cursor.execute(f"SELECT COUNT(*) AS total FROM bs_recruiting_operator_resumes WHERE {where}", params)
        total = cursor.fetchone()["total"]

        offset = (page - 1) * page_size
        cursor.execute(
            f"SELECT {_LIST_COLUMNS} FROM bs_recruiting_operator_resumes WHERE {where} "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [page_size, offset],
        )
        items = [_row_to_resume(row) for row in cursor.fetchall()]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


def list_distinct_jobs(tenant_id: str) -> List[str]:
    """该租户已录入的 distinct 职位列表（筛选下拉用）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT job_name FROM bs_recruiting_operator_resumes
            WHERE tenant_id = %s AND job_name IS NOT NULL AND job_name <> ''
            ORDER BY job_name
            """,
            (tenant_id,),
        )
        return [row["job_name"] for row in cursor.fetchall()]


def get_resume(tenant_id: str, resume_id: int) -> Optional[Dict[str, Any]]:
    """简历详情（含 ocr_text / images / candidate_info），不存在返回 None"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bs_recruiting_operator_resumes WHERE id = %s AND tenant_id = %s",
            (resume_id, tenant_id),
        )
        row = cursor.fetchone()
        return _row_to_resume(row) if row else None


def update_resume(
    tenant_id: str,
    resume_id: int,
    *,
    candidate_name: Optional[str] = None,
    job_name: Optional[str] = None,
    job_id: Optional[str] = None,
    candidate_info: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
    remark: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """更新简历（仅传的字段：status/remark/job_name/job_id/candidate_name/candidate_info），updated_at=NOW()。

    job_id 三态语义（职位硬关联）：
    - None（未传）= 不修改关联
    - 空串 = 清除关联（job_id 置 NULL，job_name 一并置 NULL）
    - 非空 = 经 _lookup_job_id 校验（格式非法/不存在/非本租户抛 ValueError 中文消息）
      后写 job_id，并回填该职位的 canonical job_name（覆盖 job_name 入参）

    状态非法 / 无待更新字段抛 ValueError；记录不存在返回 None。
    """
    if status is not None and status not in RESUME_STATUSES:
        raise ValueError(f"状态值非法: status={status} not in {RESUME_STATUSES}")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 仅拼传了的字段（None 视为未传）
        sets: list = []
        params: list = []
        if candidate_name is not None:
            sets.append("candidate_name = %s")
            params.append(candidate_name.strip() or None)
        if job_id is not None:
            # 注意：job_name 赋值必须互斥（同一 UPDATE SET 子句对同列二次赋值，
            # PG 报 multiple assignments to same column → 500），故 job_name 入参用 elif 兜底
            if not job_id.strip():
                # 空串 = 清除职位关联（FK 列与显示冗余名一并置空，job_name 入参被覆盖）
                sets.append("job_id = %s")
                params.append(None)
                sets.append("job_name = %s")
                params.append(None)
            else:
                # 非空 = 校验属本租户后硬关联，回填 canonical job_name（覆盖 job_name 入参，与 create 对齐）
                job_uuid, canonical_job_name = _lookup_job_id(tenant_id, job_id)
                sets.append("job_id = %s")
                params.append(job_uuid)
                sets.append("job_name = %s")
                params.append(canonical_job_name)
        elif job_name is not None:
            sets.append("job_name = %s")
            params.append(job_name.strip() or None)
        if candidate_info is not None:
            sets.append("candidate_info = %s")
            params.append(psycopg2.extras.Json(candidate_info))
        if status is not None:
            sets.append("status = %s")
            params.append(status)
        if remark is not None:
            sets.append("remark = %s")
            params.append(remark)
        if not sets:
            raise ValueError("无待更新字段")

        sets.append("updated_at = NOW()")
        params.extend([resume_id, tenant_id])
        cursor.execute(
            f"UPDATE bs_recruiting_operator_resumes SET {', '.join(sets)} "
            "WHERE id = %s AND tenant_id = %s RETURNING *",
            params,
        )
        row = cursor.fetchone()
        if not row:
            return None
        conn.commit()

    return _row_to_resume(row)


def delete_resume(tenant_id: str, resume_id: int) -> bool:
    """删除简历（仅删除库记录，不删除底层图片文件），返回是否删除成功"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM bs_recruiting_operator_resumes WHERE id = %s AND tenant_id = %s",
            (resume_id, tenant_id),
        )
        conn.commit()
        return cursor.rowcount > 0


# ============== CLI 结果契约适配（boss_resume_detail） ==============

def create_resume_record_from_tool_result(
    tenant_id: str,
    user_id: Optional[str],
    payload: Dict[str, Any],
    source: str = "boss",
) -> Dict[str, Any]:
    """把 boss_resume_detail CLI 结果 payload 适配为入库记录，返回完整记录 dict。

    ──【契约对齐点 2026-08-16】明日 CLI `resume-detail` 命令落地时在此对齐字段名 ──
    预期 payload 形状（宽容解析，支持别名；CLI 侧最终字段名以真机联调为准）：
    - candidate_name（必填，别名 name）
    - job_id（可选，本租户职位 id，硬关联；Phase 1 一键链路带出）
    - job_name（别名 job / position）
    - basic_info 或 candidate_info（dict）
    - ocr_text（别名 ocr / text）
    - images / screenshots：[{name?, mime_type?, base64}]
    ────────────────────────────────────────────────────────────────

    职位关联解析（简历-职位匹配设计 §4.1，落库前执行，绝不自动创建职位）：
    - payload 带 job_id → 校验属本租户后直接用（非本租户/不存在抛 ResumePayloadError 拒绝入库）；
      job_name 缺省时回填该职位的规范名
    - 只带 job_name → 租户内精确匹配 jobs（UNIQUE(tenant_id,job_name) 保证唯一命中）：
      命中 → 关联 job_id；0 命中 → job_id=NULL、job_name 原文保留，
      返回 dict 附带临时字段 job_warning「未关联职位（请在职位管理核对）」供工具层摘要透传

    解析失败（无 candidate_name / images 结构不符）抛 ResumePayloadError（中文消息说明期望格式），
    且不落任何库/盘数据（fail-loud，禁止半截入库）；入库阶段的 ValueError（base64 解码失败等）
    同样包装为 ResumePayloadError 上抛，由工具层转 RESUME_PAYLOAD_INVALID。
    """
    if not isinstance(payload, dict):
        raise ResumePayloadError(
            f"CLI 结果 payload 必须为对象，当前为 {type(payload).__name__}；"
            "期望格式需对齐（candidate_name/job_name/basic_info/ocr_text/images）"
        )

    # ──【契约对齐点】字段别名解析 ──
    candidate_name = _first_str(payload, ("candidate_name", "name"))
    job_name = _first_str(payload, ("job_name", "job", "position"))
    ocr_text = _first_str(payload, ("ocr_text", "ocr", "text"))
    basic_info = payload.get("basic_info", payload.get("candidate_info"))
    if basic_info is not None and not isinstance(basic_info, dict):
        raise ResumePayloadError(
            f"basic_info/candidate_info 必须为对象，当前为 {type(basic_info).__name__}；CLI 结果格式需对齐"
        )

    if not candidate_name:
        raise ResumePayloadError(
            "CLI 结果缺少候选人姓名（candidate_name/name）；CLI 结果格式需对齐"
        )

    # images 结构校验：必须是列表，且每项为含 base64 的对象（name/mime_type 可选）
    raw_images = payload.get("images", payload.get("screenshots"))
    if raw_images is None:
        raw_images = []
    if not isinstance(raw_images, list):
        raise ResumePayloadError(
            f"images/screenshots 必须为列表，当前为 {type(raw_images).__name__}；CLI 结果格式需对齐"
        )
    images_base64: List[Dict[str, str]] = []
    for idx, item in enumerate(raw_images):
        if not isinstance(item, dict) or not (item.get("base64") or "").strip():
            raise ResumePayloadError(
                f"images[{idx}] 必须为含 base64 字段的对象（name/mime_type 可选）；CLI 结果格式需对齐"
            )
        images_base64.append({
            "data": item["base64"],
            "name": item.get("name"),
            "mime_type": item.get("mime_type") or "image/png",
        })

    # 职位关联解析（§4.1）：带 job_id 校验租户后直用；只带 job_name 精确匹配；绝不自动创建职位
    job_id, job_name, job_warning = _resolve_job_link(tenant_id, payload.get("job_id"), job_name)

    try:
        record = create_resume_record(
            tenant_id,
            user_id,
            candidate_name=candidate_name,
            job_name=job_name,
            job_id=job_id,
            candidate_info=basic_info,
            ocr_text=ocr_text,
            images_base64=images_base64,
            source=source,
            fetched_at=_first_str(payload, ("fetched_at",)),
            remark=_first_str(payload, ("remark",)),
        )
    except ResumePayloadError:
        raise
    except ValueError as e:
        # 入库阶段失败（base64 解码/超限/mime 白名单等）：包装上抛，工具层统一 fail-loud
        raise ResumePayloadError(f"{e}；CLI 结果格式需对齐") from e

    # 临时字段（非 DB 列）：未关联职位提示，工具层摘要透传后即弃
    if job_warning:
        record["job_warning"] = job_warning
    return record


# ============== 内部辅助 ==============

def _lookup_job_id(tenant_id: str, job_id: Any) -> tuple:
    """校验 job_id（格式 + 属本租户），返回 (规范 UUID 字符串, 职位名)。

    格式非法 / 不存在 / 非本租户职位抛 ValueError（中文消息），由调用方转 400 / 工具失败结果。
    """
    try:
        job_uuid = str(uuid.UUID(str(job_id)))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"job_id 格式非法: {job_id}")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT job_name FROM bs_recruiting_operator_jobs WHERE id = %s AND tenant_id = %s",
            (job_uuid, tenant_id),
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"job_id 对应职位不存在或不属于本租户: {job_id}")
        return job_uuid, row["job_name"]


def _resolve_job_link(
    tenant_id: str, job_id_raw: Any, job_name: Optional[str]
) -> tuple:
    """工具层落库前的职位关联解析（设计 §4.1），返回 (job_id, job_name, warning)。

    - 带 job_id：校验属本租户后直接用（非本租户/不存在抛 ResumePayloadError 拒绝入库，
      绝不静默降级为未关联）；job_name 缺省时回填该职位规范名
    - 只带 job_name：租户内精确匹配 jobs（UNIQUE(tenant_id,job_name) 保证唯一命中）：
      命中 → (job_id, job_name, None)；0 命中 → (None, 原文, 未关联职位 warning)
    - 绝不自动创建职位
    """
    if job_id_raw is not None:
        if not isinstance(job_id_raw, (str, uuid.UUID)):
            raise ResumePayloadError(
                f"job_id 必须为字符串 UUID，当前为 {type(job_id_raw).__name__}；CLI 结果格式需对齐"
            )
        try:
            job_uuid, canonical_job_name = _lookup_job_id(tenant_id, job_id_raw)
        except ValueError as e:
            raise ResumePayloadError(f"{e}；CLI 结果格式需对齐") from e
        return job_uuid, (job_name or canonical_job_name), None

    if not job_name:
        return None, job_name, None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM bs_recruiting_operator_jobs WHERE tenant_id = %s AND job_name = %s",
            (tenant_id, job_name),
        )
        row = cursor.fetchone()
    if row:
        return str(row["id"]), job_name, None
    logger.warning(f"简历未关联职位（无精确匹配）: tenant={tenant_id}, job_name={job_name}")
    return None, job_name, f"未关联职位「{job_name}」（请在职位管理核对）"


def _first_str(payload: Dict[str, Any], keys) -> Optional[str]:
    """按别名顺序取第一个非空字符串字段（strip 后为空视为缺失）"""
    for key in keys:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _cleanup_image_files(tenant_id: str, images: List[Dict[str, str]]) -> None:
    """尽力清理本次调用已落盘的图片文件（失败静默，仅记日志），配合 fail-loud 禁止半截入库"""
    for item in images:
        file_id = item.get("file_id") or ""
        if not file_id.startswith("file_"):
            continue
        try:
            dir_path = ensure_tenant_storage_dir(tenant_id, _IMAGE_SCENE)
            for ext in _MIME_EXT_MAP.values():
                path = os.path.join(dir_path, f"{file_id}{ext}")
                if os.path.exists(path):
                    os.remove(path)
        except Exception as e:  # noqa: BLE001 清理是尽力而为，不掩盖主错误
            logger.warning(f"清理半截入库图片失败 file_id={file_id}: {e}")
