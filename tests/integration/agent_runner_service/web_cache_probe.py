"""Observe actual token cache hits without changing the verifier's result."""
from datetime import datetime
import json
import os
from pathlib import Path
import threading


def create_app():
    from src.api.agent_runner_web import create_app as real_create_app
    from src.services import auth_service
    from src.core.cache_utils import CacheKeys, get_cached

    app = real_create_app()
    # 收口后 fresh_web_subject 在认证服务内解析 verify_token；观测同一调用点。
    actual_verify = auth_service.verify_token
    observations = []
    lock = threading.Lock()
    destination = Path(os.environ["RUNNER_TEST_CACHE_REPORT"])

    def observed(token, *args, **kwargs):
        cached = get_cached(CacheKeys.TOKEN, token)
        valid = bool(cached and cached.get("expires_at") and
                     datetime.strptime(cached["expires_at"], "%Y-%m-%d %H:%M:%S") > datetime.now())
        actual = actual_verify(token, *args, **kwargs)
        with lock:
            observations.append({"hit": cached is not None, "valid": valid,
                                 "returned_fixture_user": bool(actual and actual.startswith("runner_actor_"))})
            try:
                destination.write_text(json.dumps(observations))
            except OSError:
                pass  # Observation must not change auth behavior.
        return actual

    auth_service.verify_token = observed
    return app
