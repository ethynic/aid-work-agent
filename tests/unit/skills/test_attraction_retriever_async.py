"""
AttractionRetriever.import_attraction 异步改造 + 图片参数单元测试

P1.2.1：验证改造后的 import_attraction 满足：
1. 传 cover_image_path：调 ImageRegistry.register(source=knowledge_base, usage=thumbnail)
   并把 file_id 写入 documents.metadata.images.cover
2. 传 gallery_image_paths：循环 register，写入 metadata.images.gallery（file_id 列表）
3. 不传图片参数：行为与现状一致，metadata 中无 "images" 键
4. register 抛异常：仅 warning 不阻断，doc_id 仍创建
5. 同步包装 import_attraction_sync 能工作

不连真实 DB / Redis：mock _embed、_get_conn、get_image_registry。
"""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import attraction_retriever  # noqa: E402


# ============================================================
# 测试基础设施：FakeConn 记录所有 execute 调用
# ============================================================

class FakeConn:
    """模拟 DB 连接上下文：记录所有 execute 调用，按需返回 fetchone 结果。"""

    def __init__(self, fetchone_seq=None):
        # fetchone_seq: 用于 RETURNING id 的多轮 fetchone（INSERT document 拿 doc_id，
        # INSERT chunk_0 拿 chunk_id）
        self._fetchone_seq = list(fetchone_seq or [])
        self._fetchone_idx = 0
        self.calls = []  # [(sql_upper, args), ...]

    def execute(self, sql, args=None):
        self.calls.append((sql.upper(), args))
        return None

    def fetchone(self):
        if self._fetchone_idx < len(self._fetchone_seq):
            r = self._fetchone_seq[self._fetchone_idx]
            self._fetchone_idx += 1
            return r
        return None

    def fetchall(self):
        return []

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_ref(file_id, **overrides):
    """构造 ImageRef-like 对象（避免依赖 ImageRef Pydantic 类的 import 副作用）。"""
    base = {
        "file_id": file_id,
        "download_url": f"/api/files/{file_id}/download",
        "display_name": f"{file_id}.jpg",
    }
    base.update(overrides)
    ref = MagicMock()
    for k, v in base.items():
        setattr(ref, k, v)
    return ref


@pytest.fixture
def retriever():
    r = attraction_retriever.AttractionRetriever()
    # 全程 mock _embed（避免真实调 dashscope API）
    with patch.object(r, '_embed', return_value=[0.1] * 8):
        yield r


@pytest.fixture
def fake_conn_factory():
    """提供 FakeConn 工厂，便于测试中按需构造多次 _get_conn 的返回值"""
    instances = []

    def _factory(fetchone_seq=None):
        conn = FakeConn(fetchone_seq=fetchone_seq)
        instances.append(conn)
        return conn

    return _factory, instances


# ============================================================
# 测试用例
# ============================================================

class TestImportAttractionAsync:
    """异步 import_attraction 的核心场景"""

    @pytest.mark.asyncio
    async def test_import_attraction_with_cover_image(
        self, retriever, fake_conn_factory
    ):
        """传 cover_image_path：注册图片并 UPDATE documents.metadata.images.cover"""
        factory, _ = fake_conn_factory

        def _get_conn():
            # 第一次 _get_conn：INSERT document + chunks + chunks_vec
            # fetchone 序列：doc_id, chunk_id_0
            return factory(fetchone_seq=[{"id": 42}, {"id": 1001}])

        # 第二次 _get_conn：UPDATE documents（仅当有图片注册成功时）
        # 这里也需要返回一个 FakeConn 用于 UPDATE
        with patch.object(retriever, '_get_conn', side_effect=_get_conn):
            cover_ref = _make_ref("file_coverxxx")
            mock_registry = MagicMock()
            mock_registry.register = AsyncMock(return_value=cover_ref)
            with patch(
                'src.core.image_asset.get_image_registry',
                return_value=mock_registry,
            ):
                doc_id = await retriever.import_attraction(
                    tenant_id="tenant_1",
                    attraction_name="黄果树瀑布",
                    region="贵州",
                    info_text="黄果树瀑布信息",
                    ticket_table_text="门票表",
                    project_table_text="项目表",
                    cover_image_path="/tmp/cover.jpg",
                    user_id="user_1",
                    source_file="attractions.xlsx",
                )

        assert doc_id == 42

        # 验证 register 被调用一次（cover），参数符合契约
        assert mock_registry.register.call_count == 1
        call_kwargs = mock_registry.register.call_args.kwargs
        assert call_kwargs["source"] == "knowledge_base"
        assert call_kwargs["usage"] == "thumbnail"
        assert call_kwargs["linked_doc_id"] == 42
        assert call_kwargs["source_ref"] == "attractions.xlsx"
        assert call_kwargs["tenant_id"] == "tenant_1"
        assert call_kwargs["user_id"] == "user_1"
        assert call_kwargs["display_name"] == "cover.jpg"

        # 第二次 _get_conn 应该有一次 UPDATE documents 调用
        _, instances = fake_conn_factory
        assert len(instances) >= 2, "应该有两次 _get_conn：INSERT 和 UPDATE"
        update_conn = instances[1]
        update_sqls = [sql for sql, _ in update_conn.calls]
        assert any("UPDATE DOCUMENTS" in s for s in update_sqls)
        # 验证 metadata 含 images.cover
        update_args = [args for _, args in update_conn.calls if args]
        update_meta_json = update_args[0][0]
        meta = json.loads(update_meta_json)
        assert meta["images"]["cover"] == "file_coverxxx"

    @pytest.mark.asyncio
    async def test_import_attraction_with_gallery(
        self, retriever, fake_conn_factory
    ):
        """传 gallery_image_paths：循环 register，metadata.images.gallery 是 file_id 列表"""
        factory, _ = fake_conn_factory

        def _get_conn():
            return factory(fetchone_seq=[{"id": 50}, {"id": 1002}])

        with patch.object(retriever, '_get_conn', side_effect=_get_conn):
            refs = [_make_ref(f"file_g{i}") for i in range(3)]
            mock_registry = MagicMock()
            mock_registry.register = AsyncMock(side_effect=refs)
            with patch(
                'src.core.image_asset.get_image_registry',
                return_value=mock_registry,
            ):
                doc_id = await retriever.import_attraction(
                    tenant_id="tenant_1",
                    attraction_name="小七孔",
                    region="贵州",
                    info_text="小七孔信息",
                    ticket_table_text="门票表",
                    gallery_image_paths=["/tmp/g1.jpg", "/tmp/g2.jpg", "/tmp/g3.jpg"],
                )

        assert doc_id == 50
        assert mock_registry.register.call_count == 3

        _, instances = fake_conn_factory
        update_conn = instances[1]
        update_args = [args for _, args in update_conn.calls if args]
        meta = json.loads(update_args[0][0])
        assert meta["images"]["gallery"] == ["file_g0", "file_g1", "file_g2"]
        assert "cover" not in meta["images"]

    @pytest.mark.asyncio
    async def test_import_attraction_without_images(self, retriever, fake_conn_factory):
        """不传图片参数：行为与现状一致，无 UPDATE documents，metadata 无 images 键"""
        factory, instances = fake_conn_factory

        def _get_conn():
            return factory(fetchone_seq=[{"id": 88}, {"id": 2000}])

        with patch.object(retriever, '_get_conn', side_effect=_get_conn):
            mock_registry = MagicMock()
            mock_registry.register = AsyncMock()
            with patch(
                'src.core.image_asset.get_image_registry',
                return_value=mock_registry,
            ):
                doc_id = await retriever.import_attraction(
                    tenant_id="tenant_1",
                    attraction_name="测试景点",
                    region="某地",
                    info_text="info",
                    ticket_table_text="ticket",
                    project_table_text="project",
                    metadata={"custom_key": "custom_value"},
                )

        assert doc_id == 88
        # register 没被调用
        assert mock_registry.register.call_count == 0
        # 只有一次 _get_conn（INSERT document + chunks + chunks_vec），没有第二次 UPDATE
        assert len(instances) == 1
        # 验证 INSERT documents 时 metadata 只有原始 custom_key，无 images
        insert_doc_call = instances[0].calls[0]
        sql_upper, args = insert_doc_call
        assert "INSERT INTO DOCUMENTS" in sql_upper
        meta_arg = args[5]  # SQL 中 metadata 位置：user_id, tenant_id, title, source_type, file_path, metadata, summary
        meta = json.loads(meta_arg)
        assert meta == {"custom_key": "custom_value"}
        assert "images" not in meta

    @pytest.mark.asyncio
    async def test_import_attraction_image_register_failure_does_not_block(
        self, retriever, fake_conn_factory
    ):
        """mock register 抛异常：仅 warning 不阻断，doc_id 仍创建，
        且不会因 UPDATE documents 报错"""
        factory, instances = fake_conn_factory

        def _get_conn():
            return factory(fetchone_seq=[{"id": 77}, {"id": 3000}])

        with patch.object(retriever, '_get_conn', side_effect=_get_conn):
            mock_registry = MagicMock()
            mock_registry.register = AsyncMock(side_effect=RuntimeError("Redis down"))
            with patch(
                'src.core.image_asset.get_image_registry',
                return_value=mock_registry,
            ):
                doc_id = await retriever.import_attraction(
                    tenant_id="tenant_1",
                    attraction_name="故障景点",
                    region="某地",
                    info_text="info",
                    ticket_table_text="ticket",
                    cover_image_path="/tmp/missing.jpg",
                    gallery_image_paths=["/tmp/g1.jpg"],
                )

        assert doc_id == 77
        # 两次 register 被尝试调用（cover 一次 + gallery 一次），都抛异常
        assert mock_registry.register.call_count == 2
        # 因为图片都失败了，images_meta 为空，不会触发 UPDATE documents
        assert len(instances) == 1

    @pytest.mark.asyncio
    async def test_import_attraction_partial_gallery_failure(
        self, retriever, fake_conn_factory
    ):
        """gallery 中部分图片注册失败：成功的写入 gallery，失败的跳过"""
        factory, _ = fake_conn_factory

        def _get_conn():
            return factory(fetchone_seq=[{"id": 99}, {"id": 4000}])

        with patch.object(retriever, '_get_conn', side_effect=_get_conn):
            ok_ref = _make_ref("file_ok1")
            mock_registry = MagicMock()
            mock_registry.register = AsyncMock(
                side_effect=[ok_ref, RuntimeError("fail"), _make_ref("file_ok2")]
            )
            with patch(
                'src.core.image_asset.get_image_registry',
                return_value=mock_registry,
            ):
                doc_id = await retriever.import_attraction(
                    tenant_id="tenant_1",
                    attraction_name="部分失败景点",
                    region="某地",
                    info_text="info",
                    ticket_table_text="ticket",
                    gallery_image_paths=["/tmp/g1.jpg", "/tmp/g2.jpg", "/tmp/g3.jpg"],
                )

        assert doc_id == 99
        assert mock_registry.register.call_count == 3
        _, instances = fake_conn_factory
        update_conn = instances[1]
        update_args = [args for _, args in update_conn.calls if args]
        meta = json.loads(update_args[0][0])
        # gallery 只包含成功的两张
        assert meta["images"]["gallery"] == ["file_ok1", "file_ok2"]


class TestImportAttractionSyncWrapper:
    """同步兼容包装 import_attraction_sync"""

    def test_sync_wrapper_works_outside_event_loop(self):
        """不在 event loop 内时，import_attraction_sync 用 asyncio.run 包装成功调用"""
        retriever = attraction_retriever.AttractionRetriever()

        # mock _embed / _get_conn / ImageRegistry，全程不依赖真实环境
        factory_instances = []

        def _get_conn():
            conn = FakeConn(fetchone_seq=[{"id": 123}, {"id": 5000}])
            factory_instances.append(conn)
            return conn

        with patch.object(retriever, '_embed', return_value=[0.1] * 8), \
             patch.object(retriever, '_get_conn', side_effect=_get_conn):
            mock_registry = MagicMock()
            mock_registry.register = AsyncMock()
            with patch(
                'src.core.image_asset.get_image_registry',
                return_value=mock_registry,
            ):
                doc_id = retriever.import_attraction_sync(
                    tenant_id="tenant_x",
                    attraction_name="sync 景点",
                    region="某地",
                    info_text="info",
                    ticket_table_text="ticket",
                )

        assert doc_id == 123
        # 没传图片参数时 register 不被调用，只有一次 _get_conn
        assert mock_registry.register.call_count == 0
        assert len(factory_instances) == 1
