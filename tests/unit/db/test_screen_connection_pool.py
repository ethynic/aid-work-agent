"""src.db.database 的画面专用连接池与画面作用域（假的 ThreadedConnectionPool，不连数据库）

- 生命周期：init_postgres_pool 只创建业务连接池；画面连接池到第一次在画面作用域里借连接时才创建，
  多个线程同时第一次用到也只创建一次；minconn = maxconn = SCREEN_DB_POOL_MAX，一次建满；close_postgres_pool
  在它已创建时关闭，关闭后下次用到会重新创建；get_screen_pool 只读，不触发创建
- 连接复用：假连接池照 psycopg2 2.9.13 的 AbstractConnectionPool 写（归还时只在「空闲数小于 minconn」时保留，
  否则关闭），所以「用完就关」这种问题在这里测得出来：画面连接池借还多次之后新建的连接数不变
- get_db_connection：画面作用域里从画面连接池借，作用域外从业务连接池借；正常路径、异常路径都归还到借来的那个池；
  回滚不了的连接、借出来已经失效的连接，都在借来的那个池里丢弃；业务连接池这一路的行为不变
- 画面作用域：默认不在；进入后可见，退出（包括异常退出）后恢复；随上下文复制到别的线程
- 健康检查：后台线程每轮只查业务连接池；画面连接仅在借出后检查
"""

import threading
import time
from contextlib import nullcontext
from contextvars import copy_context
from types import SimpleNamespace
from unittest.mock import Mock

import psycopg2
import psycopg2.pool
import pytest

from src.db import database

pytestmark = [pytest.mark.unit, pytest.mark.db]


class _FakeCursor:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, statement, params=None):
        if self._connection.dead_on_check:
            raise psycopg2.OperationalError("server closed the connection unexpectedly")

    def close(self):
        pass


class _FakeConnection:
    """假的 psycopg2 连接。pid 是它所属的连接池给的编号，相当于数据库那一侧的 backend 进程号"""

    def __init__(self, pid):
        self.pid = pid
        self.closed = False
        self.dead_on_check = False       # 借出时的存活检查（SELECT 1）会失败
        self.cannot_roll_back = False
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self)

    def rollback(self):
        if self.cannot_roll_back:
            raise psycopg2.OperationalError("connection already closed")
        self.rollbacks += 1

    def close(self):
        self.closed = True


class _FakePool:
    """假的 ThreadedConnectionPool，逻辑照 psycopg2 2.9.13 的 AbstractConnectionPool 写：

    - 创建时一次建 minconn 条连接
    - getconn：有空闲的就用空闲的；没有空闲的，已借出的还没到 maxconn 就新建一条，到了就报 connection pool exhausted
    - putconn：只有「空闲连接数小于 minconn」才保留归还的连接，否则关闭它（minconn=0 等于每次归还都关）
    - closeall：关闭全部连接，之后这个池不能再借

    在此之上记两个量：created 是一共新建了多少条连接；connect_delay 是每新建一条连接要等的时间
    （把并发第一次建池的窗口放大）
    """

    def __init__(self, minconn, maxconn, **options):
        self.minconn, self.maxconn, self.options = int(minconn), int(maxconn), options
        self.closed = False
        self.created = 0
        self.connect_delay = 0
        self._pool = []
        self._used = {}
        self._rused = {}
        self._keys = 0
        self._lock = threading.Lock()
        for _ in range(self.minconn):
            self._connect()

    def _connect(self, key=None):
        if self.connect_delay:
            time.sleep(self.connect_delay)
        self.created += 1
        conn = _FakeConnection(self.created)
        if key is not None:
            self._used[key] = conn
            self._rused[id(conn)] = key
        else:
            self._pool.append(conn)
        return conn

    def _getkey(self):
        self._keys += 1
        return self._keys

    def _getconn(self, key=None):
        if self.closed:
            raise psycopg2.pool.PoolError("connection pool is closed")
        if key is None:
            key = self._getkey()
        if key in self._used:
            return self._used[key]
        if self._pool:
            self._used[key] = conn = self._pool.pop()
            self._rused[id(conn)] = key
            return conn
        if len(self._used) == self.maxconn:
            raise psycopg2.pool.PoolError("connection pool exhausted")
        return self._connect(key)

    def _putconn(self, conn, key=None, close=False):
        if self.closed:
            raise psycopg2.pool.PoolError("connection pool is closed")
        if key is None:
            key = self._rused.get(id(conn))
            if key is None:
                raise psycopg2.pool.PoolError("trying to put unkeyed connection")
        if len(self._pool) < self.minconn and not close:
            if not conn.closed:
                self._pool.append(conn)
        else:
            conn.close()
        if not self.closed or key in self._used:
            del self._used[key]
            del self._rused[id(conn)]

    def _closeall(self):
        if self.closed:
            raise psycopg2.pool.PoolError("connection pool is closed")
        for conn in self._pool + list(self._used.values()):
            conn.close()
        self.closed = True

    def getconn(self, key=None):
        with self._lock:
            return self._getconn(key)

    def putconn(self, conn=None, key=None, close=False):
        with self._lock:
            self._putconn(conn, key, close)

    def closeall(self):
        with self._lock:
            self._closeall()


def _scope(in_scope):
    """in_scope 为真时进入画面作用域，否则什么都不做"""
    return database.screen_connection_scope() if in_scope else nullcontext()


def _idle(pool):
    """池里空闲连接的编号（按先后顺序）"""
    with pool._lock:
        return [conn.pid for conn in pool._pool]


@pytest.fixture
def pools(monkeypatch):
    """业务连接池和画面连接池都换成已经建好的假池，返回 (业务连接池, 画面连接池)"""
    business, screen = _FakePool(2, 10), _FakePool(database.SCREEN_DB_POOL_MAX, database.SCREEN_DB_POOL_MAX)
    monkeypatch.setattr(database, "_pg_connection_pool", business)
    monkeypatch.setattr(database, "_screen_connection_pool", screen)
    return business, screen


class TestFakePoolModelsPsycopg2:
    """先确认假连接池确实体现了 psycopg2 2.9.13 的行为，不然下面测不到真实问题"""

    def test_a_pool_with_minconn_zero_closes_every_returned_connection(self):
        pool = _FakePool(0, 3)
        for _ in range(5):
            pool.putconn(pool.getconn())
        assert (pool.created, _idle(pool)) == (5, [])  # 借 5 次、建 5 条，归还的全关了

    def test_a_pool_keeps_returned_connections_only_up_to_minconn(self):
        pool = _FakePool(2, 4)
        for _ in range(5):
            pool.putconn(pool.getconn())
        assert (pool.created, _idle(pool)) == (2, [1, 2])  # 借 5 次、只用最初建的 2 条

    def test_connections_beyond_maxconn_at_once_are_refused(self):
        pool = _FakePool(1, 2)
        first, second = pool.getconn(), pool.getconn()
        with pytest.raises(psycopg2.pool.PoolError, match="connection pool exhausted"):
            pool.getconn()
        pool.putconn(first)
        pool.putconn(second)


class TestGetDbConnection:
    def test_outside_the_scope_it_borrows_from_the_business_pool(self, pools):
        business, screen = pools
        before_business, before_screen = _idle(business), _idle(screen)
        with database.get_db_connection() as conn:
            conn.execute("SELECT 1")
        assert _idle(business) == before_business  # 借走的那条回到了业务连接池
        assert _idle(screen) == before_screen and screen._used == {}
        assert business.created == business.minconn

    def test_inside_the_scope_it_borrows_from_the_screen_pool(self, pools):
        business, screen = pools
        before_business, before_screen = _idle(business), _idle(screen)
        with database.screen_connection_scope():
            with database.get_db_connection() as conn:
                assert database.in_screen_scope() is True
                conn.execute("SELECT 1")
        assert _idle(screen) == before_screen  # 借走的那条回到了画面连接池
        assert _idle(business) == before_business and business._used == {}
        assert (business.created, screen.created) == (business.minconn, screen.minconn)

    @pytest.mark.parametrize("in_scope", [False, True], ids=["business-pool", "screen-pool"])
    def test_connection_is_returned_to_the_pool_it_came_from_when_the_body_raises(self, pools, in_scope):
        business, screen = pools
        pool, other = (screen, business) if in_scope else (business, screen)
        before_pool, before_other = _idle(pool), _idle(other)
        with pytest.raises(ValueError, match="boom"):
            with _scope(in_scope):
                with database.get_db_connection():
                    raise ValueError("boom")
        assert _idle(pool) == before_pool and pool._used == {}
        assert _idle(other) == before_other

    @pytest.mark.parametrize("in_scope", [False, True], ids=["business-pool", "screen-pool"])
    def test_connection_that_cannot_roll_back_is_discarded_from_the_pool_it_came_from(self, pools, in_scope):
        """正常退出时回滚失败的连接被关闭丢弃，不回池"""
        business, screen = pools
        pool, other = (screen, business) if in_scope else (business, screen)
        before = _idle(pool)
        borrowed = pool._pool[-1]
        borrowed.cannot_roll_back = True
        with _scope(in_scope):
            with database.get_db_connection():
                pass
        assert borrowed.closed is True
        assert _idle(pool) == before[:-1] and pool._used == {}
        assert len(_idle(other)) == other.minconn

    def test_dead_connection_is_replaced_from_the_same_pool(self, pools):
        """借出来已经失效的连接（存活检查失败）：在借来的那个池里关闭丢弃，再从同一个池借下一张"""
        business, screen = pools
        dead = screen._pool[-1]
        dead.dead_on_check = True
        with _scope(True):
            with database.get_db_connection() as conn:
                conn.execute("SELECT 1")
        assert dead.closed is True
        assert len(_idle(screen)) == screen.minconn - 1 and screen._used == {}
        assert screen.created == screen.minconn and len(_idle(business)) == business.minconn

    def test_business_pool_path_is_unchanged_without_a_pool(self, monkeypatch, pools):
        """业务连接池这一路不变：连接池没初始化时还是原来那条报错"""
        monkeypatch.setattr(database, "_pg_connection_pool", None)
        with pytest.raises(RuntimeError, match="PostgreSQL 连接池未初始化"):
            with database.get_db_connection():
                pass
        with pytest.raises(RuntimeError, match="PostgreSQL 连接池未初始化"):
            database.get_pooled_connection()

    def test_borrow_and_return_without_a_pool_argument_use_the_business_pool(self, pools):
        """直接借、还的旧用法（知识库、文件存储）不带 pool 参数：照旧走业务连接池，在画面作用域里也一样"""
        business, screen = pools
        with _scope(True):
            conn = database.get_pooled_connection()
            database.return_pooled_connection(conn)
        assert conn in business._pool and screen._used == {}
        assert (business.created, screen.created) == (business.minconn, screen.minconn)


class TestScreenConnectionScope:
    def test_code_runs_outside_the_scope_by_default(self):
        assert database.in_screen_scope() is False

    def test_scope_is_visible_inside_and_gone_afterwards(self):
        with database.screen_connection_scope():
            assert database.in_screen_scope() is True
        assert database.in_screen_scope() is False

    def test_scope_is_gone_after_an_exception_inside(self):
        with pytest.raises(ValueError):
            with database.screen_connection_scope():
                raise ValueError("boom")
        assert database.in_screen_scope() is False

    def test_scope_travels_with_a_copied_context(self):
        """asyncio.to_thread 和线程池都是把当前上下文复制到别的线程里执行：作用域里复制出去的上下文在作用域里，
        作用域结束后复制出去的不在"""
        with database.screen_connection_scope():
            context = copy_context()  # asyncio.to_thread 和线程池就是在调用方这里复制上下文，再带进工作线程
            seen = []
            worker = threading.Thread(target=lambda: seen.append(context.run(database.in_screen_scope)))
            worker.start()
            worker.join(timeout=5)
            assert seen == [True]
        assert copy_context().run(database.in_screen_scope) is False


class TestPoolLifecycle:
    @pytest.fixture(autouse=True)
    def fresh_pools(self):
        """模块级的两个连接池由测试自己设置，结束后恢复原状"""
        before = database._pg_connection_pool, database._screen_connection_pool
        database._pg_connection_pool = database._screen_connection_pool = None
        yield
        database._pg_connection_pool, database._screen_connection_pool = before

    @pytest.fixture
    def created(self, monkeypatch):
        """把 ThreadedConnectionPool 换成假池，返回建出来的池的列表；后台健康检查线程不启动"""

        def fake_pool(minconn, maxconn, **options):
            created.append(_FakePool(minconn, maxconn, **options))
            return created[-1]

        created = []
        monkeypatch.setattr(database.pg_pool, "ThreadedConnectionPool", fake_pool)
        monkeypatch.setattr(database, "_start_pool_health_check_thread", lambda: None)
        return created

    @staticmethod
    def _assert_same_database_options(business, screen):
        """连同一个库：连接参数相同，只有 application_name 不同"""
        assert set(screen.options) == set(business.options)
        assert {key: value for key, value in screen.options.items() if key != "application_name"} == \
            {key: value for key, value in business.options.items() if key != "application_name"}
        assert (business.options["application_name"], screen.options["application_name"]) == (
            "aid-work-agent", "aid-work-agent-screen")

    def test_init_postgres_pool_creates_only_the_business_pool(self, created):
        database.init_postgres_pool()
        assert database.get_postgres_pool() is created[0]
        assert database.get_screen_pool() is None
        assert len(created) == 1

    def test_borrowing_outside_the_scope_and_reading_the_pool_do_not_create_it(self, created):
        database.init_postgres_pool()
        for _ in range(3):
            with database.get_db_connection():
                pass
        assert database.get_screen_pool() is None  # 只读，不触发创建
        assert len(created) == 1

    def test_first_borrow_in_the_scope_creates_the_pool_once_and_full(self, created):
        database.init_postgres_pool()
        business = created[0]
        with _scope(True):
            for _ in range(20):
                with database.get_db_connection() as conn:
                    conn.execute("SELECT 1")
        screen = database.get_screen_pool()
        assert screen is created[1] and len(created) == 2
        assert (screen.minconn, screen.maxconn) == (database.SCREEN_DB_POOL_MAX,) * 2
        # 20 次借用用的都是一次建满的那几条：没有再新建，也没有关掉重建
        assert screen.created == database.SCREEN_DB_POOL_MAX
        assert sorted(_idle(screen)) == list(range(1, database.SCREEN_DB_POOL_MAX + 1))
        assert screen._used == {} and business._used == {}
        self._assert_same_database_options(business, screen)

    def test_concurrent_first_use_creates_the_pool_only_once(self, created, monkeypatch):
        """好几个线程同时第一次在画面作用域里借连接：只建一个画面连接池。建池要连数据库，在这里把它放慢，
        把并发窗口放大；没有锁的话这些线程会各建一个池"""
        database.init_postgres_pool()

        def slow_fake_pool(minconn, maxconn, **options):
            pool = _FakePool(minconn, maxconn, **options)
            pool.connect_delay = 0.05
            created.append(pool)
            return pool

        monkeypatch.setattr(database.pg_pool, "ThreadedConnectionPool", slow_fake_pool)
        workers = 8
        start = threading.Barrier(workers)
        borrowed, refused = [], []

        def borrow():
            start.wait(timeout=10)
            try:
                with _scope(True):
                    with database.get_db_connection() as conn:
                        conn.execute("SELECT 1")
                borrowed.append(threading.current_thread().name)
            except psycopg2.pool.PoolError as error:  # 名额比线程少：同时借出的超过上限时池会拒绝
                refused.append(str(error))

        threads = [threading.Thread(target=borrow) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads)
        assert len(borrowed) + len(refused) == workers
        assert len(created) == 2, f"建出了 {len(created) - 1} 个画面连接池"
        assert database.get_screen_pool() is created[1]
        assert created[1].created == database.SCREEN_DB_POOL_MAX
        assert len(borrowed) >= database.SCREEN_DB_POOL_MAX
        assert all("exhausted" in message for message in refused)

    def test_explicit_sizes_apply_to_the_business_pool_only(self, created):
        """有的调用方给业务连接池指定很小的上下限（后台进程）；画面连接池不跟着变小"""
        database.init_postgres_pool(minconn=1, maxconn=2)
        with _scope(True):
            with database.get_db_connection():
                pass
        business, screen = created
        assert (business.minconn, business.maxconn) == (1, 2)
        assert (screen.minconn, screen.maxconn) == (database.SCREEN_DB_POOL_MAX,) * 2

    def test_close_postgres_pool_closes_both_and_the_next_use_creates_the_pool_again(self, created):
        database.init_postgres_pool()
        with _scope(True):
            with database.get_db_connection():
                pass
        business, first = created
        database.close_postgres_pool()
        assert (business.closed, first.closed) == (True, True)
        assert database.get_postgres_pool() is None and database.get_screen_pool() is None

        database.init_postgres_pool()  # 重新初始化仍然只建业务连接池
        assert len(created) == 3
        with _scope(True):
            with database.get_db_connection():
                pass
        assert database.get_screen_pool() is created[3]
        assert created[3].created == database.SCREEN_DB_POOL_MAX and created[3] is not first

    def test_close_postgres_pool_without_a_screen_pool(self, created):
        database.init_postgres_pool()
        database.close_postgres_pool()
        assert created[0].closed and database.get_postgres_pool() is None
        assert database.get_screen_pool() is None


class TestHealthCheck:
    def _kill_one_idle_connection(self, pool):
        pool._pool[-1].dead_on_check = True

    def test_a_bad_idle_connection_is_replaced_and_the_business_pool_stays_full(self, pools):
        """清掉一条坏连接后补回一条，池里仍然是 minconn 条活的、各不相同的连接。"""
        pool, screen = pools
        self._kill_one_idle_connection(pool)
        result = database.health_check_pool()
        assert (result["initialized"], result["healthy"], result["bad"]) == (True, pool.minconn - 1, 1)
        assert pool.created == pool.minconn + 1
        idle = _idle(pool)
        assert len(idle) == pool.minconn and len(set(idle)) == pool.minconn  # 没有重复，也没有多出来的
        # 同时借出的两条是两条不同的连接，并且都是活的
        first, second = pool.getconn(), pool.getconn()
        assert first is not second and not first.closed and not second.closed
        pool.putconn(first)
        pool.putconn(second)
        assert screen.created == screen.minconn

    def test_business_health_check_leaves_bad_screen_connections_for_checkout(self, pools):
        _, screen = pools
        self._kill_one_idle_connection(screen)
        assert database.health_check_pool()["bad"] == 0
        assert screen._pool[-1].dead_on_check and screen._pool[-1].rollbacks == 0

    def test_without_pools_it_reports_not_initialized(self, monkeypatch):
        monkeypatch.setattr(database, "_pg_connection_pool", None)
        monkeypatch.setattr(database, "_screen_connection_pool", None)
        assert database.health_check_pool() == {"initialized": False, "healthy": 0, "bad": 0}
        database._replenish_pool_to_min()  # 没有池时什么都不做

    def test_background_round_does_not_touch_screen_connections(self, pools, monkeypatch):
        """后台检查若在画面空闲连接上回滚，可能撤销已经借走这条连接的信令写入。"""
        business, screen = pools

        class EndHealthCheck(BaseException):
            pass

        monkeypatch.setattr(database, "_pool_health_check_thread", None)
        monkeypatch.setattr(database, "time", SimpleNamespace(sleep=Mock(side_effect=[None, EndHealthCheck])))
        monkeypatch.setattr(database, "threading", SimpleNamespace(
            Thread=lambda *, target, **_: SimpleNamespace(start=target),
        ))
        with pytest.raises(EndHealthCheck):
            database._start_pool_health_check_thread()

        assert all(conn.rollbacks == 1 for conn in business._pool)
        assert all(conn.rollbacks == 0 for conn in screen._pool)

    def test_nothing_is_added_to_a_pool_that_was_just_closed(self, pools):
        """健康检查查到一半时池被 close_postgres_pool 关掉：不往已经关闭的池里补连接（补了的连接没人关）"""
        business, _ = pools
        business.closeall()
        database._replenish_pool_to_min(business)
        assert business.created == business.minconn
