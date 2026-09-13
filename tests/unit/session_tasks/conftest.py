"""session_tasks 单元测试 fixtures（真实 DB 模式，照 weixin_marketing conftest）。

每用例独立随机租户 + 测后按依赖序物理清理 session_task_* 表族、绑定业务表、
subjects 与测试设备行；verified 绑定 fixture 仅存在于测试库（设计 §13.3：
fake verified fixture 不得连接真机 Runtime）。服务层 tenant_allowed 门控默认
monkeypatch 为放行（生产 yaml 默认 false，测试不依赖配置文件状态）。
"""

import os
import uuid
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
# 受控文本加密（secret_crypto 主密钥；沿项目测试惯例）
os.environ.setdefault("RPA_SECRET_KEY", "test-session-tasks-master-key-32bytes")

# 清理顺序：子表先于父表；devices 最后（绑定/任务引用它）
SESSION_TASK_TABLES = (
    "session_tasks_idempotency_keys",
    "session_task_cost_reservations",
    "session_task_execution_links",
    "session_task_decisions",
    "session_task_events",
    "session_task_messages",
    "session_task_batches",
    "session_task_confirmations",
    "session_task_specs",
    "session_task_assignments",
    "session_task_texts",
    "session_tasks",
    "bs_weixin_conversation_bindings",
)


def _cleanup_tenant(tenant_id: str) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        for table in SESSION_TASK_TABLES:
            try:
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
            except Exception:  # noqa: BLE001 单表失败不阻断其余清理
                conn.rollback()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM desktop_automation_subjects WHERE tenant_id = %s AND scenario_key = 'weixin.conversation.v1'",
                (tenant_id,),
            )
            cursor.execute("DELETE FROM local_tool_devices WHERE tenant_id = %s", (tenant_id,))
        except Exception:  # noqa: BLE001
            conn.rollback()
        conn.commit()


def _tables_ready() -> bool:
    try:
        from src.session_tasks.init_tables import session_tasks_tables_ready

        return session_tasks_tables_ready()
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session", autouse=True)
def _init_db_pool():
    if not db_url:
        pytest.skip("未配置 DATABASE_URL，跳过 session_tasks 测试")
    from src.db.database import init_postgres_pool, get_postgres_pool

    if get_postgres_pool() is None:
        if "postgresql" not in (db_url or ""):
            pytest.skip("session_tasks 单测需要 PostgreSQL DATABASE_URL")
        try:
            init_postgres_pool()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"无法连接 PostgreSQL: {exc}")
    # 模块建表幂等（同时覆盖"迁移重复运行"验证的一半；另一半在 test_migrations）
    from src.db.database import get_db_connection

    from src.session_tasks.init_tables import init_session_task_tables
    from src.weixin_conversation.init_tables import init_weixin_conversation_tables

    with get_db_connection() as conn:
        init_session_task_tables(conn)
        init_weixin_conversation_tables(conn)
    if not _tables_ready():
        pytest.skip("session_tasks 表未建成（先运行 init_database）")
    yield


@pytest.fixture()
def tenant_id():
    value = f"st_test_{uuid.uuid4().hex[:12]}"
    yield value
    _cleanup_tenant(value)


@pytest.fixture(autouse=True)
def _gate_open(monkeypatch):
    """测试默认放开 enabled/allowlist 热读门控（生产默认关闭，不依赖 yaml 状态）。"""
    import src.session_tasks.service as service_mod
    import src.weixin_conversation.config as wx_config

    monkeypatch.setattr(service_mod, "tenant_allowed", lambda tenant: True)
    monkeypatch.setattr(wx_config, "scenario_enabled", lambda tenant: True)


@pytest.fixture()
def pending_binding(tenant_id, device_row, monkeypatch):
    """pending 会话绑定 fixture：可建草稿、拒绝生产发布（设计 §13.3）。"""
    import src.weixin_conversation.config as wx_config
    from src.weixin_conversation.bindings import create_binding

    monkeypatch.setattr(wx_config, "scenario_enabled", lambda tenant: True)
    account_binding_id = str(uuid.uuid4())
    binding = create_binding(
        tenant_id, "user-1", str(device_row["id"]), account_binding_id, "group", label="待验证会话"
    )
    return {
        "conversation_binding_id": binding["conversation_binding_id"],
        "account_binding_id": account_binding_id,
        "device_id": str(device_row["id"]),
    }


@pytest.fixture()
def device_row(tenant_id):
    """测试设备：能力满足 session_task_v1 + session_observer_v1。"""
    from src.local_tools.repository import create_device

    device = create_device(
        tenant_id,
        "user-1",
        token_hash=uuid.uuid4().hex,
        name="st-test-device",
        capabilities={
            "providers": ["weixin"],
            "capabilities": ["session_task_v1", "session_observer_v1", "weixin_message_send_v2"],
        },
    )
    return device


@pytest.fixture()
def verified_binding(tenant_id, device_row, monkeypatch):
    """verified 会话绑定 fixture（仅测试库；真机 verified 须 Provider 证据）。"""
    from src.db.database import get_db_connection

    import src.weixin_conversation.config as wx_config
    from src.weixin_conversation.bindings import create_binding

    monkeypatch.setattr(wx_config, "scenario_enabled", lambda tenant: True)

    account_binding_id = str(uuid.uuid4())
    binding = create_binding(
        tenant_id, "user-1", str(device_row["id"]), account_binding_id, "direct", label="测试会话"
    )
    from datetime import datetime, timedelta, timezone as _tzmod

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE bs_weixin_conversation_bindings
            SET verification_status='verified', verifier_version='test-fixture',
                identity_version=1, verified_at=CURRENT_TIMESTAMP,
                expires_at=%s
            WHERE tenant_id=%s AND id=%s
            """,
            (datetime.now(_tzmod.utc) + timedelta(days=30), tenant_id, binding["conversation_binding_id"]),
        )
        conn.commit()
    return {
        "conversation_binding_id": binding["conversation_binding_id"],
        "account_binding_id": account_binding_id,
        "device_id": str(device_row["id"]),
    }


def build_spec(mode: str = "judged", *, opening: bool = False) -> dict:
    """合法发布单（三种 completion_rule 共用构造器）。"""
    if mode == "rounds":
        completion = {"mode": "rounds", "rounds_target": 5}
    elif mode == "peer_confirmed":
        completion = {
            "mode": "peer_confirmed",
            "fields": [
                {"key": "willing", "question": "是否愿意沟通", "allowed_values": ["yes", "no"], "accepted_values": ["yes"]},
            ],
            "require_all": True,
        }
    else:
        completion = {"mode": "judged", "criteria": ["对方明确同意沟通", "对方明确确认具体时间"]}
    return {
        "goal": "确认对方是否愿意在周五下午沟通，并取得明确时间",
        "completion_rule": completion,
        "reply_policy": {
            "style": "简洁礼貌",
            "allowed_facts": ["可选时段为周五14:00或16:00"],
            "forbidden_commitments": ["价格承诺"],
        },
        "limits": {
            "max_replies": 10,
            "max_decisions": 20,
            "max_cost_units": 100,
            "expires_at": "2099-01-01T00:00:00Z",
            "peer_wait_timeout_seconds": 86400,
        },
        "opening_text": "您好，想和您确认周五沟通时间" if opening else None,
    }


def build_draft_payload(binding: dict, spec: dict = None) -> dict:
    return {
        "scenario_key": "weixin.conversation.v1",
        "device_id": binding["device_id"],
        "account_binding_id": binding["account_binding_id"],
        "conversation_binding_id": binding["conversation_binding_id"],
        "spec": spec or build_spec(),
    }


def publish_task_helper(tenant_id: str, binding: dict, spec: dict = None) -> dict:
    """create → confirm → publish 全链路（返回 create/publish 结果并集）。"""
    from src.session_tasks import service
    from src.session_tasks.models import TaskDraftCreatePayload

    created = service.create_draft(
        tenant_id, "user-1", TaskDraftCreatePayload.model_validate(build_draft_payload(binding, spec))
    )
    confirmation = service.issue_publish_confirmation(
        tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"]
    )
    published = service.publish_task(
        tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
        uuid.UUID(confirmation["confirmation_id"]),
    )
    published["task_id"] = created["task_id"]
    return published


def expire_assignment_lease(tenant_id: str, assignment_id: str) -> None:
    """测试辅助：把租约改到过去，模拟过期。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE session_task_assignments SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '5 seconds' WHERE tenant_id=%s AND id=%s",
            (tenant_id, assignment_id),
        )
        conn.commit()


def make_verified_binding(tenant_id: str, device_id: str, conv_type: str = "direct") -> dict:
    """测试辅助：额外创建一个 verified 绑定（同租户同设备不同会话；仅测试库）。"""
    from datetime import datetime, timedelta, timezone

    from src.db.database import get_db_connection
    from src.weixin_conversation.bindings import create_binding

    account_binding_id = str(uuid.uuid4())
    binding = create_binding(tenant_id, "user-1", device_id, account_binding_id, conv_type, label="多会话测试")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE bs_weixin_conversation_bindings
            SET verification_status='verified', verifier_version='test-fixture',
                identity_version=1, verified_at=CURRENT_TIMESTAMP, expires_at=%s
            WHERE tenant_id=%s AND id=%s
            """,
            (datetime.now(timezone.utc) + timedelta(days=30), tenant_id, binding["conversation_binding_id"]),
        )
        conn.commit()
    return {
        "conversation_binding_id": binding["conversation_binding_id"],
        "account_binding_id": account_binding_id,
        "device_id": device_id,
    }
