"""x-to-image 核心服务单元测试。

验证 src/services/x_to_image/service.py 的 XToImageService：
- 模块级单例 x_to_image_service 存在且类型正确
- register_input_type() 注册渲染器 + 日志
- convert() 在不支持类型时返回 success=False 且 error 含「不支持」
- convert() 在渲染器返回空页图列表时返回 success=False 且 error 含「渲染未产生图片」
- convert() 在渲染器抛异常时返回 success=False 且 error == str(exc)，不向上传播

所有渲染器均用桩对象 / mock，绝不真正启动 browser_pool / Chromium。
"""
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
    """渲染器记录被调用，返回占位页图列表（用于注册/日志断言，不进入 finalize）。"""

    name = "recorder"

    def __init__(self):
        self.called = False

    async def render(self, inp, work_dir):
        self.called = True
        return ["page1.png"]


def _make_input(content_type: InputType) -> XToImageInput:
    return XToImageInput(source="hello", content_type=content_type)


# ---------- 单例 ----------


def test_singleton_exists_and_is_service():
    assert x_to_image_service is not None
    assert isinstance(x_to_image_service, XToImageService)


# ---------- register_input_type ----------


def test_register_input_type_adds_to_registry_and_logs(monkeypatch):
    """register_input_type 应把渲染器加入注册表并记录一条 INFO 日志。

    注意：本项目使用 loguru，pytest 的 caplog（捕获 stdlib logging）默认抓不到
    loguru 记录，故这里直接 patch service 模块里的 logger.info 来验证调用。
    本测试只覆盖 Phase 1 范围（注册 + 日志），不走 convert() 的成功路径 ——
    该路径依赖 Phase 3 的 image_utils.finalize_long_image（尚未实现）。
    """
    import src.services.x_to_image.service as svc_mod

    logged = []

    def _capture_info(msg, *args, **kwargs):
        logged.append(msg)

    monkeypatch.setattr(svc_mod.logger, "info", _capture_info)

    svc = XToImageService()
    renderer = _RecordingRenderer()
    svc.register_input_type(InputType.MARKDOWN, renderer)

    # 注册表包含该渲染器（key 为 InputType.MARKDOWN）
    assert svc._renderers[InputType.MARKDOWN] is renderer
    # 注册产生一条含「注册」关键词的 INFO 日志
    assert any("注册" in m for m in logged), f"未在日志中找到注册记录: {logged}"


async def test_convert_invokes_registered_renderer(monkeypatch):
    """注册成功后，convert 应调用对应渲染器的 render()。

    通过注入一个「记录调用」的渲染器，并 patch 掉 service.convert 内部对
    image_utils.finalize_long_image 的延迟导入（Phase 3 尚未实现），
    验证渲染器确被调用且 page_paths 被传递给拼接逻辑。
    若 Phase 3 已落地，本测试仍兼容（patch 优先级高于真实实现）。
    """
    import sys
    from unittest.mock import AsyncMock
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


async def test_convert_unsupported_type_returns_failure_with_buzhichi():
    svc = XToImageService()
    # 用一个未注册的 InputType（TEXT 默认未注册，因 Phase 1 _register_builtin 为空）
    # 为稳健，清空注册表，确保 TEXT 未注册
    svc._renderers.clear()
    result = await svc.convert(_make_input(InputType.TEXT))
    assert isinstance(result, XToImageResult)
    assert result.success is False
    assert result.error is not None
    assert "不支持" in result.error, f"error 应包含「不支持」, 实际: {result.error!r}"


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


async def test_convert_renderer_raises_returns_failure_without_propagation():
    svc = XToImageService()
    svc._renderers.clear()
    boom = _ErrorRenderer(RuntimeError("simulated renderer failure"))
    svc.register_input_type(InputType.TEXT, boom)

    result = await svc.convert(_make_input(InputType.TEXT))
    assert isinstance(result, XToImageResult)
    assert result.success is False
    # 异常被捕获，error == str(exception)，不向上传播
    assert result.error == "simulated renderer failure"


async def test_convert_does_not_raise_on_renderer_exception():
    """convert 不应把渲染器异常向上传播（调用方只看到 XToImageResult）。"""
    svc = XToImageService()
    svc._renderers.clear()
    svc.register_input_type(
        InputType.TEXT, _ErrorRenderer(ValueError("any error"))
    )
    # 不应抛出
    result = await svc.convert(_make_input(InputType.TEXT))
    assert result.success is False


# ---------- convert: browser_pool 不应被启动 ----------


async def test_convert_with_mocked_renderer_does_not_launch_browser(monkeypatch):
    """注册表/错误路径测试中，browser_pool.shoot 不应被调用。

    通过给 browser_pool 植入一个会失败的 shoot mock 来守护：
    若有路径误触发了真实截图，测试会因 mock 抛错而失败。
    """
    from src.services.x_to_image.renderers import browser_pool as bp_mod

    async def _must_not_shoot(*a, **k):
        raise AssertionError("单元测试不应调用 browser_pool.shoot")

    monkeypatch.setattr(bp_mod.browser_pool, "shoot", _must_not_shoot)

    # 不支持类型 —— 不应触达 shoot
    svc = XToImageService()
    svc._renderers.clear()
    await svc.convert(_make_input(InputType.TEXT))
