"""Finite original SDK legacy-mode association; no channel Agent execution."""
import asyncio

import pytest

from .kf_ingress_fixtures import kf_scope, text_message
from .kf_ingress_peer import PullPage

pytestmark = pytest.mark.integration


def test_original_legacy_sdk_default_account_and_sync_requests_keep_http_shape_and_close(kf_scope):
    scope = kf_scope
    # Existing SDK permits an account response without errcode. This documents
    # default legacy behavior, never uses it as native account/page authority.
    scope.peer.account_response = {'account_list': [{'open_kfid': scope.open_kfid}]}
    scope.peer.script(PullPage({'errcode': 0, 'has_more': 0, 'next_cursor': 'legacy_cursor',
        'msg_list': [text_message(scope, 'legacy_provider_' + scope.marker)]}))
    client = scope.original_client(scope.corp_id, scope.peer.secret)

    async def original_calls():
        try:
            assert client._ingress_bytes is None
            accounts = await client.account_list()
            assert accounts['account_list'][0]['open_kfid'] == scope.open_kfid
            result = await client.sync_msg(scope.open_kfid)
            assert result['next_cursor'] == 'legacy_cursor'
            assert result['msg_list'][0]['external_userid'] == scope.actor_id
        finally:
            await client.close()

    asyncio.run(original_calls())
    assert client._http_client is not None and client._http_client.is_closed
    assert [call['path'] for call in scope.peer.calls] == [
        '/cgi-bin/gettoken', '/cgi-bin/kf/account/list', '/cgi-bin/kf/sync_msg']
    assert scope.peer.calls[-1]['keys'] == ['cursor', 'limit', 'open_kfid', 'voice_format']
    assert not scope.peer.errors
    assert scope.rows('SELECT 1 FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,)) == []
    assert scope.rows('SELECT 1 FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,)) == []
    assert scope.rows('SELECT 1 FROM channel_session_routes WHERE config_id=%s', (scope.config_id,)) == []

