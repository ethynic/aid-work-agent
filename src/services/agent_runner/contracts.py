"""Public requests and persistent application states, independent of transport IO."""

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator
from src.core.agent_engine.contracts import Identity


class RunnerStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    PAUSED = "paused"
    INTERRUPTED = "interrupted"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {RunnerStatus.COMPLETED.value, RunnerStatus.FAILED.value, RunnerStatus.CANCELLED.value}


class SessionRef(BaseModel):
    kind: Literal["web", "channel"]
    session_id: str = Field(min_length=1, max_length=256)

    class Config:
        extra = "forbid"


class AttachmentRef(BaseModel):
    """File IDs are resolved under the authenticated storage scope by the worker."""
    file_id: Optional[str] = Field(default=None, max_length=128)
    name: str = Field(default="unknown", max_length=512)
    type: Literal["file", "image"] = "file"
    mime_type: str = Field(default="", max_length=128)
    content: Optional[str] = None
    size: Optional[int] = Field(default=None, ge=0)

    class Config:
        extra = "forbid"


class SessionQuery(BaseModel):
    session: SessionRef
    source: Literal["chat", "wecom_kf", "feishu", "dingtalk"] = "chat"
    profile_id: str = Field(default="main", min_length=1, max_length=128)
    # Only platform identifiers are submitted for a channel actor. The internal
    # tenant/user are derived from the registered session, never supplied here.
    channel_user_id: Optional[str] = Field(default=None, max_length=256)
    channel_chat_id: Optional[str] = Field(default=None, max_length=256)

    @field_validator("channel_chat_id", mode="before")
    @classmethod
    def direct_chat(cls, value):
        return None if value == "" else value
    class Config:
        extra = "forbid"


class RunnerSubmit(SessionQuery):
    client_request_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=1_000_000)
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=100)
    prompt_augmentations: list[str] = Field(default_factory=list, max_length=100)
    request_data: dict[str, Any] = Field(default_factory=dict)
    instance_id: Optional[str] = Field(default=None, max_length=256)
    routing_policy: Literal["explicit", "default_single"] = "explicit"

    def intent(self):
        data = self.model_dump(mode="json")
        data.pop("client_request_id")
        # Additive fields must not change the immutable intent of accepted M2
        # requests when omitted or empty. Nonempty request data remains covered.
        if not data["request_data"]:
            data.pop("request_data")
        if data["instance_id"] is None:
            data.pop("instance_id")
        if data["routing_policy"] == "explicit":
            data.pop("routing_policy")
        for attachment in data["attachments"]:
            if attachment["size"] is None:
                attachment.pop("size")
        return data

    def digest(self):
        return hashlib.sha256(canonical_json(self.intent()).encode("utf-8")).hexdigest()


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def scope_key(tenant_id):
    return "global" if tenant_id is None else "tenant:" + tenant_id


@dataclass(frozen=True)
class Principal:
    identity: Identity
    actor_kind: str
    actor_id: str
    service_id: str

    @property
    def scope_key(self):
        return scope_key(self.identity.tenant_id)


class RunnerError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status
