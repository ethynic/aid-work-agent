"""Device authorization and durable result settlement for scheduled WeCom sends."""

from decimal import Decimal, InvalidOperation

from loguru import logger

from src.config.settings import settings
from src.core.cache_utils import invalidate_tenant_cache
from src.db.client_binding_db import ClientUsageLogDB
from src.db.database import get_db_connection
from src.local_tools import repository
from src.local_tools.wecom_send_status import RECEIPT_ID, SendStatusScopeError, original_scope, valid_invocation_id

from .adapters import decode_locator
from .service import ConversationServiceError

SEND_TOOL = 'wecom_message_send'
SEND_KIND = repository.WECOM_SCHEDULED_SEND
READ_TOOL = 'wecom_send_status'
DEFAULT_PRICE = Decimal('0.50')
CENT = Decimal('0.01')
# The stable-session metadata and local database checks, pre-Enter driver
# rejections, and the Runtime's old-plugin capability check can all stop a send.
WECOM_NO_SEND_CODES = frozenset({
    'TARGET_REF_STALE', 'TARGET_NOT_FOUND', 'TARGET_AMBIGUOUS', 'IDENTITY_UNCONFIRMED',
    'CONFIG_MISSING', 'CONTENT_UNAVAILABLE', 'LOCALDB_UNAVAILABLE', 'KEY_SCAN_FAILED',
    'SNAPSHOT_UNSTABLE', 'VERSION_UNSUPPORTED', 'WECOM_NOT_FOUND', 'WINDOW_AMBIGUOUS',
    'TOOL_NOT_SUPPORTED',
})


def _price_in_tx(cursor):
    cursor.execute('SELECT unit_price FROM wecom_scheduled_send_settings WHERE singleton')
    row = cursor.fetchone()
    return row['unit_price'] if row and row['unit_price'] is not None else DEFAULT_PRICE


def get_unit_price():
    with get_db_connection() as conn:
        return _price_in_tx(conn.cursor())


def set_unit_price(value):
    try:
        price = Decimal(str(value)) if value is not None else None
        if price is not None and (not price.is_finite() or not 0 <= price <= Decimal('9999999999.99')
                                  or price != price.quantize(CENT)):
            raise InvalidOperation
    except InvalidOperation as error:
        raise ConversationServiceError('PRICE_INVALID', '单价须为非负积分，最多两位小数', 400) from error
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO wecom_scheduled_send_settings (singleton,unit_price)
                          VALUES (TRUE,%s) ON CONFLICT (singleton)
                          DO UPDATE SET unit_price=EXCLUDED.unit_price''', (price,))
        conn.commit()
    return price.quantize(CENT) if price is not None else DEFAULT_PRICE


def _invocation_session(execution_id):
    return f'wecom_scheduled_send:{execution_id}'


def _send_binding(execution_id):
    return {'version': 1, 'execution_id': execution_id}


def _bound_send(execution, invocation):
    return (str(execution['invocation_id']) == str(invocation['id'])
            and execution['tenant_id'] == invocation['tenant_id']
            and execution['user_id'] == invocation['user_id']
            and str(execution['device_id']) == str(invocation['device_id'])
            and invocation['tool_name'] == SEND_TOOL and invocation['provider_key'] == 'wecom'
            and invocation['business_kind'] == SEND_KIND
            and invocation['session_id'] == _invocation_session(execution['id'])
            and invocation['business_ref'] == _send_binding(execution['id'])
            and invocation['arguments_json'] == {'session': execution['session'], 'text': execution['text']})


def _locked_execution(cursor, tenant_id, execution_id):
    cursor.execute('''SELECT * FROM bs_wecom_scheduled_sends WHERE tenant_id=%s AND id=%s FOR UPDATE''',
                   (tenant_id, execution_id))
    return cursor.fetchone()


def _finish_unstarted_invocation(cursor, tenant_id, invocation_id, state, code):
    if not invocation_id:
        return None
    cursor.execute('''UPDATE local_tool_invocations SET state=%s,effect='none',error_code=%s,
                             finished_at=clock_timestamp(),lease_expires_at=NULL
                       WHERE tenant_id=%s AND id=%s AND business_kind=%s AND started_at IS NULL
                         AND state IN ('queued','claimed','cancel_requested','unknown') RETURNING *''',
                   (state, code, tenant_id, invocation_id, SEND_KIND))
    return cursor.fetchone()


def _reject_start(cursor, row, status, code):
    cursor.execute('''UPDATE bs_wecom_scheduled_sends SET status=%s,error_code=%s,
                            finished_at=clock_timestamp() WHERE tenant_id=%s AND id=%s''',
                   (status, code, row['tenant_id'], row['id']))
    logger.warning(f'后端日志：企微定时发送授权拒绝 execution={row["id"]} invocation={row["invocation_id"]} code={code}')
    return _finish_unstarted_invocation(cursor, row['tenant_id'], row['invocation_id'], status, code)


def _defer_expired_claim(cursor, row):
    """Invalidate the old lease, then let the queue verify the original account again."""
    cursor.execute('''UPDATE local_tool_invocations SET state='unknown',effect='none',claim_token_hash=NULL,
                             claimed_at=NULL,lease_expires_at=NULL,finished_at=clock_timestamp()
                       WHERE tenant_id=%s AND id=%s RETURNING *''', (row['tenant_id'], row['invocation_id']))
    invocation = cursor.fetchone()
    cursor.execute('''UPDATE bs_wecom_scheduled_sends SET status='queued'
                       WHERE tenant_id=%s AND id=%s''', (row['tenant_id'], row['id']))
    return invocation


def authorize_in_tx(cursor, owner):
    """Caller holds operation admission and the authenticated device lock."""
    binding = owner.get('business_ref') or {}
    execution_id = binding.get('execution_id')
    if type(execution_id) is not int:
        return None
    row = _locked_execution(cursor, owner['tenant_id'], execution_id)
    if not row or not _bound_send(row, owner):
        return None
    # Pause, authorization and expiry serialize on the same execution row.
    cursor.execute('''SELECT * FROM local_tool_invocations WHERE tenant_id=%s AND id=%s FOR UPDATE''',
                   (owner['tenant_id'], owner['id']))
    invocation = cursor.fetchone()
    if not invocation or invocation['claim_token_hash'] != owner['claim_token_hash']:
        return None
    if invocation['state'] != 'claimed' or row['status'] != 'dispatched':
        return invocation
    cursor.execute('''SELECT status FROM scheduled_tasks WHERE task_id=%s AND tenant_id=%s AND user_id=%s''',
                   (row['task_id'], row['tenant_id'], row['user_id']))
    plan = cursor.fetchone()
    if not plan or plan['status'] != 'active':
        return _reject_start(cursor, row, 'cancelled', 'PLAN_INACTIVE')
    target = decode_locator(row['session'])
    if not target or target[0] != row['account']:
        return _reject_start(cursor, row, 'failed', 'WECOM_SEND_SCOPE_INVALID')
    repository.assert_device_operations_settled(cursor, row['tenant_id'], str(row['device_id']))
    price = Decimal('0.00')
    if not settings.deployment.private_cloud:
        cursor.execute('SELECT status,credit_balance FROM tenants WHERE tenant_id=%s FOR UPDATE',
                       (row['tenant_id'],))
        tenant = cursor.fetchone()
        if not tenant or tenant['status'] != 'active':
            return _reject_start(cursor, row, 'failed', 'TENANT_UNAVAILABLE')
        price = _price_in_tx(cursor)
    cursor.execute('''SELECT clock_timestamp() AS now, lease_expires_at > clock_timestamp() AS lease_valid
                       FROM local_tool_invocations WHERE id=%s AND tenant_id=%s''',
                   (invocation['id'], row['tenant_id']))
    clock = cursor.fetchone()
    if clock['now'] > row['deadline_at']:
        return _reject_start(cursor, row, 'expired', 'SEND_WINDOW_EXPIRED')
    if not clock['lease_valid']:
        return _defer_expired_claim(cursor, row)
    if price > 0 and tenant['credit_balance'] < price:
        return _reject_start(cursor, row, 'failed', 'NO_CREDIT')
    cursor.execute('''UPDATE bs_wecom_scheduled_sends SET status='running',started_at=%s,unit_price=%s
                       WHERE id=%s AND tenant_id=%s''', (clock['now'], price, execution_id, row['tenant_id']))
    cursor.execute('''UPDATE local_tool_invocations SET state='running',started_at=%s
                       WHERE id=%s AND tenant_id=%s RETURNING *''',
                   (clock['now'], invocation['id'], row['tenant_id']))
    return cursor.fetchone()


def retire_in_tx(cursor, condition, params, status, code):
    """Cancel or expire only calls for which /started has granted no authority."""
    cursor.execute(f'''UPDATE bs_wecom_scheduled_sends SET status=%s,error_code=%s,finished_at=clock_timestamp()
                        WHERE status IN ('queued','dispatched') AND {condition}
                        RETURNING tenant_id,invocation_id''', (status, code, *params))
    for row in cursor.fetchall():
        _finish_unstarted_invocation(cursor, row['tenant_id'], row['invocation_id'], status, code)


def invocation_in_tx(cursor, row, *, read=False):
    tenant_id, user_id, device_id = row['tenant_id'], row['user_id'], str(row['device_id'])
    session_id = _invocation_session(row['id'])
    if read:
        scope = original_scope(cursor, tenant_id, user_id, session_id, str(row['invocation_id']), device_id=device_id)
        tool, kind = READ_TOOL, READ_TOOL
        arguments = {'receipt_id': scope['receipt_id']}
        binding = {'version': 1, 'send_invocation_id': str(row['invocation_id'])}
    else:
        tool, kind = SEND_TOOL, SEND_KIND
        arguments = {'session': row['session'], 'text': row['text']}
        binding = _send_binding(row['id'])
    return repository.create_invocation_in_tx(
        cursor, tenant_id, user_id, device_id, tool, arguments, session_id, provider_key='wecom',
        business_kind=kind, business_ref=binding, dedupe_key=session_id, verify_binding=True)


def _result_data(invocation):
    return (invocation.get('result_json') or {}).get('data') or {}


def _no_send_receipt(stored, receipt):
    """Apply the same no-write proof to timely and stored late Runtime receipts."""
    effect, code = receipt.get('effect', stored['effect']), receipt.get('code')
    data = receipt.get('data') or {}
    if receipt.get('success') is not False or effect != 'none' or data.get('receipt_invalidation_failed', False) is not False:
        return False
    # The Host copies the CLI operation UUID only from a returned Provider
    # envelope. Its transport/crash fallback has no run ID and writes unknown.
    return (code in WECOM_NO_SEND_CODES or repository.result_proves_completion(False, code, effect)
            or code == 'INTERNAL_ERROR' and valid_invocation_id(data.get('run_id')))


def _stopped_with_uncertain_effect(payload, session):
    # This shape is emitted only after the matched CLI send driver has returned
    # and the operation reached post-driver local verification. The generic
    # Runtime timeout/crash and the earlier write-driver error have no such shape.
    data = payload.get('data') or {}
    receipt_id = data.get('receipt_id')
    return (payload.get('code') == 'EXECUTION_UNKNOWN' and data.get('session') == session
            and isinstance(receipt_id, str) and RECEIPT_ID.fullmatch(receipt_id) is not None
            and data.get('receipt_invalidation_failed', False) is False
            and data.get('sent_verification') == {'method': 'skipped', 'result': 'unverified'}
            and isinstance(data.get('localdb_verification'), list))


def settle_execution(tenant_id, execution_id):
    """Keep physical release separate from confirmation and from the billing retry."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = _locked_execution(cursor, tenant_id, execution_id)
        if not row or not row['invocation_id'] or row['status'] not in ('dispatched', 'running', 'unknown', 'succeeded'):
            return
        cursor.execute('''SELECT *, lease_expires_at<clock_timestamp() AS lease_expired,
                                 clock_timestamp() AS observed_at
                            FROM local_tool_invocations WHERE id=%s AND tenant_id=%s FOR UPDATE''',
                       (row['invocation_id'], tenant_id))
        stored = cursor.fetchone()
        if not stored or not _bound_send(row, stored) or stored['state'] not in repository.TERMINAL_STATES:
            return
        if (row['status'] == 'dispatched' and row['started_at'] is None and stored['started_at'] is None
                and stored['state'] == 'unknown' and stored['lease_expired']
                and stored['claimed_at'] is not None and stored['result_json'] is None):
            if stored['observed_at'] <= row['deadline_at']:
                _defer_expired_claim(cursor, row)
            else:
                _reject_start(cursor, row, 'expired', 'SEND_WINDOW_EXPIRED')
            conn.commit()
            return
        observed = repository.observed_invocation(stored)
        data = _result_data(observed)
        applied = (observed['state'] == 'succeeded' and observed['effect'] == 'applied'
                   and data.get('verification') == 'local-record' and data.get('session') == row['session'])
        receipt = stored.get('result_json') or {}
        receipt = receipt.get('_late_result') or receipt
        no_send = _no_send_receipt(stored, receipt)
        never_started = row['started_at'] is None
        released = (never_started or no_send or observed['state'] == 'succeeded'
                    and repository.operation_completion_known(stored)
                    or _stopped_with_uncertain_effect(receipt, row['session']))
        confirmed = applied or row['status'] == 'succeeded' or _reconciled_in_tx(cursor, row)
        status = 'succeeded' if confirmed else 'failed' if never_started or no_send else 'unknown'
        receipt_code = receipt.get('code') if receipt.get('success') is False else None
        code = None if confirmed else receipt_code or observed['error_code'] or (
            'SEND_NOT_STARTED' if never_started else 'EXECUTION_UNKNOWN')
        cursor.execute('''UPDATE bs_wecom_scheduled_sends SET status=%s,error_code=%s,
                                released_at=COALESCE(released_at,CASE WHEN %s THEN clock_timestamp() END),
                                finished_at=COALESCE(finished_at,clock_timestamp())
                           WHERE id=%s AND tenant_id=%s''', (status, code, released, execution_id, tenant_id))
        conn.commit()
    if code and status != row['status']:
        logger.warning(f'后端日志：企微定时发送结果 execution={execution_id} invocation={row["invocation_id"]} '
                       f'status={status} code={code}')
    if confirmed:
        _bill(tenant_id, execution_id)


def _reconciled_in_tx(cursor, row):
    if row['started_at'] is None or not row['reconcile_invocation_id']:
        return False
    cursor.execute('''SELECT * FROM local_tool_invocations WHERE id=%s AND tenant_id=%s AND user_id=%s''',
                   (row['reconcile_invocation_id'], row['tenant_id'], row['user_id']))
    read = cursor.fetchone()
    if not read:
        return False
    read = repository.observed_invocation(read)
    data = _result_data(read)
    valid = (read['state'] == 'succeeded' and read['effect'] == 'none' and read['tool_name'] == READ_TOOL
            and read['provider_key'] == 'wecom' and read['business_kind'] == READ_TOOL
            and str(read['device_id']) == str(row['device_id']) and read['session_id'] == _invocation_session(row['id'])
            and read['business_ref'] == {'version': 1, 'send_invocation_id': str(row['invocation_id'])}
            and data.get('send_status') == 'confirmed' and bool(data.get('receipt_id'))
            and read['arguments_json'] == {'receipt_id': data['receipt_id']})
    if not valid:
        return False
    try:
        scope = original_scope(cursor, row['tenant_id'], row['user_id'], read['session_id'],
                               str(row['invocation_id']), device_id=row['device_id'])
    except SendStatusScopeError:
        return False
    return scope['receipt_id'] == data['receipt_id']


def _bill(tenant_id, execution_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = _locked_execution(cursor, tenant_id, execution_id)
        if not row or row['status'] != 'succeeded' or row['billed_at'] is not None:
            return
        price = row['unit_price']
        if price > 0:
            balance = ClientUsageLogDB.insert_tool_usage_row(
                cursor, tenant_id=tenant_id, tool_name=SEND_TOOL, credit_cost=price,
                invocation_id=str(row['invocation_id']), device_id=str(row['device_id']),
                session_id=_invocation_session(execution_id), user_id=row['user_id'])
            if balance is None:
                raise RuntimeError('定时企微发送扣费时租户不存在')
        cursor.execute('''UPDATE bs_wecom_scheduled_sends SET billed_at=clock_timestamp()
                           WHERE tenant_id=%s AND id=%s''', (tenant_id, execution_id))
        cursor.execute('''UPDATE local_tool_invocations SET credit_cost=%s
                           WHERE tenant_id=%s AND id=%s AND business_kind=%s''',
                       (price, tenant_id, row['invocation_id'], SEND_KIND))
        conn.commit()
    if price > 0:
        try:
            invalidate_tenant_cache(tenant_id)
        except Exception:
            logger.opt(exception=True).warning(f'后端日志：企微定时发送扣费后缓存失效失败 tenant={tenant_id}')
