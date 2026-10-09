# -*- coding: utf-8 -*-
"""企业微信客服 markdown 长图渲染器单测。

背景（2026-10-09 线上案例 tr_runner_0d5d611b377346bcbe47e3e009d2aff4）：
browser_pool 用 set_content 加载页面（origin=about:blank），Chromium 禁止此类
页面加载 file:// 本地资源，旧实现把 `![alt](file_id:x)` 替换成 `![alt](file:///...)`
后图片全部破图。修复后 markdown→HTML 再把 `<img src>` 内联为 base64 data URI。

全 mock，不真正启动 Chromium / Redis / 网络。
"""
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.channels.wecom_kf.renderer import WeComKfRenderer

pytestmark = pytest.mark.channels


# ============================================================
# 辅助
# ============================================================

def _make_image_ref(file_id: str = "file_abc123"):
    from src.core.image_asset import ImageRef
    return ImageRef(
        file_id=file_id,
        download_url=f"/api/files/{file_id}/download",
        display_name="测试图.png",
        width=100,
        height=50,
        mime_type="image/png",
        size_bytes=1024,
        source="knowledge_base",
        usage="thumbnail",
    )


def _build_mock_registry(get_ref_side_effect=None, local_path=None, fetch_side_effect=None):
    registry = AsyncMock()
    registry.get_ref_by_file_id = AsyncMock(side_effect=get_ref_side_effect)
    registry.resolve_local_path = AsyncMock(return_value=local_path or Path("/nonexistent/x.png"))
    registry.fetch_to_local = AsyncMock(side_effect=fetch_side_effect)
    return registry


@pytest.fixture
def tiny_png(tmp_path: Path) -> Path:
    """生成一张真实小 PNG 供 data URI 读取。"""
    from PIL import Image
    p = tmp_path / "tiny.png"
    Image.new("RGB", (4, 2), (255, 0, 0)).save(p)
    return p


@pytest.fixture
def browser_stub(monkeypatch, tmp_path: Path):
    """mock browser_pool 单例：is_available=True，shoot 捕获 html 并返回占位路径。"""
    import src.services.x_to_image.renderers.browser_pool as bp_mod

    captured: dict = {}

    async def _fake_shoot(html, width, out_dir):
        captured["html"] = html
        return str(tmp_path / "page.png")

    monkeypatch.setattr(bp_mod.browser_pool, "is_available", AsyncMock(return_value=True))
    monkeypatch.setattr(bp_mod.browser_pool, "shoot", _fake_shoot)
    return captured


@pytest.fixture
def finalize_stub(monkeypatch, tmp_path: Path):
    """mock finalize_long_image：返回成功结果，产物文件已存在。"""
    import src.services.x_to_image.image_utils as iu_mod

    out_png = tmp_path / "md_xxx.png"
    out_png.write_bytes(b"fake-png")
    stub = SimpleNamespace(success=True, image_path=str(out_png), file_size=9, truncated=False)
    monkeypatch.setattr(iu_mod, "finalize_long_image", AsyncMock(return_value=stub))
    monkeypatch.setattr(WeComKfRenderer, "_resolve_save_dir", lambda self: str(tmp_path))
    return stub


MD_WITH_IMAGE = """## 景点图集

| 天数 | 行程 |
|------|------|
| D1 | 接站 |

![天空之桥研学基地](file_id:file_abc123)

结尾文字。
"""


# ============================================================
# render_markdown 全链路（mock 浏览器与后处理）
# ============================================================

class TestRenderMarkdownImageInline:
    async def test_file_id_image_inlined_as_data_uri(self, tiny_png, browser_stub, finalize_stub):
        """file_id 引用 → HTML 中内联 base64 data URI，无 file:// / file_id: 残留。"""
        renderer = WeComKfRenderer(tenant_id="t1")
        registry = _build_mock_registry(
            get_ref_side_effect=lambda fid: _make_image_ref(fid) if fid == "file_abc123" else None,
            local_path=tiny_png,
        )
        with patch("src.tools._image_inliner.get_image_registry", return_value=registry):
            result = await renderer.render_markdown(MD_WITH_IMAGE)

        assert result is not None and Path(result).exists()
        html = browser_stub["html"]
        assert 'src="data:image/png;base64,' in html
        assert "file_id:" not in html
        assert "file://" not in html
        # 表格正常进入渲染 HTML
        assert "<table>" in html

    async def test_unknown_file_id_keeps_src_and_still_renders(
        self, tiny_png, browser_stub, finalize_stub
    ):
        """file_id 查不到 → 保留原 src（破图降级），长图仍生成不阻断。"""
        renderer = WeComKfRenderer(tenant_id="t1")
        registry = _build_mock_registry(get_ref_side_effect=lambda fid: None)
        with patch("src.tools._image_inliner.get_image_registry", return_value=registry):
            result = await renderer.render_markdown(MD_WITH_IMAGE)

        assert result is not None
        assert 'src="file_id:file_abc123"' in browser_stub["html"]

    async def test_inliner_crash_falls_back_to_original_html(
        self, tiny_png, browser_stub, finalize_stub
    ):
        """内联器整体异常 → 回退原始 HTML，长图仍生成。"""
        renderer = WeComKfRenderer(tenant_id="t1")
        with patch(
            "src.tools._image_inliner.get_image_registry",
            side_effect=RuntimeError("registry boom"),
        ):
            result = await renderer.render_markdown(MD_WITH_IMAGE)

        assert result is not None
        assert 'src="file_id:file_abc123"' in browser_stub["html"]


# ============================================================
# _inline_images_as_data_uri（不经浏览器）
# ============================================================

class TestInlineDataUriDirect:
    async def test_remote_url_with_tenant_fetched_and_inlined(self, tiny_png):
        """有租户：远程 URL 下载（fetch_to_local）后内联为 data URI。"""
        renderer = WeComKfRenderer(tenant_id="t1")
        ref = _make_image_ref("file_fetch1")
        registry = _build_mock_registry(
            fetch_side_effect=lambda url, **kw: ref, local_path=tiny_png,
        )
        html = '<p>图</p><img alt="a" src="https://example.com/a.png" />'
        with patch("src.tools._image_inliner.get_image_registry", return_value=registry):
            out = await renderer._inline_images_as_data_uri(html)

        assert 'src="data:image/png;base64,' in out
        assert "https://example.com/a.png" not in out
        registry.fetch_to_local.assert_awaited_once()

    async def test_remote_url_anonymous_kept_original(self):
        """匿名（无租户）不下载远程图：URL 原样保留，由 Chromium 自行加载。"""
        renderer = WeComKfRenderer(tenant_id="")
        registry = _build_mock_registry()
        html = '<img alt="a" src="https://example.com/a.png" />'
        with patch("src.tools._image_inliner.get_image_registry", return_value=registry):
            out = await renderer._inline_images_as_data_uri(html)

        assert out == html
        registry.fetch_to_local.assert_not_awaited()

    async def test_data_uri_bytes_match_source_file(self, tiny_png):
        """内联的 base64 内容与源文件字节一致（防止只换前缀不换内容）。"""
        renderer = WeComKfRenderer(tenant_id="t1")
        registry = _build_mock_registry(
            get_ref_side_effect=lambda fid: _make_image_ref(fid) if fid == "file_abc123" else None,
            local_path=tiny_png,
        )
        html = '<img alt="t" src="file_id:file_abc123" />'
        with patch("src.tools._image_inliner.get_image_registry", return_value=registry):
            out = await renderer._inline_images_as_data_uri(html)

        marker = 'src="data:image/png;base64,'
        b64 = out.split(marker, 1)[1].rsplit('"', 1)[0]
        assert base64.b64decode(b64) == tiny_png.read_bytes()
