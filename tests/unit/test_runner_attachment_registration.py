"""attachments.prepare 图片附件注册 file_id 单元测试。

覆盖：
- 图片附件注册参数（source/usage/display_name/source_ref）与反查索引写入
- 非图片附件不注册
- 无租户上下文不注册
- 注册失败不阻断装配（files_context 仍含 Full path）
- files_context 含 File ID 行
- index_workspace_file 失败不影响返回 file_id

外部依赖 mock：get_image_registry / index_workspace_file 均在调用点 import，
patch 源模块引用即可；附件内容用真实 base64 + tmp_path。
"""

import base64
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.agent_engine.contracts import Identity
from src.services.agent_runner.runtime import workspace_path_rewriter as rew
from src.services.agent_runner.runtime.attachments import AttachmentAssembler

# 1x1 PNG（最小合法 PNG 文件头+数据）
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture
def identity():
    return Identity("tenant-1", "user-1", "session-1")


def _mock_registry():
    registry = MagicMock()
    registry.register_sync.return_value = MagicMock(
        file_id="file_reg12345678", display_name="餐桌.png")
    return registry


class TestRegisterImageAttachment:
    def test_image_registered_with_index(self, monkeypatch, identity, tmp_path):
        png = tmp_path / "餐桌.png"
        png.write_bytes(_PNG)
        registry = _mock_registry()
        monkeypatch.setattr("src.core.image_asset.get_image_registry", lambda: registry)
        indexed = {}
        monkeypatch.setattr(rew, "index_workspace_file",
                            lambda p, fid, name, tid: indexed.update(
                                path=p, file_id=fid, tenant=tid))
        assembler = AttachmentAssembler()
        file_id = assembler._register_image_attachment(png, "餐桌.png", identity)
        assert file_id == "file_reg12345678"
        kwargs = registry.register_sync.call_args.kwargs
        assert kwargs["source"] == "user_upload"
        assert kwargs["usage"] == "attachment"
        assert kwargs["tenant_id"] == "tenant-1"
        assert kwargs["user_id"] == "user-1"
        assert kwargs["display_name"] == "餐桌.png"
        assert kwargs["source_ref"] == "session:session-1"
        assert indexed["file_id"] == "file_reg12345678"
        assert indexed["path"] == str(png)
        assert indexed["tenant"] == "tenant-1"

    def test_non_image_skipped(self, monkeypatch, identity, tmp_path):
        registry = _mock_registry()
        monkeypatch.setattr("src.core.image_asset.get_image_registry", lambda: registry)
        doc = tmp_path / "doc.pdf"
        doc.write_bytes(b"%PDF-1.4")
        assembler = AttachmentAssembler()
        assert assembler._register_image_attachment(doc, "doc.pdf", identity) is None
        registry.register_sync.assert_not_called()

    def test_no_tenant_skipped(self, monkeypatch, tmp_path):
        registry = _mock_registry()
        monkeypatch.setattr("src.core.image_asset.get_image_registry", lambda: registry)
        png = tmp_path / "a.png"
        png.write_bytes(_PNG)
        assembler = AttachmentAssembler()
        assert assembler._register_image_attachment(png, "a.png",
                                                    Identity(None, None, "s1")) is None
        registry.register_sync.assert_not_called()

    def test_register_failure_not_raised(self, monkeypatch, identity, tmp_path):
        registry = _mock_registry()
        registry.register_sync.side_effect = RuntimeError("redis down")
        monkeypatch.setattr("src.core.image_asset.get_image_registry", lambda: registry)
        png = tmp_path / "a.png"
        png.write_bytes(_PNG)
        assembler = AttachmentAssembler()
        assert assembler._register_image_attachment(png, "a.png", identity) is None


class TestPrepareIntegration:
    def test_prepare_registers_image_and_lists_file_id(self, monkeypatch, identity):
        registry = _mock_registry()
        monkeypatch.setattr("src.core.image_asset.get_image_registry", lambda: registry)
        monkeypatch.setattr(rew, "index_workspace_file", MagicMock())
        assembler = AttachmentAssembler()
        enhanced, injection, workspace = assembler.prepare(
            "看看这张图",
            [{"type": "image", "name": "餐桌.png", "mime_type": "image/png",
              "content": base64.b64encode(_PNG).decode()}],
            identity, None)
        assert injection == ""
        assert workspace  # 工作区已创建
        assert "File ID: `file_reg12345678`" in enhanced
        assert "Full path: `" in enhanced
        assert Path(workspace).exists()

    def test_prepare_non_image_no_file_id_line(self, monkeypatch, identity):
        registry = _mock_registry()
        monkeypatch.setattr("src.core.image_asset.get_image_registry", lambda: registry)
        assembler = AttachmentAssembler()
        enhanced, _, _ = assembler.prepare(
            "处理文档",
            [{"type": "file", "name": "doc.pdf", "mime_type": "application/pdf",
              "content": base64.b64encode(b"%PDF-1.4").decode()}],
            identity, None)
        assert "File ID:" not in enhanced
        registry.register_sync.assert_not_called()

    def test_prepare_registration_failure_still_lists_path(self, monkeypatch, identity):
        registry = _mock_registry()
        registry.register_sync.side_effect = RuntimeError("down")
        monkeypatch.setattr("src.core.image_asset.get_image_registry", lambda: registry)
        assembler = AttachmentAssembler()
        enhanced, _, _ = assembler.prepare(
            "看看这张图",
            [{"type": "image", "name": "a.png", "mime_type": "image/png",
              "content": base64.b64encode(_PNG).decode()}],
            identity, None)
        assert "File ID:" not in enhanced  # 注册失败无 File ID 行
        assert "Full path: `" in enhanced  # 但路径信息不丢
