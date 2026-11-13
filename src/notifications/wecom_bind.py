"""企微应用身份绑定（长任务企微应用通知 Phase 1，设计 §4.1）。

- OAuth 主路径：/wecom/bind/url 生成授权链接（state 入 Redis 防 CSRF，TTL 5 分钟，
  绑定发起用户、回调一次性消费）→ /wecom/bind/callback 用 code 换 userid → 可见范围校验 → 落库。
- 手机号兜底：/wecom/bind/suggest 只读预填；/wecom/bind/confirm 服务端按登录用户
  手机号重新核实后才落库，不信任前端传的 userid。
- 解绑：DELETE /wecom/bind 清空 wecom_userid 并撤销既有 wecom_app target。

出站走 pinned HTTPS（域名白名单 + 公网地址校验，复用 web_push 防护），固定
qyapi.weixin.qq.com；secret/access_token 不落日志。target 登记（绑定成功后注册
wecom_app target）由 Phase 3 接线，本模块只提供解绑时的撤销。
"""
import http.client
import json
import re
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from loguru import logger

from src.core.cache_utils import CacheKeys, get_cached, set_cached, delete_cached
from src.notifications.contracts import NotificationError, Subject, WecomBindConfirm
from src.notifications.service import notification_repository
from src.notifications.web_push import PinnedHTTPSConnection, public_addresses

WECOM_API_HOST = 'qyapi.weixin.qq.com'
# 企业微信成员 status：1=已激活；禁用(2)/未激活(4)等均视为不可通知
WECOM_MEMBER_STATUS_ACTIVE = 1
# OAuth 授权链接（设计已定）：企业内浏览器与 PC 扫码两个形态
_OAUTH_AUTHORIZE_URL = 'https://open.weixin.qq.com/connect/oauth2/authorize'
_OAUTH_QR_URL = 'https://open.weixin.qq.com/wwopen/sso/qrConnect'
_STATE_PATTERN = re.compile(r'[0-9a-f]{32}')


class BindStateStore:
    """OAuth state 存取（CacheKeys.WECOM_BIND_STATE）：随机值 + TTL + 一次性消费。"""

    def __init__(self, ttl_seconds=300):
        self.ttl_seconds = ttl_seconds

    def issue(self, user_id):
        """签发 state 并绑定发起用户：state 值存 user_id，回调时校验同一登录用户。"""
        state = uuid.uuid4().hex
        set_cached(CacheKeys.WECOM_BIND_STATE, state, value=user_id, ttl=self.ttl_seconds)
        return state

    def consume(self, state, user_id):
        """校验并一次性消费；非法格式/不存在/已消费/过期/非发起用户均拒绝。

        用户不匹配时视为无效但不删除键：state 仍归发起用户，本人可继续完成绑定。
        """
        if not state or not _STATE_PATTERN.fullmatch(state):
            return False
        stored = get_cached(CacheKeys.WECOM_BIND_STATE, state)
        if stored is None or stored != user_id:
            return False
        delete_cached(CacheKeys.WECOM_BIND_STATE, state)
        return True


class WecomClient:
    """企微通知应用 API 封装（gettoken / OAuth 换 userid / 通讯录读成员 / 手机号查 userid）。

    通知应用独立 corp secret（config notifications.wecom_app），不复用渠道应用凭据。
    网络/HTTP/解析失败与 errcode 非零统一返回 None，由路由映射为业务错误码；
    日志只含接口名与 errcode，不含 secret/access_token。
    """

    def __init__(self, config, timeout_seconds=10):
        self.corp_id = config.corp_id
        self.agentid = config.agentid
        self._secret = config.secret.get_secret_value()
        self.timeout_seconds = timeout_seconds

    def _call(self, name, method, path, body=None):
        connection = None
        try:
            address = public_addresses(WECOM_API_HOST)[0]
            connection = PinnedHTTPSConnection(WECOM_API_HOST, address, self.timeout_seconds)
            payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
            headers = {'Content-Type': 'application/json'} if payload else {}
            connection.request(method, path, payload, headers)
            response = connection.getresponse()
            raw = response.read(65536)
            if response.status != 200:
                logger.warning(f'企微通知应用接口 HTTP {response.status}: {name}')
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except (OSError, TimeoutError, http.client.HTTPException, ValueError):
            logger.opt(exception=True).error(f'企微通知应用接口调用失败: {name}')
            return None
        finally:
            if connection is not None:
                connection.close()

    def _token(self):
        path = f'/cgi-bin/gettoken?corpid={quote(self.corp_id)}&corpsecret={quote(self._secret)}'
        data = self._call('gettoken', 'GET', path)
        if not data or data.get('errcode') != 0:
            errcode = data.get('errcode') if data else 'N/A'
            logger.warning(f'企微通知应用 gettoken 失败: errcode={errcode}')
            return None
        return data.get('access_token')

    def oauth_userid(self, code):
        """OAuth code 换成员 userid；仅返回 UnionId（成员不在通知应用可见范围）视为失败。"""
        token = self._token()
        if not token:
            return None
        data = self._call('auth/getuserinfo', 'GET',
                          f'/cgi-bin/auth/getuserinfo?access_token={quote(token)}&code={quote(code)}')
        if not data or data.get('errcode') != 0:
            errcode = data.get('errcode') if data else 'N/A'
            logger.warning(f'企微通知应用 getuserinfo 失败: errcode={errcode}')
            return None
        return data.get('userid') or None

    def member(self, userid):
        """读成员（可见范围/在职校验用）；errcode 非零返回 None。"""
        token = self._token()
        if not token:
            return None
        data = self._call('user/get', 'GET',
                          f'/cgi-bin/user/get?access_token={quote(token)}&userid={quote(userid)}')
        if not data or data.get('errcode') != 0:
            errcode = data.get('errcode') if data else 'N/A'
            logger.warning(f'企微通知应用 user/get 失败: errcode={errcode}')
            return None
        return data

    def userid_by_phone(self, mobile):
        """按手机号查成员 userid（user/get_userid_by_phone，仅查不写）。"""
        token = self._token()
        if not token:
            return None
        data = self._call('user/get_userid_by_phone', 'POST',
                          f'/cgi-bin/user/get_userid_by_phone?access_token={quote(token)}',
                          body={'mobile': mobile})
        if not data or data.get('errcode') != 0:
            errcode = data.get('errcode') if data else 'N/A'
            logger.warning(f'企微通知应用 user/get_userid_by_phone 失败: errcode={errcode}')
            return None
        return data.get('userid') or None


def create_bind_router(config=None, repository=None, user_loader=None,
                       state_store=None, wecom_client=None):
    """企微绑定路由（无前缀，由 notifications.api.create_router 挂到 /api/notifications 下）。"""
    from .api import NotificationRoute, resolve_subject
    from src.db.models import UserDB

    if config is None:
        from src.config.settings import settings
        config = settings.notifications
    repository = repository or notification_repository(config)
    if user_loader is None:
        from src.services.auth_service import fresh_web_subject
        user_loader = fresh_web_subject
    state_store = state_store or BindStateStore()
    wecom_client = wecom_client or WecomClient(config.wecom_app, config.timeout_seconds)

    router = APIRouter(route_class=NotificationRoute)

    def subject(request: Request):
        return resolve_subject(user_loader, request)

    def _require_app():
        """通知应用未启用/凭据不全时拒绝绑定相关操作。"""
        app_config = config.wecom_app
        if (not app_config.enabled or not app_config.corp_id
                or not app_config.secret.get_secret_value()):
            raise NotificationError('NOTIFICATION_WECOM_APP_UNAVAILABLE', 503)
        return app_config

    def _redirect_uri(app_config):
        from src.config.settings import settings
        if app_config.redirect_uri:
            return app_config.redirect_uri
        base = settings.app.public_base_url
        return base.rstrip('/') + '/notification/wecom-bind' if base else ''

    def _current_phone(current):
        row = UserDB.get_by_id(current.user_id)
        phone = (row or {}).get('phone')
        if not phone:
            raise NotificationError('NOTIFICATION_BIND_PHONE_REQUIRED')
        return phone

    def _bind(current, userid):
        """可见范围校验 + 租户内唯一性校验 + 落库，返回成员姓名。"""
        member = wecom_client.member(userid)
        if not member or member.get('status') != WECOM_MEMBER_STATUS_ACTIVE:
            raise NotificationError('NOTIFICATION_BIND_NOT_IN_SCOPE')
        existing = UserDB.get_by_wecom_userid_in_tenant(userid, current.tenant_id)
        if existing and existing['user_id'] != current.user_id:
            raise NotificationError('NOTIFICATION_BIND_TAKEN', 409)
        UserDB.set_wecom_userid(current.user_id, userid)
        return member.get('name') or ''

    @router.get('/wecom/bind/url')
    def wecom_bind_url(current: Subject = Depends(subject)):
        app_config = _require_app()
        redirect = _redirect_uri(app_config)
        if not redirect:
            raise NotificationError('NOTIFICATION_WECOM_APP_UNAVAILABLE', 503)
        state = state_store.issue(current.user_id)
        encoded_redirect = quote(redirect, safe='')
        oauth_url = (f'{_OAUTH_AUTHORIZE_URL}?appid={quote(app_config.corp_id)}'
                     f'&redirect_uri={encoded_redirect}&response_type=code'
                     f'&scope=snsapi_base&state={state}#wechat_redirect')
        qr_url = (f'{_OAUTH_QR_URL}?appid={quote(app_config.corp_id)}'
                  f'&agentid={app_config.agentid}&redirect_uri={encoded_redirect}&state={state}')
        return {'success': True, 'state': state, 'oauth_url': oauth_url, 'qr_url': qr_url}

    @router.get('/wecom/bind/callback')
    def wecom_bind_callback(request: Request, current: Subject = Depends(subject)):
        _require_app()
        # state 绑定发起用户：与当前登录用户不一致按无效 state 拒绝
        if not state_store.consume(request.query_params.get('state', ''), current.user_id):
            raise NotificationError('NOTIFICATION_BIND_STATE_INVALID')
        userid = wecom_client.oauth_userid(request.query_params.get('code', ''))
        if not userid:
            raise NotificationError('NOTIFICATION_BIND_FAILED')
        return {'success': True, 'userid': userid, 'name': _bind(current, userid)}

    @router.get('/wecom/bind/suggest')
    def wecom_bind_suggest(current: Subject = Depends(subject)):
        _require_app()
        userid = wecom_client.userid_by_phone(_current_phone(current))
        if not userid:
            return {'matched': False}
        member = wecom_client.member(userid)
        return {'matched': True, 'userid': userid, 'name': (member or {}).get('name') or ''}

    @router.post('/wecom/bind/confirm')
    def wecom_bind_confirm(body: WecomBindConfirm, current: Subject = Depends(subject)):
        _require_app()
        # 服务端以登录用户手机号重查核实，不信任前端传的 userid
        server_userid = wecom_client.userid_by_phone(_current_phone(current))
        if not server_userid or server_userid != body.userid:
            raise NotificationError('NOTIFICATION_BIND_MISMATCH')
        return {'success': True, 'userid': server_userid, 'name': _bind(current, server_userid)}

    @router.delete('/wecom/bind')
    def wecom_bind_unbind(current: Subject = Depends(subject)):
        repository.assert_schema()
        # 撤销既有 wecom_app target（Phase 3 登记的投递目标；无 target 时跳过）
        for target in repository.targets(current):
            if target.get('channel') == 'wecom_app' and target.get('state') == 'active':
                repository.revoke_target(current, target['target_id'], target['binding_version'])
        UserDB.set_wecom_userid(current.user_id, None)
        return {'success': True}

    return router
