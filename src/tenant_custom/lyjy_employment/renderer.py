"""聊易就业/水城创业 → 知识文档渲染（renderer，纯函数模块）。

两套模板（研究 §7.5 定稿）：职位模板（结构化字段区，宏陶 v1.7 同款）与文章
模板（qs_guide/qs_article/qs_notice/qs_explain/zc_article 共用）。正文只含
稳定内容——deadline/close_type 等时效字段（归状态机 + expires_at 管）与
refreshtime（每轮必变）不进正文也不进 metadata，消除 hash 抖动；hash =
sha256(pipeline_version + 正文全文)。

**PII 出站扫描**（决议 8 修订版）：渲染后正文做手机号/身份证正则扫描；
职位模板「联系方式：」行内豁免（岗位联系电话经用户拍板可给用户）；命中即
拒绝渲染（PiiRejectedError），调用方记 item 错误。

分类路由：源表 → 目标分类 display_name 常量表（六分类，决议 6/9/10）。
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.knowledge.chunker import MAX_EMBEDDING_CHUNK_CHARS, TextChunker
from src.knowledge.parsers import ParsedChunk

# 渲染/分块管线版本（升级触发全量重判重嵌，与业务字段无关）
PIPELINE_VERSION = "lyjy-render-v1"

SOURCE_NAME_LYJY = "聊易就业平台"
SOURCE_NAME_SCCY = "水城创业网"

TITLE_MAX_CHARS = 120
COMPANY_PROFILE_MAX_CHARS = 200

# 分类路由：源表 → 租户知识库顶级分类 display_name（§7.5 映射总表，决议定版）
CATEGORY_ROUTES = {
    "qs_jobs": "在招职位",
    "qs_guide": "就业指导",
    "qs_article": "政策资讯",
    "qs_notice": "通知公告",
    "qs_explain": "办事指南",
    "zc_article": "创业政策",
}

# 分站映射：subsite_id → 地区名（生产映射到手后仅改本常量，设计 §7.8-5）。
# 测试库 qs_subsite 为空，先空表；文章无映射时兜底用资讯分类树根名（专区名，
# 见 build_article_context：仅根名以「专区」结尾时写地区行，如 彭水专区/潍坊专区）
SUBSITE_REGION_NAMES: Dict[str, str] = {}

# PII 出站正则（兜底拦截文章正文偶发的手机号/身份证号；岗位联系方式行豁免）
_PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b")
_IDCARD18_RE = re.compile(r"\b\d{17}[\dXx]\b")
_IDCARD15_RE = re.compile(r"\b\d{15}\b")

# 职位模板联系方式行前缀（扫描器按行放行）
CONTACT_LINE_PREFIX = "- 联系方式："


class PiiRejectedError(ValueError):
    """渲染正文 PII 扫描命中，拒绝出站。"""

    def __init__(self, kinds: List[str]):
        self.kinds = kinds
        super().__init__(f"渲染正文 PII 扫描命中（{','.join(sorted(set(kinds)))}），拒绝出站")


@dataclass
class RenderedDoc:
    """一次渲染的结果（jobs/articles 通用）。"""

    table: str
    native_id: str  # 纯源主键字符串
    external_id: str  # f"{prefix}:{id}"
    title: str
    content_md: str
    metadata: Dict[str, Any]
    content_hash: str
    category_display_name: str
    expires_at: Optional[Any] = None  # UTC naive datetime；无有效期 None


def compute_content_hash(content_md: str) -> str:
    """hash = sha256(pipeline_version + 正文全文)（宏陶 §5.1 同口径）。"""
    return hashlib.sha256(
        (PIPELINE_VERSION + "\n" + content_md).encode("utf-8")
    ).hexdigest()


def _s(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _flag_true(value: Any) -> bool:
    return _s(value) in ("1", "是")


def html_to_text(raw: str) -> str:
    """HTML 纯文本化：块级标签转段落换行，剥其余标签，反转义实体，压空行。

    源库实测（2026-11-17 冒烟）：客户库富文本为**双重 HTML 编码**存储
    （&lt;p&gt;、&amp;nbsp;）——先反转义一层再剥标签，剥完再反转义一次还原
    正文实体（&nbsp; 等）；两轮有界，不循环。
    """
    if not raw:
        return ""
    text = html_lib.unescape(raw)
    text = re.sub(r"(?i)<\s*br\s*/?>", "\n", text)
    text = re.sub(r"(?i)<\s*/\s*(p|div|li|h[1-6]|tr|blockquote)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*(p|div|li|h[1-6]|tr|blockquote)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines()]
    paragraphs: List[str] = []
    for line in lines:
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs).strip()


def _truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + "…"


# ------------------------------- PII 扫描 -------------------------------

def scan_pii(text: str) -> List[str]:
    """返回命中 PII 类型列表（空 = 通过）。只回类型不回内容，避免日志泄露。"""
    kinds: List[str] = []
    if _PHONE_RE.search(text):
        kinds.append("phone")
    if _IDCARD18_RE.search(text) or _IDCARD15_RE.search(text):
        kinds.append("idcard")
    return kinds


def assert_no_pii(content_md: str) -> None:
    """逐行扫描渲染正文；「联系方式：」行豁免（岗位联系电话决议 8 放行）。"""
    kinds: List[str] = []
    for line in content_md.splitlines():
        if line.strip().startswith(CONTACT_LINE_PREFIX):
            continue
        kinds.extend(scan_pii(line))
    if kinds:
        raise PiiRejectedError(kinds)


# ------------------------------- 职位模板 -------------------------------

def _wage_line(item: Dict[str, Any]) -> str:
    """negotiable=1 写「面议」；否则 minwage-maxwage 元/月（缺一边按另一边）。"""
    if _flag_true(item.get("negotiable")):
        return "面议"
    minwage, maxwage = _int(item.get("minwage")), _int(item.get("maxwage"))
    if minwage <= 0 and maxwage <= 0:
        return "面议"
    if maxwage <= 0:
        return f"{minwage} 元/月以上"
    if minwage <= 0:
        return f"{maxwage} 元/月以下"
    return f"{minwage}-{maxwage} 元/月"


def _subclass_cn(item: Dict[str, Any], category_jobs: Dict[str, dict]) -> str:
    """subclass ID → 分类名（qs_category_jobs 映射；行内 category_cn 已含路径）。"""
    subclass_id = _s(item.get("subclass"))
    if not subclass_id or subclass_id == "0":
        return ""
    row = category_jobs.get(subclass_id) or {}
    return _s(row.get("categoryname"))


def _district_cn(item: Dict[str, Any], category_district: Dict[str, dict]) -> str:
    """工作地区：行内 district_cn 优先，空则按 district ID 从地区字典解析。"""
    district_cn = _s(item.get("district_cn"))
    if district_cn:
        return district_cn
    district_ids = _s(item.get("district"))
    if district_ids:
        names = [
            _s((category_district.get(pid.strip()) or {}).get("categoryname"))
            for pid in district_ids.split(",")
        ]
        names = [n for n in names if n]
        if names:
            return "".join(names)
    return ""


def render_job(
    item: Dict[str, Any],
    contact: Optional[dict],
    company_profile: Optional[dict],
    dict_cache: Dict[str, dict],
) -> RenderedDoc:
    """渲染一个岗位（§7.5 职位模板）：字段区 + 企业信息 + 分类/来源尾行。

    - item：fetcher 白名单列行；contact：qs_jobs_contact 行（可空）；
      company_profile：qs_company_profile 行（可空）；
      dict_cache：load_dictionary_cache 产物（qs_category_jobs/qs_category_district）。
    """
    native_id = _s(item.get("id"))
    jobs_name = _s(item.get("jobs_name")) or f"岗位{native_id}"
    companyname = _s(item.get("companyname"))
    category_cn = _s(item.get("category_cn"))
    subclass_cn = _subclass_cn(item, dict_cache.get("qs_category_jobs", {}))
    district_cn = _district_cn(item, dict_cache.get("qs_category_district", {}))

    lines: List[str] = [f"# {jobs_name}（{companyname}）", "", "## 职位信息", ""]
    # category_cn 已含小类时（源数据常见同级重名）不重复拼接 subclass 名
    if subclass_cn and subclass_cn != category_cn.split("/")[-1].strip():
        category_display = " / ".join(x for x in (category_cn, subclass_cn) if x)
    else:
        category_display = category_cn
    if category_display:
        lines.append(f"- 职位类别：{category_display}")
    if district_cn:
        lines.append(f"- 工作地区：{district_cn}")
    lines.append(f"- 薪资：{_wage_line(item)}")
    edu_exp = " / ".join(
        x for x in (_s(item.get("education_cn")), _s(item.get("experience_cn"))) if x
    )
    if edu_exp:
        lines.append(f"- 学历/经验要求：{edu_exp}")
    if _int(item.get("amount")) > 0:
        lines.append(f"- 招聘人数：{_int(item.get('amount'))}")
    if _s(item.get("tag_cn")):
        lines.append(f"- 福利待遇：{_s(item.get('tag_cn'))}")
    duties = html_to_text(_s(item.get("contents")))
    if duties:
        lines.append(f"- 岗位职责：{duties}")
    other = "、".join(
        x for x in (
            _s(item.get("sex_cn")), _s(item.get("age")), _s(item.get("language")),
            _s(item.get("jobAsk")), _s(item.get("cert")), _s(item.get("scale_cn")),
        ) if x
    )
    if other:
        lines.append(f"- 其他要求：{other}")
    special = []
    if _flag_true(item.get("focus_group")):
        special.append("优先招用重点群体")
    if _flag_true(item.get("is_ex_serviceman")):
        special.append("退役军人")
    if _flag_true(item.get("is_disabled")):
        special.append("残疾人")
    if special:
        lines.append(f"- 特殊群体：{' / '.join(special)}")
    contact_parts = []
    if contact:
        # 尊重源站展示位：雇主隐藏（*_show=0）的联系方式不同步给用户
        if _flag_true(contact.get("contact_show")) and _s(contact.get("contact")):
            contact_parts.append(_s(contact.get("contact")))
        tel = ""
        if _flag_true(contact.get("telephone_show")) and _s(contact.get("telephone")):
            tel = _s(contact.get("telephone"))
        elif (_flag_true(contact.get("landline_tel_show"))
              and _s(contact.get("landline_tel"))):
            tel = _s(contact.get("landline_tel"))
        if tel:
            contact_parts.append(tel)
    if contact_parts:
        lines.append(f"- 联系方式：{'，'.join(contact_parts)}")
    lines.append("")

    profile = ""
    if company_profile:
        profile = _truncate(
            html_to_text(_s(company_profile.get("contents"))),
            COMPANY_PROFILE_MAX_CHARS,
        )
    if profile:
        lines += ["## 企业信息", "", profile, ""]

    lines += ["---", "", f"分类：{category_cn}；数据来源：{SOURCE_NAME_LYJY}。", ""]
    content_md = "\n".join(lines)
    assert_no_pii(content_md)

    metadata = {
        "raw_payload": {
            "source_table": "qs_jobs",
            "native_id": native_id,
            "company_id": _s(item.get("company_id")),
            "category_cn": category_cn,
            "subclass": _s(item.get("subclass")),
            "district_cn": district_cn,
            "wage_min": _int(item.get("minwage")),
            "wage_max": _int(item.get("maxwage")),
            "negotiable": 1 if _flag_true(item.get("negotiable")) else 0,
            "amount": _int(item.get("amount")),
            "addtime": _int(item.get("addtime")),
            "deadline": _int(item.get("deadline")),
            "close_type": item.get("close_type"),
            "subsite_id": "",
        },
    }
    content_hash = compute_content_hash(content_md)
    metadata["trace"] = {
        "source": "lyjy_employment",
        "native_id": native_id,
        "content_hash": content_hash,
    }
    return RenderedDoc(
        table="qs_jobs",
        native_id=native_id,
        external_id=f"job:{native_id}",
        title=jobs_name[:TITLE_MAX_CHARS],
        content_md=content_md,
        metadata=metadata,
        content_hash=content_hash,
        category_display_name=CATEGORY_ROUTES["qs_jobs"],
        expires_at=deadline_to_expires_at(item.get("deadline")),
    )


def deadline_to_expires_at(deadline: Any) -> Optional[Any]:
    """岗位 deadline（Unix 秒）→ UTC naive datetime；0/空/非法返回 None（长期）。"""
    seconds = _int(deadline)
    if seconds <= 0:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)


_deadline_to_expires_at = deadline_to_expires_at  # 兼容别名（模块内历史引用）


# ------------------------------- 文章模板 -------------------------------

def _category_path(
    type_id: Any, parent_of: Dict[str, str], name_of: Dict[str, str]
) -> str:
    """沿 parentid 上溯拼分类路径（"根 / 子"，防环：最多上溯 10 层）。"""
    node = _s(type_id)
    names: List[str] = []
    seen = set()
    while node and node != "0" and node not in seen and len(seen) <= 10:
        seen.add(node)
        name = name_of.get(node, "")
        if not name:
            break
        names.append(name)
        node = parent_of.get(node, "")
    return " / ".join(reversed(names))


def build_article_context(
    table: str, item: Dict[str, Any], dict_cache: Dict[str, dict]
) -> Any:
    """文章渲染上下文：分类路径 + 地区（分站映射优先，资讯表兜底分类树专区名）。

    返回 (category_path, region)：
    - qs_guide：qs_guide_category 单级名；qs_article：qs_article_category 树路径；
      qs_notice/qs_explain：无子分类（空）；
    - 地区：SUBSITE_REGION_NAMES[subsite_id] 优先；qs_article 无映射时兜底分类
      树根名（仅根名以「专区」结尾时——彭水专区/潍坊专区等，避免把「资讯中心」
      这类普通根目当地区）；其余表 subsite_id>0 且无映射时不写地区行。
    """
    subsite_id = _s(item.get("subsite_id"))
    region = SUBSITE_REGION_NAMES.get(subsite_id, "")
    if table == "qs_guide":
        name_of = {
            str(r["id"]): _s(r.get("categoryname"))
            for r in dict_cache.get("qs_guide_category", {}).values()
        }
        return name_of.get(_s(item.get("type_id")), ""), region
    if table == "qs_article":
        rows = dict_cache.get("qs_article_category", {})
        parent_of = {str(r["id"]): str(r.get("parentid") or "0") for r in rows.values()}
        name_of = {str(r["id"]): _s(r.get("categoryname")) for r in rows.values()}
        path = _category_path(item.get("type_id"), parent_of, name_of)
        if not region and subsite_id and subsite_id != "0":
            root = path.split(" / ")[0] if path else ""
            region = root if root.endswith("专区") else ""
        return path, region
    return "", region


def render_article(
    table: str,
    item: Dict[str, Any],
    category_path: str,
    region: str,
    source_name: str = SOURCE_NAME_LYJY,
    category_display_name: Optional[str] = None,
    id_column: str = "id",
) -> RenderedDoc:
    """渲染一篇文章（qs_guide/qs_article/qs_notice/qs_explain/zc_article 共用）。

    - category_path/region：由调用方按源表分类结构预拼（lyjy 用
      build_article_context；zc_article 由 sccy fetcher 提供 cate 路径）；
    - zc_article 传 id_column='article_id'、source_name=水城创业网。
    """
    if category_display_name is None:
        category_display_name = CATEGORY_ROUTES[table]

    native_id = _s(item.get(id_column))
    title = _s(item.get("title")) or f"文章{native_id}"
    body = html_to_text(_s(item.get("content") if table != "zc_article"
                           else item.get("details")))
    if not body and table == "zc_article":
        body = html_to_text(_s(item.get("desc")))

    lines: List[str] = [f"# {title}", ""]
    if category_path:
        lines += [f"分类：{category_path}", ""]
    if region:
        lines += [f"地区：{region}", ""]
    source = _s(item.get("author")) or _s(item.get("source"))
    if source:
        lines += [f"来源：{source}", ""]
    if body:
        lines += [body, ""]
    lines += ["---", "", f"数据来源：{source_name}。", ""]
    content_md = "\n".join(lines)
    assert_no_pii(content_md)

    metadata = {
        "raw_payload": {
            "source_table": table,
            "native_id": native_id,
            "type_id": _s(item.get("type_id") if table != "zc_article"
                          else item.get("cate_id")),
            "category_path": category_path,
            "region": region,
            "addtime": _int(item.get("addtime") if table != "zc_article"
                            else item.get("dateline")),
            "subsite_id": _s(item.get("subsite_id")) if table != "zc_article" else "",
        },
    }
    content_hash = compute_content_hash(content_md)
    metadata["trace"] = {
        "source": "lyjy_employment" if table != "zc_article" else "sccy_policy",
        "native_id": native_id,
        "content_hash": content_hash,
    }
    return RenderedDoc(
        table=table,
        native_id=native_id,
        external_id=f"{table_prefix(table)}:{native_id}",
        title=title[:TITLE_MAX_CHARS],
        content_md=content_md,
        metadata=metadata,
        content_hash=content_hash,
        category_display_name=category_display_name,
        expires_at=None,
    )


def table_prefix(table: str) -> str:
    """源表 → external_id 前缀（lyjy 五表 + sccy zc_article）。"""
    from src.tenant_custom.lyjy_employment.fetcher import TABLE_PREFIXES

    if table == "zc_article":
        return "zcarticle"
    return TABLE_PREFIXES[table]


def build_chunks(content_md: str) -> List[ParsedChunk]:
    """一份文档 = 一个检索单元：单 chunk 旁路；超护栏回退 TextChunker（宏陶同款）。"""
    if len(content_md) <= MAX_EMBEDDING_CHUNK_CHARS:
        return [ParsedChunk(text=content_md, metadata={"granularity": "whole"})]
    chunker = TextChunker(chunk_size=512, overlap=64)
    return [
        ParsedChunk(text=c["text"], metadata={"granularity": "overflow_chunked"})
        for c in chunker.chunk(content_md)
    ]
