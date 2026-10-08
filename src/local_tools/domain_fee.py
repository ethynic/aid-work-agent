"""Cursor-only business fee effect for a durable local domain phase.

The domain adapter chooses the fee name, amount, original invocation and item
ordinal. Its execution owner commits this writer and that phase's receipt in
one transaction. This module never opens a connection or prices an operation.
"""

import math

import psycopg2
from loguru import logger


def write_domain_fee(cursor, row, intent):
    """Preserve original positive-fee and nonblocking SQL-failure semantics.

    A savepoint proves an ordinary SQL billing failure had no effect before the
    owner records an unpaid result. Broken connections and checkpoint/fencing
    failures remain authority failures; the outer owner must not swallow them.
    Recruiting insertion is a later independent phase: its failure cannot roll
    back a committed recognition fee.
    """
    from src.db.client_binding_db import ClientUsageLogDB

    amount = math.ceil(float(intent['credit_cost']) * 100) / 100
    if amount <= 0:
        return {'credit_cost':0.0, 'balance_after':None, 'applied':False,
                'skipped':True}
    cursor.execute('SAVEPOINT local_domain_fee')
    try:
        balance = ClientUsageLogDB.insert_tool_usage_row(cursor,
            tenant_id=row['tenant_id'], user_id=row['user_id'], session_id=row['session_id'],
            tool_name=intent['billing_tool_name'], credit_cost=amount,
            invocation_id=intent['invocation_id'], device_id=intent.get('device_id'))
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        raise
    except psycopg2.DatabaseError as error:
        cursor.execute('ROLLBACK TO SAVEPOINT local_domain_fee')
        cursor.execute('RELEASE SAVEPOINT local_domain_fee')
        # Never log driver error text, which can include SQL parameters.
        logger.warning('后端日志：本地领域费用未记账，已回滚费用效果 kind={}', type(error).__name__)
        return {'credit_cost':0.0, 'balance_after':None, 'applied':False,
                'error_code':'LOCAL_DOMAIN_FEE_NOT_APPLIED'}
    cursor.execute('RELEASE SAVEPOINT local_domain_fee')
    if balance is None:
        # The original DAL still inserts its audit row when no tenant balance
        # row exists; preserve that established policy and report it explicitly.
        logger.warning('后端日志：本地领域费用台账已记，余额未扣减')
    return {'credit_cost':amount, 'balance_after':balance, 'applied':True}


def invalidate_domain_fee_cache(tenant_id, result):
    """Post-commit projection only; failure cannot alter the saved phase."""
    if not result.get('applied'):
        return
    from src.core.cache_utils import invalidate_tenant_cache
    try:
        invalidate_tenant_cache(tenant_id)
    except Exception as error:
        logger.warning('后端日志：本地领域费用缓存刷新失败 kind={}', type(error).__name__)
