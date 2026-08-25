#!/usr/bin/env python3
"""Manual D1 vertical client: next -> remote invoke -> tool result -> final."""
import argparse, json, uuid
from urllib.request import Request, urlopen

def post(base, path, token, body):
    request = Request(base.rstrip("/") + path, json.dumps(body).encode(), {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urlopen(request, timeout=120) as response: return json.load(response)

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--base-url", default="http://127.0.0.1:8000"); parser.add_argument("--token", required=True); parser.add_argument("--session-ref", required=True, help="Existing cloud session owned by the authenticated user"); parser.add_argument("--message", required=True)
    args = parser.parse_args(); suffix = uuid.uuid4().hex
    correlation = {name: f"{name}-{suffix}" for name in ("task_id", "execution_id", "attempt_id", "action_id", "invocation_id", "artifact_id", "evidence_stream_id", "release_id", "policy_decision_id")}
    correlation["session_ref"] = args.session_ref
    first = post(args.base_url, "/api/desktop/v1/agent/next", args.token, {"supported_protocol_versions": ["1.0"], "idempotency_key": f"next-{suffix}", "correlation": correlation, "input": {"type": "user_message", "content": args.message}})
    outcome = first["outcome"]
    if outcome["type"] != "remote_tool_call": raise SystemExit(f"Expected remote_tool_call, got {outcome['type']}")
    invocation = post(args.base_url, "/api/desktop/v1/tools/invoke", args.token, {"supported_protocol_versions": ["1.0"], "idempotency_key": f"invoke-{suffix}", "correlation": correlation, "tool_name": outcome["tool_name"], "target": outcome["target"], "schema_version": outcome["schema_version"], "schema_digest": outcome["schema_digest"], "arguments": outcome["arguments"], "policy_revision": "desktop-d1-v1", "authorization_ticket": outcome["authorization_ticket"]})
    continuation_correlation = dict(correlation)
    continuation_correlation["invocation_id"] = f"next-invocation-{suffix}"
    final = post(args.base_url, "/api/desktop/v1/agent/next", args.token, {"supported_protocol_versions": ["1.0"], "idempotency_key": f"result-{suffix}", "correlation": continuation_correlation, "input": {"type": "tool_result", "invocation_id": correlation["invocation_id"], "result": invocation["result"]}})
    if final["outcome"]["type"] != "final": raise SystemExit(f"Expected final, got {final['outcome']['type']}")
    print(json.dumps(final, ensure_ascii=False, indent=2))

if __name__ == "__main__": main()
