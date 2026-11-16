"""钉钉应用工作通知 Adapter（钉钉任务通知接入，2026-11-16；设计决策 7 追加，
完全复刻飞书/企微通知的租户级模式）。

channel=provider='dingtalk_app'：target 由绑定成功后服务端登记（不走用户提交的
RegisterTarget API），发送走租户钉钉应用的 action_card 工作通知
（topapi/message/corpconversation/asyncsend_v2，单 userid）。

**凭据租户自备**：app_key/app_secret/agent_id 按目标租户读租户渠道配置——钉钉
应用配置（channel_type=dingtalk，连接中心「钉钉」）通知用途为 both/notify_only
的最新一条（notify_usage 缺省 agent_only 的存量配置不参与，存量行为不变；
borrow 路径 config 缺 agent_id 按未配置处理）。adapter 仍按 channel 全局单例
注册（registry 不变），但"可用性"与"发送凭据"按租户延迟解析（对齐
WecomAppAdapter/FeishuAppAdapter）：

- available_for_tenant(tenant_id) / credentials_for_tenant(tenant_id)：
  repository/expand/binding 的租户感知点调用；无 available_for_tenant 语义的
  全局 available() 恒 False（平台无全局通知应用，不带租户上下文的 gate 一律
  fail-closed）。凭据每次实时读库（租户保存配置立即生效）；
- access_token 按租户缓存（expires_in 提前 300 秒作废）；发送返回 40014 时
  强制刷新该租户 token 并重试一次（2026-11-16 官方文档查证 access_token
  失效码即 40014）。

- 出站复用 web_push 的 PinnedHTTPSConnection/public_addresses：域名固定
  oapi.dingtalk.com + 公网地址解析校验，无重定向、无环境代理；
- 响应 errcode 分类（2026-11-16 官方文档查证）：0→accepted（asyncsend_v2 为
  异步受理，返回 task_id；送达结果 getsendresult 本期不查，诚实语义记
  accepted）；60104（找不到该用户）/81013（userid 不在可见范围）→
  target_invalid（由 repository.finish 撤销目标并标记绑定失效，引导重新绑定）；
  -1/799913（系统繁忙/限频）→retryable 限流；HTTP 429/5xx/网络→retryable 有限
  重试；其余 errcode（40035 缺参数等）→permanent_failure（沿 im_webhook 兜底
  语义；钉钉 userid 失效场景少，靠手机号重绑兜底）。前提：应用需开通工作通知
  接口权限，接口调用域名 oapi.dingtalk.com。
- 卡片：msg.action_card，title=会话名+任务完成/失败，markdown=说明（执行时长），
  btn_orientation=1（按钮竖排数字），btn_list=[{title「查看结果」, action_url=
  {public_base_url}/notification?ref={notification_ref}}]（语义与企微 textcard
  url 一致；租户消息追加 &tenant= 供落地页选租户凭证）。
安全：app_secret/access_token 仅出现在请求路径，token 在 URL query——日志只含
接口名与 errcode，不含完整 URL（含 token）/app_secret/access_token。
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

# 钉钉开放平台出站域名（2026-11-16 官方文档查证）
DINGTALK_API_HOST = 'oapi.dingtalk.com'

# action_card 文案字节上限（自有截断防御，与 wecom_app/feishu_app 同一口径）
_CARD_TITLE_BYTES = 128
_CARD_DESCRIPTION_BYTES = 512

# userid 服务端来源（getbymobile 按登录用户手机号核实）可信，仅做形态边界校验：
# 非空、无空白与控制字符、长度 ≤64（与企微 userid 上限同口径的宽松边界）
_USERID_PATTERN = re.compile(r'[^\s\x00-\x1f]{1,64}', re.ASCII)


@dataclass(frozen=True)
class DingtalkAppCredentials:
    """单个租户的钉钉通知凭据（租户渠道配置明文读取；仅服务端内存持有）"""
    tenant_id: str
    app_key: str
    app_secret: str
    agent_id: int


def _credentials_from_channel_config(tenant_id, config):
    """从钉钉渠道 config 提取通知凭据（app_key/app_secret/agent_id 全齐才有效；
    token/encoding_aes_key 为回调字段，通知不需要；agent_id 非数字视为缺失）。
    """
    config = config or {}
    app_key = str(config.get('app_key') or '').strip()
    app_secret = str(config.get('app_secret') or '')
    try:
        agent_id = int(config.get('agent_id') or 0)
    except (TypeError, ValueError):
        agent_id = 0
    if not (app_key and app_secret and agent_id):
        return None
    return DingtalkAppCredentials(tenant_id=str(tenant_id), app_key=app_key,
                                  app_secret=app_secret, agent_id=agent_id)


def _default_credentials_source(tenant_id):
    """默认租户凭据解析：钉钉应用配置 channel_type=dingtalk 中通知用途为
    both/notify_only 的最新一条；无则 None。"""
    from src.saas.db.channel_config_db import ChannelConfigDB
    row = ChannelConfigDB.get_latest_dingtalk_notify_usage_decrypted(tenant_id)
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
    """utf-8 字节上限内按字符边界截断（与 wecom_app/feishu_app 同一口径）。"""
    raw = text.encode('utf-8')
    if len(raw) <= limit:
        return text
    return raw[:limit].decode('utf-8', errors='ignore')


def _duration_label(seconds):
    """秒 → 人类可读时长（5分32秒 / 1小时 / 1小时1分1秒）；非正整数（缺省/
    0=时长未知/类型异常）返回空串由调用方走旧文案兜底。与 feishu_app 同口径。"""
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


class DingTalkAppAdapter:
    channel = provider = 'dingtalk_app'
    capabilities = AdapterCapabilities(payload_limit=2048)
    host = DINGTALK_API_HOST
    # errcode 分类（2026-11-16 官方文档查证，严格映射）
    TOKEN_EXPIRED_ERRCODES = (40014,)
    TARGET_INVALID_ERRCODES = (60104, 81013)
    RATE_LIMIT_ERRCODES = (-1, 799913)

    def __init__(self, timeout_seconds=10, credentials_source=None):
        self.timeout_seconds = timeout_seconds
        self._credentials_source = credentials_source or _default_credentials_source
        # tenant_id -> (access_token, expires_at)
        self._tokens = {}

    def available(self):
        """无平台全局凭据（租户自备配置后的全局判定恒 False）：不带租户
        上下文的 gate 一律不可用；租户感知点必须走 available_for_tenant。"""
        return False

    def available_for_tenant(self, tenant_id):
        """租户在连接中心保存了通知用途的钉钉应用配置（both/notify_only，含
        agent_id）才可用。"""
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
            logger.opt(exception=True).warning('钉钉通知租户凭据读取失败')
            return None

    def validate_target(self, configuration):
        from .contracts import DingtalkAppConfiguration
        dto = DingtalkAppConfiguration.model_validate(configuration)
        if not _USERID_PATTERN.fullmatch(dto.dingtalk_userid):
            raise NotificationError('NOTIFICATION_INVALID_DINGTALK_USERID')
        # endpoint 即 dingtalk_userid（通知目标全局唯一），sha256 摘要与
        # im_webhook/web_push/wecom_app/feishu_app 保持同一唯一性口径
        return hashlib.sha256(dto.dingtalk_userid.encode()).hexdigest()

    def render(self, message, target):
        parameters = message.get('parameters') if isinstance(message.get('parameters'), dict) else {}
        title, description = self._card_text(message.get('event_type'), parameters)
        return {'schema_version': 1, 'notification_ref': message['notification_ref'],
                'title': _clip(title, _CARD_TITLE_BYTES),
                'description': _clip(description, _CARD_DESCRIPTION_BYTES),
                'url': self._notification_url(message)}

    @staticmethod
    def _card_text(event_type, parameters):
        """卡片文案（对齐 feishu_app._card_text）：钉钉渠道仅终态通知
        （PROVIDER_ALLOWED_EVENT_TYPES），只渲染 completed/failed；waiting_input
        等非终态映射已删除，误接事件 fail loud。会话名快照沿用事件参数；
        时长取事件参数 duration_seconds（0/缺省=未知）走旧文案兜底。"""
        session = parameters.get('session_title') or '任务'
        duration = _duration_label(parameters.get('duration_seconds'))
        if event_type == 'task.completed':
            description = (f'任务已执行 {duration}，点击查看结果。' if duration
                           else '任务已执行完成，点击查看结果。')
            return f'{session}相关任务已完成', description
        if event_type == 'task.failed':
            description = (f'任务已执行 {duration} 后失败，点击查看结果。' if duration
                           else '任务执行失败，点击查看结果。')
            return f'{session}相关任务执行失败', description
        raise ValueError('NOTIFICATION_EVENT_UNSUPPORTED')

    @staticmethod
    def _notification_url(message):
        """点击回跳（对齐 wecom_app/feishu_app）：{public_base_url}/notification?ref=
        {ref}；租户消息追加 &tenant={tenant_id}（落地页按 tenant 选租户凭证）。
        未配置公网域名时无可用落地页，渲染失败留痕。"""
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

    def _request_body(self, configuration, payload, credentials):
        """asyncsend_v2 组装（2026-11-16 官方文档查证结构）：agent_id 数字、
        userid_list 字符串（逗号分隔，本期单 userid）、msg.msgtype=action_card、
        btn_orientation 数字 1（按钮竖排）。"""
        return {'agent_id': credentials.agent_id,
                'userid_list': configuration['dingtalk_userid'],
                'msg': {'msgtype': 'action_card',
                        'action_card': {'title': payload['title'],
                                        'markdown': payload['description'],
                                        'btn_orientation': 1,
                                        'btn_list': [{'title': '查看结果',
                                                      'action_url': payload['url']}]}}}

    def _call(self, address, method, path, body=None):
        connection = None
        try:
            connection = PinnedHTTPSConnection(self.host, address, self.timeout_seconds)
            if isinstance(body, bytes):
                payload_bytes = body
            elif body is not None:
                payload_bytes = json.dumps(body, ensure_ascii=False).encode()
            else:
                payload_bytes = None
            headers = {'Content-Type': 'application/json'} if payload_bytes else {}
            connection.request(method, path, payload_bytes, headers)
            response = connection.getresponse()
            return response.status, response.read(65536)
        finally:
            if connection is not None:
                connection.close()

    def _access_token(self, address, credentials):
        """按租户缓存 access_token（expires_in 提前 300 秒作废，过期后下次发送
        强制取新 token）；获取失败返回 None。gettoken 的 token 参数在 URL query
        （appkey/appsecret 同）——日志只含接口名与 errcode，不含 URL/凭据。"""
        cached = self._tokens.get(credentials.tenant_id)
        if cached and cached[1] > time.time():
            return cached[0]
        path = (f'/gettoken?appkey={quote(credentials.app_key, safe="")}'
                f'&appsecret={quote(credentials.app_secret, safe="")}')
        status, raw = self._call(address, 'GET', path)
        data = _json_object(raw) if status == 200 else None
        token = data.get('access_token') if data else None
        if not token:
            logger.warning(f'钉钉通知 gettoken 失败: errcode={data.get("errcode", "N/A") if data else "N/A"}')
            return None
        expires_in = data.get('expires_in')
        self._tokens[credentials.tenant_id] = (
            token,
            time.time() + (expires_in if isinstance(expires_in, int) and expires_in > 300 else 7200) - 300)
        return token

    def _invalidate_token(self, tenant_id):
        self._tokens.pop(tenant_id, None)

    def _exchange(self, address, credentials, body):
        """取 token 并发一次 asyncsend_v2；token 失效（40014）返回 retryable
        token_expired 由 send 刷新后重试一次。"""
        token = self._access_token(address, credentials)
        if not token:
            return SendResult('retryable', 'provider_authentication')
        status, raw = self._call(address, 'POST',
                                 '/topapi/message/corpconversation/asyncsend_v2?access_token='
                                 + quote(token, safe=''),
                                 body=body)
        data = _json_object(raw)
        if status in (200, 201, 202):
            errcode = data.get('errcode') if data else None
            if errcode in self.TOKEN_EXPIRED_ERRCODES:
                return SendResult('retryable', 'token_expired')
            return self._interpret(errcode, data)
        if status == 429 or status >= 500:
            return SendResult('retryable', 'rate_limited' if status == 429 else 'provider_unavailable')
        return SendResult('permanent_failure', 'provider_rejected')

    def _interpret(self, errcode, data):
        """asyncsend_v2 errcode → SendResult（2026-11-16 官方文档查证语义）；
        非零未知 errcode 按 im_webhook 兜底语义归配置类失败不重试（查不到确切
        码的统一 permanent，钉钉 userid 失效场景少，靠手机号重绑兜底）。
        asyncsend_v2 异步受理：errcode=0 即 accepted，provider_id 记 task_id
        （送达结果 getsendresult 本期不查，诚实语义）。"""
        if errcode == 0:
            task_id = (data.get('result') or {}).get('task_id') if isinstance(data, dict) else None
            return SendResult('accepted', provider_id=str(task_id) if task_id is not None else None)
        if errcode in self.TARGET_INVALID_ERRCODES:
            return SendResult('target_invalid', 'dingtalk_userid_invalid')
        if errcode in self.RATE_LIMIT_ERRCODES:
            return SendResult('retryable', 'rate_limited')
        return SendResult('permanent_failure', 'provider_rejected')

    def send(self, configuration, payload, ttl):
        """发送凭据按 target 配置里的 tenant_id 延迟解析（租户自备）；租户
        无可用凭据（配置已删/不全/缺 agent_id）按配置类失败不重试。"""
        tenant_id = str((configuration or {}).get('tenant_id') or '').strip()
        credentials = self.credentials_for_tenant(tenant_id) if tenant_id else None
        if credentials is None:
            return SendResult('permanent_failure', 'provider_configuration')
        try:
            address = public_addresses(self.host)[0]
            body = json.dumps(self._request_body(configuration, payload, credentials),
                              ensure_ascii=False).encode()
        except NotificationError:
            return SendResult('permanent_failure', 'endpoint_forbidden')
        except (KeyError, TypeError, ValueError):
            return SendResult('permanent_failure', 'provider_configuration')
        try:
            result = self._exchange(address, credentials, body)
            if result.outcome == 'retryable' and result.error_class == 'token_expired':
                # 40014：access_token 失效，强制刷新后重试一次
                self._invalidate_token(credentials.tenant_id)
                result = self._exchange(address, credentials, body)
            return result
        except NotificationError:
            return SendResult('permanent_failure', 'endpoint_forbidden')
        except (OSError, TimeoutError, http.client.HTTPException):
            return SendResult('retryable', 'transport_unavailable')
        except Exception:
            return SendResult('permanent_failure', 'provider_configuration')
