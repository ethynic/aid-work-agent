from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.social_media.enums import PlatformCapability


class CapabilityNotSupported(Exception):
    def __init__(self, capability: str):
        super().__init__(f"capability_not_supported:{capability}")
        self.capability = capability


@dataclass(frozen=True)
class AccountCapabilities:
    supported: frozenset[PlatformCapability]
    limits: dict[str, Any] = field(default_factory=dict)
    detected_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "supported": [item.value for item in sorted(self.supported, key=lambda c: c.value)],
            "limits": self.limits,
            "detected_at": self.detected_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass(frozen=True)
class ContentSpec:
    platform: str
    version: str
    content_types: tuple[str, ...]
    field_limits: dict[str, Any]
    required_fields: tuple[str, ...]
    prompt_version: str


class SocialPlatformConnector(ABC):
    platform: str

    @abstractmethod
    async def validate_account(self, account: dict[str, Any]) -> AccountCapabilities:
        ...

    @abstractmethod
    async def validate_variant(self, variant: dict[str, Any]) -> dict[str, Any]:
        ...

    @abstractmethod
    async def prepare_payload(self, variant: dict[str, Any]) -> dict[str, Any]:
        ...

    async def create_remote_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise CapabilityNotSupported("remote_draft")

    async def publish(self, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        raise CapabilityNotSupported("api_publish")

    async def query_publish_status(self, external_task_id: str) -> dict[str, Any]:
        raise CapabilityNotSupported("publish_status")

    async def build_assisted_package(self, variant: dict[str, Any]) -> dict[str, Any]:
        raise CapabilityNotSupported("assisted_publish")

    async def fetch_metrics(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        raise CapabilityNotSupported("api_analytics")

    @abstractmethod
    def map_error(self, error: Exception) -> dict[str, Any]:
        ...

    @abstractmethod
    def normalize_metrics(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ...

