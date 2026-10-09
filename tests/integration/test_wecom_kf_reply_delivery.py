"""Real KF route/session/delivery combinations with external I/O isolated."""
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.channels.session import ChannelSessionManager
from src.channels.wecom_kf.adapter import WeComKfAdapter
from src.channels.wecom_kf.budget import WeComKfReplyBudget
from src.channels.wecom_kf.reply_delivery import prepare_reply
from src.channels.wecom_kf.reply_format import FILE_NOTICE
from src.core.session_queue import EnqueueResult
from src.models.message import UnifiedResponse, DownloadableFileInfo
from tests.unit.channels.test_session_manager_persist import _make_mock_db
from tests.unit.channels.test_wecom_kf_reply_delivery import Registry

pytestmark = [pytest.mark.integration, pytest.mark.channels]


@pytest.fixture(scope='session', autouse=True)
def init_db_pool():
    """The SQL boundary is replaced here; no live database is required."""
    yield


@pytest.fixture
def storage(monkeypatch, tmp_path):
    store = Registry()
    monkeypatch.setattr('src.core.redis_client.redis_client', store)
    monkeypatch.setattr('src.channels.wecom_kf.adapter.redis_client', store)
    def directory(tenant):
        path = tmp_path / tenant / 'conversation'
        path.mkdir(parents=True, exist_ok=True)
        return path
    monkeypatch.setattr('src.core.storage.get_conversation_dir', directory)
    monkeypatch.setattr('src.channels.wecom_kf.reply_delivery.get_conversation_dir', directory)
    return store


@pytest.mark.parametrize('full,cancel_summary', [
    ('您好，处理方式如下。\n\n' + '完整内容。' * 700,False),
    ('这是价格表：\n|项目|金额|\n|---|---|\n|服务|300|',False),
    ('a' * 2048,False),
    ('您好，处理方式如下。\n\n' + '完整内容。' * 700,True),
], ids=['long','table','equal-boundary','cancel-after-summary-usage'])
async def test_real_route_prepares_persists_and_sends_with_verbose_disabled(full, cancel_summary, storage, monkeypatch):
    from src.saas.api import channel_routes as route
    from src.channels import session as session_module
    from src.core.session_queue import session_queue
    from src.channels.runner_agent import ChannelRunnerAgent
    from src.services.session_record import SessionRecordService

    manager = ChannelSessionManager()
    manager._initialized = True
    store, connection, _ = _make_mock_db()
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr(session_module, 'get_db_connection', lambda: context)
    monkeypatch.setattr(route, 'channel_session_manager', manager)
    monkeypatch.setattr(manager, 'get_or_create_session', lambda **kwargs:
        {'session_id': 'independent_session', 'metadata': {}, 'tenant_id': 'tenant_a'})
    monkeypatch.setattr(manager, 'update_session', MagicMock())
    monkeypatch.setattr('src.saas.db.tenant_db.TenantDB.get_by_id', lambda tenant:
        {'credit_balance': 100, 'tenant_id': tenant})
    monkeypatch.setattr(route, '_is_kf_account_blocked', AsyncMock(return_value=False))
    monkeypatch.setattr(route, '_kf_tlog', lambda *args, **kwargs: None)
    monkeypatch.setattr(route, '_get_tenant_dedup', lambda tenant:
        SimpleNamespace(is_duplicate=AsyncMock(return_value=False)))
    monkeypatch.setattr('src.saas.services.auto_register.ensure_user_registered', AsyncMock(return_value='registered_user'))
    monkeypatch.setitem(sys.modules, 'src.core.agent_router', SimpleNamespace(agent_router=SimpleNamespace()))
    monkeypatch.setattr('src.channels.wecom_kf.context.set_kf_context', lambda *args, **kwargs: None)
    monkeypatch.setattr('src.services.recap.trigger_recap', lambda **kwargs: None)
    summary_consumed=asyncio.Event()
    if cancel_summary:
        from src.llm.call_observer import observed_call
        async def physical(**kwargs):
            return {'content':'您好，您可以先确认订单。','usage':{'prompt_tokens':1000,'completion_tokens':30,'cached_tokens':200}}
        async def chat(**kwargs):
            await observed_call(physical,provider='qwen',model='actual-summary-model',kwargs=kwargs)
            summary_consumed.set()
            await asyncio.Event().wait()
        monkeypatch.setattr('src.llm.gateway.LLMGateway',lambda:
            SimpleNamespace(chat_no_thinking=chat))
        monkeypatch.setattr('src.db.models.TokenCostPriceDB.get_by_model_name',lambda model:
            {'model_name':model,'input_price_per_m':1,'output_price_per_m':1})
    else:
        monkeypatch.setattr('src.channels.wecom_kf.reply_delivery._model_preview', AsyncMock(side_effect=AssertionError('default must not call model')))
    runner = AsyncMock(return_value=full)
    monkeypatch.setattr(ChannelRunnerAgent, 'process_message_sync', runner)
    async def queued(**kwargs):
        answer = await kwargs['processor'](lambda: False)
        return EnqueueResult(status='success', response_text=answer,
            merged_input=kwargs['user_input'], was_merged=False)
    monkeypatch.setattr(session_queue, 'enqueue_and_process', queued)
    for name in ['mark_responding', 'mark_idle', 'finish_processing']:
        monkeypatch.setattr(session_queue, name, MagicMock())
    usage = SessionRecordService('independent_session', 'registered_user', '问题', tenant_id='tenant_a', source_type='wecom_kf')
    def start(**kwargs):
        route.SessionRecordManager.set_current_record(usage)
        return usage
    monkeypatch.setattr(route.SessionRecordManager, 'start_record', start)
    save=MagicMock(return_value={'record_id':'summary_record'})
    monkeypatch.setattr(usage,'save',save)
    end_record = MagicMock(wraps=route.SessionRecordManager.end_record)
    monkeypatch.setattr(route.SessionRecordManager, 'end_record', end_record)
    adapter = WeComKfAdapter(corp_id='test', secret='test', kf_account=[{'open_kfid':'kf_a', 'subagent_type':'main'}],
        verbose_feedback={'enabled': False}, render_tables=False,summary_mode='llm' if cancel_summary else 'prefix')
    await adapter.set_tenant_id('tenant_a')
    adapter.verbose_feedback = {'enabled': False}
    message = {'msgid':'owner_a', 'origin':3, 'external_userid':'external_a', 'send_time':int(time.time()),
        'msgtype':'text', 'text':{'content':'问题'}}
    adapter.api_client = SimpleNamespace(
        sync_msg=AsyncMock(return_value={'errcode':0,'has_more':0,'next_cursor':'','msg_list':[message]}),
        get_service_state=AsyncMock(return_value={'service_state':1}),
        upload_media=AsyncMock(return_value={'errcode':0,'media_id':'md_uploaded'}),
        send_msg=AsyncMock(return_value={'errcode':0}),
    )
    adapter.get_user_info = AsyncMock(return_value={})
    adapter.cursor_manager = SimpleNamespace(get_cursor=lambda _: '', set_cursor=MagicMock())
    observed_budgets = []
    original_send = adapter.send_message
    async def send(response):
        observed_budgets.append(response.content.get('_kf_reply_budget'))
        return await original_send(response)
    adapter.send_message = send
    operation=route._process_tenant_wecom_kf_messages('tenant_a', 'config_a', 'kf_a', adapter)
    if cancel_summary:
        task=asyncio.create_task(operation)
        await asyncio.wait_for(summary_consumed.wait(),1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task,1)
        runner.assert_awaited_once()
        end_record.assert_called_once()
        save.assert_called_once()
        assert not usage.skip_save and usage.prompt_tokens==1000 and usage.completion_tokens==30
        assert usage.cached_input_tokens==200 and usage.total_token_count==1030
        assert usage.model=='actual-summary-model' and usage.provider=='qwen'
        adapter.api_client.send_msg.assert_not_awaited()
        assert not store['messages'], 'Cancelled summary must not publish or persist a partial answer'
        return
    await operation
    runner.assert_awaited_once()
    end_record.assert_called_once()
    assert usage.skip_save and usage.total_token_count == 0
    assert observed_budgets and isinstance(observed_budgets[0], WeComKfReplyBudget)
    assistant = next(row for row in store['messages'] if row['role'] == 'assistant')
    assert assistant['content'] == full
    metadata = json.loads(assistant['metadata'] or '{}')
    calls = adapter.api_client.send_msg.await_args_list
    if full == 'a' * 2048:
        assert [call.kwargs['msgtype'] for call in calls] == ['text']
        assert 'channelDelivery' not in metadata
    else:
        assert [call.kwargs['msgtype'] for call in calls] == ['text', 'file']
        assert len(calls[0].kwargs['content']['content']) <= 500
        assert metadata['channelDelivery']['mode'] in {'prefix','notice'}
        file_id = metadata['downloadableFiles'][0]['file_id']
        md = Path(storage.hgetall('uploaded_file:' + file_id)['path']).read_text(encoding='utf-8')
        assert full.replace('\n|项目|','\n\n|项目|') == md
        assert metadata['channelDelivery']['file_id'] == file_id
        assert usage.assistant_message == full


async def test_smaller_configured_byte_budget_still_delivers_prefix(storage):
    adapter = WeComKfAdapter(corp_id='test', secret='test', max_bytes=1024)
    adapter._tenant_id = 'tenant_a'
    adapter.current_open_kfid = 'kf_a'
    adapter.api_client = SimpleNamespace(upload_media=AsyncMock(return_value={'errcode':0,'media_id':'md'}),
        send_msg=AsyncMock(return_value={'errcode':0}))
    files = []
    full = '😀' * 1000
    delivery = await prepare_reply(full, files, tenant_id='tenant_a', user_id='user_a', session_id='session_a',
        owner_id='owner_a', max_bytes=1024)
    response = UnifiedResponse(message_id='owner_a', reply_to='external_a',
        content={'text':full, '_kf_delivery':delivery, '_kf_reply_budget':WeComKfReplyBudget()},
        downloadable_files=[DownloadableFileInfo(**file) for file in files])
    assert await adapter.send_message(response)
    assert [call.kwargs['msgtype'] for call in adapter.api_client.send_msg.await_args_list] == ['text','file']
    assert len(adapter.api_client.send_msg.await_args_list[0].kwargs['content']['content'].encode('utf-8')) <= 1024


async def test_real_llm_gateway_physical_call_sets_usage_once(storage, monkeypatch):
    from src.llm import gateway as gateway_module
    from src.channels.wecom_kf.reply_delivery import _model_preview
    from src.services.session_record import SessionRecordService
    monkeypatch.setattr('src.db.models.TokenCostPriceDB.get_by_model_name',
        lambda model: {'model_name': model, 'input_price_per_m':1, 'output_price_per_m':1})
    gateway = gateway_module.LLMGateway(provider_name='qwen', use_failover=False)
    key_context = MagicMock()
    key_context.__aenter__ = AsyncMock(return_value='safe-test-key')
    key_context.__aexit__ = AsyncMock(return_value=False)
    gateway._key_pool = SimpleNamespace(acquire=lambda: key_context)
    physical = AsyncMock(return_value={'content':'您好，您可以先确认订单。',
        'usage':{'prompt_tokens':1800,'completion_tokens':20,'cached_tokens':300,'cache_creation_tokens':50},
        'finish_reason':'stop'})
    monkeypatch.setattr(gateway_module, '_build_provider', lambda *args, **kwargs:
        SimpleNamespace(model='actual-summary-model', chat=physical))
    monkeypatch.setattr(gateway_module, 'LLMGateway', lambda: gateway)
    usage=SessionRecordService('session_a','user_a','问题',tenant_id='tenant_a',source_type='wecom_kf')
    usage.skip_save=True
    text=await _model_preview('您好，完整回答。' * 400,record_service=usage,question='问题')
    assert text=='您好，您可以先确认订单。'+FILE_NOTICE
    assert usage.model=='actual-summary-model' and usage.provider=='qwen'
    assert usage.total_token_count==1820 and usage.prompt_tokens==1800 and usage.completion_tokens==20
    assert usage.cached_input_tokens==300 and usage.cache_creation_input_tokens==50
    assert usage._llm_call_count==1 and not usage.skip_save
    physical.assert_awaited_once()
    assert physical.call_args.kwargs['tools'] is None
    assert physical.call_args.kwargs['enable_thinking'] is False


@pytest.mark.parametrize('source,expected', [
    ('您好，价格如下。\n|项目|金额|\n|---|---|\n|服务|300|', ['项目','金额','服务','300']),
    ('| 表达式 | 描述 |\n| --- | --- |\n| `a|b` | **保留内容** |', ['表达式','描述','a|b','保留内容']),
    ('| 项目 | 说明 |\n| --- | --- |\n| A | 包含\\|分隔符 |', ['项目','说明','A','包含|分隔符']),
])
def test_generated_md_tables_are_valid_in_independent_commonmark_gfm_renderer(source, expected):
    from markdown_it import MarkdownIt
    from src.channels.wecom_kf.reply_format import normalize_reply_markdown
    parser = MarkdownIt('commonmark').enable('table')
    tokens = parser.parse(normalize_reply_markdown(source))
    assert sum(token.type == 'table_open' for token in tokens) == 1
    inside, rendered = False, []
    for token in tokens:
        if token.type == 'table_open':
            inside = True
        elif token.type == 'table_close':
            inside = False
        elif inside and token.type == 'inline':
            rendered.append(''.join(child.content for child in token.children))
    assert rendered == expected


async def test_real_storage_and_redis_wrapper_keep_native_md_and_owner_integrity(tmp_path, monkeypatch):
    from src.core.redis_client import RedisClient
    from src.config.settings import settings
    from src.core.storage import get_conversation_dir
    monkeypatch.setattr(settings.redis, 'enabled', False)
    monkeypatch.setenv('AGENT_RUNNER_STORAGE_ROOT', str(tmp_path))
    cache = RedisClient()
    monkeypatch.setattr('src.core.redis_client.redis_client',cache)
    monkeypatch.setattr('src.channels.wecom_kf.adapter.redis_client',cache)
    files=[]
    source='您好，请根据以下内容操作。\n\n'+'详细步骤。' * 800
    delivery=await prepare_reply(source,files,tenant_id='tenant_a',user_id='u',session_id='s',owner_id='o')
    key=cache.make_key('uploaded_file',delivery['file_id'])
    meta=cache.hgetall(key)
    assert meta['visible'] is True and isinstance(meta['size'],int)
    assert 86300 <= cache.ttl(key) <= 86400
    assert Path(meta['path']).parent==get_conversation_dir('tenant_a')
    assert Path(meta['path']).read_text(encoding='utf-8')==source
    adapter=WeComKfAdapter(corp_id='test',secret='test')
    adapter._tenant_id='tenant_a'
    assert adapter._resolve_reply_file(delivery)==meta['path']
    cache.hset(key,'owner_id','different_owner')
    assert adapter._resolve_reply_file(delivery) is None


@pytest.fixture
def memory_queue(monkeypatch):
    from src.core.redis_client import RedisClient
    from src.core.session_queue import SessionMessageQueue
    from src.config.settings import settings
    monkeypatch.setattr(settings.redis, 'enabled', False)
    cache=RedisClient()
    monkeypatch.setattr('src.core.session_queue.redis_client',cache)
    queue=SessionMessageQueue()
    queue.KEEPALIVE_INTERVAL=0.01
    queue.LOCK_TTL=10
    session='independent_finalization'
    lock=queue._key('session_lock',session)
    final=queue._key('session_finalizing',session)
    assert cache.acquire_lock(lock,'original',ex=1)
    cache.set(final,'original',ex=1)
    return cache,queue,session,lock,final


async def test_real_finalization_keepalive_renews_owner_and_releases_on_cancellation(memory_queue):
    from src.channels.session import _hold_prepared_delivery
    cache,queue,session,lock,final=memory_queue
    entered=asyncio.Event()
    async def operation():
        async with _hold_prepared_delivery(queue,session,'original',True):
            entered.set()
            await asyncio.Event().wait()
    task=asyncio.create_task(operation())
    await asyncio.wait_for(entered.wait(),1)
    await asyncio.sleep(0.03)
    assert cache.ttl(lock)>=9 and cache.ttl(final)>=9
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task,1)
    assert not cache.exists(lock) and not cache.exists(final)


async def test_lost_finalization_owner_aborts_delivery_and_cannot_clear_successor(memory_queue):
    from src.channels.session import _hold_prepared_delivery
    cache,queue,session,lock,final=memory_queue
    entered=asyncio.Event()
    would_send=MagicMock()
    async def operation():
        async with _hold_prepared_delivery(queue,session,'original',True):
            entered.set()
            await asyncio.sleep(10)
            would_send()
    task=asyncio.create_task(operation())
    await asyncio.wait_for(entered.wait(),1)
    cache.delete(lock)
    assert cache.acquire_lock(lock,'successor',ex=10)
    cache.set(final,'successor',ex=10)
    with pytest.raises(RuntimeError,match='CHANNEL_DELIVERY_OWNERSHIP_LOST'):
        await asyncio.wait_for(task,1)
    would_send.assert_not_called()
    assert cache._get_backend().get(lock)=='successor' and cache.get(final)=='successor'


def test_expired_finalization_owner_cannot_be_renewed_or_revive_lock(memory_queue):
    cache,queue,session,lock,final=memory_queue
    # Set a past expiry without sleeping: renewal must evaluate actual backend TTL.
    with cache._fallback._lock:
        cache._fallback._ttls[lock]=time.time()-1
    assert not queue.renew_finalizing(session,'original')
    assert not cache.exists(lock)
    assert cache.exists(final)
