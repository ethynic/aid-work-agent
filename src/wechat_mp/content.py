"""mp 文章页正文提取（WP3，设计 §6.1，P1=URL 单篇粒度）。

输入为 fetcher 定性为 ok 的文章页 HTML，输出保序结构化序列：
    [{type:'text', text:...}, {type:'image', src:...}, ...]
- 只解析 js_content 容器；去 script/style 与明确非正文节点，不按主观规则删促销正文
- 图片节点取 data-src（WP0 实测 CDN 为 mmecoa.qpic.cn），记原 URL 占位，下载转存属 P2
- 文本经 sanitize_text 清洗孤立代理字符（WP0 实测微信内容可能含，写库会编码失败）
- 页面元信息：标题（#activity-name → var msg_title → og:title）、
  发布时间（var ct unix 秒 → var createTime UTC+8 字符串）、账号名（#js_name）、
  别名链接（identity.extract_alias_url，供 WP5 别名收敛）
"""
from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from bs4 import BeautifulSoup, NavigableString, Tag

from src.core.text_sanitizer import sanitize_text
from src.wechat_mp.identity import URLIdentity, extract_alias_url

# 块级标签：作为文本段落边界（进入/离开都截断当前文本缓冲）
_BLOCK_TAGS = {
    "p", "section", "div", "li", "tr", "table", "ul", "ol",
    "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "h7",
    "article", "header", "footer", "figure", "figcaption",
}
# js_content 内的明确非正文节点
# 已知限制（CR 登记）：iframe 丢弃意味着视频节点（video_iframe）无占位、静默丢失；
# P1 纯文本+图片粒度可接受，视频占位/转述留待后续版本
_SKIP_TAGS = {"script", "style", "noscript", "iframe", "link", "meta", "button", "input", "select", "textarea"}
# 明确非正文容器（class/id 命中即整棵剔除）；注意 js_content 内一般很干净，此处只收确定的
_NON_CONTENT_SELECTORS = ("mp-common-profile",)  # 公众号名片卡片

_MSG_TITLE_RE = re.compile(r"""var\s+msg_title\s*=\s*'(.*?)'\.html\(false\)""", re.DOTALL)
_CT_RE = re.compile(r"""var\s+ct\s*=\s*"(\d{9,11})\"""")
_CREATE_TIME_RE = re.compile(r"""var\s+createTime\s*=\s*'(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})'""")
_OG_TITLE_RE = re.compile(
    r"""<meta\b[^>]*property=["']og:title["'][^>]*content=["']([^"']+)["']""", re.IGNORECASE
)
_CN_TZ = timezone(timedelta(hours=8))


class ContentExtractionError(ValueError):
    """正文提取失败（js_content 缺失或内容为空）；页面定性问题请走 fetcher.classify_page"""


@dataclass
class ContentNode:
    type: str  # 'text' | 'image'
    text: Optional[str] = None
    src: Optional[str] = None


@dataclass
class ExtractedArticle:
    title: Optional[str]
    account_name: Optional[str]
    publish_time: Optional[datetime]  # UTC
    nodes: List[ContentNode] = field(default_factory=list)
    alias: Optional[URLIdentity] = None

    @property
    def text(self) -> str:
        return nodes_to_text(self.nodes)

    @property
    def image_count(self) -> int:
        return sum(1 for n in self.nodes if n.type == "image")


def nodes_to_text(nodes: List[ContentNode], image_placeholder: str = "[图片{n}]") -> str:
    """序列折成纯文本：图片按序记 [图片N] 占位（图片描述 P2 插回同位置）。"""
    parts: List[str] = []
    img_idx = 0
    for node in nodes:
        if node.type == "image":
            img_idx += 1
            parts.append(image_placeholder.format(n=img_idx))
        elif node.text:
            parts.append(node.text)
    return "\n".join(parts)


def _flush_text(buf: List[str], nodes: List[ContentNode]) -> None:
    text = "".join(buf).replace("\xa0", " ").strip()
    buf.clear()
    if not text:
        return
    text = sanitize_text(text)
    if nodes and nodes[-1].type == "text":
        # 同段落相邻文本合并（块级边界已截断，这里合并的是未被块级标签分开的片段）
        nodes[-1].text = f"{nodes[-1].text}\n{text}"
    else:
        nodes.append(ContentNode(type="text", text=text))


def _walk(node: Tag, buf: List[str], nodes: List[ContentNode]) -> None:
    for child in node.children:
        if isinstance(child, NavigableString):
            buf.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = (child.name or "").lower()
        if name in _SKIP_TAGS:
            continue
        classes = child.get("class") or []
        if any(c in _NON_CONTENT_SELECTORS for c in classes):
            continue
        if name == "img":
            src = child.get("data-src") or child.get("src") or ""
            src = src.strip()
            if src.startswith("http://") or src.startswith("https://"):
                _flush_text(buf, nodes)
                nodes.append(ContentNode(type="image", src=src))
            continue
        if name == "br":
            buf.append("\n")
            continue
        if name in _BLOCK_TAGS:
            _flush_text(buf, nodes)
            _walk(child, buf, nodes)
            _flush_text(buf, nodes)
        else:
            _walk(child, buf, nodes)


def _extract_title(soup: BeautifulSoup, html: str) -> Optional[str]:
    el = soup.select_one("#activity-name")
    if el:
        text = el.get_text(strip=True)
        if text:
            return sanitize_text(text)
    m = _MSG_TITLE_RE.search(html)
    if m:
        text = html_module.unescape(m.group(1)).strip()
        if text:
            return sanitize_text(text)
    m = _OG_TITLE_RE.search(html)
    if m:
        return sanitize_text(html_module.unescape(m.group(1)).strip())
    if soup.title and soup.title.string:
        return sanitize_text(soup.title.string.strip())
    return None


def _extract_publish_time(html: str) -> Optional[datetime]:
    m = _CT_RE.search(html)
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            pass
    m = _CREATE_TIME_RE.search(html)
    if m:
        try:
            local = datetime.strptime(m.group(1).replace("T", " "), "%Y-%m-%d %H:%M")
            return local.replace(tzinfo=_CN_TZ).astimezone(timezone.utc)
        except ValueError:
            pass
    return None


def extract_article(html: str) -> ExtractedArticle:
    """从文章页 HTML 提取结构化正文。js_content 缺失/内容为空抛 ContentExtractionError。"""
    if not html:
        raise ContentExtractionError("HTML 为空")
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#js_content")
    if container is None:
        raise ContentExtractionError("js_content 容器缺失（页面定性应走 fetcher.classify_page）")

    nodes: List[ContentNode] = []
    buf: List[str] = []
    _walk(container, buf, nodes)
    _flush_text(buf, nodes)  # 顶层尾部文本（如 <p>甲</p>尾部 的"尾部"）不能丢，否则纯文本误报空
    if not nodes:
        raise ContentExtractionError("js_content 内容为空")

    account_el = soup.select_one("#js_name")
    account_name = account_el.get_text(strip=True) if account_el else None

    return ExtractedArticle(
        title=_extract_title(soup, html),
        account_name=sanitize_text(account_name) if account_name else None,
        publish_time=_extract_publish_time(html),
        nodes=nodes,
        alias=extract_alias_url(html),
    )
