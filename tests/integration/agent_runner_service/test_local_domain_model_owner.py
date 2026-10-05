"""Real provider/PG finite domain permits; native owner-contract fixture only.

No actual Detail/Batch recognition producer or Engine conversation is claimed;
those need the later B pipeline. Physical responses and usage are real localhost
HTTP through the actual Gateway. All checkpoint/receipt writes use real owners.
"""
from decimal import Decimal
import json
from pathlib import Path
import threading
import uuid

import pytest

from .conftest import wait_for
from .domain_fixtures import recruiting_domain
from .provider import Reply
from .test_worker import workers, api_pair, prices, accept, runner, decoded
from .test_worker_recovery import control

pytestmark = pytest.mark.integration


def launch(workers, actor, profile, mode, reply, *, other_actor=None):
    marker=workers.provider.register(reply)
    accepted=accept(workers.api,actor,marker,profile_id=profile)
    folder=workers.root/('domain-model-'+uuid.uuid4().hex);folder.mkdir()
    args=[str(Path(__file__).with_name('domain_model_probe.py')),mode,
          accepted['runner_id'],marker,workers.models[0],str(folder)]
    if other_actor is not None:
        other=accept(workers.api,other_actor,marker)
        args.append(other['runner_id'])
    environment={**workers.environment,'LITE_MODEL_CODE':'qwen/'+workers.models[0]}
    process=workers.processes.start(args,environment=environment,private_working_directory=True)
    return marker,accepted,folder,process


def report(workers, folder, process, *, expected_exit=0):
    if expected_exit==0:
        workers.assert_clean_exit(process)
    else:
        assert process.wait(timeout=25)==expected_exit
    return json.loads((folder/'report.json').read_text())


@pytest.mark.parametrize('mode',['covered-detail','covered-batch','client-hint','hint-purpose','hint-skill'])
def test_finite_original_recognition_permit_and_untrusted_hints_have_distinct_prices(
        workers,recruiting_domain,service_database,mode):
    actor,profile=recruiting_domain
    marker,accepted,folder,process=launch(workers,actor,profile,mode,Reply(content='real-domain-physical-content'))
    actual=report(workers,folder,process,expected_exit=87 if mode=='hint-skill' else 0)
    if mode.startswith('covered-'):
        assert actual['cost']=='0' and actual['totals']['total_token_count']==18
        assert actual['display']=={'input':11,'output':7,'cached':0}
        assert actual['model_calls']==0
        assert actual['domain']==[{'branch':'resume.evaluate','ordinal':0,'phase':'completed','authorized_attempt':1}]
        assert len(actual['receipts'])==1 and actual['receipts'][0]['phase']=='observed'
        assert actual['receipts'][0]['price_covered']=='resume_recognition'
        assert actual['receipts'][0]['purpose'].startswith('domain:')
        assert len(workers.provider.requests(marker))==1
    elif mode=='client-hint':
        assert Decimal(actual['cost'])==Decimal('0.01')
        assert actual['totals']['total_token_count']==18
        assert len(actual['receipts'])==1 and actual['receipts'][0]['price_covered'] is None
        assert actual['receipts'][0]['purpose']=='llm'
        assert len(workers.provider.requests(marker))==1
    else:
        assert actual['error_code']=='COVERED_USAGE_OWNER_REQUIRED'
        assert actual['receipts']==[] and workers.provider.requests(marker)==[]
    assert not workers.provider.errors
    assert service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s',(actor.tenant_id,))==[]
    # This bounded interface proves frozen pricing/real receipt facts, not actual
    # VL delivery or a committed business-recognition fee, and never fakes one.
    assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s',(actor.session_id,))==[]


@pytest.mark.parametrize('mode',['foreign-receipt','sibling-receipt'])
def test_genuine_foreign_or_other_phase_provider_response_cannot_complete_this_phase(
        workers,recruiting_domain,actors,service_database,mode):
    actor,profile=recruiting_domain
    marker,accepted,folder,process=launch(workers,actor,profile,mode,Reply(content='genuine-earlier-physical-response'),
        other_actor=actors['b'] if mode=='foreign-receipt' else None)
    actual=report(workers,folder,process)
    assert actual['error_code']=='LOCAL_MODEL_RECEIPT_OWNER_MISMATCH'
    rejected=[fact for fact in actual['domain'] if fact['ordinal']==(0 if mode=='foreign-receipt' else 1)]
    assert len(rejected)==1 and rejected[0]['phase']=='started'
    assert len(workers.provider.requests(marker))==1 and not workers.provider.errors
    assert len(actual['receipts'])==(0 if mode=='foreign-receipt' else 1)
    assert actual['model_calls']==0


def test_cancellation_preserves_started_original_model_response_and_blocks_next_phase(
        workers,recruiting_domain,service_database):
    actor,profile=recruiting_domain
    release=threading.Event()
    reply=Reply(content='known-original-model-result',release=release)
    marker,accepted,folder,process=launch(workers,actor,profile,'cancel-response',reply)
    try:
        assert reply.arrived.wait(15),'Actual provider request did not arrive'
        assert workers.api.call('POST',f"/v1/runners/{accepted['runner_id']}/cancel",actor=actor).status_code==200
        assert runner(service_database,accepted['runner_id'])['cancel_requested']
        release.set()
        actual=report(workers,folder,process)
    finally:
        release.set()
    assert actual['error_code']=='RUNNER_CANCEL_REQUESTED'
    assert actual['domain']==[{'branch':'heal.choice','ordinal':0,'phase':'completed','authorized_attempt':1}]
    assert len(actual['receipts'])==1 and actual['receipts'][0]['phase']=='observed'
    assert actual['totals']['total_token_count']==18 and actual['display']['input']==11
    assert actual['model_calls']==0
    assert len(workers.provider.requests(marker))==1 and not workers.provider.errors
    saved=decoded(runner(service_database,accepted['runner_id'])['checkpoint'])['execution']
    fact=next(iter(saved['resources']['local_domain_phases'].values()))
    assert fact['result']['content']=='known-original-model-result'
    assert service_database.rows('SELECT count(*) AS n FROM local_tool_invocations WHERE session_id=%s',(actor.session_id,))==[{'n':0}]


@pytest.mark.parametrize('mode',['rearm-none','rearm-started','rearm-unknown','rearm-observed','rearm-old-attempt'])
def test_model_rearm_needs_zero_original_receipts_including_old_attempts(
        workers,recruiting_domain,service_database,mode):
    actor,profile=recruiting_domain
    response=Reply(content='one-physical-domain-result',reported_usage=None) if mode=='rearm-unknown' else Reply(content='one-physical-domain-result')
    marker,accepted,folder,process=launch(workers,actor,profile,mode,response)
    if mode=='rearm-none':
        wait_for((folder/'before-io').exists,timeout=15)
        control(workers.api,actor,accepted['runner_id'],'pause')
        (folder/'release-before-io').touch()
        wait_for((folder/'paused.json').exists,timeout=15)
        paused=runner(service_database,accepted['runner_id'])
        assert paused['status']=='paused' and paused['attempt']==1
        assert service_database.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                    (accepted['runner_id'],))==[]
        assert workers.provider.requests(marker)==[]
        submitted,_=control(workers.api,actor,accepted['runner_id'],'resume')
        (folder/'resume-ready').touch()
        actual=report(workers,folder,process)
        assert actual['attempt']==2 and actual['domain']==[{
            'branch':'heal.choice','ordinal':0,'phase':'completed','authorized_attempt':2}]
        assert len(actual['receipts'])==1 and actual['receipts'][0]['authorized_attempt']==2
        assert len(workers.provider.requests(marker))==1
        stored=service_database.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE control_id=%s',
                                     (submitted['control']['control_id'],))
        assert stored==[{'status':'consumed','consumed_attempt':2}]
    else:
        actual=report(workers,folder,process)
        assert actual['error_code']=='LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED'
        assert len(actual['domain'])==1 and actual['domain'][0]['phase']=='started'
        assert len(actual['receipts'])==1
        expected='started' if mode in {'rearm-started','rearm-old-attempt'} else mode.removeprefix('rearm-')
        assert actual['receipts'][0]['phase']==expected
        assert len(workers.provider.requests(marker))==(0 if expected=='started' else 1)
        if mode=='rearm-old-attempt':
            assert actual['attempt']==2 and actual['receipts'][0]['authorized_attempt']==1
    assert actual['model_calls']==0 and not workers.provider.errors
