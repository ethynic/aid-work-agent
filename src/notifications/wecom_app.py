"""企微应用消息通知 Adapter（长任务企微应用通知 Phase 3，设计 §4.3）。

channel=provider='wecom_app'：target 由绑定成功后服务端登记（不走用户提交的
RegisterTarget API），发送走独立通知应用（corp_id+agentid+secret，配置节
notifications.wecom_app）的 textcard 应用消息，touser 为单个 wecom_userid。

- 出站复用 web_push 的 PinnedHTTPSConnection/public_addresses：域名白名单固定
  qyapi.weixin.qq.com + 公网地址解析校验，无重定向、无环境代理；
- access_token 实例内缓存（expires_in 提前 300 秒作废），message/send 返回
  40014/42001 时强制刷新并重试一次；
- errcode 分类（设计 §4.3）：0→accepted；40031/43004/81013（userid 不存在/
  无权限/不在可见范围）→target_invalid（由 repository.finish 撤销目标并标记
  绑定失效，引导重新绑定）；42009/45009→retryable 限流；网络/HTTP 5xx→
  retryable 有限重试；其余 errcode→permanent_failure（沿 im_webhook 兜底语义）。
- 文案：title={会话名}相关任务已完成/执行失败；description=任务已执行 {时长}，
  点击查看结果（失败用「已执行 X 后失败」口径；时长取事件参数
  duration_seconds，由触发判定随终态事件带入，缺省/0=未知时旧文案兜底）；
  url={public_base_url}/notification?ref={notification_ref}（ref 解析端点
  Phase 4 接，url 语义与 web push 设计 §5 的 notification_ref 一致）。
安全：secret/access_token 仅出现在请求路径，日志只含接口名与 errcode。
"""
import hashlib
import http.client
import json
import re
import time
from urllib.parse import quote

from loguru import logger

from .contracts import AdapterCapabilities, NotificationError, SendResult
from .web_push import PinnedHTTPSConnection, public_addresses

# 企微 textcard 字段字节上限（企业微信 message/send 协议限制）
_TEXTCARD_TITLE_BYTES = 128
_TEXTCARD_DESCRIPTION_BYTES = 512

# userid 服务端来源（OAuth/手机号核实）可信，仅做形态边界校验：非空、
# 无空白与控制字符、长度 ≤64（企微 userid 上限）
_USERID_PATTERN = re.compile(r'[^\s\x00-\x1f]{1,64}', re.ASCII)


def _json_object(raw):
    try:
        data = json.loads(raw or b'{}')
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _clip(text, limit):
    """utf-8 字节上限内按字符边界截断（避免企微服务端按字节截断撕裂字符）。"""
    raw = text.encode('utf-8')
    if len(raw) <= limit:
        return text
    return raw[:limit].decode('utf-8', errors='ignore')


def _duration_label(seconds):
    """秒 → 人类可读时长（5分32秒 / 1小时 / 1小时1分1秒）；非正整数（缺省/
    0=时长未知/类型异常）返回空串由调用方走旧文案兜底。"""
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


class WecomAppAdapter:
    channel = provider = 'wecom_app'
    capabilities = AdapterCapabilities(payload_limit=2048)
    host = 'qyapi.weixin.qq.com'
    # errcode 分类（设计 §4.3 严格映射）
    TOKEN_EXPIRED_ERRCODES = (40014, 42001)
    TARGET_INVALID_ERRCODES = (40031, 43004, 81013)
    RATE_LIMIT_ERRCODES = (42009, 45009)

    def __init__(self, config, timeout_seconds=10):
        self.config = config
        self.timeout_seconds = timeout_seconds
        self._secret = config.secret.get_secret_value()
        self._token = None
        self._token_expires_at = 0.0

    def available(self):
        """enabled + 完整凭据（corp_id/agentid/secret）才可用；不可用时
        repository/expand 层按既有 available gate 不投递。"""
        config = self.config
        return bool(config.enabled and config.corp_id and config.agentid
                    and self._secret)

    def validate_target(self, configuration):
        from .contracts import WecomAppConfiguration
        dto = WecomAppConfiguration.model_validate(configuration)
        if not _USERID_PATTERN.fullmatch(dto.wecom_userid):
            raise NotificationError('NOTIFICATION_INVALID_WECOM_USERID')
        # endpoint 即 wecom_userid（通知应用全局唯一），sha256 摘要与
        # im_webhook/web_push 保持同一唯一性口径
        return hashlib.sha256(dto.wecom_userid.encode()).hexdigest()

    def render(self, message, target):
        parameters = message.get('parameters') if isinstance(message.get('parameters'), dict) else {}
        title, description = self._textcard_text(message.get('event_type'), parameters)
        return {'schema_version': 1, 'notification_ref': message['notification_ref'],
                'title': _clip(title, _TEXTCARD_TITLE_BYTES),
                'description': _clip(description, _TEXTCARD_DESCRIPTION_BYTES),
                'url': self._notification_url(message), 'btntxt': '点击查看'}

    @staticmethod
    def _textcard_text(event_type, parameters):
        """textcard 文案（设计 §4.3）：description=任务已执行 {时长}，点击查看
        结果（失败事件用「已执行 X 后失败」口径）。会话名快照沿用事件参数
        （生产者已按 contracts.session_title 清洗），Adapter 不查库；时长取
        事件参数 duration_seconds（触发判定带入，0/缺省=未知）走旧文案兜底。
        非终态任务事件沿用既有事件渲染口径给动作提示。"""
        if event_type == 'notification.test':
            return '通知渠道测试', '收到即表示企微应用通知链路有效。'
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
        if event_type == 'task.waiting_input':
            question = parameters.get('question_title') or '新的提问'
            return f'{session}有新提问等待回答', f'{question}，点击前往处理。'
        name = parameters.get('task_name') or session
        if event_type == 'task.paused':
            return f'定时任务「{name}」已暂停', f'{parameters.get("reason_label") or "需要处理后恢复"}，点击查看。'
        if event_type == 'task.needs_action':
            return f'定时任务「{name}」需要处理', f'{parameters.get("action_label") or "需要人工处理"}，点击前往。'
        if event_type == 'task.manual_rejected':
            return f'「{name}」手动执行未成功', f'{parameters.get("reason_label") or "任务保持原状态"}，点击查看。'
        raise ValueError('NOTIFICATION_EVENT_UNSUPPORTED')

    @staticmethod
    def _notification_url(message):
        """点击回跳（设计 §4.4）：{public_base_url}/notification?ref={ref}；
        ref 解析端点 Phase 4 接。未配置公网域名时无可用落地页，渲染失败留痕。"""
        from src.config.settings import settings
        base = (getattr(settings.app, 'public_base_url', '') or '').strip().rstrip('/')
        ref = str(message.get('notification_ref') or '')
        if not base or not ref:
            raise ValueError('NOTIFICATION_PUBLIC_URL_REQUIRED')
        return base + '/notification?ref=' + quote(ref, safe='')

    def _request_body(self, configuration, payload):
        """message/send textcard 组装：touser 单 userid（设计决策 1，一对一）。"""
        return {'touser': configuration['wecom_userid'], 'msgtype': 'textcard',
                'agentid': self.config.agentid,
                'textcard': {'title': payload['title'], 'description': payload['description'],
                             'url': payload['url'], 'btntxt': payload.get('btntxt') or '点击查看'}}

    def _call(self, address, method, path, body=None):
        """body 接受已编码 bytes（message/send 路径）或可序列化对象。"""
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

    def _access_token(self, address):
        """缓存 access_token（expires_in 提前 300 秒作废）；获取失败返回 None。
        日志只含接口名与 errcode，不含 corpsecret/access_token。"""
        if self._token and time.time() < self._token_expires_at:
            return self._token
        path = (f'/cgi-bin/gettoken?corpid={quote(self.config.corp_id, safe="")}'
                f'&corpsecret={quote(self._secret, safe="")}')
        status, raw = self._call(address, 'GET', path)
        data = _json_object(raw) if status == 200 else None
        token = data.get('access_token') if data else None
        if not token:
            logger.warning(f'企微通知应用 gettoken 失败: errcode={data.get("errcode", "N/A") if data else "N/A"}')
            return None
        expires_in = data.get('expires_in')
        self._token = token
        self._token_expires_at = time.time() + (expires_in if isinstance(expires_in, int) and expires_in > 300 else 7200) - 300
        return token

    def _invalidate_token(self):
        self._token = None
        self._token_expires_at = 0.0

    def _exchange(self, address, body):
        """取 token 并发一次 message/send；token 过期返回 retryable token_expired
        由 send 刷新后重试一次。"""
        token = self._access_token(address)
        if not token:
            return SendResult('retryable', 'provider_authentication')
        status, raw = self._call(address, 'POST',
                                 '/cgi-bin/message/send?access_token=' + quote(token, safe=''),
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
        """message/send errcode → SendResult（设计 §4.3）；非零未知 errcode 按
        im_webhook 兜底语义归配置类失败不重试。"""
        if errcode == 0:
            msgid = data.get('msgid') if data else None
            return SendResult('accepted', provider_id=str(msgid) if msgid is not None else None)
        if errcode in self.TARGET_INVALID_ERRCODES:
            return SendResult('target_invalid', 'wecom_userid_invalid')
        if errcode in self.RATE_LIMIT_ERRCODES:
            return SendResult('retryable', 'rate_limited')
        return SendResult('permanent_failure', 'provider_rejected')

    def send(self, configuration, payload, ttl):
        try:
            address = public_addresses(self.host)[0]
            body = json.dumps(self._request_body(configuration, payload),
                              ensure_ascii=False).encode()
        except NotificationError:
            return SendResult('permanent_failure', 'endpoint_forbidden')
        except (KeyError, TypeError, ValueError):
            return SendResult('permanent_failure', 'provider_configuration')
        try:
            result = self._exchange(address, body)
            if result.outcome == 'retryable' and result.error_class == 'token_expired':
                # 40014/42001：token 失效，强制刷新后重试一次（设计 §4.3）
                self._invalidate_token()
                result = self._exchange(address, body)
            return result
        except NotificationError:
            return SendResult('permanent_failure', 'endpoint_forbidden')
        except (OSError, TimeoutError, http.client.HTTPException):
            return SendResult('retryable', 'transport_unavailable')
        except Exception:
            return SendResult('permanent_failure', 'provider_configuration')
