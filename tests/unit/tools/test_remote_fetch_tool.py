"""Official remote Fetch must preserve provenance and protect credentials/resources."""

import asyncio
import json

import httpx
import pytest

from src.services.web_search import remote_fetch

pytestmark = [pytest.mark.tools, pytest.mark.search]
URL = "https://Factory.example/public.pdf?reference=ABC%2fdef#section"
SECRET = "unit-deepseek-secret"


@pytest.fixture
def network(monkeypatch):
    original = httpx.AsyncClient
    requests, clients, options = [], [], []

    def install(payload=None, *, handler=None, status=200, content=None, headers=None):
        async def handle(request):
            requests.append(request)
            if handler:
                return await handler(request)
            if content is not None:
                return httpx.Response(status, content=content, headers=headers)
            return httpx.Response(status, json=payload, headers=headers)

        def client(**kwargs):
            options.append(kwargs)
            instance = original(transport=httpx.MockTransport(handle), **kwargs)
            clients.append(instance)
            return instance

        monkeypatch.setattr(remote_fetch.httpx, "AsyncClient", client)
        return requests, clients, options

    return install


async def execute(url=URL, *, api_keys=None, timeout_seconds=5):
    return await remote_fetch.fetch_deepseek_document(
        url, api_keys=[SECRET] if api_keys is None else api_keys, timeout_seconds=timeout_seconds,
    )


async def test_fixed_official_endpoint_key_and_provenance_do_not_claim_direct_or_final_url(network):
    requests, clients, options = network({"title": "Factory", "content": "Manufacturer public source text"})
    result = await execute(api_keys=[None, " ", "bad\nkey", f" {SECRET} ", "unused-key"])
    assert result["success"] and result["provider"] == "deepseek_fetch" and result["count"] == 1
    source = result["results"][0]
    assert source["url"] == source["requested_url"] == URL
    assert source["content_kind"] == "provider_fetch" and source["provider"] == "deepseek_fetch"
    assert source["final_url"] is None and source["final_url_verified"] is False
    assert source["freshness"] == "unknown" and source["retrieved_at"].endswith("+00:00")
    assert source["notice"] == remote_fetch.PROVENANCE_NOTICE
    assert str(requests[0].url) == "https://api.deepseek.com/v1/tools/fetch"
    assert requests[0].method == "POST" and json.loads(requests[0].content) == {"url": URL}
    assert requests[0].headers["authorization"] == f"Bearer {SECRET}"
    assert "verify" not in options[0] and options[0]["trust_env"] is False
    assert options[0]["follow_redirects"] is False
    assert clients[0].is_closed and SECRET not in str(result)


@pytest.mark.parametrize("keys", [[], [None, "", "  "], ["bad\nkey"], "a-string-is-not-a-key-list"])
async def test_missing_valid_key_does_not_contact_provider(network, keys):
    requests, clients, _ = network()
    result = await execute(api_keys=keys)
    assert result["debug"] == "missing_deepseek_key" and result["requested_url"] == URL
    assert requests == clients == []


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "https://user:secret@factory.example/", "file:///etc/passwd", "https://localhost/"])
async def test_literal_unsafe_url_does_not_send_or_echo_credentials(network, url):
    requests, clients, _ = network()
    result = await execute(url)
    assert not result["success"] and "requested_url" not in result and "secret" not in str(result)
    assert requests == clients == []


@pytest.mark.parametrize("status", [301, 401, 402, 403, 404, 429, 500, 504])
async def test_provider_status_is_stable_without_echoing_body_or_following_redirect(network, status):
    requests, clients, _ = network(status=status, content=f"api_key={SECRET}".encode(), headers={"location": "https://attacker.example/"})
    result = await execute()
    assert result["debug"] == f"provider_http_{status}" and result["requested_url"] == URL
    assert len(requests) == 1 and clients[0].is_closed and SECRET not in str(result)


@pytest.mark.parametrize("payload", [
    None, [], {"error": {"message": SECRET}}, {"title": "T"}, {"content": "body"},
    {"title": 2, "content": "body"}, {"title": "T", "content": []}, {"title": "T", "content": " \n"},
    {"title": "Just a moment...", "content": "Verify you are human"},
    {"title": "Domain", "content": "This domain is for sale. Buy this domain."},
])
async def test_invalid_empty_challenge_or_sale_response_is_not_evidence(network, payload):
    network(payload)
    result = await execute()
    assert result["debug"] == "invalid_response" and SECRET not in str(result)


async def test_invalid_json_is_safe(network):
    network(content=f"invalid token={SECRET}".encode())
    result = await execute()
    assert result["debug"] == "invalid_response" and SECRET not in str(result)


async def test_declared_response_limit_is_enforced(network):
    network({"title": "T", "content": "body"}, headers={"content-length": str(remote_fetch.MAX_RESPONSE_BYTES + 1)})
    assert (await execute())["debug"] == "response_too_large"


async def test_compressed_response_is_rejected_before_iteration_or_decoder(network):
    visited, closed = False, False

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal visited
            visited = True
            raise AssertionError("compressed stream must not be read or decoded")
            yield b"unreachable"

        async def aclose(self):
            nonlocal closed
            closed = True

    async def handler(request):
        return httpx.Response(200, stream=Body(), headers={"content-encoding": "gzip"})

    _, clients, _ = network(handler=handler)
    assert (await execute())["debug"] == "unsupported_content_encoding"
    assert not visited and closed and clients[0].is_closed


async def test_identity_stream_response_limit_is_enforced_without_content_length(network, monkeypatch):
    monkeypatch.setattr(remote_fetch, "MAX_RESPONSE_BYTES", 1024)

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield json.dumps({"title": "T", "content": "x" * 3000}).encode()

    async def handler(request):
        return httpx.Response(200, stream=Body(), headers={"content-encoding": "identity"})

    _, clients, _ = network(handler=handler)
    assert (await execute())["debug"] == "response_too_large" and clients[0].is_closed


async def test_text_limit_is_independent_of_response_size_limit(network, monkeypatch):
    monkeypatch.setattr(remote_fetch, "MAX_TEXT_CHARS", 10)
    network({"title": "T", "content": "x" * 11})
    assert (await execute())["debug"] == "response_too_large"


async def test_network_failure_does_not_echo_exception_or_key(network):
    async def handler(request):
        raise httpx.ConnectError(f"password={SECRET}")
    _, clients, _ = network(handler=handler)
    result = await execute()
    assert result["debug"] == "network_failed" and SECRET not in str(result) and clients[0].is_closed


async def test_timeout_and_cancellation_release_stream_and_client(network):
    entered, closed = asyncio.Event(), asyncio.Event()

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"never"

        async def aclose(self):
            closed.set()

    async def handler(request):
        return httpx.Response(200, stream=Body())

    _, clients, _ = network(handler=handler)
    result = await execute(timeout_seconds=.01)
    assert result["debug"] == "remote_timeout" and closed.is_set() and clients[0].is_closed
    entered.clear()
    closed.clear()
    task = asyncio.create_task(execute())
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and clients[-1].is_closed


@pytest.fixture
def tavily_payload():
    """Independent Extract response, deliberately unlike the DeepSeek shape."""
    return {"results": [{
        "url": "https://factory.example/about/", "raw_content": "Factory manufactures doors. Total manpower: 155.",
        "links": ["https://factory.example/contact", "mailto:sales@factory.example"],
    }], "failed_results": [], "response_time": 1.5}


async def extract(url="https://factory.example/about", *, api_key=SECRET, timeout_seconds=90):
    return await remote_fetch.fetch_tavily_document(url, api_key=api_key, timeout_seconds=timeout_seconds)


async def test_tavily_uses_extract_endpoint_and_preserves_full_content_links_and_unknown_provenance(network, tavily_payload):
    requests, clients, options = network(tavily_payload)
    result = await extract()
    assert result["success"] and result["provider"] == "tavily_extract"
    source = result["results"][0]
    assert source["provider"] == "tavily_extract" and source["content_kind"] == "provider_fetch"
    assert source["url"] == source["requested_url"] == "https://factory.example/about"
    assert source["title"] == ""
    assert source["content"].startswith(tavily_payload["results"][0]["raw_content"])
    assert "Provider-reported navigation links" in source["content"]
    assert all(link in source["content"] for link in tavily_payload["results"][0]["links"])
    assert source["links"] == tavily_payload["results"][0]["links"]
    assert source["final_url"] is None and source["final_url_verified"] is False and source["freshness"] == "unknown"
    assert "Tavily Extract" in source["notice"]
    request = requests[0]
    assert str(request.url) == "https://api.tavily.com/extract" and request.headers["authorization"] == f"Bearer {SECRET}"
    assert json.loads(request.content) == {
        "urls": ["https://factory.example/about"], "extract_depth": "advanced", "format": "markdown", "timeout": 30,
    }
    assert len(requests) == 1 and clients[0].is_closed and options[0]["follow_redirects"] is False
    assert SECRET not in str(result)


async def test_tavily_api_timeout_never_exceeds_remaining_budget(network, tavily_payload):
    requests, _, _ = network(tavily_payload)
    assert (await extract(timeout_seconds=12.5))["success"]
    assert json.loads(requests[0].content)["timeout"] == 12.5


@pytest.mark.parametrize("key", [None, "", "  ", "bad\nkey"])
async def test_tavily_missing_key_does_not_fall_back_or_contact_deepseek(network, key):
    requests, _, _ = network()
    result = await extract(api_key=key)
    assert result["debug"] == "missing_tavily_key" and result["provider"] == "tavily_extract" and not requests


@pytest.mark.parametrize("url", [
    "https://other.example/about", "https://factory.example.evil.test/about", "https://factory.example/other",
])
async def test_tavily_rejects_different_result_host_or_document(network, tavily_payload, url):
    tavily_payload["results"][0]["url"] = url
    network(tavily_payload)
    result = await extract()
    assert result["debug"] == "invalid_response" and "results" not in result


async def test_tavily_rejects_extra_results_and_failed_success_contradictions(network, tavily_payload):
    tavily_payload["results"].append(dict(tavily_payload["results"][0]))
    network(tavily_payload)
    assert (await extract())["debug"] == "invalid_response"
    tavily_payload["results"].pop()
    tavily_payload["failed_results"] = [{"url": "https://factory.example/about", "error": SECRET}]
    result = await extract()
    assert result["debug"] == "invalid_response" and SECRET not in str(result)


@pytest.mark.parametrize("payload,code", [
    ({"results": [], "failed_results": []}, "extract_failed"),
    ({"results": [], "failed_results": [{"url": "https://factory.example/about", "error": SECRET}]}, "extract_failed"),
    ({"results": [], "failed_results": [{"url": "https://other.example", "error": SECRET}]}, "invalid_response"),
    ({"results": [{"url": "https://factory.example/about", "raw_content": ""}]}, "invalid_response"),
    ({"results": [{"url": "https://factory.example/about", "raw_content": "body", "links": "bad"}]}, "invalid_response"),
    ({"results": [{"url": "https://factory.example/about", "raw_content": "Verify you are human"}]}, "invalid_response"),
    ({"results": [{"url": "https://factory.example/about", "raw_content": "This domain is for sale"}]}, "invalid_response"),
    ({"results": {}}, "invalid_response"),
])
async def test_tavily_partial_and_malformed_responses_never_become_evidence(network, payload, code):
    network(payload)
    result = await extract()
    assert result["debug"] == code and result["provider"] == "tavily_extract" and SECRET not in str(result)


@pytest.mark.parametrize("status", [401, 402, 429, 504])
async def test_tavily_error_status_stays_safe_and_does_not_switch_provider(network, status):
    requests, _, _ = network(status=status, content=f"secret={SECRET}".encode())
    result = await extract()
    assert result["debug"] == f"provider_http_{status}" and result["provider"] == "tavily_extract"
    assert len(requests) == 1 and SECRET not in str(result)


async def test_tavily_shares_bounded_text_transport_and_cancellation(network, tavily_payload, monkeypatch):
    monkeypatch.setattr(remote_fetch, "MAX_TEXT_CHARS", 10)
    network(tavily_payload)
    assert (await extract())["debug"] == "response_too_large"

    entered, closed = asyncio.Event(), asyncio.Event()

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"never"

        async def aclose(self):
            closed.set()

    async def handler(request):
        return httpx.Response(200, stream=Body())

    _, clients, _ = network(handler=handler)
    task = asyncio.create_task(extract())
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and clients[0].is_closed

@pytest.mark.parametrize("returned", ["https://factory.example/export?path=", "https://factory.example/export?path=/#other"])
async def test_tavily_never_normalizes_away_query_values_or_fragments(network, returned):
    network({"results": [{"url": returned, "raw_content": "Company source text"}], "failed_results": []})
    result = await extract(url="https://factory.example/export?path=/")
    assert result["debug"] == "invalid_response"


@pytest.mark.parametrize("link", [None, {}, "javascript:alert(1)", "https://user:secret@factory.example", "mailto:user@example.com\nsecret"])
async def test_invalid_navigation_metadata_cannot_become_successful_source(network, tavily_payload, link):
    tavily_payload["results"][0]["links"] = [link]
    network(tavily_payload)
    assert (await extract())["debug"] == "invalid_response"


async def test_navigation_is_included_in_the_combined_source_size_limit(network, tavily_payload, monkeypatch):
    content = tavily_payload["results"][0]["raw_content"]
    monkeypatch.setattr(remote_fetch, "MAX_TEXT_CHARS", len(content) + 5)
    network(tavily_payload)
    assert (await extract())["debug"] == "response_too_large"

async def test_tavily_pdf_without_title_retains_the_available_document_text(network):
    pdf_url = "https://factory.example/company-profile.pdf"
    network({"results": [{"url": pdf_url, "title": None, "raw_content": "Official company profile: factory manufacturing evidence"}], "failed_results": []})
    result = await extract(url=pdf_url)
    assert result["success"] and result["results"][0]["title"] == ""
    assert "manufacturing evidence" in result["results"][0]["content"]
    assert result["results"][0]["content_kind"] == "provider_fetch"
