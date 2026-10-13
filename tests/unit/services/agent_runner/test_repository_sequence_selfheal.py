"""submit() queue_order 序列自愈编排的单元级验证：stub 连接复刻生产
CursorWrapper 的共享 cursor 语义，覆盖负向分支，不触真实数据库。"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services.agent_runner import repository as repository_module
from src.services.agent_runner.repository import QUEUE_ORDER_REALIGN_SQL, RunnerRepository

pytestmark = pytest.mark.unit

QUEUE_ORDER_KEY = "agent_runners_queue_order_key"


class StubUniqueViolation(Exception):
    """psycopg2 UniqueViolation 替身，diag.constraint_name 可注入。"""

    def __init__(self, constraint_name):
        super().__init__(constraint_name)
        self.diag = SimpleNamespace(constraint_name=constraint_name)


class FakeCursor:
    def __init__(self):
        self.statements = []
        self.fetchone_result = {"setval": 0}
        self.closed = False

    def execute(self, statement, *args):
        self.statements.append(statement)

    def fetchone(self):
        return self.fetchone_result

    def close(self):
        self.closed = True


class FakeConnection:
    """conn.cursor() 每次返回同一 cursor，模拟生产 CursorWrapper 行为。"""

    def __init__(self, cursor):
        self._cursor = cursor
        self.rollbacks = 0
        self.commits = 0

    def cursor(self):
        return self._cursor

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        self.commits += 1


@pytest.fixture
def harness():
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    submit_calls = []
    outcomes = []

    @contextmanager
    def factory():
        yield connection

    def scripted_submit(self, cursor_arg, *args, **kwargs):
        submit_calls.append(cursor_arg)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with patch.object(repository_module, "psycopg2",
                      SimpleNamespace(errors=SimpleNamespace(UniqueViolation=StubUniqueViolation))), \
         patch.object(RunnerRepository, "submit_in_tx", scripted_submit):
        repository = RunnerRepository(factory)
        principal = SimpleNamespace(scope_key="tenant:tenant_x", actor_id="user_x")
        yield SimpleNamespace(repository=repository, cursor=cursor, connection=connection,
                              submit_calls=submit_calls, outcomes=outcomes, principal=principal)


def test_queue_order_conflict_realigns_once_and_retries_on_shared_cursor(harness):
    harness.outcomes.extend([StubUniqueViolation(QUEUE_ORDER_KEY), ({"runner_id": "runner_x"}, True)])
    row, created = harness.repository.submit(harness.principal, object(), "fp")
    assert (row, created) == ({"runner_id": "runner_x"}, True)
    # 自愈链路：rollback → setval 对齐 → commit → 在同一（未关闭的）cursor 上重试一次
    assert harness.connection.rollbacks == 1
    assert harness.cursor.statements.count(QUEUE_ORDER_REALIGN_SQL) == 1
    assert len(harness.submit_calls) == 2 and harness.submit_calls[0] is harness.submit_calls[1]
    assert not harness.cursor.closed


def test_other_unique_constraints_propagate_without_realign(harness):
    harness.outcomes.append(StubUniqueViolation("agent_runners_pkey"))
    with pytest.raises(StubUniqueViolation):
        harness.repository.submit(harness.principal, object(), "fp")
    assert harness.connection.rollbacks == 0
    assert harness.cursor.statements == []
    assert len(harness.submit_calls) == 1


def test_conflict_again_after_realigned_retry_propagates(harness):
    harness.outcomes.extend([StubUniqueViolation(QUEUE_ORDER_KEY), StubUniqueViolation(QUEUE_ORDER_KEY)])
    with pytest.raises(StubUniqueViolation):
        harness.repository.submit(harness.principal, object(), "fp")
    assert harness.connection.rollbacks == 1
    assert harness.cursor.statements.count(QUEUE_ORDER_REALIGN_SQL) == 1
    assert len(harness.submit_calls) == 2
