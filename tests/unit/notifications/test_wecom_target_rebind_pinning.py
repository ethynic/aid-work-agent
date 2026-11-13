"""复审回炉（外部复审 3 项）· repository.register_target 钉扎语义单测：

- 复审 2 死循环：更新分支的版本/安装引用钉扎仅对 active 行生效，revoked 行
  （revive/接手后重绑）跳过校验直接激活——否则换绑闭环「A 绑甲→B 绑乙→双双
  解绑→B 接手甲→B 解绑→B 重绑甲」回传 stale 版本（wecom_bind 层 existing 回退
  按 created_at 最新取到乙行）恒 409。
- active 行防并发 lost-update 语义保留：expected 必须同时匹配 installation_ref
  与 binding_version。

Fake 光标按 SQL 形状分派，用内存行列表模拟 notification_targets 的查询/更新
序列（不执行真实 SQL，PG 语义由集成用例在真实 PostgreSQL 上覆盖）。
"""

import hashlib
from datetime import datetime, timedelta

import pytest

from src.config.settings import NotificationsConfig
from src.notifications.contracts import (AdapterCapabilities, AdapterRegistry,
                                         NotificationError, Subject,
                                         WecomAppTargetRegistration)
from src.notifications.repository import NotificationRepository, protected


class FakeWecomAdapter:
    """wecom_app 协议桩：digest 口径与生产 adapter 一致（sha256(wecom_userid)）。"""

    channel = provider = 'wecom_app'
    capabilities = AdapterCapabilities()

    def available(self):
        return True

    def validate_target(self, configuration):
        return hashlib.sha256(configuration['wecom_userid'].encode()).hexdigest()


def digest_of(userid):
    return hashlib.sha256(userid.encode()).hexdigest()


def target_row(target_id, user_id, installation, digest, version, state, created_at):
    """notification_targets 行内存态：register_target 查询/更新所需字段全集。"""
    return {'target_id': target_id, 'scope_key': 'tenant:t1', 'tenant_id': 't1',
            'recipient_user_id': user_id, 'channel': 'wecom_app', 'provider': 'wecom_app',
            'installation_ref': installation, 'identity_digest': digest,
            'configuration_encrypted': protected({'wecom_userid': digest[:6]}),
            'rule_id': None, 'binding_version': version, 'state': state,
            'enabled_since': None, 'verified_at': None, 'created_at': created_at,
            'updated_at': created_at}


class FakeCursor:
    """按 SQL 形状分派的内存光标：只实现 register_target/revoke_target/
    wecom_bind_profile 路径涉及的语句。"""

    def __init__(self, store):
        self.store = store

    def execute(self, sql, params=()):
        self._sql = sql
        self._params = params
        self._result = []
        if 'FROM users' in sql:
            self._result = [dict(self.store['user'])]
        elif 'FROM tenants' in sql:
            self._result = [{'ok': 1}]
        elif 'pg_advisory_xact_lock' in sql:
            pass
        elif 'identity_digest=%s' in sql and 'FOR UPDATE' in sql:
            _, digest, scope_key = params
            self._result = [row for row in self.store['targets']
                            if row['provider'] == 'wecom_app' and row['identity_digest'] == digest
                            and row['scope_key'] == scope_key]
        elif "installation_ref=%s ORDER BY" in sql:
            provider, installation = params
            matches = [row for row in self.store['targets']
                       if row['provider'] == provider and row['installation_ref'] == installation]
            matches.sort(key=lambda row: (row['state'] == 'active', row['updated_at']), reverse=True)
            self._result = matches[:1]
        elif 'scope_key=%s' in sql and "channel='wecom_app'" in sql:
            # wecom_bind_profile：active 优先、created_at 回退，LIMIT 1
            matches = [row for row in self.store['targets']
                       if row['scope_key'] == params[0] and row['recipient_user_id'] == params[1]]
            matches.sort(key=lambda row: (row['state'] == 'active', row['created_at']), reverse=True)
            self._result = matches[:1]
        elif 'scope_key=%s' in sql and 'recipient_user_id=%s' in sql and 'ORDER BY' in sql:
            # targets()：本人历史行全量，created_at DESC
            matches = [row for row in self.store['targets']
                       if row['scope_key'] == params[0] and row['recipient_user_id'] == params[1]]
            matches.sort(key=lambda row: row['created_at'], reverse=True)
            self._result = matches
        elif "SET recipient_user_id=%s" in sql:
            self._takeover(params)
        elif "SET configuration_encrypted=%s" in sql:
            self._update_activate(params)
        elif 'INSERT INTO notification_targets' in sql:
            self._insert(params)
        elif "SET state='revoked'" in sql and 'notification_deliveries' not in sql:
            self._revoke(params)
        elif "SET state='revoked'" in sql and 'notification_deliveries' in sql:
            pass
        elif 'WHERE target_id=%s FOR UPDATE' in sql:
            self._result = [row for row in self.store['targets']
                            if row['target_id'] == params[0]]
        elif "channel='wecom_app'" in sql and 'ORDER BY' in sql:
            matches = [row for row in self.store['targets']
                       if row['scope_key'] == params[0] and row['recipient_user_id'] == params[1]]
            matches.sort(key=lambda row: (row['state'] == 'active', row['created_at']), reverse=True)
            self._result = matches[:1]
        # 其余语句（deliveries 清理等）无需返回

    def _takeover(self, params):
        user_id, installation, configuration, target_id = params
        for row in self.store['targets']:
            if row['target_id'] == target_id:
                row.update(recipient_user_id=user_id, installation_ref=installation,
                           configuration_encrypted=configuration, state='active',
                           binding_version=row['binding_version'] + 1,
                           enabled_since=datetime(2026, 11, 13), updated_at=datetime(2026, 11, 13))
                self._result = [row]
                return

    def _update_activate(self, params):
        configuration, digest, bump, target_id = params
        for row in self.store['targets']:
            if row['target_id'] == target_id:
                was_active = row['state'] == 'active'
                row.update(configuration_encrypted=configuration, identity_digest=digest,
                           state='active', binding_version=row['binding_version'] + bump,
                           enabled_since=row['enabled_since'] if was_active else datetime(2026, 11, 13),
                           updated_at=datetime(2026, 11, 13))
                self._result = [row]
                return

    def _insert(self, params):
        (target_id, scope_key, tenant_id, user_id, channel, provider,
         installation, digest, configuration) = params
        row = {'target_id': target_id, 'scope_key': scope_key, 'tenant_id': tenant_id,
               'recipient_user_id': user_id, 'channel': channel, 'provider': provider,
               'installation_ref': installation, 'identity_digest': digest,
               'configuration_encrypted': configuration, 'rule_id': None,
               'binding_version': 1, 'state': 'active', 'enabled_since': datetime(2026, 11, 13),
               'verified_at': datetime(2026, 11, 13), 'created_at': self.store['clock'](),
               'updated_at': datetime(2026, 11, 13)}
        self.store['targets'].append(row)
        self._result = [row]

    def _revoke(self, params):
        for row in self.store['targets']:
            if row['target_id'] == params[0]:
                row.update(state='revoked', binding_version=row['binding_version'] + 1,
                           updated_at=datetime(2026, 11, 13))
                self._result = [row]
                return

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakeCursor(self.store)

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def make_repository():
    """内存行仓库：同库序列递增 created_at，复现 targets() 的 created_at DESC 排序。"""
    store = {'targets': [], 'user': {'user_id': 'u', 'tenant_id': 't1',
                                     'role': 'user', 'status': 'active'},
             'clock': (lambda seq=iter(range(1, 10 ** 6)):
                       datetime(2026, 1, 1) + timedelta(seconds=next(seq)))}
    return NotificationRepository(NotificationsConfig(),
                                  AdapterRegistry([FakeWecomAdapter()]),
                                  connection_factory=lambda: FakeConn(store))


def bind_like_wecom_layer(repository, subject, userid):
    """与 wecom_bind._register_target 同构：existing 优先 active 行，无 active
    回退 created_at 最近行回传其 binding_version（stale 亦然）。"""
    rows = [item for item in repository.targets(subject)
            if item.get('channel') == 'wecom_app']
    existing = next((item for item in rows if item.get('state') == 'active'),
                    rows[0] if rows else None)
    return repository.register_target(subject, WecomAppTargetRegistration(
        installation_ref='wecom_bind_' + subject.user_id,
        expected_binding_version=existing['binding_version'] if existing else None,
        configuration={'wecom_userid': userid, 'bound_user_name': '张三',
                       'tenant_id': subject.tenant_id or ''}))


A = Subject('user_a', 't1')
B = Subject('user_b', 't1')


class TestRebindClosedLoop:
    """复审 2 换绑闭环：A 绑甲→B 绑乙→双双解绑→B 接手甲→B 解绑→B 重绑甲。"""

    def _chain_until_unbound(self, repository):
        first = bind_like_wecom_layer(repository, A, 'zhangsan')       # A 绑甲
        assert first['binding_version'] == 1 and first['state'] == 'active'
        yi = bind_like_wecom_layer(repository, B, 'lisi')              # B 绑乙（行更新，created_at 更晚）
        repository.revoke_target(A, first['target_id'], first['binding_version'])
        repository.revoke_target(B, yi['target_id'], yi['binding_version'])  # 双双解绑
        taken = bind_like_wecom_layer(repository, B, 'zhangsan')       # B 接手甲（行复用，created_at 不变）
        assert taken['target_id'] == first['target_id'] and taken['state'] == 'active'
        repository.revoke_target(B, taken['target_id'], taken['binding_version'])  # B 解绑
        return first, yi, taken

    def test_rebind_after_takeover_unbind_succeeds(self):
        """重绑甲：wecom_bind 层 existing 回退取到乙行 stale 版本，repository
        对 revoked 甲行跳过钉扎直接激活——不得 BINDING_CHANGED 409。"""
        repository = make_repository()
        first, _, taken = self._chain_until_unbound(repository)
        rebound = bind_like_wecom_layer(repository, B, 'zhangsan')
        assert rebound['target_id'] == first['target_id']
        assert rebound['state'] == 'active'
        assert rebound['binding_version'] == taken['binding_version'] + 2  # 解绑+1、重绑+1

    def test_rebind_after_takeover_unbind_idempotent(self):
        """重绑成功后幂等：active 行回传当前版本，重复绑定版本不变。"""
        repository = make_repository()
        self._chain_until_unbound(repository)
        rebound = bind_like_wecom_layer(repository, B, 'zhangsan')
        again = bind_like_wecom_layer(repository, B, 'zhangsan')
        assert again['target_id'] == rebound['target_id']
        assert again['binding_version'] == rebound['binding_version']


class TestActiveRowPinningPreserved:
    """active 行防并发 lost-update 钉扎保留：expected 必须匹配版本与安装引用。"""

    def test_active_row_wrong_version_rejected(self):
        repository = make_repository()
        first = bind_like_wecom_layer(repository, A, 'zhangsan')
        with pytest.raises(NotificationError, match='NOTIFICATION_BINDING_CHANGED'):
            repository.register_target(A, WecomAppTargetRegistration(
                installation_ref='wecom_bind_' + A.user_id,
                expected_binding_version=first['binding_version'] + 5,
                configuration={'wecom_userid': 'zhangsan', 'bound_user_name': '张三',
                               'tenant_id': 't1'}))

    def test_active_row_wrong_installation_ref_rejected(self):
        repository = make_repository()
        bind_like_wecom_layer(repository, A, 'zhangsan')
        with pytest.raises(NotificationError, match='NOTIFICATION_BINDING_CHANGED'):
            repository.register_target(A, WecomAppTargetRegistration(
                installation_ref='wecom_bind_other_installation',
                expected_binding_version=1,
                configuration={'wecom_userid': 'zhangsan', 'bound_user_name': '张三',
                               'tenant_id': 't1'}))


class TestRevokedRowSkipsPinning:
    """复审 2：revoked 行无在途投递、无 lost-update 面（advisory 锁串行化），
    expected 传错值也必须能激活。"""

    def test_revoked_row_wrong_version_activates(self):
        repository = make_repository()
        first = bind_like_wecom_layer(repository, A, 'zhangsan')
        repository.revoke_target(A, first['target_id'], first['binding_version'])
        revived = repository.register_target(A, WecomAppTargetRegistration(
            installation_ref='wecom_bind_' + A.user_id,
            expected_binding_version=999,
            configuration={'wecom_userid': 'zhangsan', 'bound_user_name': '张三',
                           'tenant_id': 't1'}))
        assert revived['target_id'] == first['target_id']
        assert revived['state'] == 'active'
        assert revived['binding_version'] == first['binding_version'] + 2
