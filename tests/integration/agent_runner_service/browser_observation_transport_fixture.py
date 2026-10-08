"""Original Runtime with isolated timing/HTTP transport only fault wrapper."""
from contextlib import contextmanager
import json
from pathlib import Path
import uuid

from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_worker import accept, runner, decoded
from .test_browser_owner_boundaries import delete_browser_facts


@contextmanager
def fault_runtime(workers, actor, database, page, redis_environment, config, mode):
    workers.environment.update(redis_environment)
    workers.environment.update(config['worker'])
    original = tool_reply('browser_automation', {}, call_id='transport-original-browser-call')
    marker = workers.provider.register(original, Reply(content=json.dumps(
        {'action': 'ask_user', 'reason': 'Fictional original observation'})))
    original.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' observe local fixture', 'url': page.url, 'headless': True})
    identifier = accept(workers.api, actor, marker)['runner_id']
    report, release = workers.root/'transport-entered', workers.root/'transport-release'
    from urllib.parse import urlsplit, urlunsplit
    target = urlunsplit(urlsplit(page.url)._replace(path='/forbidden-observation-redirect'))
    worker_id = 'observation-transport-' + uuid.uuid4().hex
    script = Path(__file__).with_name('browser_observation_transport_probe.py')
    child = workers.processes.start([str(script), mode, str(report), str(release), target,
        '--worker-id', worker_id], environment=workers.environment, private_working_directory=True)
    workers.children.append((child, worker_id))
    try:
        wait_for(lambda: runner(database, identifier)['status'] == 'waiting', timeout=40)
        node = decoded(runner(database, identifier)['checkpoint'])['execution']
        run_id = node['resources']['browser_runs']['transport-original-browser-call']['run_id']
        yield dict(runner_id=identifier, run_id=run_id, marker=marker,
                   child=child, report=report, release=release)
    finally:
        release.touch()
        workers.processes.stop(child)
        delete_browser_facts(database, identifier)
