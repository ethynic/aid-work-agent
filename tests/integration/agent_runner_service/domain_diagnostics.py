"""Optional read-only failure observation before owned fixtures delete rows.

Load with pytest -p. Only status, counts, phases and stable error codes enter
the captured failure output; no body/args/provider log/credential is copied.
"""
from collections import Counter
import json
import re

import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != 'call' or not report.failed:
        return
    database = item.funcargs.get('service_database')
    actors = item.funcargs.get('actors')
    domain = item.funcargs.get('recruiting_domain')
    if database is None or (not actors and not domain):
        return
    sessions = [actor.session_id for actor in actors.values()] if actors else [domain[0].session_id]
    try:
        rows = database.rows('SELECT runner_id,status,attempt,revision,checkpoint FROM agent_runners WHERE session_id=ANY(%s)', (sessions,))
        diagnostics = []
        for row in rows:
            checkpoint = row['checkpoint'] or {}
            if isinstance(checkpoint, str):
                checkpoint = json.loads(checkpoint)
            execution = checkpoint.get('execution') or {}
            phases = execution.get('resources', {}).get('local_domain_phases', {})
            receipts = database.rows('SELECT phase FROM agent_runner_usage_receipts WHERE runner_id=%s', (row['runner_id'],))
            tools = execution.get('tools') or {}
            # Codes are allowlisted by syntax/prefix, never error text.
            codes = sorted({code for fact in tools.values()
                if isinstance(fact.get('result'), dict)
                for code in [fact['result'].get('code')]
                if isinstance(code, str) and re.fullmatch(r'(?:LOCAL|RUNNER|RECOVERY|RESUME|COVERED|CHECKPOINT)_[A-Z0-9_]+', code)})
            diagnostics.append({'status': row['status'], 'attempt': row['attempt'],
                'revision': row['revision'], 'outcome': execution.get('outcome'),
                'domain_phases': [{'branch': fact.get('branch'), 'ordinal': fact.get('ordinal'),
                                  'phase': fact.get('phase')} for fact in phases.values()],
                'receipt_phases': dict(Counter(fact['phase'] for fact in receipts)),
                'claim_count': database.rows('SELECT count(*) AS n FROM agent_runner_session_claims WHERE owner_runner_id=%s', (row['runner_id'],))[0]['n'],
                'codes': codes})
        report.sections.append(('owned domain safe facts before teardown', json.dumps(diagnostics, sort_keys=True)))
    except Exception as error:
        # Diagnostic failure must neither replace nor suppress the real test.
        report.sections.append(('owned domain diagnostic unavailable', type(error).__name__))
