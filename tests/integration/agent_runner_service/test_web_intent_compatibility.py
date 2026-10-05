"""Existing instance and accepted-input contracts at the real gateway boundary."""
import uuid

import pytest

from .test_web_gateway import api_pair, gateways, workers, prices, submission, submit
from .test_api import body, require_status
from .test_worker import decoded, runner

pytestmark=pytest.mark.integration


def test_web_instance_is_bound_to_current_owned_session_and_retry_keeps_original_context(gateways, actors, service_database):
    gateway=gateways.start()
    actor=actors["a"]
    instance="fictional-bound-instance"
    service_database.rows("UPDATE chat_sessions SET instance_id=%s WHERE session_id=%s",(instance,actor.session_id))
    mismatch=require_status(gateway.call("POST","/api/chat/runners",actor=actor,
                               json=submission(actor,instance_id="foreign-instance")),403)
    assert mismatch["code"]=="INSTANCE_SCOPE_MISMATCH"
    request=submission(actor,instance_id=instance,subagent="main",video_params={"duration_sec":10})
    accepted=submit(gateway,actor,request)
    private=runner(service_database,accepted["runner_id"])
    assert decoded(private["input"])["instance_id"]==instance
    assert decoded(private["input"])["request_data"]=={"video_params":{"duration_sec":10}}
    # A non-video actual profile must not receive video domain data/prompt hints.
    context=decoded(private["checkpoint"])["execution_context"]
    assert context["profile_id"]=="main" and context["request_data"]=={} and context["prompt_augmentations"]==[]
    assert submit(gateway,actor,request)["runner_id"]==accepted["runner_id"]
    assert len(service_database.rows("SELECT 1 FROM agent_runners WHERE user_id=%s",(actor.user_id,)))==1


def test_additive_default_context_fields_do_not_change_existing_explicit_idempotency_intent(gateways, actors):
    api=gateways.workers.api
    actor=actors["a"]
    request=body(actor,text="Original explicit M2 intent",attachments=[{"file_id":"fictional-old-file","name":"old.txt"}])
    original=require_status(api.call("POST","/v1/runners",actor=actor,json=request),202)["runner"]
    additive={**request,"instance_id":None,"request_data":{},"routing_policy":"explicit",
              "attachments":[{**request["attachments"][0],"size":None}]}
    retried=require_status(api.call("POST","/v1/runners",actor=actor,json=additive),202)
    assert retried["created"] is False and retried["runner"]["runner_id"]==original["runner_id"]
