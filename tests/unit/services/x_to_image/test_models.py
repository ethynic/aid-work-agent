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
    def test_string_values(self):
        assert InputType.TEXT.value == "text"
        assert InputType.MARKDOWN.value == "markdown"
        assert InputType.HTML.value == "html"
        # 设计 §5.3：InputType(str, Enum)，且取值可直接当字符串使用
        assert isinstance(InputType.TEXT, str)
        assert InputType.TEXT == "text"


class TestImageFormat:
    def test_string_values(self):
        assert ImageFormat.PNG.value == "png"
        assert ImageFormat.JPEG.value == "jpeg"
        assert isinstance(ImageFormat.PNG, str)
        assert ImageFormat.PNG == "png"


# ---------- XToImageInput ----------


class TestXToImageInput:
    def test_defaults(self):
        inp = XToImageInput(source="hi", content_type=InputType.TEXT)
        assert inp.source == "hi"
        assert inp.content_type is InputType.TEXT

        assert inp.width == 800
        assert inp.max_height == 20_000
        assert inp.max_file_size_mb == 10
        assert inp.image_format is ImageFormat.PNG
        assert inp.output_name is None
        assert inp.is_file_path is False
        assert inp.extra == {}

    def test_extra_default_not_shared_between_instances(self):
        """default_factory 必须为每个实例生成新 dict —— 防止可变默认值共享 bug。"""
        a = XToImageInput(source="a", content_type=InputType.TEXT)
        b = XToImageInput(source="b", content_type=InputType.TEXT)
        a.extra["k"] = "v"
        assert "k" not in b.extra, "extra 在实例间被错误共享（可变默认值 bug）"
        assert b.extra == {}

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
            extra={"bg": "#fff"},
        )
        assert inp.width == 1200
        assert inp.max_height == 5000
        assert inp.max_file_size_mb == 5
        assert inp.image_format is ImageFormat.JPEG
        assert inp.is_file_path is True
        assert inp.output_name == "out"
        assert inp.extra == {"bg": "#fff"}


# ---------- XToImageResult ----------


class TestXToImageResult:
    def test_success_is_required_positional(self):
        # success 没有默认值 —— 不传应抛错
        with pytest.raises(TypeError):
            XToImageResult()  # type: ignore[call-arg]

    @pytest.mark.parametrize("success", [True, False])
    def test_defaults(self, success):
        r = XToImageResult(success=success)
        assert r.success is success
        assert r.image_path is None
        assert r.width == 0
        assert r.height == 0
        assert r.file_size == 0
        assert r.truncated is False
        assert r.renderer == ""
        assert r.error is None

    def test_error_field_settable(self):
        r = XToImageResult(success=False, error="boom")
        assert r.success is False
        assert r.error == "boom"
