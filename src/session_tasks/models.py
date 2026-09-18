"""端侧会话任务发布契约模型（C1，设计 §5/§13.2）。

completion_rule 三个互斥 schema 在此冻结；发布校验（rounds_target+开场白≤
max_replies 等）在 models 层完成，service 只调用 validate_task_spec。
自然语言字段（goal/policy/criteria/question/opening）由 service 层加密入库。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class RoundsRule(BaseModel):
    """轮数完成模式：rounds_target 正整数；发布校验 target+可选开场白 ≤ max_replies。"""

    model_config = {"extra": "forbid"}

    mode: Literal["rounds"]
    rounds_target: int = Field(gt=0, le=1000)


class PeerConfirmedField(BaseModel):
    """peer_confirmed 的单字段约束：枚举提取 + 接受值子集。"""

    model_config = {"extra": "forbid"}

    key: str
    question: str = Field(min_length=1, max_length=500)
    allowed_values: List[str] = Field(min_length=1, max_length=20)
    accepted_values: List[str] = Field(min_length=1, max_length=20)

    @field_validator("key")
    @classmethod
    def _key_format(cls, v: str) -> str:
        if not _KEY_RE.match(v):
            raise ValueError("key 必须匹配 ^[a-z][a-z0-9_]{0,63}$")
        return v

    @field_validator("allowed_values")
    @classmethod
    def _allowed_values(cls, v: List[str]) -> List[str]:
        if len(set(v)) != len(v):
            raise ValueError("allowed_values 不得重复")
        for item in v:
            if not item or len(item) > 100:
                raise ValueError("allowed_values 每项须为 1–100 字符")
        return v

    @model_validator(mode="after")
    def _accepted_subset(self) -> "PeerConfirmedField":
        if not set(self.accepted_values) <= set(self.allowed_values):
            raise ValueError("accepted_values 必须是 allowed_values 的子集")
        return self


class PeerConfirmedRule(BaseModel):
    """对方确认完成模式：字段/枚举提取；require_all V1 只能为 true。"""

    model_config = {"extra": "forbid"}

    mode: Literal["peer_confirmed"]
    fields: List[PeerConfirmedField] = Field(min_length=1, max_length=10)
    require_all: Literal[True] = True

    @field_validator("fields")
    @classmethod
    def _unique_keys(cls, v: List[PeerConfirmedField]) -> List[PeerConfirmedField]:
        keys = [f.key for f in v]
        if len(set(keys)) != len(keys):
            raise ValueError("fields 的 key 不得重复")
        return v


class JudgedRule(BaseModel):
    """模型判断完成模式：criteria 1–10 条不重复。"""

    model_config = {"extra": "forbid"}

    mode: Literal["judged"]
    criteria: List[str] = Field(min_length=1, max_length=10)

    @field_validator("criteria")
    @classmethod
    def _criteria_check(cls, v: List[str]) -> List[str]:
        seen = set()
        for item in v:
            if not item.strip() or len(item) > 500:
                raise ValueError("criteria 每条须为 1–500 字符的非空字符串")
            if item in seen:
                raise ValueError("criteria 不得重复")
            seen.add(item)
        return v


CompletionRule = Union[RoundsRule, PeerConfirmedRule, JudgedRule]


class ReplyPolicy(BaseModel):
    """回复策略（设计 §5）：style/allowed_facts/forbidden_commitments。"""

    model_config = {"extra": "forbid"}

    style: str = Field(min_length=1, max_length=200)
    allowed_facts: List[str] = Field(default_factory=list, max_length=50)
    forbidden_commitments: List[str] = Field(default_factory=list, max_length=50)


class TaskLimits(BaseModel):
    """硬上限：全部必填且有限（不可默认无限费用）。"""

    model_config = {"extra": "forbid"}

    max_replies: int = Field(gt=0, le=1000)
    max_decisions: int = Field(gt=0, le=1000)
    max_cost_units: float = Field(gt=0, le=10_000_000)
    expires_at: datetime
    peer_wait_timeout_seconds: int = Field(default=86400, gt=0, le=30 * 86400)

    @field_validator("expires_at")
    @classmethod
    def _future(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("expires_at 必须带时区")
        if v <= datetime.now(timezone.utc):
            raise ValueError("expires_at 必须晚于当前时间")
        return v


class WorkWindow(BaseModel):
    """工作时段（可选）：UTC 时刻；窗口外只排队不决策/发送（设计 §5/§6）。"""

    model_config = {"extra": "forbid"}

    start_hour_utc: int = Field(ge=0, le=23)
    end_hour_utc: int = Field(ge=0, le=23)
    weekdays_utc: List[int] = Field(default_factory=lambda: list(range(7)), max_length=7)


class TaskSpecPayload(BaseModel):
    """任务发布单（draft 创建/PATCH 共用结构；发布时冻结为不可变 spec）。"""

    model_config = {"extra": "forbid"}

    goal: str = Field(min_length=1, max_length=2000)
    completion_rule: CompletionRule
    reply_policy: ReplyPolicy
    limits: TaskLimits
    opening_text: Optional[str] = Field(default=None, max_length=500)
    work_window: Optional[WorkWindow] = None

    @model_validator(mode="after")
    def _rounds_budget(self) -> "TaskSpecPayload":
        if isinstance(self.completion_rule, RoundsRule):
            opening_count = 1 if self.opening_text else 0
            if self.completion_rule.rounds_target + opening_count > self.limits.max_replies:
                raise ValueError(
                    f"rounds_target({self.completion_rule.rounds_target}) + 开场白({opening_count}) "
                    f"不得超过 max_replies({self.limits.max_replies})"
                )
        return self


class TaskDraftCreatePayload(BaseModel):
    """POST /api/session-tasks 请求体：spec + 归属绑定（设计 §9）。

    B1.2 envelope（设计 §4.3）：spec 类型为 dict（envelope 只校验"是对象"），
    内容校验按请求 scenario_key 分派到描述器 spec_validator（service.create_draft）；
    缺省场景 = weixin.conversation.v1（现状锁定）。
    """

    model_config = {"extra": "forbid"}

    scenario_key: str = Field(default="weixin.conversation.v1", pattern=r"^[a-z0-9_.-]{4,64}$")
    device_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    account_binding_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    conversation_binding_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    resolution_invocation_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    spec: Dict[str, Any]

    @model_validator(mode="after")
    def _target_source(self):
        if self.resolution_invocation_id:
            if self.account_binding_id or self.conversation_binding_id or self.scenario_key != "weixin.conversation.v1":
                raise ValueError("名称定位结果不能与既有绑定同时提交")
        elif not self.account_binding_id or not self.conversation_binding_id:
            raise ValueError("需要名称定位结果或完整的既有绑定")
        return self


def validate_task_spec(payload: dict) -> TaskSpecPayload:
    """发布/草稿保存的 spec 校验入口；非法抛 pydantic ValidationError（API 层转 field_errors）。"""
    return TaskSpecPayload.model_validate(payload)


class SpecValidationError(ValueError):
    """envelope spec 校验失败（B1.2 §4.3）。

    保留 pydantic errors 面（API 层 _validation_error 以 .errors() 转 field_errors，
    loc 统一加 "spec" 前缀——微信 POST 错误路径与 B1.1 前逐字段保真，B1.0 特征
    测试第 8 项快照）；同时保持 ValueError 基类——直连服务层的调用方/测试对
    非法 spec 的 ValueError 契约与 B1.1 前（pydantic ValidationError 即
    ValueError）一致。
    """

    def __init__(self, validation_error: Exception):
        self.validation_error = validation_error
        details = getattr(validation_error, "errors", lambda: [])() or []
        self._errors = [
            {
                "loc": ("spec",) + tuple(d.get("loc", [])),
                "msg": d.get("msg", str(validation_error)),
                "type": d.get("type", ""),
            }
            for d in details
        ]
        super().__init__(str(validation_error))

    def errors(self):  # noqa: ANN202
        return self._errors


def validate_spec_for_scenario(scenario_key: str, spec: Dict[str, Any]) -> Any:
    """按场景分派的 spec 校验（B1.2 §4.3）：描述器缺失/非法 → SessionTaskError 400。

    微信场景 spec 非法仍由原 TaskSpecPayload 抛 pydantic ValidationError；
    本函数不吞不改（调用方决定是否以 SpecValidationError 加前缀包装）。
    """
    from .constants import ERR_VALIDATION_FAILED, SessionTaskError
    from .scenario_descriptor import ScenarioDescriptorError, require_descriptor

    if not isinstance(spec, dict):
        raise SessionTaskError("spec 必须是对象", ERR_VALIDATION_FAILED, 400)
    try:
        descriptor = require_descriptor(scenario_key)
    except ScenarioDescriptorError as exc:
        raise SessionTaskError(f"场景未注册: {scenario_key}", ERR_VALIDATION_FAILED, 400) from exc
    return descriptor.spec_validator(dict(spec))
