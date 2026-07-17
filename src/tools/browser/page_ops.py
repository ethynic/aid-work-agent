"""父进程页面操作封装，仅构造 DTO 并调用 BrowserExecutor。"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any, Dict, Optional

from loguru import logger

from src.config.settings import settings
from src.tools.browser.executor.base import BrowserExecutor
from src.tools.browser.executor.models import (
    ClickCommand, ContentCommand, FillCommand, KeyboardCommand, NavigateCommand,
    PointerCommand, ResultStatus, SelectCommand, SnapshotCommand, SnapshotElement,
)


class PageOps:
    """不持有任何浏览器对象，只持有执行器与结构化快照。"""

    def __init__(self, executor: BrowserExecutor, run_id: str, initial_seq: int = 1):
        self.executor = executor
        self.run_id = run_id
        self._seq = initial_seq
        self._elements: dict[str, SnapshotElement] = {}

    def _fields(self) -> dict[str, Any]:
        self._seq += 1
        timeout = float(getattr(settings.tools.browser, "command_timeout", 30.0))
        return {
            "run_id": self.run_id,
            "seq": self._seq,
            "command_id": f"bc_{uuid.uuid4().hex}",
            "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=timeout),
        }

    @staticmethod
    def _ok(result) -> bool:
        return result.status in {ResultStatus.OK, ResultStatus.DUPLICATE}

    async def navigate(self, url: str, wait_for: str = "load") -> Dict[str, Any]:
        command = NavigateCommand(**self._fields(), url=url, wait_until=wait_for)
        result = await self.executor.navigate(command)
        if not self._ok(result):
            return {"success": False, "error": result.error_code or "NAVIGATE_FAILED"}
        logger.info("[PageOps] 导航成功")
        return {
            "success": True, "message": "页面已打开",
            "url": result.current_origin_path or "", "title": result.title or "",
        }

    async def take_snapshot(self, mode: str = "interactive") -> Dict[str, Any]:
        result = await self.executor.snapshot(SnapshotCommand(**self._fields(), mode=mode))
        if not self._ok(result):
            return {"success": False, "error": result.error_code or "SNAPSHOT_FAILED"}
        self._elements = {item.ref: item for item in result.interactive_elements if item.ref}
        elements = [item.model_dump() for item in result.interactive_elements]
        logger.info("[PageOps] 快照生成: 元素数={}", len(elements))
        return {
            "success": True, "url": result.current_origin_path or "",
            "title": result.title or "", "interactive_elements": elements,
            "page_text": result.page_text,
        }

    def _resolve_ref(self, description: str, ref: Optional[str], action: str) -> tuple[str | None, str]:
        del action
        if ref:
            item = self._elements.get(ref)
            return (ref, item.label) if item else (None, description)
        normalized = re.sub(r"\s+", "", description).lower()
        candidates = []
        for item in self._elements.values():
            label = re.sub(r"\s+", "", item.label).lower()
            if normalized and (normalized in label or label in normalized):
                candidates.append(item)
        if not candidates:
            return None, description
        candidates.sort(key=lambda item: (abs(len(item.label) - len(description)), item.ref))
        return candidates[0].ref, candidates[0].label

    async def click(self, description: str, ref: Optional[str] = None) -> Dict[str, Any]:
        target_ref, label = self._resolve_ref(description, ref, "click")
        if not target_ref:
            return {"success": False, "error": "无法从当前快照定位点击目标"}
        result = await self.executor.click(ClickCommand(**self._fields(), ref=target_ref))
        if not self._ok(result):
            return {"success": False, "error": result.error_code or "CLICK_FAILED"}
        return {
            "success": True, "message": f"已点击: {label}", "ref": target_ref,
            "label": label, "current_url": result.current_origin_path or "",
            "page_title": result.title or "",
        }

    async def fill(self, field: str, value: str, ref: Optional[str] = None) -> Dict[str, Any]:
        target_ref, label = self._resolve_ref(field, ref, "fill")
        if not target_ref:
            return {"success": False, "error": "无法从当前快照定位填写目标"}
        result = await self.executor.fill(FillCommand(**self._fields(), ref=target_ref, value=value))
        if not self._ok(result):
            return {"success": False, "error": result.error_code or "FILL_FAILED"}
        return {"success": True, "message": f"已填写: {label}", "ref": target_ref, "label": label}

    async def select(self, field: str, option: str, ref: Optional[str] = None) -> Dict[str, Any]:
        target_ref, label = self._resolve_ref(field, ref, "select")
        if not target_ref:
            return {"success": False, "error": "无法从当前快照定位下拉目标"}
        result = await self.executor.select(SelectCommand(**self._fields(), ref=target_ref, option=option))
        if not self._ok(result):
            return {"success": False, "error": result.error_code or "SELECT_FAILED"}
        return {"success": True, "message": f"已选择: {option}", "ref": target_ref, "field": label, "option": option}

    async def keyboard(self, key: str) -> Dict[str, Any]:
        result = await self.executor.keyboard(KeyboardCommand(**self._fields(), key=key))
        return {"success": self._ok(result), "error": result.error_code if not self._ok(result) else None}

    async def scroll(self, direction: str) -> Dict[str, Any]:
        delta = -800 if direction == "up" else 800
        result = await self.executor.pointer(
            PointerCommand(**self._fields(), action="wheel", delta_y=delta)
        )
        if not self._ok(result):
            return {"success": False, "error": result.error_code or "SCROLL_FAILED"}
        return {"success": True, "message": f"已向{direction}滚动一屏"}

    async def get_content(self, format: str = "markdown", selector: Optional[str] = None) -> Dict[str, Any]:
        result = await self.executor.content(
            ContentCommand(**self._fields(), format=format, selector=selector)
        )
        if not self._ok(result):
            return {"success": False, "error": result.error_code or "CONTENT_FAILED"}
        return {
            "success": True, "message": "获取页面内容成功",
            "url": result.current_origin_path or "", "title": result.title or "",
            "format": result.format,
            "content": result.content, "markdown": result.content if format == "markdown" else None,
            "content_length": result.content_length, "truncated": result.truncated,
        }

    async def take_screenshot(self, path: str = "", full_page: bool = False) -> Dict[str, Any]:
        del path, full_page
        return {"success": False, "error": "SCREENSHOT_NOT_AVAILABLE_IN_PHASE2"}

    async def close_popup(self) -> Dict[str, Any]:
        result = await self.keyboard("Escape")
        if result.get("success"):
            await asyncio.sleep(0.2)
            return {"success": True, "message": "已尝试关闭弹窗"}
        return result

    @staticmethod
    def _html_to_markdown(html: str) -> str:
        return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))).strip()
