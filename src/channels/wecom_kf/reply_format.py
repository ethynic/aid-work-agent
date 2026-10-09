"""微信客服完整 Markdown 与纯文本前缀投影，不调用模型或修改发送阈值。"""

import html
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

import markdown


REPLY_FILE_NAME = "详细答复.md"
FILE_NOTICE = f"\n\n完整回复请查看发送的文件《{REPLY_FILE_NAME}》。"

_FENCE = re.compile(r"^ {0,3}(?:> ?)*(?:(?:[-*+] |\d+[.)] ))?(`{3,}|~{3,})(.*)$")
_DELIMITER = re.compile(r":?-{3,}:?$")
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


@dataclass
class _Element:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


class _DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Element(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        # GFM removes the table escape even in code spans; Python Markdown's
        # tables extension leaves it in code text. Match the file's GFM meaning.
        if any(node.tag == "table" for node in self.stack) and self.stack[-1].tag == "code":
            data = data.replace("\\|", "|")
        self.stack[-1].children.append(data)


def _fenced_html(text: str) -> str:
    """Python Markdown 的围栏扩展不识别三空格缩进，补齐这项基础语法。

    仅渲染投影替换为转义的 pre；完整文件仍保留原 Markdown 围栏。
    """
    output, code = [], []
    marker, size = "", 0
    for line in text.splitlines(keepends=True):
        match = _FENCE.match(line.rstrip("\r\n"))
        if marker:
            if match and match[1][0] == marker and len(match[1]) >= size and not match[2].strip():
                output.append("\n<pre><code>" + html.escape("".join(code)) + "</code></pre>\n\n")
                marker, code = "", []
            else:
                code.append(line)
        elif match and not (match[1][0] == "`" and "`" in match[2]):
            marker, size = match[1][0], len(match[1])
        else:
            output.append(line)
    if marker:
        output.append("\n<pre><code>" + html.escape("".join(code)) + "</code></pre>\n")
    return "".join(output)


def _document(text: str) -> _Element:
    parser = _DocumentParser()
    parser.feed(markdown.markdown(_fenced_html(text), extensions=["tables", "fenced_code", "sane_lists"]))
    parser.close()
    return parser.root


def _elements(node: _Element, tag: str):
    if node.tag in {"pre", "code", "script", "style"}:
        return
    if node.tag == tag:
        yield node
    for child in node.children:
        if isinstance(child, _Element):
            yield from _elements(child, tag)


def _text(node: _Element) -> str:
    return "".join(child if isinstance(child, str) else _text(child) for child in node.children)


def _split_row(line: str) -> list[str]:
    """保留转义竖线及代码单元格；代码内竖线随后规范化为 GFM 转义。"""
    line = line.strip()
    cells, cell = [], []
    index, code_ticks = 0, 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            cell.extend(line[index:index + 2])
            index += 2
            continue
        if char == "`":
            end = index + 1
            while end < len(line) and line[end] == "`":
                end += 1
            run = end - index
            if not code_ticks:
                code_ticks = run
            elif run == code_ticks:
                code_ticks = 0
            cell.extend(line[index:end])
            index = end
            continue
        if char == "|" and not code_ticks:
            cells.append("".join(cell).strip())
            cell = []
        else:
            cell.append(char)
        index += 1
    cells.append("".join(cell).strip())
    if line.startswith("|"):
        cells.pop(0)
    if line.endswith("|") and not line.endswith("\\|"):
        cells.pop()
    return cells


def _escape_code_pipes(cell: str) -> str:
    # Unescaped pipes inside code spans are not valid GFM cell separators.
    return re.sub(r"(`+)(.*?)(\1)", lambda match: match[1] + re.sub(r"(?<!\\)\|", r"\\|", match[2]) + match[3], cell)


def normalize_reply_markdown(text: str) -> str:
    """保留完整正文，仅修复明确的表格边界；无法无损交付则抛 ValueError。

    普通合法正文逐字保留，不添加标题、不转成纯文本或整体代码块。
    """
    if not text:
        return ""
    text.encode("utf-8")
    lines = text.splitlines()
    # Multiline inline-code spans may themselves contain table examples.
    visible = re.sub(r"(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)", lambda match: re.sub(r"[^\r\n]", " ", match[0]), text, flags=re.S).splitlines()
    output, tables = [], []
    marker, size, index, raw_code = "", 0, 0, False
    while index < len(lines):
        line = lines[index]
        fence = _FENCE.match(line)
        if marker:
            output.append(line)
            if fence and fence[1][0] == marker and len(fence[1]) >= size and not fence[2].strip():
                marker = ""
            index += 1
            continue
        if re.match(r"^ {0,3}<(?:pre|code)(?:\s|>)", line, re.I):
            raw_code = True
        if raw_code:
            output.append(line)
            if re.search(r"</(?:pre|code)\s*>", line, re.I):
                raw_code = False
            index += 1
            continue
        if fence:
            marker, size = fence[1][0], len(fence[1])
        if fence or line.startswith(("    ", "\t")) or index + 1 >= len(lines):
            output.append(line)
            index += 1
            continue
        delimiters = _split_row(lines[index + 1])
        if "|" not in visible[index] or not delimiters or not all(_DELIMITER.fullmatch(cell) for cell in delimiters):
            output.append(line)
            index += 1
            continue
        headers = _split_row(line)
        if len(headers) != len(delimiters):
            raise ValueError("Markdown 表格表头与分隔行列数不一致")
        rows = [headers, delimiters]
        end = index + 2
        while end < len(lines) and lines[end].strip() and "|" in lines[end] and not _FENCE.match(lines[end]):
            cells = _split_row(lines[end])
            if len(cells) > len(headers):
                raise ValueError("Markdown 表格数据列超过表头，不能无损确定列含义")
            rows.append(cells)
            end += 1
        block = lines[index:end]
        repaired = [[_escape_code_pipes(cell) for cell in row] for row in rows]
        if repaired != rows:
            block = ["| " + " | ".join(row) + " |" for row in repaired]
        if output and output[-1].strip():
            output.append("")
        output.extend(block)
        tables.append(block)
        index = end
    normalized = "\n".join(output)
    if text.endswith("\n"):
        normalized += "\n"
    # Do not normalize line endings or whitespace when no repair was needed.
    if normalized == text.replace("\r\n", "\n"):
        normalized = text
    rendered_tables = list(_elements(_document(normalized), "table"))
    if len(rendered_tables) < len(tables):
        raise ValueError("Markdown 表格未渲染为完整表格")
    for block in tables:
        rendered = list(_elements(_document("\n".join(block)), "table"))
        if len(rendered) != 1:
            raise ValueError("Markdown 表格结构校验失败")
        expected = [_split_row(row) for row in block]
        actual_rows = list(_elements(rendered[0], "tr"))
        if len(actual_rows) != len(expected) - 1:
            raise ValueError("Markdown 表格行数校验失败")
        for source, actual in zip([expected[0], *expected[2:]], actual_rows):
            actual_cells = [child for child in actual.children if isinstance(child, _Element) and child.tag in {"th", "td"}]
            source += [""] * (len(expected[0]) - len(source))
            if len(actual_cells) != len(source):
                raise ValueError("Markdown 表格列数校验失败")
            if [_text(cell) for cell in actual_cells] != [_text(_document(cell.replace("\\|", "|"))).strip() for cell in source]:
                raise ValueError("Markdown 表格单元格内容校验失败")
    return normalized


def inspect_reply_markdown(text: str) -> tuple[bool, bool]:
    """判定渲染出的真实表格/正文图片，代码示例不计入。"""
    malformed_table = False
    try:
        normalized = normalize_reply_markdown(text or "")
    except ValueError:
        # Still require file preparation, where the caller handles the error;
        # never silently send an invalid short table as ordinary text.
        normalized, malformed_table = text or "", True
    document = _document(normalized)
    return malformed_table or bool(list(_elements(document, "table"))), bool(list(_elements(document, "img")))


def _mask_code(text: str) -> str:
    """保留源偏移，仅屏蔽图片重写时不能触碰的代码示例。"""
    masked, marker, size, list_indents = [], "", 0, []
    for line in text.splitlines(keepends=True):
        indent = len(line) - len(line.lstrip(" "))
        if line.strip():
            while list_indents and indent < list_indents[-1]:
                list_indents.pop()
        item = re.match(r"^ *([-*+]|\d+[.)]) +(.*)$", line)
        if item:
            list_indents.append(item.start(2))
        content_indent = list_indents[-1] if list_indents else 0
        content = line[content_indent:] if indent >= content_indent else line
        fence = _FENCE.match(content.rstrip("\r\n"))
        is_code = bool(marker or fence or indent >= content_indent + 4 or line.startswith("\t"))
        if marker and fence and fence[1][0] == marker and len(fence[1]) >= size and not fence[2].strip():
            marker = ""
        elif not marker and fence:
            marker, size = fence[1][0], len(fence[1])
        masked.append(re.sub(r"[^\r\n]", " ", line) if is_code else line)
    visible = "".join(masked)
    for pattern in [r"<(pre|code)\b[^>]*>.*?</\1\s*>", r"(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)"]:
        visible = re.sub(pattern, lambda match: re.sub(r"[^\r\n]", " ", match[0]), visible, flags=re.S | re.I)
    return visible


def _closing(text: str, start: int, opening: str, closing: str) -> int:
    depth, index = 1, start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if not depth:
                return index
        index += 1
    return -1


def _safe_image_url(src: str, resolver, *, allow_file_ids=False) -> str | None:
    resolved = resolver(html.unescape(re.sub(r"\\([\\`*{}\[\]()#+\-.!_>])", r"\1", src)))
    if not isinstance(resolved, str) or re.search(r"[\s<>]", resolved):
        return None
    if allow_file_ids and re.fullmatch(r"file_id:[a-zA-Z0-9_]+", resolved):
        return resolved
    try:
        parts = urlsplit(resolved)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
        return None
    return resolved


def map_reply_image_sources(text: str, resolver, *, allow_file_ids=False) -> str:
    """只将正文图片映射到已授权 HTTP(S) 地址，不读文件、不抓取网络。

    无法映射则保留 alt 与不可显示说明；引用定义同步去除不安全目标。
    """
    visible = _mask_code(text)
    changes, references, used_references = [], {}, {}
    reference_pattern = r"^ {0,3}\[([^]\n]+)\]:[ \t]*(<[^>\n]*>|\S+)([^\n]*)$"
    for match in re.finditer(reference_pattern, visible, re.M):
        label = " ".join(match[1].split()).lower()
        references[label] = match
    covered_until = 0
    for match in re.finditer(r"!\[", visible):
        start = match.start()
        if start < covered_until:
            continue
        escaped = len(text[:start]) - len(text[:start].rstrip("\\"))
        if escaped % 2:
            continue
        alt_end = _closing(visible, start + 1, "[", "]")
        if alt_end < 0:
            continue
        alt = text[start + 2:alt_end]
        end, src, title = alt_end + 1, None, ""
        if end < len(text) and visible[end] == "(":
            close = _closing(visible, end, "(", ")")
            if close < 0:
                continue
            inner = text[end + 1:close].strip()
            if inner.startswith("<") and ">" in inner:
                stop = inner.index(">") + 1
                src, title = inner[1:stop - 1], inner[stop:]
            else:
                target = re.match(r"\S*", inner)[0]
                src, title = target, inner[len(target):]
            end = close + 1
        else:
            label = alt
            if end < len(text) and visible[end] == "[":
                close = _closing(visible, end, "[", "]")
                if close < 0:
                    continue
                label, end = text[end + 1:close] or alt, close + 1
            key = " ".join(label.split()).lower()
            reference = references.get(key)
            if reference:
                src, title = reference[2].strip("<>"), reference[3]
                used_references[key] = reference
        if src is None:
            continue
        if title.strip() and not re.fullmatch(r'''(?:"[^"\n]*"|'[^'\n]*'|\([^\n]*\))''', title.strip()):
            continue
        url = _safe_image_url(src, resolver, allow_file_ids=allow_file_ids)
        replacement = f"![{alt}](<{url}>{title})" if url else f"{reply_plain_text(alt)}（图片暂无法在文件中显示）"
        changes.append((start, end, replacement))
        covered_until = end
    for reference in used_references.values():
        url = _safe_image_url(reference[2].strip("<>"), resolver, allow_file_ids=allow_file_ids)
        if url:
            changes.append((reference.start(2), reference.end(2), f"<{url}>"))
        else:
            changes.append((reference.start(), reference.end(), ""))
    for match in re.finditer(r'''<img\b(?:[^>"']|"[^"]*"|'[^']*')*>''', visible, re.I):
        if any(start <= match.start() < end for start, end, _ in changes):
            continue
        parser = _DocumentParser()
        parser.feed(text[match.start():match.end()])
        images = list(_elements(parser.root, "img"))
        if not images:
            continue
        image = images[0]
        url = _safe_image_url(image.attrs.get("src", ""), resolver, allow_file_ids=allow_file_ids)
        alt = image.attrs.get("alt", "") or ""
        replacement = f'<img src="{html.escape(url, quote=True)}" alt="{html.escape(alt, quote=True)}">' if url else f"{alt}（图片暂无法在文件中显示）"
        changes.append((match.start(), match.end(), replacement))
    for start, end, replacement in sorted(changes, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def _plain(node: _Element) -> str:
    if node.tag in {"script", "style"}:
        return ""
    if node.tag == "img":
        return node.attrs.get("alt", "")
    if node.tag in {"pre", "code"}:
        return ("\n\n" if node.tag == "pre" else "") + _text(node) + ("\n\n" if node.tag == "pre" else "")
    if node.tag == "br":
        return "\n"
    if node.tag == "table":
        rows = [[_plain(cell).strip() for cell in row.children if isinstance(cell, _Element) and cell.tag in {"th", "td"}] for row in _elements(node, "tr")]
        rows = [row for row in rows if row]
        if not rows:
            return ""
        headers = rows[0]
        values = rows[1:]
        caption = "".join(_plain(child) for child in node.children if isinstance(child, _Element) and child.tag == "caption").strip()
        if not values:
            projected = "；".join(headers)
        else:
            projected = "\n".join("- " + "；".join(f"{headers[index]}：{value}" if index < len(headers) and headers[index] else value for index, value in enumerate(row)) for row in values)
        return "\n\n" + (caption + "\n" if caption else "") + projected + "\n\n"
    if node.tag in {"ul", "ol"}:
        try:
            start = int(node.attrs.get("start", 1))
        except (ValueError, TypeError):
            start = 1
        items = []
        for child in node.children:
            if isinstance(child, _Element) and child.tag == "li":
                prefix = f"{start}. " if node.tag == "ol" else "- "
                items.append(prefix + _plain(child).strip())
                start += 1
        return "\n\n" + "\n".join(items) + "\n\n"
    content = "".join(child if isinstance(child, str) else _plain(child) for child in node.children)
    if node.tag == "a":
        href = node.attrs.get("href", "")
        if href and content != href:
            content += f"（{href}）"
    if node.tag in {"p", "div", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "hr"}:
        return "\n\n" + content.strip() + "\n\n"
    return content


def reply_plain_text(text: str) -> str:
    """按源顺序投影段落/列表/代码，表格展开为字段和值。"""
    plain = _plain(_document(text or ""))
    plain = re.sub(r"[ \t]+\n", "\n", plain)
    return re.sub(r"\n{3,}", "\n\n", plain).strip()


def _safe_cut(text: str, budget: int) -> str:
    end = min(len(text), budget)
    # Do not leave a trailing base character detached from following marks,
    # variation selectors or a zero-width-joiner emoji sequence.
    while end > 0 and end < len(text) and (unicodedata.category(text[end]).startswith("M") or text[end] in {"\ufe0e", "\ufe0f", "\u200d"} or text[end - 1] == "\u200d"):
        end -= 1
    return text[:end].rstrip()


def build_prefix_preview(text: str, max_chars: int = 500, max_bytes: int = 2048) -> str:
    """原文前缀 + 实际截断省略号 + 文件提示，总字符数含全部附加文字。"""
    if max_chars < len(FILE_NOTICE.strip()):
        raise ValueError("字符预算不足以容纳完整文件提示")
    try:
        plain = reply_plain_text(text)
    except Exception:
        return FILE_NOTICE.strip()
    if not plain:
        return FILE_NOTICE.strip()
    # Character count controls readability; the channel's configured byte
    # ceiling independently controls whether this single message can be sent.
    notice_bytes = len(FILE_NOTICE.encode("utf-8"))
    if len(FILE_NOTICE.strip().encode("utf-8")) > max_bytes:
        raise ValueError("字节预算不足以容纳完整文件提示")
    byte_budget = max_bytes - notice_bytes - len("…".encode("utf-8"))
    byte_end = 0
    used = 0
    for char in plain:
        used += len(char.encode("utf-8"))
        if used > byte_budget:
            break
        byte_end += 1
    if len(plain) + len(FILE_NOTICE) <= max_chars and len((plain + FILE_NOTICE).encode("utf-8")) <= max_bytes:
        return plain + FILE_NOTICE
    budget = min(max_chars - len(FILE_NOTICE) - 1, byte_end)
    if budget <= 0:
        return FILE_NOTICE.strip()
    prefix = _safe_cut(plain, budget)
    boundaries = [match.end() for match in re.finditer(r"[。！？!?](?:[’”\"']?)(?=\s|$|[^。！？!?])|\n\n|\n(?=[-•] |\d+[.)] )|\.(?=\s|$)", prefix)]
    # A period in an URL is never a sentence boundary, including a terminal dot.
    url_ranges = [match.span() for match in re.finditer(r"https?://\S+", prefix)]
    boundaries = [end for end in boundaries if not any(start < end <= stop for start, stop in url_ranges)]
    if boundaries:
        prefix = prefix[:boundaries[-1]].rstrip()
    if not prefix:
        return FILE_NOTICE.strip()
    return prefix + "…" + FILE_NOTICE
