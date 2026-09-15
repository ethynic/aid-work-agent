"""Synthetic readonly results, isolated tenant; no live UI or account identity."""
import json
from uuid import uuid4, UUID

import pytest

from src.session_tasks import service
from src.session_tasks.models import TaskDraftCreatePayload
from src.session_tasks.constants import SessionTaskError
from src.weixin_conversation.name_contexts import from_resolution, name_context_valid
from tests.unit.session_tasks.conftest import build_spec


@pytest.fixture
def resolved(tenant_id, device_row):
    from src.db.database import get_db_connection
    invocation_id = str(uuid4())
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO local_tool_invocations
            (id,tenant_id,user_id,device_id,tool_name,provider_key,arguments_json,state,effect,result_json,finished_at)
            VALUES (%s,%s,'user-1',%s,'weixin_name_resolve','weixin',%s,'succeeded','none',%s,NOW())""",
            (invocation_id, tenant_id, device_row["id"], json.dumps({"target_name": "synthetic-contact"}),
             json.dumps({"data": {"target_name": "synthetic-contact", "title_exact": True,
                                 "unique_match": True, "evidence_ref": "dpapi:synthetic_evidence"}})))
        conn.commit()
    yield invocation_id
    with get_db_connection() as conn:
        conn.cursor().execute("DELETE FROM local_tool_invocations WHERE tenant_id=%s AND id=%s", (tenant_id, invocation_id))
        conn.commit()


def payload(device, invocation_id):
    return TaskDraftCreatePayload(device_id=str(device["id"]), resolution_invocation_id=invocation_id, spec=build_spec())


def test_draft_publish_claim_name_context_no_account_identity(tenant_id, device_row, resolved):
    from src.db.database import get_db_connection
    from src.weixin_conversation.adapters import binding_verified
    draft = service.create_draft(tenant_id, "user-1", payload(device_row, resolved))
    confirmation = service.issue_publish_confirmation(tenant_id, "user-1", UUID(draft["task_id"]), draft["version"])
    service.publish_task(tenant_id, "user-1", UUID(draft["task_id"]), draft["version"], UUID(confirmation["confirmation_id"]))
    claim = service.claim_task(device_row, "synthetic-runtime")
    assert claim["spec"]["_runtime_target"] == {"policy": "current_login_name", "target_name": "synthetic-contact"}
    assert claim["binding_version"] == 1 and claim["account_identity_version"] == 0
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM bs_weixin_conversation_bindings WHERE id=%s", (claim["conversation_binding_id"],))
        row = cur.fetchone()
        assert row["verification_status"] == "resolved" and row["verified_at"] is None
        assert name_context_valid(row) and binding_verified(row)
        cur.execute("SELECT id FROM bs_weixin_marketing_account_bindings WHERE id=%s", (row["account_binding_id"],))
        assert cur.fetchone() is None


@pytest.mark.parametrize("changes", [
    {"title_exact": False}, {"unique_match": False}, {"unique_match": "true"},
    {"target_name": "different"}, {"evidence_ref": "C:/unsafe.png"},
])
def test_reject_unresolved_or_wrong_name(tenant_id, device_row, resolved, changes):
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        cur = conn.cursor()
        data = {"target_name": "synthetic-contact", "title_exact": True, "unique_match": True,
                "evidence_ref": "dpapi:synthetic_evidence", **changes}
        cur.execute("UPDATE local_tool_invocations SET result_json=%s WHERE id=%s", (json.dumps({"data": data}), resolved))
        conn.commit()
    with pytest.raises(SessionTaskError):
        service.create_draft(tenant_id, "user-1", payload(device_row, resolved))


def test_reject_wrong_owner_stale_and_non_readonly(tenant_id, device_row, resolved):
    from src.db.database import get_db_connection
    with pytest.raises(SessionTaskError):
        service.create_draft(tenant_id, "other", payload(device_row, resolved))
    for clause in ["effect='applied'", "tool_name='weixin_chat_search'", "provider_key='fake'", "state='running'",
                   "finished_at=NOW()-INTERVAL '6 minutes'"]:
        with get_db_connection() as conn:
            conn.cursor().execute(f"UPDATE local_tool_invocations SET {clause} WHERE id=%s", (resolved,))
            conn.commit()
        with pytest.raises(SessionTaskError):
            service.create_draft(tenant_id, "user-1", payload(device_row, resolved))
        with get_db_connection() as conn:
            conn.cursor().execute("UPDATE local_tool_invocations SET effect='none',tool_name='weixin_name_resolve',provider_key='weixin',state='succeeded',finished_at=NOW() WHERE id=%s", (resolved,))
            conn.commit()


def test_same_name_reuses_context_scope(tenant_id, device_row, resolved):
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        one = from_resolution(conn, tenant_id, "user-1", device_row["id"], resolved)
        two = from_resolution(conn, tenant_id, "user-1", device_row["id"], resolved)
        conn.commit()
    assert one == two


def test_mutually_exclusive_input_and_no_client_runtime_policy(device_row, resolved):
    with pytest.raises(ValueError):
        TaskDraftCreatePayload(device_id=str(device_row["id"]), resolution_invocation_id=resolved,
                               account_binding_id=str(uuid4()), conversation_binding_id=str(uuid4()), spec=build_spec())
    spec = build_spec()
    spec["_runtime_target"] = {"policy": "current_login_name", "target_name": "injected"}
    with pytest.raises(ValueError):
        TaskDraftCreatePayload(device_id=str(device_row["id"]), resolution_invocation_id=resolved, spec=spec)


def test_malformed_result_and_control_character_rejected(tenant_id, device_row, resolved):
    from src.db.database import get_db_connection
    for args, result in [({}, []), ({"target_name": "bad\nname"}, {"data": {"target_name": "bad\nname", "title_exact": True, "unique_match": True, "evidence_ref": "dpapi:test"}})]:
        with get_db_connection() as conn:
            conn.cursor().execute("UPDATE local_tool_invocations SET arguments_json=%s,result_json=%s WHERE id=%s", (json.dumps(args), json.dumps(result), resolved))
            conn.commit()
        with pytest.raises(SessionTaskError) as err:
            service.create_draft(tenant_id, "user-1", payload(device_row, resolved))
        assert err.value.code == "NAME_NOT_RESOLVED"
