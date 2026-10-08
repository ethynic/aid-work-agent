"""图片改可信产物引用 + 持久化字节上限的真实 worker/API 验收。

覆盖：
1. resume 控制回复带图：provider wire 收到 data URL，而持久 checkpoint 只存
   runner_image 引用（登记 resources['image_artifacts']，落 owner workspace），
   终态后引用产物按 owner 命名空间清理。
2. 单请求字节超限 → 提交入口 413；控制输入字节超限 → 控制入口 413。
3. checkpoint 字节超限 → CheckpointFailure 走既有存储失败语义（interrupted 保留
   最后已提交 checkpoint），绝不静默落库或继续模型 IO。
4. 提交公开快照剥离附件内联 content，只存展示字段。
"""
import base64
import hashlib
import json
import threading
import uuid
from pathlib import Path

import pytest

from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_api import body, require_status, start_api_pair
from .test_worker import workers, prices, api_pair, accept, decoded, runner, terminal

pytestmark = pytest.mark.integration


# 最小 1x1 PNG（与单测/委托多模态测试相同的 fixture 形状）
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode("ascii")
_PNG_SHA = hashlib.sha256(_PNG_BYTES).hexdigest()
_PNG_DATA_URL = f"data:image/png;base64,{_PNG_B64}"


def _cleanup_controls(service_database, runner_id):
    # teardown 显式先删 controls，FK/清理顺序不依赖库端级联。
    service_database.rows("DELETE FROM agent_runner_controls WHERE runner_id=%s", (runner_id,))


def _pause_mid_read(workers, actors, service_database, fixture_name):
    """受理 → 读工具 IO 中暂停 → runner parked，返回 (runner_id, initial_marker)。"""
    fixture = workers.root / fixture_name
    fixture.write_text("fixture-image-original-bytes")
    gate = threading.Event()
    initial = tool_reply("read", {"file_path": str(fixture)}, call_id="image-old-read")
    initial.release = gate
    marker = workers.provider.register(initial)
    accepted = accept(workers.api, actors["a"], marker)
    process, _ = workers.start()
    try:
        assert initial.arrived.wait(timeout=15)
        response = workers.api.call("POST", f"/v1/runners/{accepted['runner_id']}/controls",
                                    actor=actors["a"],
                                    json={"client_request_id": uuid.uuid4().hex, "action": "pause"})
        require_status(response, 202)
        gate.set()
        workers.assert_clean_exit(process)
    finally:
        gate.set()
    parked = runner(service_database, accepted["runner_id"])
    assert parked["status"] == "paused"
    return accepted["runner_id"], marker


def test_control_reply_image_reaches_wire_while_checkpoint_keeps_reference(
        workers, actors, service_database):
    runner_id, _ = _pause_mid_read(workers, actors, service_database, "image-original-read.txt")
    try:
        file_id = f"fixture_image_{uuid.uuid4().hex}.png"
        upload = workers.storage / "tenants" / actors["a"].tenant_id / file_id
        upload.parent.mkdir(parents=True, exist_ok=True)
        upload.write_bytes(_PNG_BYTES)
        followup_gate = threading.Event()
        followup_marker = workers.provider.register(
            Reply(content="image-followup-final", release=followup_gate))
        attachment = {"file_id": file_id, "name": "photo.png", "type": "image",
                      "mime_type": "image/png"}
        response = workers.api.call("POST", f"/v1/runners/{runner_id}/controls",
                                    actor=actors["a"],
                                    json={"client_request_id": uuid.uuid4().hex, "action": "resume",
                                          "answer": followup_marker, "attachments": [attachment]})
        require_status(response, 202)
        resumed, _ = workers.start()
        try:
            assert wait_for(lambda: workers.provider.requests(followup_marker) or None, timeout=25)
            # provider wire：真实 Gateway 收到 data URL（ModelAdapter 按 owner 装配）
            request = workers.provider.requests(followup_marker)[0]
            image_parts = [part for message in request["messages"]
                           if isinstance(message.get("content"), list)
                           for part in message["content"]
                           if part.get("type") == "image_url"]
            assert [part["image_url"]["url"] for part in image_parts] == [_PNG_DATA_URL]
            # 持久 checkpoint：只存 runner_image 引用，无任何 data: 负载
            row = runner(service_database, runner_id)
            execution = decoded(row["checkpoint"])["execution"]
            serialized = json.dumps(execution)
            assert "data:" not in serialized
            assert "runner_image" in serialized and _PNG_SHA in serialized
            artifacts = execution["resources"]["image_artifacts"]
            assert len(artifacts) == 1 and artifacts[0]["sha256"] == _PNG_SHA
            artifact_path = Path(artifacts[0]["path"])
            assert artifact_path.read_bytes() == _PNG_BYTES
            assert artifact_path.parent.parent == (workers.root / "workspaces").resolve()
            followup_gate.set()
            workers.assert_clean_exit(resumed)
        finally:
            followup_gate.set()
        finished = terminal(service_database, runner_id)
        assert finished["status"] == "completed"
        assert "data:" not in json.dumps(decoded(finished["checkpoint"]))
        # 终态清理按 owner 命名空间删除引用产物
        assert not artifact_path.exists()
        assert not workers.provider.errors
    finally:
        _cleanup_controls(service_database, runner_id)


def test_submit_snapshot_strips_inline_attachment_content(workers, actors, service_database):
    encoded = base64.b64encode(b"fixture-inline-attachment").decode()
    marker = workers.provider.register(Reply(content="snapshot-strip-final"))
    accepted = accept(workers.api, actors["a"], marker,
                      attachments=[{"name": "inline.txt", "content": encoded}])
    try:
        row = runner(service_database, accepted["runner_id"])
        snapshot = decoded(row["public_snapshot"])
        stored = snapshot["input"]["attachments"][0]
        assert stored["name"] == "inline.txt" and "content" not in stored
        # 私有 input 列保留内联 content 供 worker 装配，接口形状不变
        assert decoded(row["input"])["attachments"][0]["content"] == encoded
        child, _ = workers.start()
        workers.assert_clean_exit(child)
        assert terminal(service_database, accepted["runner_id"])["status"] == "completed"
    finally:
        _cleanup_controls(service_database, accepted["runner_id"])


def test_request_and_control_over_byte_limits_are_rejected_with_413(
        service_processes, actors, service_database):
    pair = start_api_pair(service_processes,
                          extra_environment={"AGENT_RUNNER_LIMITS_REQUEST_BYTES": "1024"})
    actor = actors["a"]
    accepted = require_status(pair.call("POST", "/v1/runners", actor=actor,
                                        json=body(actor)), 202)["runner"]
    try:
        assert accepted["status"] == "queued"
        oversized = require_status(pair.call("POST", "/v1/runners", actor=actor,
                                             json=body(actor, text="x" * 4096)), 413)
        assert oversized["error"] == "REQUEST_TOO_LARGE"
        oversized_control = require_status(
            pair.call("POST", f"/v1/runners/{accepted['runner_id']}/controls", actor=actor,
                      json={"client_request_id": uuid.uuid4().hex, "action": "resume",
                            "answer": "y" * 4096}), 413)
        assert oversized_control["error"] == "CONTROL_TOO_LARGE"
        # 正常体量的控制不受字节闸影响
        require_status(pair.call("POST", f"/v1/runners/{accepted['runner_id']}/controls",
                                 actor=actor,
                                 json={"client_request_id": uuid.uuid4().hex, "action": "pause"}), 202)
        # 超限请求/控制未落任何行
        assert service_database.rows(
            "SELECT count(*) AS n FROM agent_runners WHERE session_id=%s",
            (actor.session_id,))[0]["n"] == 1
        assert service_database.rows(
            "SELECT count(*) AS n FROM agent_runner_controls WHERE runner_id=%s",
            (accepted["runner_id"],))[0]["n"] == 1
    finally:
        _cleanup_controls(service_database, accepted["runner_id"])


def test_checkpoint_over_limit_interrupts_with_last_committed_state(
        workers, actors, service_database):
    # 初始 checkpoint（仅 execution_context）低于阈值；工具结果 200KB 入
    # state.messages 后，下一次边界 save 超限 → CheckpointFailure 走既有
    # 存储失败语义：保留最后已提交 checkpoint 并 interrupted，绝不静默落库。
    fixture = workers.root / "checkpoint-cap-payload.txt"
    fixture.write_text("u" * 200_000)
    marker = workers.provider.register(
        tool_reply("read", {"file_path": str(fixture)}, call_id="checkpoint-cap-read"),
        Reply(content="never-reached-after-cap"))
    accepted = accept(workers.api, actors["a"], marker)
    try:
        workers.environment["AGENT_RUNNER_LIMITS_CHECKPOINT_BYTES"] = "65536"
        child, _ = workers.start()
        workers.assert_clean_exit(child)
        stopped = wait_for(lambda: (row if (row := runner(service_database, accepted["runner_id"]))
                                    ["status"] == "interrupted" else None), timeout=20)
        assert stopped["status"] == "interrupted" and stopped["finished_at"] is None
        # 超限 execution 状态绝不落库：库内 checkpoint 恒低于阈值
        assert len(json.dumps(decoded(stopped["checkpoint"]), ensure_ascii=False)) < 65536
        # 首次模型调用可能已发生，但超限后零进一步模型 IO，也非终态完成
        calls = workers.provider.requests(marker)
        assert len(calls) <= 1 and not workers.provider.errors
    finally:
        workers.environment.pop("AGENT_RUNNER_LIMITS_CHECKPOINT_BYTES", None)
        _cleanup_controls(service_database, accepted["runner_id"])
