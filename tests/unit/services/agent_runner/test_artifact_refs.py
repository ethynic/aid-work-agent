"""runner_image 引用部件：构造、owner 校验装配、越权与篡改拒绝。"""

import base64
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity
from src.services.agent_runner.resource_paths import (
    cleanup_workspaces, workspace_directory)
from src.services.agent_runner.runtime.attachments import AttachmentAssembler
from src.services.agent_runner.runtime.executor import ModelAdapter

pytestmark = pytest.mark.unit


# 最小 1x1 PNG（与 tests/unit/tools/test_delegate_tool_multimodal.py 相同形状）
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
_PNG_SHA = hashlib.sha256(_PNG_BYTES).hexdigest()

_IDENTITY = Identity("tenant-1", "user-1", "session-1", "chat", "web")


class RecordingGateway:
    provider_name = "fixture"

    def __init__(self):
        self.calls = []

    async def chat_with_tools(self, *, system_prompt, messages, tools, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        return {"content": "fixture-reply"}

    def get_model_name(self):
        return "fixture-model"

    def get_provider_name(self):
        return "fixture"


def _checkpoint_restore(data):
    """经 checkpoint/restore 与真实恢复一致的深拷贝路径构造 state。"""
    return ExecutionState.restore(json.loads(json.dumps(
        ExecutionState(**data).checkpoint(), ensure_ascii=False)))


class TestBuildImageReferenceContent:
    def test_stores_image_into_owner_workspace_and_returns_reference_parts(self, tmp_path):
        owner = tmp_path / "workspaces" / "ns"
        source = tmp_path / "upload.png"
        source.write_bytes(_PNG_BYTES)
        assembler = AttachmentAssembler(owner)

        parts, artifacts = assembler.build_image_reference_content("看图", [str(source)])

        assert parts is not None and artifacts
        assert parts[0] == {"type": "text", "text": "看图"}
        reference = parts[1]
        assert reference == {"type": "runner_image", "name": "upload.png",
                             "mime_type": "image/png", "size_bytes": len(_PNG_BYTES),
                             "sha256": _PNG_SHA}
        stored = owner / f"image_{_PNG_SHA}.png"
        assert stored.read_bytes() == _PNG_BYTES
        assert artifacts == [{"name": "upload.png", "mime_type": "image/png",
                              "size_bytes": len(_PNG_BYTES), "sha256": _PNG_SHA,
                              "path": str(stored)}]
        # 引用部件序列化后不含任何 data: 负载
        assert "data:" not in json.dumps(parts)

    def test_without_owner_workspace_returns_nothing(self, tmp_path):
        source = tmp_path / "upload.png"
        source.write_bytes(_PNG_BYTES)
        assembler = AttachmentAssembler(None)
        assert assembler.build_image_reference_content("看图", [str(source)]) == (None, [])

    def test_same_content_is_idempotent_and_deduplicated_by_hash(self, tmp_path):
        owner = tmp_path / "ns"
        first, second = tmp_path / "a.png", tmp_path / "b.png"
        first.write_bytes(_PNG_BYTES)
        second.write_bytes(_PNG_BYTES)
        assembler = AttachmentAssembler(owner)

        parts_one, _ = assembler.build_image_reference_content("t", [str(first)])
        parts_two, artifacts_two = assembler.build_image_reference_content("t", [str(second)])

        assert parts_one[1]["sha256"] == parts_two[1]["sha256"] == _PNG_SHA
        assert len(list(owner.glob("image_*.png"))) == 1
        assert len(artifacts_two) == 1

    def test_bounds_skip_oversized_extra_and_unsupported_images(self, tmp_path):
        owner = tmp_path / "ns"
        big = tmp_path / "big.png"
        big.write_bytes(_PNG_BYTES + b"\x00" * (5 * 1024 * 1024 + 1))
        unsupported = tmp_path / "pic.bmp"
        unsupported.write_bytes(_PNG_BYTES)
        assembler = AttachmentAssembler(owner)

        parts, artifacts = assembler.build_image_reference_content(
            "t", [str(big), str(unsupported)])
        assert parts is None and artifacts == []

        paths = []
        for index in range(5):
            path = tmp_path / f"img{index}.png"
            path.write_bytes(_PNG_BYTES + index.to_bytes(1, "big"))
            paths.append(str(path))
        parts, artifacts = assembler.build_image_reference_content("t", paths)
        assert len(artifacts) == 3 and sum(
            1 for part in parts if part["type"] == "runner_image") == 3


class TestModelAdapterWireAssembly:
    def _state(self, resource_directory, execution_id, name_suffix=""):
        owner = workspace_directory(resource_directory, _IDENTITY, execution_id)
        owner.mkdir(parents=True, exist_ok=True)
        stored = owner / f"image_{_PNG_SHA}.png"
        stored.write_bytes(_PNG_BYTES)
        parts = [{"type": "text", "text": "看图"},
                 {"type": "runner_image", "name": f"upload{name_suffix}.png",
                  "mime_type": "image/png", "size_bytes": len(_PNG_BYTES),
                  "sha256": _PNG_SHA}]
        artifacts = [{"name": f"upload{name_suffix}.png", "mime_type": "image/png",
                      "size_bytes": len(_PNG_BYTES), "sha256": _PNG_SHA,
                      "path": str(stored)}]
        data = {"identity": _IDENTITY, "execution_id": execution_id,
                "role": AgentMode.MASTER, "system_prompt": "s",
                "messages": [{"role": "user", "content": parts}],
                "resources": {"image_artifacts": artifacts}}
        return _checkpoint_restore(data), stored

    @pytest.mark.asyncio
    async def test_wire_receives_data_url_and_state_keeps_reference(self, tmp_path):
        execution_id = "runner_wire_1"
        state, stored = self._state(tmp_path, execution_id)
        gateway = RecordingGateway()
        adapter = ModelAdapter(gateway, resource_directory=tmp_path)

        response = await adapter.complete(state, "call-1", [])

        assert response["content"] == "fixture-reply"
        wire = gateway.calls[0]
        encoded = base64.b64encode(_PNG_BYTES).decode("ascii")
        assert wire[0]["content"][1] == {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{encoded}"}}
        # 引用化核心约束：state.messages 不回写 data URL
        serialized = json.dumps(state.messages, ensure_ascii=False)
        assert "data:" not in serialized and "runner_image" in serialized
        assert state.messages[0]["content"][1]["type"] == "runner_image"
        assert stored.exists()

    @pytest.mark.asyncio
    async def test_legacy_data_url_messages_pass_through_unchanged(self, tmp_path):
        encoded = base64.b64encode(_PNG_BYTES).decode("ascii")
        legacy = [{"role": "user", "content": [
            {"type": "text", "text": "旧 checkpoint 恢复"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}]}]
        data = {"identity": _IDENTITY, "execution_id": "runner_legacy_1",
                "role": AgentMode.MASTER, "system_prompt": "s",
                "messages": legacy, "resources": {}}
        state = _checkpoint_restore(data)
        gateway = RecordingGateway()
        adapter = ModelAdapter(gateway, resource_directory=tmp_path)

        await adapter.complete(state, "call-1", [])

        # 原样透传，不二次转换也不做 owner 校验
        assert gateway.calls[0][0] == legacy[0]
        assert state.messages[0] == legacy[0]

    @pytest.mark.asyncio
    async def test_unregistered_reference_is_rejected(self, tmp_path):
        state, _ = self._state(tmp_path, "runner_tamper_1")
        state.resources.pop("image_artifacts")
        adapter = ModelAdapter(RecordingGateway(), resource_directory=tmp_path)
        with pytest.raises(ValueError, match="IMAGE_ARTIFACT_NOT_REGISTERED"):
            await adapter.complete(state, "call-1", [])

    @pytest.mark.asyncio
    async def test_reference_outside_owner_namespace_is_rejected(self, tmp_path):
        state, _ = self._state(tmp_path, "runner_scope_1")
        foreign_dir = tmp_path / "elsewhere"
        foreign_dir.mkdir()
        foreign = foreign_dir / f"image_{_PNG_SHA}.png"
        foreign.write_bytes(_PNG_BYTES)
        state.resources["image_artifacts"][0]["path"] = str(foreign)
        adapter = ModelAdapter(RecordingGateway(), resource_directory=tmp_path)
        with pytest.raises(ValueError, match="IMAGE_ARTIFACT_SCOPE_INVALID"):
            await adapter.complete(state, "call-1", [])

    @pytest.mark.asyncio
    async def test_modified_artifact_file_is_rejected(self, tmp_path):
        state, stored = self._state(tmp_path, "runner_changed_1")
        stored.write_bytes(_PNG_BYTES + b"tampered")
        adapter = ModelAdapter(RecordingGateway(), resource_directory=tmp_path)
        with pytest.raises(ValueError, match="IMAGE_ARTIFACT_CONTENT_CHANGED"):
            await adapter.complete(state, "call-1", [])

    @pytest.mark.asyncio
    async def test_symlinked_artifact_is_rejected(self, tmp_path):
        state, stored = self._state(tmp_path, "runner_link_1")
        link = stored.with_name(stored.name + ".link")
        os.symlink(stored, link)
        state.resources["image_artifacts"][0]["path"] = str(link)
        adapter = ModelAdapter(RecordingGateway(), resource_directory=tmp_path)
        with pytest.raises(ValueError, match="IMAGE_ARTIFACT_SCOPE_INVALID"):
            await adapter.complete(state, "call-1", [])


class TestArtifactCleanup:
    def test_cleanup_removes_registered_artifacts_only_within_owner_namespace(self, tmp_path):
        execution_id = "runner_clean_1"
        owner = workspace_directory(tmp_path, _IDENTITY, execution_id)
        owner.mkdir(parents=True, exist_ok=True)
        inside = owner / f"image_{_PNG_SHA}.png"
        inside.write_bytes(_PNG_BYTES)
        foreign = tmp_path / "foreign_image.png"
        foreign.write_bytes(_PNG_BYTES)
        checkpoint = {"execution_id": execution_id,
                      "resources": {"image_artifacts": [
                          {"sha256": _PNG_SHA, "path": str(inside)},
                          {"sha256": _PNG_SHA, "path": str(foreign)}]}}

        cleanup_workspaces(tmp_path, _IDENTITY, checkpoint)

        assert not inside.exists()
        # 命名空间外文件不可证为执行产物，保留
        assert foreign.exists()
