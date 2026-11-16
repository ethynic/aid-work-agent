"""飞书应用身份绑定（飞书任务通知接入，2026-11-15，设计决策 7 追加；
完全复刻企微绑定的手机号识别模式，本期无 OAuth）。

- 手机号识别：/feishu/bind/suggest 只读——服务端按登录用户手机号调
  contact/v3/users/batch_get_id（user_id_type=open_id）查 open_id，命中返回
  matched:true+open_id（batch_get_id 不返回姓名，name 恒空串），未命中
  matched:false；不落库。
- 确认绑定：/feishu/bind/confirm 服务端按登录用户手机号重新 batch_get_id
  核实后才落库 users.feishu_open_id，不信任前端传的 open_id；命中即隐含成员
  在应用可用范围（open_id 由 batch_get_id 按手机号返回）。
- 解绑：DELETE /feishu/bind 清空 feishu_open_id 并撤销既有 feishu_app target。

通知凭据租户自备：绑定相关端点按 subject 租户解析连接中心飞书渠道配置
（channel_type=feishu 且 notify_usage ∈ {both, notify_only}，app_id/app_secret），
未配置租户 503。出站走 pinned HTTPS（域名白名单 + 公网地址校验，复用 web_push
防护），固定 open.feishu.cn；app_secret/tenant_access_token 不落日志。target
登记：绑定落库成功后由服务端登记 channel='feishu_app' target，幂等 upsert；
登记失败不影响绑定落库本身，结果在响应与日志可见。
"""
import http.client
import json

from fastapi import APIRouter, Depends, Request
from loguru import logger

from src.notifications.contracts import (FeishuAppTargetRegistration, FeishuBindConfirm,
                                         NotificationError, Subject)
from src.notifications.service import notification_repository
from src.notifications.web_push import PinnedHTTPSConnection, public_addresses

FEISHU_API_HOST = 'open.feishu.cn'


class FeishuClient:
    """飞书开放平台 API 封装（tenant_access_token / 手机号批量查 open_id）。

    通知凭据租户自备（连接中心飞书渠道配置 notify_usage ∈ {both, notify_only}）。
    网络/HTTP/解析失败与 code 非零统一返回 None，由路由映射为业务错误码；日志
    只含接口名与 code，不含 app_secret/tenant_access_token。
    """

    def __init__(self, credentials, timeout_seconds=10):
        self._app_id = credentials.app_id
        self._app_secret = credentials.app_secret
        self.timeout_seconds = timeout_seconds

    def _call(self, name, method, path, body=None, token=None):
        connection = None
        try:
            address = public_addresses(FEISHU_API_HOST)[0]
            connection = PinnedHTTPSConnection(FEISHU_API_HOST, address, self.timeout_seconds)
            payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
            headers = {'Content-Type': 'application/json'} if payload else {}
            if token:
                headers['Authorization'] = 'Bearer ' + token
            connection.request(method, path, payload, headers)
            response = connection.getresponse()
            raw = response.read(65536)
            if response.status != 200:
                logger.warning(f'飞书通知接口 HTTP {response.status}: {name}')
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except (OSError, TimeoutError, http.client.HTTPException, ValueError):
            logger.opt(exception=True).error(f'飞书通知接口调用失败: {name}')
            return None
        finally:
            if connection is not None:
                connection.close()

    def _token(self):
        """租户访问令牌（token 在 body 参数而非 Basic 认证，官方文档 2026-11-15）。"""
        data = self._call('tenant_access_token', 'POST',
                          '/open-apis/auth/v3/tenant_access_token/internal',
                          body={'app_id': self._app_id, 'app_secret': self._app_secret})
        if not data or data.get('code') != 0:
            code = data.get('code') if data else 'N/A'
            logger.warning(f'飞书通知 tenant_access_token 获取失败: code={code}')
            return None
        return data.get('tenant_access_token') or None

    def open_id_by_phone(self, mobile):
        """按手机号查飞书 open_id（官方 contact/v3/users/batch_get_id，
        user_id_type=open_id，仅查不写）。未命中时该 mobile 对应项无 user_id
        字段（不是错误码），统一返回 None；命中返回 user_id（open_id）。"""
        token = self._token()
        if not token:
            return None
        data = self._call('contact/users/batch_get_id', 'POST',
                          '/open-apis/contact/v3/users/batch_get_id?user_id_type=open_id',
                          body={'mobiles': [mobile]}, token=token)
        if not data or data.get('code') != 0:
            code = data.get('code') if data else 'N/A'
            logger.warning(f'飞书通知手机号查用户失败: code={code}')
            return None
        for item in (data.get('data') or {}).get('user_list') or []:
            if item.get('user_id'):
                return item['user_id']
        return None


def create_feishu_bind_router(config=None, repository=None, user_loader=None,
                              feishu_client=None, credentials_source=None):
    """飞书绑定路由（无前缀，由 notifications.api.create_router 挂到 /api/notifications 下）。

    credentials_source：tenant_id → 通知凭据（FeishuAppCredentials）或 None。
    缺省经 repository.registry 的 FeishuAppAdapter.credentials_for_tenant 解析
    （租户飞书渠道配置）；feishu_client 注入（测试）时仍以凭据解析结果作为
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
        adapter = repository.registry.get('feishu_app', 'feishu_app')
        resolver = getattr(adapter, 'credentials_for_tenant', None)
        return resolver(tenant_id) if resolver else None

    def _require_app(current):
        """当前租户未配置通知用途的飞书应用（或凭据不全）时拒绝绑定相关操作。"""
        credentials = _credentials_for(current.tenant_id)
        if not credentials:
            raise NotificationError('NOTIFICATION_FEISHU_APP_UNAVAILABLE', 503)
        return credentials

    def _feishu_client(current):
        """按租户凭据构造飞书 API 客户端；测试可注入固定 feishu_client。"""
        credentials = _require_app(current)
        return feishu_client if feishu_client is not None else FeishuClient(
            credentials, config.timeout_seconds)

    def _current_phone(current):
        row = UserDB.get_by_id(current.user_id)
        phone = (row or {}).get('phone')
        if not phone:
            raise NotificationError('NOTIFICATION_BIND_PHONE_REQUIRED')
        return phone

    def _bind(current, open_id):
        """租户内唯一性校验 + 落库，返回姓名快照（batch_get_id 不返回姓名，
        本期留空）。"""
        existing = UserDB.get_by_feishu_open_id_in_tenant(open_id, current.tenant_id)
        if existing and existing['user_id'] != current.user_id:
            raise NotificationError('NOTIFICATION_FEISHU_BIND_TAKEN', 409)
        UserDB.set_feishu_open_id(current.user_id, open_id)
        return ''

    def _register_target(current, open_id, name):
        """绑定落库成功后登记 feishu_app 投递目标（对齐企微 Phase 3 登记接线）。

        幂等 upsert：复用 repository.register_target 的归属/幂等语义——同用户
        已有 target 时回传当前 binding_version 命中更新分支（重复绑定不变更
        版本），解绑后重绑复活 revoked target；installation_ref 按用户确定
        生成，endpoint 摘要 sha256(feishu_open_id) 由 adapter.validate_target
        产出保持唯一性口径。"""
        rows = [item for item in repository.targets(current)
                if item.get('channel') == 'feishu_app']
        # 与企微同口径：existing 优先 active 行回传版本，避免 stale revoked 行
        # 触发 BINDING_CHANGED
        existing = next((item for item in rows if item.get('state') == 'active'),
                        rows[0] if rows else None)
        body = FeishuAppTargetRegistration(
            installation_ref='feishu_bind_' + str(current.user_id),
            expected_binding_version=existing['binding_version'] if existing else None,
            configuration={'feishu_open_id': open_id, 'bound_user_name': name,
                           'tenant_id': current.tenant_id or ''})
        # identity_preverified=True 标记本登记由服务端已核验飞书身份（confirm
        # 路径 batch_get_id 按登录用户手机号重新核实）的绑定流程发起，repository
        # 据此对接手/复活非 active 行豁免版本钉扎；Web Push 用户 API 不带此标记
        return repository.register_target(current, body, identity_preverified=True)

    def _bind_and_register(current, open_id):
        """落库 + target 登记：登记失败不影响绑定落库本身，结果在响应
        （target_registered 字段）与日志可见。"""
        name = _bind(current, open_id)
        registered = True
        try:
            _register_target(current, open_id, name)
        except Exception:
            registered = False
            logger.opt(exception=True).warning(
                '飞书绑定成功但通知 target 登记失败 user_id={}', current.user_id)
        return {'success': True, 'open_id': open_id, 'name': name,
                'target_registered': registered}

    @router.get('/feishu/bind/status')
    def feishu_bind_status(current: Subject = Depends(subject)):
        """绑定状态（对齐企微 Phase 4 设置页）：应用可用性（按当前 subject
        租户的渠道配置）+ 绑定态 + 姓名快照 + target 状态。不发飞书出站请求，
        直接读本地事实。"""
        repository.assert_schema()
        row = UserDB.get_by_id(current.user_id) or {}
        bound = bool(row.get('feishu_open_id'))
        profile = (repository.feishu_bind_profile(current) if bound
                   else {'target_state': None, 'name': None})
        return {'app_available': _credentials_for(current.tenant_id) is not None,
                'bound': bound, 'name': profile.get('name'),
                'target_state': profile.get('target_state')}

    @router.get('/feishu/bind/suggest')
    def feishu_bind_suggest(current: Subject = Depends(subject)):
        """手机号识别（只读）：登录用户手机号 → batch_get_id 查 open_id；
        命中 matched:true（batch_get_id 不返回姓名，name 恒空），未命中
        matched:false。不落库。"""
        client = _feishu_client(current)
        open_id = client.open_id_by_phone(_current_phone(current))
        if not open_id:
            return {'matched': False}
        return {'matched': True, 'open_id': open_id, 'name': ''}

    @router.post('/feishu/bind/confirm')
    def feishu_bind_confirm(body: FeishuBindConfirm, current: Subject = Depends(subject)):
        client = _feishu_client(current)
        # 服务端以登录用户手机号重查核实，不信任前端传的 open_id
        server_open_id = client.open_id_by_phone(_current_phone(current))
        if not server_open_id or server_open_id != body.open_id:
            raise NotificationError('NOTIFICATION_FEISHU_BIND_MISMATCH')
        return _bind_and_register(current, server_open_id)

    @router.delete('/feishu/bind')
    def feishu_bind_unbind(current: Subject = Depends(subject)):
        repository.assert_schema()
        # 撤销既有 feishu_app target（登记的投递目标；无 target 时跳过）
        for target in repository.targets(current):
            if target.get('channel') == 'feishu_app' and target.get('state') == 'active':
                repository.revoke_target(current, target['target_id'], target['binding_version'])
        UserDB.set_feishu_open_id(current.user_id, None)
        return {'success': True}

    return router
