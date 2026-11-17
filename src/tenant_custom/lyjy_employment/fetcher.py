"""聊易就业（gt_lyjy）只读拉取（fetcher，同步 SQL 层）。

设计依据 docs/research/employment-agent-db-sync-research.md §7.4 四步拉取：
① 目录全量（轻列全表，与 records 状态比对）；② `*_delete` 表删除事件（仅 id
集合，无删除时间列只能集合差）；③ 到期扫描（deadline 过期的在招岗位）；
④ 内容增量（qs_jobs 按 refreshtime 水位、文章表按 addtime 水位，仅有效状态行）。

**列白名单**（隐私红线，设计 §7.5）：每张表只 SELECT 显式清单列——求职者侧 PII
列与 qs_resume/qs_members 等表不在清单内；源库将来加列也不会进入管线（白名单
机制，不是黑名单）。表名/列名全部来自本模块常量，不拼接任何外部输入；值一律
绑定参数。

连接：source 行 connection_encrypted（Fernet 整包 JSON）解密后建 SQLAlchemy
短连接 engine，用完 dispose（模式同 src/services/data_analysis/db_connector.py）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Set
from urllib.parse import quote_plus

from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# ------------------------------- 源表与白名单 -------------------------------

# 同步源表 → external_id 前缀（native_id/external_id = f"{prefix}:{id}"，
# 同 module 下多表共账本，native_id 必须带表前缀防主键撞号）
TABLE_PREFIXES = {
    "qs_jobs": "job",
    "qs_guide": "guide",
    "qs_article": "article",
    "qs_notice": "notice",
    "qs_explain": "explain",
}

# ① 目录全量（轻列，数万行秒级；含名称列供 records 账本 payload 展示字段）
JOBS_CATALOG_COLUMNS = (
    "id", "audit", "display", "close_type", "deadline", "refreshtime",
    "jobs_name", "companyname",
)
ARTICLE_TABLES = ("qs_guide", "qs_article", "qs_notice", "qs_explain")
# qs_guide/qs_article 有 audit 列；qs_notice/qs_explain 只有 is_display
ARTICLE_CATALOG_COLUMNS = {
    "qs_guide": ("id", "audit", "is_display", "addtime", "title"),
    "qs_article": ("id", "audit", "is_display", "addtime", "title"),
    "qs_notice": ("id", "is_display", "addtime", "title"),
    "qs_explain": ("id", "is_display", "addtime", "title"),
}

# ④ 内容增量（渲染+metadata 全字段白名单；不在清单内的列一律不出现在 SELECT）
JOBS_CONTENT_COLUMNS = (
    "id", "jobs_name", "companyname", "company_id", "trade_cn", "nature_cn",
    "district_cn", "tag_cn", "amount", "category_cn", "subclass", "education_cn",
    "experience_cn", "minwage", "maxwage", "negotiable", "contents", "addtime",
    "deadline", "refreshtime", "focus_group", "is_ex_serviceman", "is_disabled",
    "disabilityType", "disability", "sex_cn", "age", "language", "jobAsk",
    "cert", "scale_cn",
)
ARTICLE_CONTENT_COLUMNS = {
    "qs_guide": ("id", "type_id", "title", "content", "author", "source",
                 "addtime", "subsite_id"),
    "qs_article": ("id", "type_id", "parentid", "title", "content", "author",
                   "source", "addtime", "subsite_id"),
    "qs_notice": ("id", "type_id", "title", "content", "addtime", "subsite_id"),
    "qs_explain": ("id", "type_id", "title", "content", "addtime", "subsite_id"),
}

# 联系方式（决议 8：岗位联系人/电话非隐私，进正文「联系方式」行）；qq/email 不需要。
# *_show 展示位一并取用：雇主在源站隐藏的联系方式不同步给用户
CONTACT_COLUMNS = ("pid", "contact", "telephone", "landline_tel",
                   "contact_show", "telephone_show", "landline_tel_show")
# 企业简介（并入岗位正文 200 字内；不独立成档）
COMPANY_PROFILE_COLUMNS = ("id", "companyname", "contents")

# ② 删除事件表（主表行移入 *_delete 表全列快照，无删除时间列；仅取 id 集合）。
# 测试库实测（2026-11-17 只读探查）：gt_lyjy 仅 qs_jobs_delete 存在（151 行），
# 文章表无对应 _delete 表——文章删除靠目录状态位翻转 + 消失两击兜底；
# 客户若补建表，在此登记即可纳入
DELETE_TABLES = {
    "qs_jobs": "qs_jobs_delete",
}

# ③ 到期扫描与 ④ 增量条件（集中常量：close_type/deadline 语义待客户确认，
# 只需改这里；qs_jobs 有 (audit, refreshtime) 复合索引）
JOBS_VALID_WHERE = "audit = 1 AND display = 1 AND (close_type IS NULL OR close_type = 0)"
JOBS_EXPIRED_WHERE = (
    "audit = 1 AND display = 1 AND deadline > 0 AND deadline < UNIX_TIMESTAMP()"
)
JOBS_INCREMENT_WHERE = (
    "refreshtime > :watermark AND audit = 1 AND display = 1 "
    "AND (close_type IS NULL OR close_type = 0)"
)
# 文章表：guide/article 有 audit；notice/explain 仅 is_display
JOBS_INCREMENT_ORDER = "refreshtime ASC"
ARTICLE_INCREMENT_WHERE = {
    "qs_guide": "addtime > :watermark AND audit = 1 AND is_display = 1",
    "qs_article": "addtime > :watermark AND audit = 1 AND is_display = 1",
    "qs_notice": "addtime > :watermark AND is_display = 1",
    "qs_explain": "addtime > :watermark AND is_display = 1",
}

# 分类字典表（每 run 现拉内存映射；id → 名称/父级，路径在渲染层拼）
DICT_TABLE_COLUMNS = {
    "qs_category_jobs": ("id", "categoryname"),
    "qs_category_district": ("id", "categoryname"),
    "qs_article_category": ("id", "parentid", "categoryname"),
    "qs_guide_category": ("id", "categoryname"),
}

IN_CLAUSE_CHUNK = 200  # 批量 IN 分片大小
CONNECT_TIMEOUT_SECONDS = 10


class FetchError(RuntimeError):
    """客户库拉取失败（连接/SQL/超时）。"""


class MissingConnectionConfigError(FetchError):
    """source 行连接配置缺失或字段不全（未开通/配置损坏）。"""


def load_connection_config(source_row: Dict[str, Any]) -> Dict[str, Any]:
    """解密 source 行 connection_encrypted → {host, port, database, user, password}。

    Fernet 整包加密（src/db/encryption.py encryption_manager，data_connectors
    同模式）；异常统一归一为 MissingConnectionConfigError，绝不把密文/凭据带进
    异常消息。
    """
    encrypted = source_row.get("connection_encrypted")
    if not encrypted:
        raise MissingConnectionConfigError(
            f"source 行缺少连接配置（connection_encrypted 为空）: module={source_row.get('module')}"
        )
    from src.db.encryption import encryption_manager

    try:
        config = json.loads(encryption_manager.decrypt(encrypted))
    except Exception as e:  # noqa: BLE001 解密失败不外泄密文细节
        raise MissingConnectionConfigError(
            f"连接配置解密失败（{type(e).__name__}），请重新保存数据源连接"
        ) from e
    missing = [k for k in ("host", "port", "database", "user", "password")
               if not config.get(k)]
    if missing:
        raise MissingConnectionConfigError(
            f"连接配置字段不全（缺 {','.join(missing)}），请补全数据源连接"
        )
    return config


def build_engine(config: Dict[str, Any]) -> Engine:
    """客户 MySQL 只读短连接 engine（用完必须 dispose）。"""
    url = (
        f"mysql+pymysql://{quote_plus(str(config['user']))}:"
        f"{quote_plus(str(config['password']))}@"
        f"{config['host']}:{int(config['port'])}/{config['database']}"
    )
    return create_engine(
        url,
        connect_args={
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "charset": "utf8mb4",
        },
        pool_pre_ping=True,
    )


def _quote_ident(identifier: str) -> str:
    """MySQL 标识符包裹（表/列名全部来自本模块常量；`desc` 等保留字需要）。"""
    return f"`{identifier}`"


def _select_sql(table: str, columns: Iterable[str], where: str = "",
                order: str = "") -> str:
    """按白名单列拼 SELECT（标识符来自常量；值条件一律绑定参数）。"""
    cols = ", ".join(_quote_ident(c) for c in columns)
    sql = f"SELECT {cols} FROM {_quote_ident(table)}"
    if where:
        sql += f" WHERE {where}"
    if order:
        sql += f" ORDER BY {order}"
    return sql


def _rows(engine: Engine, sql: str, params: Dict[str, Any] | None = None) -> List[dict]:
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            return [dict(row._mapping) for row in result]
    except Exception as e:  # noqa: BLE001 归一为 FetchError（脱敏：不含连接串）
        raise FetchError(f"客户库查询失败 table/SQL 见日志: {type(e).__name__}: {e}") from e


def _chunked(ids: List[Any], size: int = IN_CLAUSE_CHUNK) -> Iterable[List[Any]]:
    for start in range(0, len(ids), size):
        yield ids[start:start + size]


# ------------------------------- 四步拉取 -------------------------------

def fetch_catalog(engine: Engine, table: str) -> List[dict]:
    """① 目录全量：jobs 六状态列（+名称展示列）；文章表 id/audit/is_display/addtime。"""
    if table == "qs_jobs":
        columns = JOBS_CATALOG_COLUMNS
    else:
        columns = ARTICLE_CATALOG_COLUMNS[table]
    return _rows(engine, _select_sql(table, columns))


def fetch_delete_ids(engine: Engine, table: str) -> Set[int]:
    """② 删除事件：*_delete 表全量 id 集合（单列）。"""
    delete_table = DELETE_TABLES[table]
    rows = _rows(engine, _select_sql(delete_table, ("id",)))
    return {int(r["id"]) for r in rows}


def fetch_expired_job_ids(engine: Engine) -> List[int]:
    """③ 到期扫描：在招状态但 deadline 已过期的岗位 id（走 audit_deadline 索引）。"""
    rows = _rows(
        engine,
        _select_sql("qs_jobs", ("id",), where=JOBS_EXPIRED_WHERE),
    )
    return [int(r["id"]) for r in rows]


def fetch_jobs_increment(engine: Engine, watermark: int) -> List[dict]:
    """④ 岗位内容增量：refreshtime > 水位 且 审核通过/展示中/未关闭（白名单全列）。"""
    return _rows(
        engine,
        _select_sql(
            "qs_jobs", JOBS_CONTENT_COLUMNS,
            where=JOBS_INCREMENT_WHERE, order=JOBS_INCREMENT_ORDER,
        ),
        {"watermark": int(watermark)},
    )


def fetch_articles_increment(engine: Engine, table: str, watermark: int) -> List[dict]:
    """④ 文章内容增量：addtime > 水位 且 有效状态（只覆盖新增，更新靠每日对账）。"""
    return _rows(
        engine,
        _select_sql(
            table, ARTICLE_CONTENT_COLUMNS[table],
            where=ARTICLE_INCREMENT_WHERE[table], order="addtime ASC",
        ),
        {"watermark": int(watermark)},
    )


# ------------------------------- 按需批量拉取 -------------------------------

def fetch_contacts(engine: Engine, job_ids: List[int]) -> Dict[int, dict]:
    """qs_jobs_contact 按 pid 批量取（联系方式；contact_show 等展示位不取）。"""
    result: Dict[int, dict] = {}
    for chunk in _chunked(job_ids):
        rows = _rows(
            engine,
            _select_sql("qs_jobs_contact", CONTACT_COLUMNS)
            + f" WHERE pid IN ({', '.join(str(int(i)) for i in chunk)})",
        )
        for row in rows:
            result[int(row["pid"])] = row
    return result


def fetch_company_profiles(engine: Engine, company_ids: List[int]) -> Dict[int, dict]:
    """qs_company_profile 按 id 批量取（企业简介 200 字截断在渲染层做）。"""
    result: Dict[int, dict] = {}
    for chunk in _chunked(company_ids):
        rows = _rows(
            engine,
            _select_sql("qs_company_profile", COMPANY_PROFILE_COLUMNS)
            + f" WHERE id IN ({', '.join(str(int(i)) for i in chunk)})",
        )
        for row in rows:
            result[int(row["id"])] = row
    return result


def fetch_dict_rows(engine: Engine, table: str) -> List[dict]:
    """分类字典表现拉（列清单见 DICT_TABLE_COLUMNS）。"""
    return _rows(engine, _select_sql(table, DICT_TABLE_COLUMNS[table]))


def load_dictionary_cache(engine: Engine) -> Dict[str, dict]:
    """一次 run 的全部分类字典（qs_category_jobs/district + 文章两张分类表）。"""
    cache: Dict[str, dict] = {}
    for table in DICT_TABLE_COLUMNS:
        cache[table] = {str(r["id"]): r for r in fetch_dict_rows(engine, table)}
        logger.bind(module="lyjy_employment").debug(
            "字典缓存 {} {} 行", table, len(cache[table])
        )
    return cache
