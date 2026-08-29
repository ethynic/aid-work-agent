"""定时任务执行器请求级租户上下文隔离与恢复测试（不依赖 PG/LLM，mock agent）

覆盖安全加固设计「请求级上下文隔离与恢复」门禁：
- _resolve_exec_tenant_id：用户租户优先 / None 回落 / 空串公共身份不被覆盖 / 冲突抛错
- execute：冲突 fail-closed（不调 Agent、不重试、失败日志带任务行租户）；
  成功 / 异常 / 嵌套（进入前已有上下文）路径结束后恢复进入前的 ContextVar 值
- dry_run：与 execute 同一解析优先级与恢复语义
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.saas.context import (
    get_current_tenant_id,
    get_current_user_id,
    set_tenant_context,
)
from src.scheduler.executor import (
    ScheduledTaskExecutor,
    TenantContextConflictError,
    _resolve_exec_tenant_id,
)


@pytest.fixture(autouse=True)
def _restore_tenant_context():
    """用例结束后恢复租户上下文，避免用例间串扰"""
    yield
    set_tenant_context(None, None)


def _make_task(**overrides):
    task = {
        "task_id": "task-1",
        "user_id": "user-1",
        "name": "每日摘要",
        "task_prompt": "生成摘要",
        "status": "active",
        "max_retries": 0,
        "retry_count": 0,
        "tenant_id": "tenant_task",
    }
    task.update(overrides)
    return task


def _make_user_dict(**overrides):
    user = {
        "user_id": "user-1",
        "username": "张三",
        "phone": "13800000000",
        "role": "employee",
        "tenant_id": "tenant_user",
    }
    user.update(overrides)
    return user


def _make_executor(agent_side_effect):
    """构造 mock agent 的执行器；agent_side_effect 在 Agent 调用点同步执行"""
    executor = ScheduledTaskExecutor()
    agent = MagicMock()
    agent.process_message_sync = AsyncMock(side_effect=agent_side_effect)
    executor._agent = agent
    return executor, agent


# ==================== _resolve_exec_tenant_id ====================


def test_resolve_user_tenant_wins_over_none_fallback():
    assert _resolve_exec_tenant_id({"tenant_id": "tenant_user"}, None) == "tenant_user"


def test_resolve_user_tenant_equal_fallback_no_conflict():
    assert (
        _resolve_exec_tenant_id({"tenant_id": "tenant_a"}, "tenant_a") == "tenant_a"
    )


def test_resolve_none_user_tenant_falls_back():
    assert _resolve_exec_tenant_id({"tenant_id": None}, "tenant_task") == "tenant_task"


def test_resolve_missing_user_tenant_key_falls_back():
    assert _resolve_exec_tenant_id({"user_id": "user-1"}, "tenant_task") == "tenant_task"


def test_resolve_none_user_dict_falls_back():
    assert _resolve_exec_tenant_id(None, "tenant_task") == "tenant_task"


def test_resolve_empty_string_user_tenant_not_overridden_by_none():
    """空串是明确公共租户身份，不能被 None（未知）fallback 覆盖"""
    assert _resolve_exec_tenant_id({"tenant_id": ""}, None) == ""


def test_resolve_empty_string_user_tenant_with_empty_fallback():
    assert _resolve_exec_tenant_id({"tenant_id": ""}, "") == ""


def test_resolve_user_tenant_with_empty_fallback_normal():
    """fallback='' 视同未指定（任务行 ''=公共用户或遗留未回填，存在遗留数据），
    用户行非空租户直接生效，不得抛冲突导致遗留任务全量拒绝执行"""
    assert _resolve_exec_tenant_id({"tenant_id": "tenant_user"}, "") == "tenant_user"


def test_resolve_both_non_empty_unequal_raises_conflict():
    with pytest.raises(TenantContextConflictError, match="定时任务租户上下文冲突"):
        _resolve_exec_tenant_id({"tenant_id": "tenant_user"}, "tenant_task")


def test_resolve_public_user_with_tenant_fallback_fails_closed():
    """公共用户（''）配非空 fallback 属身份错配：fail-closed 抛冲突，
    而不是静默把任务按公共租户执行（任务行记录却归属该租户）"""
    with pytest.raises(TenantContextConflictError):
        _resolve_exec_tenant_id({"tenant_id": ""}, "tenant_task")


# ==================== execute ====================


@pytest.mark.asyncio
async def test_execute_success_sets_context_and_restores_none():
    captured = {}

    def agent_call(**kwargs):
        captured["user"] = kwargs.get("user")
        captured["tenant"] = get_current_tenant_id()
        captured["ctx_user"] = get_current_user_id()
        return "任务结果"

    executor, agent = _make_executor(agent_call)

    with patch.object(ScheduledTaskExecutor, "_build_user",
                      return_value=_make_user_dict(tenant_id="tenant_a")), \
         patch.object(ScheduledTaskExecutor, "_get_cron_session_id",
                      return_value="cron_user-1"), \
         patch("src.scheduler.executor.ScheduledTaskDB") as task_db, \
         patch("src.scheduler.executor.ScheduledTaskLogDB") as log_db:
        task_db.get_by_id.return_value = _make_task(tenant_id="tenant_a")
        log_db.create.return_value = {"log_id": "log-1"}
        result = await executor.execute("task-1")

    assert result["success"] is True
    # 执行期间：ContextVar 与 User 身份都携带解析后的租户
    assert captured["tenant"] == "tenant_a"
    assert captured["ctx_user"] == "user-1"
    assert captured["user"].tenant_id == "tenant_a"
    # 结束后恢复进入前值（后台线程进入前为 None）
    assert get_current_tenant_id() is None
    assert get_current_user_id() is None


@pytest.mark.asyncio
async def test_execute_exception_restores_context():
    def agent_call(**kwargs):
        assert get_current_tenant_id() == "tenant_a"
        raise RuntimeError("boom")

    executor, agent = _make_executor(agent_call)

    with patch.object(ScheduledTaskExecutor, "_build_user",
                      return_value=_make_user_dict(tenant_id="tenant_a")), \
         patch.object(ScheduledTaskExecutor, "_get_cron_session_id",
                      return_value="cron_user-1"), \
         patch("src.scheduler.executor.ScheduledTaskDB") as task_db, \
         patch("src.scheduler.executor.ScheduledTaskLogDB") as log_db:
        task_db.get_by_id.return_value = _make_task(tenant_id="tenant_a")
        log_db.create.return_value = {"log_id": "log-1"}
        result = await executor.execute("task-1")

    assert result["success"] is False
    assert get_current_tenant_id() is None
    assert get_current_user_id() is None


@pytest.mark.asyncio
async def test_execute_nested_restores_pre_entry_context():
    """进入前已有请求租户上下文（嵌套场景）：恢复原值而非清成 None"""
    def agent_call(**kwargs):
        assert get_current_tenant_id() == "tenant_task"
        return "任务结果"

    executor, agent = _make_executor(agent_call)

    set_tenant_context("tenant_req", "user_req")
    with patch.object(ScheduledTaskExecutor, "_build_user",
                      return_value=_make_user_dict(tenant_id=None)), \
         patch.object(ScheduledTaskExecutor, "_get_cron_session_id",
                      return_value="cron_user-1"), \
         patch("src.scheduler.executor.ScheduledTaskDB") as task_db, \
         patch("src.scheduler.executor.ScheduledTaskLogDB") as log_db:
        task_db.get_by_id.return_value = _make_task(tenant_id="tenant_task")
        log_db.create.return_value = {"log_id": "log-1"}
        result = await executor.execute("task-1")

    assert result["success"] is True
    assert get_current_tenant_id() == "tenant_req"
    assert get_current_user_id() == "user_req"


@pytest.mark.asyncio
async def test_execute_conflict_fails_closed_without_agent_call():
    """用户行租户与任务行租户冲突：不调 Agent、不重试、落失败日志带任务行租户"""
    executor, agent = _make_executor(lambda **kwargs: "不应被执行")

    set_tenant_context("tenant_req", "user_req")
    with patch.object(ScheduledTaskExecutor, "_build_user",
                      return_value=_make_user_dict(tenant_id="tenant_user")), \
         patch.object(ScheduledTaskExecutor, "_get_cron_session_id",
                      return_value="cron_user-1"), \
         patch("src.scheduler.executor.ScheduledTaskDB") as task_db, \
         patch("src.scheduler.executor.ScheduledTaskLogDB") as log_db, \
         patch("src.db.database.get_db_connection") as conn:
        # max_retries=3：若冲突误走通用异常分支会触发重试计数 SQL，
        # 使下方 conn.assert_not_called() 有实际区分力（默认 0 时恒过）
        task_db.get_by_id.return_value = _make_task(tenant_id="tenant_task", max_retries=3)
        log_db.create.return_value = {"log_id": "log-1"}
        result = await executor.execute("task-1")

        agent.process_message_sync.assert_not_awaited()
        conn.assert_not_called()  # 冲突不走重试
        assert result["success"] is False
        assert result["error"] == "定时任务租户上下文冲突"
        # 失败日志带任务行租户、固定错误文案（不泄漏双方租户值）
        log_db.create.assert_called_once()
        log_kwargs = log_db.create.call_args.kwargs
        assert log_kwargs["tenant_id"] == "tenant_task"
        assert log_kwargs["status"] == "failed"
        assert log_kwargs["error_message"] == "定时任务租户上下文冲突"
        assert "tenant_user" not in str(result) and "tenant_task" not in str(result["error"])
        task_db.update_after_run.assert_called_once()
        assert task_db.update_after_run.call_args.kwargs["success"] is False
        # 冲突不改变进入前上下文
        assert get_current_tenant_id() == "tenant_req"
        assert get_current_user_id() == "user_req"


# ==================== dry_run ====================


@pytest.mark.asyncio
async def test_dry_run_same_priority_and_restore():
    """试执行与正式执行同一解析规则（用户行优先、None 回落、空串不覆盖）+ 恢复"""
    captured = {}

    def agent_call(**kwargs):
        captured.setdefault("seen", []).append(
            (get_current_tenant_id(), get_current_user_id(), kwargs.get("user").tenant_id)
        )
        return "试执行结果"

    cases = [
        # (user_tenant, fallback_tenant, expected_exec_tenant)
        ("tenant_user", None, "tenant_user"),      # 用户行优先
        (None, "tenant_req", "tenant_req"),        # None 回落
        ("", None, ""),                            # 空串公共身份不被覆盖
        ("tenant_user", "", "tenant_user"),        # fallback='' 视同未指定，用户行生效
    ]
    for user_tenant, fallback, expected in cases:
        executor, agent = _make_executor(agent_call)
        set_tenant_context("tenant_outer", "user_outer")  # 模拟请求协程内嵌套 await
        with patch.object(ScheduledTaskExecutor, "_build_user",
                          return_value=_make_user_dict(tenant_id=user_tenant)), \
             patch.object(ScheduledTaskExecutor, "_get_cron_session_id",
                          return_value="cron_user-1"):
            result = await executor.dry_run("user-1", "prompt", tenant_id=fallback)

        assert result["success"] is True
        ctx_tenant, ctx_user, user_tenant_seen = captured["seen"][-1]
        assert ctx_tenant == expected
        assert ctx_user == "user-1"
        assert user_tenant_seen == expected
        # finally 恢复进入前的请求上下文（而非 clear）
        assert get_current_tenant_id() == "tenant_outer"
        assert get_current_user_id() == "user_outer"


@pytest.mark.asyncio
async def test_dry_run_conflict_fails_closed():
    executor, agent = _make_executor(lambda **kwargs: "不应被执行")

    set_tenant_context("tenant_req", "user_req")
    with patch.object(ScheduledTaskExecutor, "_build_user",
                      return_value=_make_user_dict(tenant_id="tenant_user")), \
         patch.object(ScheduledTaskExecutor, "_get_cron_session_id",
                      return_value="cron_user-1"):
        result = await executor.dry_run("user-1", "prompt", tenant_id="tenant_req")

    agent.process_message_sync.assert_not_awaited()
    assert result["success"] is False
    assert result["error"] == "定时任务租户上下文冲突"
    assert "tenant_user" not in result["error"] and "tenant_req" not in result["error"]
    assert get_current_tenant_id() == "tenant_req"
    assert get_current_user_id() == "user_req"
