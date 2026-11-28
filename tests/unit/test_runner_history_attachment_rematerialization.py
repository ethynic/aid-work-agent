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

    def test_prepare_history_only_injects_manifest_without_workspace(self, monkeypatch, identity, tmp_path):
        """清单+按需检出：仅历史文件时不预建工作区、不复制文件，仅注入轻量清单（含 user+assistant）。"""
        assembler = AttachmentAssembler()
        enhanced, injection, workspace = assembler.prepare(
            "刚才那个文件继续处理", [], identity, None,
            history_attachments=[
                {"file_id": "file_hist0001", "name": "doc.pdf", "origin": "user_upload", "size": 1024},
                {"file_id": "file_1fba1c4a843e", "name": "诊断评估报告.pdf", "origin": "assistant_generated", "size": 524288},
            ])
        assert workspace is None
        assert "本会话已有文件清单" in enhanced
        assert "checkout_session_file" in enhanced
        assert "file_hist0001" in enhanced
        assert "file_1fba1c4a843e" in enhanced
        assert "助手生成" in enhanced
        assert "用户上传" in enhanced

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
        rows = [{"role": "user", "attachments": json.dumps([
            {"file_id": "file_a1b2c3d4e5f6", "name": "doc.pdf", "mime_type": "application/pdf", "size": 12}])},
            {"role": "user", "attachments": None}]
        captured = self._patch_db(monkeypatch, rows)
        result = self._repo().read_user_attachments("session-1", 100)
        assert len(result) == 1
        assert result[0]["file_id"] == "file_a1b2c3d4e5f6"
        assert result[0]["name"] == "doc.pdf"
        assert ["user"] in captured["parameters"]

    def test_read_session_files_includes_assistant_deliverables(self, monkeypatch):
        rows = [
            {"role": "assistant", "attachments": json.dumps([
                {"type": "file", "file_id": "file_1fba1c4a843e", "name": "诊断评估报告.pdf",
                 "mime_type": "application/pdf", "size": 524288}
            ]), "created_at": "2026-11-26 10:00:00"},
            {"role": "user", "attachments": json.dumps([
                {"type": "file", "file_id": "file_u11122233344", "name": "企业资料.xlsx",
                 "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "size": 4096}
            ]), "created_at": "2026-11-26 09:50:00"},
        ]
        captured = self._patch_db(monkeypatch, rows)
        result = self._repo("channel").read_session_files("session-1")
        # 按时间正序返回（先旧后新）
        assert [item["file_id"] for item in result] == ["file_u11122233344", "file_1fba1c4a843e"]
        assert result[0]["origin"] == "user_upload"
        assert result[1]["origin"] == "assistant_generated"
        assert ["user", "assistant"] in captured["parameters"]

    def test_read_session_files_im_channel_reads_metadata_deliverables(self, monkeypatch):
        """B3 回归：企微/企微客服/飞书/钉钉的 AI 交付文件落在 channel_messages.metadata.downloadableFiles。"""
        rows = [
            {"role": "assistant", "attachments": None, "metadata": {
                "downloadableFiles": [
                    {"file_id": "file_impdf000001", "file_name": "企微交付.pdf",
                     "mime_type": "application/pdf", "file_size": 8192}
                ]
            }, "created_at": "2026-11-26 10:10:00"},
        ]
        captured = self._patch_db(monkeypatch, rows)
        result = self._repo("channel").read_session_files("session-1")
        assert "m.metadata" in captured["query"]
        assert len(result) == 1
        assert result[0]["file_id"] == "file_impdf000001"
        assert result[0]["origin"] == "assistant_generated"

    def test_read_session_files_web_reads_metadata_not_attachments_col(self, monkeypatch):
        rows = [
            {"role": "assistant", "metadata": {
                "downloadableFiles": [
                    {"file_id": "file_webpdf000001", "file_name": "方案.pdf",
                     "mime_type": "application/pdf", "file_size": 2048}
                ]
            }, "created_at": "2026-11-26 10:05:00"},
            {"role": "user", "metadata": {
                "attachments": [
                    {"file_id": "file_webusr000002", "name": "需求.docx", "size": 1024}
                ]
            }, "created_at": "2026-11-26 10:00:00"},
        ]
        captured = self._patch_db(monkeypatch, rows)
        result = self._repo("web").read_session_files("session-1")
        assert "m.attachments" not in captured["query"]
        assert "m.metadata" in captured["query"]
        assert [item["file_id"] for item in result] == ["file_webusr000002", "file_webpdf000001"]
        assert result[1]["name"] == "方案.pdf"
        assert result[1]["size"] == 2048

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

    def test_superseded_by_other_client_exposed(self):
        value = public_runner(_minimal_row("cancelled", "superseded_by_other_client"))
        assert value["cancel_reason"] == "superseded_by_other_client"

    def test_client_id_exposed_when_present(self):
        row = _minimal_row("running")
        row["input"] = {"request_data": {"client_id": "wc_cli_abc"}}
        value = public_runner(row)
        assert value["client_id"] == "wc_cli_abc"
        assert "client_id" not in public_runner(_minimal_row("running"))

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
