"""Allowlisted diagnostics before actual Browser fixtures are reclaimed."""
import json
import re

from .browser_io import owned_descendants


def _codes(value):
    # Provider/model text never becomes a log. Only existing stable code families
    # are recognized, and private values or exception text are never returned.
    return sorted(set(re.findall(r'\b(?:BROWSER|OWNER|RUN|WORKER|TOOL|CHECKPOINT|RECOVERY|RUNNER)_[A-Z0-9_]{1,72}\b|\b(?:INTERNAL_ERROR|SNAPSHOT_FAILED|SEQ_REJECTED|NAVIGATE_FAILED|COMMAND_TIMEOUT|CONTENT_FAILED)\b',
                                str(value or ''))))


def browser_diagnostics(workers, database, runner_id, marker, page, child):
    row = database.rows('SELECT status,attempt,revision,checkpoint FROM agent_runners WHERE runner_id=%s', (runner_id,))[0]
    envelope = json.loads(row['checkpoint']) if isinstance(row['checkpoint'], str) else row['checkpoint'] or {}
    root = envelope.get('execution') or {}
    tools = [{ 'phase': fact.get('phase'), 'result_recorded': fact.get('result_recorded'),
               'result_codes': _codes(fact.get('result')), 'name':
               'browser_automation' if (fact.get('call') or {}).get('name') == 'browser_automation' else 'other'}
             for fact in root.get('tools', {}).values()]
    requests = []
    for request in workers.provider.requests(marker):
        messages = request.get('messages') or []
        requests.append({'roles': [message.get('role') for message in messages],
                         'has_tools': bool(request.get('tools')),
                         'browser_decision_instruction': any(message.get('role') == 'system' and
                             '浏览器自动化助手，只输出 JSON' in str(message.get('content')) for message in messages),
                         'tool_codes': [_codes(message.get('content')) for message in messages if message.get('role') == 'tool']})
    browser_rows = database.rows('SELECT state,runtime_state,error_code,close_reason FROM bs_browser_runs WHERE runner_id=%s', (runner_id,))
    browser = [{'state': value['state'], 'runtime_state': value['runtime_state'],
                'codes': _codes(value['error_code']), 'closed': value['runtime_state'] == 'closed'} for value in browser_rows]
    receipt_rows = database.rows('SELECT phase,purpose FROM agent_runner_usage_receipts WHERE runner_id=%s', (runner_id,))
    receipts = [{'phase': value['phase'], 'purpose': value['purpose'] if value['purpose'] in
                 {'llm','background','compression','embedding','skill','chat_no_thinking','chat_with_tools'} else 'other'} for value in receipt_rows]
    exception_classes, locations, stable_codes = [], [], []
    if child is not None:
        log = workers.processes.root / f'child-{workers.processes.children.index(child)}.log'
        if log.is_file():
            text = log.read_text(errors='replace')
            exception_classes = re.findall(r'(?:kind|type)=([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Failure|Violation))\b', text)
            exception_classes += re.findall(r'^([A-Za-z_.]+(?:Error|Exception|Failure|Violation)):', text, re.MULTILINE)
            locations = re.findall(r'File "([^"\n]+\.py)", line (\d+)', text)[-12:]
            stable_codes = _codes(text)
    return {'runner_status': row['status'], 'attempt': row['attempt'], 'revision': row['revision'],
            'tools': tools, 'browser': browser, 'requests': requests, 'receipts': receipts,
            'page_requested': page.path in page.requests, 'unexpected_page_path_count': len(page.unexpected),
            'owned_descendant_count': len(owned_descendants(child.pid)) if child is not None else 0,
            'cli_exit': child.poll() if child is not None else None,
            'exception_classes': exception_classes, 'locations': locations, 'stable_codes': stable_codes}
