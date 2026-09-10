"""微信营销自动化 Pydantic 模型（trigger 判别联合 / content block 判别联合）

后端 schema 是权威（微信计划 §8）；datetime 一律 tz-aware（naive 按 UTC 解释并规整）；
引用 ID（group_binding_id/asset_id）为 UUID 字符串形态校验（非法 422）。
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.weixin_marketing.constants import (
    BLOCK_KIND_IMAGE,
    BLOCK_KIND_LINK,
    BLOCK_KIND_TEXT,
    BLOCK_TEXT_FORBIDDEN_CHARS,
    BLOCK_TEXT_MAX_LENGTH,
)


def _ensure_utc(v: datetime) -> datetime:
    if v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc)


def _validate_uuid_str(v: str) -> str:
    """UUID 字符串形态校验（P2-7）：非法即 422"""
    try:
        uuid.UUID(str(v))
    except (ValueError, TypeError) as e:
        raise ValueError(f"非法 UUID: {v!r}") from e
    return str(v)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ==================== 触发配置（TriggerConfig 判别联合）====================


class OnceTriggerConfig(_StrictModel):
    """一次性触发：run_at 即唯一槽（one_shot；宽限内接纳、超宽限记 missed）"""

    type: Literal["once"]
    run_at: datetime
    timezone: str = "Asia/Shanghai"
    grace_seconds: int = Field(default=300, ge=0)  # P2-10：可选宽限（默认 300）

    @field_validator("run_at")
    @classmethod
    def _tz(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


class IntervalTriggerConfig(_StrictModel):
    """间隔触发：第 n 次 = start_at + n×interval_seconds（重启不积压，底座 §3.1）"""

    type: Literal["interval"]
    start_at: datetime
    interval_seconds: int = Field(gt=0)
    timezone: str = "Asia/Shanghai"
    grace_seconds: int = Field(default=300, ge=0)
    miss_policy: Literal["skip_overlap", "catch_up_latest"] = "skip_overlap"
    ends_at: Optional[datetime] = None
    max_count: Optional[int] = Field(default=None, ge=1)

    @field_validator("start_at", "ends_at")
    @classmethod
    def _tz(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _ensure_utc(v) if v is not None else v


class CalendarTriggerConfig(_StrictModel):
    """日历触发：cron_expr 五段或字段式（day_of_week 用 mon..sun），APScheduler 计算"""

    type: Literal["calendar"]
    timezone: str = "Asia/Shanghai"
    cron_expr: Optional[str] = None
    year: Optional[str] = None
    month: Optional[str] = None
    day: Optional[str] = None
    week: Optional[str] = None
    day_of_week: Optional[str] = None
    hour: Optional[str] = None
    minute: Optional[str] = None
    second: Optional[str] = None
    grace_seconds: int = Field(default=300, ge=0)
    miss_policy: Literal["skip_overlap", "catch_up_latest"] = "skip_overlap"
    ends_at: Optional[datetime] = None
    max_count: Optional[int] = Field(default=None, ge=1)

    @field_validator("ends_at")
    @classmethod
    def _tz(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _ensure_utc(v) if v is not None else v


class EventTriggerCondition(_StrictModel):
    """事件条件（白名单 DSL，简单判定）：field/op/value 列表 AND 组合。

    op ∈ eq|ne|exists|gt|lt（gt/lt 仅数值）；复杂 DSL（嵌套/or/正则）不在本期范围。
    """

    field: str = Field(min_length=1, max_length=64)
    op: Literal["eq", "ne", "exists", "gt", "lt"]
    value: Optional[Any] = None


class EventTriggerConfig(_StrictModel):
    """事件触发：source_ref/event_type 订阅（P4-B：worker 匹配接线）。

    condition 为发布时冻结的白名单判定（匹配 worker 按 payload 判定，不命中记
    skipped 不触发）；delay_seconds 冻结为 due_at = received_at + delay。
    """

    type: Literal["event"]
    source_ref: str = Field(min_length=1)
    event_type: str = "*"
    delay_seconds: int = Field(default=0, ge=0)
    condition: Optional[list[EventTriggerCondition]] = None
    timezone: str = "Asia/Shanghai"


TriggerConfig = Union[OnceTriggerConfig, IntervalTriggerConfig, CalendarTriggerConfig, EventTriggerConfig]


# ==================== 内容块（ContentBlock 判别联合）====================


class TextBlockSpec(_StrictModel):
    type: Literal[BLOCK_KIND_TEXT] = BLOCK_KIND_TEXT
    text_content: str

    @field_validator("text_content")
    @classmethod
    def _text_rules(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("正文不能为空")
        if len(v) > BLOCK_TEXT_MAX_LENGTH:
            raise ValueError(f"正文长度超过 {BLOCK_TEXT_MAX_LENGTH}")
        for ch in BLOCK_TEXT_FORBIDDEN_CHARS:
            if ch in v:
                raise ValueError("正文不允许换行/NUL 字符")
        return v


class LinkBlockSpec(_StrictModel):
    type: Literal[BLOCK_KIND_LINK] = BLOCK_KIND_LINK
    url: str

    @field_validator("url")
    @classmethod
    def _url_rules(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("网址不能为空")
        if len(v) > BLOCK_TEXT_MAX_LENGTH:
            raise ValueError(f"网址长度超过 {BLOCK_TEXT_MAX_LENGTH}")
        for ch in BLOCK_TEXT_FORBIDDEN_CHARS:
            if ch in v:
                raise ValueError("网址不允许换行/NUL 字符")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("网址必须以 http:// 或 https:// 开头")
        return v


class ImageBlockSpec(_StrictModel):
    type: Literal[BLOCK_KIND_IMAGE] = BLOCK_KIND_IMAGE
    asset_id: str

    @field_validator("asset_id")
    @classmethod
    def _asset_rules(cls, v: str) -> str:
        if not v:
            raise ValueError("asset_id 不能为空")
        return _validate_uuid_str(v)


ContentBlockSpec = Union[TextBlockSpec, LinkBlockSpec, ImageBlockSpec]


# ==================== 服务层输入 ====================


class AutomationCreateInput(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    trigger: TriggerConfig
    blocks: list[ContentBlockSpec] = Field(min_length=1)
    group_binding_id: str
    policy: dict = Field(default_factory=dict)

    @field_validator("group_binding_id")
    @classmethod
    def _binding_rules(cls, v: str) -> str:
        return _validate_uuid_str(v)


class DraftUpdateInput(_StrictModel):
    expected_version: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    trigger: Optional[TriggerConfig] = None
    blocks: Optional[list[ContentBlockSpec]] = None
    group_binding_id: Optional[str] = None
    policy: Optional[dict] = None

    @field_validator("group_binding_id")
    @classmethod
    def _binding_rules(cls, v: Optional[str]) -> Optional[str]:
        return _validate_uuid_str(v) if v is not None else v


class PublishInput(_StrictModel):
    expected_version: int = Field(ge=1)
    revision_id: Optional[str] = None
    authorization_source: str = "web"


class VersionedActionInput(_StrictModel):
    expected_version: int = Field(ge=1)
    reason: Optional[str] = None


class DeliveryResolveInput(_StrictModel):
    """人工结论（verdict=语义结论；decision=授权决定，R52）。

    decision='confirmed_not_sent'：人工确认该次未知效果实际未发送并授权重试——
    未知效果 delivery 的 retry 必须先有此决定（晚于末次 attempt）方可建新 attempt；
    提供 decision 时必须附带说明（note）。
    """

    verdict: Literal["delivered", "not_delivered", "uncertain"]
    decision: Optional[Literal["confirmed_not_sent"]] = None
    note: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _decision_requires_note(self) -> "DeliveryResolveInput":
        if self.decision is not None and not (self.note or "").strip():
            raise ValueError("decision=confirmed_not_sent 必须附带说明（note）")
        return self


class DeliveryRetryInput(_StrictModel):
    confirm: bool = False


# ==================== P3-A1 工作台输入（test-send / 搜索 / 绑定 / 预检）====================


class TestSendInput(_StrictModel):
    """试发（R54①）：显式 block position + group_binding_id，只试发该条。

    绑定必须是属主可见且 complete 的群绑定；块取自动化当前 active（已发布）revision。
    """

    group_binding_id: str
    block_position: int = Field(ge=0)

    @field_validator("group_binding_id")
    @classmethod
    def _binding_rules(cls, v: str) -> str:
        return _validate_uuid_str(v)


class GroupSearchCreateInput(_StrictModel):
    """群搜索任务（异步，202）：经 local_tool 队列向绑定设备下发 weixin_chat_search"""

    device_id: str
    keyword: str = Field(min_length=1, max_length=100)

    @field_validator("device_id")
    @classmethod
    def _device_rules(cls, v: str) -> str:
        return _validate_uuid_str(v)


class GroupBindingCreateInput(_StrictModel):
    """从搜索候选创建绑定（pending_verification）：候选 target_ref 必须来自指定搜索结果"""

    device_id: str
    label: str = Field(min_length=1, max_length=128)
    target_ref: str = Field(min_length=1, max_length=256)
    search_id: str
    account_binding_id: Optional[str] = None

    @field_validator("device_id", "search_id")
    @classmethod
    def _uuid_rules(cls, v: str) -> str:
        return _validate_uuid_str(v)

    @field_validator("account_binding_id")
    @classmethod
    def _account_rules(cls, v: Optional[str]) -> Optional[str]:
        return _validate_uuid_str(v) if v is not None else v



def parse_trigger(data: dict) -> TriggerConfig:
    """dict → TriggerConfig（判别字段 type；非法配置抛 ValidationError）"""
    from pydantic import TypeAdapter

    return TypeAdapter(TriggerConfig).validate_python(data)


def parse_blocks(items: list) -> list[ContentBlockSpec]:
    from pydantic import TypeAdapter

    return TypeAdapter(list[ContentBlockSpec]).validate_python(items)
