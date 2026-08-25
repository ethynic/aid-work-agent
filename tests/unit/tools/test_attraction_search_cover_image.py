"""
景点搜索工具 cover_image 字段单元测试

验证 attraction_search_tool 在搜索结果中正确返回 cover_image 字段：
- metadata.images.cover 存在时返回 ImageRef dict
- 无 cover 时返回 None
- images 元数据脏数据（非 dict）时不抛
- registry 解析失败时仅 warning 不阻断其他字段

参考设计：docs/system/image-asset-pipeline-design.md §5.2.1
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.image_asset import ImageRef
from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.knowledge.attraction_search_tool import AttractionSearchTool

pytestmark = pytest.mark.tools


# ============================================================
# Helpers
# ============================================================

TENANT_ID = "tenant_test_001"


def _build_row(
    doc_id=100,
    title="黄果树瀑布",
    text="黄果树瀑布位于安顺市...",
    distance=0.1,
    metadata=None,
    file_path="/data/x.xlsx",
):
    """构造 fetchall 单行数据（DictCursor 风格）"""
    return {
        "distance": distance,
        "doc_id": doc_id,
        "text": text,
        "title": title,
        "metadata": metadata if metadata is not None else {},
        "file_path": file_path,
    }


def _make_image_ref(file_id="file_cover001"):
    """构造一个合法的 ImageRef"""
    return ImageRef(
        file_id=file_id,
        download_url=f"/api/files/{file_id}/download",
        display_name="黄果树瀑布.jpg",
        width=800,
        height=600,
        mime_type="image/jpeg",
        size_bytes=12345,
        source="knowledge_base",
        usage="thumbnail",
        linked_doc_id=100,
    )


def _make_mock_conn(fetchall_side_effects):
    """
    构造 get_db_connection 的 mock。
    fetchall_side_effects: list — 每次进入 with 块后 cursor.fetchall() 依次返回的列表
    """
    mock_cursor = MagicMock()
    mock_cursor.fetchall.side_effect = list(fetchall_side_effects)

    # 关键：cursor 必须挂在 mock_conn 上（实际代码用 `with get_db_connection() as conn: conn.cursor()`）
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn


# ============================================================
# 测试用例
# ============================================================

@pytest.mark.asyncio
async def test_search_returns_cover_image_when_metadata_has_cover():
    """metadata.images.cover 存在时，cover_image 应为 ImageRef dict（含 file_id）"""
    rows = [_build_row(metadata={"region": "安顺", "images": {"cover": "file_cover001"}})]
    mock_conn = _make_mock_conn([rows, []])  # 第一次查 chunks_vec，第二次查 chunk_index=2
    expected_ref = _make_image_ref("file_cover001")

    with patch(
        "src.tools.knowledge.attraction_search_tool.get_db_connection",
        return_value=mock_conn,
    ), patch(
        "src.tools.knowledge.attraction_search_tool.get_image_registry",
        return_value=MagicMock(
            get_ref_by_file_id=AsyncMock(return_value=expected_ref)
        ),
    ):
        tool = AttractionSearchTool()
        # 跳过 _embed（绕开 dashscope）
        with (
            patch.object(AttractionSearchTool, "_embed", return_value=[0.1] * 1024),
            tool_execution_scope(ToolExecutionContext(tenant_id=TENANT_ID)),
        ):
            result = await tool.execute(query="黄果树", top_k=20)

    assert result["success"] is True
    assert result["count"] == 1
    item = result["results"][0]
    assert item["cover_image"] is not None
    assert isinstance(item["cover_image"], dict)
    assert item["cover_image"]["file_id"] == "file_cover001"
    assert item["cover_image"]["download_url"] == "/api/files/file_cover001/download"
    assert item["cover_image"]["source"] == "knowledge_base"


@pytest.mark.asyncio
async def test_search_returns_null_when_no_cover():
    """metadata 无 images 键时，cover_image 应为 None"""
    rows = [_build_row(metadata={"region": "安顺"})]  # 无 images
    mock_conn = _make_mock_conn([rows, []])

    with patch(
        "src.tools.knowledge.attraction_search_tool.get_db_connection",
        return_value=mock_conn,
    ), patch(
        "src.tools.knowledge.attraction_search_tool.get_image_registry",
        return_value=MagicMock(get_ref_by_file_id=AsyncMock(return_value=None)),
    ):
        tool = AttractionSearchTool()
        with (
            patch.object(AttractionSearchTool, "_embed", return_value=[0.1] * 1024),
            tool_execution_scope(ToolExecutionContext(tenant_id=TENANT_ID)),
        ):
            result = await tool.execute(query="黄果树", top_k=20)

    assert result["success"] is True
    assert result["results"][0]["cover_image"] is None


@pytest.mark.asyncio
async def test_search_returns_null_when_images_meta_not_dict():
    """metadata.images 为字符串（脏数据）时，cover_image 应为 None 不抛异常"""
    rows = [_build_row(metadata={"region": "安顺", "images": "not_a_dict"})]
    mock_conn = _make_mock_conn([rows, []])

    with patch(
        "src.tools.knowledge.attraction_search_tool.get_db_connection",
        return_value=mock_conn,
    ), patch(
        "src.tools.knowledge.attraction_search_tool.get_image_registry",
        return_value=MagicMock(get_ref_by_file_id=AsyncMock(return_value=None)),
    ):
        tool = AttractionSearchTool()
        with (
            patch.object(AttractionSearchTool, "_embed", return_value=[0.1] * 1024),
            tool_execution_scope(ToolExecutionContext(tenant_id=TENANT_ID)),
        ):
            result = await tool.execute(query="黄果树", top_k=20)

    assert result["success"] is True
    assert result["results"][0]["cover_image"] is None


@pytest.mark.asyncio
async def test_search_resolve_failure_does_not_block():
    """registry.get_ref_by_file_id 抛异常时，仅 warning，cover_image=None，其他字段正常"""
    rows = [_build_row(metadata={"region": "安顺", "images": {"cover": "file_bad"}})]
    mock_conn = _make_mock_conn([rows, []])

    boom_registry = MagicMock()
    boom_registry.get_ref_by_file_id = AsyncMock(side_effect=RuntimeError("redis down"))

    with patch(
        "src.tools.knowledge.attraction_search_tool.get_db_connection",
        return_value=mock_conn,
    ), patch(
        "src.tools.knowledge.attraction_search_tool.get_image_registry",
        return_value=boom_registry,
    ):
        tool = AttractionSearchTool()
        with (
            patch.object(AttractionSearchTool, "_embed", return_value=[0.1] * 1024),
            tool_execution_scope(ToolExecutionContext(tenant_id=TENANT_ID)),
        ):
            result = await tool.execute(query="黄果树", top_k=20)

    assert result["success"] is True
    item = result["results"][0]
    assert item["cover_image"] is None
    # 其他字段仍正常
    assert item["doc_id"] == 100
    assert item["title"] == "黄果树瀑布"
    assert item["region"] == "安顺"
