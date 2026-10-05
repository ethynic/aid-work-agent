"""Actual registered resume tools and VL HTTP; wait for B source freeze.

Only the fictional desktop screenshot/result and local model replies replace
external IO. Original Runtime, domain pipeline, receipts, fees and DAL run.
"""
import base64
from decimal import Decimal
import json
import uuid

import httpx
import pytest

from .domain_fixtures import recruiting_domain, fictional_resume_image
from .domain_io import ScriptedDesktop
from .provider import Reply, tool_reply
from .test_worker import workers, api_pair, prices, accept, terminal, decoded
from .test_shared_worker_artifacts import start_download

pytestmark = pytest.mark.integration


def resume_payload(name):
    return {'candidate_name': name, 'name_source': 'param',
            'ocr_text': 'fictional-untrusted-old-client-text',
            'images': [{'name': 'fictional-resume.png', 'mime_type': 'image/png',
                        'base64': fictional_resume_image()}]}


def evaluation_reply(name, *, score=82, reported_usage=True):
    content = json.dumps({'name_seen': name, 'resume_summary': 'Fictional evaluated summary',
                          'score': score, 'match_summary': 'Fictional matching evidence',
                          'key_info': {'education': '本科', 'core_skills': ['Python']}}, ensure_ascii=False)
    return Reply(content=content) if reported_usage else Reply(content=content, reported_usage=None)


def test_actual_detail_vl_covered_fee_insert_match_and_shared_image_delivery(
        workers, recruiting_domain, actors, service_database):
    actor, profile = recruiting_domain
    candidate = '虚构候选' + uuid.uuid4().hex[:12]
    payload = resume_payload(candidate)
    workers.provider.register(evaluation_reply(candidate), marker=candidate)
    marker = workers.provider.register(
        tool_reply('boss_resume_detail', {'candidate_name': candidate}, call_id='original-detail-call'),
        Reply(content='fictional-detail-final-output'))
    steps = [{'tool_name': 'boss_resume_detail', 'result': {
        'success': True, 'effect': 'none', 'data': payload}}]
    with ScriptedDesktop(workers, actor, steps) as desktop:
        desktop.allow_claim(0)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        process, _ = workers.start()
        invocation_id = desktop.claimed(0)['invocation_id']
        desktop.allow_result(0)
        desktop.completed(0)
        workers.assert_clean_exit(process)
    finished = terminal(service_database, accepted['runner_id'])
    assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
    execution = decoded(finished['checkpoint'])['execution']
    assert len(execution['model_calls']) == 2
    result = execution['tools']['original-detail-call']['result']
    assert result['success'] and 'fictional-untrusted-old-client-text' not in str(result)
    assert payload['images'][0]['base64'] not in str(result)
    stored = service_database.rows('SELECT * FROM bs_recruiting_operator_resumes WHERE tenant_id=%s',
                                   (actor.tenant_id,))
    assert len(stored) == 1 and stored[0]['candidate_name'] == candidate
    assert stored[0]['resume_summary'] == 'Fictional evaluated summary' and stored[0]['ocr_text'] is None
    assert stored[0]['match_score'] == 82 and stored[0]['match_status'] == 'matched'
    images = decoded(stored[0]['images'])
    assert len(images) == 1 and images[0]['file_id']
    exact_files = list((workers.storage / 'tenants' / actor.tenant_id).rglob(images[0]['file_id'] + '.*'))
    assert len(exact_files) == 1 and exact_files[0].read_bytes() == base64.b64decode(payload['images'][0]['base64'])
    delivered = httpx.get(start_download(workers) + '/api/files/' + images[0]['file_id'] + '/download', timeout=10)
    assert delivered.status_code == 200 and delivered.content == exact_files[0].read_bytes()
    invocations = service_database.rows('SELECT id,credit_cost FROM local_tool_invocations WHERE session_id=%s',
                                       (actor.session_id,))
    assert len(invocations) == 1 and str(invocations[0]['id']) == invocation_id
    assert Decimal(str(invocations[0]['credit_cost'] or 0)) == 0
    fees = service_database.rows('SELECT model,credit_cost FROM client_usage_logs WHERE tenant_id=%s',
                                (actor.tenant_id,))
    assert fees == [{'model': 'boss_resume_recognition', 'credit_cost': Decimal('1.00')}]
    receipts = service_database.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                    (accepted['runner_id'],))
    assert len(receipts) == 3 and all(row['phase'] == 'observed' and row['applied'] for row in receipts)
    covered = [row for row in receipts if decoded(row['price_snapshot']).get('covered_cost') == 'resume_recognition']
    assert len(covered) == 1 and covered[0]['tool_call_id'] == 'original-detail-call'
    assert covered[0]['provider'] == 'zhipu' and covered[0]['model'] == 'GLM-5.3-Flash'
    records = service_database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
                                   (actor.session_id,))
    assert records == [{'total_token_count': 54, 'credit_cost': Decimal('0.01')}]
    assert service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
                                 (actor.tenant_id,)) == [{'credit_balance': Decimal('998.99')}]
    assert len(workers.provider.requests(marker)) == 2 and len(workers.provider.requests(candidate)) == 1
    assert not workers.provider.errors
    # The legacy opaque download URL intentionally remains public. The actual
    # authenticated Runner attachment path must still reject another tenant's
    # generated image before provider IO, without deleting the owner's file.
    foreign_marker = workers.provider.register(Reply(content='must-not-read-another-tenant-resume'))
    foreign = accept(workers.api, actors['b'], foreign_marker,
                     attachments=[{'file_id': images[0]['file_id']}])
    foreign_process, _ = workers.start()
    workers.assert_clean_exit(foreign_process)
    refused = terminal(service_database, foreign['runner_id'])
    assert refused['status'] == 'failed' and workers.provider.requests(foreign_marker) == []
    assert service_database.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                 (foreign['runner_id'],)) == []
    assert exact_files[0].read_bytes() == base64.b64decode(payload['images'][0]['base64'])


def test_actual_batch_keeps_per_item_recognition_charges_and_distinct_images(
        workers, recruiting_domain, service_database):
    actor, profile = recruiting_domain
    candidates = ['虚构甲' + uuid.uuid4().hex[:10], '虚构乙' + uuid.uuid4().hex[:10]]
    payloads = [resume_payload(name) for name in candidates]
    for name in candidates:
        workers.provider.register(evaluation_reply(name), marker=name)
    marker = workers.provider.register(
        tool_reply('boss_resume_batch', {'limit': 2}, call_id='original-batch-call'),
        Reply(content='fictional-batch-final-output'))
    steps = [{'tool_name': 'boss_resume_batch', 'result': {'success': True, 'effect': 'none',
              'data': {'resumes': payloads, 'failures': [], 'attempted': 2}}}]
    with ScriptedDesktop(workers, actor, steps) as desktop:
        desktop.allow_claim(0)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        process, _ = workers.start()
        original = desktop.claimed(0)['invocation_id']
        desktop.allow_result(0)
        desktop.completed(0)
        workers.assert_clean_exit(process)
    finished = terminal(service_database, accepted['runner_id'])
    assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
    stored = service_database.rows('SELECT candidate_name,images,match_score FROM bs_recruiting_operator_resumes WHERE tenant_id=%s',
                                   (actor.tenant_id,))
    assert len(stored) == 2 and {row['candidate_name'] for row in stored} == set(candidates)
    references = [decoded(row['images'])[0]['file_id'] for row in stored]
    assert len(set(references)) == 2 and all(row['match_score'] == 82 for row in stored)
    fees = service_database.rows('SELECT model,credit_cost FROM client_usage_logs WHERE tenant_id=%s', (actor.tenant_id,))
    assert len(fees) == 2 and all(row == {'model': 'boss_resume_recognition', 'credit_cost': Decimal('1.00')} for row in fees)
    invocations = service_database.rows('SELECT id,credit_cost FROM local_tool_invocations WHERE session_id=%s', (actor.session_id,))
    assert len(invocations) == 1 and str(invocations[0]['id']) == original and not invocations[0]['credit_cost']
    receipts = service_database.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s', (accepted['runner_id'],))
    covered = [row for row in receipts if decoded(row['price_snapshot']).get('covered_cost') == 'resume_recognition']
    assert len(receipts) == 4 and len(covered) == 2
    assert all(row['phase'] == 'observed' and row['applied'] for row in receipts)
    assert len({row['purpose'] for row in covered}) == 2
    assert all(row['tool_call_id'] == 'original-batch-call' for row in covered)
    assert service_database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
                                 (actor.session_id,)) == [{'total_token_count': 72, 'credit_cost': Decimal('0.01')}]
    assert service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
                                 (actor.tenant_id,)) == [{'credit_balance': Decimal('997.99')}]
    assert len(workers.provider.requests(marker)) == 2
    assert all(len(workers.provider.requests(candidate)) == 1 for candidate in candidates)
    assert not workers.provider.errors


def test_actual_resume_name_gate_refuses_delivery_and_recognition_charge(
        workers, recruiting_domain, service_database):
    actor, profile = recruiting_domain
    candidate = '虚构甲' + uuid.uuid4().hex[:12]
    # Preserve the original expected-name prompt marker; external response is a
    # genuinely mismatched name, not a fabricated stored evaluation/tool result.
    workers.provider.register(evaluation_reply('完全不同的虚构姓名'), marker=candidate)
    marker = workers.provider.register(
        tool_reply('boss_resume_detail', {'candidate_name': candidate}, call_id='name-gate-call'),
        Reply(content='fictional-name-mismatch-explained'))
    steps = [{'tool_name': 'boss_resume_detail', 'result': {
        'success': True, 'effect': 'none', 'data': resume_payload(candidate)}}]
    with ScriptedDesktop(workers, actor, steps) as desktop:
        desktop.allow_claim(0)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        process, _ = workers.start()
        desktop.claimed(0)
        desktop.allow_result(0)
        desktop.completed(0)
        workers.assert_clean_exit(process)
    finished = terminal(service_database, accepted['runner_id'])
    assert finished['status'] == 'completed'
    fact = decoded(finished['checkpoint'])['execution']['tools']['name-gate-call']
    assert not fact['result']['success'] and fact['result']['code'] == 'RESUME_NAME_MISMATCH'
    assert service_database.rows('SELECT 1 FROM bs_recruiting_operator_resumes WHERE tenant_id=%s', (actor.tenant_id,)) == []
    assert service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
    receipts = service_database.rows('SELECT price_snapshot,phase FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                    (accepted['runner_id'],))
    assert len(receipts) == 3 and sum(decoded(row['price_snapshot']).get('covered_cost') == 'resume_recognition' for row in receipts) == 1
    assert all(row['phase'] == 'observed' for row in receipts)
    assert len(workers.provider.requests(candidate)) == 1 and len(workers.provider.requests(marker)) == 2
    assert not workers.provider.errors
