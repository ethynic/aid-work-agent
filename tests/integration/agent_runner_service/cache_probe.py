"""Observe the real token verifier; never replace its result or its DB checks."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import threading

import uvicorn

from src.services.agent_runner.bootstrap import create_app
from src.core.cache_utils import CacheKeys, get_cached


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    app = create_app()
    authorizer = app.state.runner_manager.authorizer
    real_verify_token = authorizer.token_verifier
    observations, lock = [], threading.Lock()

    def observed(token, *arguments, **keywords):
        cached = get_cached(CacheKeys.TOKEN, token)
        valid = False
        if cached and cached.get("expires_at"):
            valid = datetime.strptime(cached["expires_at"], "%Y-%m-%d %H:%M:%S") > datetime.now()
        actual = real_verify_token(token, *arguments, **keywords)
        # Do not write raw token, cache key, credential or environment mapping.
        cached_user = cached.get("user_id") if cached else None
        cached_user = cached_user if cached_user and cached_user.startswith("runner_actor_") else None
        reported_user = actual if actual and actual.startswith("runner_actor_") else None
        with lock:
            observations.append({"hit": cached is not None, "valid": valid, "cached_user_id": cached_user,
                                 "returned_user_id": reported_user})
            try:
                Path(args.report).write_text(json.dumps(observations))
            except OSError:
                pass  # Observation failure cannot change the real auth result.
        return actual

    authorizer.token_verifier = observed
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
