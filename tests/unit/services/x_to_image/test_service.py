"""x-to-image 核心服务单元测试。

验证 src/services/x_to_image/service.py 的 XToImageService：
- register_input_type() 注册后实际分派渲染与图片后处理
- convert() 在不支持类型时返回 success=False 且 error 含「不支持」
- convert() 在渲染器返回空页图列表时返回 success=False 且 error 含「渲染未产生图片」
- convert() 在渲染器抛异常时返回 success=False 且 error == str(exc)，不向上传播

所有渲染器均用桩对象 / mock，绝不真正启动 browser_pool / Chromium。
"""
from unittest.mock import AsyncMock

import pytest

from src.services.x_to_image.models import InputType, XToImageInput, XToImageResult
from src.services.x_to_image.service import XToImageService, x_to_image_service
from src.services.x_to_image.renderers.base import ImageRendererBase

pytestmark = pytest.mark.unit


# ---------- 渲染器桩 ----------


class _EmptyRenderer(ImageRendererBase):
    """渲染器返回空页图列表。"""

    name = "empty"

    async def render(self, inp, work_dir):
        return []


class _ErrorRenderer(ImageRendererBase):
    """渲染器抛出固定异常。"""

    name = "boom"

    def __init__(self, exc):
        self.exc = exc

    async def render(self, inp, work_dir):
        raise self.exc


class _RecordingRenderer(ImageRendererBase):
    """记录调用并返回占位页图，用于验证分派及后处理参数。"""

    name = "recorder"

    def __init__(self):
        self.called = False

    async def render(self, inp, work_dir):
        self.called = True
        return ["page1.png"]


def _make_input(content_type: InputType) -> XToImageInput:
    return XToImageInput(source="hello", content_type=content_type)


# ---------- register_input_type ----------


def test_renderer_names_match_input_types():
    """默认注册与内容类型一致，无需启动浏览器。"""
    renderers = x_to_image_service._renderers
    assert renderers[InputType.TEXT].name == "text"
    assert renderers[InputType.MARKDOWN].name == "markdown"
    assert renderers[InputType.HTML].name == "html"


async def test_convert_invokes_registered_renderer(monkeypatch):
    """注册成功后，convert 应调用对应渲染器的 render()。

    通过注入一个「记录调用」的渲染器，并 patch 掉 service.convert 内部对
    image_utils.finalize_long_image 的延迟导入，验证 render 确被调用且
    page_paths 被传递给后处理边界；图片拼接本身由 image_utils 测试验证。
    """
    import sys
    import types

    svc = XToImageService()
    renderer = _RecordingRenderer()
    svc.register_input_type(InputType.TEXT, renderer)

    # 构造一个 stub image_utils 模块注入 sys.modules，使 service.convert 内的
    # `from .image_utils import finalize_long_image` 拿到我们的桩实现。
    finalize_calls = {}

    async def _fake_finalize(page_paths, inp, work_dir, renderer_name):
        finalize_calls["page_paths"] = page_paths
        finalize_calls["renderer_name"] = renderer_name
        return XToImageResult(success=True, renderer=renderer_name)

    stub_iu = types.ModuleType("src.services.x_to_image.image_utils")
    stub_iu.finalize_long_image = _fake_finalize
    monkeypatch.setitem(sys.modules, "src.services.x_to_image.image_utils", stub_iu)

    result = await svc.convert(_make_input(InputType.TEXT))

    # 渲染器被调用
    assert renderer.called is True
    # 桩 finalize 被调用，且 page_paths 被正确传递
    assert finalize_calls.get("page_paths") == ["page1.png"]
    assert finalize_calls.get("renderer_name") == "recorder"
    # 结果由桩返回
    assert result.success is True
    assert result.renderer == "recorder"


# ---------- convert: 不支持的输入类型 ----------


async def test_convert_unsupported_type_returns_failure_without_browser(monkeypatch):
    from src.services.x_to_image.renderers import browser_pool as bp_mod

    shoot = AsyncMock()
    monkeypatch.setattr(bp_mod.browser_pool, "shoot", shoot)
    svc = XToImageService()
    # 当前 TEXT 默认已注册，清空注册表以验证不支持类型分支。
    svc._renderers.clear()
    result = await svc.convert(_make_input(InputType.TEXT))
    assert isinstance(result, XToImageResult)
    assert result.success is False
    assert result.error is not None
    assert "不支持" in result.error, f"error 应包含「不支持」, 实际: {result.error!r}"
    shoot.assert_not_called()


# ---------- convert: 渲染器返回空列表 ----------


async def test_convert_empty_page_list_returns_failure():
    svc = XToImageService()
    svc._renderers.clear()
    svc.register_input_type(InputType.HTML, _EmptyRenderer())

    result = await svc.convert(_make_input(InputType.HTML))
    assert isinstance(result, XToImageResult)
    assert result.success is False
    assert result.error is not None
    assert "渲染未产生图片" in result.error, f"error 应包含「渲染未产生图片」, 实际: {result.error!r}"
    assert result.renderer == "empty"


# ---------- convert: 渲染器抛异常 ----------


@pytest.mark.parametrize("error", [RuntimeError("simulated renderer failure"), ValueError("any error")])
async def test_convert_renderer_raises_returns_failure_without_propagation(error):
    svc = XToImageService()
    svc._renderers.clear()
    boom = _ErrorRenderer(error)
    svc.register_input_type(InputType.TEXT, boom)

    result = await svc.convert(_make_input(InputType.TEXT))
    assert isinstance(result, XToImageResult)
    assert result.success is False
    # 异常被捕获，error == str(exception)，不向上传播
    assert result.error == str(error)
