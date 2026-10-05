"""Narrow actual protocol observation; no substituted Browser/Core results."""
import json
from pathlib import Path
import threading
import uuid

import pytest

from .browser_io import browser_page, browser_redis
from .browser_diagnostics import browser_diagnostics
from .provider import Reply, tool_reply
from .test_worker import workers, api_pair, prices, accept

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def test_actual_browser_page_protocol_reaches_real_decision_with_safe_observation(
        workers, actors, service_database, browser_page, browser_redis):
    actor = actors['a']
    _, _, environment = browser_redis
    workers.environment.update(environment)
    workers.environment['AGENT_RUNNER_BROWSER_OWNER_ENABLED'] = 'true'
    release = threading.Event()
    initial = tool_reply('browser_automation', {}, call_id='observed-browser-protocol-call')
    decision = Reply(content=json.dumps({'action':'done','reason':'Fictional protocol verified'}),release=release)
    marker = workers.provider.register(initial, decision,
                                       Reply(content='Fictional browser protocol final'))
    initial.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task':marker + ' inspect fixture','url':browser_page.url,'headless':True})
    accepted = accept(workers.api,actor,marker)
    identifier = accepted['runner_id']
    report = workers.root/'browser-protocol-observations.json'
    probe = Path(__file__).with_name('browser_producer_probe.py')
    worker_id = 'observed-browser-' + uuid.uuid4().hex
    child = workers.processes.start([str(probe),str(report),'--worker-id',worker_id,'--once'],
                                    environment=workers.environment,private_working_directory=True)
    workers.children.append((child,worker_id))
    try:
        arrived = decision.arrived.wait(timeout=25)
        diagnosis = browser_diagnostics(workers,service_database,identifier,marker,browser_page,child)
        diagnosis['protocol_observations'] = json.loads(report.read_text()) if report.exists() else []
        assert arrived and diagnosis['requests'][-1]['browser_decision_instruction'], json.dumps(diagnosis)
        page_events = [event for event in diagnosis['protocol_observations'] if event['boundary'].startswith('page_')]
        assert page_events and all(event.get('success') for event in page_events), json.dumps(diagnosis)
    finally:
        release.set()
        workers.processes.stop(child)
        service_database.rows('DELETE FROM bs_browser_assistance_requests WHERE runner_id=%s',(identifier,))
        service_database.rows('DELETE FROM bs_browser_runs WHERE runner_id=%s',(identifier,))
