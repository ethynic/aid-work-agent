"""Content fillers for the two routes.

Route A (placeholder): add a slide from a layout, fill its placeholders.
Route B (deep-copy):   copy a template slide, replace text by shape name /
                       original text / substring.

Both go through _fill_paragraphs(), which preserves the original run and
paragraph formatting (font, color, bullet, indent) while swapping the text,
and grows/shrinks paragraph count by cloning the last paragraph.
"""
import copy

from pptx.enum.text import MSO_AUTO_SIZE


def _safe_text(tf):
    """Prevent overflow: enable wrap + shrink-to-fit (matches PowerPoint's
    'shrink text on overflow'). Applied only when we fully control the text."""
    tf.word_wrap = True
    try:
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    except Exception:
        pass


def _as_lines(value):
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    return str(value).split("\n")


def _fill_paragraphs(text_frame, lines):
    """Replace the text of a text frame, keeping first-run formatting."""
    lines = _as_lines(lines)
    if not lines:
        return

    # grow: clone the last paragraph until we have enough
    while len(text_frame.paragraphs) < len(lines):
        last_p = text_frame.paragraphs[-1]._p
        last_p.addnext(copy.deepcopy(last_p))

    # shrink: drop surplus trailing paragraphs
    while len(text_frame.paragraphs) > len(lines):
        p = text_frame.paragraphs[-1]._p
        p.getparent().remove(p)

    # write each line into its paragraph, keeping only the first run's style
    for para, line in zip(text_frame.paragraphs, lines):
        if para.runs:
            para.runs[0].text = line
            for r in para.runs[1:]:
                r._r.getparent().remove(r._r)
        else:
            para.text = line


def _iter_text_shapes(slide):
    for shp in slide.shapes:
        if shp.has_text_frame:
            yield shp


def replace_text(slide, replacements):
    """Replace text on a copied slide.

    ``replacements`` maps a key to new text. A key matches a shape by, in
    order: shape name, full original text, then substring of original text
    (substring hits are applied run-by-run, keeping surrounding runs).
    Returns the list of shape names that were modified.
    """
    touched = []
    done = set()

    # pass 1: whole-shape replacement by name or full text
    for shp in _iter_text_shapes(slide):
        name, full = shp.name, shp.text_frame.text
        for key, new_text in replacements.items():
            if id(shp) in done:
                break
            if key == name or key == full:
                _fill_paragraphs(shp.text_frame, new_text)
                _safe_text(shp.text_frame)
                touched.append(name)
                done.add(id(shp))
                break

    # pass 2: substring replacement inside remaining shapes (run level)
    for shp in _iter_text_shapes(slide):
        if id(shp) in done:
            continue
        shape_changed = False
        for para in shp.text_frame.paragraphs:
            for run in para.runs:
                for key, new_text in replacements.items():
                    if key and key in run.text:
                        run.text = run.text.replace(key, str(new_text))
                        shape_changed = True
        if shape_changed:
            touched.append(shp.name)
            done.add(id(shp))
    return touched


def fill_placeholders(slide, fields):
    """Fill placeholders on a layout-created slide.

    ``fields`` is keyed by placeholder idx (int/str), name, or type text.
    List/tuple values become multiple bullet paragraphs.
    """
    filled = []
    for ph in slide.placeholders:
        idx = ph.placeholder_format.idx
        candidates = {str(idx), ph.name}
        for key, value in fields.items():
            if key in candidates or key == str(idx):
                if ph.has_text_frame:
                    _fill_paragraphs(ph.text_frame, value)
                    _safe_text(ph.text_frame)
                    filled.append((idx, ph.name))
                break
    return filled
