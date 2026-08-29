"""scheduled_tasks / scheduled_task_logs 租户隔离集成测试（真实 PG）

定时任务租户与用户隔离：
- db_update.sql 增量 DDL 幂等块（加列 / 按 users 回填 / 租户索引）在真实库可执行
- list_by_user 租户过滤：跨租户任务与 tenant_id='' 遗留行在租户视图不可见（fail-closed）
- get_by_id 租户过滤：跨租户任务查不到
- API 详情/列表路由：属主匹配但租户不匹配 → 统一返回任务不存在（不泄露存在性）
- 回填 UPDATE 语义：'' 行按 users.tenant_id 回填，日志按任务链回填

DB 不可达时整模块 pytest.skip。
"""

import uuid

import pytest

pytestmark = pytest.mark.integration

from src.saas.context import set_tenant_context, clear_tenant_context

# 与 deploy/db_update.sql「定时任务租户隔离」增量节逐句一致（幂等）
_DDL_BLOCK = """
ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';
ALTER TABLE scheduled_task_logs ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_tenant ON scheduled_tasks(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_scheduled_task_logs_tenant ON scheduled_task_logs(tenant_id, created_at DESC);
"""

_BACKFILL_TASKS = """
UPDATE scheduled_tasks st SET tenant_id = u.tenant_id
  FROM users u WHERE st.user_id = u.user_id AND u.tenant_id IS NOT NULL AND st.tenant_id = '';
"""

_BACKFILL_LOGS = """
UPDATE scheduled_task_logs sl SET tenant_id = st.tenant_id
  FROM scheduled_tasks st WHERE sl.task_id = st.task_id AND sl.tenant_id = '';
"""


@pytest.fixture(scope="module", autouse=True)
def _ensure_columns(init_db_pool):
    """模块级幂等 DDL（测试库可能未跑过服务启动迁移，确保具备 tenant_id 列）。

    DB 不可达由 tests/integration/conftest.py 的 init_db_pool session fixture
    统一 skip，本 fixture 只在 DB 可用时执行。
    """
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(_DDL_BLOCK)
        conn.commit()


@pytest.fixture
def env():
    """双租户 + 双用户 + 四条任务行（含一条 '' 遗留行与一条「属主 uA 但租户 B」行）"""
    from src.db.database import get_db_connection
    from src.saas.db.tenant_db import TenantDB
    from src.db.models import UserDB

    code = uuid.uuid4().hex[:6].upper()
    tenant_a = TenantDB.create(
        company_name=f"定时任务测试租户-A-{code}", tenant_code=f"T{code}",
        contact_name="测试联系人", contact_phone="13800000000")
    tenant_b = TenantDB.create(
        company_name=f"定时任务测试租户-B-{code}", tenant_code=f"U{code}",
        contact_name="测试联系人", contact_phone="13800000001")
    if not tenant_a or not tenant_b:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_a_id, tenant_b_id = tenant_a["tenant_id"], tenant_b["tenant_id"]

    user_a = UserDB.create(username=f"st_user_a_{code}", tenant_id=tenant_a_id)
    user_b = UserDB.create(username=f"st_user_b_{code}", tenant_id=tenant_b_id)
    user_a_id, user_b_id = user_a["user_id"], user_b["user_id"]

    def _insert_task(task_id, tenant_id, user_id):
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO scheduled_tasks
                    (task_id, tenant_id, user_id, name, description, task_prompt,
                     schedule_type, cron_expression, status)
                VALUES (%s, %s, %s, %s, '', '测试任务', 'daily', '0 9 * * *', 'active')
                ON CONFLICT (task_id) DO NOTHING
            """, (task_id, tenant_id, user_id, f"任务{task_id}"))
            conn.commit()

    task_a = f"sched_it_{uuid.uuid4().hex[:12]}"       # 租户 A 任务
    task_b = f"sched_it_{uuid.uuid4().hex[:12]}"       # 租户 B 任务
    task_legacy = f"sched_it_{uuid.uuid4().hex[:12]}"  # '' 遗留行（属主 uA）
    task_a_in_b = f"sched_it_{uuid.uuid4().hex[:12]}"  # 属主 uA 但挂在租户 B（越权检测用）
    _insert_task(task_a, tenant_a_id, user_a_id)
    _insert_task(task_b, tenant_b_id, user_b_id)
    _insert_task(task_legacy, "", user_a_id)
    _insert_task(task_a_in_b, tenant_b_id, user_a_id)

    yield {
        "tenant_a": tenant_a_id, "tenant_b": tenant_b_id,
        "user_a": user_a_id, "user_b": user_b_id,
        "task_a": task_a, "task_b": task_b,
        "task_legacy": task_legacy, "task_a_in_b": task_a_in_b,
    }

    # 清理（'' 遗留行不在全局兜底清理范围，必须显式删）
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM scheduled_tasks WHERE task_id = ANY(%s)",
                ([task_a, task_b, task_legacy, task_a_in_b],))
            cur.execute(
                "DELETE FROM users WHERE user_id = ANY(%s)", ([user_a_id, user_b_id],))
            conn.commit()
    except Exception:
        pass
    for tid in (tenant_a_id, tenant_b_id):
        TenantDB.delete(tid)
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM tenants WHERE tenant_id = ANY(%s)",
                ([tenant_a_id, tenant_b_id],))
            conn.commit()
    except Exception:
        pass


class TestScheduledTasksTenant:
    def test_ddl_columns_exist(self, env):
        """DDL 幂等块执行后两表均有 tenant_id 列"""
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT table_name FROM information_schema.columns
                WHERE column_name = 'tenant_id'
                  AND table_name IN ('scheduled_tasks', 'scheduled_task_logs')
            """)
            tables = {r["table_name"] for r in cur.fetchall()}
        assert {"scheduled_tasks", "scheduled_task_logs"} <= tables

    def test_list_by_user_tenant_filter_fail_closed(self, env):
        """租户视图：只见本租户任务，跨租户与 '' 遗留行均不可见"""
        from src.scheduler.db import ScheduledTaskDB
        rows = ScheduledTaskDB.list_by_user(env["user_a"], tenant_id=env["tenant_a"])
        ids = {r["task_id"] for r in rows}
        assert env["task_a"] in ids
        assert env["task_b"] not in ids          # 跨租户不可见
        assert env["task_legacy"] not in ids     # '' 遗留行不可见（fail-closed）
        assert env["task_a_in_b"] not in ids     # 属主是 uA 但租户 B 的行不可见

    def test_list_by_user_other_tenant(self, env):
        """租户 B 视图只见 B 的任务"""
        from src.scheduler.db import ScheduledTaskDB
        rows = ScheduledTaskDB.list_by_user(env["user_b"], tenant_id=env["tenant_b"])
        ids = {r["task_id"] for r in rows}
        assert env["task_b"] in ids
        assert env["task_a"] not in ids

    def test_list_by_user_without_tenant_keeps_legacy_visible(self, env):
        """不传租户（公共用户/内部调用）：'' 遗留行仍可见，行为不回退"""
        from src.scheduler.db import ScheduledTaskDB
        rows = ScheduledTaskDB.list_by_user(env["user_a"])
        ids = {r["task_id"] for r in rows}
        assert env["task_a"] in ids
        assert env["task_legacy"] in ids

    def test_get_by_id_tenant_filter(self, env):
        """get_by_id 带租户条件：本租户可见，跨租户 None"""
        from src.scheduler.db import ScheduledTaskDB
        assert ScheduledTaskDB.get_by_id(env["task_a"], tenant_id=env["tenant_a"]) is not None
        assert ScheduledTaskDB.get_by_id(env["task_a"], tenant_id=env["tenant_b"]) is None
        # '' 遗留行在租户视图不可见
        assert ScheduledTaskDB.get_by_id(env["task_legacy"], tenant_id=env["tenant_a"]) is None
        # 不传租户保持原行为
        assert ScheduledTaskDB.get_by_id(env["task_legacy"]) is not None

    def test_update_status_requires_paired_identity(self, env):
        """只传 tenant 不传 user（或反之）视为调用方身份不完整，fail-closed 拒绝"""
        from src.db.database import get_db_connection
        from src.scheduler.db import ScheduledTaskDB
        try:
            assert ScheduledTaskDB.update_status(
                env["task_a"], "paused", tenant_id=env["tenant_a"]
            ) is False
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT status FROM scheduled_tasks WHERE task_id = %s",
                    (env["task_a"],))
                assert cur.fetchone()["status"] == "active"  # 未被改动
        finally:
            # 防御性复位（本用例不应产生变更）
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE scheduled_tasks SET status = 'active' WHERE task_id = %s",
                    (env["task_a"],))
                conn.commit()

    async def test_api_detail_tenant_mismatch_not_found(self, env):
        """API 详情：属主匹配但租户不匹配（uA 的任务挂在租户 B）→ 统一返回任务不存在"""
        from unittest.mock import patch, MagicMock
        from src.api.scheduled_task import get_task

        request = MagicMock()
        try:
            with patch("src.api.scheduled_task.get_current_user",
                       return_value={"user_id": env["user_a"]}), \
                 patch("src.api.scheduled_task.ScheduledTaskLogDB.get_stats", return_value={}):
                set_tenant_context(env["tenant_a"], env["user_a"])
                # 先证明数据在（不加租户过滤可查到）
                from src.scheduler.db import ScheduledTaskDB
                assert ScheduledTaskDB.get_by_id(env["task_a_in_b"]) is not None
                result = await get_task(request, env["task_a_in_b"])
            assert result == {"success": False, "error": "任务不存在"}
        finally:
            clear_tenant_context()

    async def test_api_list_route_filters_by_context_tenant(self, env):
        """API 列表路由经 ContextVar 租户过滤，'' 遗留行不可见"""
        from unittest.mock import patch, MagicMock
        from src.api.scheduled_task import list_tasks

        request = MagicMock()
        try:
            with patch("src.api.scheduled_task.get_current_user",
                       return_value={"user_id": env["user_a"]}), \
                 patch("src.api.scheduled_task.ScheduledTaskLogDB.get_stats", return_value={}):
                set_tenant_context(env["tenant_a"], env["user_a"])
                result = await list_tasks(request)
            ids = {t["task_id"] for t in result["data"]["tasks"]}
            assert env["task_a"] in ids
            assert env["task_legacy"] not in ids
            assert env["task_a_in_b"] not in ids
        finally:
            clear_tenant_context()

    async def test_api_identity_missing_tenant_in_saas_fail_closed(self, env):
        """SaaS 部署下租户来源缺失（ContextVar 与用户行均无）→ 403 fail-closed"""
        from fastapi import HTTPException
        from unittest.mock import patch, MagicMock
        from src.api.scheduled_task import list_tasks
        from src.config.settings import settings as app_settings

        request = MagicMock()
        # 认证用户行不含 tenant_id 字段，且请求上下文未注入租户
        try:
            with patch("src.api.scheduled_task.get_current_user",
                       return_value={"user_id": env["user_a"]}), \
                 patch.object(app_settings.saas, "enabled", True):
                set_tenant_context(None, env["user_a"])
                with pytest.raises(HTTPException) as exc_info:
                    await list_tasks(request)
            assert exc_info.value.status_code == 403
        finally:
            clear_tenant_context()

    def test_backfill_updates_legacy_row_from_user_tenant(self, env):
        """回填 UPDATE 语义：'' 行按属主 users.tenant_id 回填，日志按任务链回填"""
        from src.db.database import get_db_connection
        fresh = f"sched_it_{uuid.uuid4().hex[:12]}"
        fresh_log = f"slog_it_{uuid.uuid4().hex[:12]}"
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO scheduled_tasks
                        (task_id, tenant_id, user_id, name, description, task_prompt,
                         schedule_type, cron_expression, status)
                    VALUES (%s, '', %s, '待回填', '', 'p', 'daily', '0 9 * * *', 'active')
                """, (fresh, env["user_b"]))
                cur.execute("""
                    INSERT INTO scheduled_task_logs
                        (log_id, tenant_id, task_id, user_id, status, trigger_type, started_at)
                    VALUES (%s, '', %s, %s, 'success', 'scheduled', NOW())
                """, (fresh_log, fresh, env["user_b"]))
                conn.commit()

                # 执行 db_update.sql 同款回填语句（任务按 users，日志按任务链）
                cur.execute(_BACKFILL_TASKS)
                cur.execute(_BACKFILL_LOGS)
                conn.commit()

                cur.execute("SELECT tenant_id FROM scheduled_tasks WHERE task_id = %s", (fresh,))
                assert cur.fetchone()["tenant_id"] == env["tenant_b"]
                cur.execute("SELECT tenant_id FROM scheduled_task_logs WHERE log_id = %s", (fresh_log,))
                assert cur.fetchone()["tenant_id"] == env["tenant_b"]
        finally:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM scheduled_task_logs WHERE log_id = %s", (fresh_log,))
                cur.execute("DELETE FROM scheduled_tasks WHERE task_id = %s", (fresh,))
                conn.commit()

    def test_log_create_carries_tenant(self, env):
        """ScheduledTaskLogDB.create 落库 tenant_id 且 list_by_task 租户过滤生效"""
        from src.db.database import get_db_connection
        from src.scheduler.db import ScheduledTaskLogDB

        log = ScheduledTaskLogDB.create(
            task_id=env["task_a"], user_id=env["user_a"],
            status="success", tenant_id=env["tenant_a"])
        assert log is not None and log["tenant_id"] == env["tenant_a"]

        rows_own = ScheduledTaskLogDB.list_by_task(env["task_a"], tenant_id=env["tenant_a"])
        assert any(r["log_id"] == log["log_id"] for r in rows_own)
        rows_other = ScheduledTaskLogDB.list_by_task(env["task_a"], tenant_id=env["tenant_b"])
        assert not any(r["log_id"] == log["log_id"] for r in rows_other)

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM scheduled_task_logs WHERE log_id = %s", (log["log_id"],))
            conn.commit()
