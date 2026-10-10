"""browser_pool 单元测试。

验证 src/services/x_to_image/renderers/browser_pool.py：
- is_available() 返回 bool 且缓存（两次调用不抛异常、结果一致）
- _ensure_browser 中 headless=True 为硬编码字面量（通过读源码字符串匹配守护）
- 不在单元测试中真正启动 Chromium，真实截图由 real_browser 套件覆盖
"""
from pathlib import Path

import pytest

from src.services.x_to_image.renderers.browser_pool import BrowserPool, browser_pool

pytestmark = pytest.mark.unit


# ---------- is_available ----------


async def test_is_available_is_cached_and_consistent():
    first = await browser_pool.is_available()
    second = await browser_pool.is_available()
    # 缓存命中：第二次直接返回，且与第一次一致
    assert first == second
    assert isinstance(first, bool)
    assert browser_pool._available is first  # 缓存已被写入


async def test_is_available_caches_after_first_call(monkeypatch):
    """第一次调用后，后续调用应命中缓存，不再触发 import 探测。

    用一个全新的 BrowserPool 实例验证缓存机制：首次调用后 _available 被设置，
    再 monkeypatch 掉 import 探测路径，第二次调用结果应不变（来自缓存）。
    """
    pool = BrowserPool()
    first = await pool.is_available()
    assert pool._available is first

    # 即便禁用 __import__，缓存命中也不应受影响
    import builtins

    real_import = builtins.__import__

    def _block_playwright(name, *args, **kwargs):
        if name.startswith("playwright"):
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_playwright)
    second = await pool.is_available()
    assert second == first, "缓存后再次调用结果应一致"


# ---------- headless 硬编码守护 ----------


def test_headless_true_is_hardcoded_literal_in_source():
    """读取 browser_pool.py 源码，断言 `headless=True` 作为字面量出现。

    这守护设计 §5.5 / N2 的硬编码要求：headless 必须恒为 True，
    不接受外部参数覆盖。
    """
    src_path = Path(browser_pool.__module__.replace(".", "/") + ".py")
    # 通过实例拿到所在文件，避免依赖 CWD
    import src.services.x_to_image.renderers.browser_pool as bp_mod

    source = Path(bp_mod.__file__).read_text(encoding="utf-8")

    assert "headless=True" in source, (
        "browser_pool 源码中未找到 `headless=True` 字面量 —— 违反 N2 强制 headless 约定"
    )


def test_headless_not_parameterized_from_outside():
    """进一步守护：BrowserPool 没有把 headless 暴露为构造参数或方法参数。"""
    import inspect

    # __init__ 不应有 headless 相关参数
    init_sig = inspect.signature(BrowserPool.__init__)
    init_params = set(init_sig.parameters) - {"self"}
    assert not any("headless" in p.lower() for p in init_params), (
        f"BrowserPool.__init__ 不应暴露 headless 参数: {init_params}"
    )

    # shoot 不应有 headless 相关参数
    shoot_sig = inspect.signature(BrowserPool.shoot)
    shoot_params = set(shoot_sig.parameters) - {"self"}
    assert not any("headless" in p.lower() for p in shoot_params), (
        f"BrowserPool.shoot 不应暴露 headless 参数: {shoot_params}"
    )


# ---------- acquire_page（async context manager） ----------


def test_acquire_page_signature_no_headless():
    """acquire_page 不应暴露 headless 参数（遵循 N2 硬编码约定）。"""
    import inspect

    sig = inspect.signature(BrowserPool.acquire_page)
    params = set(sig.parameters) - {"self"}
    assert "width" in params, "acquire_page 应接受 width 参数"
    assert not any("headless" in p.lower() for p in params), (
        f"BrowserPool.acquire_page 不应暴露 headless 参数: {params}"
    )


def test_acquire_page_is_async_context_manager():
    """acquire_page 返回的对象应实现 __aenter__/__aexit__（async context manager）。

    用一个全新的 BrowserPool 实例验证（不真正启动浏览器，只验证协议）。
    """
    pool = BrowserPool()
    # 调用 acquire_page 拿到 context manager 对象（不进入 __aenter__）
    cm = pool.acquire_page(width=440)
    assert hasattr(cm, "__aenter__"), "acquire_page 返回对象必须实现 __aenter__"
    assert hasattr(cm, "__aexit__"), "acquire_page 返回对象必须实现 __aexit__"
