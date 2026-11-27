"""跨执行历史附件重物化 + public_runner cancel_reason 透出单元测试（2026-11-27）。

覆盖：
- attachments._materialize_history_attachments：物化到 history_files/、files_context 路径
- 历史内 file_id 去重、与当前消息附件去重
- file_id 解析失败（TTL 过期/文件已清理）跳过不阻断
- 当前消息无附件时仍创建工作区
- 重物化异常不阻断 prepare（fail-open）
- HistoryRepository.read_user_attachments：attachments JSON 解析、异常 fail-open 返回 []
- public_runner：仅终态 cancelled + superseded_by_new_message 白名单透出 cancel_reason

外部依赖 mock：redis_client / resolve_scoped_uploaded_file 在调用点 import，
patch 源模块引用；DB 连接 mock 到模块引用；附件用真实 tmp 文件。
"""

import base64
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.agent_engine.contracts import Identity
from src.services.agent_runner.runtime.attachments import AttachmentAssembler
from src.services.agent_runner.runtime.history_repository import HistoryRepository
from src.services.agent_runner.public_view import public_runner

_PDF_BYTES = b"%PDF-1.4 test"


@pytest.fixture
def identity():
    return Identity("tenant-1", "user-1", "session-1", "web_c", "channel")


# ============== 历史附件重物化 ==============

class TestMaterializeHistoryAttachments:
    def _patch_resolve(self, monkeypatch, mapping):
        """mapping: file_id -> resolved dict（None=失效）。"""
        fake_redis = MagicMock()
        fake_redis.make_key.side_effect = lambda *parts: ":".join(parts)
        fake_redis.hgetall.return_value = {}
        monkeypatch.setattr("src.core.redis_client.redis_client", fake_redis)
        monkeypatch.setattr("src.core.storage.configured_storage_root", lambda: "/tmp")
        monkeypatch.setattr("src.core.storage.resolve_scoped_uploaded_file",
                            lambda fid, tid, **kw: mapping.get(fid))

    def test_materialize_and_list_path(self, monkeypatch, identity, tmp_path):
        src = tmp_path / "file_abc123.pdf"
        src.write_bytes(_PDF_BYTES)
        self._patch_resolve(monkeypatch, {"file_abc123": {
            "file_id": "file_abc123", "name": "报告.pdf", "path": str(src), "size": len(_PDF_BYTES)}})
        assembler = AttachmentAssembler()
        ws = tmp_path / "ws1"
        ws.mkdir()
        context = assembler._materialize_history_attachments(
            [{"file_id": "file_abc123", "name": "报告.pdf", "mime_type": "application/pdf"}],
            ws, identity, [])
        assert "Historical attachments" in context
        assert "Full path: `" in context
        copied = list((ws / "history_files").iterdir())
        assert len(copied) == 1
        assert copied[0].read_bytes() == _PDF_BYTES

    def test_history_dedup_and_current_dedup(self, monkeypatch, identity, tmp_path):
        src = tmp_path / "file_abc123.pdf"
        src.write_bytes(_PDF_BYTES)
        self._patch_resolve(monkeypatch, {"file_abc123": {
            "file_id": "file_abc123", "name": "doc.pdf", "path": str(src), "size": len(_PDF_BYTES)}})
        assembler = AttachmentAssembler()
        ws = tmp_path / "ws2"
        ws.mkdir()
        # 历史内重复 + 与当前消息附件重复
        history = [{"file_id": "file_abc123", "name": "doc.pdf"},
                   {"file_id": "file_abc123", "name": "doc.pdf"}]
        current = [{"file_id": "file_abc123", "name": "doc.pdf", "url": str(src)}]
        context = assembler._materialize_history_attachments(history, ws, identity, current)
        assert context == ""
        assert not (ws / "history_files").exists()

    def test_expired_file_skipped(self, monkeypatch, identity, tmp_path):
        self._patch_resolve(monkeypatch, {"file_gone": None})
        assembler = AttachmentAssembler()
        ws = tmp_path / "ws3"
        ws.mkdir()
        context = assembler._materialize_history_attachments(
            [{"file_id": "file_gone", "name": "old.pdf"}], ws, identity, [])
        assert context == ""
        assert not (ws / "history_files").exists()

    def test_prepare_history_only_creates_workspace(self, monkeypatch, identity, tmp_path):
        src = tmp_path / "file_hist0001.pdf"
        src.write_bytes(_PDF_BYTES)
        self._patch_resolve(monkeypatch, {"file_hist0001": {
            "file_id": "file_hist0001", "name": "doc.pdf", "path": str(src), "size": len(_PDF_BYTES)}})
        assembler = AttachmentAssembler()
        enhanced, injection, workspace = assembler.prepare(
            "刚才那个文件继续处理", [], identity, None,
            history_attachments=[{"file_id": "file_hist0001", "name": "doc.pdf"}])
        assert workspace  # 当前消息无附件也创建工作区
        assert "Historical attachments" in enhanced
        assert Path(workspace, "history_files").exists()

    def test_materialize_exception_not_raised(self, monkeypatch, identity, tmp_path):
        def _boom(*args, **kwargs):
            raise RuntimeError("redis down")
        monkeypatch.setattr("src.core.redis_client.redis_client", MagicMock(
            make_key=lambda *p: ":".join(p), hgetall=_boom))
        assembler = AttachmentAssembler()
        ws = tmp_path / "ws4"
        ws.mkdir()
        assert assembler._materialize_history_attachments(
            [{"file_id": "file_x", "name": "a.pdf"}], ws, identity, []) == ""

    def test_total_size_cap(self, monkeypatch, identity, tmp_path):
        assembler = AttachmentAssembler()
        assembler._HISTORY_MAX_TOTAL_BYTES = 30
        mapping = {}
        for i in range(3):
            fid = f"file_cap{i:06d}"
            src = tmp_path / f"{fid}.pdf"
            src.write_bytes(b"x" * 20)
            mapping[fid] = {"file_id": fid, "name": f"{fid}.pdf", "path": str(src), "size": 20}
        self._patch_resolve(monkeypatch, mapping)
        ws = tmp_path / "ws5"
        ws.mkdir()
        context = assembler._materialize_history_attachments(
            [{"file_id": fid, "name": f"{fid}.pdf"} for fid in mapping], ws, identity, [])
        # 20B 装 1 个后 40B > 30B 上限，停止；第 2 个 20+20=40 也超限
        assert len(list((ws / "history_files").iterdir())) == 1


# ============== HistoryRepository.read_user_attachments ==============

class TestReadUserAttachments:
    def _repo(self, session_kind="channel"):
        return HistoryRepository(Identity("tenant-1", "user-1", "session-1", "web_c", session_kind))

    def _patch_db(self, monkeypatch, rows):
        captured = {}

        class _Cursor:
            def execute(self, query, parameters):
                captured["query"], captured["parameters"] = query, parameters

            def fetchall(self):
                return rows

        @contextmanager
        def _conn():
            yield MagicMock(cursor=lambda: _Cursor())

        monkeypatch.setattr(
            "src.services.agent_runner.runtime.history_repository.get_db_connection", _conn)
        return captured

    def test_parses_json_attachments(self, monkeypatch):
        rows = [{"attachments": json.dumps([
            {"file_id": "file_a1b2c3d4e5f6", "name": "doc.pdf", "mime_type": "application/pdf", "size": 12}])},
            {"attachments": None}]
        captured = self._patch_db(monkeypatch, rows)
        result = self._repo().read_user_attachments("session-1", 100)
        assert len(result) == 1
        assert result[0]["file_id"] == "file_a1b2c3d4e5f6"
        assert result[0]["name"] == "doc.pdf"
        assert "role = 'user'" in captured["query"]

    def test_db_failure_fail_open(self, monkeypatch):
        @contextmanager
        def _conn():
            raise RuntimeError("db down")
            yield  # pragma: no cover

        monkeypatch.setattr(
            "src.services.agent_runner.runtime.history_repository.get_db_connection", _conn)
        assert self._repo().read_user_attachments("session-1") == []


# ============== public_runner cancel_reason 白名单透出 ==============

def _minimal_row(status, cancel_reason=None):
    row = {
        "runner_id": "tr_runner_x", "session_kind": "channel", "session_id": "session-1",
        "source": "web_c", "status": status, "settlement_status": None,
        "client_request_id": "req-1", "profile_id": "main", "queue_order": 0,
        "revision": 1, "cancel_requested": False, "view_revision": 0,
        "control_revision": 0, "accepted_at": None, "updated_at": None,
        "finished_at": None, "public_snapshot": {}, "result": {},
    }
    if cancel_reason:
        row["checkpoint"] = {"cancel_reason": cancel_reason}
    return row


class TestPublicRunnerCancelReason:
    def test_superseded_exposed(self):
        value = public_runner(_minimal_row("cancelled", "superseded_by_new_message"))
        assert value["cancel_reason"] == "superseded_by_new_message"

    def test_user_stop_not_exposed(self):
        assert "cancel_reason" not in public_runner(_minimal_row("cancelled"))

    def test_other_reason_not_exposed(self):
        value = public_runner(_minimal_row("cancelled", "admin_kill"))
        assert "cancel_reason" not in value

    def test_non_terminal_not_exposed(self):
        value = public_runner(_minimal_row("running", "superseded_by_new_message"))
        assert "cancel_reason" not in value

    def test_completed_not_exposed(self):
        value = public_runner(_minimal_row("completed", "superseded_by_new_message"))
        assert "cancel_reason" not in value
