"""Fresh history subject/owner checks after real cached token authentication."""
from datetime import datetime, timedelta
import json
import uuid

import pytest

from .test_web_gateway import api_pair, gateways, workers, prices
from .test_api import require_status

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("mutation,expected", [("revoke",401),("rebind",401),("move_tenant",404)])
def test_warmed_history_token_is_not_authority_after_sql_identity_changes(gateways, actors, service_database, mutation, expected):
    actor=actors["a"]
    report=gateways.workers.root / ("history-cache-"+uuid.uuid4().hex+".json")
    gateway=gateways.start(factory="tests.integration.agent_runner_service.web_cache_probe:create_app",
                           extra_environment={"RUNNER_TEST_CACHE_REPORT":str(report)})
    path="/api/sessions/"+actor.session_id+"/messages"
    require_status(gateway.call("GET",path,actor=actor),200)
    require_status(gateway.call("GET",path,actor=actor),200)
    assert any(item["hit"] and item["valid"] and item["returned_fixture_user"] for item in json.loads(report.read_text()))
    if mutation=="revoke":
        service_database.rows("DELETE FROM tokens WHERE token=%s",(actor.token,))
    elif mutation=="rebind":
        service_database.rows("UPDATE tokens SET user_id=%s WHERE token=%s",(actors["a_other"].user_id,actor.token))
    else:
        service_database.rows("UPDATE users SET tenant_id=%s WHERE user_id=%s",(actors["b"].tenant_id,actor.user_id))
    require_status(gateway.call("GET",path,actor=actor),expected)
    final=json.loads(report.read_text())[-1]
    assert final["hit"] and final["valid"] and final["returned_fixture_user"]


def test_history_owner_scope_includes_null_admin_and_rejects_foreign_tenant_header(gateways, actors, service_database):
    gateway=gateways.start()
    for label in ("a","global"):
        actor=actors[label]
        require_status(gateway.call("GET","/api/sessions/"+actor.session_id+"/messages",actor=actor),200)
    require_status(gateway.call("GET","/api/sessions/"+actors["a"].session_id+"/messages",
                               headers={**gateway.headers(actors["a"]),"X-Tenant-Id":actors["b"].tenant_id}),403)
    require_status(gateway.call("GET","/api/sessions/"+actors["global"].session_id+"/messages",actor=actors["global_other"]),404)
    service_database.rows("UPDATE tenants SET credit_balance=0,status='suspended' WHERE tenant_id=%s",(actors["a"].tenant_id,))
    require_status(gateway.call("GET","/api/sessions/"+actors["a"].session_id+"/messages",actor=actors["a"]),200)
