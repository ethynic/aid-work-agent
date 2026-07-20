"""浏览器执行器严格 DTO。

DTO 只传递完成命令所需数据。错误仅使用白名单 code/type，不携带异常正文、
URL query、DOM、cookie、header 或表单值。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BrowserRunSpec(StrictDTO):
    run_id: str = Field(pattern=r"^br_[0-9a-f]{32}$")
    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=256)
    execution_target: Literal["server", "client"] = "server"
    headless: bool = True
    viewport_width: int = Field(default=1920, ge=320, le=4096)
    viewport_height: int = Field(default=1080, ge=240, le=4096)


class BrowserCommand(StrictDTO):
    run_id: str = Field(pattern=r"^br_[0-9a-f]{32}$")
    seq: int = Field(ge=1)
    command_id: str = Field(min_length=8, max_length=128)
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_at 必须包含时区")
        return value.astimezone(timezone.utc)


class StartCommand(BrowserCommand):
    type: Literal["start"] = "start"
    run: BrowserRunSpec

    @field_validator("run")
    @classmethod
    def same_run(cls, value: BrowserRunSpec, info):
        if info.data.get("run_id") != value.run_id:
            raise ValueError("start 的 run_id 不一致")
        return value


class NavigateCommand(BrowserCommand):
    type: Literal["navigate"] = "navigate"
    url: str = Field(min_length=1, max_length=8192, repr=False)
    wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "load"


class SnapshotCommand(BrowserCommand):
    type: Literal["snapshot"] = "snapshot"
    mode: Literal["interactive", "all"] = "interactive"


class ClickCommand(BrowserCommand):
    type: Literal["click"] = "click"
    ref: str = Field(pattern=r"^e[0-9]+(?:-[0-9]+)?$")


class FillCommand(BrowserCommand):
    type: Literal["fill"] = "fill"
    ref: str = Field(pattern=r"^e[0-9]+(?:-[0-9]+)?$")
    value: str = Field(max_length=100_000, repr=False)


class SelectCommand(BrowserCommand):
    type: Literal["select"] = "select"
    ref: str = Field(pattern=r"^e[0-9]+(?:-[0-9]+)?$")
    option: str = Field(max_length=10_000, repr=False)


class KeyboardCommand(BrowserCommand):
    type: Literal["keyboard"] = "keyboard"
    key: str = Field(min_length=1, max_length=128, repr=False)


class PointerCommand(BrowserCommand):
    type: Literal["pointer"] = "pointer"
    action: Literal["click", "move", "down", "up", "wheel"]
    x: float = 0
    y: float = 0
    delta_x: float = 0
    delta_y: float = 0


class ContentCommand(BrowserCommand):
    type: Literal["content"] = "content"
    format: Literal["text", "html", "markdown"] = "markdown"
    selector: Optional[str] = Field(default=None, max_length=2048, repr=False)


class ScreenshotCommand(BrowserCommand):
    type: Literal["screenshot"] = "screenshot"


class CloseCommand(BrowserCommand):
    type: Literal["close", "cancel"] = "close"
    reason: str = Field(default="completed", pattern=r"^[a-z0-9_]{1,64}$")


Command = (
    StartCommand | NavigateCommand | SnapshotCommand | ClickCommand | FillCommand
    | SelectCommand | KeyboardCommand | PointerCommand | ContentCommand | CloseCommand
    | ScreenshotCommand
)


class ResultStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    DUPLICATE = "duplicate"


class BaseResult(StrictDTO):
    run_id: str
    command_id: str
    seq: int
    status: ResultStatus
    error_code: Optional[str] = Field(default=None, pattern=r"^[A-Z0-9_]{1,64}$")


class StartResult(BaseResult):
    worker_pid: Optional[int] = Field(default=None, exclude=True)


class CommandResult(BaseResult):
    current_origin_path: Optional[str] = Field(default=None, repr=False)
    title: Optional[str] = Field(default=None, max_length=512, repr=False)


class SnapshotElement(StrictDTO):
    ref: str
    role: str = ""
    label: str = Field(default="", max_length=500, repr=False)
    element_type: str = ""
    disabled: bool = False


class SnapshotResult(BaseResult):
    current_origin_path: Optional[str] = Field(default=None, repr=False)
    title: Optional[str] = Field(default=None, max_length=512, repr=False)
    interactive_elements: tuple[SnapshotElement, ...] = Field(default=(), repr=False)
    page_text: str = Field(default="", max_length=3000, repr=False)


class ContentResult(BaseResult):
    current_origin_path: Optional[str] = Field(default=None, repr=False)
    title: Optional[str] = Field(default=None, max_length=512, repr=False)
    format: Literal["text", "html", "markdown"] = "markdown"
    content: str = Field(default="", max_length=50_000, repr=False)
    content_length: int = Field(default=0, ge=0)
    truncated: bool = False


class ScreenshotResult(BaseResult):
    jpeg_base64: str = Field(default="", repr=False)
    width: int = Field(default=1280, ge=1, le=1280)
    height: int = Field(default=720, ge=1, le=720)


class CloseResult(BaseResult):
    closed: bool = False
    forced: bool = False


Result = StartResult | CommandResult | SnapshotResult | ContentResult | ScreenshotResult | CloseResult


def error_result(command: BrowserCommand, code: str) -> CommandResult:
    """构造不携带异常正文的标准错误。"""
    return CommandResult(
        run_id=command.run_id, command_id=command.command_id, seq=command.seq,
        status=ResultStatus.ERROR, error_code=code,
    )
