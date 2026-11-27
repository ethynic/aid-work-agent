"""skill 子进程 DB 访问守卫测试（2026-11-26 08e40836 回归防闸门）

背景：08e40836 把 skill 子进程 sitecustomize 的启动期 DB 池初始化改为首个
LLM provider 调用惰性化后，非 LLM 技能脚本（load_api_config.py 等）经
tenant_api_doc 间接读 DB 时连接池未初始化，静默降级为 configured=false，
新会话全部答「接口配置还没做」。单测全 mock DB 测不到该场景，只有
「全新子进程 + 真实 PG」的集成冒烟能暴露。

本文件用全新子进程跑 form-filling-api 的 load_api_config.py，覆盖三类：
1. DB 自定义智能体 + 环境变量齐备 -> configured=true 且 ${APP_ID} 已渲染
   （回归场景：修复前这里返回 configured=false）
2. DB 表缺失 -> reason=db_unavailable（可区分的故障上报，非伪装成 no_doc）
3. 白名单无匹配模板且租户无文档 -> reason=no_doc（合法业务态）

运行：docker compose -f docker-compose.test-db.yml run --rm sql tests/integration/test_skill_subprocess_db_access.py -p no:cacheprovider -q
（harness 提供一次性 aid_test_* 库并注入 DATABASE_URL）
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.db]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "src/skills/form-filling-api-1.0.0/scripts/load_api_config.py"


@pytest.fixture(scope="module")
def pg_dsn():
    """harness（compose sql 服务）注入的一次性 aid_test_* 库 DSN"""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn or "postgresql" not in dsn:
        pytest.skip("需要显式一次性测试库 DATABASE_URL（compose sql 服务提供）")
    conn = psycopg2.connect(dsn)
    conn.close()  # 验证可连接即可；表结构见 module 级 fixture
    return dsn


@pytest.fixture(scope="module")
def seeded_db(pg_dsn):
    """建三张最小表 + 造一个 DB 自定义智能体（skills.allowed 含 form-filling-api）

    列结构对齐 deploy/init-postgres.sql（本测试只依赖 SubagentDefinitionDB /
    TenantAgentInstanceDB / SubagentEnvVarDB 实际 SELECT 的列）。
    """
    conn = psycopg2.connect(pg_dsn)
    conn.autocommit = True
    with conn.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subagent_definitions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                agent_id TEXT UNIQUE NOT NULL,
                name TEXT,
                status TEXT DEFAULT 'active',
                skills JSONB DEFAULT '{}'::jsonb,
                tools JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tenant_agent_instances (
                id SERIAL PRIMARY KEY,
                tenant_id TEXT,
                agent_id TEXT,
                base_agent_id TEXT,
                base_source TEXT,
                display_name TEXT,
                description TEXT,
                extra_prompt TEXT,
                greeting_summary TEXT,
                greeting_prompts JSONB,
                llm_provider JSONB,
                status TEXT DEFAULT 'active',
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS subagent_env_vars (
                id SERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                subagent_name TEXT NOT NULL,
                var_name TEXT NOT NULL,
                var_value TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute(
            "INSERT INTO subagent_definitions (agent_id, name, skills) "
            "VALUES (%s, %s, %s) ON CONFLICT (agent_id) DO NOTHING",
            ("zzz-smoke-db-agent", "冒烟智能体",
             json.dumps({"allowed": ["form-filling-api"]})),
        )
    yield conn
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM subagent_definitions WHERE agent_id = 'zzz-smoke-db-agent'")
    conn.close()


def _run_load_api_config(dsn: str, tenant_id: str, subagent_id: str) -> dict:
    """全新子进程执行 load_api_config.py（模拟 skill_execute 子进程环境）"""
    env = dict(os.environ)
    env.update({
        "DATABASE_URL": dsn,
        "AID_TENANT_ID": tenant_id,
        "AID_SUBAGENT_ID": subagent_id,
        # 隔离：清掉可能残留的请求上下文注入
        "CURRENT_TENANT_ID": "",
        "PYTHONPATH": str(PROJECT_ROOT),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, timeout=60,
        env=env, cwd=str(SCRIPT.parent),
    )
    assert proc.returncode == 0, f"子进程失败: {proc.stderr[-800:]}"
    return json.loads(proc.stdout)


def test_db_defined_agent_loads_template_and_env(seeded_db, pg_dsn):
    """回归场景：DB 自定义智能体 + APP_ID 环境变量齐备 -> configured=true

    08e40836 回归时本场景返回 configured=false（DB 池未初始化 -> 白名单空 ->
    模板兜底失效）。租户存储目录不存在 -> 走技能模板兜底路径。
    """
    tenant_id = f"tenant_smoke{uuid.uuid4().hex[:8]}"
    with seeded_db.cursor() as cursor:
        cursor.execute(
            "INSERT INTO subagent_env_vars (tenant_id, subagent_name, var_name, var_value) "
            "VALUES (%s, 'zzz-smoke-db-agent', 'APP_ID', %s)",
            (tenant_id, "11199"),
        )
    try:
        result = _run_load_api_config(pg_dsn, tenant_id, "zzz-smoke-db-agent")
    finally:
        with seeded_db.cursor() as cursor:
            cursor.execute(
                "DELETE FROM subagent_env_vars WHERE tenant_id = %s", (tenant_id,))
    assert result["success"] is True
    assert result["configured"] is True, f"reason={result.get('reason')} err={result.get('error')}"
    # ${APP_ID} 已渲染为环境变量实际值
    assert "erp11199.aidingyi.cn" in result["content"]
    assert "${APP_ID}" not in result["content"]


def test_db_unavailable_is_distinguishable(pg_dsn):
    """DB 表缺失（连接池正常但查询失败）-> reason=db_unavailable，不伪装成 no_doc

    非内置智能体名（DB 读失败 -> 内置 registry 也查不到）触发降级路径。
    """
    # 用独立的一次性库：表不存在 = DB 查询全部失败
    admin = psycopg2.connect(pg_dsn)
    admin.autocommit = True
    empty_db = f"aid_test_smoke_{uuid.uuid4().hex[:8]}"
    with admin.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE {empty_db}")
    try:
        # 从 harness 注入的 DATABASE_URL 解析连接参数（trust 认证，无密码）
        parsed = urlparse(os.environ["DATABASE_URL"])
        port = parsed.port or 5432
        empty_dsn = f"postgresql://{parsed.username}@{parsed.hostname}:{port}/{empty_db}"
        result = _run_load_api_config(empty_dsn, "tenant_smoke_none", "zzz-smoke-db-agent")
    finally:
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {empty_db}")
        admin.close()
    assert result["configured"] is False
    assert result.get("reason") == "db_unavailable", \
        f"DB 故障被伪装成了其他原因: {result.get('reason')}"
    assert "数据库" in result["content"]


def test_no_doc_when_no_template_and_no_tenant_doc(seeded_db, pg_dsn):
    """白名单技能无匹配模板（sms-verification）且租户无文档 -> reason=no_doc

    合法业务态：不是故障，智能体应引导管理员上传文档/配置环境变量。
    """
    with seeded_db.cursor() as cursor:
        cursor.execute(
            "INSERT INTO subagent_definitions (agent_id, name, skills) "
            "VALUES (%s, %s, %s) ON CONFLICT (agent_id) DO UPDATE SET skills = EXCLUDED.skills",
            ("zzz-smoke-no-template", "无模板智能体",
             json.dumps({"allowed": ["sms-verification"]})),
        )
    try:
        result = _run_load_api_config(pg_dsn, f"tenant_smoke{uuid.uuid4().hex[:8]}", "zzz-smoke-no-template")
    finally:
        with seeded_db.cursor() as cursor:
            cursor.execute(
                "DELETE FROM subagent_definitions WHERE agent_id = 'zzz-smoke-no-template'")
    assert result["configured"] is False
    assert result.get("reason") == "no_doc", \
        f"无 DB 故障场景不应上报 db_unavailable: {result.get('reason')}"
