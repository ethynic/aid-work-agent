"""Owned secondary config using the original legacy shared-SID binding DAL."""
from contextlib import contextmanager
from dataclasses import replace

from .kf_context_fixtures import ContextReceiptScope


@contextmanager
def secondary_context_scope(primary):
    from .kf_ingress_fixtures import seed_intent
    s = primary.scope
    config_id = 'kf_context_other_config_' + s.marker
    secondary = replace(s, config_id=config_id)
    account = None
    s.rows("INSERT INTO tenant_channel_configs(config_id,tenant_id,channel_type,name,config,verified,subagent_type,updated_at) "
        "SELECT %s,tenant_id,channel_type,'Fictional second context route',config,verified,subagent_type,clock_timestamp() "
        "FROM tenant_channel_configs WHERE config_id=%s", (config_id, s.config_id))
    try:
        account = seed_intent(secondary, primary.repository)
        yield ContextReceiptScope(secondary, primary.repository, account,
            primary.platform, primary.config.model_copy(deep=True))
    finally:
        if account is not None:
            for table in ('wecom_kf_context_task_intents','wecom_kf_context_consumptions',
                    'wecom_kf_receipt_classifications'):
                s.rows('DELETE FROM '+table+' WHERE account_id=%s AND tenant_id=%s',
                    (account['account_id'], s.tenant_id))
            s.rows("DELETE FROM channel_messages WHERE tenant_id=%s AND session_id=%s AND metadata->>'account_id'=%s",
                (s.tenant_id, s.legacy_sid, account['account_id']))
            s.rows('DELETE FROM wecom_kf_inbox WHERE account_id=%s AND tenant_id=%s',
                (account['account_id'], s.tenant_id))
            s.rows('DELETE FROM wecom_kf_account_sync WHERE account_id=%s AND tenant_id=%s',
                (account['account_id'], s.tenant_id))
        s.rows('DELETE FROM channel_session_routes WHERE config_id=%s AND tenant_id=%s',
            (config_id, s.tenant_id))
        s.rows('DELETE FROM tenant_channel_configs WHERE config_id=%s AND tenant_id=%s',
            (config_id, s.tenant_id))
