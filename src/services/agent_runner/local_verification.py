"""Explicit Host observation, separate from restoring an unknown business action."""

from uuid import uuid4
from datetime import timedelta
from .contracts import RunnerError
from .ownership import lock_runner
from .persistence_limits import capped_checkpoint_dumps
from .repository import decoded

CAPABILITY = 'runtime_recovery_observe_v2'
STOP_CAPABILITY = 'runtime_recovery_stop_v1'


def verify_local(repository, principal, runner_id, request_id, *, stop_resources=False):
    from src.local_tools import repository as local
    from .local_recovery import _cancel_facts
    with repository.connection_factory() as connection:
        cursor = connection.cursor()
        row = lock_runner(cursor, runner_id)
        repository.assert_owner(principal, row)
        checkpoint = row.get('checkpoint') or {}
        if row['session_kind'] != 'web' or row['source'] != 'chat':
            raise RunnerError('LOCAL_VERIFICATION_FORBIDDEN', 403)
        if (row.get('worker_id') is not None or row.get('lease_until') is not None
                or checkpoint.get('detached_local_wait') and row['status'] not in {'completed', 'cancelled', 'failed'}):
            raise RunnerError('LOCAL_VERIFICATION_NOT_READY', 409)
        from .public_view import local_recovery_applicable, with_local_operation_facts
        if not checkpoint.get('detached_local_wait') and not local_recovery_applicable(row):
            raise RunnerError('LOCAL_VERIFICATION_NOT_APPLICABLE', 409)
        local.lock_operation_admission(cursor, row['tenant_id'])
        cursor.execute('SELECT id,business_ref FROM local_tool_invocations WHERE tenant_id=%s AND business_kind=%s AND dedupe_key=%s',
            (row['tenant_id'], 'agent_runner_recovery', runner_id + ':' + request_id))
        accepted = cursor.fetchone()
        if accepted:
            if ((accepted.get('business_ref') or {}).get('version') == 3) != stop_resources:
                raise RunnerError('LOCAL_VERIFICATION_REQUEST_MISMATCH', 409)
            return with_local_operation_facts(cursor, row)  # Observe current facts, never replace scope.
        previous = checkpoint.get('local_verification') or {}
        if previous.get('request_id') == request_id:
            return with_local_operation_facts(cursor, row)
        facts = _cancel_facts(cursor, row, request=False)
        if any(fact.get('kind') == 'local' and not fact['completion_known']
                and fact.get('state') not in local.TERMINAL_STATES for fact in facts):
            raise RunnerError('LOCAL_VERIFICATION_ORIGINAL_ACTIVE', 409)
        ids = sorted(str(fact['id']) for fact in facts if fact.get('kind') == 'local'
            and fact.get('holdable') and not fact['completion_known']
            and str(fact['id']) not in checkpoint.get('verified_local_invocations', []))
        if not ids:
            # A late receipt may settle the hold after the page rendered its button.
            # Treat that race as an idempotent refresh, not an error or new action.
            return with_local_operation_facts(cursor, row)
        if previous.get('state') in {'queued', 'claimed', 'running'}:
            cursor.execute('SELECT state,deadline_at,deadline_at>clock_timestamp() AS unexpired FROM local_tool_invocations WHERE id=%s AND tenant_id=%s',
                (previous['invocation_id'], row['tenant_id']))
            pending = cursor.fetchone()
            if pending and pending['state'] not in local.TERMINAL_STATES and pending['unexpired']:
                raise RunnerError('LOCAL_VERIFICATION_PENDING', 409)
        cursor.execute('SELECT * FROM local_tool_invocations WHERE tenant_id=%s AND id=ANY(%s::uuid[])',
            (row['tenant_id'], ids))
        originals = [dict(item) for item in cursor.fetchall()]
        devices = {str(item['device_id']) for item in originals}
        if len(originals) != len(ids) or len(devices) != 1:
            raise RunnerError('LOCAL_VERIFICATION_DEVICE_SCOPE_REQUIRED', 409)
        device_id = devices.pop()
        cursor.execute('SELECT capabilities_json FROM local_tool_devices WHERE id=%s AND tenant_id=%s AND user_id=%s AND status=\'active\' FOR SHARE',
            (device_id, row['tenant_id'], row['user_id']))
        device = cursor.fetchone()
        required_capability = STOP_CAPABILITY if stop_resources else CAPABILITY
        providers = {(item.get('provider_key') or 'boss-recruiting') for item in originals}
        if len(providers) != 1 or not providers <= {'boss-recruiting', 'wecom'}:
            raise RunnerError('LOCAL_VERIFICATION_PROVIDER_UNSUPPORTED', 409)
        provider = next(iter(providers))
        if provider == 'wecom' and 'runtime_recovery_wecom_v1' not in (
                ((device or {}).get('capabilities_json') or {}).get('capabilities') or []):
            raise RunnerError('LOCAL_VERIFICATION_RUNTIME_UPGRADE_REQUIRED', 409)
        if not device or required_capability not in ((device['capabilities_json'] or {}).get('capabilities') or []):
            raise RunnerError('LOCAL_VERIFICATION_RUNTIME_UPGRADE_REQUIRED', 409)
        if stop_resources:
            if any((item.get('provider_key') or 'boss-recruiting') != provider for item in originals):
                raise RunnerError('LOCAL_RECOVERY_PROVIDER_UNSUPPORTED', 409)
            try:
                local.assert_device_stop_available(cursor, row['tenant_id'], device_id)
            except local.DeviceOperationUnresolved as error:
                raise RunnerError('LOCAL_RECOVERY_BUSY', 409) from error
        targets = set()
        target_unavailable = False
        for item in originals:
            receipt = item.get('result_json') or {}
            for value in (item.get('arguments_json') or {}, receipt.get('data') or {},
                    (receipt.get('_late_result') or {}).get('data') or {}):
                target = value.get('_boss_target_ref') or value.get('browser_target_ref')
                if target is not None:
                    from src.local_tools.recovery_observation import valid_uuid
                    if not valid_uuid(target):
                        if stop_resources:
                            target_unavailable = True
                            continue
                        raise RunnerError('LOCAL_VERIFICATION_TARGET_SCOPE_REQUIRED', 409)
                    targets.add(target)
        if len(targets) > 1:
            if stop_resources:
                target_unavailable = True
            else:
                raise RunnerError('LOCAL_VERIFICATION_TARGET_SCOPE_REQUIRED', 409)
        scope_id = previous.get('scope_id') if previous.get('original_invocation_ids') == ids and previous.get('device_id') == device_id and previous.get('version') == (3 if stop_resources else 2) else str(uuid4())
        version = 3 if stop_resources else 2
        arguments = {'version': version, 'scope_id': scope_id, 'device_id': device_id, 'original_invocation_ids': ids,
            'browser_target_ref': None if target_unavailable else next(iter(targets), None)}
        if provider == 'wecom':
            arguments['provider_key'] = 'wecom'
        if stop_resources:
            arguments['stop_resources'] = True
            if target_unavailable:
                arguments['page_target_unavailable'] = True
        # Expectations are derived from the original accepted operation only.
        # No arbitrary page script or automatic replay is accepted from the UI.
        if len(originals) == 1:
            original = originals[0]
            expected = None
            if original['tool_name'] == 'boss_goto':
                target = (original.get('arguments_json') or {}).get('target')
                if target in {'recommend', 'chat'}:
                    expected = {'kind': 'navigation', 'target': target}
            elif original['tool_name'] == 'boss_clear_filter':
                expected = {'kind': 'clear_filter'}
            elif original['tool_name'] == 'boss_filter':
                expected = {'kind': 'filter'}
                receipt = local.observed_invocation(original).get('result_json') or {}
                count = (receipt.get('data') or {}).get('filter_count')
                if type(count) is int and count >= 0:
                    expected['count'] = count
            if expected:
                arguments['expectation'] = expected
        binding = {'runner_id': runner_id, 'version': version, 'scope_id': scope_id, 'request_id': request_id,
            'original_invocation_ids': ids, 'device_id': device_id}
        cursor.execute('SELECT clock_timestamp() AS database_now')
        deadline = cursor.fetchone()['database_now'] + timedelta(minutes=5)
        invocation_id = local.create_invocation_in_tx(cursor, row['tenant_id'], row['user_id'],
            device_id, 'runtime_recovery_observe', arguments, row['session_id'],
            provider_key='runtime-recovery', business_kind='agent_runner_recovery',
            business_ref=binding, dedupe_key=runner_id + ':' + request_id,
            deadline_at=deadline, verify_binding=True)
        checkpoint['local_verification'] = {**binding, 'invocation_id': invocation_id, 'state': 'queued', 'deadline_at': deadline.isoformat()}
        cursor.execute('UPDATE agent_runners SET checkpoint=%s::jsonb,view_revision=view_revision+1,updated_at=clock_timestamp() WHERE runner_id=%s RETURNING *',
            (capped_checkpoint_dumps(checkpoint), runner_id))
        result = decoded(cursor.fetchone())
        from .event_repository import EventRepository
        result = EventRepository.notify_in_tx(cursor, row, after=result)
        connection.commit()
        return result
