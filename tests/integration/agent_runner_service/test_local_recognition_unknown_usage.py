"""Actual successful VL response without provider usage remains unknown audit."""
from decimal import Decimal
import uuid

import pytest

from .domain_fixtures import recruiting_domain
from .domain_io import ScriptedDesktop
from .provider import Reply, tool_reply
from .test_local_resume_flow import resume_payload, evaluation_reply
from .test_worker import workers, api_pair, prices, accept, terminal, decoded

pytestmark = pytest.mark.integration


def test_actual_known_recognition_without_usage_keeps_unknown_receipt_and_one_domain_fee(
        workers, recruiting_domain, service_database):
    actor, profile = recruiting_domain
    candidate = '虚构未知量' + uuid.uuid4().hex[:10]
    workers.provider.register(evaluation_reply(candidate, reported_usage=False), marker=candidate)
    marker = workers.provider.register(tool_reply('boss_resume_detail', {'candidate_name': candidate},
                                                call_id='unknown-audit-detail-call'),
                                       Reply(content='fictional-known-recognition-delivered'))
    steps = [{'tool_name': 'boss_resume_detail', 'result': {'success': True, 'effect': 'none',
              'data': resume_payload(candidate)}}]
    with ScriptedDesktop(workers, actor, steps) as desktop:
        desktop.allow_claim(0)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        process, _ = workers.start()
        desktop.claimed(0)
        desktop.allow_result(0)
        desktop.completed(0)
        workers.assert_clean_exit(process)
    finished = terminal(service_database, accepted['runner_id'])
    assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
    assert service_database.rows('SELECT candidate_name FROM bs_recruiting_operator_resumes WHERE tenant_id=%s',
                                 (actor.tenant_id,)) == [{'candidate_name': candidate}]
    fees = service_database.rows('SELECT model,credit_cost FROM client_usage_logs WHERE tenant_id=%s', (actor.tenant_id,))
    assert fees == [{'model': 'boss_resume_recognition', 'credit_cost': Decimal('1.00')}]
    receipts = service_database.rows('SELECT phase,usage,price_snapshot FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                    (accepted['runner_id'],))
    covered = [row for row in receipts if decoded(row['price_snapshot']).get('covered_cost') == 'resume_recognition']
    assert len(receipts) == 3 and len(covered) == 1
    assert covered[0]['phase'] == 'unknown' and covered[0]['usage'] is None
    # Only actually reported usage contributes known totals. Recognition's
    # fixed delivered-item fee already covers its model cost, so its unknown
    # audit is neither observed-zero nor a second token charge/pending debt.
    assert service_database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
                                 (actor.session_id,)) == [{'total_token_count': 36, 'credit_cost': Decimal('0.01')}]
    assert service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
                                 (actor.tenant_id,)) == [{'credit_balance': Decimal('998.99')}]
    assert len(workers.provider.requests(candidate)) == 1 and len(workers.provider.requests(marker)) == 2
    assert not workers.provider.errors
