"""
x-to-image 长图后处理（Pillow）。

主入口 ``finalize_long_image``：把渲染器产出的页图列表纵向拼接为单张长图，
做空白检测、高度截断、体积控制，最终写入临时工作目录并返回 XToImageResult。

参见设计文档 §5.7。

所有 Pillow 操作均为同步且耗时较短（通常 <100ms），故在 ``finalize_long_image``
内部直接调用即可，无需 run_in_executor。各操作本身不做 try/except 兜底：
- 预期的「空白图」失败以 ``XToImageResult(success=False)`` 返回；
- 其它意外异常（如损坏的 PNG）让其向上抛出，交由 ``service.convert`` 的
  try/except 捕获并转为干净的失败结果（见 service.py）。
唯一必须全程防御的是 ``_append_truncation_notice`` 的字体加载——绝不能因
缺 CJK 字体导致整个流程崩溃。
"""
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, List

from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from src.services.x_to_image.models import XToImageInput, XToImageResult

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# 拼接
# ---------------------------------------------------------------------------
def _stitch_vertical(page_paths: List[str]) -> Image.Image:
    """纵向拼接多张页图。

    - 单张: 直接 ``Image.open`` 并转 RGB 返回；
    - 多张: 以最大宽度为画布宽度，各图水平居中 paste 到白色背景上，高度累加；
    - 处理不同宽度: 较窄图居中放置，左右留白（避免拼接错位）。

    Args:
        page_paths: 页图文件路径列表。

    Returns:
        拼接后的 RGB ``Image.Image``。

    Raises:
        ValueError: ``page_paths`` 为空。
    """
    if not page_paths:
        raise ValueError("page_paths 不能为空")

    # 逐张打开并转 RGB（避免 RGBA/mode 不一致导致 paste 出错；白色背景）
    images: List[Image.Image] = [Image.open(p).convert("RGB") for p in page_paths]

    # 单张：直接返回（注意：不能关闭它，调用方还要用）
    if len(images) == 1:
        return images[0]

    # 多张：以最大宽度为画布宽度，各图水平居中 paste 到白色背景，高度累加。
    # canvas 是独立新图，paste 完即可释放各源图。
    canvas_width = max(img.width for img in images)
    canvas_height = sum(img.height for img in images)
    canvas = Image.new("RGB", (canvas_width, canvas_height), "white")

    y_offset = 0
    for img in images:
        x = (canvas_width - img.width) // 2  # 水平居中
        canvas.paste(img, (x, y_offset))
        y_offset += img.height
        img.close()
    return canvas


# ---------------------------------------------------------------------------
# 空白检测
# ---------------------------------------------------------------------------
def _is_blank(img: Image.Image) -> bool:
    """检测图片是否空白（全白）。

    用 ``img.convert("L").getextrema()`` —— 若 ``min == max == 255``（全白）
    判为空白。采用严格精确匹配（参考设计 §5.7：blank = 白/近白）。

    Args:
        img: 待检测图片。

    Returns:
        True 表示空白。
    """
    try:
        extrema = img.convert("L").getextrema()
    except Exception as e:
        logger.warning(f"[XToImage] 空白检测失败，按非空处理: {e}")
        return False
    # L 模式下 getextrema 返回 (min, max)
    return extrema == (255, 255)


# ---------------------------------------------------------------------------
# 截断提示
# ---------------------------------------------------------------------------
# 候选 CJK 字体路径（按优先级尝试）；Docker 装 fonts-noto-cjk、Windows 用微软雅黑等。
# 找不到任何一个时，回退到纯 ASCII 提示 + 默认字体，确保绝不崩溃。
_CJK_FONT_CANDIDATES = (
    # Linux (Docker: fonts-noto-cjk)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    # Windows
    "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
    "C:/Windows/Fonts/msyh.ttf",
    "C:/Windows/Fonts/simhei.ttf",     # 黑体
    "C:/Windows/Fonts/simsun.ttc",     # 宋体
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
)


def _load_cjk_font(size: int = 18):
    """尝试加载一个 CJK-capable TrueType 字体；找不到则返回 None。

    全程 try/except，绝不抛异常。
    """
    for path in _CJK_FONT_CANDIDATES:
        try:
            if os.path.isfile(path):
                return ImageFont.truetype(path, size=size)
        except Exception as e:
            logger.debug(f"[XToImage] 加载字体失败 {path}: {e}")
            continue
    return None


def _append_truncation_notice(
    img: Image.Image, notice: str = "⚠ 内容已截断"
) -> Image.Image:
    """在图片底部追加一条高 40px、灰底白字的截断提示横条。

    字体加载必须全程防御：找不到 CJK 字体时回退为纯 ASCII 提示
    ``[truncated]`` + 默认字体，**绝不因字体问题崩溃**。

    Args:
        img: 原图（RGB）。
        notice: 提示文本（默认含 CJK + emoji）。

    Returns:
        新的 RGB Image（原图 + 底部 40px 提示条）。
    """
    strip_height = 40
    strip = Image.new("RGB", (img.width, strip_height), "#888888")
    draw = ImageDraw.Draw(strip)

    # 1. 尝试加载 CJK 字体；失败则降级为纯 ASCII 提示 + 默认字体
    font = _load_cjk_font(size=18)
    text = notice
    if font is None:
        text = "[truncated]"
        try:
            font = ImageFont.load_default()
        except Exception as e:
            logger.warning(f"[XToImage] 默认字体也加载失败，提示条不绘文字: {e}")
            font = None

    # 2. 居中绘制文字（绘制本身也可能抛异常，需兜底）
    if font is not None:
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            x = (strip.width - text_w) / 2 - bbox[0]
            y = (strip.height - text_h) / 2 - bbox[1]
            draw.text((x, y), text, fill="white", font=font)
        except Exception as e:
            logger.warning(f"[XToImage] 截断提示文字绘制失败，保留空白灰条: {e}")

    # 3. 拼接：原图在上，提示条在下
    new = Image.new("RGB", (img.width, img.height + strip_height))
    new.paste(img, (0, 0))
    new.paste(strip, (0, img.height))
    return new


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
async def finalize_long_image(
    page_paths: List[str],
    inp: "XToImageInput",
    work_dir: "Path",
    renderer_name: str = "",
) -> "XToImageResult":
    """主流程: 拼接 → 空白检测 → 高度截断 → 体积控制 → 写入 work_dir → 返回结果。

    Args:
        page_paths: 渲染器产出的页图 PNG 路径列表。
        inp: 转换输入（取 max_height / max_file_size_mb / output_name）。
        work_dir: 本次转换的临时工作目录，最终长图写入其中。
        renderer_name: 实际使用的渲染器名（透传到结果）。

    Returns:
        ``XToImageResult``：
        - 渲染结果为空白 → ``success=False, error="渲染结果为空白图片"``；
        - 正常 → ``success=True`` + 临时图片绝对路径 + 元信息；
        - 其它意外异常（如损坏 PNG）向上抛出，由 ``service.convert`` 捕获。
    """
    # 1. 纵向拼接（同步 Pillow 操作，耗时短，直接调用即可）
    long_img = _stitch_vertical(page_paths)

    # 2. 空白检测
    if _is_blank(long_img):
        logger.warning(f"[XToImage] 渲染结果为空白图片 (renderer={renderer_name})")
        return XToImageResult(
            success=False,
            error="渲染结果为空白图片",
            renderer=renderer_name,
        )

    # 3. 高度截断
    truncated = False
    if long_img.height > inp.max_height:
        long_img = long_img.crop((0, 0, long_img.width, inp.max_height))
        long_img = long_img.convert("RGB")  # crop 后转 RGB 保险（提示条要求 RGB）
        long_img = _append_truncation_notice(long_img)
        truncated = True
        logger.info(
            f"[XToImage] 长图高度超限({inp.max_height}px)已截断并附加提示 "
            f"(renderer={renderer_name})"
        )

    # 4. 写入 PNG
    name = inp.output_name or uuid.uuid4().hex[:12]
    out_path = work_dir / f"{name}.png"
    long_img.save(str(out_path), format="PNG")

    # 5. 体积控制：PNG 超 max_file_size_mb 则转 JPEG(quality=85)
    max_bytes = inp.max_file_size_mb * 1024 * 1024
    if os.path.getsize(out_path) > max_bytes:
        out_path = work_dir / f"{name}.jpg"
        long_img.save(str(out_path), format="JPEG", quality=85)
        logger.info(
            f"[XToImage] PNG 体积超限({inp.max_file_size_mb}MB)，已转 JPEG(quality=85)"
        )

    # 6. 返回结果
    return XToImageResult(
        success=True,
        image_path=str(out_path.absolute()),
        width=long_img.width,
        height=long_img.height,
        file_size=os.path.getsize(out_path),
        truncated=truncated,
        renderer=renderer_name,
    )
