# -*- coding: utf-8 -*-
"""
景点图片管理 API（PATCH /kb/attractions/{doc_id}/images）单元测试

覆盖：
1. replace_cover：上传/替换封面，metadata.images.cover 被更新为新 file_id
2. add_gallery：追加图集，metadata.images.gallery 列表追加新 file_id（去重）
3. remove_cover：移除封面，metadata.images.cover=None（不写入 cover 键）
4. remove_gallery_file_id：从图集中删除指定 file_id
5. 文档不存在 → 404
6. 不支持的动作 → 400
7. 缺少必需参数（replace_cover 无 file）→ 400
8. 不支持的图片格式 → 400
9. 路径无关：metadata 其他键保留不变
10. 空状态（cover+gallery 全空）→ metadata 中不写入 images 键

测试策略：
- 直接 await 调用 patch_attraction_images，绕过 FastAPI 路由层
- mock _get_tenant_id / get_current_user / get_db_connection
- mock ImageRegistry.register（避免真实磁盘 + Redis）
"""
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 触发 src.api.travel_quote 的依赖 import（确保模块可加载）
from src.api import travel_quote as tq


def _mock_db_connection(metadata_dict):
    """构造一个 mock 的 get_db_connection，row.metadata 返回指定 dict（自动 JSON 序列化）

    每次 with 进入返回独立的 conn；UPDATE SQL 通过 cursor.execute 捕获。
    """
    # 自动 JSON 序列化（模拟 PostgreSQL 返回的字符串）
    metadata_str = json.dumps(metadata_dict, ensure_ascii=False) if metadata_dict else None

    captured = {"update_sqls": [], "update_params": []}

    def _make_conn():
        cursor = MagicMock()
        row = {"id": 1, "metadata": metadata_str}
        cursor.fetchone.return_value = row

        def _execute(sql, params=None):
            # 记录 UPDATE 调用
            if "UPDATE documents SET metadata" in sql:
                captured["update_sqls"].append(sql)
                captured["update_params"].append(params)

        cursor.execute.side_effect = _execute
        conn = MagicMock()
        conn.cursor.return_value = cursor
        return conn

    return _make_conn, captured


def _make_upload_file(filename: str, content: bytes = b"fake-image"):
    """构造一个 mock UploadFile"""
    upload = MagicMock()
    upload.filename = filename
    upload.read = AsyncMock(return_value=content)
    return upload


class TestPatchAttractionImages:
    """PATCH /kb/attractions/{doc_id}/images"""

    @pytest.mark.asyncio
    async def test_replace_cover_sets_cover_file_id(self, tmp_path):
        """action=replace_cover：上传新封面，cover 被替换为新 file_id"""
        from src.core.image_asset import ImageRef

        existing_meta = {"category": "natural", "images": {"cover": "file_OLD123"}}
        _make_conn, captured = _mock_db_connection(existing_meta)

        fake_ref = ImageRef(
            file_id="file_NEW456",
            download_url="/api/files/file_NEW456/download",
            display_name="cover.jpg",
            mime_type="image/jpeg",
            size_bytes=100,
            source="knowledge_base",
            usage="thumbnail",
        )

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value={"user_id": "u1"}), \
             patch.object(tq, "get_db_connection", side_effect=lambda: _FakeCtx(_make_conn())), \
             patch("src.core.image_asset.get_image_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.register = AsyncMock(return_value=fake_ref)
            mock_get_reg.return_value = mock_reg

            result = await tq.patch_attraction_images(
                request=MagicMock(),
                doc_id=42,
                action="replace_cover",
                file=_make_upload_file("cover.jpg"),
                file_id=None,
            )

        assert result["success"] is True
        assert result["data"]["cover"] == "file_NEW456"
        # UPDATE 被调用一次
        assert len(captured["update_params"]) == 1
        params = captured["update_params"][0]
        merged = json.loads(params[0])
        assert merged["images"]["cover"] == "file_NEW456"
        # 其他 metadata 保留
        assert merged["category"] == "natural"

    @pytest.mark.asyncio
    async def test_add_gallery_appends_new_file_id(self, tmp_path):
        """action=add_gallery：图集追加新 file_id（已存在的不重复加）"""
        from src.core.image_asset import ImageRef

        existing_meta = {"images": {"cover": "file_C", "gallery": ["file_G1"]}}
        _make_conn, captured = _mock_db_connection(existing_meta)

        fake_ref = ImageRef(
            file_id="file_G2",
            download_url="/api/files/file_G2/download",
            display_name="g2.jpg",
            mime_type="image/jpeg",
            size_bytes=100,
            source="knowledge_base",
            usage="inline",
        )

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value=None), \
             patch.object(tq, "get_db_connection", side_effect=lambda: _FakeCtx(_make_conn())), \
             patch("src.core.image_asset.get_image_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.register = AsyncMock(return_value=fake_ref)
            mock_get_reg.return_value = mock_reg

            result = await tq.patch_attraction_images(
                request=MagicMock(),
                doc_id=42,
                action="add_gallery",
                file=_make_upload_file("g2.jpg"),
                file_id=None,
            )

        assert result["data"]["gallery"] == ["file_G1", "file_G2"]
        params = captured["update_params"][0]
        merged = json.loads(params[0])
        assert merged["images"]["gallery"] == ["file_G1", "file_G2"]
        assert merged["images"]["cover"] == "file_C"

    @pytest.mark.asyncio
    async def test_add_gallery_dedupes_existing_file_id(self, tmp_path):
        """add_gallery 同一 file_id 二次添加不重复"""
        from src.core.image_asset import ImageRef

        existing_meta = {"images": {"gallery": ["file_G1"]}}
        _make_conn, captured = _mock_db_connection(existing_meta)

        fake_ref = ImageRef(
            file_id="file_G1",  # 与已存在相同
            download_url="/api/files/file_G1/download",
            display_name="g1.jpg",
            mime_type="image/jpeg",
            size_bytes=100,
            source="knowledge_base",
            usage="inline",
        )

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value=None), \
             patch.object(tq, "get_db_connection", side_effect=lambda: _FakeCtx(_make_conn())), \
             patch("src.core.image_asset.get_image_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.register = AsyncMock(return_value=fake_ref)
            mock_get_reg.return_value = mock_reg

            result = await tq.patch_attraction_images(
                request=MagicMock(),
                doc_id=42,
                action="add_gallery",
                file=_make_upload_file("g1.jpg"),
                file_id=None,
            )

        # 去重：gallery 仍只有 1 个 file_G1
        assert result["data"]["gallery"] == ["file_G1"]

    @pytest.mark.asyncio
    async def test_remove_cover_clears_cover_field(self):
        """action=remove_cover：cover 被清空，metadata.images 不含 cover 键"""
        existing_meta = {"category": "natural", "images": {"cover": "file_C", "gallery": ["file_G1"]}}
        _make_conn, captured = _mock_db_connection(existing_meta)

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value=None), \
             patch.object(tq, "get_db_connection", side_effect=lambda: _FakeCtx(_make_conn())), \
             patch("src.core.image_asset.get_image_registry"):
            result = await tq.patch_attraction_images(
                request=MagicMock(),
                doc_id=42,
                action="remove_cover",
                file=None,
                file_id=None,
            )

        assert result["data"]["cover"] is None
        assert result["data"]["gallery"] == ["file_G1"]
        merged = json.loads(captured["update_params"][0][0])
        # images 中只保留 gallery，不写 cover
        assert "cover" not in merged["images"]
        assert merged["images"]["gallery"] == ["file_G1"]
        assert merged["category"] == "natural"

    @pytest.mark.asyncio
    async def test_remove_gallery_file_id_removes_specific_id(self):
        """action=remove_gallery_file_id：从 gallery 中删除指定 file_id"""
        existing_meta = {"images": {"gallery": ["file_G1", "file_G2", "file_G3"]}}
        _make_conn, captured = _mock_db_connection(existing_meta)

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value=None), \
             patch.object(tq, "get_db_connection", side_effect=lambda: _FakeCtx(_make_conn())), \
             patch("src.core.image_asset.get_image_registry"):
            result = await tq.patch_attraction_images(
                request=MagicMock(),
                doc_id=42,
                action="remove_gallery_file_id",
                file=None,
                file_id="file_G2",
            )

        assert result["data"]["gallery"] == ["file_G1", "file_G3"]

    @pytest.mark.asyncio
    async def test_remove_gallery_last_id_drops_images_key(self):
        """删图后 cover+gallery 全空时，metadata 不保留 images 键"""
        existing_meta = {"category": "natural", "images": {"gallery": ["file_G1"]}}
        _make_conn, captured = _mock_db_connection(existing_meta)

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value=None), \
             patch.object(tq, "get_db_connection", side_effect=lambda: _FakeCtx(_make_conn())), \
             patch("src.core.image_asset.get_image_registry"):
            await tq.patch_attraction_images(
                request=MagicMock(),
                doc_id=42,
                action="remove_gallery_file_id",
                file=None,
                file_id="file_G1",
            )

        merged = json.loads(captured["update_params"][0][0])
        # images 键被完全移除（cover+gallery 全空）
        assert "images" not in merged
        # 其他 metadata 保留
        assert merged["category"] == "natural"

    @pytest.mark.asyncio
    async def test_doc_not_found_returns_404(self):
        """文档不存在 → 404"""
        from fastapi import HTTPException

        def _make_empty_conn():
            cursor = MagicMock()
            cursor.fetchone.return_value = None
            conn = MagicMock()
            conn.cursor.return_value = cursor
            return conn

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value=None), \
             patch.object(tq, "get_db_connection", side_effect=lambda: _FakeCtx(_make_empty_conn())):
            with pytest.raises(HTTPException) as exc_info:
                await tq.patch_attraction_images(
                    request=MagicMock(),
                    doc_id=999,
                    action="remove_cover",
                    file=None,
                    file_id=None,
                )
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unsupported_action_returns_400(self):
        """不支持的 action → 400"""
        from fastapi import HTTPException

        _make_conn, _ = _mock_db_connection({"category": "natural"})

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value=None), \
             patch.object(tq, "get_db_connection", side_effect=lambda: _FakeCtx(_make_conn())):
            with pytest.raises(HTTPException) as exc_info:
                await tq.patch_attraction_images(
                    request=MagicMock(),
                    doc_id=42,
                    action="bogus_action",
                    file=None,
                    file_id=None,
                )
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_replace_cover_without_file_returns_400(self):
        """replace_cover 缺 file 参数 → 400"""
        from fastapi import HTTPException

        _make_conn, _ = _mock_db_connection({"category": "natural"})

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value=None), \
             patch.object(tq, "get_db_connection", side_effect=lambda: _FakeCtx(_make_conn())):
            with pytest.raises(HTTPException) as exc_info:
                await tq.patch_attraction_images(
                    request=MagicMock(),
                    doc_id=42,
                    action="replace_cover",
                    file=None,
                    file_id=None,
                )
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_replace_cover_unsupported_image_format_returns_400(self):
        """replace_cover 上传 .gif → 400"""
        from fastapi import HTTPException

        _make_conn, _ = _mock_db_connection({"category": "natural"})

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value=None), \
             patch.object(tq, "get_db_connection", side_effect=lambda: _FakeCtx(_make_conn())):
            with pytest.raises(HTTPException) as exc_info:
                await tq.patch_attraction_images(
                    request=MagicMock(),
                    doc_id=42,
                    action="replace_cover",
                    file=_make_upload_file("cover.gif"),
                    file_id=None,
                )
            assert exc_info.value.status_code == 400
            assert "不支持" in exc_info.value.detail or "格式" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_remove_gallery_file_id_without_file_id_returns_400(self):
        """remove_gallery_file_id 缺 file_id 参数 → 400"""
        from fastapi import HTTPException

        _make_conn, _ = _mock_db_connection({"category": "natural"})

        with patch.object(tq, "_get_tenant_id", return_value="t1"), \
             patch.object(tq, "get_current_user", return_value=None), \
             patch.object(tq, "get_db_connection", side_effect=lambda: _FakeCtx(_make_conn())):
            with pytest.raises(HTTPException) as exc_info:
                await tq.patch_attraction_images(
                    request=MagicMock(),
                    doc_id=42,
                    action="remove_gallery_file_id",
                    file=None,
                    file_id=None,
                )
            assert exc_info.value.status_code == 400


class _FakeCtx:
    """模拟 with get_db_connection() as conn: 的上下文管理器"""
    def __init__(self, conn):
        self._conn = conn
    def __enter__(self):
        return self._conn
    def __exit__(self, *args):
        return False
