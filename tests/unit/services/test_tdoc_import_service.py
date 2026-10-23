"""导入服务单测：批次创建（预检/配额闸门/无变化跳过）+ item 执行编排（全 mock，不连库不联网）"""

import os

import pytest

import src.services.tencent_docs.import_service as svc_mod
from src.services.tencent_docs.client import (
    TencentDocsError,
    TencentDocsRateLimitError,
    TencentDocsUnmarshalError,
)
from src.services.tencent_docs.import_service import (
    create_batch,
    ensure_tdoc_category,
    execute_item,
)
from src.services.tencent_docs.models import ExportResult, TencentDocsCredentials

CREDS = TencentDocsCredentials(client_id="cid", access_token="tok", open_id="oid")


# ------------------------------- 打桩设施 -------------------------------

class FakeImportedDocs:
    def __init__(self):
        self.store = {}

    def get(self, tenant_id, file_id):
        return self.store.get((tenant_id, file_id))

    def upsert(self, tenant_id, file_id, document_id, title, last_modify_time):
        self.store[(tenant_id, file_id)] = {
            "document_id": document_id, "title": title, "last_modify_time": last_modify_time,
        }


class FakeItemsDB:
    def __init__(self):
        self.calls = []

    def mark_succeeded(self, item_id, document_id):
        self.calls.append(("succeeded", item_id, document_id))

    def mark_failed(self, item_id, error_message, retry_delay_seconds=None):
        self.calls.append(("failed", item_id, error_message, retry_delay_seconds))

    def refresh_progress(self, batch_id):
        self.calls.append(("refresh", batch_id))


class FakeBatchesDB:
    def __init__(self):
        self.created = []
        self.refreshed = []

    def create(self, batch_id, tenant_id, user_id, items, error_message=""):
        self.created.append({"batch_id": batch_id, "items": items, "error": error_message})
        return batch_id

    def refresh_progress(self, batch_id):
        self.refreshed.append(batch_id)


class FakeKnowledgeService:
    def __init__(self):
        self.uploads = []
        self.deleted = []
        self.categories = set()
        self.upload_result = {"success": True, "document_id": 101}
        self.delete_result = {"success": True}

    async def upload_document(self, file_path, file_filename, user_id=None, tenant_id=None,
                              source_type=None, sub_category=None):
        self.uploads.append({
            "file_path": file_path, "filename": file_filename, "user_id": user_id,
            "tenant_id": tenant_id, "source_type": source_type,
        })
        return dict(self.upload_result)

    async def delete_document(self, doc_id, tenant_id=None, global_view=False):
        self.deleted.append(doc_id)
        return dict(self.delete_result)

    def list_categories(self, tenant_id):
        return [{"source_type": s} for s in self.categories]

    def create_category(self, tenant_id, source_type=None, display_name=None, parent_id=None):
        self.categories.add(source_type)
        return {"success": True}


class FakeClient:
    """替身 TencentDocsClient：按 file_id 决定预检结果"""

    def __init__(self, behavior):
        self.behavior = behavior  # file_id -> "ok" | "unmarshal" | TencentDocsError

    async def get_doc_content(self, file_id):
        b = self.behavior.get(file_id, "ok")
        if b == "unmarshal":
            raise TencentDocsUnmarshalError(400010, "Unmarshal document error")
        if isinstance(b, TencentDocsError):
            raise b
        return {"document": {"type": "Document", "children": []}}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def env(monkeypatch, tmp_path):
    """import_service 全部外部依赖打桩"""
    imported = FakeImportedDocs()
    items_db = FakeItemsDB()
    batches_db = FakeBatchesDB()
    ks = FakeKnowledgeService()

    monkeypatch.setattr(svc_mod, "TdocImportedDocDB", imported)
    monkeypatch.setattr(svc_mod, "TdocImportItemDB", items_db)
    monkeypatch.setattr(svc_mod, "TdocImportBatchDB", batches_db)
    monkeypatch.setattr(svc_mod, "knowledge_service", ks)
    monkeypatch.setattr(svc_mod, "get_credentials", lambda t, u: CREDS)
    monkeypatch.setattr(svc_mod, "get_tenant_storage_dir", lambda tid, scene: str(tmp_path))
    monkeypatch.setattr(svc_mod, "get_tenant_storage_path",
                        lambda tid, scene, fn: os.path.join(str(tmp_path), fn))
    monkeypatch.setattr(svc_mod, "throttle_tdoc_call", _noop_throttle)
    monkeypatch.setattr(svc_mod, "get_export_quota_used", lambda open_id: 0)
    return {"imported": imported, "items": items_db, "batches": batches_db, "ks": ks,
            "tmp_path": tmp_path}


async def _noop_throttle():
    return None


def _patch_client(monkeypatch, behavior):
    monkeypatch.setattr(svc_mod, "TencentDocsClient", lambda creds: FakeClient(behavior))


# ------------------------------- 创建批次 -------------------------------

@pytest.mark.unit
class TestCreateBatch:
    @pytest.mark.asyncio
    async def test_empty_files_rejected(self, env):
        result = await create_batch("t1", "u1", [])
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_non_doc_type_failed_not_queued(self, env, monkeypatch):
        _patch_client(monkeypatch, {})
        result = await create_batch("t1", "u1", [
            {"file_id": "F1", "title": "表格", "doc_type": "sheet", "last_modify_time": 100},
        ])
        # 全部预检失败（无可导入项）：整批拒绝且不落库
        assert result["success"] is False
        assert "sheet" in result["error"]
        assert not env["batches"].created

    @pytest.mark.asyncio
    async def test_preflight_ok_creates_queued_batch(self, env, monkeypatch):
        _patch_client(monkeypatch, {"F1": "ok"})
        result = await create_batch("t1", "u1", [
            {"file_id": "F1", "title": "文档A", "doc_type": "doc", "last_modify_time": 100},
        ])
        assert result["success"] is True
        assert result["queued"] == 1
        batch = env["batches"].created[-1]
        assert batch["items"][0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_unmarshal_needs_export_within_quota(self, env, monkeypatch):
        _patch_client(monkeypatch, {"F1": "unmarshal"})
        result = await create_batch("t1", "u1", [
            {"file_id": "F1", "title": "含表文档", "doc_type": "doc", "last_modify_time": 1},
        ])
        assert result["success"] is True  # 1 篇 <= 剩余 9

    @pytest.mark.asyncio
    async def test_quota_gate_rejects_whole_batch(self, env, monkeypatch):
        _patch_client(monkeypatch, {"F1": "unmarshal", "F2": "unmarshal"})
        monkeypatch.setattr(svc_mod, "get_export_quota_used", lambda open_id: 8)  # 剩 1 < 2
        result = await create_batch("t1", "u1", [
            {"file_id": "F1", "title": "A", "doc_type": "doc", "last_modify_time": 1},
            {"file_id": "F2", "title": "B", "doc_type": "doc", "last_modify_time": 1},
        ])
        assert result["success"] is False
        assert "导出兜底" in result["error"]
        assert not env["batches"].created  # 整批拒绝，不落库

    @pytest.mark.asyncio
    async def test_unchanged_doc_skipped(self, env, monkeypatch):
        env["imported"].store[("t1", "F1")] = {
            "document_id": 9, "last_modify_time": 100,
        }
        _patch_client(monkeypatch, {})
        result = await create_batch("t1", "u1", [
            {"file_id": "F1", "title": "旧文档", "doc_type": "doc", "last_modify_time": 100},
        ])
        assert result["success"] is True
        assert result["skipped"] == 1
        batch = env["batches"].created[-1]
        assert batch["items"][0]["status"] == "skipped"
        # 变化后（last_modify_time 推进）不再跳过
        result2 = await create_batch("t1", "u1", [
            {"file_id": "F1", "title": "旧文档", "doc_type": "doc", "last_modify_time": 200},
        ])
        assert result2["success"] is True
        assert env["batches"].created[-1]["items"][0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_preflight_api_error_marks_failed(self, env, monkeypatch):
        _patch_client(monkeypatch, {"F1": TencentDocsError(300001, "no permission")})
        result = await create_batch("t1", "u1", [
            {"file_id": "F1", "title": "A", "doc_type": "doc", "last_modify_time": 1},
        ])
        assert result["success"] is False
        assert "预检失败" in result["error"]


# ------------------------------- 执行 item -------------------------------

def _make_item(**overrides):
    item = {
        "id": 1, "batch_id": "tdocb_x", "tenant_id": "t1", "user_id": "u1",
        "tdoc_file_id": "300000000$AAA", "title": "测试文档",
        "doc_type": "doc", "status": "running", "last_modify_time": 100,
        "retry_count": 0,
    }
    item.update(overrides)
    return item


@pytest.mark.unit
class TestExecuteItem:
    @pytest.mark.asyncio
    async def test_tree_path_uploads_markdown(self, env, monkeypatch):
        monkeypatch.setattr(
            svc_mod, "fetch_document_with_fallback", _fake_fetch_tree)
        await execute_item(_make_item(), CREDS)
        assert env["items"].calls[0] == ("succeeded", 1, 101)
        upload = env["ks"].uploads[0]
        assert upload["source_type"] == "tencent_docs"
        assert upload["filename"] == "测试文档.md"
        assert os.path.exists(upload["file_path"])
        with open(upload["file_path"], encoding="utf-8") as f:
            assert f.read() == "# 标题\n"
        # 无旧映射：不删旧文档，写入映射
        assert env["ks"].deleted == []
        assert env["imported"].store[("t1", "300000000$AAA")]["document_id"] == 101

    @pytest.mark.asyncio
    async def test_export_fallback_uploads_docx(self, env, monkeypatch):
        monkeypatch.setattr(
            svc_mod, "fetch_document_with_fallback", _fake_fetch_file)
        await execute_item(_make_item(), CREDS)
        assert env["items"].calls[0] == ("succeeded", 1, 101)
        assert env["ks"].uploads[0]["filename"] == "测试文档.docx"

    @pytest.mark.asyncio
    async def test_overwrite_deletes_old_after_new_created(self, env, monkeypatch):
        env["imported"].store[("t1", "300000000$AAA")] = {
            "document_id": 9, "title": "旧版", "last_modify_time": 50,
        }
        monkeypatch.setattr(svc_mod, "fetch_document_with_fallback", _fake_fetch_tree)
        await execute_item(_make_item(), CREDS)
        assert env["ks"].deleted == [9]
        assert env["imported"].store[("t1", "300000000$AAA")]["document_id"] == 101

    @pytest.mark.asyncio
    async def test_upload_failure_keeps_old_mapping(self, env, monkeypatch):
        env["imported"].store[("t1", "300000000$AAA")] = {
            "document_id": 9, "title": "旧版", "last_modify_time": 50,
        }
        env["ks"].upload_result = {"success": False, "error": "embedding 失败"}
        monkeypatch.setattr(svc_mod, "fetch_document_with_fallback", _fake_fetch_tree)
        await execute_item(_make_item(), CREDS)
        # 新文档入库失败：不删旧版、不更新映射、item 失败
        assert env["ks"].deleted == []
        assert env["imported"].store[("t1", "300000000$AAA")]["document_id"] == 9
        assert env["items"].calls[0][0] == "failed"

    @pytest.mark.asyncio
    async def test_rate_limit_schedules_retry(self, env, monkeypatch):
        async def fetch_rate_limit(client, file_id, dest_dir, **kw):
            raise TencentDocsRateLimitError(9998, "too many requests", retryable=True)

        monkeypatch.setattr(svc_mod, "fetch_document_with_fallback", fetch_rate_limit)
        await execute_item(_make_item(retry_count=0), CREDS)
        call = env["items"].calls[0]
        assert call[0] == "failed"
        assert call[3] == 300  # 首次退避 300s

    @pytest.mark.asyncio
    async def test_quota_exceeded_marks_failed_no_retry(self, env, monkeypatch):
        async def fetch_quota(client, file_id, dest_dir, **kw):
            from src.services.tencent_docs.exporter import ExportQuotaExceededError
            raise ExportQuotaExceededError("quota used up")

        monkeypatch.setattr(svc_mod, "fetch_document_with_fallback", fetch_quota)
        await execute_item(_make_item(), CREDS)
        call = env["items"].calls[0]
        assert call[0] == "failed"
        assert call[3] is None  # 无退避（非频控类失败不自动重试）
        assert "配额" in call[2]

    @pytest.mark.asyncio
    async def test_batch_progress_refreshed_in_finally(self, env, monkeypatch):
        monkeypatch.setattr(svc_mod, "fetch_document_with_fallback", _fake_fetch_tree)
        await execute_item(_make_item(), CREDS)
        assert "tdocb_x" in env["batches"].refreshed

    @pytest.mark.asyncio
    async def test_empty_markdown_fails(self, env, monkeypatch):
        async def fetch_empty(client, file_id, dest_dir, **kw):
            return {"path_type": "tree", "document": {"type": "Document", "children": []}}

        monkeypatch.setattr(svc_mod, "fetch_document_with_fallback", fetch_empty)
        await execute_item(_make_item(), CREDS)
        assert env["items"].calls[0][0] == "failed"
        assert "为空" in env["items"].calls[0][2]


# ------------------------------- 分类登记 -------------------------------

@pytest.mark.unit
class TestEnsureCategory:
    def test_creates_when_missing(self, env):
        ensure_tdoc_category("t1")
        assert "tencent_docs" in env["ks"].categories

    def test_idempotent_when_exists(self, env):
        env["ks"].categories.add("tencent_docs")
        ensure_tdoc_category("t1")  # 不抛、不重复建
        assert "tencent_docs" in env["ks"].categories

    def test_failure_does_not_raise(self, env):
        async def boom(*a, **kw):
            raise RuntimeError("db down")

        env["ks"].list_categories = boom
        ensure_tdoc_category("t1")  # 吞异常不阻塞导入


# ------------------------------- fetch 替身 -------------------------------

async def _fake_fetch_tree(client, file_id, dest_dir, **kw):
    return {"path_type": "tree", "document": {
        "type": "Document", "children": [
            {"type": "Paragraph", "children": [{"type": "Text", "text": "# 标题"}]},
        ],
    }}


async def _fake_fetch_file(client, file_id, dest_dir, **kw):
    import os
    path = os.path.join(dest_dir, "tdoc_export.docx")
    with open(path, "wb") as f:
        f.write(b"PK docx")
    return {"path_type": "file", "export": ExportResult(
        file_id=file_id, export_type="docx", file_path=path, size_bytes=7)}
