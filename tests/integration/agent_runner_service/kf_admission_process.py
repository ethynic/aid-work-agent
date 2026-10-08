"""Prepared subprocess entry: original services with fictional platform IO URL.

No fake Owner, authorizer, repository, model result or SDK method. Class URL
routing affects only the SDK's outbound HTTP origin in these owned children.
"""
import os
import sys
from urllib.parse import urlsplit


def main():
    mode = sys.argv.pop(1)
    endpoint = os.environ['KF_ADMISSION_FIXTURE_BASE_URL']
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port \
            or parsed.path not in ('', '/') or parsed.username or parsed.password \
            or parsed.query or parsed.fragment:
        raise RuntimeError('FICTIONAL_PLATFORM_ENDPOINT_REQUIRED')
    from src.channels.wecom_kf.api_client import WeComKfApiClient
    WeComKfApiClient.BASE_URL = endpoint.rstrip('/')
    if mode == 'service':
        from src.services.agent_runner.bootstrap import main as original_main
    elif mode == 'worker':
        from src.services.agent_runner.worker import main as original_main
    else:
        raise RuntimeError('INVALID_ORIGINAL_ENTRY_FIXTURE')
    original_main()


if __name__ == '__main__':
    main()
