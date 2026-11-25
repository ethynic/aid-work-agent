"""飞书应用消息通知 Adapter（飞书任务通知接入，2026-11-15；设计决策 7 追加，
完全复刻企微通知的租户级模式）。

channel=provider='feishu_app'：target 由绑定成功后服务端登记（不走用户提交的
RegisterTarget API），发送走租户飞书应用的 interactive 卡片消息，receive_id 为
单个 open_id。

**凭据租户自备**：app_id/app_secret 按目标租户读租户渠道配置——飞书应用配置
（channel_type=feishu，连接中心「飞书」）通知用途为 both/notify_only 的最新
一条（notify_usage 缺省 agent_only 的存量配置不参与，存量行为不变）。adapter
仍按 channel 全局单例注册（registry 不变），但"可用性"与"发送凭据"按租户延迟
解析（对齐 WecomAppAdapter）：

- available_for_tenant(tenant_id) / credentials_for_tenant(tenant_id)：
  repository/expand/binding 的租户感知点调用；无 available_for_tenant 语义的
  全局 available() 恒 False（平台无全局通知应用，不带租户上下文的 gate 一律
  fail-closed）。凭据每次实时读库（租户保存配置立即生效）；
- tenant_access_token 按租户缓存（expire 提前 300 秒作废；过期后下次发送强制
  取新 token）。飞书以响应 code 报错（HTTP 200 + code 非零），本期错误码语义
  （2026-11-15 官方文档查证）不含 token 失效专项码，不做 code 触发的刷新重试。

- 出站复用 web_push 的 PinnedHTTPSConnection/public_addresses：域名固定
  open.feishu.cn + 公网地址解析校验，无重定向、无环境代理；
- 响应 code 分类（2026-11-15 官方文档查证）：0→accepted；230013（应用不在
  可用范围）/230034（receive_id 无效）→target_invalid（由 repository.finish
  撤销目标并标记绑定失效，引导重新绑定）；230020→retryable 限流；HTTP
  429/5xx/网络→retryable 有限重试；其余 code→permanent_failure（沿
  im_webhook 兜底语义）。前提：应用需开启机器人能力并发布，成员在应用可用范围。
- 卡片：msg_type=interactive，content 为卡片 JSON 序列化字符串（≤30KB）——
  header 标题=会话名+任务完成/失败，div 说明含执行时长，button「查看结果」
  url={public_base_url}/notification?ref={notification_ref}（语义与企微
  textcard url 一致；租户消息追加 &tenant= 供落地页选租户凭证）。
安全：app_secret/tenant_access_token 仅出现在请求路径，日志只含接口名与 code。
"""
import hashlib
import http.client
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

from loguru import logger

from .contracts import AdapterCapabilities, NotificationError, SendResult
from .web_push import PinnedHTTPSConnection, public_addresses

# 飞书开放平台出站域名（2026-11-15 官方文档查证）
FEISHU_API_HOST = 'open.feishu.cn'

# 卡片文案字节上限（自有截断防御，远小于 content 整体 30KB 协议上限）
_CARD_TITLE_BYTES = 128
_CARD_DESCRIPTION_BYTES = 512

# open_id 形态边界校验（服务端 batch_get_id 来源可信，仅做形态边界：ou_ 前缀
# + 十六进制主体，量级宽松）
_OPEN_ID_PATTERN = re.compile(r'ou_[0-9a-f]{32,}', re.ASCII)


@dataclass(frozen=True)
class FeishuAppCredentials:
    """单个租户的飞书通知凭据（租户渠道配置明文读取；仅服务端内存持有）"""
    tenant_id: str
    app_id: str
    app_secret: str


def _credentials_from_channel_config(tenant_id, config):
    """从飞书渠道 config 提取通知凭据（app_id/app_secret 全齐才有效；
    encrypt_key/verification_token 为回调加解密字段，通知不需要）。"""
    config = config or {}
    app_id = str(config.get('app_id') or '').strip()
    app_secret = str(config.get('app_secret') or '')
    if not (app_id and app_secret):
        return None
    return FeishuAppCredentials(tenant_id=str(tenant_id), app_id=app_id,
                                app_secret=app_secret)


def _default_credentials_source(tenant_id):
    """默认租户凭据解析：飞书应用配置 channel_type=feishu 中通知用途为
    both/notify_only 的最新一条；无则 None。"""
    from src.saas.db.channel_config_db import ChannelConfigDB
    row = ChannelConfigDB.get_latest_feishu_notify_usage_decrypted(tenant_id)
    if not row:
        return None
    return _credentials_from_channel_config(tenant_id, row.get('config') or {})


def _json_object(raw):
    try:
        data = json.loads(raw or b'{}')
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _clip(text, limit):
    """utf-8 字节上限内按字符边界截断（与 wecom_app 同一口径）。"""
    raw = text.encode('utf-8')
    if len(raw) <= limit:
        return text
    return raw[:limit].decode('utf-8', errors='ignore')


def _duration_label(seconds):
    """秒 → 人类可读时长（5分32秒 / 1小时 / 1小时1分1秒）；非正整数（缺省/
    0=时长未知/类型异常）返回空串由调用方走旧文案兜底。与 wecom_app 同口径。"""
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        return ''
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f'{hours}小时')
    if minutes:
        parts.append(f'{minutes}分')
    if secs or not parts:
        parts.append(f'{secs}秒')
    return ''.join(parts)


class FeishuAppAdapter:
    channel = provider = 'feishu_app'
    capabilities = AdapterCapabilities(payload_limit=2048)
    host = FEISHU_API_HOST
    # 响应 code 分类（2026-11-15 官方文档查证，严格映射）
    TARGET_INVALID_CODES = (230013, 230034)
    RATE_LIMIT_CODE = 230020

    def __init__(self, timeout_seconds=10, credentials_source=None):
        self.timeout_seconds = timeout_seconds
        self._credentials_source = credentials_source or _default_credentials_source
        # tenant_id -> (tenant_access_token, expires_at)
        self._tokens = {}

    def available(self):
        """无平台全局凭据（租户自备配置后的全局判定恒 False）：不带租户
        上下文的 gate 一律不可用；租户感知点必须走 available_for_tenant。"""
        return False

    def available_for_tenant(self, tenant_id):
        """租户在连接中心保存了通知用途的飞书应用配置（both/notify_only）才可用。"""
        return self.credentials_for_tenant(tenant_id) is not None

    def credentials_for_tenant(self, tenant_id):
        """按租户实时解析通知凭据（租户保存配置立即生效）；
        未配置/凭据不全/读取失败返回 None（读取失败留痕，按未配置处理）。"""
        tenant_id = str(tenant_id or '').strip()
        if not tenant_id:
            return None
        try:
            return self._credentials_source(tenant_id)
        except Exception:
            # 配置读取失败按未配置处理（不阻断通知主流程）
            logger.opt(exception=True).warning('飞书通知租户凭据读取失败')
            return None

    def validate_target(self, configuration):
        from .contracts import FeishuAppConfiguration
        dto = FeishuAppConfiguration.model_validate(configuration)
        if not _OPEN_ID_PATTERN.fullmatch(dto.feishu_open_id):
            raise NotificationError('NOTIFICATION_INVALID_FEISHU_OPEN_ID')
        # endpoint 即 feishu_open_id（通知目标全局唯一），sha256 摘要与
        # im_webhook/web_push/wecom_app 保持同一唯一性口径
        return hashlib.sha256(dto.feishu_open_id.encode()).hexdigest()

    def render(self, message, target):
        parameters = message.get('parameters') if isinstance(message.get('parameters'), dict) else {}
        title, description = self._card_text(message.get('event_type'), parameters)
        return {'schema_version': 1, 'notification_ref': message['notification_ref'],
                'title': _clip(title, _CARD_TITLE_BYTES),
                'description': _clip(description, _CARD_DESCRIPTION_BYTES),
                'url': self._notification_url(message)}

    @staticmethod
    def _card_text(event_type, parameters):
        """卡片文案（对齐 wecom_app._textcard_text）：飞书渠道仅终态通知
        （PROVIDER_ALLOWED_EVENT_TYPES），只渲染 completed/failed；waiting_input
        等非终态映射已删除，误接事件 fail loud。会话名快照沿用事件参数；
        时长取事件参数 duration_seconds（0/缺省=未知）走旧文案兜底。"""
        from .events import weixin_batch_text

        if batch := weixin_batch_text(parameters):
            return batch
        session = parameters.get('session_title') or '任务'
        duration = _duration_label(parameters.get('duration_seconds'))
        if event_type == 'task.completed':
            description = (f'任务已执行 {duration}，点击查看结果。' if duration
                           else '任务已执行完成，点击查看结果。')
            return f'{session}相关任务已完成', description
        if event_type == 'task.failed':
            if summary := parameters.get('recovery_summary'):
                return f'{session}相关任务执行失败', summary
            description = (f'任务已执行 {duration} 后失败，点击查看结果。' if duration
                           else '任务执行失败，点击查看结果。')
            return f'{session}相关任务执行失败', description
        raise ValueError('NOTIFICATION_EVENT_UNSUPPORTED')

    @staticmethod
    def _notification_url(message):
        """点击回跳（对齐 wecom_app）：{public_base_url}/notification?ref={ref}；
        租户消息追加 &tenant={tenant_id}（落地页按 tenant 选租户凭证）。未配置
        公网域名时无可用落地页，渲染失败留痕。"""
        from src.config.settings import settings
        base = (getattr(settings.app, 'public_base_url', '') or '').strip().rstrip('/')
        ref = str(message.get('notification_ref') or '')
        if not base or not ref:
            raise ValueError('NOTIFICATION_PUBLIC_URL_REQUIRED')
        url = base + '/notification?ref=' + quote(ref, safe='')
        tenant_id = str(message.get('tenant_id') or '').strip()
        if tenant_id:
            url += '&tenant=' + quote(tenant_id, safe='')
        return url

    def _card_content(self, payload):
        """interactive 卡片（2026-11-15 官方文档查证的 JSON 结构，序列化为
        content 字符串）：header 标题 + div 说明（lark_md）+ action button
        「查看结果」跳转回跳 url。"""
        card = {
            'config': {'wide_screen_mode': True},
            'header': {'title': {'tag': 'plain_text', 'content': payload['title']},
                       'template': 'blue'},
            'elements': [
                {'tag': 'div', 'text': {'tag': 'lark_md', 'content': payload['description']}},
                {'tag': 'action', 'actions': [{'tag': 'button',
                                               'text': {'tag': 'plain_text', 'content': '查看结果'},
                                               'type': 'primary', 'url': payload['url']}]},
            ],
        }
        return json.dumps(card, ensure_ascii=False)

    def _request_body(self, configuration, payload):
        """im/v1/messages 组装：receive_id 单 open_id（一对一）；content 是
        卡片 JSON 序列化后的字符串。"""
        return {'receive_id': configuration['feishu_open_id'], 'msg_type': 'interactive',
                'content': self._card_content(payload)}

    def _call(self, address, method, path, body=None, token=None):
        connection = None
        try:
            connection = PinnedHTTPSConnection(self.host, address, self.timeout_seconds)
            if isinstance(body, bytes):
                payload_bytes = body
            elif body is not None:
                payload_bytes = json.dumps(body, ensure_ascii=False).encode()
            else:
                payload_bytes = None
            headers = {}
            if payload_bytes:
                headers['Content-Type'] = 'application/json'
            if token:
                headers['Authorization'] = 'Bearer ' + token
            connection.request(method, path, payload_bytes, headers)
            response = connection.getresponse()
            return response.status, response.read(65536)
        finally:
            if connection is not None:
                connection.close()

    def _access_token(self, address, credentials):
        """按租户缓存 tenant_access_token（expire 提前 300 秒作废，过期后下次
        发送强制取新 token）；获取失败返回 None。日志只含接口名与 code，不含
        app_secret/tenant_access_token。"""
        cached = self._tokens.get(credentials.tenant_id)
        if cached and cached[1] > time.time():
            return cached[0]
        status, raw = self._call(address, 'POST',
                                 '/open-apis/auth/v3/tenant_access_token/internal',
                                 body={'app_id': credentials.app_id,
                                       'app_secret': credentials.app_secret})
        data = _json_object(raw) if status == 200 else None
        token = data.get('tenant_access_token') if data else None
        if not token:
            code = data.get('code', 'N/A') if data else 'N/A'
            logger.warning(f'飞书通知 tenant_access_token 获取失败: code={code}')
            return None
        expire = data.get('expire')
        self._tokens[credentials.tenant_id] = (
            token,
            time.time() + (expire if isinstance(expire, int) and expire > 300 else 7200) - 300)
        return token

    def _exchange(self, address, credentials, body):
        """取 token 并发一次 im/v1/messages；飞书以响应 code 报错（HTTP 200 +
        code 非零），本期语义无 token 失效专项码，不做 code 触发的刷新重试。"""
        token = self._access_token(address, credentials)
        if not token:
            return SendResult('retryable', 'provider_authentication')
        status, raw = self._call(address, 'POST',
                                 '/open-apis/im/v1/messages?receive_id_type=open_id',
                                 body=body, token=token)
        data = _json_object(raw)
        if status in (200, 201, 202):
            return self._interpret(data.get('code') if data else None, data)
        if status == 429 or status >= 500:
            return SendResult('retryable', 'rate_limited' if status == 429 else 'provider_unavailable')
        return SendResult('permanent_failure', 'provider_rejected')

    def _interpret(self, code, data):
        """im/v1/messages 响应 code → SendResult（2026-11-15 官方文档查证语义）；
        非零未知 code 按 im_webhook 兜底语义归配置类失败不重试。"""
        if code == 0:
            message_id = None
            if isinstance(data, dict) and isinstance(data.get('data'), dict):
                message_id = data['data'].get('message_id')
            return SendResult('accepted', provider_id=str(message_id) if message_id else None)
        if code in self.TARGET_INVALID_CODES:
            return SendResult('target_invalid', 'feishu_open_id_invalid')
        if code == self.RATE_LIMIT_CODE:
            return SendResult('retryable', 'rate_limited')
        return SendResult('permanent_failure', 'provider_rejected')

    def send(self, configuration, payload, ttl):
        """发送凭据按 target 配置里的 tenant_id 延迟解析（租户自备）；租户
        无可用凭据（配置已删/不全）按配置类失败不重试。"""
        tenant_id = str((configuration or {}).get('tenant_id') or '').strip()
        credentials = self.credentials_for_tenant(tenant_id) if tenant_id else None
        if credentials is None:
            return SendResult('permanent_failure', 'provider_configuration')
        try:
            address = public_addresses(self.host)[0]
            body = json.dumps(self._request_body(configuration, payload),
                              ensure_ascii=False).encode()
        except NotificationError:
            return SendResult('permanent_failure', 'endpoint_forbidden')
        except (KeyError, TypeError, ValueError):
            return SendResult('permanent_failure', 'provider_configuration')
        try:
            return self._exchange(address, credentials, body)
        except NotificationError:
            return SendResult('permanent_failure', 'endpoint_forbidden')
        except (OSError, TimeoutError, http.client.HTTPException):
            return SendResult('retryable', 'transport_unavailable')
        except Exception:
            return SendResult('permanent_failure', 'provider_configuration')
