"""四轮回炉（外部复审 1 项：Web Push 回归）· register_target 钉扎豁免收口单测：

上一轮为企微换绑把 register_target 更新分支的 installation_ref/版本钉扎与接手
分支放宽为「仅 active 行生效」，该放宽作用于所有渠道——包括用户直接调用的
Web Push 登记 API（POST /api/notifications/targets，channel 仅 web_push）。
回归场景：标签页 A 读 binding_version=1 发登记请求 → 标签页 B 关闭通知
（revoke，版本升 2）→ A 的请求晚到仍带版本 1 → 正确行为是 409
NOTIFICATION_BINDING_CHANGED（尊重用户刚做出的关闭），回归后却是 200 并把
目标复活为 active（opt-out 被翻转为 opt-in）。

修复：钉扎/接手豁免收口为 identity_preverified=True（登记由服务端核验企微
身份的绑定流程发起）；Web Push 用户 API（默认 False）恢复——
- 更新分支：无条件 installation_ref + expected 版本校验（revoked 行 stale
  请求 409 BINDING_CHANGED）；
- 接手分支：他人行（不分 state）409 NOTIFICATION_TARGET_OWNED（web push
  设计 §6：切换主体必须撤销旧目标重新订阅，不静默覆盖）；
- active 行防并发 lost-update 语义对所有取值不变。

Fake 光标按 SQL 形状分派（与 test_wecom_target_rebind_pinning.py 同风格，
provider 取自 SQL 参数以覆盖 web_push 行），不执行真实 SQL；PG 语义由集成
用例在真实 PostgreSQL 上覆盖。
"""

import hashlib
from datetime import datetime, timedelta

import pytest

from src.config.settings import NotificationsConfig
from src.notifications.contracts import (AdapterCapabilities, AdapterRegistry,
                                         NotificationError, RegisterTarget,
                                         Subject)
from src.notifications.repository import NotificationRepository


class FakeWebPushAdapter:
    """web_push 协议桩：digest 口径与生产 adapter 一致（sha256(endpoint)）。"""

    channel = provider = 'web_push'
    capabilities = AdapterCapabilities()

    def available(self):
        return True

    def validate_target(self, configuration):
        return hashlib.sha256(configuration['endpoint'].encode()).hexdigest()


class FakeCursor:
    """按 SQL 形状分派的内存光标：只实现 register_target/revoke_target 路径
    涉及的语句。"""

    def __init__(self, store):
        self.store = store

    def execute(self, sql, params=()):
        self._result = []
        if 'FROM users' in sql:
            self._result = [dict(self.store['user'])]
        elif 'FROM tenants' in sql:
            self._result = [{'ok': 1}]
        elif 'pg_advisory_xact_lock' in sql:
            pass
        elif 'identity_digest=%s' in sql and 'FOR UPDATE' in sql:
            # digest 查重（provider, digest, scope_key）——provider 用 SQL 参数，
            # 不钉死 wecom_app，web_push 行同样可查
            provider, digest, scope_key = params
            self._result = [row for row in self.store['targets']
                            if row['provider'] == provider and row['identity_digest'] == digest
                            and row['scope_key'] == scope_key]
        elif 'installation_ref=%s' in sql and 'FOR UPDATE' in sql:
            # 生产 SQL 中 %s 与 ORDER BY 之间是换行，不能用含空格的整段子串匹配
            provider, installation = params
            matches = [row for row in self.store['targets']
                       if row['provider'] == provider and row['installation_ref'] == installation]
            matches.sort(key=lambda row: (row['state'] == 'active', row['updated_at']), reverse=True)
            self._result = matches[:1]
        elif 'scope_key=%s' in sql and 'recipient_user_id=%s' in sql and 'ORDER BY' in sql:
            # targets()：本人历史行全量，created_at DESC
            matches = [row for row in self.store['targets']
                       if row['scope_key'] == params[0] and row['recipient_user_id'] == params[1]]
            matches.sort(key=lambda row: row['created_at'], reverse=True)
            self._result = matches
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
        # 其余语句（deliveries 清理等）无需返回

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
    store = {'targets': [], 'user': {'user_id': 'u', 'tenant_id': 't1',
                                     'role': 'user', 'status': 'active'},
             'clock': (lambda seq=iter(range(1, 10 ** 6)):
                       datetime(2026, 1, 1) + timedelta(seconds=next(seq)))}
    return NotificationRepository(NotificationsConfig(),
                                  AdapterRegistry([FakeWebPushAdapter()]),
                                  connection_factory=lambda: FakeConn(store))


def registration(*, installation='web_push_installation_0001', endpoint='https://push.invalid/endpoint-1',
                 version=None):
    return RegisterTarget(channel='web_push', provider='web_push',
                          installation_ref=installation, expected_binding_version=version,
                          configuration={'endpoint': endpoint,
                                         'keys': {'p256dh': 'fixture-public', 'auth': 'fixture-auth'},
                                         'key_id': 'v1'})


owner = Subject('user_a', 't1')
other = Subject('user_b', 't1')


class TestWebPushStaleRequestOnRevokedRow:
    """回归主场景：用户关闭通知（revoke，版本升高）后，晚到的 stale 登记请求
    （expected 版本仍为关闭前）必须 409 BINDING_CHANGED，不得复活目标。"""

    def test_stale_version_after_revoke_rejected(self):
        repository = make_repository()
        first = repository.register_target(owner, registration(version=None))
        assert first['binding_version'] == 1 and first['state'] == 'active'
        repository.revoke_target(owner, first['target_id'], 1)  # 用户关闭通知，版本升 2
        with pytest.raises(NotificationError, match='NOTIFICATION_BINDING_CHANGED'):
            repository.register_target(owner, registration(version=1))
        # 目标保持 revoked、版本未变——不得被 stale 请求复活为 opt-in
        row = next(item for item in repository.targets(owner)
                   if item['target_id'] == first['target_id'])
        assert row['state'] == 'revoked' and row['binding_version'] == 2

    def test_wrong_installation_on_revoked_row_rejected(self):
        """钉扎校验同时覆盖 installation_ref：revoked 行上换安装引用+stale 版本
        同样 409（恢复回归前的无条件校验）。"""
        repository = make_repository()
        first = repository.register_target(owner, registration())
        repository.revoke_target(owner, first['target_id'], 1)
        with pytest.raises(NotificationError, match='NOTIFICATION_BINDING_CHANGED'):
            repository.register_target(owner, registration(
                installation='web_push_installation_0002', version=1))

    def test_stale_version_on_active_row_rejected_unchanged(self):
        """active 行防并发 lost-update 语义不变（对本修复的取值不敏感，守住
        不被顺带放松）。换 endpoint 使 digest 查重不命中，走 installation
        查重命中 active 行；版本带 stale 值必须 409（回归前同版本+换
        endpoint 本就是合法更新，返回 200，此处 stale 版本才是守卫点）。"""
        repository = make_repository()
        first = repository.register_target(owner, registration())
        with pytest.raises(NotificationError, match='NOTIFICATION_BINDING_CHANGED'):
            repository.register_target(owner, registration(version=999, endpoint='https://push.invalid/rotated'))
        row = next(item for item in repository.targets(owner)
                   if item['target_id'] == first['target_id'])
        assert row['state'] == 'active' and row['binding_version'] == 1

    def test_revive_with_matching_version_still_works(self):
        """正常复活路径保持可用：用户重新开启通知时前端回传当前版本
        （revoke 后的 2），复活为 active 版本 3。"""
        repository = make_repository()
        first = repository.register_target(owner, registration())
        repository.revoke_target(owner, first['target_id'], 1)
        revived = repository.register_target(owner, registration(version=2))
        assert revived['target_id'] == first['target_id']
        assert revived['state'] == 'active' and revived['binding_version'] == 3


class TestWebPushTakeoverOtherRow:
    """web push 设计 §6：切换主体必须撤销旧目标重新订阅，不静默覆盖——他人
    endpoint 行（含 revoked）对 Web Push 用户 API 保持 409 TARGET_OWNED。"""

    def test_takeover_other_revoked_endpoint_rejected(self):
        repository = make_repository()
        first = repository.register_target(owner, registration())
        repository.revoke_target(owner, first['target_id'], 1)
        with pytest.raises(NotificationError, match='NOTIFICATION_TARGET_OWNED'):
            repository.register_target(other, registration(endpoint='https://push.invalid/endpoint-1'))

    def test_takeover_other_active_endpoint_rejected_unchanged(self):
        """他人 active 行 409 语义不变（对本修复的取值不敏感）。"""
        repository = make_repository()
        repository.register_target(owner, registration())
        with pytest.raises(NotificationError, match='NOTIFICATION_TARGET_OWNED'):
            repository.register_target(other, registration(endpoint='https://push.invalid/endpoint-1'))
