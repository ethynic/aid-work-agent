"""GET /api/notifications/task-rules 路由测试（定时任务接入通知规则）：

普通用户只读接口：委托 repository.list_task_rules（本租户 enabled 的
scheduled.task 规则 + 渠道目标脱敏摘要）；租户上下文缺失 fail-closed。
参照 tests/unit/api 现有路由测试手法：最小 app + stub repository/user_loader。
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.notifications.api import create_router
from src.notifications.contracts import NotificationError


class _StubRepository:
    def __init__(self):
        self.subjects = []

    def assert_schema(self):
        pass

    def list_task_rules(self, subject):
        # 与真实实现同口径：租户缺失 fail-closed（本租户限定查询的守卫）
        if subject.tenant_id is None:
            raise NotificationError('NOTIFICATION_TENANT_REQUIRED', 400)
        self.subjects.append(subject)
        return [{'rule_id': 'rule_1', 'name': '值班通知', 'conditions': {},
                 'targets': []}]


def _client(repository, user):
    app = FastAPI()
    app.include_router(create_router(
        repository=repository, user_loader=lambda authorization: dict(user)))
    return TestClient(app, raise_server_exceptions=False)


def test_task_rules_delegates_to_repository_with_subject():
    repository = _StubRepository()
    user = {'user_id': 'user_1', 'tenant_id': 'tenant_1', 'role': 'user'}
    resp = _client(repository, user).get(
        '/api/notifications/task-rules', headers={'Authorization': 'Bearer t'})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {'rules': [{'rule_id': 'rule_1', 'name': '值班通知',
                               'conditions': {}, 'targets': []}]}
    # 普通用户只读：subject 按请求身份解析（user + 所属租户）
    assert len(repository.subjects) == 1
    assert repository.subjects[0].user_id == 'user_1'
    assert repository.subjects[0].tenant_id == 'tenant_1'


def test_task_rules_missing_tenant_rejected_fail_closed():
    """platform_admin 未带 X-Tenant-Id：subject 租户为空，list_task_rules
    守卫拒绝（NOTIFICATION_TENANT_REQUIRED 400），不返回规则数据"""
    repository = _StubRepository()
    user = {'user_id': 'plat_1', 'tenant_id': None, 'role': 'platform_admin'}
    resp = _client(repository, user).get(
        '/api/notifications/task-rules', headers={'Authorization': 'Bearer t'})
    assert resp.status_code == 400
    assert resp.json() == {'error': 'NOTIFICATION_TENANT_REQUIRED'}
    assert repository.subjects == []
