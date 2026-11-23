"""Keep the one-time WeChat summary independent of general tool receipts and logs."""
import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.config.settings import settings
from src.llm import llm_call_logger
from src.llm.errors import ProviderHTTPError
from src.llm.providers.qwen import QwenProvider
from src.local_tools import api, pricing, repository, weixin_summary


DEVICE = '00000000-0000-4000-8000-000000000001'


def test_summary_requires_explicit_boolean_consent_and_bills_only_the_model(monkeypatch):
    with pytest.raises(ValidationError):
        weixin_summary.CreateRun(target_name='测试群', confirmed='true', device_id=DEVICE)
    with pytest.raises(ValidationError):
        weixin_summary.CreateRun(target_name='测试群', confirmed=True, device_id='not-a-device-id')
    assert weixin_summary.CreateRun(target_name='测试群', confirmed=True, device_id=DEVICE).confirmed is True
    monkeypatch.setattr(settings.boss_tool_billing, 'enabled', True)
    monkeypatch.setattr(settings.boss_tool_billing, 'default_credit_price', 9.0)
    assert pricing.tool_credit_price('another_tool') == 9.0
    assert pricing.tool_credit_price(weixin_summary.TOOL) == 0.0


@pytest.mark.asyncio
async def test_low_balance_blocks_enqueue_and_model_after_the_one_use_grant(monkeypatch):
    credit = JSONResponse({'code': 'NO_CREDIT'}, status_code=403)
    check = Mock(return_value=credit)
    monkeypatch.setattr(api, 'check_tenant_credit_blocked', check)
    monkeypatch.setattr(api, '_current_user_and_tenant', AsyncMock(return_value=({'user_id': 'user'}, 'tenant')))
    enqueue = Mock()
    monkeypatch.setattr(weixin_summary, 'create_run', enqueue)
    with pytest.raises(HTTPException) as error:
        await api.create_weixin_summary_run(
            weixin_summary.CreateRun(target_name='测试群', confirmed=True, device_id=DEVICE), Mock(),
        )
    assert error.value.status_code == 403 and error.value.detail['code'] == 'NO_CREDIT'
    enqueue.assert_not_called()

    events = []
    check.side_effect = lambda tenant: events.append(('credit', tenant)) or credit
    grant = Mock(side_effect=lambda *args: events.append(('consume', args[0])) or {'user_id': 'user'})
    monkeypatch.setattr(weixin_summary, 'consume_grant', grant)
    monkeypatch.setattr(weixin_summary, 'read_upload', AsyncMock(return_value=weixin_summary.Upload(
        tenant_id='tenant', business_date='2026-11-23', messages=['synthetic message'],
    )))
    model = AsyncMock()
    monkeypatch.setattr(weixin_summary, 'summarize', model)
    request = Mock(headers={'Authorization': 'Bearer ' + 'x' * 43})
    assert await api.runtime_weixin_summary(request) is credit
    assert events == [('consume', 'tenant'), ('credit', 'tenant')]
    model.assert_not_awaited()


def test_weixin_summary_receipt_only_persists_allowed_projection(monkeypatch):
    binding = {'tool_name': weixin_summary.TOOL, 'business_ref': {'grant_consumed': True}}
    monkeypatch.setattr(repository, 'get_invocation', Mock(return_value=binding))
    write = Mock()
    monkeypatch.setattr(repository, 'write_result', write)
    public = {'summary': '讨论了测试计划', 'message_count': 2, 'partial': True}
    repository.write_runtime_result(
        'run-id', 'tenant', 'token-hash', True, 'OK', 'private diagnostics', 'none',
        {**public, 'messages': ['private source'], 'snapshot': 'private image'}, device_id='device',
    )
    write.assert_called_once_with('run-id', 'tenant', 'token-hash', True, 'OK',
                                  '微信总结已完成', 'none', public, False, device_id='device')
    binding['business_ref'] = {}
    repository.write_runtime_result('run-id', 'tenant', 'token-hash', True, 'OK', data=public)
    assert write.call_args.args[3:9] == (False, 'INTERNAL_ERROR', '微信总结未完成', 'none', None, False)
    repository.write_runtime_result('run-id', 'tenant', 'token-hash', False, 'NO_CREDIT', 'provider echo')
    assert write.call_args.args[3:9] == (False, 'NO_CREDIT', '微信总结未完成', 'none', None, False)


@pytest.mark.asyncio
async def test_model_input_is_in_memory_and_usage_is_attributed(monkeypatch, tmp_path):
    from src.llm import gateway
    from src.services import session_record

    monkeypatch.setattr(llm_call_logger, '_LOG_DIR', tmp_path)
    charge = Mock()
    monkeypatch.setattr(session_record, 'record_background_llm_usage', charge)
    model = Mock()
    model.get_model_name.return_value = 'fixture-model'

    async def chat(*, messages, temperature):
        assert llm_call_logger.provider_content_private()
        assert messages[0]['role'] == 'system'
        assert json.loads(messages[1]['content']) == ['synthetic-private-marker']
        result = {'content': '测试会议纪要', 'usage': {'total_tokens': 15}}
        llm_call_logger.log_llm_invoke('req-id', 'fixture', 'fixture-model',
                                       {'messages': messages}, result, usage=result['usage'])
        return result

    model.chat_no_thinking = AsyncMock(side_effect=chat)
    make = Mock(return_value=model)
    monkeypatch.setattr(gateway, 'LLMGateway', make)
    assert await weixin_summary.summarize(['synthetic-private-marker'], 'tenant', 'user') == '测试会议纪要'
    make.assert_called_once_with(use_failover=False)
    assert not llm_call_logger.provider_content_private()
    charge.assert_called_once_with({'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 15},
        tenant_id='tenant', user_id='user', source=weixin_summary.KIND,
        user_message='微信群聊今日总结', model='fixture-model')
    log_line = json.loads(next(tmp_path.glob('*.jsonl')).read_text(encoding='utf8'))
    assert log_line['request'] is None and log_line['response'] is None
    assert log_line['usage'] == {'total_tokens': 15}


@pytest.mark.asyncio
async def test_provider_error_echo_is_suppressed_only_for_the_private_call(monkeypatch, tmp_path):
    marker = 'synthetic-echo-marker'
    monkeypatch.setattr(llm_call_logger, '_LOG_DIR', tmp_path)
    async_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(400, text=marker))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: async_client(transport=transport, **kwargs))
    provider = QwenProvider(api_key='fixture-key')
    records = []
    from loguru import logger

    log_id = logger.add(lambda message: records.append(str(message)), level='ERROR')
    try:
        with llm_call_logger.private_provider_call(), pytest.raises(ProviderHTTPError) as error:
            await provider.chat(messages=[{'role': 'user', 'content': marker}])
        assert marker not in str(error.value)
        assert all(marker not in line for line in records)
        assert marker not in next(tmp_path.glob('*.jsonl')).read_text(encoding='utf8')
        with pytest.raises(ProviderHTTPError, match=marker):
            await provider.chat(messages=[{'role': 'user', 'content': marker}])
    finally:
        logger.remove(log_id)
