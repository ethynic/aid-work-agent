"""boss_conversation 单元测试 fixtures（真实 DB 模式，照 session_tasks conftest 范式）。

每用例独立随机租户 + 测后按依赖序清理 session_task_* 表族、bs_boss_* 表、recruiting
种子行与测试设备行；verified 绑定 fixture 仅存在于测试库（设计 §13.3：fake verified
fixture 不得连接真机 Runtime）。boss_conversation.enabled 生产默认 false，测试经
monkeypatch 热读门控自行启用（config.yaml 不改，capability 默认关闭）。
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
os.environ.setdefault("RPA_SECRET_KEY", "test-boss-conversation-master-key-32bytes")

from tests.unit.session_tasks.test_c3_decisions import _cfg  # noqa: E402

# 清理顺序：子表先于父表；devices 最后（DA 底座表族随租户一并清理）
BOSS_TABLES = (
    "chat_records",
    "desktop_automation_attempts",
    "desktop_automation_evidence",
    "desktop_automation_deliveries",
    "desktop_automation_runs",
    "desktop_automation_occurrences",
    "desktop_automation_outbox",
    "desktop_automation_events",
    "desktop_automation_audit_events",
    "desktop_automation_quota_buckets",
    "local_tool_operation_permits",
    "local_tool_events",
    "local_tool_invocations",
    "bs_boss_comm_log_projection_queue",
    "bs_boss_rate_settlement_anomalies",
    "bs_boss_conversation_rate_slots",
    "bs_boss_reply_script_versions",
    "session_task_decision_attempts",
    "session_tasks_idempotency_keys",
    "session_task_control_requests",
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
    "bs_recruiting_operator_resume_comm_logs",
    "bs_recruiting_operator_resumes",
    "bs_boss_conversation_bindings",
)

_DA_TABLES = (
    "desktop_automation_attempts",
    "desktop_automation_evidence",
    "desktop_automation_deliveries",
    "desktop_automation_runs",
    "desktop_automation_occurrences",
    "desktop_automation_outbox",
    "desktop_automation_events",
    "desktop_automation_audit_events",
    "desktop_automation_quota_buckets",
    "local_tool_operation_permits",
    "local_tool_events",
    "local_tool_invocations",
)


def _cleanup_tenant(tenant_id: str) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        for table in BOSS_TABLES:
            try:
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
            except Exception:  # noqa: BLE001 单表失败不阻断其余清理
                conn.rollback()
        try:
            cursor = conn.cursor()
            for scenario in ("boss.chat_reply.v1", "weixin.conversation.v1"):
                cursor.execute(
                    "DELETE FROM desktop_automation_subjects WHERE tenant_id = %s AND scenario_key = %s",
                    (tenant_id, scenario),
                )
            cursor.execute("DELETE FROM local_tool_devices WHERE tenant_id = %s", (tenant_id,))
        except Exception:  # noqa: BLE001
            conn.rollback()
        conn.commit()


@pytest.fixture(scope="session", autouse=True)
def _init_db_pool():
    if not db_url:
        pytest.skip("未配置 DATABASE_URL，跳过 boss_conversation 测试")
    from src.db.database import init_postgres_pool, get_postgres_pool

    if get_postgres_pool() is None:
        if "postgresql" not in (db_url or ""):
            pytest.skip("boss_conversation 单测需要 PostgreSQL DATABASE_URL")
        try:
            init_postgres_pool()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"无法连接 PostgreSQL: {exc}")
    from src.db.database import get_db_connection

    from src.boss_conversation.init_tables import init_boss_conversation_tables
    from src.session_tasks.init_tables import init_session_task_tables
    from src.services.recruiting_resume_service import init_recruiting_operator_tables
    from src.services.recruiting_resume_timeline_service import init_recruiting_timeline_tables
    from src.weixin_conversation.init_tables import init_weixin_conversation_tables

    with get_db_connection() as conn:
        init_recruiting_operator_tables(conn)
        init_recruiting_timeline_tables(conn)
        init_session_task_tables(conn)
        init_weixin_conversation_tables(conn)
        init_boss_conversation_tables(conn)
    yield


@pytest.fixture()
def tenant_id():
    value = f"boss_test_{uuid.uuid4().hex[:12]}"
    yield value
    _cleanup_tenant(value)


@pytest.fixture(autouse=True)
def _boss_env(monkeypatch):
    """放开决策/许可链门控（含 boss 场景热读门控）+ 屏蔽计费（照 b12 范式）。"""
    import src.boss_conversation.config as boss_config
    import src.boss_conversation.registration as boss_registration
    import src.session_tasks.config as st_config
    import src.session_tasks.decisions as decisions_mod
    import src.session_tasks.service as service_mod
    import src.weixin_conversation.config as wx_config
    import src.weixin_conversation.registration as wx_registration
    from dataclasses import replace

    monkeypatch.setattr(wx_registration, "get_session_tasks_config", lambda: replace(_cfg(), enabled=True))
    monkeypatch.setattr(wx_config, "scenario_enabled", lambda tenant: True)
    monkeypatch.setattr(wx_config, "scenario_enabled_gate", lambda: True)
    monkeypatch.setattr(st_config, "tenant_allowed", lambda tenant: True)
    monkeypatch.setattr(service_mod, "tenant_allowed", lambda tenant: True)
    monkeypatch.setattr(boss_registration, "get_session_tasks_config", lambda: replace(_cfg(), enabled=True))
    monkeypatch.setattr(boss_config, "scenario_enabled_gate", lambda: True)
    monkeypatch.setattr(boss_config, "tenant_allowed", lambda tenant: True)
    monkeypatch.setattr(decisions_mod, "get_session_tasks_config", lambda: _cfg())
    monkeypatch.setattr(decisions_mod, "tenant_allowed", lambda tenant: True)
    monkeypatch.setattr("src.services.session_record.record_background_llm_usage", lambda usage, **kw: None)
    monkeypatch.setattr(
        "src.services.billing.calculate_credit_cost_with_breakdown",
        lambda p, c, m, cached_input_tokens=0: (0.01, {}),
    )
    wx_registration.ensure_registered()
    boss_registration.ensure_registered()
    _batch_seq_reset()
    yield
    # 个体用例可能改写过注册，复位后由后续用例重建
    boss_registration.reset_registration()
    wx_registration.reset_registration()


def _batch_seq_reset():
    from tests.unit.session_tasks.test_c3_decisions import _batch_seq_by_assignment

    _batch_seq_by_assignment.clear()


@pytest.fixture()
def device_row(tenant_id):
    """测试设备：能力满足 session_task_v1 + session_observer_v1 + boss_send_to_v2。"""
    from src.local_tools.repository import create_device

    device = create_device(
        tenant_id,
        "user-1",
        token_hash=uuid.uuid4().hex,
        name="boss-test-device",
        capabilities={
            "providers": ["boss-recruiting"],
            "capabilities": ["session_task_v1", "session_observer_v1", "boss_send_to_v2"],
        },
    )
    return device


@pytest.fixture()
def boss_binding(tenant_id, device_row):
    """verified 候选人绑定 fixture（仅测试库；含 resume 关联）。"""
    from datetime import datetime, timedelta, timezone as _tzmod

    from src.db.database import get_db_connection

    account_scope_id = str(uuid.uuid4())
    binding = create_test_binding(
        tenant_id, "user-1", device_id=str(device_row["id"]),
        account_scope_id=account_scope_id, candidate_name="张三", job_id=str(uuid.uuid4()),
    )
    resume_id = seed_resume(tenant_id, key_info={"current_company": "字节跳动", "years_of_experience": 5})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE bs_boss_conversation_bindings
            SET verification_status='verified', identity_version=1, verified_at=CURRENT_TIMESTAMP,
                encrypted_identity_evidence='enc:test-evidence', expires_at=%s, resume_id=%s
            WHERE tenant_id=%s AND id=%s
            """,
            (datetime.now(_tzmod.utc) + timedelta(days=30), resume_id, tenant_id,
             binding["conversation_binding_id"]),
        )
        conn.commit()
    return {
        "conversation_binding_id": binding["conversation_binding_id"],
        "account_scope_id": account_scope_id,
        "device_id": str(device_row["id"]),
        "resume_id": resume_id,
        "candidate_name": "张三",
    }


def create_test_binding(tenant_id: str, user_id: str, *, device_id: str, account_scope_id: str,
                        candidate_name: str, job_id: str, resume_id=None):
    from src.boss_conversation.bindings import create_pending_binding

    return create_pending_binding(
        tenant_id, user_id,
        device_id=device_id, account_scope_id=account_scope_id,
        candidate_name=candidate_name, job_id=job_id, resume_id=resume_id,
    )


def seed_resume(tenant_id: str, key_info: dict = None) -> int:
    import json

    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_recruiting_operator_resumes (tenant_id, candidate_name, key_info)
            VALUES (%s, '张三', %s) RETURNING id
            """,
            (tenant_id, json.dumps(key_info or {}, ensure_ascii=False)),
        )
        resume_id = int(cursor.fetchone()["id"])
        conn.commit()
    return resume_id


def seed_peer_message(tenant_id: str, task_id: str, message_id: str, text: str,
                      *, sender: str = "peer", input_version: int = 1) -> None:
    """P1-7 测试辅助：落一条已接纳批次内的消息（加密正文 + session_task_messages 行），
    供服务端正文加载（敏感兜底/槽位证据核验）使用。"""
    import uuid as _uuid

    from src.db.database import get_db_connection
    from src.session_tasks.texts import store_text

    with get_db_connection() as conn:
        cursor = conn.cursor()
        text_id = store_text(conn, tenant_id, _uuid.UUID(task_id), "message", {"text": text})
        cursor.execute(
            """
            INSERT INTO session_task_messages
                (tenant_id, task_id, conversation_binding_id, binding_version,
                 input_version, message_id, sender, text_id)
            VALUES (%s, %s, %s, 0, %s, %s, %s, %s)
            """,
            (tenant_id, task_id, f"binding-{message_id}", input_version, message_id,
             sender, text_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# 话术版本与 boss 任务发布辅助
# ---------------------------------------------------------------------------

NO_SLOT_TEMPLATE = "您好，看到您投递了我们的岗位，方便简单聊两句吗？"
SLOT_TEMPLATE = "您好，{expected_time} 方便沟通吗？我目前在{company}的招聘团队。"


def make_script_version(tenant_id: str, template: str, slot_schema: dict = None) -> dict:
    from src.boss_conversation import script_versions

    return script_versions.create_version(
        tenant_id, "user-1", template=template, slot_schema=slot_schema or {},
        source_job_name="测试职位",
    )


def make_boss_spec(tenant_id: str, scripts: list = None) -> dict:
    """合法 boss 发布单（引用已发布话术版本；rounds 完成）。"""
    if scripts is None:
        version = make_script_version(tenant_id, NO_SLOT_TEMPLATE)
        scripts = [{
            "script_version_id": version["id"],
            "content_hash": version["content_hash"],
            "frozen_template": version["template"],
            "slot_schema": {},
        }]
    from src.session_tasks.models import RoundsRule, TaskLimits

    _ = (RoundsRule, TaskLimits)
    return {
        "goal": "与候选人确认周五下午沟通意向",
        "completion_rule": {"mode": "rounds", "rounds_target": 2},
        "reply_policy": {
            "style": "简洁礼貌",
            "allowed_facts": ["可选时段为周五14:00或16:00"],
            "forbidden_commitments": ["薪资承诺"],
        },
        "limits": {
            "max_replies": 10,
            "max_decisions": 20,
            "max_cost_units": 100,
            "expires_at": "2099-01-01T00:00:00Z",
            "peer_wait_timeout_seconds": 86400,
        },
        "scripts": scripts,
        "slot_evidence_sources": _collect_sources(scripts),
    }


def _collect_sources(scripts: list) -> dict:
    sources = {}
    for s in scripts:
        for key in (s.get("slot_schema") or {}):
            if key == "expected_time":
                sources[key] = {"source": "peer_message"}
            elif key == "company":
                sources[key] = {"source": "resume_field", "field": "current_company"}
            else:
                sources[key] = {"source": "peer_message"}
    return sources


def publish_boss_task(tenant_id: str, binding: dict, spec: dict = None) -> str:
    """create → confirm → publish（boss 场景；返回 task_id）。"""
    from src.session_tasks import service
    from src.session_tasks.models import TaskDraftCreatePayload

    payload = TaskDraftCreatePayload.model_validate({
        "scenario_key": "boss.chat_reply.v1",
        "device_id": binding["device_id"],
        "account_binding_id": binding["account_scope_id"],
        "conversation_binding_id": binding["conversation_binding_id"],
        "spec": spec or make_boss_spec(tenant_id),
    })
    created = service.create_draft(tenant_id, "user-1", payload)
    confirmation = service.issue_publish_confirmation(
        tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"]
    )
    service.publish_task(
        tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
        uuid.UUID(confirmation["confirmation_id"]),
    )
    return created["task_id"]


def _device_dict(tenant_id, device_row):
    return {"id": device_row["id"], "tenant_id": tenant_id, "user_id": "user-1"}


def publish_and_claim_boss(tenant_id, device_row, binding, spec: dict = None):
    from src.session_tasks import service

    task_id = publish_boss_task(tenant_id, binding, spec)
    claimed = service.claim_task(_device_dict(tenant_id, device_row), "rt-boss")
    assert claimed is not None and str(claimed["task_id"]) == str(task_id)
    return task_id, claimed
