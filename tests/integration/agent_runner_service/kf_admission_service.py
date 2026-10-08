"""Prepared real socket service/Worker setup, no admission/authorization mocks.

Exact native environment and public response oracles await frozen handoff. This
module is intentionally not imported/run during mutable production preparation.
"""
from pathlib import Path
import secrets
import re
import socket
import uuid
from urllib.parse import urlsplit

import bcrypt
import httpx

from .conftest import wait_for

PROBE = Path(__file__).with_name('kf_admission_process.py')


class KfSourceApi:
    def __init__(self, processes, platform, *, provider_environment=None, source_id='wecom_kf_native', native_enabled=True):
        self.processes, self.platform = processes, platform
        self.service_id = source_id
        self._credential = secrets.token_urlsafe(32)
        self._service_token = self._credential  # Existing original Workers fixture contract.
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        self.url = 'http://127.0.0.1:' + str(port)
        self.environment = {**(provider_environment or {}),
            'AGENT_RUNNER_ENABLED': 'true', 'AGENT_RUNNER_WECOM_KF_ENABLED': str(native_enabled).lower(),
            'AGENT_RUNNER_SERVICE_ID': source_id,
            'AGENT_RUNNER_SERVICE_TOKEN_HASH': bcrypt.hashpw(self._credential.encode(), bcrypt.gensalt(rounds=4)).decode(),
            'AGENT_RUNNER_SERVICE_SOURCES': 'wecom_kf',
            'AGENT_RUNNER_WECOM_KF_SERVICE_TOKEN': self._credential,
            'AGENT_RUNNER_API_URL': self.url, 'REDIS_ENABLED': 'false',
            'KF_ADMISSION_FIXTURE_BASE_URL': platform.base_url}
        self.child = processes.start([str(PROBE), 'service', '--host', '127.0.0.1', '--port', str(port)],
            environment=self.environment, private_working_directory=True)

        def ready():
            assert self.child.poll() is None, 'Original source API exited before readiness'
            try:
                return httpx.get(self.url + '/health', timeout=.5).status_code == 200
            except httpx.TransportError:
                return False
        wait_for(ready, timeout=15)

    def headers(self, *, scope=None, input_ref=None):
        headers = {'X-AgentRunner-Service': self.service_id,
            'X-AgentRunner-Service-Token': self._credential}
        if scope is not None:
            headers.update({'X-Tenant-Id': scope.tenant_id,
                'X-AgentRunner-Channel-User': scope.actor_id,
                'X-AgentRunner-Channel-Chat': scope.open_kfid,
                'X-AgentRunner-Source': 'wecom_kf'})
        if input_ref is not None:
            headers['X-AgentRunner-Source-Input'] = input_ref
        return headers

    def call(self, method, path, *, headers=None, **kwargs):
        return httpx.request(method, self.url + path,
            headers=self.headers() if headers is None else headers, timeout=10, **kwargs)

    def close(self):
        self.processes.stop(self.child)
        assert self.child.poll() is not None


def start_original_worker(fleet, api, *, maximum=None, once=False, one_round_profile=False):
    identifier = 'kf_source_worker_' + uuid.uuid4().hex
    probe = PROBE.with_name('kf_admission_budget_process.py') if one_round_profile else PROBE
    args = [str(probe), 'worker', '--worker-id', identifier]
    if once:
        args.append('--once')
    if maximum is not None:
        args.extend(['--max-tasks', str(maximum)])
    # Original fleet retains all model/resource/preflight configuration. The
    # new service environment supplies only native peer and external SDK URL.
    environment = {**fleet.environment, **api.environment, 'QWEN_MODEL_CODE': fleet.models[0]}
    child = fleet.processes.start(args, environment=environment, private_working_directory=True)
    fleet.children.append((child, identifier))
    return child, identifier


def verify_original_resources(fleet, api):
    """Original effective DB/provider preflight, no simulated resource proof."""
    script = """
import runpy,sys
verify=runpy.run_path(sys.argv[1])["verify_resource_targets"]
verify(database_name=sys.argv[2],provider_port=int(sys.argv[3]))
"""
    environment = {**fleet.environment, **api.environment, 'QWEN_MODEL_CODE': fleet.models[0]}
    safety = Path(__file__).with_name('safety.py')
    child = fleet.processes.start(['-c', script, str(safety), fleet.processes.database.name,
        str(urlsplit(fleet.provider.environment['QWEN_BASE_URL']).port)],
        environment=environment, private_working_directory=True)
    try:
        assert child.wait(timeout=15) == 0, 'Original effective resource isolation preflight failed'
    finally:
        fleet.processes.stop(child)


def safe_original_child_diagnostic(processes):
    """Finite original log classes/codes/locations only, no body/URL/identity."""
    reports = []
    for index, path in enumerate(sorted(processes.root.glob('child-*.log'))):
        log = path.read_text(errors='replace')[-65536:]
        classes = re.findall(r'^([A-Za-z_.]+(?:Error|Exception|Failure|Violation))(?::|$)', log, re.MULTILINE)
        classes += re.findall(r'AgentRunner HTTP operation failed: ([A-Za-z_][A-Za-z0-9_]*); frames=', log)
        codes = re.findall(r'\b(?:SOURCE|KF_INGRESS|RUNNER|INPUT|CHECKPOINT|FINALIZE)_[A-Z0-9_]+\b', log)
        locations = re.findall(r'File "/app/(src/[A-Za-z0-9_./-]+\.py)", line (\d+), in ([A-Za-z0-9_<>]+)', log)
        locations += re.findall(r'/app/(src/[A-Za-z0-9_./-]+\.py):(\d+):([A-Za-z0-9_<>]+)', log)
        reports.append({'child_ordinal': index, 'exception_classes': sorted(set(classes))[:8],
            'error_codes': sorted(set(codes))[:16], 'locations': [list(v) for v in locations[-12:]]})
    return reports


def safe_source_execution_diagnostic(scope, identifier, processes):
    """Capture finite original state before owned fixture cleanup removes logs."""
    value = {'children': safe_original_child_diagnostic(processes),
        'owned_processes_live': sum(child.poll() is None for child in processes.children)}
    try:
        rows = scope.rows('SELECT status,attempt,cancel_requested,pause_requested,settlement_status FROM agent_runners WHERE runner_id=%s', (identifier,))
        value['runner'] = rows[0] if rows else None
        value['input_phases'] = scope.rows('SELECT phase,count(*) AS n FROM agent_runner_inputs WHERE current_runner_id=%s GROUP BY phase ORDER BY phase', (identifier,))
        value['control_phases'] = scope.rows('SELECT status,count(*) AS n FROM agent_runner_controls WHERE runner_id=%s GROUP BY status ORDER BY status', (identifier,))
        value['receipt_phases'] = scope.rows('SELECT phase,count(*) AS n FROM agent_runner_usage_receipts WHERE runner_id=%s GROUP BY phase ORDER BY phase', (identifier,))
    except Exception as error:
        value['diagnostic_exception_class'] = type(error).__name__
    return value
