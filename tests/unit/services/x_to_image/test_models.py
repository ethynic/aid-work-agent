"""x-to-image 数据模型单元测试。

验证 src/services/x_to_image/models.py 的：
- InputType / ImageFormat 枚举取值
- XToImageInput 默认值
- XToImageResult 默认值（success 为必填位置参数）
- XToImageInput.extra 每实例独立（default_factory 不共享）
"""
import pytest

from src.services.x_to_image.models import (
    ImageFormat,
    InputType,
    XToImageInput,
    XToImageResult,
)

pytestmark = pytest.mark.unit


# ---------- 枚举 ----------


class TestInputType:
    def test_text_value(self):
        assert InputType.TEXT.value == "text"

    def test_markdown_value(self):
        assert InputType.MARKDOWN.value == "markdown"

    def test_html_value(self):
        assert InputType.HTML.value == "html"

    def test_is_str_enum(self):
        # 设计 §5.3：InputType(str, Enum)，且取值可直接当字符串使用
        assert isinstance(InputType.TEXT, str)
        assert InputType.TEXT == "text"


class TestImageFormat:
    def test_png_value(self):
        assert ImageFormat.PNG.value == "png"

    def test_jpeg_value(self):
        assert ImageFormat.JPEG.value == "jpeg"

    def test_is_str_enum(self):
        assert isinstance(ImageFormat.PNG, str)
        assert ImageFormat.PNG == "png"


# ---------- XToImageInput ----------


class TestXToImageInput:
    def test_required_fields(self):
        inp = XToImageInput(source="hi", content_type=InputType.TEXT)
        assert inp.source == "hi"
        assert inp.content_type is InputType.TEXT

    def test_default_width(self):
        inp = XToImageInput(source="hi", content_type=InputType.TEXT)
        assert inp.width == 800

    def test_default_max_height(self):
        inp = XToImageInput(source="hi", content_type=InputType.TEXT)
        assert inp.max_height == 20_000

    def test_default_max_file_size_mb(self):
        inp = XToImageInput(source="hi", content_type=InputType.TEXT)
        assert inp.max_file_size_mb == 10

    def test_default_image_format_is_png(self):
        inp = XToImageInput(source="hi", content_type=InputType.TEXT)
        assert inp.image_format is ImageFormat.PNG

    def test_default_output_name_is_none(self):
        inp = XToImageInput(source="hi", content_type=InputType.TEXT)
        assert inp.output_name is None

    def test_default_is_file_path_is_false(self):
        inp = XToImageInput(source="hi", content_type=InputType.TEXT)
        assert inp.is_file_path is False

    def test_extra_default_is_empty_dict(self):
        inp = XToImageInput(source="hi", content_type=InputType.TEXT)
        assert inp.extra == {}

    def test_extra_default_not_shared_between_instances(self):
        """default_factory 必须为每个实例生成新 dict —— 防止可变默认值共享 bug。"""
        a = XToImageInput(source="a", content_type=InputType.TEXT)
        b = XToImageInput(source="b", content_type=InputType.TEXT)
        a.extra["k"] = "v"
        assert "k" not in b.extra, "extra 在实例间被错误共享（可变默认值 bug）"
        assert b.extra == {}

    def test_extra_can_be_provided(self):
        inp = XToImageInput(
            source="hi", content_type=InputType.TEXT, extra={"bg": "#fff"}
        )
        assert inp.extra == {"bg": "#fff"}

    def test_overrides_applied(self):
        inp = XToImageInput(
            source="hi",
            content_type=InputType.MARKDOWN,
            width=1200,
            max_height=5000,
            max_file_size_mb=5,
            image_format=ImageFormat.JPEG,
            is_file_path=True,
            output_name="out",
        )
        assert inp.width == 1200
        assert inp.max_height == 5000
        assert inp.max_file_size_mb == 5
        assert inp.image_format is ImageFormat.JPEG
        assert inp.is_file_path is True
        assert inp.output_name == "out"


# ---------- XToImageResult ----------


class TestXToImageResult:
    def test_success_is_required_positional(self):
        # success 没有默认值 —— 不传应抛错
        with pytest.raises(TypeError):
            XToImageResult()  # type: ignore[call-arg]

    def test_defaults_on_success_true(self):
        r = XToImageResult(success=True)
        assert r.success is True
        assert r.image_path is None
        assert r.width == 0
        assert r.height == 0
        assert r.file_size == 0
        assert r.truncated is False
        assert r.renderer == ""
        assert r.error is None

    def test_defaults_on_success_false(self):
        r = XToImageResult(success=False)
        assert r.success is False
        assert r.error is None

    def test_error_field_settable(self):
        r = XToImageResult(success=False, error="boom")
        assert r.success is False
        assert r.error == "boom"
