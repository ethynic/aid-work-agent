"""每个 browser run 独占的 Playwright worker。"""

from __future__ import annotations

import asyncio
import base64
import re
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from pydantic import TypeAdapter, ValidationError

from src.tools.browser.executor.models import (
    ClickCommand, CloseCommand, Command, ContentCommand, FillCommand,
    KeyboardCommand, NavigateCommand, PointerCommand, ResultStatus,
    SelectCommand, SnapshotCommand, StartCommand, ScreenshotCommand,
)
from src.tools.browser.worker_protocol import ProtocolError, read_frame_sync, write_frame_sync

_COMMAND_ADAPTER = TypeAdapter(Command)
_CHALLENGE_IFRAME_MARKERS = (
    "captcha", "challenge", "recaptcha", "hcaptcha", "turnstile",
    "verification", "验证码", "人机验证", "安全验证",
)


def _origin_path(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            return f"{parsed.scheme}:" if parsed.scheme else ""
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = f":{parsed.port}" if parsed.port is not None else ""
        except ValueError:
            return ""
        return f"{parsed.scheme}://{host}{port}{parsed.path}"[:4096]
    except (TypeError, ValueError):
        return ""


def _contains_challenge_iframe(items: list[dict[str, Any]]) -> bool:
    """仅基于 iframe 脱敏元数据识别仍可见的页面挑战。"""
    for item in items:
        signature = " ".join(
            str(item.get(field, ""))
            for field in ("src", "name", "title", "ref")
        ).lower()
        if any(marker in signature for marker in _CHALLENGE_IFRAME_MARKERS):
            return True
        if _contains_challenge_iframe(item.get("nested_iframes", [])):
            return True
    return False


class BrowserWorker:
    def __init__(self) -> None:
        self.run = None
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.ref_mapper = None
        self.last_seq = 0
        self.results: dict[str, dict[str, Any]] = {}
        self.closed = False

    def _base(self, command, status: str = "ok", error_code: str | None = None):
        return {
            "run_id": command.run_id,
            "command_id": command.command_id,
            "seq": command.seq,
            "status": status,
            "error_code": error_code,
        }

    async def handle(self, command) -> dict[str, Any]:
        # 画面采样是只读旁路，不参与业务命令 seq/幂等窗口。
        if isinstance(command, ScreenshotCommand):
            if self.run is None or command.run_id != self.run.run_id:
                return self._base(command, "error", "RUN_NOT_STARTED")
            try:
                return await self._dispatch(command)
            except Exception:
                return self._base(command, "error", "SCREENCAST_FAILED")
        duplicate = self.results.get(command.command_id)
        if duplicate is not None:
            return duplicate
        if command.seq != self.last_seq + 1:
            return self._base(command, "error", "SEQ_REJECTED")
        if self.run is not None and command.run_id != self.run.run_id:
            return self._base(command, "error", "RUN_MISMATCH")
        if command.deadline_at <= datetime.now(timezone.utc):
            result = self._base(command, "error", "COMMAND_TIMEOUT")
            self.last_seq = command.seq
            self.results[command.command_id] = result
            return result

        try:
            result = await self._dispatch(command)
        except asyncio.CancelledError:
            raise
        except Exception:
            result = self._base(command, "error", "WORKER_COMMAND_FAILED")
        self.last_seq = command.seq
        self.results[command.command_id] = result
        return result

    async def _dispatch(self, command) -> dict[str, Any]:
        if isinstance(command, StartCommand):
            return await self._start(command)
        if self.page is None and not isinstance(command, CloseCommand):
            return self._base(command, "error", "RUN_NOT_STARTED")
        if isinstance(command, NavigateCommand):
            await self.page.goto(
                command.url, wait_until=command.wait_until,
                timeout=max(1, int((command.deadline_at.timestamp() - __import__("time").time()) * 1000)),
            )
            return {**self._base(command), "current_origin_path": _origin_path(self.page.url), "title": (await self.page.title())[:512]}
        if isinstance(command, SnapshotCommand):
            return await self._snapshot(command)
        if isinstance(command, ClickCommand):
            element = await self._element(command.ref)
            if element is None:
                return self._base(command, "error", "REF_NOT_FOUND")
            await element.click(timeout=10_000, force=True)
            return await self._command_result(command)
        if isinstance(command, FillCommand):
            element = await self._element(command.ref)
            if element is None:
                return self._base(command, "error", "REF_NOT_FOUND")
            await element.fill(command.value, timeout=10_000, force=True)
            return await self._command_result(command)
        if isinstance(command, SelectCommand):
            element = await self._element(command.ref)
            if element is None:
                return self._base(command, "error", "REF_NOT_FOUND")
            await element.select_option(label=command.option, timeout=10_000)
            return await self._command_result(command)
        if isinstance(command, KeyboardCommand):
            await self.page.keyboard.press(command.key)
            return await self._command_result(command)
        if isinstance(command, PointerCommand):
            if command.action == "click":
                await self.page.mouse.click(command.x, command.y)
            elif command.action == "move":
                await self.page.mouse.move(command.x, command.y)
            elif command.action == "down":
                await self.page.mouse.down()
            elif command.action == "up":
                await self.page.mouse.up()
            else:
                await self.page.mouse.wheel(command.delta_x, command.delta_y)
            return await self._command_result(command)
        if isinstance(command, ContentCommand):
            return await self._content(command)
        if isinstance(command, ScreenshotCommand):
            data = await self.page.screenshot(type="jpeg", quality=60, full_page=False)
            return {
                **self._base(command), "jpeg_base64": base64.b64encode(data).decode("ascii"),
                "width": min(command and self.run.viewport_width, 1280),
                "height": min(command and self.run.viewport_height, 720),
            }
        if isinstance(command, CloseCommand):
            await self.close()
            return {**self._base(command), "closed": True, "forced": False}
        return self._base(command, "error", "UNKNOWN_MESSAGE")

    async def _start(self, command: StartCommand) -> dict[str, Any]:
        if self.run is not None:
            return self._base(command, "error", "ALREADY_STARTED")
        self.run = command.run
        from playwright.async_api import async_playwright

        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.launch(
                headless=command.run.headless,
                args=["--disable-dev-shm-usage"],
            )
            self.context = await self.browser.new_context(
                viewport={"width": command.run.viewport_width, "height": command.run.viewport_height}
            )
            self.page = await self.context.new_page()
        except BaseException:
            await self.close()
            raise
        return {**self._base(command), "worker_pid": None}

    async def _snapshot(self, command: SnapshotCommand) -> dict[str, Any]:
        from src.tools.browser.semantic import SemanticSnapshotGenerator

        generator = SemanticSnapshotGenerator(session_id=command.run_id)
        snapshot = await generator.generate(page=self.page, mode=command.mode, use_cache=False)
        if not snapshot.success:
            return self._base(command, "error", "SNAPSHOT_FAILED")
        self.ref_mapper = generator.get_ref_mapper()
        elements = []
        for item in snapshot.interactive_elements[:200]:
            elements.append({
                "ref": str(item.get("ref", ""))[:64],
                "role": str(item.get("role", ""))[:64],
                "label": str(item.get("label", ""))[:500],
                "element_type": str(item.get("element_type") or item.get("type") or item.get("tag") or "")[:64],
                "disabled": bool(item.get("disabled", False)),
            })
        page_text = await self.page.locator("body").inner_text(timeout=3000)
        return {
            **self._base(command),
            "current_origin_path": _origin_path(snapshot.url),
            "title": snapshot.title[:512],
            "interactive_elements": elements,
            "challenge_iframe_present": _contains_challenge_iframe(
                snapshot.iframe_snapshots
            ),
            "page_text": re.sub(r"\s+", " ", page_text)[:3000],
        }

    async def _element(self, ref: str):
        if self.ref_mapper is not None:
            return await self.ref_mapper.get_handle(ref)
        return None

    async def _command_result(self, command) -> dict[str, Any]:
        return {
            **self._base(command),
            "current_origin_path": _origin_path(self.page.url),
            "title": (await self.page.title())[:512],
        }

    async def _content(self, command: ContentCommand) -> dict[str, Any]:
        locator = self.page.locator(command.selector or "body")
        if command.format == "text":
            content = await locator.inner_text()
        elif command.format == "html":
            content = await locator.inner_html()
        else:
            html = await locator.inner_html()
            try:
                from markdownify import markdownify
                content = markdownify(html, strip=["script", "style", "nav", "footer", "aside"])
            except Exception:
                content = re.sub(r"<[^>]+>", " ", html)
        length = len(content)
        content = content[:50_000]
        return {
            **self._base(command), "current_origin_path": _origin_path(self.page.url),
            "title": (await self.page.title())[:512], "format": command.format,
            "content": content, "content_length": length,
            "truncated": length > len(content),
        }

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for attr, method in (("page", "close"), ("context", "close"), ("browser", "close"), ("playwright", "stop")):
            resource = getattr(self, attr)
            setattr(self, attr, None)
            if resource is not None:
                try:
                    await getattr(resource, method)()
                except BaseException:
                    pass


async def _run() -> int:
    worker = BrowserWorker()
    try:
        while True:
            try:
                message = await asyncio.to_thread(read_frame_sync, sys.stdin.buffer)
            except ProtocolError as exc:
                print(f"browser_worker_protocol_error:{type(exc).__name__}", file=sys.stderr, flush=True)
                return 2
            if message is None:
                return 0
            try:
                command = _COMMAND_ADAPTER.validate_python(message)
            except ValidationError:
                safe = {
                    "run_id": str(message.get("run_id", ""))[:64],
                    "command_id": str(message.get("command_id", ""))[:128],
                    "seq": int(message.get("seq", 0)) if str(message.get("seq", "0")).isdigit() else 0,
                    "status": ResultStatus.ERROR.value,
                    "error_code": "UNKNOWN_MESSAGE",
                }
                await asyncio.to_thread(write_frame_sync, sys.stdout.buffer, safe)
                continue
            result = await worker.handle(command)
            await asyncio.to_thread(write_frame_sync, sys.stdout.buffer, result)
            if isinstance(command, CloseCommand):
                return 0
    finally:
        await worker.close()


def main() -> None:
    # worker stderr 只保留级别，不输出异常正文、URL 或 DOM；stdout 专用于协议帧。
    try:
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, level="ERROR", format="browser_worker_error:{level}")
    except Exception:
        pass
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
