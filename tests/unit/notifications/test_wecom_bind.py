"""企微应用绑定 Phase 1（身份绑定）测试（docs/system/wecom-app-task-notification-design.md §4.1）：

- OAuth state 生成与一次性消费（重放/过期/非法格式拒绝）
- callback：code 换 userid 成功落库；企微 errcode 非零失败；成员不在可见范围拒绝；未登录 401
- suggest 手机号预填只读不写；confirm 服务端二次核实（前端伪造 userid 被拒）
- 解绑清空 wecom_userid 并撤销既有 wecom_app target
- Phase 3 登记接线：绑定落库后服务端登记 wecom_app target（幂等 upsert，
  回传既有 binding_version）；登记失败不影响绑定落库，响应 target_registered 可见

参照 test_api_task_rules.py 手法：最小 app + 注入 fake 客户端/仓储/DB。
"""

import pytest
from types import SimpleNamespace
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.config.settings import NotificationsConfig, WecomAppConfig
from src.db.models import UserDB
from src.notifications.contracts import NotificationError
from src.notifications.wecom_bind import BindStateStore, create_bind_router
from src.services.auth_service import AuthSubjectError


# ============== Fakes ==============

_UNSET = object()


class FakeWecomClient:
    """企微通知应用 API 假客户端：记录调用，按预设返回（None 表示 errcode 非零失败）。"""

    def __init__(self, oauth_userid='zhangsan', member=_UNSET, userid_by_phone='zhangsan'):
        self.oauth_userid_result = oauth_userid
        self.member_result = ({'userid': 'zhangsan', 'name': '张三', 'status': 1}
                              if member is _UNSET else member)
        self.userid_by_phone_result = userid_by_phone
        self.oauth_codes = []
        self.phone_queries = []
        self.member_queries = []

    def oauth_userid(self, code):
        self.oauth_codes.append(code)
        return self.oauth_userid_result

    def member(self, userid):
        self.member_queries.append(userid)
        return self.member_result

    def userid_by_phone(self, mobile):
        self.phone_queries.append(mobile)
        return self.userid_by_phone_result


class StubBindRepository:
    """unbind 撤销 + Phase 3 登记接线 + Phase 4 状态用：assert_schema/targets/
    revoke_target/register_target/registry/wecom_bind_profile。"""

    def __init__(self, targets=(), register_error=None, profile=None, adapter=None):
        self._targets = list(targets)
        self.revoked = []
        self.registered = []
        self.register_error = register_error
        # Phase 4 状态端点：registry.get('wecom_app','wecom_app') 判应用可用性；
        # wecom_bind_profile 返回 target 状态与姓名快照（None=未登记 target）
        self.registry = SimpleNamespace(get=lambda channel, provider: adapter)
        self._profile = profile if profile is not None else {'target_state': None, 'name': None}

    def wecom_bind_profile(self, subject):
        return dict(self._profile)

    def assert_schema(self):
        pass

    def targets(self, subject):
        return self._targets

    def revoke_target(self, subject, target_id, version):
        self.revoked.append((target_id, version))
        return {'target_id': target_id, 'state': 'revoked'}

    def register_target(self, subject, body):
        if self.register_error is not None:
            raise self.register_error
        self.registered.append((subject, body))
        return {'target_id': 'target_new', 'installation_ref': body.installation_ref,
                'binding_version': 1, 'state': 'active', 'channel': body.channel,
                'provider': body.provider}


def _wecom_target(version=1, state='active'):
    return {'target_id': 'target_wecom_1', 'installation_ref': 'inst_wecom',
            'binding_version': version, 'state': state, 'enabled_since': None,
            'channel': 'wecom_app', 'provider': 'wecom_app', 'rule_id': None}


def _web_push_target():
    return {'target_id': 'target_web_1', 'installation_ref': 'inst_web',
            'binding_version': 1, 'state': 'active', 'enabled_since': None,
            'channel': 'web_push', 'provider': 'web_push', 'rule_id': None}


# ============== 测试装配 ==============

def enabled_config(**overrides):
    return NotificationsConfig(wecom_app=WecomAppConfig(
        enabled=True, corp_id='corp123', agentid=1000001,
        secret=SecretStr('app-secret'), redirect_uri='https://x.example.com/notification/wecom-bind',
        **overrides))


def make_app(user=None, wecom=None, store=None, repository=None, config=None):
    """user=None 时注入拒绝型 user_loader（模拟未登录）。"""
    if user is None:
        def user_loader(authorization):
            raise AuthSubjectError('USER_UNAUTHORIZED', 401)
    else:
        def user_loader(authorization):
            return dict(user)
    app = FastAPI()
    # 与生产挂载同构：bind 路由无前缀，由 /api/notifications 父路由挂载
    parent = APIRouter(prefix='/api/notifications')
    parent.include_router(create_bind_router(
        config=config if config is not None else enabled_config(),
        repository=repository if repository is not None else StubBindRepository(),
        user_loader=user_loader,
        state_store=store,
        wecom_client=wecom if wecom is not None else FakeWecomClient()))
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
    monkeypatch.setattr(UserDB, 'get_by_wecom_userid_in_tenant',
                        staticmethod(lambda wecom_userid, tenant_id: (
                            {'user_id': state['taken_by'], 'tenant_id': tenant_id}
                            if state['taken_by'] else None)))
    monkeypatch.setattr(UserDB, 'set_wecom_userid',
                        staticmethod(lambda user_id, wecom_userid: state['saved'].append((user_id, wecom_userid)) or True))
    return state


# ============== state 一次性消费（绑定发起用户） ==============

class TestBindState:
    def test_issue_and_single_consumption(self):
        store = BindStateStore()
        state = store.issue('user_1', 't1')
        assert state
        assert store.consume(state, 'user_1', 't1') is True
        # 重放拒绝
        assert store.consume(state, 'user_1', 't1') is False

    def test_other_user_rejected_and_owner_unaffected(self):
        """CR P2：state 绑定发起用户——用户 B 消费用户 A 的 state 被拒，且不烧掉 A 的 state。"""
        store = BindStateStore()
        state = store.issue('user_1', 't1')
        assert store.consume(state, 'user_2', 't1') is False
        # 发起人本人仍可正常消费
        assert store.consume(state, 'user_1', 't1') is True
        assert store.consume(state, 'user_1', 't1') is False

    def test_other_tenant_rejected_and_owner_unaffected(self):
        """复审 3：state 绑定发起租户——同用户在不同租户上下文回调被拒
        （平台管理员经 X-Tenant-Id 决定租户，防止发起租户绑定登记到 global）。"""
        store = BindStateStore()
        state = store.issue('user_1', 't1')
        assert store.consume(state, 'user_1', 't2') is False
        assert store.consume(state, 'user_1', None) is False
        # 发起租户本人仍可正常消费
        assert store.consume(state, 'user_1', 't1') is True

    def test_expired_rejected(self):
        store = BindStateStore(ttl_seconds=0)
        state = store.issue('user_1', 't1')
        assert store.consume(state, 'user_1', 't1') is False

    def test_malformed_rejected(self):
        store = BindStateStore()
        assert store.consume('', 'user_1', 't1') is False
        assert store.consume('not-a-state', 'user_1', 't1') is False


# ============== callback ==============

class TestCallback:
    def test_success_persists_userid(self, saved):
        wecom = FakeWecomClient()
        store = BindStateStore()
        state = store.issue('user_1', 't1')
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        wecom=wecom, store=store).get(
            f'/api/notifications/wecom/bind/callback?code=goodcode&state={state}', headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {'success': True, 'userid': 'zhangsan', 'name': '张三',
                               'target_registered': True}
        assert wecom.oauth_codes == ['goodcode']
        # 落库当前登录用户（租户归属来自登录态，不信任前端）
        assert saved['saved'] == [('user_1', 'zhangsan')]

    def test_state_replay_rejected(self, saved):
        store = BindStateStore()
        state = store.issue('user_1', 't1')
        client = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'}, store=store)
        first = client.get(f'/api/notifications/wecom/bind/callback?code=c&state={state}', headers=AUTH)
        assert first.status_code == 200
        assert saved['saved'] == [('user_1', 'zhangsan')]
        replay = client.get(f'/api/notifications/wecom/bind/callback?code=c&state={state}', headers=AUTH)
        assert replay.status_code == 400
        assert replay.json() == {'error': 'NOTIFICATION_BIND_STATE_INVALID'}
        assert saved['saved'] == [('user_1', 'zhangsan')]

    def test_state_missing_rejected(self, saved):
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'}).get(
            '/api/notifications/wecom/bind/callback?code=c', headers=AUTH)
        assert resp.status_code == 400
        assert resp.json() == {'error': 'NOTIFICATION_BIND_STATE_INVALID'}
        assert saved['saved'] == []

    def test_state_from_other_user_rejected_owner_unaffected(self, saved):
        """CR P2：用户 B 拿用户 A 签发的 state 调 callback 被拒；A 本人随后仍绑定成功。"""
        wecom = FakeWecomClient()
        store = BindStateStore()
        state_a = store.issue('user_1', 't1')
        client_b = make_app(user={'user_id': 'user_2', 'tenant_id': 't1', 'role': 'user'},
                            wecom=wecom, store=store)
        resp_b = client_b.get(f'/api/notifications/wecom/bind/callback?code=c&state={state_a}',
                              headers=AUTH)
        assert resp_b.status_code == 400
        assert resp_b.json() == {'error': 'NOTIFICATION_BIND_STATE_INVALID'}
        assert saved['saved'] == []
        # 发起人 A 用同一 state 正常走完绑定
        client_a = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                            wecom=wecom, store=store)
        resp_a = client_a.get(f'/api/notifications/wecom/bind/callback?code=goodcode&state={state_a}',
                              headers=AUTH)
        assert resp_a.status_code == 200
        assert resp_a.json() == {'success': True, 'userid': 'zhangsan', 'name': '张三',
                                 'target_registered': True}
        assert saved['saved'] == [('user_1', 'zhangsan')]

    def test_callback_tenant_mismatch_rejected(self, saved):
        """复审 3：回调 subject 租户与发起租户不一致 → STATE_INVALID（不消费 state）。"""
        wecom = FakeWecomClient()
        store = BindStateStore()
        state = store.issue('user_1', 't1')
        # 同用户在另一租户上下文回调（普通用户租户来自登录态）
        client = make_app(user={'user_id': 'user_1', 'tenant_id': 't2', 'role': 'user'},
                          wecom=wecom, store=store)
        resp = client.get(f'/api/notifications/wecom/bind/callback?code=c&state={state}',
                          headers=AUTH)
        assert resp.status_code == 400
        assert resp.json() == {'error': 'NOTIFICATION_BIND_STATE_INVALID'}
        assert saved['saved'] == []
        # state 未被烧掉，发起租户本人仍可完成绑定
        owner = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                         wecom=wecom, store=store)
        assert owner.get(f'/api/notifications/wecom/bind/callback?code=c&state={state}',
                         headers=AUTH).status_code == 200

    def test_platform_admin_without_tenant_header_rejected(self, saved):
        """复审 3：平台管理员从租户设置页发起（state 锁定发起租户）但回调不带
        X-Tenant-Id → STATE_INVALID，不再把绑定登记进 global scope。"""
        wecom = FakeWecomClient()
        store = BindStateStore()
        state = store.issue('admin_1', 't1')
        client = make_app(user={'user_id': 'admin_1', 'tenant_id': None, 'role': 'platform_admin'},
                          wecom=wecom, store=store)
        resp = client.get(f'/api/notifications/wecom/bind/callback?code=c&state={state}',
                          headers=AUTH)
        assert resp.status_code == 400
        assert resp.json() == {'error': 'NOTIFICATION_BIND_STATE_INVALID'}
        assert saved['saved'] == []

    def test_platform_admin_with_tenant_header_registers_to_tenant(self, saved):
        """复审 3：平台管理员带发起租户 X-Tenant-Id 回调 → subject 租户=发起租户，
        state 校验通过，target 登记到发起租户 scope。"""
        wecom = FakeWecomClient()
        repository = StubBindRepository()
        store = BindStateStore()
        state = store.issue('admin_1', 't1')
        client = make_app(user={'user_id': 'admin_1', 'tenant_id': None, 'role': 'platform_admin'},
                          wecom=wecom, store=store, repository=repository)
        resp = client.get(f'/api/notifications/wecom/bind/callback?code=goodcode&state={state}',
                          headers={**AUTH, 'X-Tenant-Id': 't1'})
        assert resp.status_code == 200
        assert resp.json() == {'success': True, 'userid': 'zhangsan', 'name': '张三',
                               'target_registered': True}
        assert saved['saved'] == [('admin_1', 'zhangsan')]
        # 登记到发起租户（subject 租户与配置 tenant_id 均为发起租户）
        subject, body = repository.registered[0]
        assert (subject.user_id, subject.tenant_id) == ('admin_1', 't1')
        assert body.configuration.tenant_id == 't1'

    def test_wecom_errcode_nonzero_rejected(self, saved):
        wecom = FakeWecomClient(oauth_userid=None)  # errcode 非零 → 客户端返回 None
        store = BindStateStore()
        state = store.issue('user_1', 't1')
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        wecom=wecom, store=store).get(
            f'/api/notifications/wecom/bind/callback?code=bad&state={state}', headers=AUTH)
        assert resp.status_code == 400
        assert resp.json() == {'error': 'NOTIFICATION_BIND_FAILED'}
        assert saved['saved'] == []

    def test_member_out_of_scope_rejected(self, saved):
        wecom = FakeWecomClient(member=None)  # user/get errcode 非零（不在可见范围/不存在）
        store = BindStateStore()
        state = store.issue('user_1', 't1')
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        wecom=wecom, store=store).get(
            f'/api/notifications/wecom/bind/callback?code=c&state={state}', headers=AUTH)
        assert resp.status_code == 400
        assert resp.json() == {'error': 'NOTIFICATION_BIND_NOT_IN_SCOPE'}
        assert saved['saved'] == []

    def test_member_disabled_rejected(self, saved):
        wecom = FakeWecomClient(member={'userid': 'zhangsan', 'name': '张三', 'status': 2})
        store = BindStateStore()
        state = store.issue('user_1', 't1')
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        wecom=wecom, store=store).get(
            f'/api/notifications/wecom/bind/callback?code=c&state={state}', headers=AUTH)
        assert resp.status_code == 400
        assert resp.json() == {'error': 'NOTIFICATION_BIND_NOT_IN_SCOPE'}
        assert saved['saved'] == []

    def test_taken_by_other_user_rejected(self, saved):
        saved['taken_by'] = 'user_2'
        store = BindStateStore()
        state = store.issue('user_1', 't1')
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        store=store).get(
            f'/api/notifications/wecom/bind/callback?code=c&state={state}', headers=AUTH)
        assert resp.status_code == 409
        assert resp.json() == {'error': 'NOTIFICATION_BIND_TAKEN'}
        assert saved['saved'] == []

    def test_rebind_same_user_ok(self, saved):
        saved['taken_by'] = 'user_1'  # 已绑定的就是本人 → 幂等重绑
        store = BindStateStore()
        state = store.issue('user_1', 't1')
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        store=store).get(
            f'/api/notifications/wecom/bind/callback?code=c&state={state}', headers=AUTH)
        assert resp.status_code == 200
        assert saved['saved'] == [('user_1', 'zhangsan')]

    def test_unauthenticated_rejected(self, saved):
        resp = make_app().get('/api/notifications/wecom/bind/callback?code=c&state=' + 'a' * 32)
        assert resp.status_code == 401
        assert resp.json() == {'error': 'NOTIFICATION_UNAUTHORIZED'}
        assert saved['saved'] == []


# ============== suggest（手机号预填，只读） ==============

class TestSuggest:
    def test_matched_returns_prefill_without_write(self, saved):
        wecom = FakeWecomClient()
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        wecom=wecom).get('/api/notifications/wecom/bind/suggest', headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {'matched': True, 'userid': 'zhangsan', 'name': '张三'}
        assert wecom.phone_queries == ['13800000000']
        # 只读不改：预填不落库
        assert saved['saved'] == []

    def test_not_matched(self, saved):
        wecom = FakeWecomClient(userid_by_phone=None)
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        wecom=wecom).get('/api/notifications/wecom/bind/suggest', headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {'matched': False}
        assert saved['saved'] == []

    def test_empty_phone_rejected(self, saved):
        saved['phone'] = None
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'}).get(
            '/api/notifications/wecom/bind/suggest', headers=AUTH)
        assert resp.status_code == 400
        assert resp.json() == {'error': 'NOTIFICATION_BIND_PHONE_REQUIRED'}


# ============== confirm（服务端二次核实） ==============

class TestConfirm:
    def test_forged_userid_rejected(self, saved):
        """核心防线：前端传伪造 userid，服务端以登录用户手机号重查不匹配 → 拒绝且不落库。"""
        wecom = FakeWecomClient(userid_by_phone='zhangsan')
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        wecom=wecom).post('/api/notifications/wecom/bind/confirm',
                                          json={'userid': 'hacker'}, headers=AUTH)
        assert resp.status_code == 400
        assert resp.json() == {'error': 'NOTIFICATION_BIND_MISMATCH'}
        assert wecom.phone_queries == ['13800000000']
        assert saved['saved'] == []

    def test_success(self, saved):
        wecom = FakeWecomClient()
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        wecom=wecom).post('/api/notifications/wecom/bind/confirm',
                                          json={'userid': 'zhangsan'}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {'success': True, 'userid': 'zhangsan', 'name': '张三',
                               'target_registered': True}
        assert saved['saved'] == [('user_1', 'zhangsan')]

    def test_phone_unmatched_rejected(self, saved):
        wecom = FakeWecomClient(userid_by_phone=None)
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        wecom=wecom).post('/api/notifications/wecom/bind/confirm',
                                          json={'userid': 'zhangsan'}, headers=AUTH)
        assert resp.status_code == 400
        assert resp.json() == {'error': 'NOTIFICATION_BIND_MISMATCH'}
        assert saved['saved'] == []

    def test_empty_phone_rejected(self, saved):
        saved['phone'] = None
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'}).post(
            '/api/notifications/wecom/bind/confirm', json={'userid': 'zhangsan'}, headers=AUTH)
        assert resp.status_code == 400
        assert resp.json() == {'error': 'NOTIFICATION_BIND_PHONE_REQUIRED'}


# ============== Phase 3 登记接线（绑定 → wecom_app target） ==============

class TestTargetRegistration:
    def _bind_via_callback(self, saved, repository):
        store = BindStateStore()
        state = store.issue('user_1', 't1')
        return make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        store=store, repository=repository).get(
            f'/api/notifications/wecom/bind/callback?code=goodcode&state={state}',
            headers=AUTH)

    def test_callback_registers_wecom_target(self, saved):
        repository = StubBindRepository()
        resp = self._bind_via_callback(saved, repository)
        assert resp.status_code == 200
        assert resp.json()['target_registered'] is True
        assert len(repository.registered) == 1
        subject, body = repository.registered[0]
        assert (body.channel, body.provider) == ('wecom_app', 'wecom_app')
        assert body.installation_ref == 'wecom_bind_user_1'
        assert body.expected_binding_version is None  # 首次登记走 INSERT 分支
        assert body.configuration.wecom_userid == 'zhangsan'
        assert body.configuration.bound_user_name == '张三'
        assert body.configuration.tenant_id == 't1'

    def test_repeat_bind_passes_existing_binding_version(self, saved):
        """重复绑定幂等（upsert 语义）：回传既有 target 的 binding_version，
        repository.register_target 命中更新分支不抬版本。"""
        repository = StubBindRepository(targets=[_wecom_target(version=3)])
        resp = self._bind_via_callback(saved, repository)
        assert resp.status_code == 200
        _, body = repository.registered[0]
        assert body.expected_binding_version == 3

    def test_rebind_after_unbind_upserts_revoked_target(self, saved):
        """解绑（revoked，版本+1）后重绑：回传当前版本复活 target。"""
        repository = StubBindRepository(targets=[_wecom_target(version=2, state='revoked')])
        resp = self._bind_via_callback(saved, repository)
        assert resp.status_code == 200
        _, body = repository.registered[0]
        assert body.expected_binding_version == 2

    def test_existing_selection_prefers_active_row(self, saved):
        """复审 1：existing 优先 active 行回传 binding_version，避免 stale
        revoked 行取错 expected_binding_version 触发 BINDING_CHANGED。"""
        repository = StubBindRepository(targets=[_wecom_target(version=9, state='revoked'),
                                                 _wecom_target(version=5)])
        resp = self._bind_via_callback(saved, repository)
        assert resp.status_code == 200
        _, body = repository.registered[0]
        assert body.expected_binding_version == 5

    def test_register_failure_does_not_break_bind(self, saved):
        """登记失败不影响绑定落库本身：绑定成功 + 响应 target_registered=False 可见。"""
        repository = StubBindRepository(
            register_error=NotificationError('NOTIFICATION_PROVIDER_UNAVAILABLE', 503))
        resp = self._bind_via_callback(saved, repository)
        assert resp.status_code == 200
        body = resp.json()
        assert body['success'] is True and body['target_registered'] is False
        # 绑定落库已生效
        assert saved['saved'] == [('user_1', 'zhangsan')]

    def test_confirm_registers_target(self, saved):
        repository = StubBindRepository()
        wecom = FakeWecomClient()
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        wecom=wecom, repository=repository).post(
            '/api/notifications/wecom/bind/confirm', json={'userid': 'zhangsan'}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()['target_registered'] is True
        _, body = repository.registered[0]
        assert body.configuration.wecom_userid == 'zhangsan'
        assert body.installation_ref == 'wecom_bind_user_1'

    def test_suggest_still_read_only(self, saved):
        """suggest 只读预填：不落库也不登记 target。"""
        repository = StubBindRepository()
        make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                 repository=repository).get('/api/notifications/wecom/bind/suggest', headers=AUTH)
        assert repository.registered == []


# ============== 解绑 ==============

class TestUnbind:
    def test_clears_field_and_revokes_wecom_targets(self, saved):
        repository = StubBindRepository(targets=[_wecom_target(), _web_push_target()])
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        repository=repository).delete('/api/notifications/wecom/bind', headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {'success': True}
        # 只撤销 wecom_app target，不动其他渠道
        assert repository.revoked == [('target_wecom_1', 1)]
        assert saved['saved'] == [('user_1', None)]

    def test_no_targets_still_clears(self, saved):
        repository = StubBindRepository()
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        repository=repository).delete('/api/notifications/wecom/bind', headers=AUTH)
        assert resp.status_code == 200
        assert repository.revoked == []
        assert saved['saved'] == [('user_1', None)]


# ============== 授权链接 ==============

class TestBindUrl:
    def test_success_returns_links_with_state(self):
        store = BindStateStore()
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        store=store).get('/api/notifications/wecom/bind/url', headers=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body['success'] is True
        # 企业内 OAuth 链接与 PC 扫码链接共用同一 state；state 已入 Redis 可消费
        assert f"appid=corp123" in body['oauth_url']
        assert 'scope=snsapi_base' in body['oauth_url']
        assert 'redirect_uri=https%3A%2F%2Fx.example.com%2Fnotification%2Fwecom-bind' in body['oauth_url']
        assert 'agentid=1000001' in body['qr_url']
        # 评审回炉 P1-3：扫码授权页域名 open.work.weixin.qq.com（open.weixin.qq.com
        # 的 qrConnect 路径 404）；不再返回 qr_data_url（前端新窗口打开授权页，
        # 不把登录页链接编码成二维码给手机扫）
        assert body['qr_url'].startswith('https://open.work.weixin.qq.com/wwopen/sso/qrConnect')
        assert 'qr_data_url' not in body
        # state 已入 Redis：发起人可被回调一次性消费
        assert store.consume(body['state'], 'user_1', 't1') is True

    def test_disabled_config_unavailable(self):
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        config=NotificationsConfig()).get('/api/notifications/wecom/bind/url', headers=AUTH)
        assert resp.status_code == 503
        assert resp.json() == {'error': 'NOTIFICATION_WECOM_APP_UNAVAILABLE'}


# ============== 绑定状态（Phase 4，设计 §4.5 设置页） ==============

class AvailableAdapter:
    """占位 adapter：registry 命中且 available()=True。"""
    channel = provider = 'wecom_app'

    def available(self):
        return True


class TestBindStatus:
    def _client(self, repository=None):
        return make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        repository=repository or StubBindRepository())

    def test_unbound_reports_available_without_target(self, monkeypatch):
        monkeypatch.setattr(UserDB, 'get_by_id',
                            staticmethod(lambda user_id: {'user_id': user_id,
                                                          'wecom_userid': None}))
        repository = StubBindRepository(adapter=AvailableAdapter())
        resp = self._client(repository).get('/api/notifications/wecom/bind/status', headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {'app_available': True, 'bound': False,
                               'name': None, 'target_state': None}

    def test_bound_reports_name_and_target_state(self, monkeypatch):
        monkeypatch.setattr(UserDB, 'get_by_id',
                            staticmethod(lambda user_id: {'user_id': user_id,
                                                          'wecom_userid': 'zhangsan'}))
        repository = StubBindRepository(
            adapter=AvailableAdapter(),
            profile={'target_state': 'active', 'name': '张三'})
        resp = self._client(repository).get('/api/notifications/wecom/bind/status', headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {'app_available': True, 'bound': True,
                               'name': '张三', 'target_state': 'active'}

    def test_adapter_unavailable_reports_false(self, monkeypatch):
        monkeypatch.setattr(UserDB, 'get_by_id', staticmethod(lambda user_id: None))
        # registry 无 wecom_app adapter（get 返回 None）→ 应用不可用
        resp = self._client().get('/api/notifications/wecom/bind/status', headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()['app_available'] is False

    def test_target_invalid_after_send_failure(self, monkeypatch):
        """投递 errcode target_invalid 后 target 置 invalid：设置页据此提示重绑。"""
        monkeypatch.setattr(UserDB, 'get_by_id',
                            staticmethod(lambda user_id: {'user_id': user_id,
                                                          'wecom_userid': 'zhangsan'}))
        repository = StubBindRepository(
            adapter=AvailableAdapter(),
            profile={'target_state': 'invalid', 'name': '张三'})
        resp = self._client(repository).get('/api/notifications/wecom/bind/status', headers=AUTH)
        assert resp.json()['target_state'] == 'invalid'

    def test_unauthenticated_rejected(self):
        resp = make_app(repository=StubBindRepository()).get('/api/notifications/wecom/bind/status')
        assert resp.status_code == 401


# ============== 偏好开关接线（Phase 4，设计 §4.5） ==============

class TestPreferenceWiring:
    """PreferenceRequest 已扩 wecom_app → repository.set_preference 经
    registry.get(channel, provider) 命中 WecomAppAdapter（service.py 组合根），
    available gate 决定启用是否放行。本组只测不依赖 DB 的链路环节。"""

    def _repository(self, config):
        from src.notifications.service import notification_repository
        return notification_repository(config)

    def test_wecom_app_adapter_registered_in_registry(self):
        repository = self._repository(enabled_config())
        adapter = repository.registry.get('wecom_app', 'wecom_app')
        assert adapter is not None and adapter.available() is True

    def test_preference_request_accepts_wecom_app(self):
        from src.notifications.contracts import PreferenceRequest
        body = PreferenceRequest.model_validate({'category': 'task', 'channel': 'wecom_app',
                                                 'provider': 'wecom_app', 'enabled': True})
        assert body.enabled is True

    def test_preference_request_rejects_unknown_channel(self):
        from pydantic import ValidationError
        from src.notifications.contracts import PreferenceRequest
        with pytest.raises(ValidationError):
            PreferenceRequest.model_validate({'category': 'task', 'channel': 'dingtalk_bot',
                                              'provider': 'dingtalk_bot', 'enabled': True})

    def test_enable_preference_blocked_when_adapter_unavailable(self):
        """wecom_app 未启用时启用偏好被 available gate 拒绝（DB 访问之前）。"""
        from src.notifications.contracts import NotificationError, PreferenceRequest
        repository = self._repository(NotificationsConfig())
        with pytest.raises(NotificationError) as excinfo:
            repository.set_preference(
                None, PreferenceRequest.model_validate({'category': 'task', 'channel': 'wecom_app',
                                                        'provider': 'wecom_app', 'enabled': True}))
        assert excinfo.value.code == 'NOTIFICATION_PROVIDER_UNAVAILABLE'


def test_disabled_config_blocks_suggest_and_confirm(saved):
    config = NotificationsConfig()
    client = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'}, config=config)
    resp = client.get('/api/notifications/wecom/bind/suggest', headers=AUTH)
    assert resp.status_code == 503
    resp = client.post('/api/notifications/wecom/bind/confirm', json={'userid': 'zhangsan'}, headers=AUTH)
    assert resp.status_code == 503
    assert resp.json() == {'error': 'NOTIFICATION_WECOM_APP_UNAVAILABLE'}
