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
    """unbind 撤销 + Phase 3 登记接线用：assert_schema/targets/revoke_target/
    register_target。"""

    def __init__(self, targets=(), register_error=None):
        self._targets = list(targets)
        self.revoked = []
        self.registered = []
        self.register_error = register_error

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
        state = store.issue('user_1')
        assert state
        assert store.consume(state, 'user_1') is True
        # 重放拒绝
        assert store.consume(state, 'user_1') is False

    def test_other_user_rejected_and_owner_unaffected(self):
        """CR P2：state 绑定发起用户——用户 B 消费用户 A 的 state 被拒，且不烧掉 A 的 state。"""
        store = BindStateStore()
        state = store.issue('user_1')
        assert store.consume(state, 'user_2') is False
        # 发起人本人仍可正常消费
        assert store.consume(state, 'user_1') is True
        assert store.consume(state, 'user_1') is False

    def test_expired_rejected(self):
        store = BindStateStore(ttl_seconds=0)
        state = store.issue('user_1')
        assert store.consume(state, 'user_1') is False

    def test_malformed_rejected(self):
        store = BindStateStore()
        assert store.consume('', 'user_1') is False
        assert store.consume('not-a-state', 'user_1') is False


# ============== callback ==============

class TestCallback:
    def test_success_persists_userid(self, saved):
        wecom = FakeWecomClient()
        store = BindStateStore()
        state = store.issue('user_1')
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
        state = store.issue('user_1')
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
        state_a = store.issue('user_1')
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

    def test_wecom_errcode_nonzero_rejected(self, saved):
        wecom = FakeWecomClient(oauth_userid=None)  # errcode 非零 → 客户端返回 None
        store = BindStateStore()
        state = store.issue('user_1')
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        wecom=wecom, store=store).get(
            f'/api/notifications/wecom/bind/callback?code=bad&state={state}', headers=AUTH)
        assert resp.status_code == 400
        assert resp.json() == {'error': 'NOTIFICATION_BIND_FAILED'}
        assert saved['saved'] == []

    def test_member_out_of_scope_rejected(self, saved):
        wecom = FakeWecomClient(member=None)  # user/get errcode 非零（不在可见范围/不存在）
        store = BindStateStore()
        state = store.issue('user_1')
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        wecom=wecom, store=store).get(
            f'/api/notifications/wecom/bind/callback?code=c&state={state}', headers=AUTH)
        assert resp.status_code == 400
        assert resp.json() == {'error': 'NOTIFICATION_BIND_NOT_IN_SCOPE'}
        assert saved['saved'] == []

    def test_member_disabled_rejected(self, saved):
        wecom = FakeWecomClient(member={'userid': 'zhangsan', 'name': '张三', 'status': 2})
        store = BindStateStore()
        state = store.issue('user_1')
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        wecom=wecom, store=store).get(
            f'/api/notifications/wecom/bind/callback?code=c&state={state}', headers=AUTH)
        assert resp.status_code == 400
        assert resp.json() == {'error': 'NOTIFICATION_BIND_NOT_IN_SCOPE'}
        assert saved['saved'] == []

    def test_taken_by_other_user_rejected(self, saved):
        saved['taken_by'] = 'user_2'
        store = BindStateStore()
        state = store.issue('user_1')
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        store=store).get(
            f'/api/notifications/wecom/bind/callback?code=c&state={state}', headers=AUTH)
        assert resp.status_code == 409
        assert resp.json() == {'error': 'NOTIFICATION_BIND_TAKEN'}
        assert saved['saved'] == []

    def test_rebind_same_user_ok(self, saved):
        saved['taken_by'] = 'user_1'  # 已绑定的就是本人 → 幂等重绑
        store = BindStateStore()
        state = store.issue('user_1')
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
        state = store.issue('user_1')
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
        # state 已入 Redis：发起人可被回调一次性消费
        assert store.consume(body['state'], 'user_1') is True

    def test_disabled_config_unavailable(self):
        resp = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'},
                        config=NotificationsConfig()).get('/api/notifications/wecom/bind/url', headers=AUTH)
        assert resp.status_code == 503
        assert resp.json() == {'error': 'NOTIFICATION_WECOM_APP_UNAVAILABLE'}


def test_disabled_config_blocks_suggest_and_confirm(saved):
    config = NotificationsConfig()
    client = make_app(user={'user_id': 'user_1', 'tenant_id': 't1', 'role': 'user'}, config=config)
    resp = client.get('/api/notifications/wecom/bind/suggest', headers=AUTH)
    assert resp.status_code == 503
    resp = client.post('/api/notifications/wecom/bind/confirm', json={'userid': 'zhangsan'}, headers=AUTH)
    assert resp.status_code == 503
    assert resp.json() == {'error': 'NOTIFICATION_WECOM_APP_UNAVAILABLE'}
