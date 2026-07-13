# -*- coding: utf-8 -*-
"""
景点 Excel 导入 API（P1.2.2）：zip 包支持 + 图片解析 单元测试

验证：
1. zip 包含 xlsx + images/ → import_attraction 收到正确的 cover_image_path / gallery_image_paths
2. zip 只含 xlsx（无 images/） → 导入成功但不传图（cover/gallery 都为 None）
3. zip 不含 xlsx → 返回 success=False
4. 图片格式不支持（.gif） → 跳过该图（warning，不阻断）
5. Excel 引用了图片但 images/ 中不存在 → 跳过该图，不阻断
6. 上传 .xlsx（向后兼容） → 不传图参数（cover_image_path=None, gallery_image_paths=None）

测试策略：
- 直接 await 调用 import_attraction_excel_to_kb，绕过 FastAPI 路由层
- mock 掉 _get_tenant_id / get_current_user / _save_upload_to_storage
- mock 掉 AttractionExcelParser / AttractionRetriever 的导入（patch sys.modules）
- 用 openpyxl 真实生成最小 xlsx，打包到 zip 中
"""
import io
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 把 skill scripts 目录加入 sys.path，使 patch("attraction_excel_parser.AttractionExcelParser")
# 能解析到模块（路由函数内部也是从该路径 import）
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
# 触发 import，确保 patch 目标存在
import attraction_excel_parser  # noqa: E402,F401
import attraction_retriever  # noqa: E402,F401

# 构造最小 xlsx 内容（不依赖 openpyxl 也能用，但 openpyxl 是项目现有依赖）
def _make_minimal_xlsx_bytes() -> bytes:
    """生成包含 1 个有效 Sheet 的最小 xlsx 文件"""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["景点名称", "区域", "门票"])
    ws.append(["测试景点", "测试区", "100元"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ============================================================
# 测试目标：_resolve_image_path 纯函数
# ============================================================

class TestResolveImagePath:
    """图片路径校验：扩展名白名单 + 文件存在性"""

    def test_valid_jpg_returns_path(self, tmp_path):
        from src.api.travel_quote import _resolve_image_path, ALLOWED_IMG_EXTS
        assert ".jpg" in ALLOWED_IMG_EXTS
        img = tmp_path / "a.jpg"
        img.write_bytes(b"x")
        assert _resolve_image_path(str(tmp_path), "a.jpg") == str(img)

    def test_unsupported_ext_returns_none(self, tmp_path, caplog):
        from src.api.travel_quote import _resolve_image_path
        img = tmp_path / "a.gif"
        img.write_bytes(b"x")
        # .gif 不在白名单
        assert _resolve_image_path(str(tmp_path), "a.gif") is None

    def test_missing_file_returns_none(self, tmp_path):
        from src.api.travel_quote import _resolve_image_path
        assert _resolve_image_path(str(tmp_path), "notexist.jpg") is None

    def test_none_inputs_return_none(self):
        from src.api.travel_quote import _resolve_image_path
        assert _resolve_image_path(None, "a.jpg") is None
        assert _resolve_image_path("/tmp", None) is None
        assert _resolve_image_path(None, None) is None

    def test_path_traversal_sanitized_to_basename(self, tmp_path):
        """LLM 输出含路径遍历（../）时，应只取 basename，无法逃出 images_dir"""
        from src.api.travel_quote import _resolve_image_path
        # 在 tmp_path/images_dir 内放一个合法图片
        img = tmp_path / "safe.jpg"
        img.write_bytes(b"x")
        # 攻击者试图读取 tmp_path 之外的文件（路径不存在，应被 basename 化后找不到）
        assert _resolve_image_path(str(tmp_path), "../../etc/passwd.jpg") is None
        # 即使 attacker 把真图片放到 tmp_path 之外的同一 basename，也无法被读
        # （basename 后只在 images_dir 内查找）
        # 验证 basename 化：构造合法子目录同名文件，attacker 路径应解析到子目录的 basename
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "trav.jpg").write_bytes(b"y")
        # _resolve_image_path("a/subdir/trav.jpg") → basename "trav.jpg" → 查 tmp_path/trav.jpg（不存在）
        assert _resolve_image_path(str(tmp_path), "subdir/trav.jpg") is None
        # 但如果在 tmp_path 根目录有 trav.jpg，basename 化后会找到它
        (tmp_path / "trav.jpg").write_bytes(b"z")
        result = _resolve_image_path(str(tmp_path), "subdir/trav.jpg")
        assert result is not None
        assert os.path.abspath(result) == os.path.abspath(str(tmp_path / "trav.jpg"))


# ============================================================
# 测试目标：_process_parsed_attractions 集成逻辑
# ============================================================

class TestProcessParsedAttractions:
    """_process_parsed_attractions 在 zip 模式下传图，xlsx 模式下不传图"""

    @pytest.mark.asyncio
    async def test_zip_mode_passes_image_paths(self, tmp_path):
        """images_dir 存在 + 景点含图片字段 → import_attraction 收到正确路径"""
        from src.api.travel_quote import _process_parsed_attractions

        # 准备真实 xlsx
        xlsx_path = tmp_path / "test.xlsx"
        xlsx_path.write_bytes(_make_minimal_xlsx_bytes())
        # 准备 images 目录 + 真实图片
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        (images_dir / "cover.jpg").write_bytes(b"cover-bytes")
        (images_dir / "g1.png").write_bytes(b"g1-bytes")
        (images_dir / "g2.jpeg").write_bytes(b"g2-bytes")

        # mock parser：返回单个景点，含图片字段
        parser = MagicMock()
        parser.parse_sheet_by_name = AsyncMock(return_value=[{
            "attraction_name": "测试景点",
            "region": "测试区",
            "info_text": "景点信息",
            "ticket_table_text": "门票",
            "project_table_text": "",
            "cover_image_filename": "cover.jpg",
            "gallery_image_filenames": ["g1.png", "g2.jpeg"],
            "metadata": {},
        }])

        # mock retriever：记录 import_attraction 调用
        retriever = MagicMock()
        retriever.import_attraction = AsyncMock(return_value=1)
        retriever.search_by_name = MagicMock(return_value=[])

        result = await _process_parsed_attractions(
            parser=parser,
            retriever=retriever,
            xlsx_path=str(xlsx_path),
            tenant_id="t1",
            user_id="u1",
            file_rel_path="storage/xx.xlsx",
            images_dir=str(images_dir),
        )

        assert result["success"] is True
        assert result["data"]["imported"] == 1
        # 验证 import_attraction 收到正确的图片路径
        call_kwargs = retriever.import_attraction.call_args.kwargs
        assert call_kwargs["cover_image_path"].endswith("cover.jpg")
        gallery = call_kwargs["gallery_image_paths"]
        assert len(gallery) == 2
        assert any(p.endswith("g1.png") for p in gallery)
        assert any(p.endswith("g2.jpeg") for p in gallery)

    @pytest.mark.asyncio
    async def test_xlsx_mode_images_dir_none_no_image_args(self, tmp_path):
        """xlsx 模式（images_dir=None）→ cover_image_path=None, gallery_image_paths=None"""
        from src.api.travel_quote import _process_parsed_attractions

        xlsx_path = tmp_path / "test.xlsx"
        xlsx_path.write_bytes(_make_minimal_xlsx_bytes())

        parser = MagicMock()
        # 即使 parser 输出了图片字段，xlsx 模式下也应被忽略（_resolve_image_path 返回 None）
        parser.parse_sheet_by_name = AsyncMock(return_value=[{
            "attraction_name": "测试景点",
            "region": "测试区",
            "info_text": "景点信息",
            "ticket_table_text": "门票",
            "project_table_text": "",
            "cover_image_filename": "cover.jpg",
            "gallery_image_filenames": ["g1.jpg"],
            "metadata": {},
        }])

        retriever = MagicMock()
        retriever.import_attraction = AsyncMock(return_value=1)
        retriever.search_by_name = MagicMock(return_value=[])

        result = await _process_parsed_attractions(
            parser=parser,
            retriever=retriever,
            xlsx_path=str(xlsx_path),
            tenant_id="t1",
            user_id="u1",
            file_rel_path="storage/xx.xlsx",
            images_dir=None,  # xlsx 模式
        )

        assert result["success"] is True
        assert result["data"]["imported"] == 1
        call_kwargs = retriever.import_attraction.call_args.kwargs
        # xlsx 模式必须不传图
        assert call_kwargs["cover_image_path"] is None
        assert call_kwargs["gallery_image_paths"] is None

    @pytest.mark.asyncio
    async def test_unsupported_image_skipped(self, tmp_path):
        """images/ 含 .gif → 跳过该图（warning），不阻断导入"""
        from src.api.travel_quote import _process_parsed_attractions

        xlsx_path = tmp_path / "test.xlsx"
        xlsx_path.write_bytes(_make_minimal_xlsx_bytes())
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        (images_dir / "cover.jpg").write_bytes(b"x")
        (images_dir / "bad.gif").write_bytes(b"x")  # 不支持的格式

        parser = MagicMock()
        parser.parse_sheet_by_name = AsyncMock(return_value=[{
            "attraction_name": "测试景点",
            "region": "测试区",
            "info_text": "景点信息",
            "ticket_table_text": "门票",
            "project_table_text": "",
            "cover_image_filename": "cover.jpg",
            "gallery_image_filenames": ["bad.gif", "good.jpg"],
            "metadata": {},
        }])
        (images_dir / "good.jpg").write_bytes(b"x")

        retriever = MagicMock()
        retriever.import_attraction = AsyncMock(return_value=1)
        retriever.search_by_name = MagicMock(return_value=[])

        await _process_parsed_attractions(
            parser=parser,
            retriever=retriever,
            xlsx_path=str(xlsx_path),
            tenant_id="t1",
            user_id="u1",
            file_rel_path="storage/xx.xlsx",
            images_dir=str(images_dir),
        )

        call_kwargs = retriever.import_attraction.call_args.kwargs
        # cover 仍传，gallery 只剩 good.jpg（bad.gif 被跳过）
        assert call_kwargs["cover_image_path"].endswith("cover.jpg")
        gallery = call_kwargs["gallery_image_paths"]
        assert len(gallery) == 1
        assert gallery[0].endswith("good.jpg")

    @pytest.mark.asyncio
    async def test_missing_image_file_skipped(self, tmp_path):
        """Excel 引用了图片但 images/ 中不存在 → 跳过，不阻断"""
        from src.api.travel_quote import _process_parsed_attractions

        xlsx_path = tmp_path / "test.xlsx"
        xlsx_path.write_bytes(_make_minimal_xlsx_bytes())
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        # 故意不创建 cover.jpg
        (images_dir / "g1.jpg").write_bytes(b"x")

        parser = MagicMock()
        parser.parse_sheet_by_name = AsyncMock(return_value=[{
            "attraction_name": "测试景点",
            "region": "测试区",
            "info_text": "景点信息",
            "ticket_table_text": "门票",
            "project_table_text": "",
            "cover_image_filename": "notexist.jpg",
            "gallery_image_filenames": ["g1.jpg", "also_missing.jpg"],
            "metadata": {},
        }])

        retriever = MagicMock()
        retriever.import_attraction = AsyncMock(return_value=1)
        retriever.search_by_name = MagicMock(return_value=[])

        result = await _process_parsed_attractions(
            parser=parser,
            retriever=retriever,
            xlsx_path=str(xlsx_path),
            tenant_id="t1",
            user_id="u1",
            file_rel_path="storage/xx.xlsx",
            images_dir=str(images_dir),
        )

        # 导入仍然成功
        assert result["success"] is True
        assert result["data"]["imported"] == 1
        call_kwargs = retriever.import_attraction.call_args.kwargs
        # cover 缺失 → None
        assert call_kwargs["cover_image_path"] is None
        # gallery 只剩 g1.jpg
        gallery = call_kwargs["gallery_image_paths"]
        assert len(gallery) == 1
        assert gallery[0].endswith("g1.jpg")


# ============================================================
# 测试目标：完整路由函数（直接 await）
# ============================================================

def _build_zip(xlsx_bytes: bytes, images: dict = None) -> bytes:
    """构造测试 zip 包。images: {filename: bytes}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("attractions.xlsx", xlsx_bytes)
        if images:
            for name, data in images.items():
                zf.writestr(f"images/{name}", data)
    return buf.getvalue()


class TestImportRouteZipAndXlsx:
    """端到端：直接 await 路由函数，验证 zip 模式和 xlsx 模式分支"""

    @pytest.mark.asyncio
    async def test_import_zip_without_images_dir_succeeds(self, tmp_path):
        """zip 只含 xlsx（无 images/）→ 导入成功但不传图"""
        # 在路由函数内部 sys.path 注入 skill scripts 后会 import AttractionExcelParser/Retriever
        # 我们直接 patch src.api.travel_quote 模块内的依赖
        from src.api import travel_quote as tq

        xlsx_bytes = _make_minimal_xlsx_bytes()
        zip_bytes = _build_zip(xlsx_bytes)

        upload = MagicMock()
        upload.filename = "data.zip"
        upload.read = AsyncMock(return_value=zip_bytes)

        # mock 内部依赖
        fake_parser = MagicMock()
        fake_parser.parse_sheet_by_name = AsyncMock(return_value=[{
            "attraction_name": "测试景点",
            "region": "测试区",
            "info_text": "信息",
            "ticket_table_text": "门票",
            "project_table_text": "",
            "cover_image_filename": "cover.jpg",  # 有字段但无 images 目录
            "gallery_image_filenames": ["g.jpg"],
            "metadata": {},
        }])
        fake_retriever = MagicMock()
        fake_retriever.import_attraction = AsyncMock(return_value=1)
        fake_retriever.search_by_name = MagicMock(return_value=[])

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value={"user_id": "u1"}), \
             patch.object(tq, "_save_upload_to_storage", return_value="storage/xx.xlsx"), \
             patch("attraction_excel_parser.AttractionExcelParser", return_value=fake_parser), \
             patch("attraction_retriever.AttractionRetriever", return_value=fake_retriever):
            result = await tq.import_attraction_excel_to_kb(MagicMock(), upload)

        assert result["success"] is True
        assert result["data"]["imported"] == 1
        # 无 images/ → cover_image_path 必须为 None
        kwargs = fake_retriever.import_attraction.call_args.kwargs
        assert kwargs["cover_image_path"] is None
        assert kwargs["gallery_image_paths"] is None

    @pytest.mark.asyncio
    async def test_import_zip_missing_xlsx_returns_error(self):
        """zip 不含 xlsx → 返回 success=False"""
        from src.api import travel_quote as tq

        # 空 zip（只含一个无关文件）
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "no xlsx here")
        zip_bytes = buf.getvalue()

        upload = MagicMock()
        upload.filename = "data.zip"
        upload.read = AsyncMock(return_value=zip_bytes)

        fake_parser = MagicMock()
        fake_retriever = MagicMock()

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value={"user_id": "u1"}), \
             patch.object(tq, "_save_upload_to_storage", return_value="storage/xx.xlsx"), \
             patch("attraction_excel_parser.AttractionExcelParser", return_value=fake_parser), \
             patch("attraction_retriever.AttractionRetriever", return_value=fake_retriever):
            result = await tq.import_attraction_excel_to_kb(MagicMock(), upload)

        assert result["success"] is False
        assert "未找到" in result["error"] or "xlsx" in result["error"]
        # 不应调用 import_attraction
        fake_retriever.import_attraction.assert_not_called()

    @pytest.mark.asyncio
    async def test_import_xlsx_backward_compatible(self):
        """上传 .xlsx 文件 → 行为与改造前一致（不传图）"""
        from src.api import travel_quote as tq

        xlsx_bytes = _make_minimal_xlsx_bytes()
        upload = MagicMock()
        upload.filename = "data.xlsx"
        upload.read = AsyncMock(return_value=xlsx_bytes)

        fake_parser = MagicMock()
        fake_parser.parse_sheet_by_name = AsyncMock(return_value=[{
            "attraction_name": "测试景点",
            "region": "测试区",
            "info_text": "信息",
            "ticket_table_text": "门票",
            "project_table_text": "",
            "cover_image_filename": "ignored.jpg",  # xlsx 模式应忽略
            "gallery_image_filenames": ["ignored2.jpg"],
            "metadata": {},
        }])
        fake_retriever = MagicMock()
        fake_retriever.import_attraction = AsyncMock(return_value=1)
        fake_retriever.search_by_name = MagicMock(return_value=[])

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value={"user_id": "u1"}), \
             patch.object(tq, "_save_upload_to_storage", return_value="storage/xx.xlsx"), \
             patch("attraction_excel_parser.AttractionExcelParser", return_value=fake_parser), \
             patch("attraction_retriever.AttractionRetriever", return_value=fake_retriever):
            result = await tq.import_attraction_excel_to_kb(MagicMock(), upload)

        assert result["success"] is True
        assert result["data"]["imported"] == 1
        # xlsx 模式 → cover/gallery 必须为 None（向后兼容）
        kwargs = fake_retriever.import_attraction.call_args.kwargs
        assert kwargs["cover_image_path"] is None
        assert kwargs["gallery_image_paths"] is None

    @pytest.mark.asyncio
    async def test_import_zip_with_images_passes_paths(self):
        """zip 含 xlsx + images/ → import_attraction 收到图片路径"""
        from src.api import travel_quote as tq

        xlsx_bytes = _make_minimal_xlsx_bytes()
        zip_bytes = _build_zip(xlsx_bytes, images={
            "cover.jpg": b"cover-data",
            "g1.jpg": b"g1-data",
        })

        upload = MagicMock()
        upload.filename = "data.zip"
        upload.read = AsyncMock(return_value=zip_bytes)

        fake_parser = MagicMock()
        fake_parser.parse_sheet_by_name = AsyncMock(return_value=[{
            "attraction_name": "测试景点",
            "region": "测试区",
            "info_text": "信息",
            "ticket_table_text": "门票",
            "project_table_text": "",
            "cover_image_filename": "cover.jpg",
            "gallery_image_filenames": ["g1.jpg"],
            "metadata": {},
        }])
        fake_retriever = MagicMock()
        fake_retriever.import_attraction = AsyncMock(return_value=1)
        fake_retriever.search_by_name = MagicMock(return_value=[])

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value={"user_id": "u1"}), \
             patch.object(tq, "_save_upload_to_storage", return_value="storage/xx.xlsx"), \
             patch("attraction_excel_parser.AttractionExcelParser", return_value=fake_parser), \
             patch("attraction_retriever.AttractionRetriever", return_value=fake_retriever):
            result = await tq.import_attraction_excel_to_kb(MagicMock(), upload)

        assert result["success"] is True
        assert result["data"]["imported"] == 1
        kwargs = fake_retriever.import_attraction.call_args.kwargs
        # cover 和 gallery 都应被传入（路径在临时目录里，endwith 校验）
        assert kwargs["cover_image_path"] is not None
        assert kwargs["cover_image_path"].endswith("cover.jpg")
        assert kwargs["gallery_image_paths"] is not None
        assert len(kwargs["gallery_image_paths"]) == 1
        assert kwargs["gallery_image_paths"][0].endswith("g1.jpg")

    @pytest.mark.asyncio
    async def test_import_rejects_unsupported_filetype(self):
        """上传 .csv → 400"""
        from src.api import travel_quote as tq
        from fastapi import HTTPException

        upload = MagicMock()
        upload.filename = "data.csv"
        upload.read = AsyncMock(return_value=b"a,b,c")

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await tq.import_attraction_excel_to_kb(MagicMock(), upload)
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_import_zip_rejects_zip_slip(self, tmp_path):
        """zip 含 ../ 路径遍历成员时，应拒绝解压（zip slip 防护）"""
        from src.api import travel_quote as tq

        # 构造恶意 zip：含一个 ../escape.jpg 成员
        bad_zip = io.BytesIO()
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("attractions.xlsx", "fake")
            zf.writestr("../escape.jpg", "malicious")
        zip_bytes = bad_zip.getvalue()

        upload = MagicMock()
        upload.filename = "evil.zip"
        upload.read = AsyncMock(return_value=zip_bytes)

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value=None):
            result = await tq.import_attraction_excel_to_kb(MagicMock(), upload)

        assert result["success"] is False
        assert "zip slip" in result["error"] or "非法路径" in result["error"]
