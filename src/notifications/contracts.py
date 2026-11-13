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
PLATFORM_RUNNER_POLICY_KEY = 'platform_runner_alert'
PLATFORM_RUNNER_INSTALLATION_REF = 'runner_alert_platform'


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
    # wecom_app：企微任务通知偏好（绑定后开关用，wecom-app-task-notification-design
    # §4.5；target 登记是服务端行为，偏好开关仍走本 API）
    category: Literal['task']
    channel: Literal['web_push', 'wecom_app']
    provider: Literal['web_push', 'wecom_app']
    enabled: StrictBool


class TestRequest(StrictDTO):
    target_id: str = Field(min_length=1, max_length=128)
    binding_version: StrictInt = Field(ge=1)


class ImWebhookConfiguration(StrictDTO):
    webhook_url: str = Field(min_length=1, max_length=2048)


class PlatformRunnerTargetRequest(StrictDTO):
    webhook_url: str = Field(min_length=1, max_length=2048)
    expected_binding_version: StrictInt | None = Field(default=None, ge=1)


class EmailConfiguration(StrictDTO):
    email_address: str = Field(min_length=3, max_length=254)


class WecomBindConfirm(StrictDTO):
    """手机号兜底确认绑定：userid 仅回传预填值，服务端以登录用户手机号重新核实（设计 §4.1）"""
    userid: str = Field(min_length=1, max_length=64)


class WecomAppConfiguration(StrictDTO):
    """企微应用通知目标配置（wecom-app-task-notification-design §4.3）。

    目标注册是绑定成功后的服务端内部行为（OAuth/确认绑定后登记），不走用户
    提交的 RegisterTarget API；RegisterTarget.channel 维持 web_push 不扩。"""
    wecom_userid: str = Field(min_length=1, max_length=64)
    # 绑定成员姓名快照（企微 user/get 返回，设置页展示用，非机密）
    bound_user_name: str = Field(default='', max_length=128)
    tenant_id: str = Field(default='', max_length=64)


class WecomAppTargetRegistration(StrictDTO):
    """绑定事务内服务端登记 wecom_app target 的载体（复用
    repository.register_target 的 upsert/归属/幂等语义）。"""
    channel: Literal['wecom_app'] = 'wecom_app'
    provider: Literal['wecom_app'] = 'wecom_app'
    installation_ref: str = Field(min_length=1, max_length=128)
    expected_binding_version: StrictInt | None = Field(default=None, ge=1)
    configuration: WecomAppConfiguration


TENANT_CHANNEL_PROVIDERS = {
    'im_webhook': frozenset({'feishu_bot', 'dingtalk_bot', 'wecom_bot'}),
    'email': frozenset({'smtp'}),
}


class TenantRegisterTarget(StrictDTO):
    channel: Literal['im_webhook', 'email']
    provider: Literal['feishu_bot', 'dingtalk_bot', 'wecom_bot', 'smtp']
    expected_binding_version: StrictInt | None = Field(default=None, ge=1)
    configuration: ImWebhookConfiguration | EmailConfiguration
    # 规则级目标登记（规则引擎，设计 §10）：目标恒挂到具体规则下
    rule_id: str = Field(min_length=1, max_length=64)


class RuleCreate(StrictDTO):
    """通知规则创建（规则引擎，设计 §10）；事件白名单与条件校验在 repository。

    task_owner 仅用于 scheduled.task 聚合规则（站内信恒发任务属主，仅占位）。"""
    event_type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    conditions: dict = Field(default_factory=dict)
    recipient_mode: Literal['tenant_admins', 'kf_assignee', 'task_owner']
    enabled: StrictBool = True


class RuleUpdate(StrictDTO):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    conditions: dict | None = None
    recipient_mode: Literal['tenant_admins', 'kf_assignee', 'task_owner'] | None = None
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
