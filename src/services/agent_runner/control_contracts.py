"""Immutable private control intents. They are neither events nor usage receipts."""

import hashlib
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .contracts import AttachmentRef, canonical_json


class RunnerControl(BaseModel):
    client_request_id: str = Field(min_length=1, max_length=128)
    action: Literal['pause','resume','reply','browser_complete']
    target_execution_id: str | None = Field(default=None, max_length=256)
    wait_id: str | None = Field(default=None, max_length=256)
    answer: str = Field(default='', max_length=1_000_000)
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=100)
    completion_ref: str | None = Field(default=None, max_length=256)

    class Config:
        extra = 'forbid'

    @model_validator(mode='after')
    def consistent_action(self):
        if self.action in ('reply','browser_complete') and (not self.wait_id or not self.target_execution_id):
            raise ValueError('CONTROL_WAIT_REQUIRED')
        if self.action == 'reply' and not (self.answer.strip() or self.attachments):
            raise ValueError('CONTROL_REPLY_REQUIRED')
        if self.action not in ('reply','resume') and (self.answer or self.attachments):
            raise ValueError('CONTROL_REPLY_UNEXPECTED')
        if self.action == 'browser_complete' and not self.completion_ref:
            raise ValueError('CONTROL_COMPLETION_REQUIRED')
        if self.action != 'browser_complete' and self.completion_ref:
            raise ValueError('CONTROL_COMPLETION_UNEXPECTED')
        return self

    def intent(self):
        value = self.model_dump(mode='json')
        value.pop('client_request_id')
        return value

    def digest(self):
        return hashlib.sha256(canonical_json(self.intent()).encode()).hexdigest()


def public_control(row):
    return {key:row.get(key) for key in ('control_id','runner_id','action','client_request_id',
        'status','error_code','accepted_at','consumed_at')}
