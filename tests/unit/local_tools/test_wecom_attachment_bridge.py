"""Anonymous local attachment bytes become scoped refs and real vision input."""

import base64
import copy
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
import pytest
from pydantic import ValidationError

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity
from src.core.image_asset import ImageRef
from src.local_tools.wecom_business import ReadInput
from src.local_tools.wecom_attachments import MAX_IMAGE_BYTES, materialize_attachment
from src.services.agent_runner.resource_paths import workspace_directory
from src.services.agent_runner.runtime.attachments import AttachmentAssembler
from src.services.agent_runner.runtime.executor import ModelAdapter
from src.services.agent_runner.runtime.local_tools import LocalToolAdapter

pytestmark = pytest.mark.unit
IDENTITY = Identity('tenant-attachment', 'user-attachment', 'session-attachment', 'chat', 'web')
ARGS = {'session': 'ws1.synthetic', 'attachment': {'message_id': 11}}


def png():
    stream = BytesIO()
    Image.new('RGB', (3, 2), color='green').save(stream, format='PNG')
    return stream.getvalue()


def receipt(content=None):
    raw = png() if content is None else content
    return {'success': True, 'code': 'OK', 'effect': 'none', 'data': {'channel': 'local-db',
        'session': ARGS['session'], 'attachment': {'message_id': 11, 'kind': 'image',
            'name': 'anonymous.png', 'mime_type': 'image/png', 'size_bytes': len(raw),
            'status': 'available', 'image': {'type': 'image', 'mimeType': 'image/png',
                'data': base64.b64encode(raw).decode('ascii')}}}}


def state():
    return ExecutionState(identity=IDENTITY, execution_id='execution-attachment',
        role=AgentMode.MASTER, system_prompt='fixture', messages=[])


@pytest.fixture
def assets(tmp_path, monkeypatch):
    """Only storage/registry external boundaries are fake; image files are real."""
    import src.local_tools.wecom_attachments as bridge
    temp = tmp_path / 'temp'
    store = tmp_path / 'registered'
    store.mkdir()
    registered = []

    async def register(**kwargs):
        src = Path(kwargs['source_path'])
        image_id = 'file_' + str(len(registered))
        destination = store / (image_id + src.suffix)
        destination.write_bytes(src.read_bytes())
        reference = ImageRef(file_id=image_id, download_url='/api/files/' + image_id + '/download',
            display_name=kwargs['display_name'], width=3, height=2, mime_type='image/png',
            size_bytes=destination.stat().st_size, source='user_upload', usage='inline')
        registered.append((kwargs, reference, destination))
        return reference

    monkeypatch.setattr(bridge, 'get_image_registry', lambda: SimpleNamespace(register=register))
    monkeypatch.setattr(bridge, 'ensure_tenant_storage_dir', lambda tenant, scene: temp.mkdir(exist_ok=True))
    monkeypatch.setattr(bridge, 'get_tenant_storage_abs_path', lambda tenant, scene, name: str(temp / name))
    return SimpleNamespace(registered=registered, temp=temp)


async def materialize(raw, current, tmp_path):
    assembler = AttachmentAssembler(workspace_directory(tmp_path, current.identity, current.execution_id))
    return await materialize_attachment(raw, ARGS, tenant_id=IDENTITY.tenant_id,
        user_id=IDENTITY.user_id, tool_call_id='attachment-call', state=current, assembler=assembler)


def pair(current, result, *, complete=True):
    calls = [{'id': 'attachment-call', 'type': 'function', 'function': {
        'name': 'wecom_read_session', 'arguments': json.dumps(ARGS)}}]
    if not complete:
        calls.append({'id': 'other-call', 'type': 'function', 'function': {'name': 'wecom_read_sessions', 'arguments': '{}'}})
    current.messages = [{'role': 'user', 'content': '请看这张图'},
        {'role': 'assistant', 'content': '', 'tool_calls': calls},
        {'role': 'tool', 'tool_call_id': 'attachment-call', 'content': json.dumps(result)}]


class Gateway:
    def __init__(self):
        self.calls = []

    async def chat_with_tools(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs['messages']))
        return {'content': 'image understood'}

    def get_model_name(self):
        return 'fixture-model'

    def get_provider_name(self):
        return 'fixture-provider'


@pytest.mark.asyncio
async def test_requested_image_becomes_ref_and_owned_resource_without_mutating_receipt(tmp_path, assets):
    current = state()
    raw = receipt()
    original = copy.deepcopy(raw)
    public = await materialize(raw, current, tmp_path)
    assert raw == original, 'original invocation remains the source evidence for safe read recovery'
    ref = public['data']['attachment']['image']
    assert ref['file_id'] == 'file_0' and 'data' not in ref
    assert public['images'] == [ref]
    assert base64.b64encode(png()).decode('ascii') not in json.dumps(public)
    artifact = current.resources['image_artifacts'][0]
    assert artifact['source'] == 'local_attachment'
    assert artifact['image_ref']['file_id'] == ref['file_id']
    assert Path(artifact['path']).read_bytes() == png()
    again = await materialize(raw, current, tmp_path)
    assert again['data']['attachment']['image'] == ref
    assert len(assets.registered) == len(current.resources['image_artifacts']) == 1
    assert list(assets.temp.iterdir()) == []


@pytest.mark.asyncio
async def test_real_model_wire_has_image_after_paired_receipt_and_restore_keeps_refs(tmp_path, assets):
    current = state()
    public = await materialize(receipt(), current, tmp_path)
    pair(current, public)
    restored = ExecutionState.restore(current.checkpoint())
    before = restored.checkpoint()
    gateway = Gateway()
    adapter = ModelAdapter(gateway, resource_directory=tmp_path)
    await adapter.complete(restored, 'model-call', [])
    wire = gateway.calls[0]
    assert wire[-2]['role'] == 'tool' and wire[-1]['role'] == 'user'
    assert wire[-1]['content'][1] == {'type': 'image_url', 'image_url': {
        'url': 'data:image/png;base64,' + base64.b64encode(png()).decode('ascii')}}
    assert restored.checkpoint() == before, 'provider serialization must never persist base64 in checkpoint/history'
    assert 'data:image' not in json.dumps(restored.checkpoint())


@pytest.mark.asyncio
async def test_next_assistant_turn_does_not_reattach_historical_image(tmp_path, assets):
    current = state()
    pair(current, await materialize(receipt(), current, tmp_path))
    current.messages.append({'role': 'assistant', 'content': 'already understood'})
    gateway = Gateway()
    await ModelAdapter(gateway, resource_directory=tmp_path).complete(current, 'next-call', [])
    assert gateway.calls[0] == current.messages


@pytest.mark.asyncio
async def test_ordinary_tool_array_payload_is_unchanged(tmp_path):
    current = state()
    pair(current, {'success': True, 'data': [{'candidate': 'anonymous'}]})
    gateway = Gateway()
    await ModelAdapter(gateway, resource_directory=tmp_path).complete(current, 'ordinary-model', [])
    assert gateway.calls[0] == current.messages


@pytest.mark.asyncio
async def test_incomplete_parallel_tool_batch_does_not_inject_user_between_results(tmp_path, assets):
    current = state()
    pair(current, await materialize(receipt(), current, tmp_path), complete=False)
    gateway = Gateway()
    await ModelAdapter(gateway, resource_directory=tmp_path).complete(current, 'model-call', [])
    assert gateway.calls[0] == current.messages


@pytest.mark.asyncio
async def test_followup_after_complete_parallel_batch_does_not_hide_attachment(tmp_path, assets):
    current = state()
    pair(current, await materialize(receipt(), current, tmp_path), complete=False)
    current.messages.extend([{'role': 'tool', 'tool_call_id': 'other-call', 'content': '{}'},
        {'role': 'user', 'content': '还请结合最新补充判断'}])
    gateway = Gateway()
    await ModelAdapter(gateway, resource_directory=tmp_path).complete(current, 'model-call', [])
    wire = gateway.calls[0]
    assert [item['role'] for item in wire[-4:]] == ['tool', 'tool', 'user', 'user']
    assert wire[-2]['content'][1]['type'] == 'image_url'
    assert wire[-1] == current.messages[-1], 'new user input remains after the original attachment context'


@pytest.mark.asyncio
async def test_lost_owned_image_is_a_reread_hint_not_a_failed_task(tmp_path, assets):
    current = state()
    pair(current, await materialize(receipt(), current, tmp_path))
    Path(current.resources['image_artifacts'][0]['path']).unlink()
    gateway = Gateway()
    await ModelAdapter(gateway, resource_directory=tmp_path).complete(current, 'model-call', [])
    assert '重新读取' in gateway.calls[0][-1]['content']
    assert 'data:image' not in json.dumps(gateway.calls[0])


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['large', 'corrupt', 'wrong_mime', 'missing_content'])
async def test_unusable_image_is_one_attachment_failure_not_base64_text(tmp_path, assets, case):
    raw = receipt()
    image = raw['data']['attachment']['image']
    if case == 'large':
        image['data'] = 'A' * (4 * ((MAX_IMAGE_BYTES + 2) // 3) + 4)
    elif case == 'corrupt':
        image['data'] = base64.b64encode(b'not image bytes').decode('ascii')
    elif case == 'wrong_mime':
        image['mimeType'] = 'image/jpeg'
    else:
        raw['data']['attachment'].pop('image')
    public = await materialize(raw, state(), tmp_path)
    assert public['success'] is True
    assert public['data']['attachment']['status'] in {'too_large', 'unreadable'}
    assert 'image' not in public['data']['attachment']
    assert not assets.registered


@pytest.mark.asyncio
async def test_no_new_capability_only_fails_explicit_attachment_request(assets):
    old = {'success': True, 'code': 'OK', 'data': {'messages': [{'text': 'ordinary chat'}]}}
    plain = await materialize_attachment(old, {'session': 'same'}, tenant_id='tenant', user_id='user', tool_call_id='call')
    assert plain is old
    missing = await materialize_attachment(old, ARGS, tenant_id='tenant', user_id='user', tool_call_id='call')
    assert missing['success'] is False and missing['code'] == 'TOOL_NOT_SUPPORTED'
    assert 'messages' not in missing['data'] and not assets.registered


@pytest.mark.asyncio
async def test_text_chunk_needs_no_registration_or_model_conversion(assets):
    value = {'success': True, 'data': {'attachment': {'message_id': 11, 'kind': 'file',
        'status': 'available', 'text': '# partial', 'offset': 0, 'next_offset': 9, 'has_more': True}}}
    public = await materialize_attachment(value, ARGS, tenant_id='tenant', user_id='user', tool_call_id='call')
    assert public == value and not assets.registered


@pytest.mark.asyncio
async def test_attachment_expansion_never_advances_inbound_recovery_cursor(monkeypatch):
    from src.local_tools.wecom_flow import run
    current = state()
    current.resources['wecom_inbound_scan'] = {'invocation_id': 'original', 'offset': 2}
    original = copy.deepcopy(current.resources['wecom_inbound_scan'])
    before = AsyncMock(return_value=[])
    after = AsyncMock()
    monkeypatch.setattr('src.local_tools.wecom_inbound.before_read', before)
    monkeypatch.setattr('src.local_tools.wecom_inbound.after_read', after)
    monkeypatch.setattr('src.local_tools.overlay_flow.poll_operation', AsyncMock(return_value=receipt()))
    result = await run(SimpleNamespace(name='wecom_read_session'),
        SimpleNamespace(owner=SimpleNamespace(state=current)), ARGS)
    assert result['success']
    assert current.resources['wecom_inbound_scan'] == original
    before.assert_not_awaited()
    after.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('recover', [False, True])
async def test_local_adapter_consumes_bytes_on_normal_and_recovered_paths(tmp_path, assets, monkeypatch, recover):
    current = state()
    context = SimpleNamespace(session_id=IDENTITY.session_id, tenant_id=IDENTITY.tenant_id,
        user_id=IDENTITY.user_id, tool_call_id='attachment-call')
    executor = SimpleNamespace(execute=AsyncMock(return_value=receipt()),
        registry=SimpleNamespace(get_tool=lambda name: object()))
    if recover:
        monkeypatch.setattr('src.local_tools.domain_flow.supports_owned_tool', lambda tool: True)
        monkeypatch.setattr('src.services.agent_runner.local_owner.RunnerLocalLifecycle', lambda *args: object())
        monkeypatch.setattr('src.local_tools.domain_flow.recover_owned_tool', AsyncMock(return_value=receipt()))
    adapter = LocalToolAdapter(session_id=IDENTITY.session_id, execution_id=current.execution_id,
        subagent_config=None, llm=None, tool_executor=executor,
        control=SimpleNamespace(durable_owner=recover), state=current,
        attachments=AttachmentAssembler(workspace_directory(tmp_path, current.identity, current.execution_id)))
    events = [event async for event in adapter._run_local_required_tool('wecom_read_session', ARGS,
        IDENTITY.tenant_id, IDENTITY.user_id, context=context, recover=recover)]
    public = next(payload for kind, payload in events if kind == 'result')
    assert public['data']['attachment']['image']['file_id'] == 'file_0'
    assert len(assets.registered) == 1
    if recover:
        executor.execute.assert_not_awaited()


def test_old_inputs_stay_valid_and_new_read_does_not_consume_chat_cursor():
    from src.local_tools.proxy_tool import WecomReadSessionTool
    from src.tools.executor import normalize_provided_parameters
    from src.local_tools.durable_flow import supports_original_invocation
    assert supports_original_invocation(WecomReadSessionTool()), 'new attachment handling must not replace the old execute/recovery route'
    normalized = normalize_provided_parameters(WecomReadSessionTool(), ARGS)
    assert json.loads(json.dumps(normalized)) == ARGS, 'native invocation arguments must stay JSON, including nested attachment'
    assert ReadInput(session='same').model_dump(exclude_none=True) == {'session': 'same'}
    assert ReadInput(**ARGS).attachment.message_id == 11
    with pytest.raises(ValidationError):
        ReadInput(**ARGS, cursor='original cursor')
    with pytest.raises(ValidationError):
        ReadInput(session='same', attachment={'message_id': True})
    with pytest.raises(ValidationError):
        ReadInput(session='same', attachment={'message_id': 11, 'max_bytes': 8193})
