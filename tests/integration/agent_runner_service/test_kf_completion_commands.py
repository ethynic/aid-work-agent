"""Original command/stale policy through actual Admission orchestration.

Owned real Crypto/page/SQL and original command executor; only SDK HTTP is
localhost. No product imports or business operations at module load.
"""
import asyncio
import time

import pytest

from .kf_completion_fixtures import completion_scope, kf_scope, fresh_customer_text
from .kf_completion_peer import CompletionWireReply
from .kf_completion_orchestration import original_admission
from .kf_admission_service import KfSourceApi
from .kf_admission_fixtures import cleanup_text_runners

pytestmark = pytest.mark.integration


def test_actual_hidden_clear_stale_and_media_filter_hint_are_bounded_without_runner_or_paid_dispatch(
        completion_scope):
    c, s = completion_scope, completion_scope.scope
    # Original channel policy checks remote AI state before hidden commands.
    # The command bypasses model/credit dispatch, not this trusted SDK read.
    state = CompletionWireReply(payload={'errcode': 0, 'service_state': 1})
    c.platform.script('/cgi-bin/kf/service_state/get', *([state] * 96))
    c.platform.script('/cgi-bin/kf/send_msg', CompletionWireReply(payload={'errcode': 0}),
        CompletionWireReply(payload={'errcode': 0}))
    # 原注册/归因流程会读取客户信息（completion_business 注册分支，
    # 契约「注册/归因/欢迎」为必须保留的原行为），脚本化其响应。
    customer_info = CompletionWireReply(payload={'errcode': 0, 'customer_list': [{
        'external_userid': s.actor_id, 'nickname': 'Fictional Customer',
        'avatar': '', 'gender': 1, 'unionid': ''}]})
    c.platform.script('/cgi-bin/kf/customer/batchget', customer_info, customer_info)
    before = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,))
    command = c.receive(fresh_customer_text(s, 'actual_hidden_clear_' + s.marker, '清空会话'))[0]
    api, admission = None, None
    try:
        api = KfSourceApi(c.processes, c.platform)
        c.config.api_url = api.url
        admission = original_admission(c, api)
        c.record_resources('commands_actual_admission_api_started', [api.child])
        assert asyncio.run(admission.run_once()) is None
        assert s.rows('SELECT 1 FROM channel_messages WHERE tenant_id=%s AND session_id=%s',
            (s.tenant_id, s.legacy_sid)) == []
        assert c.platform.count('/cgi-bin/kf/send_msg') == 1
        handled = s.rows('SELECT phase,value,payload_digest,scope FROM wecom_kf_business_facts '
            "WHERE account_id=%s AND message_id=%s AND business_kind='hidden_command'",
            (command.account_id, command.message_id))
        assert len(handled) == 1 and handled[0]['phase'] == 'known'
        assert handled[0]['value']['reply'] == '会话消息已清空，开始新会话。'
        wires = s.rows('SELECT phase,response_origin,errcode,purpose,payload_digest,scope '
            "FROM wecom_kf_wire_operations WHERE locator->>'account_id'=%s AND locator->>'message_id'=%s",
            (command.account_id, command.message_id))
        assert wires == [{'phase': 'ack', 'response_origin': 'platform', 'errcode': 0,
            'purpose': 'hidden_reply', 'payload_digest': handled[0]['payload_digest'],
            'scope': handled[0]['scope']}]
        assert asyncio.run(admission.run_once()) is None
        assert c.platform.count('/cgi-bin/kf/send_msg') == 1
        # Original receive time is an actual historical fixture timestamp,
        # not a fake clock or an invented fixed value like timestamp=100.
        stale = c.receive(fresh_customer_text(s, 'actual_older_than_30m_' + s.marker,
            'Fictional old receipt must not dispatch', send_time=int(time.time()) - 1801))[0]
        for _ in range(3):
            assert asyncio.run(admission.run_once()) is None
        assert s.rows('SELECT phase,value FROM wecom_kf_business_facts WHERE account_id=%s '
            "AND message_id=%s AND business_kind='stale'", (stale.account_id, stale.message_id)) == [
                {'phase': 'suppressed', 'value': {'reason': 'older_than_30_minutes'}}]
        assert c.platform.count('/cgi-bin/kf/send_msg') == 1
        # Original image/file/video AI filtering shares the complete route.
        # filter uses one bounded customer hint, never a media download/model.
        images = c.receive(*({'msgid': 'actual_filtered_' + kind + '_' + s.marker,
            'external_userid': s.actor_id, 'open_kfid': s.open_kfid, 'origin': 3,
            'send_time': int(time.time()), 'msgtype': kind,
            kind: {'media_id': 'fictional_unfetched_' + kind, **(
                {'file_name': 'fictional-unfetched.bin'} if kind == 'file' else {})}}
            for kind in ('image', 'file', 'video')))
        for _ in range(6):
            assert asyncio.run(admission.run_once()) is None
        filtered = s.rows('SELECT phase,value FROM wecom_kf_business_facts WHERE account_id=%s '
            "AND message_id=ANY(%s) AND business_kind='media_filtered' ORDER BY message_id",
            (command.account_id, [image.message_id for image in images]))
        assert filtered == [{'phase': 'suppressed', 'value': {'reason': 'ai_media_filter'}}] * 3
        hints = s.rows('SELECT phase,response_origin,errcode,purpose,scope FROM wecom_kf_wire_operations '
            "WHERE locator->>'account_id'=%s AND purpose='filter_hint'", (command.account_id,))
        assert hints == [{'phase': 'ack', 'response_origin': 'platform', 'errcode': 0,
            'purpose': 'filter_hint', 'scope': handled[0]['scope']}]
        assert c.platform.count('/cgi-bin/kf/send_msg') == 2
        assert s.rows('SELECT accepted_input_ref FROM wecom_kf_inbox WHERE account_id=%s '
            'AND message_id=ANY(%s)', (command.account_id,
                [command.message_id, stale.message_id, *(image.message_id for image in images)])) == [
                {'accepted_input_ref': None}] * 5
        # Original remote human/ended filtering records customer history and
        # performs no AI acceptance, robot response or this-round charge.
        for remote_state, source in ((3, 'customer_human'), (4, 'customer_ended')):
            state.payload = {'errcode': 0, 'service_state': remote_state}
            text = 'Fictional ordinary customer context in state ' + str(remote_state)
            locator = c.receive(fresh_customer_text(s, 'state_filter_' + str(remote_state) + '_' + s.marker, text))[0]
            for _ in range(6):
                assert asyncio.run(admission.run_once()) is None
            history = s.rows("SELECT role,content,metadata->>'source' AS source FROM channel_messages "
                "WHERE tenant_id=%s AND session_id=%s AND metadata->>'msgid'=%s",
                (s.tenant_id, s.legacy_sid, locator.message_id))
            assert history == [{'role': 'user', 'content': text, 'source': source}]
            assert c.platform.count('/cgi-bin/kf/send_msg') == 2
        assert s.rows('SELECT 1 FROM agent_runners WHERE tenant_id=%s AND session_id=%s',
            (s.tenant_id, s.legacy_sid)) == []
        assert s.rows('SELECT 1 FROM agent_runner_inputs WHERE locator->>\'account_id\'=%s',
            (c.account['account_id'],)) == []
        assert s.rows('SELECT 1 FROM agent_runner_session_claims WHERE tenant_id=%s AND session_id=%s',
            (s.tenant_id, s.legacy_sid)) == []
        # 原行为：人工期/已结束期消息需落库并推送第三方（channel_routes.py
        # 「人工期/已结束期需落库并推送第三方系统（external_push_human）」），
        # recap 侧另有 lead_refresh（services/recap/runner.py）。上方 state 3/4
        # 两条消息各产生 2 条 pending_adapter 有界意图，共 4 条；无 AI 执行由
        # 上面 runners/inputs/claims/余额断言单独覆盖。
        assert s.rows('SELECT task_name,state,COUNT(*) AS n FROM wecom_kf_context_task_intents '
            'WHERE account_id=%s GROUP BY task_name,state ORDER BY task_name',
            (c.account['account_id'],)) == [
            {'task_name': 'external_push_human', 'state': 'pending_adapter', 'n': 2},
            {'task_name': 'lead_refresh', 'state': 'pending_adapter', 'n': 2}]
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == before
        assert not c.platform.errors and not s.peer.errors and not admission.tasks
    finally:
        if admission is not None:
            asyncio.run(admission.close())
        if api is not None:
            api.close()
        cleanup_text_runners(s)
        c.record_resources('commands_body_finally_before_fixture_teardown')
