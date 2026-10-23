"""导出兜底单测：导出轮询流程 + 每日配额闸门（redis_client 打桩）"""

import httpx
import pytest

import src.services.tencent_docs.exporter as exporter_mod
from src.services.tencent_docs.client import TencentDocsClient, TencentDocsUnmarshalError
from src.services.tencent_docs.exporter import (
    ExportQuotaExceededError,
    check_and_consume_export_quota,
    export_count_key,
    export_document,
    fetch_document_with_fallback,
)
from src.services.tencent_docs.models import TencentDocsCredentials

CREDS = TencentDocsCredentials(client_id="cid", access_token="tok", open_id="oid-test")


class StubRedis:
    """get/set 打桩，替代 redis_client（避免单测依赖真实 Redis）"""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value


@pytest.fixture
def stub_redis(monkeypatch):
    stub = StubRedis()
    monkeypatch.setattr(exporter_mod, "redis_client", stub)
    return stub


def _export_client(progress_sequence):
    """构造导出全流程 mock client：start -> progress 序列 -> 下载"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/async-export"):
            return httpx.Response(200, json={"ret": 0, "data": {"operationID": "op-1"}})
        if path.endswith("/export-progress"):
            # 序列耗尽后默认卡在 10%：供超时测试等需要"永不完成"的场景
            progress = progress_sequence.pop(0) if progress_sequence else 10
            url = (
                "https://cos.example.com/export/docx/x.docx?sig=1" if progress >= 100 else ""
            )
            return httpx.Response(200, json={"ret": 0, "data": {"progress": progress, "url": url}})
        if path.startswith("https://cos.example.com") or "cos.example.com" in str(request.url.host):
            return httpx.Response(200, content=b"PK\x03\x04 fake docx")
        return httpx.Response(404, json={"ret": -1})

    return TencentDocsClient(CREDS, transport=httpx.MockTransport(handler))


@pytest.mark.unit
class TestExportQuota:
    def test_consume_increments(self, stub_redis):
        used = check_and_consume_export_quota("oid-a", limit=9)
        used = check_and_consume_export_quota("oid-a", limit=9)
        assert used == 2
        assert stub_redis.store[export_count_key("oid-a")] == 2

    def test_quota_isolated_per_open_id(self, stub_redis):
        check_and_consume_export_quota("oid-a", limit=9)
        used = check_and_consume_export_quota("oid-b", limit=9)
        assert used == 1

    def test_quota_exceeded(self, stub_redis):
        for _ in range(9):
            check_and_consume_export_quota("oid-a", limit=9)
        with pytest.raises(ExportQuotaExceededError):
            check_and_consume_export_quota("oid-a", limit=9)


@pytest.mark.unit
class TestExportDocument:
    @pytest.mark.asyncio
    async def test_full_flow_downloads_file(self, tmp_path, stub_redis):
        async with _export_client(progress_sequence=[30, 100]) as client:
            result = await export_document(
                client, "300000000$MQUENxlUkTvJ", str(tmp_path),
                export_type=None, poll_interval=0.0,
            )
        assert result.export_type == "docx"
        assert result.operation_id == "op-1"
        assert result.size_bytes > 0
        assert result.file_path.endswith("tdoc_300000000_MQUENxlUkTvJ.docx")
        import os
        assert os.path.exists(result.file_path)

    @pytest.mark.asyncio
    async def test_pdf_export_suffix(self, tmp_path, stub_redis):
        async with _export_client(progress_sequence=[100]) as client:
            result = await export_document(
                client, "300000000$AAA", str(tmp_path),
                export_type="pdf", poll_interval=0.0,
            )
        assert result.export_type == "pdf"
        assert result.file_path.endswith(".pdf")

    @pytest.mark.asyncio
    async def test_timeout_when_never_complete(self, tmp_path, stub_redis, monkeypatch):
        # progress 一直在 0-99 循环，deadline 极短触发超时
        async with _export_client(progress_sequence=[10] * 100) as client:
            with pytest.raises(TimeoutError):
                await export_document(
                    client, "300000000$AAA", str(tmp_path),
                    poll_interval=0.0, timeout=0.05,
                )


@pytest.mark.unit
class TestFetchWithFallback:
    @pytest.mark.asyncio
    async def test_tree_success_no_export(self, tmp_path, stub_redis):
        document = {"type": "Document", "children": []}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"document": document, "version": 1})

        async with TencentDocsClient(CREDS, transport=httpx.MockTransport(handler)) as client:
            result = await fetch_document_with_fallback(client, "300000000$AAA", str(tmp_path))
        assert result["path_type"] == "tree"
        assert result["document"]["type"] == "Document"
        # 未消耗导出配额
        assert not stub_redis.store

    @pytest.mark.asyncio
    async def test_unmarshal_error_falls_back_to_export(self, tmp_path, stub_redis):
        state = {"phase": "doc"}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.startswith("/openapi/doc/v3/"):
                return httpx.Response(200, json={
                    "code": 400010, "message": "Unmarshal document error"})
            if path.endswith("/async-export"):
                state["phase"] = "exporting"
                return httpx.Response(200, json={"ret": 0, "data": {"operationID": "op-9"}})
            if path.endswith("/export-progress"):
                return httpx.Response(200, json={"ret": 0, "data": {
                    "progress": 100, "url": "https://cos.example.com/f.docx?sig=1"}})
            if request.url.host == "cos.example.com":
                return httpx.Response(200, content=b"PK docx bytes")
            return httpx.Response(404)

        async with TencentDocsClient(CREDS, transport=httpx.MockTransport(handler)) as client:
            result = await fetch_document_with_fallback(client, "300000000$AAA", str(tmp_path))
        assert result["path_type"] == "file"
        assert result["export"].export_type == "docx"
        assert state["phase"] == "exporting"
        # 降级导出消耗了 1 次配额
        assert stub_redis.store[export_count_key("oid-test")] == 1

    @pytest.mark.asyncio
    async def test_quota_exceeded_blocks_fallback(self, tmp_path, stub_redis):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.startswith("/openapi/doc/v3/"):
                return httpx.Response(200, json={"code": 400010, "message": "bad"})
            if path.endswith("/async-export"):
                return httpx.Response(200, json={"ret": 0, "data": {"operationID": "op-0"}})
            return httpx.Response(404)

        async with TencentDocsClient(CREDS, transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ExportQuotaExceededError):
                await fetch_document_with_fallback(
                    client, "300000000$AAA", str(tmp_path), daily_limit=0)
