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
    return config, account, row.get('config_id') or ''


def web_c_binding(cursor, request, session):
    """校验 web_c 渠道配置绑定，返回 (config_id, config)。

    config_id 用于留资通知规则 kf_config 层匹配；config 供调用方读取
    lead_assignee_user_id（留资归属员工，方案 2 注入 kf_config.tenant_user_id）。

    web_c 为渠道级绑定（tenant_channel_configs.subagent_type 列），无 kf 账号维度；
    语义与 C 端 _load_web_c_config 对齐：行存在 + enabled 才放行（防枚举不区分细节）。
    """
    config_id = request.request_data.get('channel_config_id')
    if not isinstance(config_id, str) or not config_id:
        raise RunnerError('CHANNEL_CONFIG_REQUIRED', 400)
    cursor.execute('SELECT subagent_type, config FROM tenant_channel_configs '
                   'WHERE tenant_id=%s AND config_id=%s AND channel_type=%s',
                   (session['tenant_id'], config_id, 'web_c'))
    row = cursor.fetchone()
    if not row:
        raise RunnerError('CHANNEL_CONFIG_FORBIDDEN', 403)
    config = row.get('config') or {}
    if isinstance(config, str):
        config = json.loads(config)
    if not config.get('enabled'):
        raise RunnerError('CHANNEL_CONFIG_DISABLED', 403)
    profile = row.get('subagent_type') or 'main'
    if profile != (request.profile_id or 'main'):
        raise RunnerError('CHANNEL_PROFILE_MISMATCH', 403)
    return config_id, config


@asynccontextmanager
async def channel_runtime_scope(row, connection_factory):
    if row['source'] == 'wecom_kf':
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
                config, account, config_id = kf_binding(cursor, request, session)
                return dict(session), config, account, config_id
        session, config, account, config_id = await asyncio.to_thread(load)
        adapter = WeComKfAdapter(**config)
        try:
            await adapter.set_tenant_id(row['tenant_id'])
            metadata = session.get('metadata') or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            token = _kf_context.set({'adapter':adapter, 'open_kfid':session['channel_chat_id'],
                'external_userid':session['channel_user_id'], 'kf_config':account,
                # 渠道配置行 id：留资通知规则 kf_config 层（渠道级）条件匹配依据，
                # 与原入口 channel_routes 的 set_kf_context 口径一致
                'kf_config_id':config_id,
                'session_id':row['session_id'], 'tenant_id':row['tenant_id'],
                'user_id':session.get('user_id'), 'channel_user_info':row['input'].get('request_data', {}).get('channel_user_info') or {},
                'lead_capture':metadata.get('lead_capture')})
            try:
                yield
            finally:
                _kf_context.reset(token)
        finally:
            await adapter.close()
    elif row['source'] == 'web_c':
        # web_c（官网客服）留资上下文（设计 web-c-channel-design.md §4.7）：复用
        # record_lead_capture 的 get_kf_context 消费口径，字段按 web_c 语义降维——
        # open_kfid=None（该列是 wecom_kf open_kfid 语义，web_c 线索靠 session_id
        # 关联）、adapter=None（无渠道发送适配器，transfer_to_human/send_advisor_qr
        # 各自走「渠道不支持/未配置」降级）、kf_config 按渠道配置的留资归属员工注入
        # （未配置为 {}，assigned_to 为空）。
        from .contracts import RunnerSubmit
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
                config_id, config = web_c_binding(cursor, request, session)
                return dict(session), config_id, config
        session, config_id, config = await asyncio.to_thread(load)
        metadata = session.get('metadata') or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        # 留资归属员工（方案 2，2026-11-20）：渠道配置 lead_assignee_user_id 注入
        # kf_config.tenant_user_id，record_lead_capture 的 assigned_to/assignee 快照
        # 与通知 kf_assignee 接收人全链路复用；未配置保持 {}（线索不归属员工）
        assignee = str(config.get('lead_assignee_user_id') or '').strip()
        token = _kf_context.set({'adapter':None, 'open_kfid':None,
            'external_userid':session.get('channel_user_id') or row['channel_user_id'],
            'kf_config':({'tenant_user_id':assignee} if assignee else {}),
            'kf_config_id':config_id,
            'session_id':row['session_id'], 'tenant_id':row['tenant_id'],
            'user_id':session.get('user_id'), 'channel_user_info':{},
            'lead_capture':metadata.get('lead_capture')})
        try:
            yield
        finally:
            _kf_context.reset(token)
    else:
        yield
        return
