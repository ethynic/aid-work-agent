#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XLSX 包内 XML 手术引擎。

设计目标：只改「需要填的单元格 + 动态增减行牵连的引用」，其余部件保持原始字节。
因此图表、宏、数据透视、切片器、形状、条件格式、数据验证、批注、定义名称、
打印设置、主题等元素不会因为重新序列化而丢失。

坐标约定：所有 analysis.json 中的 sheet / row / ref 均指【模板原始坐标】；
动态增减行后由引擎统一做行号映射，调用方不需要自己推算新行号。
"""
import os
import re
import copy
import zipfile
import hashlib
import datetime
from lxml import etree

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CHART = "http://schemas.openxmlformats.org/drawingml/2006/chart"


def q(ns, tag):
    return "{%s}%s" % (ns, tag)


def col_to_num(col):
    n = 0
    for ch in str(col).upper():
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch) - 64)
    return n


def num_to_col(n):
    s = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s or "A"


def split_ref(ref):
    """'Sheet1'!$A$5 -> ('Sheet1', 'A', 5, True, True)"""
    ref = (ref or "").strip()
    sheet = None
    m = re.match(r"^(?:'((?:[^']|'')+)'|([^!]+))!(.+)$", ref)
    if m:
        sheet = (m.group(1) or m.group(2)).replace("''", "'")
        ref = m.group(3)
    m = re.match(r"^(\$?)([A-Za-z]{1,3})(\$?)([1-9][0-9]*)$", ref)
    if not m:
        return (sheet, None, None, False, False)
    return (sheet, m.group(2).upper(), int(m.group(4)),
            bool(m.group(1)), bool(m.group(3)))


def join_ref(sheet, col, row, abs_col=False, abs_row=False):
    s = ("$" if abs_col else "") + col + ("$" if abs_row else "") + str(row)
    if sheet:
        if re.search(r"[^A-Za-z0-9_.]", sheet):
            s = "'" + sheet.replace("'", "''") + "'!" + s
        else:
            s = sheet + "!" + s
    return s


_REF_RE = re.compile(
    r"(?P<sheet>(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!)?"
    r"(?P<dc>\$?)(?P<col>[A-Za-z]{1,3})(?P<dr>\$?)(?P<row>[1-9][0-9]*)"
    r"(?![A-Za-z0-9_(])"
)


def _map_row(row_map, r, default=None):
    if r in row_map:
        return row_map[r]
    return default if default is not None else r


def shift_formula(text, row_map, sheet_name=None, extend=None):
    """平移公式里的 A1 引用；字符串常量不动；非目标 sheet 的限定引用不动。

    extend=(first, last, delta)：动态表增行时，把恰好落在样例末行的引用扩到新末行。
    """
    if not text or not row_map:
        return text
    out, pos = [], 0
    for part in re.split(r'("(?:[^"]|"")*")', text):
        if part.startswith('"'):
            out.append(part)
            continue
        def repl(m):
            prefix = m.group("sheet")
            target = None
            if prefix:
                target = prefix[:-1]
                if target.startswith("'") and target.endswith("'"):
                    target = target[1:-1].replace("''", "'")
            if sheet_name and target and target != sheet_name:
                return m.group(0)
            r = int(m.group("row"))
            nr = _map_row(row_map, r)
            if extend and extend[2] > 0 and r == extend[1]:
                nr = r + extend[2]
            if nr == r:
                return m.group(0)
            return ("%s" % (prefix or "")) + m.group("dc") + m.group("col") + \
                   m.group("dr") + str(nr)
        out.append(_REF_RE.sub(repl, part))
        pos += 1
    return "".join(out)


def shift_range_ref(ref, row_map, sheet_name=None, extend=None):
    """平移 A1 / A1:B5 / 1:5 / Sheet1!$A$1:$B$5 / 多区域引用。"""
    if not ref or not row_map:
        return ref
    pieces = re.split(r"(\s+)", ref)
    out = []
    for piece in pieces:
        if not piece.strip():
            out.append(piece)
            continue
        m = re.match(r"^(?:(?:'((?:[^']|'')+)'|([^!:+]+))!)?(.*)$", piece)
        sheet = None
        body = piece
        if m and m.group(3) is not None and (m.group(1) or m.group(2)):
            sheet = (m.group(1) or m.group(2)).replace("''", "'")
            body = m.group(3)
            if sheet_name and sheet != sheet_name:
                out.append(piece)
                continue
        if re.match(r"^\$?[1-9][0-9]*:\$?[1-9][0-9]*$", body):
            a, b = body.split(":")
            ra, rb = int(a.lstrip("$")), int(b.lstrip("$"))
            na, nb = _map_row(row_map, ra), _map_row(row_map, rb)
            if extend and extend[2] > 0 and rb == extend[1]:
                nb = rb + extend[2]
            body = a[0] + str(na) + ":" + b[0] + str(nb)
        else:
            body = shift_formula(body, row_map, None, extend)
        out.append((sheet + "!" if sheet else "") + body)
    return "".join(out)


def date_to_serial(value, date1904=False):
    if isinstance(value, datetime.datetime):
        dt = value
    elif isinstance(value, datetime.date):
        dt = datetime.datetime.combine(value, datetime.time())
    else:
        s = str(value).strip().replace("/", "-")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError("无法解析日期: %s" % value)
    epoch = datetime.datetime(1904, 1, 1) if date1904 else datetime.datetime(1899, 12, 30)
    return (dt - epoch).total_seconds() / 86400.0


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


class XlsxEditor:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.zf = zipfile.ZipFile(self.path, "r")
        self.infos = self.zf.infolist()
        self.parts = {}
        for info in self.infos:
            self.parts[info.filename] = self.zf.read(info.filename)
        self._roots = {}
        self.dirty = set()
        self.warnings = []
        self._load_workbook()
        self._load_shared()

    # ---------- 基础读取 ----------
    def _load_workbook(self):
        root = self.get_root("xl/workbook.xml")
        rel_root = self.get_root("xl/_rels/workbook.xml.rels")
        rels = {}
        for rel in rel_root:
            rels[rel.get("Id")] = rel.get("Target")
        self.sheet_map = {}
        self.sheet_order = []
        for s in root.find(q(NS_MAIN, "sheets")):
            name = s.get("name")
            rid = s.get(q(NS_REL_DOC, "id"))
            target = rels.get(rid, "")
            target = target.lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            target = os.path.normpath(target).replace("\\", "/")
            self.sheet_map[name] = target
            self.sheet_order.append(name)
        pr = root.find(q(NS_MAIN, "workbookPr"))
        self.date1904 = bool(pr is not None and pr.get("date1904") in ("1", "true"))

    def _load_shared(self):
        self.shared = []
        if "xl/sharedStrings.xml" not in self.parts:
            return
        root = self.get_root("xl/sharedStrings.xml")
        for si in root.findall(q(NS_MAIN, "si")):
            self.shared.append("".join(t.text or "" for t in si.iter(q(NS_MAIN, "t"))))

    def get_root(self, part):
        if part not in self._roots:
            self._roots[part] = etree.fromstring(self.parts[part])
        return self._roots[part]

    def mark_dirty(self, part):
        self.dirty.add(part)

    def sheet_root(self, name):
        if name not in self.sheet_map:
            raise KeyError("找不到 sheet: %s（现有: %s）" % (name, ", ".join(self.sheet_order)))
        return self.get_root(self.sheet_map[name])

    def _sheet_part(self, name):
        return self.sheet_map[name]

    # ---------- 单元格读写 ----------
    def cell_value(self, sheet, ref):
        sheet_name, col, row, _, _ = split_ref(ref)
        if sheet_name:
            sheet = sheet_name
        root = self.sheet_root(sheet)
        c = self._find_cell(root, row, col_to_num(col)) if col else None
        if c is None:
            return {"value": "", "formula": "", "type": None, "style": None, "raw": ""}
        f = c.find(q(NS_MAIN, "f"))
        formula = (f.text or "") if f is not None else ""
        t = c.get("t")
        if t == "inlineStr":
            is_el = c.find(q(NS_MAIN, "is"))
            value = "".join(x.text or "" for x in is_el.iter(q(NS_MAIN, "t"))) if is_el is not None else ""
        else:
            v = c.find(q(NS_MAIN, "v"))
            raw = v.text if v is not None and v.text is not None else ""
            if t == "s" and raw.isdigit():
                idx = int(raw)
                value = self.shared[idx] if idx < len(self.shared) else ""
            elif t == "str":
                value = raw
            elif t == "b":
                value = "TRUE" if raw == "1" else "FALSE"
            else:
                value = raw
        return {"value": value, "formula": formula, "type": t,
                "style": c.get("s"), "raw": value}

    def _find_row(self, sd, row_num):
        for r in sd.findall(q(NS_MAIN, "row")):
            if int(r.get("r")) == row_num:
                return r
        return None

    def _find_cell(self, root, row_num, col_num):
        sd = root.find(q(NS_MAIN, "sheetData"))
        row = self._find_row(sd, row_num)
        if row is None:
            return None
        for c in row.findall(q(NS_MAIN, "c")):
            if col_to_num(split_ref(c.get("r"))[1]) == col_num:
                return c
        return None

    def _ensure_cell(self, root, row_num, col_num):
        sd = root.find(q(NS_MAIN, "sheetData"))
        row = self._find_row(sd, row_num)
        if row is None:
            row = etree.Element(q(NS_MAIN, "row"), r=str(row_num))
            inserted = False
            for other in sd.findall(q(NS_MAIN, "row")):
                if int(other.get("r")) > row_num:
                    other.addprevious(row)
                    inserted = True
                    break
            if not inserted:
                sd.append(row)
        for c in row.findall(q(NS_MAIN, "c")):
            if col_to_num(split_ref(c.get("r"))[1]) == col_num:
                return c
        c = etree.Element(q(NS_MAIN, "c"),
                          r=join_ref(None, num_to_col(col_num), row_num))
        inserted = False
        for other in row.findall(q(NS_MAIN, "c")):
            if col_to_num(split_ref(other.get("r"))[1]) > col_num:
                other.addprevious(c)
                inserted = True
                break
        if not inserted:
            row.append(c)
        return c

    def write_cell(self, sheet, ref, value=None, kind="auto", style=None,
                   template_ref=None, partial=None):
        """写入单个单元格。kind: auto/text/number/date/bool/formula/blank/keep_formula."""
        sname, col, row, _, _ = split_ref(ref)
        if sname:
            sheet = sname
        if not col or not row:
            raise ValueError("非法单元格坐标: %s" % ref)
        root = self.sheet_root(sheet)
        old = self.cell_value(sheet, "%s%d" % (col, row))
        if template_ref and template_ref != "%s%d" % (col, row):
            old = self.cell_value(sheet, template_ref)
        c = self._ensure_cell(root, row, col_to_num(col))
        if style is not None:
            c.set("s", str(style))
        elif old.get("style") and not c.get("s"):
            c.set("s", old["style"])

        for tag in ("f", "v", "is"):
            el = c.find(q(NS_MAIN, tag))
            if el is not None:
                c.remove(el)

        if kind == "blank":
            c.attrib.pop("t", None)
        elif kind in ("formula", "keep_formula"):
            formula = str(value if value is not None else "")
            if formula.startswith("="):
                formula = formula[1:]
            if kind == "keep_formula" and old.get("formula"):
                formula = old["formula"]
            f = etree.SubElement(c, q(NS_MAIN, "f"))
            f.text = formula
            c.attrib.pop("t", None)
            self.set_full_calc()
        elif kind == "text":
            c.set("t", "inlineStr")
            is_el = etree.SubElement(c, q(NS_MAIN, "is"))
            t = etree.SubElement(is_el, q(NS_MAIN, "t"))
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            t.text = "" if value is None else str(value)
        elif kind == "number":
            c.attrib.pop("t", None)
            v = etree.SubElement(c, q(NS_MAIN, "v"))
            v.text = str(value)
        elif kind == "date":
            c.attrib.pop("t", None)
            v = etree.SubElement(c, q(NS_MAIN, "v"))
            v.text = "%.10g" % date_to_serial(value, self.date1904)
        elif kind == "bool":
            c.set("t", "b")
            v = etree.SubElement(c, q(NS_MAIN, "v"))
            v.text = "1" if value in (True, 1, "1", "TRUE", "true", "是") else "0"
        else:  # auto
            if partial is not None:
                current = old.get("value", "")
                newval = current.replace(str(partial), str(value)) if partial in current else str(value)
                self.write_cell(sheet, ref, newval, kind="text", style=style)
                return
            if isinstance(value, bool):
                self.write_cell(sheet, ref, value, kind="bool", style=style)
                return
            old_t = old.get("type")
            textish = old_t in ("s", "inlineStr", "str", "e")
            sval = "" if value is None else str(value).strip()
            if old_t == "n" or (not textish and re.match(r"^-?\d+(?:\.\d+)?$", sval) and not sval.startswith("0")):
                self.write_cell(sheet, ref, sval, kind="number", style=style)
            else:
                self.write_cell(sheet, ref, value, kind="text", style=style)
        self.mark_dirty(self._sheet_part(sheet))

    def find_cell(self, sheet, contains=None, exact=False, formula_contains=None,
                  occurrence=0, exclude_refs=None):
        """按文本/公式内容定位单元格，返回 'A1' 坐标。"""
        root = self.sheet_root(sheet)
        exclude = set(exclude_refs or [])
        hits = []
        for row in root.find(q(NS_MAIN, "sheetData")).findall(q(NS_MAIN, "row")):
            for c in row.findall(q(NS_MAIN, "c")):
                ref = c.get("r")
                if ref in exclude:
                    continue
                info = self.cell_value(sheet, ref)
                if formula_contains is not None:
                    if formula_contains in info["formula"]:
                        hits.append(ref)
                elif contains is not None:
                    if (info["value"] == contains if exact else contains in info["value"]):
                        hits.append(ref)
        if not hits:
            return None
        return hits[min(occurrence, len(hits) - 1)]

    def set_full_calc(self):
        root = self.get_root("xl/workbook.xml")
        calc = root.find(q(NS_MAIN, "calcPr"))
        if calc is None:
            calc = etree.SubElement(root, q(NS_MAIN, "calcPr"))
        calc.set("calcMode", "auto")
        calc.set("fullCalcOnLoad", "1")
        calc.set("forceFullCalc", "1")
        self.mark_dirty("xl/workbook.xml")

    # ---------- 动态增减行 ----------
    def expand_rows(self, sheet, sample_rows, count, template_row=None):
        """按 count 条数据扩展样例行，返回行映射与最终行号列表。"""
        root = self.sheet_root(sheet)
        sd = root.find(q(NS_MAIN, "sheetData"))
        existing = {}
        for r in sd.findall(q(NS_MAIN, "row")):
            existing[int(r.get("r"))] = r
        sample = sorted(set(int(x) for x in sample_rows))
        if not sample:
            raise ValueError("sample_rows 不能为空: %s" % sheet)
        first, last = sample[0], sample[-1]
        if sample != list(range(first, last + 1)):
            self.warnings.append("sheet %s 的 sample_rows 非连续，按连续区间 %d-%d 处理" %
                                 (sheet, first, last))
            sample = list(range(first, last + 1))
        m = len(sample)
        count = int(count)
        keep = min(m, count)
        delta = count - m
        trow_num = int(template_row or first)
        if trow_num not in existing:
            trow_num = first
        template_copy = copy.deepcopy(existing[trow_num])

        last_kept = first - 1 if keep == 0 else first + keep - 1
        row_map, drop_rows = {}, set()
        for r in existing:
            if r <= last_kept:
                row_map[r] = r
            elif r <= last:
                row_map[r] = first if keep == 0 else last_kept
                drop_rows.add(r)
            else:
                row_map[r] = r + delta
        for r in drop_rows:
            sd.remove(existing[r])

        extend = (first, last, delta) if delta > 0 else None
        self._apply_map(root, row_map, sheet_name=sheet, extend=extend)
        self._apply_map_workbook(row_map, sheet, extend)
        self._apply_map_tables(sheet, row_map, extend)
        self._apply_map_pivots(sheet, row_map, extend)
        self._apply_map_charts(sheet, row_map, extend)
        self._apply_map_comments(sheet, row_map)

        if count > m:
            anchor = existing.get(last)
            for i in range(count - m):
                new_num = last + 1 + i
                new_row = copy.deepcopy(template_copy)
                new_row.set("r", str(new_num))
                self._apply_map(new_row, {trow_num: new_num}, sheet_name=sheet)
                if anchor is not None and anchor.getparent() is not None:
                    anchor.addnext(new_row)
                    anchor = new_row
                else:
                    sd.append(new_row)

        self.mark_dirty(self._sheet_part(sheet))
        return {"row_map": row_map, "first": first, "last": last, "delta": delta,
                "final_rows": [first + i for i in range(count)], "count": count}

    def _apply_map(self, root, row_map, sheet_name=None, extend=None):
        for el in root.iter():
            tag = etree.QName(el).localname if isinstance(el.tag, str) else ""
            if tag == "f" and el.text:
                el.text = shift_formula(el.text, row_map, sheet_name, extend)
            for attr in ("r", "ref", "sqref", "activeCell", "topLeftCell"):
                val = el.get(attr)
                if not val:
                    continue
                if attr == "r" and tag == "row":
                    if val.isdigit():
                        el.set(attr, str(_map_row(row_map, int(val))))
                    continue
                if attr == "r" and tag == "c":
                    sh, col, rr, dc, dr = split_ref(val)
                    if col and rr:
                        el.set(attr, join_ref(None, col, _map_row(row_map, rr, self._default_map(row_map, rr)),
                                              dc, dr))
                    continue
                sh, col, rr, dc, dr = split_ref(val)
                if col and rr and " " not in val and ":" not in val:
                    el.set(attr, join_ref(sh, col, _map_row(row_map, rr, self._default_map(row_map, rr)), dc, dr))
                else:
                    el.set(attr, shift_range_ref(val, row_map, sheet_name, extend))

    @staticmethod
    def _default_map(row_map, r):
        return row_map.get(r, r)

    def _apply_map_workbook(self, row_map, sheet_name, extend=None):
        part = "xl/workbook.xml"
        root = self.get_root(part)
        wb_sheets = list(root.find(q(NS_MAIN, "sheets")))
        idx = self.sheet_order.index(sheet_name) if sheet_name in self.sheet_order else -1
        for dn in root.findall(".//" + q(NS_MAIN, "definedName")):
            local = dn.get("localSheetId")
            local_ok = local is not None and int(local) == idx
            target = sheet_name if (local_ok or not local) else None
            if dn.text:
                dn.text = shift_formula(dn.text, row_map, target, extend)
        self.mark_dirty(part)

    def _related_parts(self, part):
        directory, filename = os.path.split(part)
        rel_part = os.path.join(directory, "_rels", filename + ".rels").replace("\\", "/")
        if rel_part not in self.parts:
            return {}
        root = self.get_root(rel_part)
        out = {}
        for rel in root:
            target = rel.get("Target", "")
            if target.startswith("http") or rel.get("TargetMode") == "External":
                continue
            if target.startswith("/"):
                full = target.lstrip("/")
            else:
                full = os.path.normpath(os.path.join(directory, target)).replace("\\", "/")
            out[rel.get("Id")] = full
        return out

    def _apply_map_tables(self, sheet_name, row_map, extend=None):
        part = self._sheet_part(sheet_name)
        for rel in self._related_parts(part).values():
            if "/tables/" not in rel or rel not in self.parts:
                continue
            root = self.get_root(rel)
            for el in root.iter():
                tag = etree.QName(el).localname if isinstance(el.tag, str) else ""
                if tag in ("table", "autoFilter") and el.get("ref"):
                    el.set("ref", shift_range_ref(el.get("ref"), row_map, None, extend))
                if tag in ("f", "formula") and el.text:
                    el.text = shift_formula(el.text, row_map, None, extend)
            self.mark_dirty(rel)

    def _apply_map_pivots(self, sheet_name, row_map, extend=None):
        """同步数据透视缓存的 worksheetSource 区域，并设置打开时刷新。

        仅重写源区域 ref 与 refreshOnLoad；缓存记录与共享项由 Excel 打开时重建。
        """
        for part in sorted(self.parts):
            if not (part.startswith("xl/pivotCache/") and part.endswith(".xml")):
                continue
            root = self.get_root(part)
            touched = False
            for src in root.iter(q(NS_MAIN, "worksheetSource")):
                src_sheet = src.get("sheet")
                if src_sheet and src_sheet != sheet_name:
                    continue
                if src.get("ref"):
                    src.set("ref", shift_range_ref(src.get("ref"), row_map, None, extend))
                    touched = True
            if touched:
                root.set("refreshOnLoad", "1")
                self.mark_dirty(part)

    def _apply_map_charts(self, sheet_name, row_map, extend=None):
        part = self._sheet_part(sheet_name)
        drawings = [v for v in self._related_parts(part).values() if "/drawings/" in v]
        charts = []
        for d in drawings:
            for v in self._related_parts(d).values():
                if "/charts/" in v:
                    charts.append(v)
        for cpart in charts:
            if cpart not in self.parts:
                continue
            root = self.get_root(cpart)
            for f in root.iter(q(NS_CHART, "f")):
                if f.text:
                    f.text = shift_formula(f.text, row_map, sheet_name, extend)
            self.mark_dirty(cpart)

    def _apply_map_comments(self, sheet_name, row_map):
        part = self._sheet_part(sheet_name)
        for rel in self._related_parts(part).values():
            if "/comments/" not in rel or not rel.endswith(".xml") or rel not in self.parts:
                continue
            root = self.get_root(rel)
            for c in root.iter(q(NS_MAIN, "comment")):
                if c.get("ref"):
                    c.set("ref", shift_range_ref(c.get("ref"), row_map, None))
            self.mark_dirty(rel)

    # ---------- 验收与保存 ----------
    def signature(self):
        sig = {"sheets": [], "parts_count": len(self.parts)}
        wb = self.get_root("xl/workbook.xml")
        sig["defined_names"] = sorted((d.get("name") or "") for d in
                                      wb.findall(".//" + q(NS_MAIN, "definedName")))
        sig["styles_sha256"] = sha256_bytes(self.parts.get("xl/styles.xml", b""))
        for name in self.sheet_order:
            root = self.sheet_root(name)
            sd = root.find(q(NS_MAIN, "sheetData"))
            rows = sd.findall(q(NS_MAIN, "row")) if sd is not None else []
            merges = root.find(q(NS_MAIN, "mergeCells"))
            item = {
                "name": name,
                "state": self._sheet_state(name),
                "max_row": max([int(r.get("r")) for r in rows], default=0),
                "merged_count": len(merges) if merges is not None else 0,
                "formula_count": len(list(root.iter(q(NS_MAIN, "f")))),
                "dv_count": len(list(root.iter(q(NS_MAIN, "dataValidation")))),
                "cf_count": len(list(root.iter(q(NS_MAIN, "conditionalFormatting")))),
                "hyperlinks": len(list(root.iter(q(NS_MAIN, "hyperlink")))),
                "freeze": self._pane(root),
                "autofilter": self._attr(root, "autoFilter", "ref"),
                "dimension": self._attr(root, "dimension", "ref"),
            }
            sig["sheets"].append(item)
        for key in ("xl/tables", "xl/drawings", "xl/charts", "xl/pivotTables", "xl/slicerCaches"):
            sig[key] = len([p for p in self.parts if p.startswith(key + "/")])
        sig["vba"] = any(p.endswith("vbaProject.bin") for p in self.parts)
        return sig

    def _sheet_state(self, name):
        for s in self.get_root("xl/workbook.xml").find(q(NS_MAIN, "sheets")):
            if s.get("name") == name:
                return s.get("state", "visible")
        return None

    @staticmethod
    def _attr(root, tag, attr):
        el = root.find(q(NS_MAIN, tag))
        return el.get(attr) if el is not None else None

    @staticmethod
    def _pane(root):
        pane = root.find(".//" + q(NS_MAIN, "pane"))
        if pane is None:
            return None
        return "%s|%s|%s|%s" % (pane.get("xSplit"), pane.get("ySplit"),
                                pane.get("topLeftCell"), pane.get("state"))

    def _serialize(self, part):
        return etree.tostring(self._roots[part], xml_declaration=True,
                              encoding="UTF-8", standalone=True)

    def save(self, output):
        output = os.path.abspath(output)
        os.makedirs(os.path.dirname(output), exist_ok=True)
        changed = []
        with zipfile.ZipFile(output, "w") as zout:
            for info in self.infos:
                data = self._serialize(info.filename) if info.filename in self.dirty \
                    else self.parts[info.filename]
                if info.filename in self.dirty:
                    changed.append(info.filename)
                zout.writestr(info, data)
        return changed

    def close(self):
        self.zf.close()
