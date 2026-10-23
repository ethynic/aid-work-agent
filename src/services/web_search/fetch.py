"""Bounded public HTTP document retrieval, without browser tools, with explicit remote fallback."""

import asyncio
import ipaddress
import json
import os
import socket
import ssl
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_PDF_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 1_000_000
MAX_PDF_PAGES = 100
MAX_REDIRECTS = 5
_PARSE_SLOTS = threading.BoundedSemaphore(2)
_PARSER_OUTPUT_LIMIT = MAX_TEXT_CHARS * 6 + 65536


class _FetchError(Exception):
    def __init__(self, code: str, **details):
        self.code = code
        self.details = details


def _failure(code: str) -> dict:
    return {"success": False, "error": "无法读取公开文档，请根据失败类型调整来源", "debug": code}


def _runtime_config():
    # Parser workers do not load deployment settings or their credentials.
    from src.config.settings import settings
    return settings.tools.fetch


def _public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def _validate_url(value: str) -> httpx.URL:
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise _FetchError("invalid_url")
    if any(ord(character) <= 32 or ord(character) == 127 for character in value):
        raise _FetchError("invalid_url")
    try:
        url = httpx.URL(value)
        if url.scheme not in ("http", "https") or not url.host:
            raise _FetchError("invalid_url")
        if url.userinfo or url.port not in (None, 80 if url.scheme == "http" else 443):
            raise _FetchError("unsafe_url")
        host = url.host.rstrip(".").lower()
        if host == "localhost" or host.endswith(".localhost") or "%" in host:
            raise _FetchError("unsafe_url")
        try:
            if not _public_ip(host):
                raise _FetchError("non_public_address")
        except ValueError:
            pass  # Hostnames are checked against every DNS answer below.
        return url.copy_with(fragment=None)
    except (httpx.InvalidURL, ValueError):
        raise _FetchError("invalid_url") from None


async def _resolve_public_ip(url: httpx.URL) -> str:
    try:
        address = ipaddress.ip_address(url.host)
    except ValueError:
        try:
            records = await asyncio.get_running_loop().getaddrinfo(
                url.host, url.port or (443 if url.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError:
            raise _FetchError("dns_failed") from None
        addresses = [record[4][0] for record in records]
        if not addresses or any(not _public_ip(address) for address in addresses):
            raise _FetchError("non_public_address")
        return addresses[0]
    if not _public_ip(str(address)):
        raise _FetchError("non_public_address")
    return str(address)


def _parse_html(data: bytes, encoding: str | None, final_url: str) -> tuple[str, str]:
    soup = BeautifulSoup(data, "html.parser", from_encoding=encoding)
    title = soup.title.get_text(" ", strip=True)[:4096] if soup.title else final_url
    link_base = httpx.URL(final_url)
    for base in soup.find_all("base", href=True):
        try:
            candidate = link_base.join(base["href"])
            if candidate.scheme in ("http", "https") and candidate.host:
                link_base = candidate
                break
        except (httpx.InvalidURL, ValueError):
            continue
    had_script = soup.find("script") is not None
    for node in soup.find_all("title"):
        node.decompose()
    for node in soup(["head", "script", "style", "template", "svg", "noscript"]):
        node.decompose()
    visible = soup.get_text(" ", strip=True)
    lowered = f"{title} {visible}".lower()
    challenge = ("just a moment", "verify you are human", "checking your browser",
                 "attention required!", "enable javascript and cookies", "人机验证", "安全验证")
    if len(visible) < 5000 and any(marker in lowered for marker in challenge):
        raise _FetchError("access_challenge")
    if len(visible) < 80 and had_script:
        raise _FetchError("requires_rendering")
    if not visible:
        raise _FetchError("no_text")
    for anchor in soup.find_all("a", href=True):
        try:
            anchor["href"] = str(link_base.join(anchor["href"]))
        except (httpx.InvalidURL, ValueError):
            del anchor["href"]
    body = markdownify(str(soup.body or soup), heading_style="ATX").strip()
    if not body:
        raise _FetchError("no_text")
    if len(body) > MAX_TEXT_CHARS:
        raise _FetchError("text_too_large")
    return title, body


def _parse_pdf(data: bytes, final_url: str) -> tuple[str, str]:
    import fitz

    try:
        with fitz.open(stream=data, filetype="pdf") as document:
            if document.needs_pass:
                raise _FetchError("pdf_password_protected")
            if document.page_count > MAX_PDF_PAGES:
                raise _FetchError("pdf_too_many_pages")
            title = (document.metadata.get("title") or final_url)[:4096]
            pages, total, has_text = [], 0, False
            for number, page in enumerate(document, 1):
                text = page.get_text().strip()
                has_text = has_text or bool(text)
                value = f"## Page {number}\n\n{text}"
                total += len(value)
                if total > MAX_TEXT_CHARS:
                    raise _FetchError("text_too_large")
                pages.append(value)
            if not has_text:
                raise _FetchError("pdf_no_text")
            return title, "\n\n".join(pages)
    except _FetchError:
        raise
    except Exception:
        raise _FetchError("pdf_parse_failed") from None


async def _write_parser_input(process, metadata: bytes, data: bytes) -> None:
    process.stdin.write(metadata + b"\n")
    process.stdin.write(data)
    await process.stdin.drain()
    process.stdin.close()


async def _parse_isolated(data: bytes, media_type: str, encoding: str | None, final_url: str,
                          download_limit_bytes: int) -> tuple[str, str]:
    """Terminate document parsing on timeout/cancellation, including CPU-bound parsers.

    The nonblocking process-wide semaphore also works with different event loops.
    The outer fetch deadline covers waiting for a slot and subprocess startup.
    This bounds concurrency and lifetime; it is not an OS memory sandbox.
    """
    while not _PARSE_SLOTS.acquire(blocking=False):
        await asyncio.sleep(.02)
    process = writer = creation = None
    try:
        metadata = json.dumps({
            "media_type": media_type, "encoding": encoding, "url": final_url,
            "max_text_chars": MAX_TEXT_CHARS, "max_pdf_pages": MAX_PDF_PAGES,
            "max_download_bytes": download_limit_bytes,
        }, ensure_ascii=True).encode("ascii")
        environment = {key: value for key, value in os.environ.items() if key.upper() in {
            "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL",
        }}
        creation = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, "-X", "utf8", "-m", "src.services.web_search.fetch",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, cwd=str(Path(__file__).resolve().parents[3]),
            env=environment, limit=65536,
        ))
        # Shield creation so cancellation cannot orphan an as-yet-unreturned child.
        process = await asyncio.shield(creation)
        writer = asyncio.create_task(_write_parser_input(process, metadata, data))
        output = bytearray()
        while chunk := await process.stdout.read(65536):
            if len(output) + len(chunk) > _PARSER_OUTPUT_LIMIT:
                raise _FetchError("parser_output_too_large")
            output.extend(chunk)
        await writer
        if await process.wait() != 0:
            raise _FetchError("document_parse_failed")
        try:
            parsed = json.loads(output)
        except (UnicodeDecodeError, ValueError):
            raise _FetchError("document_parse_failed") from None
        if not isinstance(parsed, dict):
            raise _FetchError("document_parse_failed")
        if parsed.get("debug"):
            codes = {"access_challenge", "requires_rendering", "no_text", "text_too_large",
                     "pdf_password_protected", "pdf_too_many_pages", "pdf_no_text", "pdf_parse_failed"}
            raise _FetchError(parsed["debug"] if parsed["debug"] in codes else "document_parse_failed")
        title, content = parsed.get("title"), parsed.get("content")
        if not isinstance(title, str) or not isinstance(content, str) or len(content) > MAX_TEXT_CHARS:
            raise _FetchError("document_parse_failed")
        return title, content
    finally:
        try:
            if process is None and creation is not None:
                process = await creation
            if process is not None:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                await process.wait()
            if writer is not None:
                writer.cancel()
                await asyncio.gather(writer, return_exceptions=True)
        finally:
            _PARSE_SLOTS.release()


async def _fetch(url: str, pdf_limit_bytes: int) -> dict:
    current = _validate_url(url)
    for redirect in range(MAX_REDIRECTS + 1):
        ip = await _resolve_public_ip(current)
        # Connect to the validated IP, never resolve the original name again.
        # httpcore passes sni_hostname to start_tls for SNI AND certificate checks.
        # A fresh client per hop avoids sharing IP-keyed TLS pools across hosts.
        pinned = current.copy_with(host=ip)
        host = current.netloc.decode("ascii")
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=15,
        ) as client:
            async with client.stream(
                "GET", pinned,
                headers={"Host": host, "Accept": "text/html,application/pdf", "Accept-Encoding": "identity",
                         "User-Agent": "AidWorkResearch/1.0"},
                extensions={"sni_hostname": current.host},
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise _FetchError("invalid_redirect")
                    if redirect == MAX_REDIRECTS:
                        raise _FetchError("too_many_redirects")
                    current = _validate_url(str(current.join(location)))
                    continue
                if response.status_code != 200:
                    raise _FetchError(f"http_status_{response.status_code}")
                if response.headers.get("content-encoding", "identity").lower() not in ("identity", ""):
                    raise _FetchError("unsupported_content_encoding")
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media_type not in ("text/html", "application/xhtml+xml", "application/pdf"):
                    raise _FetchError("unsupported_content_type")
                download_limit = pdf_limit_bytes if media_type == "application/pdf" else MAX_DOWNLOAD_BYTES
                length = response.headers.get("content-length")
                if length and length.isdigit() and int(length) > download_limit:
                    raise _FetchError("download_too_large", limit_bytes=download_limit)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > download_limit:
                        raise _FetchError("download_too_large", limit_bytes=download_limit)
                    data.extend(chunk)
                final_url = str(current)
                encoding = response.charset_encoding
        title, content = await _parse_isolated(bytes(data), media_type, encoding, final_url, download_limit)
        kind = "direct_pdf" if media_type == "application/pdf" else "direct_html"
        return {"success": True, "results": [{
            "title": title, "url": final_url, "requested_url": url, "content": content,
            "content_kind": kind, "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "final_url": final_url, "final_url_verified": True,
        }], "count": 1}
    raise _FetchError("too_many_redirects")


def _network_failure(error):
    chain, current = [], error
    for _ in range(8):
        if current is None or any(current is item for item in chain):
            break
        chain.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(item, ssl.SSLError) for item in chain):
        code, stage = "fetch_tls_failed", "tls"
    elif isinstance(error, httpx.ConnectError):
        code, stage = "fetch_connect_failed", "connect"
    elif isinstance(error, (httpx.RemoteProtocolError, httpx.ReadError)):
        code, stage = "fetch_response_failed", "response"
    else:
        code, stage = "fetch_network_failed", "network"
    return {**_failure(code), "stage": stage}


async def _native_document(url, pdf_limit_bytes):
    try:
        return await _fetch(url, pdf_limit_bytes)
    except _FetchError as error:
        return {**_failure(error.code), **error.details}
    except httpx.TimeoutException:
        return _failure("fetch_timeout")
    except httpx.HTTPError as error:
        return _network_failure(error)


async def _remote_document(url, config):
    # DNS admission also applies to remote-only mode. No user headers are forwarded.
    await _resolve_public_ip(_validate_url(url))
    from src.config.settings import settings
    from .remote_fetch import fetch_deepseek_document, fetch_tavily_document
    if config.remote_provider == "tavily":
        return await fetch_tavily_document(url, api_key=settings.tools.search.tavily_api_key,
            timeout_seconds=config.total_timeout_seconds)
    return await fetch_deepseek_document(url, api_keys=settings.llm.deepseek.get_effective_keys(),
        timeout_seconds=config.total_timeout_seconds)


async def fetch_public_document(url: str) -> dict:
    """Read native documents or configured remote content without browser tools."""
    try:
        _validate_url(url)
    except _FetchError as error:
        return _failure(error.code)
    attempts = []
    try:
        config = _runtime_config()
        async with asyncio.timeout(config.total_timeout_seconds):
            if config.provider != "remote":
                attempt = {"method": "native", "success": False, "debug": "in_progress", "stage": "native"}
                attempts.append(attempt)
                result = await _native_document(url, config.pdf_max_download_mb * 1024 * 1024)
                attempt.update(success=result["success"], debug=result.get("debug"), stage=result.get("stage"))
                retryable = {"fetch_network_failed", "fetch_tls_failed", "fetch_connect_failed",
                    "fetch_response_failed", "fetch_timeout"}
                if result["success"] or config.provider == "native" or result.get("debug") not in retryable:
                    result["attempts"] = attempts
                    if not result["success"]:
                        result["requested_url"] = url
                    return result
            attempt = {"method": config.remote_provider, "success": False, "debug": "in_progress", "stage": "remote"}
            attempts.append(attempt)
            result = await _remote_document(url, config)
            attempt.update(success=result["success"], debug=result.get("debug"), stage="completed")
            result["attempts"] = attempts
            return result
    except _FetchError as error:
        result = {**_failure(error.code), **error.details}
    except (TimeoutError, httpx.TimeoutException):
        result = _failure("fetch_timeout")
    except httpx.HTTPError as error:
        result = _network_failure(error)
    except Exception:
        result = _failure("fetch_failed")
    result["requested_url"] = url
    if attempts and attempts[-1].get("debug") == "in_progress":
        attempts[-1].update(success=False, debug=result.get("debug"))
    result["attempts"] = attempts
    return result


def _parser_worker() -> None:
    """Private binary-stdin/JSON-stdout worker. No documents or parser errors on stderr."""
    global MAX_TEXT_CHARS, MAX_PDF_PAGES
    # Suppress both Python prints and native-library stdout, keeping one private
    # descriptor for the bounded JSON result. stderr was discarded by the parent.
    output_stream = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    with open(os.devnull, "wb") as null:
        os.dup2(null.fileno(), sys.stdout.fileno())
    try:
        header = sys.stdin.buffer.readline(65537)
        if len(header) > 65536 or not header.endswith(b"\n"):
            raise _FetchError("document_parse_failed")
        metadata = json.loads(header)
        hard_limit = MAX_PDF_DOWNLOAD_BYTES if metadata["media_type"] == "application/pdf" else MAX_DOWNLOAD_BYTES
        download_limit = min(hard_limit, int(metadata["max_download_bytes"]))
        if download_limit <= 0:
            raise _FetchError("document_parse_failed")
        data = sys.stdin.buffer.read(download_limit + 1)
        if len(data) > download_limit:
            raise _FetchError("document_parse_failed")
        MAX_TEXT_CHARS = min(MAX_TEXT_CHARS, int(metadata["max_text_chars"]))
        MAX_PDF_PAGES = min(MAX_PDF_PAGES, int(metadata["max_pdf_pages"]))
        if metadata["media_type"] == "application/pdf":
            title, content = _parse_pdf(data, metadata["url"])
        else:
            title, content = _parse_html(data, metadata.get("encoding"), metadata["url"])
        payload = {"title": title, "content": content}
    except _FetchError as error:
        payload = {"debug": error.code}
    except Exception:
        payload = {"debug": "document_parse_failed"}
    output = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(output) > _PARSER_OUTPUT_LIMIT:
        output = b'{"debug":"document_parse_failed"}'
    with output_stream:
        output_stream.write(output)


if __name__ == "__main__":
    _parser_worker()
