"""Immutable private control intents. They are neither events nor usage receipts."""

import hashlib
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .contracts import AttachmentRef, canonical_json


class AnswerItem(BaseModel):
    """One structured answer. Question/option range checks belong to the
    acceptance layer against the immutable form; the entry only checks shape."""
    question_id: str = Field(pattern='^[qQ][1-9]$')  # case-insensitive; lowercased at acceptance
    selected_keys: list[str]  # subset of that question's options, enforced at acceptance
    text: str | None

    @field_validator('question_id', mode='before')
    @classmethod
    def _strip_question_id(cls, value):
        # 无害首尾空白在格式校验前剥离（评审 P2-1）：" Q1 " 是合法题号；
        # 剥离后才进入 pattern 校验，未知题号仍被既有规则拒绝。
        return value.strip() if isinstance(value, str) else value

    class Config:
        extra = 'forbid'


class RunnerControl(BaseModel):
    client_request_id: str = Field(min_length=1, max_length=128)
    action: Literal['pause','resume','reply','browser_complete']
    target_execution_id: str | None = Field(default=None, max_length=256)
    wait_id: str | None = Field(default=None, max_length=256)
    answer: str = Field(default='', max_length=1_000_000)
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=100)
    completion_ref: str | None = Field(default=None, max_length=256)
    # Structured answers for an ask_user form. None means not supplied; an
    # explicit [] is a legitimate all-unanswered submission. skip is the
    # explicit whole-form skip, reply-only.
    answers: list[AnswerItem] | None = Field(default=None, max_length=5)
    skip: bool = False

    class Config:
        extra = 'forbid'

    @model_validator(mode='after')
    def consistent_action(self):
        if self.action in ('reply','browser_complete') and (not self.wait_id or not self.target_execution_id):
            raise ValueError('CONTROL_WAIT_REQUIRED')
        if self.action == 'reply' and not (self.answer.strip() or self.attachments
                or self.answers is not None or self.skip):
            raise ValueError('CONTROL_REPLY_REQUIRED')
        if self.action not in ('reply','resume') and (self.answer or self.attachments):
            raise ValueError('CONTROL_REPLY_UNEXPECTED')
        if self.action != 'reply' and (self.answers is not None or self.skip):
            raise ValueError('CONTROL_STRUCTURED_UNEXPECTED')
        if self.answers is not None and self.answer.strip():
            raise ValueError('CONTROL_ANSWER_CONFLICT')
        if self.skip and self.answers is not None:
            raise ValueError('CONTROL_SKIP_CONFLICT')
        if self.action == 'browser_complete' and not self.completion_ref:
            raise ValueError('CONTROL_COMPLETION_REQUIRED')
        if self.action != 'browser_complete' and self.completion_ref:
            raise ValueError('CONTROL_COMPLETION_UNEXPECTED')
        return self

    def intent(self):
        # Additive fields keep the immutable intent of requests that omit them
        # (see RunnerSubmit.intent): legacy payloads serialize byte-identically,
        # and each new key appears only when the client actually supplied it.
        value = self.model_dump(mode='json')
        value.pop('client_request_id')
        if self.answers is None:
            value.pop('answers')
        if not self.skip:
            value.pop('skip')
        return value

    def digest(self):
        return hashlib.sha256(canonical_json(self.intent()).encode()).hexdigest()


def public_control(row):
    return {key:row.get(key) for key in ('control_id','runner_id','action','client_request_id',
        'status','error_code','accepted_at','consumed_at')}
