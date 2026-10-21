"""从客户 .pptx 模板提取视觉识别（VI）：主题色 / 主题字体 / Logo。

提取结果填充到完整 VI dict（缺项用默认值兜底），供渲染引擎使用。
"""
import glob
import io

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from lxml import etree

NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}

# 字体查找同时覆盖 Windows（开发机）与 Linux（部署容器，Noto CJK / 文泉驿）
_WIN_FONTS = r"C:\Windows\Fonts"
_LINUX_FONTS = "/usr/share/fonts"


def _find_cjk(bold):
    if bold:
        order = [
            fr"{_WIN_FONTS}\msyhbd.ttc", fr"{_WIN_FONTS}\msyhbd.ttf",
            f"{_LINUX_FONTS}/opentype/noto/NotoSansCJK-Bold.ttc",
            f"{_LINUX_FONTS}/truetype/noto/NotoSansCJK-Bold.ttc",
        ]
    else:
        order = [
            fr"{_WIN_FONTS}\msyh.ttc", fr"{_WIN_FONTS}\msyh.ttf",
            f"{_LINUX_FONTS}/opentype/noto/NotoSansCJK-Regular.ttc",
            f"{_LINUX_FONTS}/truetype/noto/NotoSansCJK-Regular.ttc",
        ]
    order += [
        fr"{_WIN_FONTS}\simhei.ttf", fr"{_WIN_FONTS}\Deng.ttf",
        fr"{_WIN_FONTS}\simsun.ttf",
        f"{_LINUX_FONTS}/truetype/wqy/wqy-zenhei.ttc",
    ]
    for pat in order:
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    return None


def default_vi():
    return {
        "brand": "#1F4E79",
        "accent": "#2E9CCA",
        "dark": "#10263D",
        "light": "#F5F7FA",
        "ink": "#1A1A1A",
        "muted": "#6B7280",
        "font_title": _find_cjk(bold=True),
        "font_body": _find_cjk(bold=False),
        "font_latin_title": None,
        "font_latin_body": None,
        "logo_img": None,
    }


def _first_picture(container):
    try:
        for shp in container.shapes:
            if shp.shape_type == MSO_SHAPE_TYPE.PICTURE:
                return shp
    except Exception:
        return None
    return None


def _logo_image(prs):
    for container in [prs.slide_masters[0]] + list(prs.slides):
        pic = _first_picture(container)
        if pic is not None:
            try:
                return Image.open(io.BytesIO(pic.image.blob)).convert("RGBA")
            except Exception:
                continue
    return None


def extract_vi(template_path):
    vi = default_vi()
    prs = Presentation(template_path)

    try:
        theme_part = prs.slide_masters[0].part.part_related_by(RT.THEME)
        root = etree.fromstring(theme_part.blob)

        scheme = root.find(".//a:clrScheme", NS)
        if scheme is not None:
            def col(tag):
                el = scheme.find(f"a:{tag}/a:srgbClr", NS)
                if el is not None and el.get("val"):
                    return "#" + el.get("val")
                # sysClr 的实际颜色在 lastClr（val 只是系统色名如 windowText）
                el = scheme.find(f"a:{tag}/a:sysClr", NS)
                return ("#" + el.get("lastClr")) if (el is not None and el.get("lastClr")) else None
            vi["brand"] = col("accent1") or vi["brand"]
            vi["accent"] = col("accent2") or col("accent1") or vi["accent"]
            vi["dark"] = col("dk2") or col("dk1") or vi["dark"]

        fs = root.find(".//a:fontScheme", NS)
        if fs is not None:
            mj = fs.find(".//a:majorFont/a:latin", NS)
            mn = fs.find(".//a:minorFont/a:latin", NS)
            if mj is not None:
                vi["font_latin_title"] = mj.get("typeface")
            if mn is not None:
                vi["font_latin_body"] = mn.get("typeface")
    except Exception:
        pass

    vi["logo_img"] = _logo_image(prs)
    return vi
