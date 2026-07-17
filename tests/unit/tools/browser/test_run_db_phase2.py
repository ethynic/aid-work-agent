import inspect

from src.tools.browser.run_db import BrowserRunDB


def test_browser_tables_are_in_both_idempotent_deploy_scripts():
    for path in ("deploy/init-postgres.sql", "deploy/db_update.sql"):
        sql = open(path, encoding="utf-8").read().lower()
        assert "create table if not exists bs_browser_runs" in sql
        assert "create table if not exists bs_browser_assistance_requests" in sql
        assert "run_id text not null unique" in sql
        assert "assistance_id text not null unique" in sql
        assert "browser_device" not in sql


def test_browser_run_db_select_and_update_are_parameterized_and_tenant_scoped():
    source = inspect.getsource(BrowserRunDB)
    assert "f\"SELECT" not in source
    assert "f\"UPDATE" not in source
    assert "WHERE tenant_id=%s AND run_id=%s" in source
    assert "WHERE tenant_id=%s AND assistance_id=%s" in source
    assert "WHERE tenant_id=%s AND assistance_id=%s AND state=%s" in source
    assert "asyncio.to_thread" in source
