"""Reliably duplicate a slide inside a python-pptx Presentation.

python-pptx has no native slide copy. This keeps every non-layout
relationship (images, charts, media, external hyperlinks) by recreating
them on the destination slide and remapping rIds, so copied shapes keep
their pictures. Verified against python-pptx 1.0.2.

Why not the classic `rels.add_relationship(...)` snippet? That private
method was removed in 1.0.x; we rely only on the public get_or_add API.
"""
import copy
from pptx.oxml.ns import qn

# DrawingML attributes that hold a relationship id
_R_ATTRS = (
    qn("r:embed"), qn("r:id"), qn("r:link"), qn("r:pict"),
    qn("r:dm"), qn("r:lo"), qn("r:qs"), qn("r:cs"),
)

# reltypes we must NOT clone onto the copy (they are layout/master scoped
# or would drag speaker notes across)
_SKIP_RELTYPE_SUFFIX = ("/slideLayout", "/notesSlide", "/notesMaster")


def _should_copy(reltype: str) -> bool:
    return not reltype.endswith(_SKIP_RELTYPE_SUFFIX)


def copy_slide(prs, source_slide):
    """Return a new slide that is a faithful copy of ``source_slide``.

    The copy reuses ``source_slide.slide_layout`` so master background,
    theme colors and logos are preserved, and deep-copies every shape
    together with its image / chart / media relationships.
    """
    dest = prs.slides.add_slide(source_slide.slide_layout)

    # 1. drop placeholder shapes auto-created from the layout
    for shp in list(dest.shapes):
        shp._element.getparent().remove(shp._element)

    # 2. recreate every non-layout relationship on dest, remember rId remap
    remap = {}
    for rId, rel in source_slide.part.rels.items():
        if not _should_copy(rel.reltype):
            continue
        if rel.is_external:
            new_rId = dest.part.rels.get_or_add_ext_rel(rel.reltype, rel.target_ref)
        else:
            new_rId = dest.part.rels.get_or_add(rel.reltype, rel.target_part)
        if new_rId != rId:
            remap[rId] = new_rId

    # 3. deep-copy the shapes
    for shp in source_slide.shapes:
        dest.shapes._spTree.append(copy.deepcopy(shp._element))

    # 4. repoint copied XML at the remapped rIds
    if remap:
        for el in dest.shapes._spTree.iter():
            for attr in _R_ATTRS:
                val = el.get(attr)
                if val in remap:
                    el.set(attr, remap[val])
    return dest
