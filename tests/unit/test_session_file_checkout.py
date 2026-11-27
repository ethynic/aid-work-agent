"""会话文件清单 + 按需检出（checkout_session_file）及 file_id 租户域解析单测。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.agent_engine.contracts import Identity
from src.core.storage import normalize_tenant_id
from src.services.files.session_file_checkout import (
    checkout_session_file,
    resolve_tenant_file_record,
)
from src.tools.control_set import ControlToolDependencies, ToolControlSet
from src.tools.file.read_tool import ReadTool

pytestmark = pytest.mark.unit


def _make_tenant_file(tmp_path: Path, tenant_id: str, rel: str, content: bytes) -> Path:
    tid = normalize_tenant_id(tenant_id)
    path = tmp_path / "tenants" / tid / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _make_control_set():
    skill_registry = MagicMock()
    skill_registry.get_skill_tool_definition.return_value = {
        "type": "function",
        "function": {"name": "use_skill", "description": "use skill", "parameters": {"type": "object"}},
    }
    deps = ControlToolDependencies(
        plan_manager=MagicMock(),
        skill_registry=skill_registry,
        skill_executor=MagicMock(),
        tool_registry=MagicMock(),
    )
    return ToolControlSet(deps)


class TestSessionFileCheckout:
    def test_resolve_from_db_file_assets(self, monkeypatch, tmp_path):
        pdf = _make_tenant_file(tmp_path, "tenant_a", "conversation/file_1fba1c4a843e.pdf", b"%PDF-1.4")
        monkeypatch.setattr(
            "src.services.files.asset_store.get_file_asset",
            lambda fid: {
                "file_id": fid,
                "tenant_id": "tenant_a",
                "path": str(pdf),
                "name": "诊断评估报告.pdf",
                "mime_type": "application/pdf",
                "size": 8,
            },
        )
        rec = resolve_tenant_file_record("file_1fba1c4a843e", "tenant_a", storage_root=str(tmp_path))
        assert rec is not None
        assert rec["path"] == str(pdf.resolve())
        assert rec["name"] == "诊断评估报告.pdf"

    def test_rejects_foreign_tenant_file_from_db(self, monkeypatch, tmp_path):
        pdf = _make_tenant_file(tmp_path, "tenant_b", "conversation/file_1fba1c4a843e.pdf", b"%PDF-1.4")
        monkeypatch.setattr(
            "src.services.files.asset_store.get_file_asset",
            lambda fid: {
                "file_id": fid,
                "tenant_id": "tenant_b",
                "path": str(pdf),
                "name": "secret.pdf",
                "size": 8,
            },
        )
        monkeypatch.setattr(
            "src.core.redis_client.redis_client",
            MagicMock(make_key=lambda *p: ":".join(p), hgetall=lambda k: {}),
        )
        assert resolve_tenant_file_record("file_1fba1c4a843e", "tenant_a", storage_root=str(tmp_path)) is None

    def test_checkout_requires_session_ownership(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "src.services.files.session_file_checkout.verify_session_file_ownership",
            lambda fid, **kw: None,
        )
        res = checkout_session_file(
            file_id="file_1fba1c4a843e",
            tenant_id="tenant_a",
            session_id="sess_1",
            session_kind="channel",
            workspace_dir=str(tmp_path / "ws"),
        )
        assert res["success"] is False
        assert "不属于当前会话" in res["error"]

    def test_checkout_materializes_hardlink_and_handles_collisions(self, monkeypatch, tmp_path):
        pdf1 = _make_tenant_file(tmp_path, "tenant_a", "conversation/file_1fba1c4a843e.pdf", b"%PDF-1.4-first")
        pdf2 = _make_tenant_file(tmp_path, "tenant_a", "conversation/file_222233334444.pdf", b"%PDF-1.4-second")
        records = {
            "file_1fba1c4a843e": {"file_id": "file_1fba1c4a843e", "name": "报告.pdf", "path": str(pdf1), "mime_type": "application/pdf", "size": 14},
            "file_222233334444": {"file_id": "file_222233334444", "name": "报告.pdf", "path": str(pdf2), "mime_type": "application/pdf", "size": 15},
        }
        monkeypatch.setattr(
            "src.services.files.session_file_checkout.verify_session_file_ownership",
            lambda fid, **kw: records.get(fid),
        )
        monkeypatch.setattr(
            "src.services.files.session_file_checkout.resolve_tenant_file_record",
            lambda fid, tid, **kw: records.get(fid),
        )
        ws = tmp_path / "ws"
        r1 = checkout_session_file(
            file_id="file_1fba1c4a843e", tenant_id="tenant_a",
            session_id="sess_1", session_kind="channel", workspace_dir=str(ws),
        )
        r2 = checkout_session_file(
            file_id="file_222233334444", tenant_id="tenant_a",
            session_id="sess_1", session_kind="channel", workspace_dir=str(ws),
        )
        assert r1["success"] is True and r2["success"] is True
        p1, p2 = Path(r1["file_path"]), Path(r2["file_path"])
        assert p1.exists() and p2.exists() and p1 != p2
        assert p1.suffix == ".pdf" and p2.suffix == ".pdf"
        assert p1.read_bytes() == b"%PDF-1.4-first"
        assert p2.read_bytes() == b"%PDF-1.4-second"
        # B2 回归：隔离拷贝不得共享 inode，原地修改检出文件绝不污染持久存储原件
        p1.write_bytes(b"MODIFIED-IN-WORKSPACE")
        assert pdf1.read_bytes() == b"%PDF-1.4-first"
        # 同 file_id 重复检出幂等返回相同路径
        r1_again = checkout_session_file(
            file_id="file_1fba1c4a843e", tenant_id="tenant_a",
            session_id="sess_1", session_kind="channel", workspace_dir=str(ws),
        )
        assert r1_again["file_path"] == r1["file_path"]


class TestCheckoutControlToolAndReadTool:
    def test_control_set_exposes_checkout_session_file(self):
        cs = _make_control_set()
        names = {
            d.get("name") or (d.get("function") or {}).get("name")
            for d in cs.definitions(available_subagents=None)
        }
        assert "checkout_session_file" in names
        assert "checkout_session_file" in cs.usage_guides()

    @pytest.mark.asyncio
    async def test_control_adapter_lazily_creates_workspace_for_checkout(self, monkeypatch, tmp_path):
        from src.core.agent_engine.contracts import DispatchResult
        from src.services.agent_runner.runtime.controls import ControlToolAdapter
        from src.tools.context import ToolExecutionContext

        ws_created = tmp_path / "lazy_ws"
        ws_created.mkdir()
        attachments = MagicMock()
        attachments._create_session_workspace.return_value = ws_created

        captured_kwargs = {}

        def fake_checkout(file_id, **kwargs):
            captured_kwargs["file_id"] = file_id
            captured_kwargs.update(kwargs)
            return {
                "success": True,
                "file_id": file_id,
                "file_name": "报告.pdf",
                "file_path": str(ws_created / "session_files" / "报告.pdf"),
                "size": 100,
                "mime_type": "application/pdf",
            }

        monkeypatch.setattr("src.services.files.session_file_checkout.checkout_session_file", fake_checkout)

        adapter = ControlToolAdapter(
            controls=_make_control_set(),
            skills=MagicMock(),
            registry=MagicMock(),
            plan_manager=MagicMock(),
            clarification_store=MagicMock(),
            context=ToolExecutionContext(tenant_id="tenant_a", user_id="user_1", session_id="sess_1"),
            user_input="查一下刚才的报告",
            attachments=attachments,
        )
        state = SimpleNamespace(
            identity=Identity("tenant_a", "user_1", "sess_1", "web_c", "channel"),
            resources={},
        )
        call = SimpleNamespace(id="call_1", name="checkout_session_file", arguments={"file_id": "file_1fba1c4a843e"})
        yielded = [item async for item in adapter.execute(state, call)]
        dispatch = next(item for item in yielded if isinstance(item, DispatchResult))
        assert dispatch.success is True
        assert dispatch.content["file_id"] == "file_1fba1c4a843e"
        assert state.resources["workspace"] == str(ws_created)
        assert captured_kwargs["workspace_dir"] == str(ws_created)
        attachments._create_session_workspace.assert_called_once()

    def test_read_tool_resolves_file_id_and_blocks_foreign_tenant(self, monkeypatch, tmp_path):
        pdf = _make_tenant_file(tmp_path, "tenant_a", "conversation/file_1fba1c4a843e.pdf", b"%PDF-1.4")
        foreign = _make_tenant_file(tmp_path, "tenant_b", "conversation/file_deadbeef1234.pdf", b"secret")
        monkeypatch.setattr("src.core.storage.configured_storage_root", lambda: str(tmp_path))
        monkeypatch.setattr("src.core.storage.resolve_uploaded_file_path", lambda fid: str(pdf) if fid == "file_1fba1c4a843e" else None)

        tool = ReadTool()
        from src.tools.context import ToolExecutionContext, tool_execution_scope

        with tool_execution_scope(ToolExecutionContext(tenant_id="tenant_a", user_id="u1", session_id="s1")):
            resolved = tool._resolve_path("file_1fba1c4a843e")
            assert resolved == pdf.resolve()
            with pytest.raises(PermissionError, match="其他租户"):
                tool._resolve_path(str(foreign))

    def test_read_tool_allows_authorized_shared_knowledge_file(self, monkeypatch, tmp_path):
        """B1 回归：已授权的跨租户共享知识库文件必须允许 read 读取。"""
        shared_doc = _make_tenant_file(tmp_path, "tenant_owner", "knowledge/policy/guide.md", b"# Policy")
        monkeypatch.setattr(
            "src.knowledge.retriever.tenant_range.load_shared_ranges",
            lambda tid, sid: [("tenant_owner", "policy")] if (tid == "tenant_b" and sid == "sales") else [],
        )
        monkeypatch.setattr("src.core.storage.configured_storage_root", lambda: str(tmp_path))

        tool = ReadTool()
        from src.tools.context import ToolExecutionContext, tool_execution_scope

        with tool_execution_scope(
            ToolExecutionContext(tenant_id="tenant_b", user_id="u1", session_id="s1", subagent_id="sales")
        ):
            # 在 tmp_path 下，允许读取已授权共享知识库文件（不抛 PermissionError）
            resolved = tool._resolve_path(str(shared_doc))
            assert resolved == shared_doc.resolve()
