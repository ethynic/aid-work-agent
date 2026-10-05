"""Registered recruiting cloud reads/send ACKs; prepared for B source freeze."""
from decimal import Decimal
import uuid

import pytest

from .domain_fixtures import recruiting_domain
from .domain_io import ScriptedDesktop
from .provider import Reply, tool_reply
from .test_worker import workers, api_pair, prices, accept, terminal, decoded

pytestmark = pytest.mark.integration


def test_actual_jobs_list_uses_original_tenant_scope_and_ui_options_without_device(
        workers, recruiting_domain, actors, service_database):
    from src.services.recruiting_job_service import create_job
    actor, profile = recruiting_domain
    active = create_job(actor.tenant_id, job_name='Fictional active job',
                        match_threshold=0, job_requirements={'keywords': ['Python']})
    create_job(actor.tenant_id, job_name='Fictional paused job', status='paused')
    foreign = create_job(actors['b'].tenant_id, job_name='Fictional foreign job')
    marker = workers.provider.register(tool_reply('boss_jobs_list', {}, call_id='jobs-cloud-call'),
                                       Reply(content='fictional-cloud-jobs-output'))
    accepted = accept(workers.api, actor, marker, profile_id=profile)
    process, _ = workers.start()
    workers.assert_clean_exit(process)
    finished = terminal(service_database, accepted['runner_id'])
    assert finished['status'] == 'completed'
    result = decoded(finished['checkpoint'])['execution']['tools']['jobs-cloud-call']['result']
    assert result['success'] and len(result['data']['jobs']) == 1
    job = result['data']['jobs'][0]
    assert job['job_id'] == active['id'] and job['match_threshold'] == 0
    assert job['job_requirements'] == {'keywords': ['Python']}
    assert job['resume_count'] == 0 and job['matched_count'] == 0
    assert result['data']['options'][0]['key'] == active['id']
    assert result['data']['options'][0]['label'] == active['job_name']
    assert str(foreign['id']) not in str(result)
    assert service_database.rows('SELECT 1 FROM local_tool_invocations WHERE session_id=%s', (actor.session_id,)) == []
    assert service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
    assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors


@pytest.mark.parametrize('tool', ['boss_send_to', 'boss_send_current'])
def test_actual_script_lookup_is_read_only_and_keeps_original_needs_fill_contract(
        workers, recruiting_domain, actors, service_database, tool):
    from src.services.recruiting_job_service import create_job, create_script
    actor, profile = recruiting_domain
    job = create_job(actor.tenant_id, job_name='Fictional exact script job')
    create_script(actor.tenant_id, job['id'], category='初次开场', title='Fictional greeting',
                  content='您好 {{姓名}}，这是虚构测试话术。')
    foreign = create_job(actors['b'].tenant_id, job_name=job['job_name'])
    create_script(actors['b'].tenant_id, foreign['id'], category='初次开场', title='Fictional greeting',
                  content='fictional-foreign-template-must-not-appear')
    arguments = {'script_title': 'Fictional greeting', 'job_name': job['job_name']}
    if tool == 'boss_send_to':
        arguments['to'] = '虚构候选'
    marker = workers.provider.register(tool_reply(tool, arguments, call_id='cloud-script-call'),
                                       Reply(content='fictional-script-requires-confirmation'))
    accepted = accept(workers.api, actor, marker, profile_id=profile)
    process, _ = workers.start()
    workers.assert_clean_exit(process)
    finished = terminal(service_database, accepted['runner_id'])
    assert finished['status'] == 'completed'
    result = decoded(finished['checkpoint'])['execution']['tools']['cloud-script-call']['result']
    assert not result['success'] and result['code'] == 'SCRIPT_NEEDS_FILL'
    assert result['script']['content'] == '您好 {{姓名}}，这是虚构测试话术。'
    assert 'fictional-foreign-template-must-not-appear' not in str(result)
    assert service_database.rows('SELECT 1 FROM local_tool_invocations WHERE session_id=%s', (actor.session_id,)) == []
    assert service_database.rows('SELECT 1 FROM bs_recruiting_operator_resume_comm_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
    assert service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
    assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors


@pytest.mark.parametrize('tool', ['boss_send_to', 'boss_send_current'])
def test_actual_sent_original_ack_has_one_device_fee_and_only_send_to_comm_log(
        workers, recruiting_domain, service_database, tool):
    from src.services.recruiting_resume_service import create_resume_record
    actor, profile = recruiting_domain
    candidate = '虚构接收' + uuid.uuid4().hex[:12]
    resume = create_resume_record(actor.tenant_id, actor.user_id, candidate_name=candidate)
    message = 'fictional externally acknowledged message'
    arguments = {'message': message}
    if tool == 'boss_send_to':
        arguments['to'] = candidate
    marker = workers.provider.register(tool_reply(tool, arguments, call_id='sent-original-call'),
                                       Reply(content='fictional-send-delivered-once'))
    steps = [{'tool_name': tool, 'result': {'success': True, 'effect': 'applied',
              'data': {'to': candidate, 'sent': True, 'dry_run': False}}}]
    with ScriptedDesktop(workers, actor, steps) as desktop:
        desktop.allow_claim(0)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        process, _ = workers.start()
        original = desktop.claimed(0)['invocation_id']
        assert service_database.rows('SELECT 1 FROM bs_recruiting_operator_resume_comm_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
        desktop.allow_result(0)
        desktop.completed(0)
        workers.assert_clean_exit(process)
    finished = terminal(service_database, accepted['runner_id'])
    assert finished['status'] == 'completed'
    result = decoded(finished['checkpoint'])['execution']['tools']['sent-original-call']['result']
    assert result['success'] and result['data']['sent']
    logs = service_database.rows('SELECT resume_id,content,direction,channel,user_id FROM bs_recruiting_operator_resume_comm_logs WHERE tenant_id=%s', (actor.tenant_id,))
    if tool == 'boss_send_to':
        assert logs == [{'resume_id': resume['id'], 'content': message, 'direction': 'out',
                         'channel': 'boss', 'user_id': actor.user_id}]
    else:
        assert logs == []
    invocations = service_database.rows('SELECT id,credit_cost FROM local_tool_invocations WHERE session_id=%s', (actor.session_id,))
    assert len(invocations) == 1 and str(invocations[0]['id']) == original
    assert Decimal(str(invocations[0]['credit_cost'])) == Decimal('1.00')
    fees = service_database.rows('SELECT model,credit_cost FROM client_usage_logs WHERE tenant_id=%s', (actor.tenant_id,))
    assert fees == [{'model': tool, 'credit_cost': Decimal('1.00')}]
    assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors
