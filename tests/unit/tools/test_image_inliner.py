"""image_inliner 单元测试。

验证 src/tools/_image_inliner.py 的 inline_images：
- file_id scheme 命中 → registry.get_ref_by_file_id + resolve_local_path
- file_id 找不到 → 保留原文
- 远程 URL → registry.fetch_to_local + resolve_local_path
- 远程 URL fetch 失败 → 保留原文
- 纯文本无图 → 原样返回
- 多张图 → asyncio.gather 并发处理

所有用例 mock 掉 get_image_registry()，绝不真正调 Redis / 网络。
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.tools


# ============================================================
# 辅助：构造 mock registry
# ============================================================

def _make_image_ref(file_id: str = "file_abc123", display_name: str = "测试图.jpg"):
    """构造一个最小可用的 ImageRef。"""
    from src.core.image_asset import ImageRef
    return ImageRef(
        file_id=file_id,
        download_url=f"/api/files/{file_id}/download",
        display_name=display_name,
        width=800,
        height=600,
        mime_type="image/jpeg",
        size_bytes=1024,
        source="knowledge_base",
        usage="thumbnail",
    )


def _build_mock_registry(
    get_ref_by_file_id_side_effect=None,
    resolve_local_path_return: Path = Path("/tmp/images/test.jpg"),
    fetch_to_local_side_effect=None,
) -> MagicMock:
    """构造一个 mock ImageRegistry，所有方法都是 AsyncMock。"""
    registry = MagicMock()

    registry.get_ref_by_file_id = AsyncMock(side_effect=get_ref_by_file_id_side_effect)
    registry.resolve_local_path = AsyncMock(return_value=resolve_local_path_return)
    registry.fetch_to_local = AsyncMock(side_effect=fetch_to_local_side_effect)

    return registry


# ============================================================
# 用例 1：file_id scheme 命中，被替换为本地路径
# ============================================================

@pytest.mark.asyncio
async def test_inline_file_id_scheme_resolves_local_path():
    """file_id 命中 → 文本被替换为本地路径，refs 含该 ImageRef。"""
    from src.tools._image_inliner import inline_images

    ref = _make_image_ref(file_id="file_abc123")
    local_path = Path("/storage/tenants/t1/images/2026-07/file_abc123.jpg")
    mock_registry = _build_mock_registry(
        get_ref_by_file_id_side_effect=lambda fid: ref if fid == "file_abc123" else None,
        resolve_local_path_return=local_path,
    )

    text = "行程概览：\n\n![黄果树瀑布](file_id:file_abc123)\n\n请查阅。"

    with patch(
        "src.tools._image_inliner.get_image_registry", return_value=mock_registry
    ):
        result_text, refs = await inline_images(text, tenant_id="t1", user_id="u1")

    # 文本被替换
    assert "![黄果树瀑布](file_id:file_abc123)" not in result_text
    assert str(local_path) in result_text
    assert "黄果树瀑布" in result_text  # alt 保留

    # refs 含该 ImageRef
    assert len(refs) == 1
    assert refs[0].file_id == "file_abc123"


# ============================================================
# 用例 2：file_id 找不到，保留原文
# ============================================================

@pytest.mark.asyncio
async def test_inline_unknown_file_id_keeps_original():
    """registry.get_ref_by_file_id 返回 None → 文本不变，refs 为空。"""
    from src.tools._image_inliner import inline_images

    mock_registry = _build_mock_registry(
        get_ref_by_file_id_side_effect=lambda fid: None,
    )

    text = "![未知图](file_id:file_not_exist)"

    with patch(
        "src.tools._image_inliner.get_image_registry", return_value=mock_registry
    ):
        result_text, refs = await inline_images(text, tenant_id="t1", fetch_remote=False)

    # fetch_remote=False 防御性关闭，避免误触发 URL 替换（本用例无 URL）
    assert result_text == text
    assert refs == []


# ============================================================
# 用例 3：远程 URL 被下载并替换
# ============================================================

@pytest.mark.asyncio
async def test_inline_remote_url_fetches_and_replaces():
    """远程 URL → fetch_to_local 返回 ImageRef → 文本被替换为本地路径。"""
    from src.tools._image_inliner import inline_images

    ref = _make_image_ref(file_id="file_remote001", display_name="网络图.png")
    local_path = Path("/storage/tenants/t1/images/2026-07/file_remote001.png")
    mock_registry = _build_mock_registry(
        get_ref_by_file_id_side_effect=lambda fid: None,
        resolve_local_path_return=local_path,
        fetch_to_local_side_effect=lambda url, **kw: ref,
    )

    text = "![景点官网图](https://example.com/scenic.png)"

    with patch(
        "src.tools._image_inliner.get_image_registry", return_value=mock_registry
    ):
        result_text, refs = await inline_images(text, tenant_id="t1", user_id="u1")

    # fetch_to_local 被调用
    mock_registry.fetch_to_local.assert_awaited_once()
    call_args = mock_registry.fetch_to_local.call_args
    assert call_args.args[0] == "https://example.com/scenic.png"
    # tenant_id / user_id 被透传
    assert call_args.kwargs.get("tenant_id") == "t1"
    assert call_args.kwargs.get("user_id") == "u1"

    # 文本被替换
    assert "https://example.com/scenic.png" not in result_text
    assert str(local_path) in result_text

    # refs 含该 ImageRef
    assert len(refs) == 1
    assert refs[0].file_id == "file_remote001"


# ============================================================
# 用例 4：远程 URL fetch 失败，保留原文
# ============================================================

@pytest.mark.asyncio
async def test_inline_remote_url_fetch_failure_keeps_original():
    """fetch_to_local 抛异常 → 文本保留原 URL，refs 为空。"""
    from src.tools._image_inliner import inline_images

    mock_registry = _build_mock_registry(
        get_ref_by_file_id_side_effect=lambda fid: None,
        fetch_to_local_side_effect=RuntimeError("网络超时"),
    )

    text = "![坏图](https://broken.example.com/x.png)"

    with patch(
        "src.tools._image_inliner.get_image_registry", return_value=mock_registry
    ):
        result_text, refs = await inline_images(text, tenant_id="t1")

    # 文本保留原 URL
    assert "https://broken.example.com/x.png" in result_text
    assert result_text == text
    # refs 为空（单图失败不阻断）
    assert refs == []


# ============================================================
# 用例 5：纯文本无图，原样返回
# ============================================================

@pytest.mark.asyncio
async def test_inline_no_images_returns_text_unchanged():
    """纯文本无图 → 原样返回，refs 为空，registry 方法零调用。"""
    from src.tools._image_inliner import inline_images

    mock_registry = _build_mock_registry()

    text = "这是一段没有图片的纯文本。\n\n只有文字，没有 ![](file_id:...) 也没有 URL。"

    with patch(
        "src.tools._image_inliner.get_image_registry", return_value=mock_registry
    ):
        result_text, refs = await inline_images(text, tenant_id="t1")

    assert result_text == text
    assert refs == []
    # 无图时不应该调用 registry
    mock_registry.get_ref_by_file_id.assert_not_awaited()
    mock_registry.fetch_to_local.assert_not_awaited()


# ============================================================
# 用例 6：多张图并发处理（asyncio.gather）
# ============================================================

@pytest.mark.asyncio
async def test_inline_multiple_images_concurrent():
    """多张 file_id 图同时存在 → 全部被替换，refs 去重后含全部。

    通过对 get_ref_by_file_id 的调用次数验证每张图都被处理；
    通过检查 refs 数量验证并发结果正确收集。
    """
    from src.tools._image_inliner import inline_images

    refs_by_file_id = {
        "file_aaa": _make_image_ref(file_id="file_aaa", display_name="A.jpg"),
        "file_bbb": _make_image_ref(file_id="file_bbb", display_name="B.jpg"),
        "file_ccc": _make_image_ref(file_id="file_ccc", display_name="C.jpg"),
    }
    local_path_by_file_id = {
        "file_aaa": Path("/storage/A.jpg"),
        "file_bbb": Path("/storage/B.jpg"),
        "file_ccc": Path("/storage/C.jpg"),
    }

    async def _mock_get_ref(fid):
        return refs_by_file_id.get(fid)

    async def _mock_resolve(ref):
        return local_path_by_file_id[ref.file_id]

    mock_registry = MagicMock()
    mock_registry.get_ref_by_file_id = AsyncMock(side_effect=_mock_get_ref)
    mock_registry.resolve_local_path = AsyncMock(side_effect=_mock_resolve)

    text = (
        "# 行程\n\n"
        "![A](file_id:file_aaa)\n\n"
        "中间一段文字\n\n"
        "![B](file_id:file_bbb) 和 ![C](file_id:file_ccc)\n"
    )

    with patch(
        "src.tools._image_inliner.get_image_registry", return_value=mock_registry
    ):
        result_text, refs = await inline_images(
            text, tenant_id="t1", fetch_remote=False
        )

    # 三张图都被 get_ref_by_file_id 处理（调用 3 次）
    assert mock_registry.get_ref_by_file_id.await_count == 3
    # resolve_local_path 被调用 3 次
    assert mock_registry.resolve_local_path.await_count == 3

    # 所有原 file_id: 引用都消失了
    assert "file_id:file_aaa" not in result_text
    assert "file_id:file_bbb" not in result_text
    assert "file_id:file_ccc" not in result_text

    # 三个本地路径都出现在结果中（Path 在 Windows 上字符串化为反斜杠，统一比较）
    assert str(local_path_by_file_id["file_aaa"]) in result_text
    assert str(local_path_by_file_id["file_bbb"]) in result_text
    assert str(local_path_by_file_id["file_ccc"]) in result_text

    # refs 去重后含 3 个不同的 ImageRef
    assert len(refs) == 3
    ref_ids = {r.file_id for r in refs}
    assert ref_ids == {"file_aaa", "file_bbb", "file_ccc"}
