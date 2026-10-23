"""Surgical edits for already-generated decks.

edit_deck(pptx_path, edit_spec, out_path=None) applies a list of ops to an
existing .pptx -- in place by default (a .bak sibling is kept), or to a new
path. Everything not touched by an op is preserved byte-for-byte, so decks
that were manually edited in PowerPoint/WPS keep those manual edits.

edit_spec:
    {
      "palette": {"gold": "...", "top": "...", "bottom": "..."},   # optional
      "ops": [
        {"op": "replace_text", "find": "1,897", "replace": "1,999",
         "scope": 6},                                   # 1-based page or "deck"
        {"op": "set_text", "slide": 1, "match": "成交额分析",
         "text": "成交额复盘"},
        {"op": "add_slide", "after": 5, "role": "bullets",
         "page": {"title": "...", "lead": "...",
                  "items": [{"h": "...", "d": "..."}]}},
        {"op": "replace_slide", "slide": 5, "role": "twocol",
         "page": {"title": "...", "left": {...}, "right": {...}}},
        {"op": "delete_slide", "slide": 8},
        {"op": "move_slide", "slide": 8, "to": 3},
        {"op": "recolor", "accent": "#E4572E"}          # or "map": {old: new}
      ]
    }

replace_text / set_text / delete / move / recolor also work on arbitrary
(non-skill) pptx files; add_slide / replace_slide render with the skill's
native layout and palette. Footer page numbers refresh automatically after
structural ops.
"""
import copy
import os
import re
import shutil
from collections import Counter

from pptx import Presentation
from pptx.opc.packuri import PackURI
from pptx.oxml.ns import qn
from pptx.parts.presentation import PresentationPart

from .fonts import remap_presentation
from .native import EMU_PX, _hex, add_page_slide

# colors that are never the "accent" when sniffing a deck's palette
_NON_ACCENT = {"FFFFFF", "E8F0F8", "A9C2DA", "0A1E33", "CDDCEF", "5A6B7E",
               "14395F", "FFB545", "35B8D9", "000000"}

_PAGENO_RE = re.compile(r"^\d{1,2}\s*/\s*\d{1,2}$")


class _FreePartnames:
    """Context manager: collision-free slide partnames during an edit.

    python-pptx numbers new slides by len(sldIdLst)+1, which reuses a
    partname after any delete op (replace_slide / delete_slide) and yields
    duplicate zip entries (a later add silently overwrites the earlier
    slide's XML). Patch to first-free-integer for the duration of the edit.
    """

    def __enter__(self):
        self._orig = PresentationPart._next_slide_partname

        def patched(self):
            used = set()
            for part in self.package.iter_parts():
                m = re.match(r"/ppt/slides/slide(\d+)\.xml$",
                             str(part.partname))
                if m:
                    used.add(int(m.group(1)))
            n = 1
            while n in used:
                n += 1
            return PackURI("/ppt/slides/slide%d.xml" % n)

        PresentationPart._next_slide_partname = property(patched)
        return self

    def __exit__(self, *exc):
        PresentationPart._next_slide_partname = self._orig
        return False


# ---------------------------------------------------------------- helpers
def _frame_text(tf):
    return "\n".join(p.text for p in tf.paragraphs)


def _set_para_text(p, s):
    if p.runs:
        p.runs[0].text = s
        for r in p.runs[1:]:
            r._r.getparent().remove(r._r)
    else:
        p.add_run().text = s


def _set_frame_text(tf, text):
    """Replace a text frame's content, keeping the first run's formatting."""
    lines = str(text).split("\n")
    while len(tf.paragraphs) < len(lines):
        last = tf.paragraphs[-1]._p
        last.addnext(copy.deepcopy(last))
    for i, p in enumerate(tf.paragraphs):
        if i < len(lines):
            _set_para_text(p, lines[i])
        else:
            p._p.getparent().remove(p._p)


def _replace_in_frame(tf, find, repl):
    hits = 0
    for p in tf.paragraphs:
        runs = p.runs
        if not runs:
            continue
        original = "".join(r.text for r in runs)
        n = original.count(find)
        if not n:
            continue
        hits += n
        in_run = sum(r.text.count(find) for r in runs)
        if in_run == n:
            # 所有命中都完整落在单个 run 内，逐 run 替换（保留各 run 格式）
            for r in runs:
                r.text = r.text.replace(find, repl)
        else:
            # 存在跨 run 边界的命中 -> 合并整段替换（保留首 run 格式）。
            # 判定必须用替换前的原始文本，否则 repl 含 find 时会误判残留导致二次替换
            runs[0].text = original.replace(find, repl)
            for r in runs[1:]:
                r.text = ""
    return hits


def _resolve_slides(prs, scope):
    if scope in (None, "deck", "all"):
        return list(enumerate(prs.slides))
    return [(int(scope) - 1, prs.slides[int(scope) - 1])]


def _resolve_image(page, base_dir):
    ref = page.get("image")
    if not ref:
        return None
    p = ref if os.path.isabs(ref) else os.path.join(base_dir, ref)
    return p if os.path.exists(p) else None


def _move(prs, from0, to0):
    lst = prs.slides._sldIdLst
    el = list(lst)[from0]
    lst.remove(el)
    lst.insert(to0, el)


def _delete_at(prs, idx0):
    lst = prs.slides._sldIdLst
    el = list(lst)[idx0]
    prs.part.drop_rel(el.get(qn("r:id")))
    lst.remove(el)


def _is_pageno(sh):
    """Named footer page-number box, or a legacy one found heuristically
    (right-bottom corner + 'NN / NN' text) so pre-naming decks also refresh."""
    if not sh.has_text_frame:
        return False
    if sh.name == "pptxgen_pageno":
        return True
    if not _PAGENO_RE.match(sh.text_frame.text.strip()):
        return False
    x = (sh.left or 0) / EMU_PX
    y = (sh.top or 0) / EMU_PX
    return 1400 < x < 1800 and 950 < y < 1050


def _refresh_page_numbers(prs):
    n = len(prs.slides._sldIdLst)
    for i, slide in enumerate(prs.slides, 1):
        for sh in slide.shapes:
            if _is_pageno(sh):
                sh.name = "pptxgen_pageno"     # tag legacy decks too
                _set_frame_text(sh.text_frame, f"{i:02d} / {n:02d}")


def derive_palette(prs):
    """Best-effort palette read-back: gradient stops + accent sniff."""
    pal = {"gold": "FFB545", "top": "14395F", "bottom": "0A1E33"}
    try:
        s1 = prs.slides[0]
        for sh in s1.shapes:
            grad = sh._element.spPr.find(qn("a:gradFill"))
            if grad is not None:
                gs = grad.find(qn("a:gsLst")).findall(qn("a:gs"))
                cols = [g.find(qn("a:srgbClr")).get("val").upper() for g in gs]
                if len(cols) >= 2:
                    pal["top"], pal["bottom"] = cols[0], cols[-1]
                break
        cnt = Counter()
        for sh in s1.shapes:
            for clr in sh._element.iter(qn("a:srgbClr")):
                v = (clr.get("val") or "").upper()
                if v and v not in _NON_ACCENT:
                    cnt[v] += 1
        if cnt:
            pal["gold"] = cnt.most_common(1)[0][0]
    except Exception:
        pass
    return pal


# ------------------------------------------------------------------- ops
def _op_replace_text(prs, op):
    find, repl = op["find"], op["replace"]
    hits = 0
    for _, slide in _resolve_slides(prs, op.get("scope", "deck")):
        for sh in slide.shapes:
            if sh.has_text_frame and find in _frame_text(sh.text_frame):
                hits += _replace_in_frame(sh.text_frame, find, repl)
    return hits


def _op_set_text(prs, op):
    slide = prs.slides[int(op["slide"]) - 1]
    target = None
    if "shape" in op:
        frames = [sh for sh in slide.shapes if sh.has_text_frame]
        target = frames[int(op["shape"])]
    else:
        for sh in slide.shapes:
            if sh.has_text_frame and op.get("match", "") in _frame_text(sh.text_frame):
                target = sh
                break
    if target is None:
        raise ValueError("no shape matching %r on slide %s"
                         % (op.get("match"), op["slide"]))
    _set_frame_text(target.text_frame, op["text"])
    # new text may be longer than the old one: enable wrap + shrink-to-fit
    # so it can never spill out of the shape / page
    from .content_filler import _safe_text
    _safe_text(target.text_frame)


def _op_add_slide(prs, op, pal, base_dir):
    page = dict(op["page"])
    page.setdefault("role", op.get("role"))
    if "role" not in page:
        raise ValueError("add_slide needs a role")
    add_page_slide(prs, page, pal, _resolve_image(page, base_dir))
    if "after" in op:
        _move(prs, len(prs.slides._sldIdLst) - 1, int(op["after"]))


def _op_replace_slide(prs, op, pal, base_dir):
    old = int(op["slide"]) - 1
    page = dict(op["page"])
    page.setdefault("role", op.get("role"))
    if "role" not in page:
        raise ValueError("replace_slide needs a role")
    add_page_slide(prs, page, pal, _resolve_image(page, base_dir))
    _move(prs, len(prs.slides._sldIdLst) - 1, old)   # new page takes the slot
    _delete_at(prs, old + 1)                         # old page shifted down


def _op_recolor(prs, op, pal_now):
    mapping = {k.upper().lstrip("#"): v.upper().lstrip("#")
               for k, v in (op.get("map") or {}).items()}
    if not mapping:
        if op.get("accent"):
            mapping[pal_now["gold"]] = _hex(op["accent"])
        if op.get("brand"):
            mapping[pal_now["top"]] = _hex(op["brand"])
        if op.get("dark"):
            mapping[pal_now["bottom"]] = _hex(op["dark"])
    if not mapping:
        return 0
    n = 0
    for slide in prs.slides:
        for clr in slide._element.iter(qn("a:srgbClr")):
            v = (clr.get("val") or "").upper()
            if v in mapping:
                clr.set("val", mapping[v])
                n += 1
    return n


# ---------------------------------------------------------------- pipeline
def edit_deck(pptx_path, edit_spec, out_path=None, palette=None,
              remap_fonts=True):
    """Apply edit_spec ops to an existing deck. Returns a report dict.

    In-place by default (a .bak sibling is kept next to the file).

    remap_fonts: 保存前把 deck 内商业字体替换为商用免费的思源黑体 CN
                 （白名单内的免费字体保留）；对自家生成的 deck 是 no-op。
    """
    prs = Presentation(pptx_path)
    base_dir = os.path.dirname(os.path.abspath(pptx_path))
    pal = dict(palette or derive_palette(prs))
    for k, v in (edit_spec.get("palette") or {}).items():
        pal[k] = _hex(v)

    applied, warnings = [], []
    # keep slide partnames collision-free across delete/replace + add combos
    with _FreePartnames():
        for op in edit_spec.get("ops", []):
            kind = op.get("op")
            try:
                if kind == "replace_text":
                    n = _op_replace_text(prs, op)
                    applied.append("replace_text %r->%r x%d"
                                   % (op.get("find"), op.get("replace"), n))
                elif kind == "set_text":
                    _op_set_text(prs, op)
                    applied.append("set_text slide %s" % op.get("slide"))
                elif kind == "add_slide":
                    _op_add_slide(prs, op, pal, base_dir)
                    applied.append("add_slide %s" % op.get("role"))
                elif kind == "replace_slide":
                    _op_replace_slide(prs, op, pal, base_dir)
                    applied.append("replace_slide %s" % op.get("slide"))
                elif kind == "delete_slide":
                    _delete_at(prs, int(op["slide"]) - 1)
                    applied.append("delete_slide %s" % op.get("slide"))
                elif kind == "move_slide":
                    _move(prs, int(op["slide"]) - 1, int(op["to"]) - 1)
                    applied.append("move_slide %s->%s" % (op.get("slide"),
                                                          op.get("to")))
                elif kind == "recolor":
                    n = _op_recolor(prs, op, pal)
                    applied.append("recolor x%d" % n)
                else:
                    warnings.append("unknown op: %r" % (kind,))
            except Exception as e:                   # keep going, report at end
                warnings.append("%s failed: %s" % (kind, e))

    _refresh_page_numbers(prs)
    font_report = remap_presentation(prs) if remap_fonts else None
    out = out_path or pptx_path
    if os.path.abspath(out) == os.path.abspath(pptx_path):
        shutil.copy2(pptx_path, pptx_path + ".bak")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    prs.save(out)
    return {"out": out, "slides": len(prs.slides._sldIdLst),
            "applied": applied, "warnings": warnings,
            "fonts": font_report}
