"""Actual shared Write/cp artifacts delivered by a fresh real main file route."""
import socket
import uuid
from pathlib import Path

import httpx
import pytest

from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_worker import workers, prices, api_pair, accept, decoded, terminal, runner

pytestmark = pytest.mark.integration


def start_download(workers):
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
    process = workers.processes.start(['-m', 'tests.integration.agent_runner_service.file_download_probe', '--port', str(port)],
        environment=workers.environment, private_working_directory=True)
    url = f'http://127.0.0.1:{port}'
    def ready():
        assert process.poll() is None, 'Test-owned actual file route process exited'
        try:
            return httpx.get(url + '/health', timeout=.5).status_code == 200
        except httpx.TransportError:
            return False
    wait_for(ready, timeout=15)
    return url


@pytest.mark.parametrize('actor_name', ['a', 'global'])
def test_default_cli_write_cp_delivers_shared_artifact_from_different_cwd_without_cache(
        workers, actors, service_database, actor_name):
    actor = actors[actor_name]
    tenant = actor.tenant_id or '_anonymous'
    filename = 'shared-worker-fictional-' + uuid.uuid4().hex + '.txt'
    generated = workers.storage / 'tenants' / tenant / 'conversation' / filename
    content = 'fictional shared worker artifact bytes'
    marker = workers.provider.register(
        tool_reply('write', {'file_path': str(generated), 'content': content}, call_id='shared-original-write'),
        tool_reply('cp', {'source_file_path': str(generated), 'display_name': 'fictional-shared-delivery.txt'}, call_id='shared-original-cp'),
        Reply(content='fictional shared artifact delivered'))
    accepted = accept(workers.api, actor, marker)
    process, _ = workers.start()
    workers.assert_clean_exit(process)
    finished = terminal(service_database, accepted['runner_id'])
    assert finished['status'] == 'completed' and generated.read_text() == content
    checkpoint = decoded(finished['checkpoint'])['execution']
    write = checkpoint['tools']['shared-original-write']
    copied = checkpoint['tools']['shared-original-cp']
    assert write['success'] and copied['success']
    write_result = decoded(write['result'])
    cp_result = decoded(copied['result'])
    assert Path(write_result['file_path']) == generated
    delivered = Path(cp_result['file_path'])
    assert delivered.is_relative_to(workers.storage / 'tenants' / tenant)
    assert delivered.read_text() == content and cp_result['file_id']
    url = start_download(workers)
    # Redis is explicitly disabled in each fresh process: the actual route must
    # recover the committed copied file from the common mount. Existing opaque
    # public file URL behavior is preserved, not presented as tenant auth.
    response = httpx.get(url + cp_result['download_url'], timeout=10)
    assert response.status_code == 200 and response.content == content.encode()
    public = workers.api.call('GET', '/v1/runners/' + accepted['runner_id'], actor=actor).json()['runner']
    assert cp_result['file_id'] in str(public['snapshot']['progressMessages'])
    assert len(workers.provider.requests(marker)) == 3 and not workers.provider.errors


def test_default_cli_cp_cannot_publish_another_tenant_shared_file(workers, actors, service_database):
    foreign = workers.storage / 'tenants' / actors['a'].tenant_id / 'conversation' / 'fictional-foreign.txt'
    foreign.parent.mkdir(parents=True)
    foreign.write_text('fictional foreign owner bytes')
    marker = workers.provider.register(
        tool_reply('cp', {'source_file_path': str(foreign)}, call_id='foreign-cp'),
        Reply(content='foreign copy denied'))
    accepted = accept(workers.api, actors['b'], marker)
    process, _ = workers.start()
    workers.assert_clean_exit(process)
    finished = terminal(service_database, accepted['runner_id'])
    fact = decoded(finished['checkpoint'])['execution']['tools']['foreign-cp']
    assert '其他租户' in str(fact['result']) and 'file_id' not in str(fact['result'])
    assert foreign.read_text() == 'fictional foreign owner bytes'
    directory = workers.storage / 'tenants' / actors['b'].tenant_id
    assert not directory.exists() or not list(directory.rglob('*.*'))
    assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors
