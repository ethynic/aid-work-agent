"""Finite real-PG display contracts on declared native owned-call fixtures.

These shapes are storage contracts, not actual Browser activation/human IO.
The separate real resident test covers the actual producer/Worker/Web boundary.
"""
import copy
import uuid

import pytest
from psycopg2 import sql
from psycopg2.errors import RaiseException

from .test_storage import storage
from .test_browser_binding_storage import browser_owner, assistance
from .test_browser_completion_storage import prepared_wait

pytestmark = pytest.mark.integration


def card(request):
    return {key: request[key] for key in ('assistance_id', 'run_id', 'reason_code', 'completion_mode')} | {
        'surface': 'server_web', 'title': 'Original fictional page verification', 'steps': [],
        'completion_status': 'pending', 'state': 'pending',
        'expires_at': request['expires_at'].isoformat(), 'view_available': True,
    }


def selected_wait(values, database, row, request):
    actual = database.rows('SELECT runner_wait_id FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                          (request['assistance_id'],))[0]
    checkpoint = copy.deepcopy(row['checkpoint'])
    root = checkpoint['execution']
    root['tools'][values['call_id']]['phase'] = 'waiting'
    root['waiting'] = dict(kind='human_assistance', tool_call_id=values['call_id'],
        assistance_id=request['assistance_id'], wait_id=actual['runner_wait_id'], target_execution_id=values['root_id'])
    return checkpoint


def visible_wait(values, database):
    _, waited, request, binding = prepared_wait(values)
    checkpoint = selected_wait(values, database, waited, request)
    saved = values['execution'].save_checkpoint(values['attempt'], waited['revision'], checkpoint,
        {'browserAssistance': card(request)})
    assert saved['public_snapshot']['browserAssistance']['assistance_id'] == request['assistance_id']
    return saved, request, binding


def test_domain_display_failure_rolls_back_assistance_and_view_in_the_same_original_transaction(browser_owner, service_database):
    from src.services.agent_runner.browser_human_repository import BrowserHumanRepository
    values = browser_owner
    before, request, binding = visible_wait(values, service_database)
    name = 'fixture_display_refused_' + uuid.uuid4().hex
    trigger = name + '_trigger'
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL('''CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.runner_id={} AND NEW.view_revision>OLD.view_revision AND NEW.revision=OLD.revision THEN
                RAISE EXCEPTION 'FIXTURE_PUBLIC_DISPLAY_REFUSED'; END IF; RETURN NEW; END $$''')
            .format(sql.Identifier(name), sql.Literal(before['runner_id'])))
        cursor.execute(sql.SQL('CREATE TRIGGER {} BEFORE UPDATE ON agent_runners FOR EACH ROW EXECUTE FUNCTION {}()')
                       .format(sql.Identifier(trigger), sql.Identifier(name)))
    try:
        with pytest.raises(RaiseException):
            BrowserHumanRepository(service_database.connect).take(binding, request['assistance_id'])
        after = values['repository'].get(before['runner_id'])
        for key in ('checkpoint', 'revision', 'view_revision', 'public_snapshot', 'attempt', 'worker_id', 'lease_until'):
            assert after[key] == before[key]
        assert service_database.rows('SELECT state,completion_ref FROM bs_browser_assistance_requests WHERE assistance_id=%s',
            (request['assistance_id'],)) == [{'state': 'pending', 'completion_ref': None}]
        assert service_database.rows('SELECT 1 FROM agent_runner_controls WHERE runner_id=%s', (before['runner_id'],)) == []
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL('DROP TRIGGER {} ON agent_runners').format(sql.Identifier(trigger)))
            cursor.execute(sql.SQL('DROP FUNCTION {}()').format(sql.Identifier(name)))


def test_old_snapshot_save_and_park_cannot_replace_the_second_wait_or_mutate_sibling_facts(browser_owner, service_database):
    values = browser_owner
    first, first_request, _ = visible_wait(values, service_database)
    old_snapshot = copy.deepcopy(first['public_snapshot'])
    second_request = assistance(values)
    bound = values['owner'].bind_wait(values['attempt'], first['revision'], execution_id=values['root_id'],
        call_id=values['call_id'], assistance=second_request, worker_boot=values['boot'], browser_epoch=values['epoch'])
    checkpoint = selected_wait(values, service_database, bound, second_request)
    current = values['execution'].save_checkpoint(values['attempt'], bound['revision'], checkpoint,
        {'browserAssistance': card(second_request)})
    second_card = copy.deepcopy(current['public_snapshot']['browserAssistance'])
    assert second_card['assistance_id'] != first_request['assistance_id']
    sibling = copy.deepcopy(checkpoint['execution']['children'])
    saved = values['execution'].save_checkpoint(values['attempt'], current['revision'], checkpoint, old_snapshot)
    assert saved['public_snapshot']['browserAssistance'] == second_card
    assert saved['revision'] == current['revision'] + 1
    assert saved['view_revision'] == current['view_revision'] + 1
    assert saved['checkpoint']['execution']['children'] == sibling
    parked_checkpoint = copy.deepcopy(checkpoint)
    parked_checkpoint['execution']['outcome'] = 'waiting'
    parked = values['execution'].park(values['attempt'], saved['revision'], 'waiting', parked_checkpoint, old_snapshot)
    assert parked['public_snapshot']['browserAssistance'] == second_card
    assert parked['checkpoint']['execution']['children'] == sibling
    assert parked['worker_id'] is None and parked['lease_until'] is None
    assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
        (parked['runner_id'],)) == [{'owner_runner_id': parked['runner_id']}]


def test_corrupt_original_binding_hides_card_and_shows_safe_verification_without_copying_private_resources(browser_owner, service_database):
    values = browser_owner
    original, _, _ = visible_wait(values, service_database)
    checkpoint = copy.deepcopy(original['checkpoint'])
    reference = checkpoint['execution']['resources']['browser_runs'][values['call_id']]
    reference['owner_boot_id'] = 'fictional-untrusted-boot'
    reference['private_probe'] = {'do_not_display': 'fictional-private-resource'}
    saved = values['execution'].save_checkpoint(values['attempt'], original['revision'], checkpoint,
                                               copy.deepcopy(original['public_snapshot']))
    assert 'browserAssistance' not in saved['public_snapshot']
    assert saved['public_snapshot']['waiting']['kind'] == 'verification'
    assert saved['public_snapshot']['waiting']['question']
    assert 'fictional-private-resource' not in str(saved['public_snapshot'])
    assert saved['checkpoint'] == checkpoint
    assert original['checkpoint'] != checkpoint
    assert service_database.rows('SELECT runtime_state FROM bs_browser_runs WHERE runner_id=%s',
        (saved['runner_id'],)) == [{'runtime_state': 'live'}]
