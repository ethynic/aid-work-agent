"""weixin_marketing 单元测试 fixtures（真实 DB 模式，照 desktop_automation conftest）

每用例独立租户 + 测后清理 weixin 业务表与 desktop_automation/local_tools 底座表
（发布链路会写 subjects/schedules/runs/invocations/permits）；租户用随机字符串，
本表族 SQL 不做 tenants join。
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv

# 必须在任何 src 导入之前加载 .env（DATABASE_URL）
project_root = Path(__file__).parent.parent.parent.parent
load_dotenv(project_root / ".env")
db_url = os.getenv("DATABASE_URL", "")
if db_url:
    os.environ["DATABASE_URL"] = db_url
    os.environ.setdefault("DB_POOL_MIN", "2")
    os.environ.setdefault("DB_POOL_MAX", "10")
# P4-B webhook 密钥加密（secret_crypto 主密钥；沿 wecom_personal_rpa 测试惯例）
os.environ.setdefault("RPA_SECRET_KEY", "test-wxm-event-source-key-32bytes")

# weixin 业务表（audit 追加行也随租户清理）+ 底座表（复用 desktop_automation conftest 顺序）
WEIXIN_TABLES = (
    "bs_weixin_marketing_audit_events",
    "bs_weixin_marketing_content_blocks",
    "bs_weixin_marketing_revisions",
    "bs_weixin_marketing_automations",
    "bs_weixin_marketing_group_bindings",
    "bs_weixin_marketing_account_bindings",
    "bs_weixin_marketing_assets",
    # P4-B 事件闭环表（example_orders/payloads/keys 按 tenant_id 直清；
    # nonces 无 tenant 列，按本租户 source 子查询清，且须先于 event_sources 删除）
    "weixin_marketing_example_orders",
    "weixin_marketing_event_payloads",
    "weixin_marketing_event_source_keys",
)


def _cleanup_webhook_nonces(tenant_id: str) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM weixin_marketing_webhook_nonces
            WHERE source_id IN (
                SELECT id FROM desktop_automation_event_sources WHERE tenant_id = %s
            )
            """,
            (tenant_id,),
        )
        conn.commit()

from tests.unit.desktop_automation.conftest import (  # noqa: E402
    cleanup_tenant as cleanup_da_tables,
)


def cleanup_weixin_tenant(tenant_id: str) -> None:
    from src.db.database import get_db_connection

    failed, residual = [], []
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            for table in WEIXIN_TABLES:
                try:
                    cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
                except Exception as e:  # noqa: BLE001 单表失败（如未建）回滚后继续下一张
                    conn.rollback()
                    failed.append((table, str(e)))
            conn.commit()
            for table in WEIXIN_TABLES:
                try:
                    cur.execute(
                        f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = %s", (tenant_id,)
                    )
                    if cur.fetchone()["c"]:
                        residual.append(table)
                except Exception as e:  # noqa: BLE001
                    conn.rollback()
                    failed.append((table, str(e)))
    except Exception as e:  # noqa: BLE001
        failed.append(("<connection>", str(e)))
    if failed or residual:
        import logging

        logging.getLogger(__name__).warning(
            "weixin_marketing 测试租户清理异常 tenant=%s failed=%s residual=%s",
            tenant_id, failed, residual,
        )


def weixin_tables_ready() -> bool:
    try:
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='bs_weixin_marketing_automations'"
            )
            return cur.fetchone() is not None
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def _init_db_pool():
    from src.db.database import (
        close_postgres_pool,
        get_postgres_pool,
        init_postgres_pool,
    )

    if get_postgres_pool() is not None:
        yield
        return
    if not db_url or "postgresql" not in db_url:
        pytest.skip("weixin_marketing 单测需要 PostgreSQL DATABASE_URL")
    try:
        init_postgres_pool()
    except Exception as e:
        pytest.skip(f"无法连接 PostgreSQL: {e}")
    if not weixin_tables_ready():
        close_postgres_pool()
        pytest.skip("bs_weixin_marketing 表未初始化（先运行 init_database）")
    # P4-B 模块级配套表（keys/nonces/payloads/example_orders）——幂等建齐，
    # 租户清理 DELETE 不因表缺失报错
    from src.weixin_marketing import event_sources as wxm_sources
    from src.weixin_marketing import internal_event_example as wxm_example

    wxm_sources.ensure_event_source_tables()
    wxm_example.ensure_example_tables()
    yield
    close_postgres_pool()


@pytest.fixture()
def tenant_id():
    """每用例独立租户：先清底座表（依赖序），再清 weixin 业务表"""
    tid = f"wxm_test_{uuid.uuid4().hex[:12]}"
    yield tid
    try:
        _cleanup_webhook_nonces(tid)
    except Exception:  # noqa: BLE001 表未建等场景由后续清理告警兜底
        pass
    cleanup_da_tables(tid)
    cleanup_weixin_tenant(tid)


@pytest.fixture()
def wx_config():
    """测试用模块配置（enabled=true；时间/事件触发开启，与生产 yaml 门控解耦）"""
    from dataclasses import replace

    from src.weixin_marketing.config import WeixinQuotaConfig, get_weixin_marketing_config

    base = get_weixin_marketing_config()
    return replace(
        base,
        enabled=True,
        time_triggers_enabled=True,
        event_triggers_enabled=True,
        images_enabled=False,
        evidence_real_mode=False,
        max_blocks=20,
        min_interval_seconds=300,
        quotas=WeixinQuotaConfig(
            window_seconds=3600, tenant_limit=100, task_limit=30,
            target_limit=10, account_limit=60,
        ),
    )


@pytest.fixture()
def adapter(wx_config):
    """注册受信 weixin 适配器（每用例独立实例；测后注销避免污染其他套件）"""
    from src.desktop_automation.adapters import TrustedAdapterRegistry
    from src.weixin_marketing.adapters import WeixinFixedContentAdapter

    instance = WeixinFixedContentAdapter(config=wx_config)
    TrustedAdapterRegistry.register(instance)
    yield instance
    TrustedAdapterRegistry.unregister(instance.scenario_key)


@pytest.fixture()
def service():
    from src.weixin_marketing.service import WeixinMarketingService

    return WeixinMarketingService()


@pytest.fixture()
def bindings(tenant_id):
    """建一对 account/group 绑定（complete 状态），返回 (account_id, group_id)"""
    from src.db.database import get_db_connection

    account_id = str(uuid.uuid4())
    group_id = str(uuid.uuid4())
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO bs_weixin_marketing_account_bindings
                (id, tenant_id, user_id, device_id, account_anchor_ref, session_epoch, status)
            VALUES (%s, %s, 'owner-1', %s, 'anchor-1', 3, 'active')
            """,
            (account_id, tenant_id, str(uuid.uuid4())),
        )
        cur.execute(
            """
            INSERT INTO bs_weixin_marketing_group_bindings
                (id, tenant_id, user_id, device_id, account_binding_id, label,
                 identity_evidence_ref, identity_version, state, verified_at)
            VALUES (%s, %s, 'owner-1', %s, %s, '测试群', 'ev-1', '7', 'complete', NOW())
            """,
            (group_id, tenant_id, str(uuid.uuid4()), account_id),
        )
        conn.commit()
    return account_id, group_id


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_create_payload(
    group_binding_id: str,
    *,
    trigger=None,
    blocks=None,
    name: str = "测试自动化",
):
    from src.weixin_marketing.models import AutomationCreateInput

    if trigger is None:
        trigger = {
            "type": "once",
            "run_at": (utcnow() + timedelta(hours=1)).isoformat(),
            "timezone": "UTC",
        }
    if blocks is None:
        blocks = [
            {"type": "text", "text_content": "第一条内容"},
            {"type": "link", "url": "https://example.com/a"},
        ]
    return AutomationCreateInput(
        name=name, trigger=trigger, blocks=blocks, group_binding_id=group_binding_id,
    )


def create_and_publish(
    service,
    tenant_id,
    group_binding_id,
    *,
    trigger=None,
    blocks=None,
    user_id: str = "owner-1",
):
    """创建草稿 → 发布，返回 (automation_id, revision_id, publish 结果)"""
    from src.weixin_marketing.models import PublishInput

    detail = service.create_automation(
        tenant_id, user_id, make_create_payload(group_binding_id, trigger=trigger, blocks=blocks)
    )
    automation = detail["automation"]
    result = service.publish(
        tenant_id, str(automation["id"]), user_id,
        PublishInput(expected_version=automation["version"]),
    )
    return str(automation["id"]), result["revision_id"], result


def manual_run_pending(service, tenant_id, automation_id, *, user_id="owner-1", request_id=None):
    """手动触发并返回 (occurrence_id, run_id)"""
    result = service.manual_run(
        tenant_id, automation_id, user_id,
        request_id=request_id or f"req-{uuid.uuid4().hex[:8]}", now=utcnow(),
    )
    return result["occurrence_id"], result["run_id"]
