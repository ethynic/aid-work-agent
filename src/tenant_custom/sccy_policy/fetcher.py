"""水城创业网（gt_sccy）只读拉取（fetcher；连接/白名单机制同 lyjy fetcher）。

每日全量：zc_article 全表拉取（含状态列），有效行 = audit=1 AND closed=0 且
分类未隐藏（hidden=0，Python 侧按 cate 映射过滤——sccy 删除/下架都是状态位
翻转，隐藏分类的文章一并视为无效）；全量即对账，无水位。
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.engine import Engine

from src.tenant_custom.lyjy_employment.fetcher import _rows, _select_sql

# 列白名单（sccy 库同样白名单纪律；求职者/用户侧 zc_member 等表不在清单内）
ARTICLE_TABLE = "zc_article"
ARTICLE_CATALOG_COLUMNS = (
    "article_id", "cate_id", "title", "audit", "closed", "dateline",
)
ARTICLE_CONTENT_COLUMNS = (
    "article_id", "cate_id", "title", "desc", "details", "linkurl",
    "audit", "closed", "dateline",
)
CATE_TABLE = "zc_article_cate"
CATE_COLUMNS = ("cate_id", "parent_id", "title", "hidden")

VALID_AUDIT = 1
VALID_CLOSED = 0
HIDDEN = 1


def fetch_articles(engine: Engine) -> List[dict]:
    """zc_article 全量（目录+内容一次拉齐，千级行；状态过滤在 Python 侧做）。"""
    return _rows(engine, _select_sql(ARTICLE_TABLE, ARTICLE_CONTENT_COLUMNS))


def fetch_cate_rows(engine: Engine) -> List[dict]:
    """zc_article_cate 全量（分类路径 + hidden 过滤依据）。"""
    return _rows(engine, _select_sql(CATE_TABLE, CATE_COLUMNS))


def article_valid(row: Dict[str, Any], hidden_cate_ids: set) -> bool:
    """有效行判定：audit=1（发布）AND closed=0 AND 分类未隐藏。"""
    if _int(row.get("audit")) != VALID_AUDIT or _int(row.get("closed")) != VALID_CLOSED:
        return False
    return str(row.get("cate_id")) not in hidden_cate_ids


def build_cate_maps(cate_rows: List[dict]) -> Dict[str, Any]:
    """分类映射：{path_of: {cate_id: "根 / 子"}, hidden_ids: set}（防环：最多上溯 10 层）。"""
    rows = {str(r["cate_id"]): r for r in cate_rows}
    hidden_ids = {
        str(r["cate_id"]) for r in cate_rows if _int(r.get("hidden")) == HIDDEN
    }
    path_of: Dict[str, str] = {}
    for cate_id in rows:
        names: List[str] = []
        node = cate_id
        seen = set()
        while node and node in rows and node not in seen and len(seen) <= 10:
            seen.add(node)
            names.append(str(rows[node].get("title") or ""))
            node = str(rows[node].get("parent_id") or "0")
        path_of[cate_id] = " / ".join(reversed([n for n in names if n]))
    return {"path_of": path_of, "hidden_ids": hidden_ids}


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
