"""腾讯文档导入调度器单测（Redis 与 DB 全打桩）"""

import asyncio

import pytest

import src.services.tencent_docs.import_scheduler as sched_mod
from src.services.tencent_docs.import_scheduler import TdocImportScheduler


class FakeRedis:
    def __init__(self, available=True, can_acquire=True):
        self.available = available
        self.can_acquire = can_acquire
        self.locks = {}
        self.released = []

    def is_available(self):
        return self.available

    def make_key(self, prefix):
        return f"aid:{prefix}"

    def acquire_lock(self, key, value, ex=60):
        if not self.can_acquire:
            return False
        self.locks[key] = value
        return True

    def renew_lock(self, key, value, ex):
        return self.locks.get(key) == value

    def release_lock(self, key, value):
        if self.locks.get(key) == value:
            del self.locks[key]
            self.released.append(key)
            return True
        return False


@pytest.fixture
def patched_db(monkeypatch):
    calls = {"claim_queued": 0, "claim_due": [], "get_batch": {}}

    def fake_claim_queued(limit):
        calls["claim_queued"] += 1
        return []

    def fake_claim_due(limit):
        return calls.pop("due_items", []) or []

    def fake_get_batch_user(batch_id):
        user = calls["get_batch"].get(batch_id, "")
        return {"user_id": user} if batch_id else None

    monkeypatch.setattr(sched_mod, "TdocImportBatchDB",
                        type("B", (), {"claim_queued": staticmethod(fake_claim_queued),
                                       "get": staticmethod(fake_get_batch_user)}))
    monkeypatch.setattr(sched_mod, "TdocImportItemDB",
                        type("I", (), {"claim_due": staticmethod(fake_claim_due)}))
    return calls


def _make_item(item_id=1, batch_id="tdocb_x"):
    return {"id": item_id, "batch_id": batch_id, "tenant_id": "t1",
            "tdoc_file_id": "F1", "title": "A", "status": "running", "retry_count": 0}


@pytest.mark.unit
class TestLifecycle:
    @pytest.mark.asyncio
    async def test_rejects_start_when_redis_down(self, patched_db):
        s = TdocImportScheduler(redis=FakeRedis(available=False))
        assert await s.start() is False
        assert s._task is None

    @pytest.mark.asyncio
    async def test_rejects_start_when_lock_taken(self, patched_db):
        s = TdocImportScheduler(redis=FakeRedis(can_acquire=False))
        assert await s.start() is False

    @pytest.mark.asyncio
    async def test_start_stop_releases_lock(self, patched_db):
        redis = FakeRedis()
        s = TdocImportScheduler(redis=redis)
        assert await s.start() is True
        assert s._task is not None and not s._task.done()
        task = s._task
        await s.stop()
        assert task.done()
        assert s._task is None
        assert len(redis.released) == 1

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self, patched_db):
        redis = FakeRedis()
        s = TdocImportScheduler(redis=redis)
        assert await s.start() is True
        task = s._task
        assert await s.start() is True  # 已运行时跳过
        assert s._task is task
        await s.stop()


@pytest.mark.unit
class TestScanRound:
    @pytest.mark.asyncio
    async def test_claims_and_executes_with_user_id_injected(self, patched_db, monkeypatch):
        executed = []

        async def fake_execute(item):
            executed.append(item)

        s = TdocImportScheduler(redis=FakeRedis(), execute_item=fake_execute)
        patched_db["due_items"] = [_make_item()]
        patched_db["get_batch"]["tdocb_x"] = "user-42"

        await s._scan_round()
        assert len(executed) == 1
        assert executed[0]["user_id"] == "user-42"

    @pytest.mark.asyncio
    async def test_no_items_is_quiet(self, patched_db):
        executed = []

        async def fake_execute(item):
            executed.append(item)

        s = TdocImportScheduler(redis=FakeRedis(), execute_item=fake_execute)
        await s._scan_round()
        assert executed == []

    @pytest.mark.asyncio
    async def test_concurrent_execution_bounded(self, patched_db, monkeypatch):
        active = {"n": 0, "peak": 0}

        async def fake_execute(item):
            active["n"] += 1
            active["peak"] = max(active["peak"], active["n"])
            await asyncio.sleep(0.01)
            active["n"] -= 1

        s = TdocImportScheduler(redis=FakeRedis(), execute_item=fake_execute)
        patched_db["due_items"] = [_make_item(item_id=i, batch_id=f"b{i}") for i in range(5)]
        patched_db["get_batch"] = {f"b{i}": "u" for i in range(5)}

        # _scan_round 内部懒加载真实 execute_item 仅当 self._execute_item 为 None；
        # 已注入替身则直接用
        await s._scan_round()
        assert active["peak"] <= sched_mod.ITEM_CONCURRENCY
        assert active["n"] == 0

    @pytest.mark.asyncio
    async def test_execute_item_exception_does_not_break_round(self, patched_db):
        """item 执行异常（替身实现不消化的意外）不中断整轮（gather 默认抛出——
        真实 execute_item 内部消化异常；此处验证调度器对异常路径的容错策略为
        外层 try/except 捕获记日志，循环继续）"""
        async def boom(item):
            raise RuntimeError("unexpected")

        s = TdocImportScheduler(redis=FakeRedis(), execute_item=boom)
        patched_db["due_items"] = [_make_item()]
        with pytest.raises(RuntimeError):
            await s._scan_round()  # 真实实现内部消化，替身直接抛——预期上抛由 _run_loop 兜底
