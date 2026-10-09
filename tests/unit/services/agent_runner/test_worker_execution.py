"""真实 RunnerWorker.execute 的渠道执行用例（R4.2 固化）。

与 entries 测试直接调用 `RunnerWorker._agent_user` / `channel_verbose_config`
helper 不同，这里驱动**真实 `RunnerWorker.execute(row)`**，断言 Runtime 收到
正确用户与有效 verbose 配置、事件经 `iter_with_verbose_feedback` 到达
observer、执行走到 stage/finalize 收尾且不抛异常。

真实/假件边界：
- 真实：RunnerWorker.execute 主流程（授权、guard、UsageScope、TraceCollector
  装配、_agent_user 渠道分支、channel_verbose_config、事件包装与 observer
  投递、execution_result、DurableControl.stage 收尾）、RunnerAuthorizer
  .authorize_persisted / assert_credit（内存 connection）、User 模型构造。
- 假件：connection（内存 channel_sessions/users/tenants 行）、execution
  repository（最小 claim/save 桩，record 型 stage_finalization）、repository
  （get 返回内存行）、runtime factory 与 runtime 本体（记录型：run() 记录
  kwargs 并 yield 已注册 verbose + response 事件；observer 记录事件）、
  finalizer（记录调用）。Runtime 内部的引擎/子代理树未装配——单测不覆盖真实
  LLM 执行与子代理 drain。
- IO 边界 monkeypatch（均在测试内注明）：`RunnerWorker._persist_trace`（真实
  实现要读 chat_records/写 trace 库，单测无库）、`UserDB.get_by_id`（真实
  users 表读取，桩为内存行）、`settings.agent.verbose_feedback=None`（固定
  base 默认值，避免环境配置影响断言）。被测对象 `_agent_user` /
  `channel_verbose_config` 本身不 patch。
"""

import copy
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config.settings import AgentRunnerConfig
from src.core.agent_engine.contracts import (
    AgentMode,
    ExecutionState,
    Identity,
    Outcome,
)
from src.core.agent_events import make_verbose_event
from src.services.agent_runner.authorization import RunnerAuthorizer
from src.services.agent_runner.contracts import canonical_json
from src.services.agent_runner.worker import RunnerWorker

_CHANNEL_USERS = {"feishu": "ou1", "dingtalk": "staff_1"}


class WorkerStore:
    """authorize_persisted / assert_credit 所需的最小内存 connection。"""

    def __init__(self, source):
        self.sessions = {
            "session": {
                "session_id": "session",
                "tenant_id": "tenant",
                "channel_type": source,
                "channel_user_id": _CHANNEL_USERS[source],
                "channel_chat_id": None,
                "subagent_id": "main",
                "user_id": "u-1",
                "metadata": {},
            }
        }
        self.users = {
            ("u-1", "tenant"): {
                "user_id": "u-1",
                "tenant_id": "tenant",
                "role": "employee",
                "status": "active",
            }
        }

    @contextmanager
    def connection(self):
        store = self

        class Cursor:
            def execute(self, sql, params=()):
                if "FROM channel_sessions" in sql:
                    self.row = store.sessions.get(params[0])
                elif "FROM users" in sql:
                    self.row = store.users.get((params[0], params[1]))
                elif "credit_balance FROM tenants" in sql:
                    self.row = {"credit_balance": 100}
                elif "FROM tenants" in sql:
                    self.row = {"status": "active", "expire_at": None}
                else:
                    raise AssertionError("Unexpected database I/O: " + sql)

            def fetchone(self):
                return copy.deepcopy(self.row)

        yield SimpleNamespace(cursor=lambda: Cursor())


class MemoryExecutions:
    """DurableControl 走到 completed 终态所需的最小执行仓库桩。

    正常完成路径只调用 stage_finalization（和心跳查询）；其余方法被意外
    调用即视为桩覆盖不足，直接失败暴露。
    """

    def __init__(self, row):
        self.row = row
        self.staged = None

    def heartbeat(self, attempt, lease_seconds):
        return {
            "closed": False,
            "cancel_requested": False,
            "pause_requested": False,
            "control_revision": 0,
        }

    def stage_finalization(self, attempt, revision, checkpoint, result, snapshot):
        self.row = {
            **self.row,
            "status": "finalizing",
            "checkpoint": checkpoint,
            "revision": self.row["revision"] + 1,
        }
        self.staged = copy.deepcopy(result)
        return {
            "status": "finalizing",
            "revision": self.row["revision"],
            "checkpoint": checkpoint,
            "public_snapshot": snapshot,
        }

    def assert_dispatch(self, *args, **kwargs):
        raise AssertionError("unexpected dispatch: stubbed runtime issues none")

    def save_checkpoint(self, *args, **kwargs):
        raise AssertionError("unexpected checkpoint save outside observer stub")

    def park(self, *args, **kwargs):
        raise AssertionError("unexpected park for completed execution")

    def interrupt_attempt(self, *args, **kwargs):
        raise AssertionError("unexpected interrupt for completed execution")


class RecordingRuntime:
    """记录型 runtime：run() 记录调用参数，产出已注册 verbose + response 事件。"""

    def __init__(self, state):
        self.state = state
        self.resources = SimpleNamespace(subagent_executor=None)
        self.run_args = None
        self.run_kwargs = None
        self.observed = []
        runtime = self

        class Observer:
            async def event(self, state, event):
                runtime.observed.append(event)

        self.observer = Observer()

    def run(self, *args, **kwargs):
        self.run_args = args
        self.run_kwargs = kwargs
        return self._events()

    async def _events(self):
        feedback = self.run_kwargs["verbose_state"]
        verbose = make_verbose_event("verbose-w1", "正在查询相关信息", "policy")
        if feedback.try_emit(verbose):
            yield verbose
        yield {"type": "response", "data": self.state.output}


class StubFactory:
    """记录型 runtime factory：resource_directory 指向 tmp_path。"""

    def __init__(self, resource_directory, runtime):
        self.resource_directory = resource_directory
        self.runtime = runtime
        self.create_calls = []
        self.attachments_calls = []

    async def create(self, row, principal, control):
        self.create_calls.append((row["runner_id"], principal.actor_kind))
        return self.runtime

    def attachments(self, row):
        self.attachments_calls.append(row["runner_id"])
        return []


def _channel_row(source):
    """与 bridge MemoryRunners 受理行同构的 channel 执行行。"""
    channel_user = _CHANNEL_USERS[source]
    request_data = {
        "channel_config_id": "config",
        # 渠道侧冻结的 verbose 配置（max_per_turn=3 区别于全局默认 1）
        "verbose_feedback": {
            "enabled": True,
            "force_disabled": False,
            "max_per_turn": 3,
            "max_text_chars": 42,
            "delivery_timeout_seconds": 5.0,
            "fallback_message": "请稍候",
        },
    }
    return {
        "runner_id": "runner-1",
        "session_kind": "channel",
        "session_id": "session",
        "tenant_id": "tenant",
        "scope_key": "tenant:tenant",
        "source": source,
        "actor_kind": "channel",
        "actor_id": canonical_json([channel_user, None]),
        "user_id": "u-1",
        "service_id": "bridge",
        "client_request_id": "req-1",
        "input": {
            "source": source,
            "session": {"kind": "channel", "session_id": "session"},
            "channel_user_id": channel_user,
            "channel_chat_id": None,
            "profile_id": "main",
            "text": "你好",
            "attachments": [],
            "prompt_augmentations": [],
            "request_data": request_data,
        },
        "profile_id": "main",
        "profile_fingerprint": "fingerprint",
        "status": "running",
        "attempt": 1,
        "queue_order": 1,
        "revision": 1,
        "view_revision": 1,
        "control_revision": 0,
        "cancel_requested": False,
        "pause_requested": False,
        "checkpoint": {},
        "public_snapshot": {},
        "result": None,
        "record_id": "record-1",
        "accepted_at": "2026-10-09",
        "updated_at": "2026-10-09",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["feishu", "dingtalk"])
async def test_worker_execute_delivers_channel_user_and_verbose_config(
    monkeypatch, tmp_path, source
):
    from src.config.settings import settings
    from src.db.models import UserDB

    store = WorkerStore(source)
    row = _channel_row(source)
    executions = MemoryExecutions(row)
    state = ExecutionState(
        Identity("tenant", "u-1", "session", source, "channel"),
        "runner-1",
        AgentMode.MASTER,
        "test",
        [],
        profile_id="main",
    )
    state.outcome = Outcome.COMPLETED
    state.output = "answer"
    factory = StubFactory(tmp_path, RecordingRuntime(state))
    config = AgentRunnerConfig(
        enabled=True,
        web_service_id="bridge",
        api_url="http://runner.test",
        peers={
            "bridge": {"sources": ["wecom_kf", "chat", "feishu", "dingtalk"]},
        },
    )
    finalizer = SimpleNamespace(finalize=MagicMock())
    worker = RunnerWorker(
        config,
        worker_id="worker-test",
        execution_repository=executions,
        repository=SimpleNamespace(
            get=lambda runner_id: copy.deepcopy(executions.row)
        ),
        authorizer=RunnerAuthorizer(config, store.connection),
        usage_repository=SimpleNamespace(),
        finalizer=finalizer,
        runtime_factory=factory,
    )

    # IO 边界 monkeypatch（见模块 docstring；被测路径本身不 patch）
    monkeypatch.setattr(
        UserDB,
        "get_by_id",
        lambda user_id: {
            "user_id": user_id,
            "username": f"{source}_x1",
            "nickname": "张三",
            "phone": "13800000000",
        },
    )
    monkeypatch.setattr(
        RunnerWorker, "_persist_trace", lambda self, *args, **kwargs: None
    )
    monkeypatch.setattr(
        settings.agent, "verbose_feedback", None, raising=False
    )

    await worker.execute(row)  # 正常返回不抛即主断言之一

    runtime = factory.runtime
    assert factory.create_calls == [("runner-1", "channel")]
    assert factory.attachments_calls == ["runner-1"]

    # 真实 _agent_user 渠道分支：nickname 优先并恢复渠道身份字段
    user = runtime.run_kwargs["user"]
    assert user.name == "张三"
    assert user.channel_type == source
    assert user.channel_user_id == _CHANNEL_USERS[source]
    assert user.tenant_id == "tenant" and user.phone == "13800000000"

    # 真实 channel_verbose_config：渠道配置生效（max_per_turn=3 非全局默认 1）
    verbose_config = runtime.run_kwargs["verbose_config"]
    assert verbose_config.enabled is True
    assert verbose_config.max_per_turn == 3
    assert verbose_config.max_text_chars == 42
    assert verbose_config.force_disabled is False

    # 请求上下文与输入透传
    assert runtime.run_args == ("你好",)
    assert runtime.run_kwargs["attachments"] == []
    assert runtime.run_kwargs["state"] is None
    request_context = runtime.run_kwargs["request_context"]
    assert request_context.request_data["verbose_feedback"]["max_per_turn"] == 3
    assert request_context.request_data["channel_config_id"] == "config"

    # 事件经 iter_with_verbose_feedback：已注册 verbose 事件到达 observer
    assert [event["type"] for event in runtime.observed] == ["verbose"]
    assert runtime.observed[0]["eventId"] == "verbose-w1"

    # 收尾：stage 终态 result 为 completed/answer，finalizer 被调用一次
    assert executions.staged["status"] == "completed"
    assert executions.staged["output"] == "answer"
    assert executions.row["status"] == "finalizing"
    finalizer.finalize.assert_called_once()
    # guard 文件已清理（Windows 兼容的 O_EXCL 创建/unlink 路径）
    assert not (tmp_path / "guards" / "runner-1.1.failure").exists()
