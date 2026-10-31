"""Synthetic CLI/cache -> real MCP/Runtime/outbox -> scoped resource -> model vision.

Only Device HTTP/SQL/Redis and the remote model are substituted. No installed
client, UI, real account, business database or listener is used.
"""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity
from src.core.image_asset import ImageRegistry
from src.local_tools.proxy_tool import WecomReadSessionTool
from src.services.agent_runner.resource_paths import workspace_directory
from src.services.agent_runner.runtime.attachments import AttachmentAssembler
from src.services.agent_runner.runtime.executor import ModelAdapter
from src.services.agent_runner.runtime.local_tools import LocalToolAdapter
from src.tools.context import ToolExecutionContext

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = ROOT / 'clients/shared/local-tool-host-core/tests/fixtures/attachmentBridge.mjs'
pytestmark = pytest.mark.integration


@pytest.fixture(scope='session', autouse=True)
def init_db_pool():
    """This component chain replaces SQL IO; the parent PG fixture is inapplicable."""
    yield


class RedisBoundary:
    def make_key(self, *parts):
        return ':'.join(parts)

    def hset(self, *args):
        return True

    def expire(self, *args):
        return True


class Gateway:
    async def chat_with_tools(self, **kwargs):
        self.messages = copy.deepcopy(kwargs['messages'])
        return {'content': 'synthetic visual response'}

    def get_model_name(self):
        return 'fixture'

    def get_provider_name(self):
        return 'fixture'


@pytest.mark.asyncio
async def test_attachment_bytes_reach_model_and_text_stays_paged(tmp_path, monkeypatch):
    base = tmp_path / 'synthetic-wecom'
    for helper, arguments in [('group_monitor_fixture.py', ['setup']), ('attachment_fixture.py', [])]:
        result = subprocess.run([sys.executable, str(ROOT / 'clients/wecom-cli/tests/py' / helper),
            str(base), *arguments], cwd=ROOT, capture_output=True, timeout=30, check=True)
        if helper == 'attachment_fixture.py':
            info = json.loads(result.stdout)

    def runtime_read(arguments):
        completed = subprocess.run(['node', str(BRIDGE)], cwd=ROOT, input=json.dumps(arguments).encode(),
            env={**os.environ, 'WECOM_TEST_HOME': str(base)}, capture_output=True, timeout=90)
        assert completed.returncode == 0, completed.stderr.decode('utf-8', 'replace')
        wire = json.loads(completed.stdout.splitlines()[-1])
        assert wire['result_attempts'] == 2, 'lost ACK must resend original result, not rerun CLI'
        return wire['payload']

    arguments = {'session': '合成群甲', 'attachment': {'message_id': info['image_message_id']}}
    wire = runtime_read(arguments)
    assert wire['success'] is True
    original_bytes = base64.b64decode(wire['data']['attachment']['image']['data'])
    proxy_result = WecomReadSessionTool()._map_terminal({'id': 'anonymous-invocation',
        'state': 'succeeded', 'effect': wire['effect'], 'result_json': wire})

    # Production ImageRegistry/tenant storage; replace only Redis and SQL IO.
    import src.core.storage as storage
    import src.local_tools.wecom_attachments as bridge
    import src.services.files.asset_store as asset_store
    tenant_root = tmp_path / 'storage' / 'tenants'
    monkeypatch.setattr(storage, '_TENANTS_ROOT', str(tenant_root))
    monkeypatch.setenv('AGENT_RUNNER_STORAGE_ROOT', str(tmp_path / 'storage'))
    registry = ImageRegistry.__new__(ImageRegistry)
    registry._redis = RedisBoundary()
    monkeypatch.setattr(bridge, 'get_image_registry', lambda: registry)
    monkeypatch.setattr(asset_store, 'upsert_file_asset', lambda *args, **kwargs: None)
    identity = Identity('tenant-fixture', 'user-fixture', 'session-fixture')
    state = ExecutionState(identity, 'execution-fixture', AgentMode.MASTER, 'fixture', [])
    resources = tmp_path / 'resources'
    assembler = AttachmentAssembler(workspace_directory(resources, identity, state.execution_id))
    executor = SimpleNamespace(registry=SimpleNamespace(get_tool=lambda name: None),
        execute=AsyncMock(return_value=proxy_result))
    adapter = LocalToolAdapter(session_id=identity.session_id, execution_id=state.execution_id,
        subagent_config=None, llm=None, tool_executor=executor, state=state, attachments=assembler)
    context = ToolExecutionContext(tenant_id=identity.tenant_id, user_id=identity.user_id,
        session_id=identity.session_id, tool_call_id='attachment-call')
    public = None
    async for kind, payload in adapter._run_local_required_tool('wecom_read_session', arguments,
            identity.tenant_id, identity.user_id, context=context):
        if kind == 'result':
            public = payload
    assert public['data']['attachment']['image']['file_id'].startswith('file_')
    assert 'base64' not in json.dumps(public) and 'claim_token' not in public
    assert all(str(tmp_path) not in str(value) for value in public['data']['attachment'].values())
    state.messages = [{'role': 'assistant', 'tool_calls': [{'id': 'attachment-call',
        'type': 'function', 'function': {'name': 'wecom_read_session', 'arguments': json.dumps(arguments)}}]},
        {'role': 'tool', 'tool_call_id': 'attachment-call', 'content': json.dumps(public)}]
    checkpoint = state.checkpoint()
    restored = ExecutionState.restore(checkpoint)
    gateway = Gateway()
    await ModelAdapter(gateway, resource_directory=resources).complete(restored, 'model-call', [])
    image = gateway.messages[-1]['content'][1]
    assert image['type'] == 'image_url'
    assert base64.b64decode(image['image_url']['url'].split(',', 1)[1]) == original_bytes
    assert restored.checkpoint() == checkpoint, 'vision assembly must not store base64 in durable history'

    text = runtime_read({'session': '合成群甲', 'attachment': {'message_id': info['file_message_id'], 'max_bytes': 1024}})
    chunk = text['data']['attachment']
    assert chunk['status'] == 'available' and chunk['has_more'] is True
    assert chunk['next_offset'] <= 1024 and chunk['text'] != info['text']
    assert 'cursor' not in text['data'], 'attachment expansion must not advance timeline state'
