"""Record already allocated test ownership; no fixture, DB or process mutation."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def record_context_resources(scope, processes, node, *, stage):
    assert stage in ('allocated', 'body_finally')
    assert scope.database.name.startswith('aid_test_')
    assert processes.database.name == scope.database.name
    key = hashlib.sha256((node + ':' + scope.database.name + ':' + scope.marker).encode()).hexdigest()
    directory = Path(__file__).resolve().parents[3] / 'tmp/agent-runner-evidence/m6-kf-context'
    target = directory / ('v4-b3-owned-' + key + '.json')
    current = json.loads(target.read_text()) if target.exists() else {
        'node': node, 'database_name': scope.database.name,
        'processes_root': str(processes.root), 'events': [],
        'boundary': 'Original allocated fixtures only; final root/database removal audited after pytest exits.'}
    assert current['node'] == node and current['database_name'] == scope.database.name
    assert current['processes_root'] == str(processes.root)
    current['events'].append({'utc': datetime.now(timezone.utc).isoformat(), 'stage': stage})
    target.write_text(json.dumps(current, indent=2) + '\n')
