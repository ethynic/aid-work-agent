"""Narrow admission/result rules for the authenticated Host-only observation."""

from uuid import UUID
from psycopg2.extras import Json
from . import repository


def observation_scope(cursor, invocation, *, dispatch=False):
    binding = invocation.get('business_ref') or {}
    if (invocation.get('business_kind') != 'agent_runner_recovery'
            or invocation.get('provider_key') != 'runtime-recovery'
            or invocation.get('tool_name') != 'runtime_recovery_observe'
            or binding.get('version') not in {1, 2, 3}
            or binding.get('device_id') != str(invocation.get('device_id'))):
        return None
    if dispatch and binding['version'] == 3 and invocation.get('id'):
        deadline = invocation.get('deadline_at')
        if not deadline:
            return None
        cursor.execute('SELECT %s::timestamptz>clock_timestamp() AS unexpired', (deadline,))
        if not cursor.fetchone()['unexpired']:
            return None
    ids = binding.get('original_invocation_ids')
    if not isinstance(ids, list) or not ids or len(ids) > 128 or len(set(ids)) != len(ids):
        return None
    cursor.execute('SELECT checkpoint,user_id,session_id FROM agent_runners WHERE runner_id=%s AND tenant_id=%s',
        (binding.get('runner_id'), invocation['tenant_id']))
    row = cursor.fetchone()
    if (not row or row['user_id'] != invocation['user_id'] or row['session_id'] != invocation['session_id']
            or not set(ids) <= repository.owned_invocation_ids((row['checkpoint'] or {}).get('execution') or {})):
        return None
    if dispatch and binding['version'] == 3 and invocation.get('id'):
        current = (row['checkpoint'] or {}).get('local_verification') or {}
        if current.get('invocation_id') != str(invocation['id']) or any(current.get(key) != binding.get(key)
                for key in ('runner_id', 'version', 'scope_id', 'request_id', 'device_id', 'original_invocation_ids')):
            return None
    cursor.execute('SELECT id,provider_key FROM local_tool_invocations WHERE tenant_id=%s AND user_id=%s AND session_id=%s AND device_id=%s AND id=ANY(%s::uuid[])',
        (invocation['tenant_id'], invocation['user_id'], invocation['session_id'], invocation['device_id'], ids))
    originals = cursor.fetchall()
    if {str(item['id']) for item in originals} != set(ids):
        return None
    arguments = invocation.get('arguments_json') or {}
    if (arguments.get('version') != binding['version'] or arguments.get('scope_id') != binding.get('scope_id')
            or arguments.get('device_id') != binding['device_id'] or arguments.get('original_invocation_ids') != ids):
        return None
    provider = arguments.get('provider_key', 'boss-recruiting')
    if provider not in {'boss-recruiting', 'wecom'} or any(
            (item.get('provider_key') or 'boss-recruiting') != provider for item in originals):
        return None
    if binding['version'] == 3 and arguments.get('stop_resources') is not True:
        return None
    return {**binding, 'invocation_id': str(invocation['id']) if invocation.get('id') else None}


def valid_uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def write_result(invocation_id, tenant_id, claim_token_hash, success, code, message, effect, data, retryable, device_id):
    """Lock Runner -> admission -> receipt, atomically apply only the current exact scope."""
    from src.services.agent_runner.persistence_limits import capped_checkpoint_dumps
    with repository.get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute('SELECT business_ref FROM local_tool_invocations WHERE id=%s AND tenant_id=%s', (invocation_id, tenant_id))
        initial = cursor.fetchone()
        if not initial:
            return None
        binding = initial['business_ref'] or {}
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s AND tenant_id=%s FOR UPDATE', (binding.get('runner_id'), tenant_id))
        runner = cursor.fetchone()
        repository.lock_operation_admission(cursor, tenant_id)
        cursor.execute('SELECT * FROM local_tool_invocations WHERE id=%s AND tenant_id=%s FOR UPDATE', (invocation_id, tenant_id))
        row = dict(cursor.fetchone() or {})
        if (not runner or row.get('claim_token_hash') != claim_token_hash or device_id is None
                or str(row.get('device_id')) != str(device_id) or not observation_scope(cursor, row)):
            return None
        if row['state'] in repository.TERMINAL_STATES and row.get('result_json') is not None:
            return row
        observation = (data or {}).get('recovery_observation')
        target = (row.get('arguments_json') or {}).get('browser_target_ref')
        target_unavailable = (row.get('arguments_json') or {}).get('page_target_unavailable') is True
        version = binding['version']
        summary = observation.get('page_summary') if isinstance(observation, dict) else None
        if version in {2, 3}:
            summary_valid = (isinstance(summary, dict)
                and set(summary) <= {'observed', 'page_kind', 'result', 'filter_count', 'panel_open', 'count_result'}
                and summary.get('observed') is True and summary.get('page_kind') in {'recommend', 'chat', 'other'}
                and summary.get('result') in {'match', 'mismatch', 'unknown'}
                and ('filter_count' not in summary or summary['filter_count'] is None
                    or type(summary['filter_count']) is int and summary['filter_count'] >= 0)
                and ('panel_open' not in summary or type(summary['panel_open']) is bool)
                and ('count_result' not in summary or summary['count_result'] in {'match', 'mismatch', 'unknown'}))
        else:
            summary_valid = summary in [dict(observed=True, page_kind=kind) for kind in ('chat', 'other')]
        page_observed = (isinstance(observation, dict) and success is True
            and summary_valid and valid_uuid(observation.get('browser_target_ref')))
        page_failed = (isinstance(observation, dict) and success is False
            and summary == (dict(observed=False, page_kind='unavailable', result='unknown') if version in {2, 3}
                else dict(observed=False, page_kind='unavailable'))
            and observation.get('page_error_code') in {'WRONG_PAGE', 'CHROME_UNAVAILABLE', 'UI_CHANGED', 'LOCAL_PAGE_OBSERVATION_FAILED'}
            and code == observation['page_error_code']
            and (observation.get('browser_target_ref') == target if target else observation.get('browser_target_ref') is None))
        envelope_valid = (effect == 'none' and isinstance(observation, dict)
            and observation.get('version') == version and observation.get('scope_id') == binding.get('scope_id')
            and observation.get('device_id') == binding['device_id']
            and observation.get('original_invocation_ids') == binding['original_invocation_ids']
            and (observation.get('target_relation') == 'unavailable' and observation.get('browser_target_ref') is None if target_unavailable
                else observation.get('target_relation') == 'original' and observation.get('browser_target_ref') == target if target
                else observation.get('target_relation') == 'current_unbound')
            and (page_observed or page_failed))
        wecom = (row.get('arguments_json') or {}).get('provider_key') == 'wecom'
        if wecom:
            envelope_valid = (effect == 'none' and isinstance(observation, dict)
                and observation.get('version') == version and version in {2, 3}
                and observation.get('provider_scope') == 'wecom'
                and observation.get('scope_id') == binding.get('scope_id')
                and observation.get('device_id') == binding['device_id']
                and observation.get('original_invocation_ids') == binding['original_invocation_ids']
                and observation.get('target_relation') == 'not_applicable'
                and observation.get('browser_target_ref') is None
                and summary == {'observed': False, 'page_kind': 'not_applicable', 'result': 'unknown'})
        # Accept in-flight pre-upgrade v1 receipts without redefining their proof.
        # v2 pure observation cannot manufacture stop evidence for an unbound old call.
        stop_valid = (envelope_valid and version == 1 and observation.get('resource_stopped') is True
            and observation.get('stop_evidence') == 'protected_reboot' and valid_uuid(observation.get('runtime_generation')))
        stop_valid = stop_valid or (envelope_valid and version == 3
            and observation.get('resource_stopped') is True
            and observation.get('stop_evidence') == 'supervised_device_boundary'
            and observation.get('resource_scope') == 'managed_provider_pool'
            and valid_uuid(observation.get('runtime_generation')))
        stop_errors = {'LOCAL_RECOVERY_STOP_UNPROVED', 'LOCAL_RECOVERY_BUSY', 'LOCAL_RECOVERY_LEGACY_UNMANAGED',
            'LOCAL_RECOVERY_SUPERVISION_UNAVAILABLE', 'LOCAL_RECOVERY_NO_EXECUTION_EVIDENCE',
            'LOCAL_RECOVERY_EVIDENCE_INVALID', 'LOCAL_RECOVERY_EVIDENCE_CHANGED', 'LOCAL_RECOVERY_OTHER_SESSION',
            'LOCAL_RECOVERY_PROCESS_CHECK_UNAVAILABLE'}
        read_valid = (envelope_valid and version in {2, 3} and observation.get('resource_stopped') is False
            and observation.get('stop_error_code') in stop_errors)
        valid = bool((stop_valid or read_valid) and (page_observed or wecom and envelope_valid))
        # No arbitrary Provider text/page content enters the public projection.
        checkpoint = dict(runner['checkpoint'] or {})
        current = checkpoint.get('local_verification') or {}
        if (current.get('invocation_id') == invocation_id and all(current.get(key) == binding.get(key)
                for key in ('runner_id', 'version', 'scope_id', 'request_id', 'device_id', 'original_invocation_ids'))):
            state = 'succeeded' if valid else 'failed'
            checkpoint['local_verification'] = {**current, 'state': state,
                'resource_stopped': bool(stop_valid), 'page_observed': bool(valid and not wecom),
                'stop_error_code': observation.get('stop_error_code') if read_valid else None,
                'page_summary': summary if valid else None,
                'target_relation': observation.get('target_relation') if envelope_valid else None,
                'error_code': None if valid else code if code in {
                    'LOCAL_RECOVERY_RESTART_REQUIRED', 'LOCAL_RECOVERY_STOP_UNPROVED', 'LOCAL_RECOVERY_REBOOT_REQUIRED',
                    'PROVIDER_NOT_AVAILABLE', 'TOOL_NOT_ALLOWED', 'WRONG_PAGE', 'CHROME_UNAVAILABLE', 'UI_CHANGED', 'LOCAL_PAGE_OBSERVATION_FAILED'} else 'LOCAL_VERIFICATION_FAILED'}
            if stop_valid:
                checkpoint['verified_local_invocations'] = sorted(set(checkpoint.get('verified_local_invocations', [])) | set(binding['original_invocation_ids']))
            cursor.execute('UPDATE agent_runners SET checkpoint=%s::jsonb,view_revision=view_revision+1,updated_at=clock_timestamp() WHERE runner_id=%s',
                (capped_checkpoint_dumps(checkpoint), binding['runner_id']))
            from src.services.agent_runner.event_repository import EventRepository
            EventRepository.notify_in_tx(cursor, dict(runner))
        result = {'success': bool(valid), 'code': code, 'message': message, 'data': data, 'retryable': retryable}
        cursor.execute('UPDATE local_tool_invocations SET state=%s,effect=\'none\',result_json=%s,error_code=%s,finished_at=NOW(),lease_expires_at=NULL,credit_cost=0 WHERE id=%s RETURNING *',
            ('succeeded' if valid else 'failed', Json(result), None if valid else code or 'LOCAL_VERIFICATION_FAILED', invocation_id))
        updated = dict(cursor.fetchone())
        connection.commit()
        return updated
