"""Controllable HTTP peer for real provider/Gateway calls in service tests.

Only external model IO is substituted. This module imports no application code
and has no access to the runner database, checkpoints or worker lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import uuid

_DEFAULT_USAGE = object()


@dataclass
class Reply:
    content: str = "Fixture model reply"
    tool_calls: list[dict] = field(default_factory=list)
    prompt_tokens: int = 11
    completion_tokens: int = 7
    cached_tokens: int = 0
    status: int = 200
    reported_usage: object = _DEFAULT_USAGE
    release: threading.Event | None = None
    arrived: threading.Event = field(default_factory=threading.Event)
    reasoning_content: str | None = None


@dataclass
class EmbeddingReply(Reply):
    embedding_tokens: int = 9
    dimensions: int = 1024


class ProviderPeer:
    """Explicit response scripts keyed by a fictional user-message marker.

    Each request consumes a reply, including a request whose client disconnects.
    Exhausting a script returns HTTP 409, so unexpected model replay cannot be
    hidden behind a default success response. Headers are never retained/logged.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._scripts: dict[str, list[Reply]] = {}
        self._requests: dict[str, list[dict]] = {}
        self._errors: list[str] = []
        self._credential = secrets.token_urlsafe(24)
        peer = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 5

            def log_message(self, *_):
                pass

            def do_POST(self):
                embedding = self.path.startswith("/v1/dashscope/services/embeddings/")
                if self.path != "/v1/chat/completions" and not embedding:
                    self.respond(404, {"error": "fixture endpoint not found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4 * 1024 * 1024:
                        raise ValueError("invalid body length")
                    request = json.loads(self.rfile.read(length))
                    messages = request.get("messages", [])
                    if embedding:
                        texts = request.get("input", {}).get("texts", [])
                        messages = [{"role": "user", "content": texts}]
                    # Match only user input, never internal prompts/tool results.
                    with peer._lock:
                        matches = []
                        # A queued second turn can legitimately contain the first
                        # marker in session history. The most recent matching user
                        # message owns this request, including its tool follow-ups.
                        for message in reversed(messages):
                            if message.get("role") != "user":
                                continue
                            user_text = json.dumps(message.get("content"), ensure_ascii=False)
                            matches = [marker for marker in peer._scripts if marker in user_text]
                            if matches:
                                break
                        if len(matches) != 1:
                            peer._errors.append("unmatched-or-ambiguous-input")
                            self.respond(409, {"error": "fixture input not registered"})
                            return
                        marker = matches[0]
                        index = len(peer._requests[marker])
                        peer._requests[marker].append(request)
                        script = peer._scripts[marker]
                        if index >= len(script):
                            peer._errors.append("unexpected-model-replay")
                            self.respond(409, {"error": "fixture script exhausted"})
                            return
                        reply = script[index]
                        if embedding != isinstance(reply, EmbeddingReply):
                            peer._errors.append("unexpected-provider-method")
                            self.respond(409, {"error": "fixture response method mismatch"})
                            return
                    reply.arrived.set()
                    if reply.release is not None and not reply.release.wait(timeout=45):
                        with peer._lock:
                            peer._errors.append("provider-gate-timeout")
                        self.respond(504, {"error": "fixture gate timed out"})
                        return
                    if reply.status != 200:
                        self.respond(reply.status, {"error": "fixture provider failure"})
                        return
                    if embedding:
                        self.respond(200, {
                            "request_id": f"fixture_embedding_{uuid.uuid4().hex}",
                            "output": {"embeddings": [
                                {"text_index": index, "embedding": [1.0] + [0.0] * (reply.dimensions - 1)}
                                for index, _ in enumerate(texts)
                            ]},
                            "usage": ({"total_tokens": reply.embedding_tokens}
                                      if reply.reported_usage is _DEFAULT_USAGE else reply.reported_usage),
                        })
                        return
                    message = {"role": "assistant", "content": reply.content}
                    if reply.reasoning_content is not None:
                        message["reasoning_content"] = reply.reasoning_content
                    if reply.tool_calls:
                        message["tool_calls"] = reply.tool_calls
                    self.respond(200, {
                        "id": f"fixture_call_{uuid.uuid4().hex}",
                        "model": request.get("model"),
                        "choices": [{"index": 0, "message": message,
                                     "finish_reason": "tool_calls" if reply.tool_calls else "stop"}],
                        "usage": ({"prompt_tokens": reply.prompt_tokens,
                                   "completion_tokens": reply.completion_tokens,
                                   "total_tokens": reply.prompt_tokens + reply.completion_tokens,
                                   "prompt_tokens_details": {"cached_tokens": reply.cached_tokens}}
                                  if reply.reported_usage is _DEFAULT_USAGE else reply.reported_usage),
                    })
                except (ValueError, TypeError, AttributeError):
                    with peer._lock:
                        peer._errors.append("malformed-provider-request")
                    self.respond(400, {"error": "fixture malformed request"})

            def respond(self, status, payload):
                data = json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    # Killing a worker deliberately disconnects this HTTP client.
                    pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # close() releases gates before joining handler threads; no detached
        # request threads should outlive an individual test fixture.
        self._server.daemon_threads = False
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def environment(self):
        base_url = f"http://127.0.0.1:{self._server.server_port}/v1"
        # Normal provider configuration; single-provider mode prevents failover
        # from sending fixture prompts to real external model endpoints.
        environment = {"LLM_PROVIDER": "qwen",
                       "LITE_MODEL_CODE": "qwen/qwen-runner-lite-fixture",
                       # Embeddings use the DashScope SDK, independently of the
                       # OpenAI-compatible Qwen client. SDK env names are verified
                       # against the runtime-installed SDK, not assumed aliases.
                       "DASHSCOPE_HTTP_BASE_URL": base_url + "/dashscope",
                       "DASHSCOPE_COMPATIBLE_BASE_URL": base_url,
                       "EMBEDDING_API_KEY": self._credential,
                       "DASHSCOPE_API_KEY": self._credential}
        # Child profiles can explicitly select a different provider. Configure
        # those normal routes locally as well, even though global failover is off.
        for provider in ("QWEN", "ZHIPU", "DEEPSEEK", "MOONSHOT"):
            environment.update({f"{provider}_BASE_URL": base_url,
                                f"{provider}_API_KEYS": self._credential,
                                f"{provider}_MODEL_CODE": f"{provider.lower()}-runner-fixture"})
        return environment

    def register(self, *replies: Reply, marker=None):
        if not replies:
            raise ValueError("A provider script must contain at least one reply")
        # Domain producers build their own messages (e.g. VL candidate name or
        # overlay candidate text). Match an explicit fictional input there,
        # without replacing the producer or giving this HTTP peer runner state.
        marker = marker or f"runner_fixture_input_{uuid.uuid4().hex}"
        if not isinstance(marker, str) or not marker:
            raise ValueError("A fixture input marker must be nonempty text")
        with self._lock:
            if marker in self._scripts:
                raise ValueError("A fixture input marker must be unique")
            self._scripts[marker] = list(replies)
            self._requests[marker] = []
        return marker

    def requests(self, marker):
        with self._lock:
            # Independent copies prevent assertions from mutating the HTTP fact.
            return json.loads(json.dumps(self._requests[marker]))

    @property
    def errors(self):
        with self._lock:
            return tuple(self._errors)

    def close(self):
        with self._lock:
            for script in self._scripts.values():
                for reply in script:
                    if reply.release is not None:
                        reply.release.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def tool_reply(name: str, arguments: dict, *, call_id=None, **kwargs):
    return Reply(content="", tool_calls=[{
        "id": call_id or f"fixture_tool_{uuid.uuid4().hex}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }], **kwargs)
