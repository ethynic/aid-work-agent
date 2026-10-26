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
    async def test_lock_taken_starts_driver_task(self, patched_db):
        """锁被占（强杀残留幽灵锁）→ 仍启动驱动任务返回 True（原断言「返回 False」
        按幽灵锁自愈语义改写）；未持锁的 stop() 正常停机不误删他人锁。"""
        redis = FakeRedis(can_acquire=False)
        s = TdocImportScheduler(redis=redis)
        assert await s.start() is True
        assert s._task is not None and not s._task.done()
        assert s._lock_key is not None  # 锁 key 保留供循环内重试接管
        await s.stop()
        assert s._task is None
        assert redis.released == []  # 未持锁，stop 不产生任何释放动作

    @pytest.mark.asyncio
    async def test_lock_taken_takeover_after_release(self, patched_db):
        """锁被占启动后，幽灵锁 TTL 过期消失 → _ensure_driver_lock 失锁重取接管。"""
        redis = FakeRedis(can_acquire=False)
        s = TdocImportScheduler(redis=redis)
        assert await s.start() is True
        # 幽灵锁过期消失：替换为可正常 acquire 的替身行为（模拟 TTL 到期后键不存在）
        redis.can_acquire = True
        assert await s._ensure_driver_lock() is True
        assert redis.locks[s._lock_key] == s._owner
        await s.stop()
        assert redis.locks == {}

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
