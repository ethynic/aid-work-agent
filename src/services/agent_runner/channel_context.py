"""从已授权的渠道会话重建工具上下文，不拥有回调、拉取或回复投递。"""
import asyncio
import json
from contextlib import asynccontextmanager
from .contracts import RunnerError


def kf_binding(cursor, request, session):
    config_id = request.request_data.get('channel_config_id')
    if not isinstance(config_id, str) or not config_id:
        raise RunnerError('CHANNEL_CONFIG_REQUIRED', 400)
    cursor.execute('SELECT * FROM tenant_channel_configs WHERE tenant_id=%s AND config_id=%s',
                   (session['tenant_id'], config_id))
    row = cursor.fetchone()
    if not row or row['channel_type'] != 'wecom_kf':
        raise RunnerError('CHANNEL_CONFIG_FORBIDDEN', 403)
    config = row.get('config') or {}
    if isinstance(config, str):
        config = json.loads(config)
    if config.get('enabled', True) is not True:
        raise RunnerError('CHANNEL_CONFIG_DISABLED', 403)
    accounts = [item for item in config.get('kf_account', [])
                if item.get('open_kfid') == session.get('channel_chat_id')]
    if len(accounts) != 1:
        raise RunnerError('CHANNEL_ACCOUNT_FORBIDDEN', 403)
    account = accounts[0]
    profile = account.get('subagent_type') or 'main'
    if profile != request.profile_id:
        raise RunnerError('CHANNEL_PROFILE_MISMATCH', 403)
    return config, account


@asynccontextmanager
async def channel_runtime_scope(row, connection_factory):
    if row['source'] != 'wecom_kf':
        yield
        return
    from .contracts import RunnerSubmit
    from src.channels.wecom_kf.adapter import WeComKfAdapter
    from src.channels.wecom_kf.context import _kf_context
    def load():
        with connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM channel_sessions WHERE session_id=%s AND tenant_id=%s',
                           (row['session_id'], row['tenant_id']))
            session = cursor.fetchone()
            if not session:
                raise RunnerError('SESSION_NOT_FOUND', 404)
            request = RunnerSubmit(client_request_id=row['client_request_id'], **row['input'])
            config, account = kf_binding(cursor, request, session)
            return dict(session), config, account
    session, config, account = await asyncio.to_thread(load)
    adapter = WeComKfAdapter(**config)
    try:
        await adapter.set_tenant_id(row['tenant_id'])
        metadata = session.get('metadata') or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        token = _kf_context.set({'adapter':adapter, 'open_kfid':session['channel_chat_id'],
            'external_userid':session['channel_user_id'], 'kf_config':account,
            'session_id':row['session_id'], 'tenant_id':row['tenant_id'],
            'user_id':session.get('user_id'), 'channel_user_info':row['input'].get('request_data', {}).get('channel_user_info') or {},
            'lead_capture':metadata.get('lead_capture')})
        try:
            yield
        finally:
            _kf_context.reset(token)
    finally:
        await adapter.close()
