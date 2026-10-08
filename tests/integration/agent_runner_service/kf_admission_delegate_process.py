"""Native external SDK URL + existing real owned-child timing harness.

Delegate wait timeout is the previously accepted 5s timing DI. It never makes
up a child result. Restoring execution uses the default CLI, not this harness.
"""
import os
from urllib.parse import urlsplit


endpoint = os.environ['KF_ADMISSION_FIXTURE_BASE_URL']
value = urlsplit(endpoint)
if value.scheme != 'http' or value.hostname != '127.0.0.1' or not value.port or value.path not in ('', '/') or value.username or value.password or value.query or value.fragment:
    raise RuntimeError('FICTIONAL_PLATFORM_ENDPOINT_REQUIRED')
from src.channels.wecom_kf.api_client import WeComKfApiClient
WeComKfApiClient.BASE_URL = endpoint.rstrip('/')
from tests.integration.agent_runner_service.delegate_timeout_probe import main
main()
