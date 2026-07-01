"""x-to-image 长图后处理单元测试（Phase 3）。

覆盖 src/services/x_to_image/image_utils.py 的纯 Pillow 函数：
- ``_stitch_vertical``：单图 / 多图（不同宽度）/ 空列表
- ``_is_blank``：全白 / 含黑像素 / 含彩色内容
- ``_append_truncation_notice``：尺寸 +40 / 无 CJK 字体降级不崩溃
- ``finalize_long_image``：正常 / 空白检测 / 截断 / 体积控制 JPEG 回退 /
  output_name 尊重 / None 默认 uuid / 路径位于 work_dir

所有用例纯 Pillow，无需 Chromium，CI 安全。
"""
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from src.services.x_to_image.image_utils import (
    _append_truncation_notice,
    _is_blank,
    _load_cjk_font,
    _stitch_vertical,
    finalize_long_image,
)
from src.services.x_to_image.models import InputType, XToImageInput

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _save(img: Image.Image, name: str) -> str:
    """把图片写到临时目录，返回路径字符串。"""
    d = tempfile.mkdtemp(prefix="xti_iu_")
    p = os.path.join(d, name)
    img.save(p)
    return p


def _make_page(width: int, height: int, color: str = "white") -> str:
    """生成一张指定尺寸纯色 PNG，返回路径。"""
    return _save(Image.new("RGB", (width, height), color), "page.png")


# ===========================================================================
# _stitch_vertical
# ===========================================================================
class TestStitchVertical:
    def test_single_image_returns_same_dimensions(self):
        img = Image.new("RGB", (120, 80), "white")
        path = _save(img, "single.png")
        result = _stitch_vertical([path])
        assert result.size == (120, 80)

    def test_multiple_images_max_width_sum_height(self):
        # 3 张不同宽度 100/200/150，高度 50/60/40
        paths = [
            _save(Image.new("RGB", (100, 50), "white"), "a.png"),
            _save(Image.new("RGB", (200, 60), "white"), "b.png"),
            _save(Image.new("RGB", (150, 40), "white"), "c.png"),
        ]
        result = _stitch_vertical(paths)
        # 宽度 == max(100,200,150)=200，高度 == 50+60+40=150
        assert result.width == 200
        assert result.height == 150

    def test_empty_list_raises_value_error(self):
        with pytest.raises(ValueError):
            _stitch_vertical([])

    def test_narrow_images_centered_width_contract(self):
        # 较窄图不应被拉伸：宽度 == max，高度 == sum（关键契约）
        paths = [
            _save(Image.new("RGB", (50, 100), "white"), "narrow.png"),
            _save(Image.new("RGB", (200, 30), "white"), "wide.png"),
        ]
        result = _stitch_vertical(paths)
        assert result.width == 200  # max
        assert result.height == 130  # 100+30


# ===========================================================================
# _is_blank
# ===========================================================================
class TestIsBlank:
    def test_all_white_is_blank(self):
        img = Image.new("RGB", (100, 100), "white")
        assert _is_blank(img) is True

    def test_black_pixel_not_blank(self):
        img = Image.new("RGB", (100, 100), "white")
        img.putpixel((50, 50), (0, 0, 0))
        assert _is_blank(img) is False

    def test_colored_content_not_blank(self):
        img = Image.new("RGB", (100, 100), "red")
        assert _is_blank(img) is False


# ===========================================================================
# _append_truncation_notice
# ===========================================================================
class TestAppendTruncationNotice:
    def test_appends_40px_strip(self):
        img = Image.new("RGB", (200, 100), "white")
        result = _append_truncation_notice(img)
        assert isinstance(result, Image.Image)
        # 宽度不变，高度 +40
        assert result.width == 200
        assert result.height == 140

    def test_returns_valid_pil_image(self):
        img = Image.new("RGB", (50, 30), "white")
        result = _append_truncation_notice(img)
        assert isinstance(result, Image.Image)
        assert result.mode == "RGB"

    def test_no_font_does_not_crash(self, monkeypatch):
        """即使没有 CJK 字体（_load_cjk_font 返回 None），函数也应不崩溃
        返回有效图片（高度 +40）。"""
        # 强制字体加载失败 → 走 ASCII 降级路径
        monkeypatch.setattr(
            "src.services.x_to_image.image_utils._load_cjk_font",
            lambda size=18: None,
        )
        img = Image.new("RGB", (200, 100), "white")
        result = _append_truncation_notice(img)
        assert isinstance(result, Image.Image)
        assert result.width == 200
        assert result.height == 140

    def test_no_cjk_candidate_files_present(self, monkeypatch):
        """无任何候选 CJK 字体文件存在时（_load_cjk_font 返回 None），
        _append_truncation_notice 仍应走 ASCII 降级路径不崩溃。"""
        # 让所有候选字体路径判定为不存在
        monkeypatch.setattr(
            "src.services.x_to_image.image_utils.os.path.isfile", lambda p: False
        )
        assert _load_cjk_font(size=18) is None
        img = Image.new("RGB", (100, 50), "white")
        result = _append_truncation_notice(img)
        assert isinstance(result, Image.Image)
        assert result.height == 90  # 50 + 40


# ===========================================================================
# finalize_long_image
# ===========================================================================
class TestFinalizeLongImage:
    async def _finalize(self, page_paths, inp, renderer_name="ut-renderer"):
        work_dir = Path(tempfile.mkdtemp(prefix="xti_fin_"))
        try:
            result = await finalize_long_image(
                page_paths, inp, work_dir, renderer_name=renderer_name
            )
            return result, work_dir
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

    async def _run(self, page_paths, inp, renderer_name="ut-renderer"):
        return await self._finalize(page_paths, inp, renderer_name)

    async def test_normal_non_blank_page(self):
        # 100x100 白底 + 黑色方块（非空白）
        img = Image.new("RGB", (100, 100), "white")
        ImageDraw_Draw(img).rectangle((10, 10, 90, 90), fill="black")
        path = _save(img, "page.png")

        inp = XToImageInput(
            source="x", content_type=InputType.MARKDOWN,
            max_height=20000, max_file_size_mb=10,
        )
        result, work_dir = await self._finalize([path], inp)

        assert result.success is True
        assert result.error is None
        assert result.truncated is False
        assert result.renderer == "ut-renderer"
        assert result.width == 100
        assert result.height == 100
        assert result.file_size > 0
        assert result.image_path is not None
        assert Path(result.image_path).exists()
        # 位于 work_dir 内
        assert Path(result.image_path).parent == work_dir
        # 真实可打开且尺寸匹配
        with Image.open(result.image_path) as im:
            assert im.size == (result.width, result.height)
        shutil.rmtree(work_dir, ignore_errors=True)

    async def test_blank_page_detected(self):
        path = _make_page(100, 100, "white")  # 全白
        inp = XToImageInput(
            source="x", content_type=InputType.MARKDOWN,
            max_height=20000, max_file_size_mb=10,
        )
        result, work_dir = await self._finalize([path], inp)
        try:
            assert result.success is False
            assert result.error is not None
            assert "空白" in result.error
            assert result.renderer == "ut-renderer"
            assert result.image_path is None
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def test_truncation_when_height_exceeds_max(self):
        # 实际实现：先 crop 到 max_height，再加 40px 提示条 → 终高 = max_height + 40
        tall = Image.new("RGB", (100, 25000), "white")
        # 加点内容避免空白判定
        ImageDraw_Draw(tall).rectangle((0, 0, 100, 50), fill="black")
        path = _save(tall, "tall.png")

        inp = XToImageInput(
            source="x", content_type=InputType.MARKDOWN,
            max_height=20000, max_file_size_mb=20,
        )
        result, work_dir = await self._finalize([path], inp)
        try:
            assert result.success is True
            assert result.truncated is True
            # 关键契约：高度受控在 ~max_height，而非 25000
            assert result.height < 25000
            # 实现行为：crop 到 max_height(20000) 后 +40 提示条
            assert result.height == 20000 + 40
            assert result.width == 100
            assert result.file_size > 0
            with Image.open(result.image_path) as im:
                assert im.size == (100, 20000 + 40)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def test_jpeg_fallback_when_png_exceeds_limit(self, monkeypatch):
        """PNG 超 max_file_size_mb → 转 JPEG(.jpg)。用 monkeypatch 强制走回退分支，
        保证可靠（无需造巨型随机噪声图）。"""
        img = Image.new("RGB", (100, 100), "white")
        ImageDraw_Draw(img).rectangle((10, 10, 90, 90), fill="black")
        path = _save(img, "page.png")

        inp = XToImageInput(
            source="x", content_type=InputType.MARKDOWN,
            max_height=20000, max_file_size_mb=1,
        )

        # 让 os.path.getsize 首次（PNG 写入后检查）返回超大值触发 JPEG 分支
        import src.services.x_to_image.image_utils as iu

        real_getsize = os.path.getsize
        png_check_count = {"n": 0}

        def fake_getsize(p):
            # 仅对 work_dir 内的 .png 返回超大值，触发转 JPEG；其余正常
            if str(p).endswith(".png"):
                png_check_count["n"] += 1
                return 10 * 1024 * 1024  # 10MB > 1MB 限额
            return real_getsize(p)

        monkeypatch.setattr(iu.os.path, "getsize", fake_getsize)

        result, work_dir = await self._finalize([path], inp)
        try:
            assert result.success is True
            assert result.image_path.endswith(".jpg")
            assert Path(result.image_path).exists()
            assert Path(result.image_path).parent == work_dir
            assert result.file_size > 0
            # JPEG 实际体积应正常（未被 mock 影响，因为非 .png）
            assert real_getsize(result.image_path) > 0
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def test_output_name_respected(self):
        img = Image.new("RGB", (100, 100), "white")
        ImageDraw_Draw(img).rectangle((10, 10, 90, 90), fill="black")
        path = _save(img, "page.png")

        inp = XToImageInput(
            source="x", content_type=InputType.MARKDOWN,
            output_name="myreport",
            max_height=20000, max_file_size_mb=20,
        )
        result, work_dir = await self._finalize([path], inp)
        try:
            assert result.success is True
            fname = Path(result.image_path).name
            assert fname.startswith("myreport")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def test_output_name_none_defaults_uuid(self):
        img = Image.new("RGB", (100, 100), "white")
        ImageDraw_Draw(img).rectangle((10, 10, 90, 90), fill="black")
        path = _save(img, "page.png")

        inp = XToImageInput(
            source="x", content_type=InputType.MARKDOWN,
            output_name=None,
            max_height=20000, max_file_size_mb=20,
        )
        result, work_dir = await self._finalize([path], inp)
        try:
            assert result.success is True
            fname = Path(result.image_path).name
            stem = fname.split(".")[0]
            # uuid4().hex[:12] → 12 个十六进制字符
            assert len(stem) == 12
            int(stem, 16)  # 合法十六进制
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def test_image_path_inside_work_dir(self):
        img = Image.new("RGB", (100, 100), "white")
        ImageDraw_Draw(img).rectangle((10, 10, 90, 90), fill="black")
        path = _save(img, "page.png")

        inp = XToImageInput(
            source="x", content_type=InputType.MARKDOWN,
            max_height=20000, max_file_size_mb=20,
        )
        result, work_dir = await self._finalize([path], inp)
        try:
            assert result.success is True
            assert result.image_path is not None
            out_path = Path(result.image_path).resolve()
            wd = work_dir.resolve()
            # 路径必须位于 work_dir 之内
            assert wd in out_path.parents or out_path.parent == wd
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


# 局部 import 避免顶层引入（保持与文件风格一致）
def ImageDraw_Draw(img):
    from PIL import ImageDraw
    return ImageDraw.Draw(img)
