"""钉钉应用绑定（钉钉任务通知接入，2026-11-16，设计决策 7 追加）测试：

- suggest 手机号识别只读不写（命中 matched:true + userid；未命中/errcode 非 0
  matched:false；getbymobile 无姓名字段，name 恒空）
- confirm 服务端二次核实（前端伪造 userid 被拒）；命中即落库 + 登记 dingtalk_app target
- 唯一性校验（同租户已被其他账号绑定 409）；无手机号 400
- 解绑清空 dingtalk_userid 并撤销既有 dingtalk_app target（web_push/feishu_app
  target 不动）
- status 纯本地事实（不出站）；租户未配置 503 gate；未登录 401
- target 登记接线：幂等 upsert（active 行优先回传版本）、登记失败不影响绑定落库

参照 test_feishu_bind.py 手法：最小 app + 注入 fake 客户端/仓储/DB。
"""

import pytest
from types import SimpleNamespace
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from src.config.settings import NotificationsConfig
from src.db.models import UserDB
from src.notifications.contracts import DingtalkBindConfirm
from src.notifications.dingtalk_bind import create_dingtalk_bind_router
from src.notifications.dingtalk_app import DingtalkAppCredentials
from src.services.auth_service import AuthSubjectError

USERID = 'user123'


# ============== Fakes ==============

class FakeDingtalkClient:
    """钉钉通知 API 假客户端：记录调用，按预设返回（None 表示接口/errcode 失败）。"""

    def __init__(self, userid_by_mobile=USERID):
        self.userid_result = userid_by_mobile
        self.phone_queries = []

    def userid_by_mobile(self, mobile):
        self.phone_queries.append(mobile)
        return self.userid_result


class StubBindRepository:
    """unbind 撤销 + 登记接线 + status 状态用：assert_schema/targets/
    revoke_target/register_target/registry/dingtalk_bind_profile。"""

    def __init__(self, targets=(), register_error=None, profile=None, adapter=None):
        self._targets = list(targets)
        self.revoked = []
        self.registered = []
        self.register_error = register_error
        # status 端点：registry.get('dingtalk_app','dingtalk_app') 判应用可用性；
        # dingtalk_bind_profile 返回 target 状态与姓名快照（None=未登记 target）
        self.registry = SimpleNamespace(get=lambda channel, provider: adapter)
        self._profile = profile if profile is not None else {'target_state': None, 'name': None}

    def dingtalk_bind_profile(self, subject):
        return dict(self._profile)

    def assert_schema(self):
        pass

    def targets(self, subject):
        return self._targets

    def revoke_target(self, subject, target_id, version):
        self.revoked.append((target_id, version))
        return {'target_id': target_id, 'state': 'revoked'}

    def register_target(self, subject, body, *, identity_preverified=False):
        # identity_preverified=True（钉钉绑定路径标记，对齐企微/飞书）不影响本桩断言
        if self.register_error is not None:
            raise self.register_error
        self.registered.append((subject, body, identity_preverified))
        return {'target_id': 'target_new', 'installation_ref': body.installation_ref,
                'binding_version': 1, 'state': 'active', 'channel': body.channel,
                'provider': body.provider}


def _dingtalk_target(version=1, state='active'):
    return {'target_id': 'target_ding_1', 'installation_ref': 'inst_ding',
            'binding_version': version, 'state': state, 'enabled_since': None,
            'channel': 'dingtalk_app', 'provider': 'dingtalk_app', 'rule_id': None}


def _web_push_target():
    return {'target_id': 'target_web_1', 'installation_ref': 'inst_web',
            'binding_version': 1, 'state': 'active', 'enabled_since': None,
            'channel': 'web_push', 'provider': 'web_push', 'rule_id': None}


def _feishu_target():
    return {'target_id': 'target_feishu_1', 'installation_ref': 'inst_feishu',
            'binding_version': 1, 'state': 'active', 'enabled_since': None,
            'channel': 'feishu_app', 'provider': 'feishu_app', 'rule_id': None}


# ============== 测试装配 ==============

TENANT_CREDENTIALS = DingtalkAppCredentials(tenant_id='t1', app_key='ding-key',
                                            app_secret='ding-secret', agent_id=1000002)


def tenant_credentials_source(tenant_id):
    """租户自备凭据桩：t1 有完整的通知用途钉钉应用配置，其他租户未配置。"""
    return TENANT_CREDENTIALS if tenant_id == 't1' else None


def make_app(user=None, dingtalk=None, repository=None, config=None,
             source=tenant_credentials_source):
    """user=None 时注入拒绝型 user_loader（模拟未登录）。

    source：tenant_id → 凭据（缺省 t1 可用）；传 None 走 repository.registry
    adapter 的 credentials_for_tenant（生产默认路径）。
    """
    if user is None:
        def user_loader(authorization):
            raise AuthSubjectError('USER_UNAUTHORIZED', 401)
    else:
        def user_loader(authorization):
            return dict(user)
    app = FastAPI()
    # 与生产挂载同构：bind 路由无前缀，由 /api/notifications 父路由挂载
    parent = APIRouter(prefix='/api/notifications')
    kwargs = {}
    if source is not None:
        kwargs['credentials_source'] = source
    parent.include_router(create_dingtalk_bind_router(
        config=config if config is not None else NotificationsConfig(),
        repository=repository if repository is not None else StubBindRepository(),
        user_loader=user_loader,
        dingtalk_client=dingtalk if dingtalk is not None else FakeDingtalkClient(),
        **kwargs))
    app.include_router(parent)
    return TestClient(app, raise_server_exceptions=False)


AUTH = {'Authorization': 'Bearer t'}


@pytest.fixture()
def saved(monkeypatch):
    """接管 UserDB 三个静态方法：phone 用户行 / 唯一性查询 / 落库记录。"""
    state = {'phone': '13800000000', 'taken_by': None, 'saved': []}

    monkeypatch.setattr(UserDB, 'get_by_id',
                        staticmethod(lambda user_id: {'user_id': user_id, 'tenant_id': 't1',
                                                      'phone': state['phone']}))
    monkeypatch.setattr(UserDB, 'get_by_dingtalk_userid_in_tenant',
                        staticmethod(lambda dingtalk_userid, tenant_id: (
                            {'user_id': state['taken_by'], 'tenant_id': tenant_id}
                            if state['taken_by'] else None)))
    monkeypatch.setattr(UserDB, 'set_dingtalk_userid',
                        staticmethod(lambda user_id, dingtalk_userid: state['saved'].append((user_id, dingtalk_userid)) or True))
    return state


SUBJECT = {'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'}


# ============== suggest（手机号识别，只读） ==============

class TestSuggest:
    def test_matched_returns_userid_without_write(self, saved):
        client = make_app(user=SUBJECT)
        resp = client.get('/api/notifications/dingtalk/bind/suggest', headers=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        # getbymobile 无姓名字段，name 恒空（2026-11-16 官方文档口径）
        assert body == {'matched': True, 'userid': USERID, 'name': ''}
        assert saved['saved'] == []  # 只读，不落库

    def test_not_matched(self, saved):
        client = make_app(user=SUBJECT, dingtalk=FakeDingtalkClient(userid_by_mobile=None))
        resp = client.get('/api/notifications/dingtalk/bind/suggest', headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {'matched': False}
        assert saved['saved'] == []

    def test_empty_phone_rejected(self, saved):
        saved['phone'] = None
        client = make_app(user=SUBJECT)
        resp = client.get('/api/notifications/dingtalk/bind/suggest', headers=AUTH)
        assert resp.status_code == 400
        assert resp.json()['error'] == 'NOTIFICATION_BIND_PHONE_REQUIRED'

    def test_unconfigured_tenant_unavailable(self, saved):
        client = make_app(user=dict(SUBJECT, tenant_id='t9'), source=tenant_credentials_source)
        resp = client.get('/api/notifications/dingtalk/bind/suggest', headers=AUTH)
        assert resp.status_code == 503
        assert resp.json()['error'] == 'NOTIFICATION_DINGTALK_APP_UNAVAILABLE'

    def test_unauthenticated_rejected(self, saved):
        client = make_app(user=None)
        resp = client.get('/api/notifications/dingtalk/bind/suggest')
        assert resp.status_code == 401


# ============== confirm（服务端二次核实 + 落库 + 登记） ==============

class TestConfirm:
    def test_success_binds_and_registers(self, saved):
        repo = StubBindRepository()
        client = make_app(user=SUBJECT, repository=repo)
        resp = client.post('/api/notifications/dingtalk/bind/confirm',
                           headers=AUTH, json={'userid': USERID})
        assert resp.status_code == 200
        body = resp.json()
        assert body['success'] is True and body['userid'] == USERID
        assert body['target_registered'] is True
        # 落库的是服务端核实出的 userid
        assert saved['saved'] == [('user_1', USERID)]
        # target 登记：channel dingtalk_app + 服务端已核验标记
        assert len(repo.registered) == 1
        subject, registered_body, preverified = repo.registered[0]
        assert registered_body.channel == 'dingtalk_app'
        assert registered_body.configuration.dingtalk_userid == USERID
        assert registered_body.configuration.tenant_id == 't1'
        assert registered_body.installation_ref == 'dingtalk_bind_user_1'
        assert preverified is True

    def test_forged_userid_rejected(self, saved):
        """前端伪造 userid：服务端按手机号重查结果不一致 → 拒绝，不落库。"""
        repo = StubBindRepository()
        client = make_app(user=SUBJECT, repository=repo,
                          dingtalk=FakeDingtalkClient(userid_by_mobile=None))
        resp = client.post('/api/notifications/dingtalk/bind/confirm',
                           headers=AUTH, json={'userid': USERID})
        assert resp.status_code == 400
        assert resp.json()['error'] == 'NOTIFICATION_DINGTALK_BIND_MISMATCH'
        assert saved['saved'] == [] and repo.registered == []

    def test_phone_unmatched_rejected(self, saved):
        """手机号未命中（服务端核实 None）→ MISMATCH，不落库。"""
        client = make_app(user=SUBJECT, dingtalk=FakeDingtalkClient(userid_by_mobile=None))
        resp = client.post('/api/notifications/dingtalk/bind/confirm',
                           headers=AUTH, json={'userid': USERID})
        assert resp.status_code == 400
        assert saved['saved'] == []

    def test_mismatched_userid_rejected(self, saved):
        """服务端核实出不同 userid（前端回传旧值）→ MISMATCH。"""
        client = make_app(user=SUBJECT,
                          dingtalk=FakeDingtalkClient(userid_by_mobile='someone-else'))
        resp = client.post('/api/notifications/dingtalk/bind/confirm',
                           headers=AUTH, json={'userid': USERID})
        assert resp.status_code == 400
        assert saved['saved'] == []

    def test_taken_by_other_user_rejected(self, saved):
        saved['taken_by'] = 'user_2'
        client = make_app(user=SUBJECT)
        resp = client.post('/api/notifications/dingtalk/bind/confirm',
                           headers=AUTH, json={'userid': USERID})
        assert resp.status_code == 409
        assert resp.json()['error'] == 'NOTIFICATION_DINGTALK_BIND_TAKEN'
        assert saved['saved'] == []

    def test_rebind_same_user_ok(self, saved):
        """同用户重复 confirm（幂等）：唯一性校验放行，正常落库登记。"""
        saved['taken_by'] = 'user_1'
        repo = StubBindRepository()
        client = make_app(user=SUBJECT, repository=repo)
        resp = client.post('/api/notifications/dingtalk/bind/confirm',
                           headers=AUTH, json={'userid': USERID})
        assert resp.status_code == 200
        assert saved['saved'] == [('user_1', USERID)]

    def test_empty_phone_rejected(self, saved):
        saved['phone'] = None
        client = make_app(user=SUBJECT)
        resp = client.post('/api/notifications/dingtalk/bind/confirm',
                           headers=AUTH, json={'userid': USERID})
        assert resp.status_code == 400
        assert resp.json()['error'] == 'NOTIFICATION_BIND_PHONE_REQUIRED'

    def test_invalid_body_rejected(self, saved):
        client = make_app(user=SUBJECT)
        resp = client.post('/api/notifications/dingtalk/bind/confirm', headers=AUTH, json={})
        assert resp.status_code == 422
        assert resp.json()['error'] == 'NOTIFICATION_INVALID_REQUEST'


# ============== target 登记接线 ==============

class TestTargetRegistration:
    def test_repeat_bind_passes_existing_binding_version(self, saved):
        """幂等 upsert：已有 active target 时回传 binding_version 命中更新分支。"""
        repo = StubBindRepository(targets=[_dingtalk_target(version=3)])
        client = make_app(user=SUBJECT, repository=repo)
        client.post('/api/notifications/dingtalk/bind/confirm', headers=AUTH, json={'userid': USERID})
        registered_body = repo.registered[0][1]
        assert registered_body.expected_binding_version == 3

    def test_rebind_after_unbind_upserts_revoked_target(self, saved):
        """解绑后重绑：revoked 行回传版本复活（对齐企微/飞书 upsert 语义）。"""
        repo = StubBindRepository(targets=[_dingtalk_target(version=2, state='revoked')])
        client = make_app(user=SUBJECT, repository=repo)
        client.post('/api/notifications/dingtalk/bind/confirm', headers=AUTH, json={'userid': USERID})
        registered_body = repo.registered[0][1]
        assert registered_body.expected_binding_version == 2

    def test_existing_selection_prefers_active_row(self, saved):
        """既有 active 行被 stale revoked 行遮蔽时优先 active（对齐飞书复审 1）。"""
        repo = StubBindRepository(targets=[_dingtalk_target(version=9, state='revoked'),
                                           _dingtalk_target(version=4)])
        client = make_app(user=SUBJECT, repository=repo)
        client.post('/api/notifications/dingtalk/bind/confirm', headers=AUTH, json={'userid': USERID})
        registered_body = repo.registered[0][1]
        assert registered_body.expected_binding_version == 4

    def test_register_failure_does_not_break_bind(self, saved):
        """登记失败不影响绑定落库本身，响应 target_registered 可见（对齐飞书）。"""
        repo = StubBindRepository(register_error=RuntimeError('db down'))
        client = make_app(user=SUBJECT, repository=repo)
        resp = client.post('/api/notifications/dingtalk/bind/confirm',
                           headers=AUTH, json={'userid': USERID})
        assert resp.status_code == 200
        body = resp.json()
        assert body['success'] is True and body['target_registered'] is False
        assert saved['saved'] == [('user_1', USERID)]

    def test_suggest_still_read_only(self, saved):
        """suggest 全程只读：不落库、不登记 target。"""
        repo = StubBindRepository()
        client = make_app(user=SUBJECT, repository=repo)
        client.get('/api/notifications/dingtalk/bind/suggest', headers=AUTH)
        assert saved['saved'] == [] and repo.registered == []


# ============== 解绑 ==============

class TestUnbind:
    def test_clears_field_and_revokes_dingtalk_targets(self, saved, monkeypatch):
        monkeypatch.setattr(UserDB, 'get_by_id',
                            staticmethod(lambda user_id: {'user_id': user_id, 'tenant_id': 't1',
                                                          'phone': '13800000000',
                                                          'dingtalk_userid': USERID}))
        repo = StubBindRepository(targets=[_dingtalk_target(), _web_push_target(), _feishu_target()])
        client = make_app(user=SUBJECT, repository=repo)
        resp = client.delete('/api/notifications/dingtalk/bind', headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {'success': True}
        # 清列 + 仅撤销 dingtalk_app target（web_push/feishu_app 不动）
        assert saved['saved'] == [('user_1', None)]
        assert repo.revoked == [('target_ding_1', 1)]

    def test_no_targets_still_clears(self, saved, monkeypatch):
        monkeypatch.setattr(UserDB, 'get_by_id',
                            staticmethod(lambda user_id: {'user_id': user_id, 'tenant_id': 't1',
                                                          'phone': '13800000000',
                                                          'dingtalk_userid': USERID}))
        repo = StubBindRepository()
        client = make_app(user=SUBJECT, repository=repo)
        resp = client.delete('/api/notifications/dingtalk/bind', headers=AUTH)
        assert resp.status_code == 200
        assert saved['saved'] == [('user_1', None)]
        assert repo.revoked == []

    def test_unconfigured_tenant_still_unbinds(self, saved):
        """解绑不过租户 gate（对齐企微/飞书 unbind 口径）：用户始终可以解绑撤离。"""
        client = make_app(user=dict(SUBJECT, tenant_id='t9'), source=tenant_credentials_source)
        resp = client.delete('/api/notifications/dingtalk/bind', headers=AUTH)
        assert resp.status_code == 200
        assert saved['saved'] == [('user_1', None)]


# ============== status（纯本地事实） ==============

class AvailableAdapter:
    """registry 命中的可用 adapter（credentials_for_tenant 走生产默认路径时）。"""
    channel = provider = 'dingtalk_app'

    def credentials_for_tenant(self, tenant_id):
        return TENANT_CREDENTIALS if tenant_id == 't1' else None

    def available(self):
        return False


class UnavailableAdapter(AvailableAdapter):
    def credentials_for_tenant(self, tenant_id):
        return None


class TestBindStatus:
    def test_unbound_reports_available_without_target(self, monkeypatch):
        monkeypatch.setattr(UserDB, 'get_by_id',
                            staticmethod(lambda user_id: {'user_id': user_id, 'tenant_id': 't1',
                                                          'dingtalk_userid': None}))
        client = make_app(user=SUBJECT)
        resp = client.get('/api/notifications/dingtalk/bind/status', headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {'app_available': True, 'bound': False, 'name': None,
                               'target_state': None}

    def test_bound_reports_name_and_target_state(self, monkeypatch):
        monkeypatch.setattr(UserDB, 'get_by_id',
                            staticmethod(lambda user_id: {'user_id': user_id, 'tenant_id': 't1',
                                                          'dingtalk_userid': USERID}))
        repo = StubBindRepository(profile={'target_state': 'active', 'name': ''})
        client = make_app(user=SUBJECT, repository=repo)
        resp = client.get('/api/notifications/dingtalk/bind/status', headers=AUTH)
        # getbymobile 不返回姓名，name 快照恒空串（路由原样透传 profile 值）
        assert resp.json() == {'app_available': True, 'bound': True, 'name': '',
                               'target_state': 'active'}

    def test_app_available_resolved_via_registry_adapter(self, monkeypatch):
        """生产默认路径：repository.registry 的 DingTalkAppAdapter.credentials_for_tenant。"""
        monkeypatch.setattr(UserDB, 'get_by_id',
                            staticmethod(lambda user_id: {'user_id': user_id, 'tenant_id': 't1',
                                                          'dingtalk_userid': None}))
        client = make_app(user=SUBJECT, source=None,
                          repository=StubBindRepository(adapter=AvailableAdapter()))
        resp = client.get('/api/notifications/dingtalk/bind/status', headers=AUTH)
        assert resp.json()['app_available'] is True

    def test_adapter_unavailable_reports_false(self, monkeypatch):
        monkeypatch.setattr(UserDB, 'get_by_id',
                            staticmethod(lambda user_id: {'user_id': user_id, 'tenant_id': 't1',
                                                          'dingtalk_userid': None}))
        client = make_app(user=SUBJECT, source=None,
                          repository=StubBindRepository(adapter=UnavailableAdapter()))
        resp = client.get('/api/notifications/dingtalk/bind/status', headers=AUTH)
        assert resp.json()['app_available'] is False

    def test_other_tenant_without_config_reports_false(self, monkeypatch):
        monkeypatch.setattr(UserDB, 'get_by_id',
                            staticmethod(lambda user_id: {'user_id': user_id, 'tenant_id': 't1',
                                                          'dingtalk_userid': None}))
        client = make_app(user=dict(SUBJECT, tenant_id='t9'), source=tenant_credentials_source)
        resp = client.get('/api/notifications/dingtalk/bind/status', headers=AUTH)
        assert resp.json()['app_available'] is False

    def test_unauthenticated_rejected(self, monkeypatch):
        client = make_app(user=None)
        resp = client.get('/api/notifications/dingtalk/bind/status')
        assert resp.status_code == 401


# ============== 合同与注册 ==============

class TestContractsAndRegistry:
    def test_dingtalk_app_adapter_registered_in_service_registry(self):
        """service 组合根注册 dingtalk_app adapter（dispatcher 投递可用）。"""
        from src.notifications.service import notification_repository
        repo = notification_repository(NotificationsConfig())
        assert repo.registry.get('dingtalk_app', 'dingtalk_app') is not None

    def test_preference_request_accepts_dingtalk_app(self):
        from src.notifications.contracts import PreferenceRequest
        body = PreferenceRequest.model_validate(
            {'category': 'task', 'channel': 'dingtalk_app', 'provider': 'dingtalk_app',
             'enabled': True})
        assert body.enabled is True

    def test_preference_request_rejects_unknown_channel(self):
        from src.notifications.contracts import PreferenceRequest
        with pytest.raises(Exception):
            PreferenceRequest.model_validate(
                {'category': 'task', 'channel': 'dingtalk', 'provider': 'dingtalk_app',
                 'enabled': True})

    def test_dingtalk_bind_confirm_dto_rejects_extra(self):
        with pytest.raises(Exception):
            DingtalkBindConfirm.model_validate({'userid': USERID, 'open_id': 'x'})

    def test_provider_whitelist_contains_dingtalk_app(self):
        """渠道事件白名单含 dingtalk_app（completed/failed，与 wecom/feishu 同口径）。"""
        from src.notifications.repository import PROVIDER_ALLOWED_EVENT_TYPES
        assert PROVIDER_ALLOWED_EVENT_TYPES['dingtalk_app'] == ('task.completed', 'task.failed')


def test_unconfigured_tenant_blocks_suggest_and_confirm(saved):
    """租户未配置通知用途的钉钉应用：suggest/confirm 均 503（fail-closed）。"""
    client = make_app(user=SUBJECT, source=lambda tenant_id: None)
    assert client.get('/api/notifications/dingtalk/bind/suggest',
                      headers=AUTH).status_code == 503
    assert client.post('/api/notifications/dingtalk/bind/confirm', headers=AUTH,
                       json={'userid': USERID}).status_code == 503
    assert saved['saved'] == []
