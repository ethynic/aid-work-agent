"""钉钉应用身份绑定（钉钉任务通知接入，2026-11-16，设计决策 7 追加；
完全复刻飞书绑定的手机号识别模式，本期无 OAuth）。

- 手机号识别：/dingtalk/bind/suggest 只读——服务端按登录用户手机号调
  topapi/v2/user/getbymobile 查 userid，命中返回 matched:true+userid
  （getbymobile 不返回姓名，name 恒空串），未命中/离职 errcode 非 0（如
  60104）视为未命中 matched:false；不落库。
- 确认绑定：/dingtalk/bind/confirm 服务端按登录用户手机号重新 getbymobile
  核实后才落库 users.dingtalk_userid，不信任前端传的 userid；命中即隐含成员
  在企业内（userid 由 getbymobile 按手机号返回）。
- 解绑：DELETE /dingtalk/bind 清空 dingtalk_userid 并撤销既有 dingtalk_app
  target。

通知凭据租户自备：绑定相关端点按 subject 租户解析连接中心钉钉渠道配置
（channel_type=dingtalk 且 notify_usage ∈ {both, notify_only}，
app_key/app_secret/agent_id），未配置租户 503。出站走 pinned HTTPS（域名
白名单 + 公网地址校验，复用 web_push 防护），固定 oapi.dingtalk.com；token
在 URL query——日志只含接口名与 errcode，不含完整 URL/app_secret/access_token。
target 登记：绑定落库成功后由服务端登记 channel='dingtalk_app' target，幂等
upsert；登记失败不影响绑定落库本身，结果在响应与日志可见。
"""
import http.client
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from loguru import logger

from src.notifications.contracts import (DingtalkAppTargetRegistration, DingtalkBindConfirm,
                                         NotificationError, Subject)
from src.notifications.service import notification_repository
from src.notifications.web_push import PinnedHTTPSConnection, public_addresses

DINGTALK_API_HOST = 'oapi.dingtalk.com'


class DingtalkClient:
    """钉钉开放平台 API 封装（access_token / 手机号查 userid）。

    通知凭据租户自备（连接中心钉钉渠道配置 notify_usage ∈ {both, notify_only}）。
    网络/HTTP/解析失败与 errcode 非零统一返回 None，由路由映射为业务错误码；
    token 在 URL query——日志只含接口名与 errcode，不含完整 URL/app_secret/
    access_token。
    """

    def __init__(self, credentials, timeout_seconds=10):
        self._app_key = credentials.app_key
        self._app_secret = credentials.app_secret
        self.timeout_seconds = timeout_seconds

    def _call(self, name, method, path, body=None):
        connection = None
        try:
            address = public_addresses(DINGTALK_API_HOST)[0]
            connection = PinnedHTTPSConnection(DINGTALK_API_HOST, address, self.timeout_seconds)
            payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
            headers = {'Content-Type': 'application/json'} if payload else {}
            connection.request(method, path, payload, headers)
            response = connection.getresponse()
            raw = response.read(65536)
            if response.status != 200:
                logger.warning(f'钉钉通知接口 HTTP {response.status}: {name}')
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except (OSError, TimeoutError, http.client.HTTPException, ValueError):
            logger.opt(exception=True).error(f'钉钉通知接口调用失败: {name}')
            return None
        finally:
            if connection is not None:
                connection.close()

    def _token(self):
        """访问令牌（token/appkey/appsecret 均在 URL query，官方文档 2026-11-16；
        appkey/appsecret URL 编码防特殊字符破坏 query，与 DingTalkAppAdapter 同口径；
        日志只含接口名与 errcode，不含 URL）。"""
        data = self._call('gettoken', 'GET',
                          f'/gettoken?appkey={quote(self._app_key, safe="")}'
                          f'&appsecret={quote(self._app_secret, safe="")}')
        if not data or data.get('errcode') != 0:
            errcode = data.get('errcode') if data else 'N/A'
            logger.warning(f'钉钉通知 gettoken 失败: errcode={errcode}')
            return None
        return data.get('access_token') or None

    def userid_by_mobile(self, mobile):
        """按手机号查钉钉 userid（官方 topapi/v2/user/getbymobile，token 在
        query，仅查不写）。未命中/离职/不在范围 errcode 非 0（如 60104）统一
        返回 None；命中返回 result.userid。"""
        token = self._token()
        if not token:
            return None
        data = self._call('topapi/v2/user/getbymobile', 'POST',
                          '/topapi/v2/user/getbymobile?access_token=' + quote(token, safe=''),
                          body={'mobile': mobile})
        if not data or data.get('errcode') != 0:
            errcode = data.get('errcode') if data else 'N/A'
            logger.warning(f'钉钉通知手机号查用户失败: errcode={errcode}')
            return None
        return ((data.get('result') or {}).get('userid')) or None


def create_dingtalk_bind_router(config=None, repository=None, user_loader=None,
                                dingtalk_client=None, credentials_source=None):
    """钉钉绑定路由（无前缀，由 notifications.api.create_router 挂到 /api/notifications 下）。

    credentials_source：tenant_id → 通知凭据（DingtalkAppCredentials）或 None。
    缺省经 repository.registry 的 DingTalkAppAdapter.credentials_for_tenant 解析
    （租户钉钉渠道配置）；dingtalk_client 注入（测试）时仍以凭据解析结果作为
    可用性 gate。
    """
    from .api import NotificationRoute, resolve_subject
    from src.db.models import UserDB

    if config is None:
        from src.config.settings import settings
        config = settings.notifications
    repository = repository or notification_repository(config)
    if user_loader is None:
        from src.services.auth_service import fresh_web_subject
        user_loader = fresh_web_subject

    router = APIRouter(route_class=NotificationRoute)

    def subject(request: Request):
        return resolve_subject(user_loader, request)

    def _credentials_for(tenant_id):
        """按 subject 租户解析通知凭据；未配置/凭据不全返回 None。"""
        if credentials_source is not None:
            return credentials_source(tenant_id)
        adapter = repository.registry.get('dingtalk_app', 'dingtalk_app')
        resolver = getattr(adapter, 'credentials_for_tenant', None)
        return resolver(tenant_id) if resolver else None

    def _require_app(current):
        """当前租户未配置通知用途的钉钉应用（或凭据不全/缺 agent_id）时拒绝绑定相关操作。"""
        credentials = _credentials_for(current.tenant_id)
        if not credentials:
            raise NotificationError('NOTIFICATION_DINGTALK_APP_UNAVAILABLE', 503)
        return credentials

    def _dingtalk_client(current):
        """按租户凭据构造钉钉 API 客户端；测试可注入固定 dingtalk_client。"""
        credentials = _require_app(current)
        return dingtalk_client if dingtalk_client is not None else DingtalkClient(
            credentials, config.timeout_seconds)

    def _current_phone(current):
        row = UserDB.get_by_id(current.user_id)
        phone = (row or {}).get('phone')
        if not phone:
            raise NotificationError('NOTIFICATION_BIND_PHONE_REQUIRED')
        return phone

    def _bind(current, userid):
        """租户内唯一性校验 + 落库，返回姓名快照（getbymobile 不返回姓名，
        本期留空）。"""
        existing = UserDB.get_by_dingtalk_userid_in_tenant(userid, current.tenant_id)
        if existing and existing['user_id'] != current.user_id:
            raise NotificationError('NOTIFICATION_DINGTALK_BIND_TAKEN', 409)
        UserDB.set_dingtalk_userid(current.user_id, userid)
        return ''

    def _register_target(current, userid, name):
        """绑定落库成功后登记 dingtalk_app 投递目标（对齐飞书绑定登记接线）。

        幂等 upsert：复用 repository.register_target 的归属/幂等语义——同用户
        已有 target 时回传当前 binding_version 命中更新分支（重复绑定不变更
        版本），解绑后重绑复活 revoked target；installation_ref 按用户确定
        生成，endpoint 摘要 sha256(dingtalk_userid) 由 adapter.validate_target
        产出保持唯一性口径。"""
        rows = [item for item in repository.targets(current)
                if item.get('channel') == 'dingtalk_app']
        # 与飞书同口径：existing 优先 active 行回传版本，避免 stale revoked 行
        # 触发 BINDING_CHANGED
        existing = next((item for item in rows if item.get('state') == 'active'),
                        rows[0] if rows else None)
        body = DingtalkAppTargetRegistration(
            installation_ref='dingtalk_bind_' + str(current.user_id),
            expected_binding_version=existing['binding_version'] if existing else None,
            configuration={'dingtalk_userid': userid, 'bound_user_name': name,
                           'tenant_id': current.tenant_id or ''})
        # identity_preverified=True 标记本登记由服务端已核验钉钉身份（confirm
        # 路径 getbymobile 按登录用户手机号重新核实）的绑定流程发起，repository
        # 据此对接手/复活非 active 行豁免版本钉扎；Web Push 用户 API 不带此标记
        return repository.register_target(current, body, identity_preverified=True)

    def _bind_and_register(current, userid):
        """落库 + target 登记：登记失败不影响绑定落库本身，结果在响应
        （target_registered 字段）与日志可见。"""
        name = _bind(current, userid)
        registered = True
        try:
            _register_target(current, userid, name)
        except Exception:
            registered = False
            logger.opt(exception=True).warning(
                '钉钉绑定成功但通知 target 登记失败 user_id={}', current.user_id)
        return {'success': True, 'userid': userid, 'name': name,
                'target_registered': registered}

    @router.get('/dingtalk/bind/status')
    def dingtalk_bind_status(current: Subject = Depends(subject)):
        """绑定状态（对齐飞书设置页）：应用可用性（按当前 subject 租户的渠道
        配置）+ 绑定态 + 姓名快照 + target 状态。不发钉钉出站请求，直接读本地
        事实。"""
        repository.assert_schema()
        row = UserDB.get_by_id(current.user_id) or {}
        bound = bool(row.get('dingtalk_userid'))
        profile = (repository.dingtalk_bind_profile(current) if bound
                   else {'target_state': None, 'name': None})
        return {'app_available': _credentials_for(current.tenant_id) is not None,
                'bound': bound, 'name': profile.get('name'),
                'target_state': profile.get('target_state')}

    @router.get('/dingtalk/bind/suggest')
    def dingtalk_bind_suggest(current: Subject = Depends(subject)):
        """手机号识别（只读）：登录用户手机号 → getbymobile 查 userid；
        命中 matched:true（getbymobile 不返回姓名，name 恒空），未命中
        matched:false。不落库。"""
        client = _dingtalk_client(current)
        userid = client.userid_by_mobile(_current_phone(current))
        if not userid:
            return {'matched': False}
        return {'matched': True, 'userid': userid, 'name': ''}

    @router.post('/dingtalk/bind/confirm')
    def dingtalk_bind_confirm(body: DingtalkBindConfirm, current: Subject = Depends(subject)):
        client = _dingtalk_client(current)
        # 服务端以登录用户手机号重查核实，不信任前端传的 userid
        server_userid = client.userid_by_mobile(_current_phone(current))
        if not server_userid or server_userid != body.userid:
            raise NotificationError('NOTIFICATION_DINGTALK_BIND_MISMATCH')
        return _bind_and_register(current, server_userid)

    @router.delete('/dingtalk/bind')
    def dingtalk_bind_unbind(current: Subject = Depends(subject)):
        repository.assert_schema()
        # 撤销既有 dingtalk_app target（登记的投递目标；无 target 时跳过）
        for target in repository.targets(current):
            if target.get('channel') == 'dingtalk_app' and target.get('state') == 'active':
                repository.revoke_target(current, target['target_id'], target['binding_version'])
        UserDB.set_dingtalk_userid(current.user_id, None)
        return {'success': True}

    return router
