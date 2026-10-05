"""Raw Web intent and effective video context travel through actual worker/tools."""
import json
import uuid

import pytest

from .test_web_gateway import api_pair, gateways, workers, prices, submission, submit
from .test_worker import decoded, runner, terminal
from .provider import Reply, tool_reply

pytestmark=pytest.mark.integration


@pytest.mark.parametrize("route",["explicit","default_single"])
def test_web_effective_video_profile_normalizes_private_context_without_mutating_raw_intent(gateways, actors, service_database, route):
    actor=actors["a"]
    subscription="web-video-"+uuid.uuid4().hex
    profile="video-agent"
    service_database.rows("INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,payment_status) VALUES (%s,%s,%s,'active','paid')",(subscription,actor.tenant_id,profile))
    service_database.rows("INSERT INTO user_agent_permissions(user_id,tenant_id,agent_id) VALUES (%s,%s,%s)",(actor.user_id,actor.tenant_id,profile))
    try:
        raw={"mode":"agile","duration_sec":12,"ratio":"invalid","resolution":"invalid","card_count":8,
             "prompt_model":"fictional-video-prompt-model","session_id":"spoofed-session","tenant_id":actors["b"].tenant_id}
        marker=gateways.workers.provider.register(tool_reply("submit_video_task",{
            "user_input":"fictional video description","session_id":"spoofed-tool-session",
            "tenant_id":actors["b"].tenant_id,"user_id":actors["b"].user_id},call_id="actual-video-tool"),Reply(content="Video request completed"))
        request=submission(actor,message=marker,video_params=raw,**({"subagent":profile} if route=="explicit" else {}))
        gateway=gateways.start()
        accepted=submit(gateway,actor,request)
        before=runner(service_database,accepted["runner_id"])
        assert before["profile_id"]==profile
        original=decoded(before["input"])
        assert original["profile_id"]==(profile if route=="explicit" else "main")
        assert original["request_data"]["video_params"]["duration_sec"]==12
        assert "session_id" not in original["request_data"]["video_params"]
        projected=decoded(before["checkpoint"])["execution_context"]["request_data"]["video_params"]
        assert projected=={"mode":"agile","duration_sec":5,"ratio":"9:16","resolution":"720P","card_count":1,
                           "prompt_model":"fictional-video-prompt-model","session_id":actor.session_id}
        report=gateways.workers.root/("video-report-"+uuid.uuid4().hex+".json")
        child=gateways.workers.processes.start(["-m","tests.integration.agent_runner_service.video_io_worker","--report",str(report)],
                                              environment=gateways.workers.environment,private_working_directory=True)
        gateways.workers.children.append((child,"fixture-video-worker"))
        gateways.workers.assert_clean_exit(child)
        finished=terminal(service_database,accepted["runner_id"])
        assert finished["status"]=="completed"
        observed=json.loads(report.read_text())
        assert (observed["tenant_id"],observed["user_id"],observed["session_id"])==(actor.tenant_id,actor.user_id,actor.session_id)
        assert observed["params"]=={key:projected[key] for key in ("mode","duration_sec","ratio","resolution","card_count","prompt_model")}
        assert observed["trusted_context"]["request_data"]["video_params"]==projected
        assert decoded(finished["input"])==original and finished["input_digest"]==before["input_digest"]
        assert len(gateways.workers.provider.requests(marker))==2
        assert len(service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s",(actor.session_id,)))==1
    finally:
        service_database.rows("DELETE FROM subscriptions WHERE subscription_id=%s",(subscription,))
        service_database.rows("DELETE FROM user_agent_permissions WHERE user_id=%s AND tenant_id=%s AND agent_id=%s",(actor.user_id,actor.tenant_id,profile))
