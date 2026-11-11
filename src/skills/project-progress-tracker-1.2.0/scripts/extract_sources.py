#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多格式项目资料 -> 统一证据包 data/extracted.json

零必装依赖：Office/PDF 用标准库 zip+XML 兜底，装了第三方库则自动增强。
原始资料只读，绝不改写。
"""
from __future__ import annotations

import argparse
import datetime as dt
import email
import html.parser
import io
import json
import re
import sys
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".eml", ".html", ".htm", ".xml", ".yaml", ".yml"}
OFFICE_EXT = {".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
SKIP_NAMES = {".ds_store", "thumbs.db", "desktop.ini", ".gitkeep"}

COMM_KW = ["沟通", "聊天", "会议", "纪要", "记录", "chat", "微信", "邮件", "往来", "群消息", "对话", "访谈", "沟通记录"]
ACCEPT_KW = ["验收", "签收", "确认单", "交付单", "上线确认"]
MILE_KW = ["里程碑", "排期", "台账", "甘特", "计划表", "wbs"]
REPORT_KW = ["周报", "月报", "日报", "进度", "汇报", "阶段", "简报", "status", "report", "小结", "总结"]

DATE_RES = [
    re.compile(r"(20\d{2})[-_.年/](\d{1,2})[-_.月/](\d{1,2})"),
    re.compile(r"(20\d{2})(\d{2})(\d{2})"),
]


# ---------- 通用工具 ----------

def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def guess_date(name: str, text: str = "") -> str:
    for source in (name, text[:1500]):
        for rx in DATE_RES:
            m = rx.search(source)
            if m:
                y, mo, d = (int(x) for x in m.groups())
                try:
                    return dt.date(y, mo, d).isoformat()
                except ValueError:
                    continue
    return ""


def guess_type(name: str, text: str) -> str:
    """文件名优先，正文兜底：避免正文里的「排期/验收」把周报误分类。"""
    pairs = ((COMM_KW, "communication"), (ACCEPT_KW, "acceptance"), (MILE_KW, "milestone"), (REPORT_KW, "report"))
    fname = name.lower()
    for kw, label in pairs:
        if any(k.lower() in fname for k in kw):
            return label
    body = text[:600].lower()
    for kw, label in pairs:
        if any(k.lower() in body for k in kw):
            return label
    return "other"


class _HTMLText(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("p", "br", "div", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


# ---------- 各类解析 ----------

def parse_docx(data: bytes) -> tuple[str, str, str]:
    try:
        import docx  # type: ignore
        doc = docx.Document(io.BytesIO(data))
        lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for tb in doc.tables:
            for row in tb.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    lines.append("\t".join(cells))
        if lines:
            return "\n".join(lines), "python-docx", ""
    except Exception:
        pass
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            targets = ["word/document.xml"] + sorted(
                n for n in z.namelist() if re.match(r"word/(header|footer)\d*\.xml$", n)
            )
            lines = []
            for n in targets:
                if n not in z.namelist():
                    continue
                root = ET.fromstring(z.read(n))
                for p in root.iter(W_NS + "p"):
                    line = "".join(t.text or "" for t in p.iter(W_NS + "t")).strip()
                    if line:
                        lines.append(line)
            return "\n".join(lines), "zip+XML", ""
    except Exception as exc:
        return "", "docx-failed", f"无法解析 docx：{exc}"


def parse_xlsx(data: bytes) -> tuple[str, str, str]:
    try:
        import openpyxl  # type: ignore
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines = []
        for ws in wb.worksheets:
            lines.append(f"### 工作表：{ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                if any(c.strip() for c in cells):
                    lines.append("\t".join(cells))
        wb.close()
        if lines:
            return "\n".join(lines), "openpyxl", ""
    except Exception:
        pass
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in z.namelist():
                root = ET.fromstring(z.read("xl/sharedStrings.xml"))
                for si in root.findall(S_NS + "si"):
                    shared.append("".join(t.text or "" for t in si.iter(S_NS + "t")))
            sheets = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
            lines = []
            for sh in sheets:
                lines.append(f"### {sh}")
                root = ET.fromstring(z.read(sh))
                for row in root.iter(S_NS + "row"):
                    cells = []
                    for c in row.findall(S_NS + "c"):
                        t, v, inline = c.get("t"), c.find(S_NS + "v"), c.find(S_NS + "is")
                        if t == "s" and v is not None and v.text:
                            idx = int(v.text)
                            val = shared[idx] if idx < len(shared) else ""
                        elif t == "inlineStr" and inline is not None:
                            val = "".join(x.text or "" for x in inline.iter(S_NS + "t"))
                        elif v is not None:
                            val = v.text or ""
                        else:
                            val = ""
                        cells.append(val)
                    if any(x.strip() for x in cells):
                        lines.append("\t".join(cells))
            return "\n".join(lines), "zip+XML", ""
    except Exception as exc:
        return "", "xlsx-failed", f"无法解析 xlsx：{exc}"


def parse_pptx(data: bytes) -> tuple[str, str, str]:
    try:
        import pptx  # type: ignore
        prs = pptx.Presentation(io.BytesIO(data))
        lines = []
        for i, slide in enumerate(prs.slides, 1):
            lines.append(f"### 第 {i} 页")
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text.strip():
                    lines.append(shape.text.strip())
        if lines:
            return "\n".join(lines), "python-pptx", ""
    except Exception:
        pass
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            slides = sorted(
                (n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                key=lambda n: int(re.search(r"slide(\d+)", n).group(1)),
            )
            lines = []
            for n in slides:
                lines.append(f"### {n}")
                root = ET.fromstring(z.read(n))
                buf = []
                for t in root.iter(A_NS + "t"):
                    if t.text and t.text.strip():
                        buf.append(t.text.strip())
                lines.append("\n".join(buf))
            return "\n".join(lines), "zip+XML", ""
    except Exception as exc:
        return "", "pptx-failed", f"无法解析 pptx：{exc}"


def parse_pdf(data: bytes) -> tuple[str, str, str]:
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
        if text.strip():
            return text, "pypdf", ""
    except Exception:
        pass
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        if text.strip():
            return text, "pdfplumber", ""
    except Exception:
        pass
    # 标准库兜底：抽取内容流里的文本字面量（对未压缩/Flate 的拉丁文本有效）
    out: list[str] = []
    streams = re.findall(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S)
    for raw in streams:
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
        for m in re.finditer(rb"\(((?:\\.|[^\\()])*)\)", raw):
            s = m.group(1)
            s = re.sub(rb"\\([nrt()\\])", lambda x: {b"n": b"\n", b"r": b"\r", b"t": b"\t"}.get(x.group(1), x.group(1)), s)
            try:
                out.append(s.decode("utf-8"))
            except UnicodeDecodeError:
                out.append(s.decode("latin-1", "replace"))
    text = " ".join(out)
    warn = ""
    if not text.strip():
        warn = "PDF 无可选文本层（扫描件或 CID 编码），需 OCR / 视觉读取；也可 pip install pypdf"
    else:
        warn = "使用标准库兜底抽取，中文 PDF 可能不完整；建议 pip install pypdf"
    return text, "stdlib-stream", warn


def parse_eml(data: bytes) -> tuple[str, str, str]:
    try:
        from email import policy
        from email.parser import BytesParser
        msg = BytesParser(policy=policy.default).parsebytes(data)
        parts = [f"主题：{msg.get('Subject', '')}", f"发件人：{msg.get('From', '')}", f"日期：{msg.get('Date', '')}"]
        if msg.is_multipart():
            for p in msg.walk():
                if p.get_content_type() == "text/plain":
                    parts.append(decode_bytes(p.get_payload(decode=True) or b""))
                elif p.get_content_type() == "text/html":
                    parser = _HTMLText()
                    parser.feed(decode_bytes(p.get_payload(decode=True) or b""))
                    parts.append("".join(parser.parts))
        else:
            parts.append(decode_bytes(msg.get_payload(decode=True) or b""))
        return "\n".join(parts), "email", ""
    except Exception as exc:
        return decode_bytes(data), "email-fallback", f"邮件解析降级：{exc}"


def parse_html(data: bytes) -> tuple[str, str, str]:
    parser = _HTMLText()
    try:
        parser.feed(decode_bytes(data))
    except Exception:
        pass
    lines = [re.sub(r"\s+", " ", x).strip() for x in "".join(parser.parts).splitlines()]
    return "\n".join(x for x in lines if x), "html.parser", ""


def parse_image(data: bytes) -> tuple[str, str, str]:
    size = ""
    try:
        from PIL import Image  # type: ignore
        with Image.open(io.BytesIO(data)) as im:
            size = f"{im.width}x{im.height}"
    except Exception:
        pass
    text = f"[图片资料{'，尺寸 ' + size if size else ''}：截图类沟通记录，未提取文字]"
    return text, "image-metadata", "needs_vision：请交给视觉读取，或另存为文本后再跑一次"


def parse_any(name: str, data: bytes) -> tuple[str, str, str]:
    ext = Path(name).suffix.lower()
    if ext in {".docx"}:
        return parse_docx(data)
    if ext in {".xlsx", ".xlsm"}:
        return parse_xlsx(data)
    if ext in {".pptx", ".pptm"}:
        return parse_pptx(data)
    if ext == ".pdf":
        return parse_pdf(data)
    if ext in {".doc", ".xls", ".ppt"}:
        return "", "legacy-office", "旧版二进制 Office，请另存为 .docx/.xlsx/.pptx 后重新放入"
    if ext in {".eml", ".msg"}:
        return parse_eml(data)
    if ext in {".html", ".htm", ".xml"}:
        return parse_html(data)
    if ext in IMAGE_EXT:
        return parse_image(data)
    if ext in TEXT_EXT or not ext:
        text = decode_bytes(data)
        if ext == ".json":
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except Exception:
                pass
        return text, "plain-text", ""
    return decode_bytes(data), "plain-text", f"未识别扩展名 {ext}，按纯文本处理"


# ---------- 文件收集 ----------

def expand_zip(name: str, data: bytes, depth: int = 0) -> list[tuple[str, bytes]]:
    if depth > 2:
        return []
    out = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if info.is_dir() or Path(info.filename).name.lower() in SKIP_NAMES:
                    continue
                if info.file_size > 20 * 1024 * 1024:
                    continue
                inner = z.read(info)
                subname = f"{name}!/{info.filename}"
                if info.filename.lower().endswith(".zip"):
                    out.extend(expand_zip(subname, inner, depth + 1))
                else:
                    out.append((subname, inner))
    except Exception:
        pass
    return out


def make_record(project: str, rel: str, name: str, data: bytes, as_of: str) -> dict:
    text, method, warning = parse_any(name, data)
    text = re.sub(r"\n{4,}", "\n\n\n", text or "").strip()
    date = guess_date(name, text)
    kind = "other" if method == "image-metadata" else guess_type(name, text)
    return {
        "project": project,
        "file": name,
        "relpath": rel,
        "ext": Path(name).suffix.lower(),
        "type": kind,
        "date": date,
        "text": text,
        "chars": len(text),
        "method": method,
        "warning": warning,
    }


def collect(root: Path, as_of: str) -> tuple[list[dict], list[str]]:
    src = root / "data" / "sources"
    notes: list[str] = []
    if not src.exists():
        src.mkdir(parents=True, exist_ok=True)
        notes.append("data/sources/ 为空，未找到任何资料。")
        return [], notes

    project_dirs = sorted(p for p in src.iterdir() if p.is_dir())
    known = [p.name for p in project_dirs]
    records: list[dict] = []

    for pdir in project_dirs:
        for f in sorted(pdir.rglob("*")):
            if not f.is_file() or f.name.lower() in SKIP_NAMES:
                continue
            data = f.read_bytes()
            rel = str(f.relative_to(src)).replace("\\", "/")
            if f.suffix.lower() == ".zip":
                for subname, inner in expand_zip(f.name, data):
                    records.append(make_record(pdir.name, rel, subname, inner, as_of))
            else:
                records.append(make_record(pdir.name, rel, f.name, data, as_of))

    for f in sorted(p for p in src.iterdir() if p.is_file() and p.name.lower() not in SKIP_NAMES):
        project = next((k for k in known if k in f.stem), "_未归类")
        data = f.read_bytes()
        if f.suffix.lower() == ".zip":
            for subname, inner in expand_zip(f.name, data):
                records.append(make_record(project, f.name, subname, inner, as_of))
        else:
            records.append(make_record(project, f.name, f.name, data, as_of))

    if any(r["project"] == "_未归类" for r in records):
        notes.append("存在散落根目录、未能归类的文件，已放入 _未归类/ 分组，请人工确认归属。")
    if not records:
        notes.append("未读取到任何文件。把客户资料按项目放进 data/sources/<项目名>/ 后重跑。")
    return records, notes


def write_report(root: Path, payload: dict, notes: list[str]) -> None:
    lines = [
        "# 资料抽取报告", "",
        f"- 基准日：{payload['meta']['as_of']}",
        f"- 生成时间：{payload['meta']['generated_at']}",
        f"- 文件数：{payload['meta']['source_count']}",
        f"- 项目数：{len(payload['meta']['projects'])}（{', '.join(payload['meta']['projects']) or '无'}）",
        "",
    ]
    if notes:
        lines += ["## 提示", ""] + [f"- {n}" for n in notes] + [""]
    lines += ["| 项目 | 文件 | 类型 | 日期 | 字数 | 解析方式 | 备注 |", "|---|---|---|---|---:|---|---|"]
    for r in payload["records"]:
        lines.append(
            f"| {r['project']} | `{r['file']}` | {r['type']} | {r['date'] or '—'} | {r['chars']} | {r['method']} | {r['warning'] or '—'} |"
        )
    warns = [r for r in payload["records"] if r["warning"]]
    lines += ["", f"## 需关注（{len(warns)} 项）", ""]
    lines += [f"- `{r['file']}`：{r['warning']}" for r in warns] or ["- 无"]
    (root / "logs" / "extract_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="抽取多格式项目资料为统一证据包")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="技能根目录")
    ap.add_argument("--as-of", default=dt.date.today().isoformat(), help="判定基准日 YYYY-MM-DD")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)

    records, notes = collect(root, args.as_of)
    projects = sorted({r["project"] for r in records})
    payload = {
        "meta": {
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "as_of": args.as_of,
            "source_count": len(records),
            "projects": projects,
        },
        "records": records,
    }
    (root / "data" / "extracted.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_report(root, payload, notes)
    print(f"[extract] {len(records)} 个文件 / {len(projects)} 个项目 -> data/extracted.json")
    for n in notes:
        print(f"[extract] 提示：{n}")
    for r in records:
        if r["warning"]:
            print(f"[extract] 降级：{r['file']} -> {r['warning']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
