"""Public fetch must preserve source text without opening a route to private networks."""

import asyncio
import gzip
import socket
import ssl
import sys
from types import ModuleType
from unittest.mock import AsyncMock

import fitz
import httpcore
import httpx
import pytest
from pydantic import ValidationError

from src.config.settings import ToolsConfig, WebFetchToolConfig, create_settings
from src.services.web_search import fetch
from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.search import fetch_tool

pytestmark = [pytest.mark.tools, pytest.mark.search]
PUBLIC_IP = "93.184.216.34"
HTML = b'<html><title>Factory</title><body><h1>Manufacturing</h1><p>Company original evidence.</p><a href="/contact">Contact</a></body></html>'


@pytest.fixture
def fetch_config(monkeypatch):
    config = WebFetchToolConfig(provider="native")
    monkeypatch.setattr(fetch, "_runtime_config", lambda: config)
    return config


@pytest.fixture
async def network(monkeypatch, fetch_config):
    original_client = httpx.AsyncClient
    dns = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    requests, clients, options = [], [], []

    def install(handler=None, content=HTML, media_type="text/html", headers=None):
        async def handle(request):
            requests.append(request)
            if handler:
                return await handler(request)
            return httpx.Response(200, content=content, headers={"Content-Type": media_type, **(headers or {})})

        def client(**kwargs):
            options.append(kwargs)
            instance = original_client(transport=httpx.MockTransport(handle), **kwargs)
            clients.append(instance)
            return instance

        monkeypatch.setattr(fetch.httpx, "AsyncClient", client)
        return requests, clients, options, dns

    return install


async def test_html_keeps_original_source_and_pins_tls_connection_without_proxy(network, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    requests, clients, options, dns = network()
    result = await fetch.fetch_public_document("https://factory.example/about")
    assert result["success"] and result["count"] == 1
    source = result["results"][0]
    assert source["url"] == source["requested_url"] == "https://factory.example/about"
    assert source["content_kind"] == "direct_html" and source["retrieved_at"].endswith("+00:00")
    assert "Company original evidence." in source["content"]
    assert "https://factory.example/contact" in source["content"]
    assert requests[0].url.host == PUBLIC_IP
    assert requests[0].headers["host"] == "factory.example"
    assert requests[0].extensions["sni_hostname"] == "factory.example"
    assert requests[0].headers["accept-encoding"] == "identity"
    assert dns.await_count == 1 and options[0]["trust_env"] is False
    assert options[0]["follow_redirects"] is False and all(client.is_closed for client in clients)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "https://user:secret@factory.example", "https://factory.example:444/a",
    "http://127.0.0.1", "https://10.1.2.3", "https://169.254.169.254", "https://localhost/a",
    "https://a.localhost/a", "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://[ff02::1]/",
    "https://factory.example/\nsecret", "http://100.64.0.1", "http://192.0.2.1", "http://0.0.0.0",
])
async def test_invalid_or_non_public_url_never_connects(network, url):
    requests, clients, _, _ = network()
    result = await fetch.fetch_public_document(url)
    assert not result["success"] and result["debug"] in {"invalid_url", "unsafe_url", "non_public_address"}
    assert requests == clients == []
    assert "secret" not in str(result)
    assert "requested_url" not in result


async def test_mixed_public_private_dns_answers_are_rejected(network):
    requests, _, _, dns = network()
    dns.return_value.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)))
    result = await fetch.fetch_public_document("https://factory.example/")
    assert result["debug"] == "non_public_address" and not requests


async def test_each_redirect_resolves_again_and_blocks_rebinding(network):
    async def redirect(request):
        return httpx.Response(302, headers={"location": "/new"})

    requests, clients, _, dns = network(handler=redirect)
    dns.side_effect = [dns.return_value, [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]]
    result = await fetch.fetch_public_document("https://factory.example/")
    assert result["debug"] == "non_public_address" and len(requests) == 1
    assert result["requested_url"] == "https://factory.example/"
    assert clients[0].is_closed and dns.await_count == 2


@pytest.mark.parametrize("location", ["http://169.254.169.254/latest", "http://[::ffff:10.0.0.1]/", "https://a:b@factory.example/"])
async def test_private_or_credential_redirect_cannot_issue_second_request(network, location):
    async def redirect(request):
        return httpx.Response(302, headers={"location": location})

    requests, clients, _, _ = network(handler=redirect)
    result = await fetch.fetch_public_document("https://factory.example/")
    assert not result["success"] and len(requests) == 1 and clients[0].is_closed


async def test_public_redirect_updates_source_without_reusing_tls_pool(network):
    async def redirect(request):
        if request.headers["host"] == "factory.example":
            return httpx.Response(302, headers={"location": "https://docs.example/final"})
        return httpx.Response(200, content=HTML, headers={"content-type": "text/html"})

    requests, clients, _, _ = network(handler=redirect)
    result = await fetch.fetch_public_document("https://factory.example/")
    assert result["results"][0]["url"] == "https://docs.example/final"
    assert result["results"][0]["requested_url"] == "https://factory.example/"
    assert len(clients) == 2 and all(client.is_closed for client in clients)
    assert [r.extensions["sni_hostname"] for r in requests] == ["factory.example", "docs.example"]


@pytest.mark.parametrize("content,headers,code", [
    (b"x", {"content-type": "image/png"}, "unsupported_content_type"),
    (HTML, {"content-length": str(fetch.MAX_DOWNLOAD_BYTES + 1)}, "download_too_large"),
    (gzip.compress(HTML), {"content-encoding": "gzip"}, "unsupported_content_encoding"),
    (b"<html><title>Just a moment...</title><body>Verify you are human</body></html>", {}, "access_challenge"),
    (b'<html><body><div id="root"></div><script src="app.js"></script></body></html>', {}, "requires_rendering"),
    (b"<html><body></body></html>", {}, "no_text"),
])
async def test_failures_are_explicit_without_browser_fallback(network, content, headers, code):
    _, clients, _, _ = network(content=content, headers=headers)
    result = await fetch.fetch_public_document("https://factory.example/")
    assert result["debug"] == code and all(client.is_closed for client in clients)


async def test_stream_size_is_enforced_even_without_content_length(network, monkeypatch):
    monkeypatch.setattr(fetch, "MAX_DOWNLOAD_BYTES", 10)
    network(content=HTML)
    assert (await fetch.fetch_public_document("https://factory.example/"))["debug"] == "download_too_large"


def pdf_bytes(*texts, encrypted=False):
    with fitz.open() as document:
        for text in texts:
            page = document.new_page()
            if text:
                page.insert_text((72, 72), text)
        if encrypted:
            return document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
        return document.tobytes()


async def test_real_pdf_parser_preserves_page_numbers_and_original_text(network):
    network(content=pdf_bytes("Manufacturer Alice", "Sales bob@example.com"), media_type="application/pdf")
    result = await fetch.fetch_public_document("https://factory.example/contacts.pdf")
    assert result["success"]
    source = result["results"][0]
    assert source["content_kind"] == "direct_pdf"
    assert "## Page 1" in source["content"] and "## Page 2" in source["content"]
    assert "Sales bob@example.com" in source["content"]
    assert source["pdf_complete"] is True and source["pdf_total_pages"] == 2
    assert (source["pdf_page_start"], source["pdf_page_end"]) == (1, 2)


async def test_large_pdf_can_read_selected_pages_without_raising_the_whole_document_limit(network):
    network(content=pdf_bytes(*(f"Evidence on page {n}" for n in range(1, 102))), media_type="application/pdf")
    refused = await fetch.fetch_public_document("https://factory.example/profile.pdf")
    assert refused["debug"] == "pdf_too_many_pages" and refused["pdf_total_pages"] == 101
    assert "pdf_start_page" in refused["retry_hint"]
    result = await fetch.fetch_public_document("https://factory.example/profile.pdf", pdf_start_page=100, pdf_page_count=2)
    source = result["results"][0]
    assert source["content_kind"] == "direct_pdf" and source["pdf_complete"] is False
    assert (source["pdf_total_pages"], source["pdf_page_start"], source["pdf_page_end"]) == (101, 100, 101)
    assert "## Page 100" in source["content"] and "Evidence on page 101" in source["content"]
    assert "Evidence on page 99" not in source["content"]


async def test_pdf_tail_is_reported_as_actual_pages_and_out_of_range_is_not_empty_success(network):
    network(content=pdf_bytes("one", "two", "three"), media_type="application/pdf")
    result = await fetch.fetch_public_document("https://factory.example/profile.pdf", pdf_start_page=3, pdf_page_count=20)
    source = result["results"][0]
    assert source["pdf_page_end"] == 3 and source["pdf_complete"] is False
    result = await fetch.fetch_public_document("https://factory.example/profile.pdf", pdf_start_page=4)
    assert result["debug"] == "pdf_page_out_of_range" and result["pdf_total_pages"] == 3


@pytest.mark.parametrize("options", [
    {"pdf_start_page": 0}, {"pdf_start_page": True}, {"pdf_start_page": "1"},
    {"pdf_page_count": 0}, {"pdf_page_count": 101}, {"pdf_page_count": 2.5},
])
async def test_invalid_page_selection_is_rejected_before_network(network, options):
    requests, _, _, _ = network()
    result = await fetch.fetch_public_document("https://factory.example/profile.pdf", **options)
    assert result["debug"] == "invalid_pdf_page_selection" and not requests


async def test_html_does_not_pretend_pdf_page_selection_was_honored(network):
    network()
    result = await fetch.fetch_public_document("https://factory.example/profile", pdf_start_page=1)
    assert result["debug"] == "pdf_page_selection_requires_pdf"


async def test_selected_pdf_pages_survive_real_tool_projection_and_read(network, tmp_path, monkeypatch):
    from src.tools.executor import ToolExecutor
    from src.tools.file.read_tool import ReadTool
    from src.tools.registry import ToolRegistry

    monkeypatch.setenv("AGENT_RUNNER_STORAGE_ROOT", str(tmp_path))
    network(content=pdf_bytes("Old page", "Source evidence", "Other page"), media_type="application/pdf")
    registry = ToolRegistry()
    registry.register(fetch_tool.WebFetchTool())
    registry.register(ReadTool())
    executor = ToolExecutor(registry)
    context = ToolExecutionContext(tenant_id="pdf-range", user_id="reader", session_id="selected")
    result = await executor.execute("web_fetch", {"url": "https://factory.example/profile.pdf",
        "pdf_start_page": 2, "pdf_page_count": 1}, context=context)
    source = result["results"][0]
    assert source["pdf_complete"] is False and source["pdf_total_pages"] == 3
    assert source["pdf_page_start"] == source["pdf_page_end"] == 2
    read = await executor.execute("read", {"file_path": source["body_file_path"]}, context=context)
    assert "## Page 2" in read["content"] and "Source evidence" in read["content"]
    assert "Old page" not in read["content"] and "Other page" not in read["content"]


@pytest.mark.parametrize("content,code", [
    (b"broken PDF token=secret", "pdf_parse_failed"),
    (pdf_bytes(""), "pdf_no_text"),
    (pdf_bytes("private", encrypted=True), "pdf_password_protected"),
], ids=["broken", "scan", "encrypted"])
async def test_unreadable_pdfs_do_not_claim_success_or_disclose_parser_errors(network, content, code):
    network(content=content, media_type="application/pdf")
    result = await fetch.fetch_public_document("https://factory.example/file.pdf")
    assert result["debug"] == code and "secret" not in str(result)


async def test_pdf_page_and_text_limits_fail_instead_of_silently_truncating(network, monkeypatch):
    monkeypatch.setattr(fetch, "MAX_PDF_PAGES", 1)
    network(content=pdf_bytes("one", "two"), media_type="application/pdf")
    assert (await fetch.fetch_public_document("https://factory.example/a.pdf"))["debug"] == "pdf_too_many_pages"
    monkeypatch.setattr(fetch, "MAX_PDF_PAGES", 10)
    monkeypatch.setattr(fetch, "MAX_TEXT_CHARS", 1)
    assert (await fetch.fetch_public_document("https://factory.example/a.pdf"))["debug"] == "text_too_large"


async def test_html_charset_from_document_is_not_lost(network):
    html = '<html><meta charset="gbk"><title>公司</title><body>公司公开原文及联系方式</body></html>'.encode("gbk")
    network(content=html)
    result = await fetch.fetch_public_document("https://factory.example/")
    assert "公司公开原文及联系方式" in result["results"][0]["content"]


async def test_html_base_href_preserves_relative_link_semantics_without_fetching_it(network):
    html = (b'<html><head><base href="javascript:invalid"><base href="https://factory.example/">'
            b'<base href="https://ignored.example/"></head><body><a href="contact">Contact</a></body></html>')
    requests, _, _, _ = network(content=html)
    result = await fetch.fetch_public_document("https://factory.example/en/about")
    assert result["success"]
    assert "https://factory.example/contact" in result["results"][0]["content"]
    assert "https://factory.example/en/contact" not in result["results"][0]["content"]
    assert "ignored.example" not in result["results"][0]["content"]
    assert len(requests) == 1


async def test_dns_failure_is_safe(network):
    _, _, _, dns = network()
    dns.side_effect = OSError("password=secret")
    result = await fetch.fetch_public_document("https://factory.example/")
    assert result["debug"] == "dns_failed" and "secret" not in str(result)


async def test_cancellation_propagates_and_releases_stream_and_client(network):
    entered, closed = asyncio.Event(), asyncio.Event()

    class WaitingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"never"

        async def aclose(self):
            closed.set()

    async def handler(request):
        return httpx.Response(200, stream=WaitingStream(), headers={"content-type": "text/html"})

    _, clients, _, _ = network(handler=handler)
    task = asyncio.create_task(fetch.fetch_public_document("https://factory.example/"))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and all(client.is_closed for client in clients)


async def test_total_timeout_covers_dns(network, fetch_config):
    _, _, _, dns = network()
    async def forever(*args, **kwargs):
        await asyncio.Event().wait()
    dns.side_effect = forever
    fetch_config.total_timeout_seconds = .01
    assert (await fetch.fetch_public_document("https://factory.example/"))["debug"] == "fetch_timeout"


@pytest.mark.parametrize("content", [
    b"<html><title>Factory</title><body></body></html>",
    b"<html><head><title>Factory</title></head></html>",
    b"<html><title>Factory</title></html>",
])
async def test_title_without_document_body_is_not_successful_evidence(network, content):
    network(content=content)
    assert (await fetch.fetch_public_document("https://factory.example/"))["debug"] == "no_text"


@pytest.fixture
def slow_parser(monkeypatch):
    original_spawn = asyncio.create_subprocess_exec
    started = asyncio.Event()
    processes = []

    async def spawn(*args, **kwargs):
        process = await original_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(fetch.asyncio, "create_subprocess_exec", spawn)
    return started, processes


async def test_total_timeout_kills_and_reaps_running_parser(network, slow_parser, fetch_config):
    network()
    started, processes = slow_parser
    fetch_config.total_timeout_seconds = .3
    result = await fetch.fetch_public_document("https://factory.example/")
    assert started.is_set() and result["debug"] == "fetch_timeout"
    assert len(processes) == 1 and processes[0].returncode is not None
    assert processes[0].returncode != 0


async def test_cancellation_kills_and_reaps_running_parser(network, slow_parser):
    network()
    started, processes = slow_parser
    task = asyncio.create_task(fetch.fetch_public_document("https://factory.example/"))
    await asyncio.wait_for(started.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert len(processes) == 1 and processes[0].returncode is not None
    assert processes[0].returncode != 0


async def test_parser_concurrency_is_bounded_and_cancelled_slots_are_reusable(network, slow_parser):
    network()
    _, processes = slow_parser
    tasks = [asyncio.create_task(fetch.fetch_public_document("https://factory.example/")) for _ in range(3)]
    for _ in range(100):
        if len(processes) == 2:
            break
        await asyncio.sleep(.02)
    assert len(processes) == 2
    await asyncio.sleep(.05)
    assert len(processes) == 2
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert all(process.returncode is not None for process in processes)
    assert fetch._PARSE_SLOTS.acquire(blocking=False)
    assert fetch._PARSE_SLOTS.acquire(blocking=False)
    assert not fetch._PARSE_SLOTS.acquire(blocking=False)
    fetch._PARSE_SLOTS.release()
    fetch._PARSE_SLOTS.release()


async def test_cancellation_during_spawn_also_reaps_child(network, monkeypatch):
    network()
    original_spawn = asyncio.create_subprocess_exec
    started, release = asyncio.Event(), asyncio.Event()
    processes = []

    async def delayed_spawn(*args, **kwargs):
        process = await original_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        processes.append(process)
        started.set()
        await release.wait()
        return process

    monkeypatch.setattr(fetch.asyncio, "create_subprocess_exec", delayed_spawn)
    task = asyncio.create_task(fetch.fetch_public_document("https://factory.example/"))
    await asyncio.wait_for(started.wait(), 3)
    task.cancel()
    await asyncio.sleep(.02)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert len(processes) == 1 and processes[0].returncode is not None


async def test_parser_output_is_bounded_and_failure_does_not_expose_it(network, monkeypatch):
    network()
    original_spawn = asyncio.create_subprocess_exec
    processes = []
    monkeypatch.setattr(fetch, "_PARSER_OUTPUT_LIMIT", 1024)

    async def spawn(*args, **kwargs):
        process = await original_spawn(
            sys.executable, "-c", "import sys,time; sys.stdout.write('secret'*1000); sys.stdout.flush(); time.sleep(60)",
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(fetch.asyncio, "create_subprocess_exec", spawn)
    result = await fetch.fetch_public_document("https://factory.example/")
    assert result["debug"] == "parser_output_too_large" and "secret" not in str(result)
    assert processes[0].returncode is not None


async def test_tool_requires_trusted_session_before_network(monkeypatch):
    api = AsyncMock()
    monkeypatch.setattr(fetch_tool, "fetch_public_document", api)
    with tool_execution_scope(None):
        result = await fetch_tool.WebFetchTool().execute(url="https://factory.example/", session_id="forged")
    assert result["debug"] == "missing_execution_context" and api.await_count == 0
    with tool_execution_scope(ToolExecutionContext(session_id="trusted")):
        result = await fetch_tool.WebFetchTool().execute(url="https://factory.example/", tenant_id="forged")
    assert result["debug"] == "invalid_parameters" and api.await_count == 0


async def test_tool_passes_fetch_failure_without_claiming_evidence(monkeypatch):
    monkeypatch.setattr(fetch_tool, "fetch_public_document", AsyncMock(return_value=fetch._failure("requires_rendering")))
    with tool_execution_scope(ToolExecutionContext(session_id="trusted")):
        result = await fetch_tool.WebFetchTool().execute(url="https://factory.example/")
    assert result["debug"] == "requires_rendering"


async def test_tool_publishes_source_inside_the_trusted_context(monkeypatch):
    payload = {"success": True, "results": [{"url": "https://factory.example/", "content": "original"}], "count": 1}
    monkeypatch.setattr(fetch_tool, "fetch_public_document", AsyncMock(return_value=payload))
    evidence = ModuleType("src.services.web_search.evidence")
    observed = []

    def publish(value):
        from src.tools.context import current_tool_execution_context
        observed.append((value, current_tool_execution_context()))
        return {"success": True, "results": [{"source_id": "trusted-source", "body_file_path": "evidence/body.md"}]}

    evidence.publish_research_result = publish
    monkeypatch.setitem(sys.modules, "src.services.web_search.evidence", evidence)
    context = ToolExecutionContext(tenant_id="trusted-tenant", session_id="trusted-session")
    with tool_execution_scope(context):
        result = await fetch_tool.WebFetchTool().execute(url="https://factory.example/")
    assert observed == [(payload, context)]
    assert result["results"][0]["source_id"] == "trusted-source"


async def test_httpcore_pins_tcp_but_verifies_original_tls_hostname(monkeypatch):
    """Check the real httpx/httpcore plumbing, rather than only mock request metadata."""
    original_client = httpx.AsyncClient
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))],
    ))
    calls = []

    class Stream(httpcore.AsyncNetworkStream):
        def __init__(self):
            self.body = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: " + str(len(HTML)).encode() + b"\r\n\r\n" + HTML

        async def read(self, max_bytes, timeout=None):
            result, self.body = self.body[:max_bytes], self.body[max_bytes:]
            return result

        async def write(self, buffer, timeout=None):
            calls.append(("write", buffer))

        async def aclose(self):
            calls.append(("close",))

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            calls.append(("tls", server_hostname, ssl_context.verify_mode, ssl_context.check_hostname))
            return self

        def get_extra_info(self, info):
            return None

    class Backend(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, host, port, **kwargs):
            calls.append(("tcp", host, port))
            return Stream()

    def client(**kwargs):
        transport = httpx.AsyncHTTPTransport()
        transport._pool = httpcore.AsyncConnectionPool(network_backend=Backend())
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(fetch.httpx, "AsyncClient", client)
    result = await fetch.fetch_public_document("https://factory.example/contact")
    assert result["success"]
    assert ("tcp", PUBLIC_IP, 443) in calls
    assert ("tls", "factory.example", ssl.CERT_REQUIRED, True) in calls
    assert any(value[0] == "write" and b"Host: factory.example" in value[1] for value in calls)
    assert ("close",) in calls


def test_tool_discovery_schema_has_no_tenant_or_browser_parameters():
    tool = fetch_tool.WebFetchTool()
    assert tool.name == "web_fetch" and tool.catalog
    schema = tool.to_tool_definition()["input_schema"]
    assert set(schema["properties"]) == {"url", "pdf_start_page", "pdf_page_count"}
    assert schema["required"] == ["url"]
    assert "body_file_path" in tool.usage_guide and "source_id" in tool.usage_guide
    assert "research_validate" not in tool.usage_guide


async def test_text_pdf_larger_than_five_mib_is_read_by_real_isolated_parser(network):
    data = pdf_bytes("Original large PDF public text") + b"\n%" + b"x" * (6 * 1024 * 1024) + b"\n"
    network(content=data, media_type="application/pdf")
    result = await fetch.fetch_public_document("https://factory.example/large.pdf")
    assert result["success"]
    assert result["results"][0]["content_kind"] == "direct_pdf"
    assert "Original large PDF public text" in result["results"][0]["content"]


async def test_default_pdf_limit_rejects_more_than_twenty_five_mib(network):
    data = pdf_bytes("Too large") + b"\n%" + b"x" * (25 * 1024 * 1024)
    network(content=data, media_type="application/pdf")
    result = await fetch.fetch_public_document("https://factory.example/oversized.pdf")
    assert result["debug"] == "download_too_large"
    assert result["limit_bytes"] == 25 * 1024 * 1024
    assert result["requested_url"] == "https://factory.example/oversized.pdf"


async def test_html_keeps_five_mib_limit_even_when_pdf_limit_is_raised(network, fetch_config):
    fetch_config.pdf_max_download_mb = 50
    network(headers={"content-length": str(5 * 1024 * 1024 + 1)})
    result = await fetch.fetch_public_document("https://factory.example/about")
    assert result["debug"] == "download_too_large" and result["limit_bytes"] == 5 * 1024 * 1024


async def test_configured_pdf_limit_is_used_before_download(network, fetch_config):
    fetch_config.pdf_max_download_mb = 1
    network(content=b"pdf", media_type="application/pdf", headers={"content-length": str(1024 * 1024 + 1)})
    result = await fetch.fetch_public_document("https://factory.example/a.pdf")
    assert result["debug"] == "download_too_large" and result["limit_bytes"] == 1024 * 1024


async def test_pdf_stream_enforces_configured_limit_without_content_length(network, fetch_config):
    fetch_config.pdf_max_download_mb = 1

    class OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * (512 * 1024)
            yield b"x" * (512 * 1024)
            yield b"extra"

    async def handler(request):
        return httpx.Response(200, stream=OversizedStream(), headers={"content-type": "application/pdf"})

    network(handler=handler)
    result = await fetch.fetch_public_document("https://factory.example/stream.pdf")
    assert result["debug"] == "download_too_large" and result["limit_bytes"] == 1024 * 1024
    assert result["requested_url"] == "https://factory.example/stream.pdf"


async def test_failure_after_redirect_keeps_exact_original_request_url(network):
    async def handler(request):
        if request.url.path == "/initial":
            return httpx.Response(302, headers={"location": "https://other.example/missing"})
        return httpx.Response(404)

    network(handler=handler)
    original = "https://FACTORY.example/initial?reference=ABC%2fdef#section"
    result = await fetch.fetch_public_document(original)
    assert result["debug"] == "http_status_404" and result["requested_url"] == original


@pytest.mark.parametrize("field,value", [
    ("pdf_max_download_mb", 0), ("pdf_max_download_mb", 51), ("pdf_max_download_mb", 1.5),
    ("total_timeout_seconds", 0), ("total_timeout_seconds", -1), ("total_timeout_seconds", 181),
])
def test_trusted_fetch_config_rejects_invalid_boundaries(field, value):
    with pytest.raises(ValidationError):
        WebFetchToolConfig(**{field: value})


def test_fetch_config_defaults_and_extremes():
    config = ToolsConfig().fetch
    assert config.pdf_max_download_mb == 25 and config.total_timeout_seconds == 90
    assert WebFetchToolConfig(pdf_max_download_mb=1).pdf_max_download_mb == 1
    assert WebFetchToolConfig(pdf_max_download_mb=50, total_timeout_seconds=180).total_timeout_seconds == 180


def test_fetch_environment_overrides_only_trusted_settings(monkeypatch, tmp_path):
    config_path = tmp_path / "empty.yaml"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("WEB_FETCH_PDF_MAX_DOWNLOAD_MB", "35")
    monkeypatch.setenv("WEB_FETCH_TOTAL_TIMEOUT_SECONDS", "120")
    config = create_settings(config_path).tools.fetch
    assert config.pdf_max_download_mb == 35 and config.total_timeout_seconds == 120
    monkeypatch.setenv("WEB_FETCH_PDF_MAX_DOWNLOAD_MB", "51")
    with pytest.raises(ValidationError):
        create_settings(config_path)
