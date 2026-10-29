"""Versioned notification and adapter contracts; no browser fields in the core."""
from dataclasses import dataclass
from typing import Literal, Protocol
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

SCHEMA_VERSION = 1
EVENT_TYPES = frozenset({'task.completed', 'task.failed', 'notification.test',
                         'billing.credit_low', 'billing.tenant_expiry', 'abuse.rule_triggered'})
# 规则引擎事件级消息 policy_key（设计 §2/§5）：recipient NULL，展开阶段按租户规则路由
RULE_EVENT_POLICY_KEY = 'rule_event'


class NotificationError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra='forbid')


class WebKeys(StrictDTO):
    p256dh: str = Field(min_length=1, max_length=128)
    auth: str = Field(min_length=1, max_length=64)


class WebConfiguration(StrictDTO):
    endpoint: str = Field(min_length=1, max_length=4096)
    keys: WebKeys
    expirationTime: int | None = Field(default=None, ge=0)
    key_id: str = Field(min_length=1, max_length=64)


class RegisterTarget(StrictDTO):
    channel: Literal['web_push']
    provider: Literal['web_push']
    installation_ref: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,128}$')
    expected_binding_version: StrictInt | None = Field(default=None, ge=1)
    configuration: WebConfiguration


class PreferenceRequest(StrictDTO):
    category: Literal['task']
    channel: Literal['web_push']
    provider: Literal['web_push']
    enabled: StrictBool


class TestRequest(StrictDTO):
    target_id: str = Field(min_length=1, max_length=128)
    binding_version: StrictInt = Field(ge=1)


class ImWebhookConfiguration(StrictDTO):
    webhook_url: str = Field(min_length=1, max_length=2048)


class EmailConfiguration(StrictDTO):
    email_address: str = Field(min_length=3, max_length=254)


TENANT_CHANNEL_PROVIDERS = {
    'im_webhook': frozenset({'feishu_bot', 'dingtalk_bot', 'wecom_bot'}),
    'email': frozenset({'smtp'}),
}


class TenantRegisterTarget(StrictDTO):
    channel: Literal['im_webhook', 'email']
    provider: Literal['feishu_bot', 'dingtalk_bot', 'wecom_bot', 'smtp']
    expected_binding_version: StrictInt | None = Field(default=None, ge=1)
    configuration: ImWebhookConfiguration | EmailConfiguration
    # 规则级目标登记（规则引擎，设计 §10）：非空时目标挂到该规则下
    rule_id: str | None = Field(default=None, min_length=1, max_length=64)


class RuleCreate(StrictDTO):
    """通知规则创建（规则引擎，设计 §10）；事件白名单与条件校验在 repository。"""
    event_type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    conditions: dict = Field(default_factory=dict)
    recipient_mode: Literal['tenant_admins', 'kf_assignee']
    include_default_target: StrictBool = False
    enabled: StrictBool = True


class RuleUpdate(StrictDTO):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    conditions: dict | None = None
    recipient_mode: Literal['tenant_admins', 'kf_assignee'] | None = None
    include_default_target: StrictBool | None = None
    enabled: StrictBool | None = None


@dataclass(frozen=True)
class Subject:
    user_id: str
    tenant_id: str | None

    @property
    def scope_key(self):
        return 'global' if self.tenant_id is None else 'tenant:' + self.tenant_id


@dataclass(frozen=True)
class AdapterCapabilities:
    target_schema_version: int = 1
    notification_schema_versions: tuple[int, ...] = (1,)
    payload_limit: int = 3072
    receipts: bool = False
    idempotency: bool = False


@dataclass(frozen=True)
class SendResult:
    outcome: Literal['accepted', 'retryable', 'permanent_failure', 'target_invalid']
    error_class: str | None = None
    retry_after: int | None = None
    provider_id: str | None = None


class Adapter(Protocol):
    channel: str
    provider: str
    capabilities: AdapterCapabilities

    def available(self) -> bool: ...
    def validate_target(self, configuration: dict) -> str: ...
    def render(self, message: dict, target: dict) -> dict: ...
    def send(self, configuration: dict, payload: dict, ttl: int) -> SendResult: ...


class AdapterRegistry:
    def __init__(self, adapters=()):
        self._adapters = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter):
        key = (adapter.channel, adapter.provider)
        if key in self._adapters:
            raise ValueError('NOTIFICATION_ADAPTER_ALREADY_REGISTERED')
        self._adapters[key] = adapter

    def get(self, channel, provider):
        return self._adapters.get((channel, provider))


def session_title(value):
    text = ''.join(c for c in (value if isinstance(value, str) else '')
                   if not unicodedata.category(c).startswith('C'))
    return text.strip()[:80] or '未命名会话'
