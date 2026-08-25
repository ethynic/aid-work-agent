# -*- coding: utf-8 -*-
"""Phase 1 P1.8 端到端集成测试：旅游顾问图片资产管线。

覆盖计划 `docs/plans/plan-image-asset-pipeline.md` P1.8 中列出的 4 个测试场景：

1. `test_import_attraction_zip_registers_images`
   - 验证 `AttractionRetriever.import_attraction` 携带 cover/gallery 图片参数时
     `documents.metadata.images.cover / .gallery` 被正确写入（UPDATE documents SQL）
2. `test_attraction_search_returns_cover_image`
   - 验证 `attraction_search` 工具能从 `metadata.images.cover` 解析出 ImageRef 并以
     dict 形式返回到 `results[i].cover_image`
3. `test_inline_file_id_to_word`
   - 验证含 `![](file_id:file_xxx)` 的 Markdown 经过 `inline_images` 后被替换为本地
     路径，并返回对应的 ImageRef（不真实跑 Pandoc，避免依赖系统二进制）
4. `test_e2e_attraction_search_to_word_pipeline`
   - 模拟完整管线（attraction_search → 拼接 Markdown → convert_async），
     mock Pandoc 与 ImageRegistry，断言 inline_images 被触发且 tenant_id 已传递

mock 策略说明（与计划一致）：
- 不真启 FastAPI、不真连 PostgreSQL、不真调 LLM / dashscope
- 不真实跑 Pandoc（依赖系统二进制，CI 不稳），仅断言到 inline_images 输出
- 单元级别的「集成」测试：直接调 retriever / tool 方法，跳过 HTTP 路由层
- 真实文件用 tmp_path fixture，路径用 pathlib.Path（Windows 兼容）
"""

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# travel-quote 是带连字符的 skill 目录，Python 无法直接 import，
# 需要把 scripts 目录加入 sys.path 后 import 模块（参考现有 test_attraction_retriever_async.py）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _PROJECT_ROOT / "src" / "skills" / "travel-quote" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import attraction_retriever  # noqa: E402


# ============================================================
# Fixture：1x1 PNG 临时文件
# ============================================================

@pytest.fixture
def tmp_png(tmp_path: Path) -> str:
    """创建一个合法的 1x1 PNG 临时文件，返回绝对路径字符串。

    用 Pillow 生成（项目已依赖 Pillow），保证文件合法可被 Pillow 重新打开读取宽高。
    """
    from PIL import Image

    img = Image.new("RGBA", (1, 1), (255, 0, 0, 255))
    p = tmp_path / "test.png"
    img.save(p, format="PNG")
    return str(p)


@pytest.fixture
def tmp_png_2(tmp_path: Path) -> str:
    """第二个 1x1 PNG 临时文件（用于图集场景）"""
    from PIL import Image

    img = Image.new("RGBA", (1, 1), (0, 255, 0, 255))
    p = tmp_path / "test2.png"
    img.save(p, format="PNG")
    return str(p)


@pytest.fixture
def mock_image_ref(tmp_png):
    """构造一个稳定的 ImageRef 用于 mock 返回值。"""
    from src.core.image_asset import ImageRef

    return ImageRef(
        file_id="file_test123abc",
        download_url="/api/files/file_test123abc/download",
        display_name="test.png",
        mime_type="image/png",
        size_bytes=100,
        source="knowledge_base",
        usage="thumbnail",
    )


# ============================================================
# 辅助：构造 mock 的 DB cursor + 上下文管理器
# ============================================================

def _make_mock_conn(rows_for_fetchall: Optional[List[Dict]] = None,
                    row_for_fetchone: Optional[Dict] = None):
    """构造一个 mock connection，支持 execute / fetchall / fetchone / commit / __enter__/__exit__。

    `rows_for_fetchall`：execute 后 fetchall 返回的行列表（按调用顺序）。
        若为 None，fetchall 返回 []。
    `row_for_fetchone`：execute 后 fetchone 返回的单行。若为 None，返回 None。

    返回的 mock conn 支持 with 语法（contextmanager）。
    """
    cursor = MagicMock()
    cursor.fetchall.return_value = rows_for_fetchall or []
    cursor.fetchone.return_value = row_for_fetchone

    @contextmanager
    def fake_get_conn():
        yield cursor

    return fake_get_conn, cursor


# ============================================================
# 测试 1：import_attraction 写入 documents.metadata.images
# ============================================================

@pytest.mark.asyncio
async def test_import_attraction_zip_registers_images(tmp_png, tmp_png_2, mock_image_ref):
    """验证 import_attraction 携带 cover/gallery 图片参数时，
    documents.metadata.images.cover/gallery 被正确写入。

    实现要点：
    - mock AttractionRetriever._get_conn（用 MagicMock cursor，fetchone 返回 doc_id）
    - mock AttractionRetriever._embed（返回固定向量，避免调真实 dashscope）
    - mock ImageRegistry.register（返回固定 ImageRef，避免磁盘/Redis 操作）
    - 调 await AttractionRetriever().import_attraction(..., cover_image_path=...,
      gallery_image_paths=[...])
    - 断言 UPDATE documents SQL 被调用、参数中 metadata JSON 含 images.cover 与 images.gallery
    """
    retriever = attraction_retriever.AttractionRetriever()

    # mock 数据库连接：每次 with _get_conn() 返回同一个 cursor
    fake_conn, cursor = _make_mock_conn(
        # INSERT ... RETURNING id 第一次拿 doc_id；后续 UPDATE 不 fetchone
        row_for_fetchone={"id": 42},
    )
    # fetchone 第一次返回 {"id": 42}（INSERT documents），后续 chunks RETURNING id 也要返回
    # 用 side_effect 列表按顺序返回
    cursor.fetchone.side_effect = [
        {"id": 42},   # INSERT documents RETURNING id
        {"id": 100},  # INSERT chunk 0 RETURNING id
    ]

    # mock _embed，避免真实调用 dashscope
    fake_embedding = [0.1] * 1024
    with patch.object(retriever, "_get_conn", fake_conn), \
         patch.object(retriever, "_embed", return_value=fake_embedding), \
         patch(
             "src.core.image_asset.get_image_registry",
             return_value=MagicMock(
                 register=AsyncMock(side_effect=[mock_image_ref, mock_image_ref]),
             ),
         ):
        doc_id = await retriever.import_attraction(
            tenant_id="tenant_test",
            attraction_name="黄果树瀑布",
            region="贵州",
            info_text="黄果树瀑布位于...",
            ticket_table_text="| 票种 | 价格 |\n| --- | --- |\n| 成人 | 160 |",
            project_table_text="",
            metadata={"category": "natural"},
            source_file="attractions.xlsx",
            user_id="user_test",
            cover_image_path=tmp_png,
            gallery_image_paths=[tmp_png_2],
        )

    # 1. 返回 doc_id
    assert doc_id == 42

    # 2. 至少有一次 UPDATE documents SET metadata（写 images 信息）
    # cursor.execute 被调用多次（INSERT + UPDATE），找 UPDATE 调用
    execute_calls = cursor.execute.call_args_list
    update_calls = [
        c for c in execute_calls
        if isinstance(c.args, tuple) and len(c.args) >= 1
        and isinstance(c.args[0], str)
        and "UPDATE documents SET metadata" in c.args[0]
    ]
    assert len(update_calls) == 1, f"期望恰好 1 次 UPDATE documents，实际: {len(update_calls)}"

    # UPDATE 调用第二个参数应为 (metadata_json, doc_id)
    update_args = update_calls[0].args[1]
    assert len(update_args) == 2, f"UPDATE 参数数量错误: {update_args}"
    metadata_json, doc_id_arg = update_args
    assert doc_id_arg == 42

    # 3. metadata_json 应能解析出 images.cover / images.gallery
    final_meta = json.loads(metadata_json)
    assert "images" in final_meta, f"metadata 缺少 images 字段: {final_meta}"
    images_meta = final_meta["images"]
    assert images_meta.get("cover") == "file_test123abc", f"images.cover 错误: {images_meta}"
    assert images_meta.get("gallery") == ["file_test123abc"], f"images.gallery 错误: {images_meta}"

    # 4. 原有 metadata 字段（category）应保留（合并而非覆盖）
    assert final_meta.get("category") == "natural", f"原 metadata 字段被覆盖: {final_meta}"


# ============================================================
# 测试 2：attraction_search 返回 cover_image
# ============================================================

@pytest.mark.asyncio
async def test_attraction_search_returns_cover_image(mock_image_ref):
    """验证 attraction_search 工具能从 metadata.images.cover 解析出 ImageRef。

    实现要点：
    - mock get_db_connection（cursor fetchall 返回含 metadata.images.cover 的 row）
    - mock ImageRegistry.get_ref_by_file_id 返回 ImageRef
    - mock AttractionSearchTool._embed（避免调真实 dashscope）
    - 调 await AttractionSearchTool().execute(query="测试", top_k=5)
    - 断言 result.results[0].cover_image 是 dict 含 file_id
    """
    from src.tools.knowledge.attraction_search_tool import AttractionSearchTool
    from src.tools.context import ToolExecutionContext, tool_execution_scope

    tool = AttractionSearchTool()

    # 模拟数据库返回一行景点，metadata 含 images.cover
    cover_file_id = "file_cover123abc"
    metadata = {
        "region": "贵州",
        "category": "natural",
        "category_cn": "自然景观",
        "images": {
            "cover": cover_file_id,
            "gallery": ["file_g1", "file_g2"],
        },
    }
    rows = [
        {
            "distance": 0.1,
            "doc_id": 42,
            "text": "黄果树瀑布位于贵州省...",
            "title": "景点：黄果树瀑布",
            "metadata": metadata,  # 已经是 dict（attraction_search_tool 内部跳过 json.loads）
            "file_path": "attractions.xlsx",
        }
    ]

    # 项目表查询返回空（不影响测试）
    project_rows = []

    # attraction_search_tool.execute 的调用模式：
    #   with get_db_connection() as conn:
    #       cursor = conn.cursor()
    #       cursor.execute(...)
    #       rows = cursor.fetchall()
    # 因此 fake conn 需要支持 .cursor() 方法返回真实 cursor
    call_state = {"main_calls": 0}

    def make_cursor(fetchall_ret):
        c = MagicMock()
        c.fetchall.return_value = fetchall_ret
        c.fetchone.return_value = None
        return c

    main_cursor = make_cursor(rows)
    project_cursor = make_cursor(project_rows)

    @contextmanager
    def fake_get_db_connection():
        # 第一次 with：主查询；第二次 with：项目表查询
        if call_state["main_calls"] == 0:
            call_state["main_calls"] = 1
            yield MagicMock(cursor=MagicMock(return_value=main_cursor))
        else:
            yield MagicMock(cursor=MagicMock(return_value=project_cursor))

    fake_embedding = [0.1] * 1024

    with patch(
        "src.tools.knowledge.attraction_search_tool.get_db_connection",
        fake_get_db_connection,
    ), patch.object(
        tool, "_embed", return_value=fake_embedding,
    ), patch(
        # 注意：attraction_search_tool 顶层 `from src.core.image_asset import get_image_registry`
        # 把函数绑定到本模块，必须 patch 本模块的引用
        "src.tools.knowledge.attraction_search_tool.get_image_registry",
        return_value=MagicMock(
            get_ref_by_file_id=AsyncMock(return_value=mock_image_ref),
        ),
    ), tool_execution_scope(
        ToolExecutionContext(tenant_id="tenant_test")
    ):
        result = await tool.execute(query="黄果树", top_k=5)

    # 1. 工具结果成功
    assert result["success"] is True, f"工具失败: {result}"
    assert result["count"] == 1

    # 2. cover_image 应为 dict 形式的 ImageRef
    item = result["results"][0]
    assert item["cover_image"] is not None, "cover_image 为 None"
    assert isinstance(item["cover_image"], dict), f"cover_image 不是 dict: {type(item['cover_image'])}"
    assert item["cover_image"]["file_id"] == mock_image_ref.file_id
    assert item["cover_image"]["source"] == "knowledge_base"
    assert item["cover_image"]["usage"] == "thumbnail"
    assert item["cover_image"]["download_url"] == "/api/files/file_test123abc/download"

    # 3. 景点其他字段仍正常
    assert item["doc_id"] == 42
    assert item["region"] == "贵州"


# ============================================================
# 测试 3：inline_images 处理 file_id: scheme
# ============================================================

@pytest.mark.asyncio
async def test_inline_file_id_to_word(tmp_png, mock_image_ref):
    """验证含 `![](file_id:file_xxx)` 的 Markdown 经过 inline_images 后
    被替换为本地路径，并返回 ImageRef 列表。

    不真实跑 Pandoc（依赖系统二进制，CI 不稳）；只断言到 inline_images 输出。
    """
    from src.tools._image_inliner import inline_images

    text = f"text ![](file_id:{mock_image_ref.file_id}) more"

    with patch(
        # _image_inliner 顶层 `from src.core.image_asset import get_image_registry`
        # 把函数绑定到本模块，必须 patch 本模块的引用
        "src.tools._image_inliner.get_image_registry",
        return_value=MagicMock(
            get_ref_by_file_id=AsyncMock(return_value=mock_image_ref),
            resolve_local_path=AsyncMock(return_value=Path(tmp_png)),
        ),
    ):
        out_text, refs = await inline_images(text, tenant_id="t1")

    # 1. 输出文本含本地路径，不再含 file_id:file_xxx
    assert "file_id:" not in out_text, f"输出仍含 file_id: 原始标记: {out_text}"
    assert tmp_png in out_text or Path(tmp_png).name in out_text, \
        f"输出未含本地路径: {out_text}"

    # 2. 返回的 ImageRef 列表含 1 个 ref
    assert len(refs) == 1, f"refs 数量错误: {refs}"
    assert refs[0].file_id == mock_image_ref.file_id


# ============================================================
# 测试 4：完整管线（attraction_search → Markdown → convert_async）
# ============================================================

@pytest.mark.asyncio
async def test_e2e_attraction_search_to_word_pipeline(mock_image_ref, tmp_png):
    """完整管线模拟：attraction_search 返回带 cover_image 的结果 → 拼接 Markdown → convert_async。

    mock 策略：
    - 不真实跑 attraction_search（避免依赖 DB/embedding）：构造一个等价的 results
    - 从 results 提取 cover_image.file_id，拼成含 `![](file_id:file_xxx)` 的 Markdown
    - mock ImageRegistry（让 inline_images 能解析 file_id）
    - mock `src.tools.word.md_to_word._pandoc_convert`（避免依赖真实 Pandoc 二进制），
      返回一个简单 Document
    - 调 await convert_async(md_text, tenant_id="t1")
    - 断言：convert_async 内 inline_images 被触发（输出 markdown 不含 file_id:，含本地路径），
      _pandoc_convert 被调用且参数中 markdown 已被 inline 处理
    """
    from docx import Document

    from src.tools.word.md_to_word import convert_async

    # 1. 构造 attraction_search 风格的 cover_image 字段（与真实工具返回一致：dict 形式）
    cover_image_dict = mock_image_ref.model_dump()
    file_id = cover_image_dict["file_id"]

    # 2. 拼接 Markdown（模拟 SUBAGENT.md 中规定的「景点图集」章节格式）
    md_text = (
        "# 行程文档\n\n"
        "## 第 1 天\n\n"
        "| 时间 | 行程 |\n| --- | --- |\n| 上午 | 游览景点 |\n\n"
        "## 景点图集\n\n"
        f"### 黄果树瀑布\n\n"
        f"![黄果树瀑布](file_id:{file_id})\n\n"
        "*黄果树瀑布是亚洲第一大瀑布*\n"
    )

    # 3. mock ImageRegistry（让 inline_images 解析 file_id 为本地路径）
    fake_registry = MagicMock(
        get_ref_by_file_id=AsyncMock(return_value=mock_image_ref),
        resolve_local_path=AsyncMock(return_value=Path(tmp_png)),
    )

    # 4. mock _pandoc_convert，捕获传入的 markdown 并返回简单 Document
    captured_markdown = {"value": ""}

    def fake_pandoc_convert(md_text_arg, reference_doc=None, title="", author=""):
        # 记录 inline 处理后的 markdown（验证 file_id: 已被替换为本地路径）
        captured_markdown["value"] = md_text_arg
        return Document()

    with patch(
        # _image_inliner 顶层 import 绑定到本模块的 get_image_registry，
        # 必须在该模块的引用上 patch
        "src.tools._image_inliner.get_image_registry",
        return_value=fake_registry,
    ), patch(
        "src.tools.word.md_to_word._pandoc_convert",
        side_effect=fake_pandoc_convert,
    ), patch(
        "src.tools.word.md_to_word._ensure_cjk_fonts",
        return_value=None,
    ), patch(
        "src.tools.word.md_to_word._apply_template_table_style",
        return_value=None,
    ):
        doc = await convert_async(md_text, tenant_id="t1", user_id="u1")

    # 1. convert_async 返回 Document 实例（mock 版）
    assert doc is not None, "convert_async 未返回 Document"

    # 2. inline_images 被触发：传入 _pandoc_convert 的 markdown 不应再含 file_id:file_xxx
    captured = captured_markdown["value"]
    assert f"file_id:{file_id}" not in captured, \
        f"file_id: 标记未被替换为本地路径，captured: {captured}"

    # 3. 本地路径应在 captured 中出现（替换成功）
    # inline_images 把 src 替换为本地路径字符串
    assert tmp_png in captured or Path(tmp_png).name in captured, \
        f"captured markdown 未含本地图片路径: {captured}"

    # 4. 章节结构应保留（图集标题 / 三级标题）
    assert "景点图集" in captured, f"图集章节丢失: {captured}"
    assert "黄果树瀑布" in captured, f"景点名丢失: {captured}"

    # 5. ImageRegistry.get_ref_by_file_id 被调用（说明 inline_images 解析了 file_id）
    assert fake_registry.get_ref_by_file_id.called, \
        "ImageRegistry.get_ref_by_file_id 未被调用（inline_images 未触发）"
