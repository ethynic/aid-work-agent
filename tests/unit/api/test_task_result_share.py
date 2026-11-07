"""The public result reader accepts anonymous visits without disclosing owner auth failures."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import task_result_share
from src.services.auth_service import AuthSubjectError


def test_view_is_no_store_and_invalid_bearer_stays_anonymous(monkeypatch):
    app = FastAPI()
    app.include_router(task_result_share.router)
    seen = []
    monkeypatch.setattr(task_result_share, 'fresh_web_subject',
                        lambda auth: (_ for _ in ()).throw(AuthSubjectError('USER_UNAUTHORIZED', 401)))
    monkeypatch.setattr(task_result_share, 'view_task_result',
                        lambda key, viewer: seen.append((key, viewer)) or {'viewer_is_owner': False})
    with TestClient(app) as client:
        response = client.get('/api/public/task-result-shares/opaque_key',
                              headers={'Authorization': 'Bearer expired'})
    assert response.status_code == 200
    assert response.json() == {'viewer_is_owner': False}
    assert seen == [('opaque_key', None)]
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Referrer-Policy'] == 'no-referrer'


def test_anonymous_read_is_limited_before_loading_result(monkeypatch):
    app = FastAPI()
    app.include_router(task_result_share.router)
    monkeypatch.setattr(task_result_share._view_limiter, 'is_allowed', lambda key: False)
    loaded = []
    monkeypatch.setattr(task_result_share, 'view_task_result',
                        lambda *args: loaded.append(True))
    with TestClient(app) as client:
        response = client.get('/api/public/task-result-shares/opaque_key')
        forged = client.get('/api/public/task-result-shares/opaque_key',
                            headers={'Authorization': 'Bearer arbitrary'})
    assert response.status_code == 429
    assert forged.status_code == 429
    assert response.json()['detail']['code'] == 'SHARE_RATE_LIMITED'
    assert loaded == []


def test_platform_owner_uses_explicit_tenant_context_only_for_admin(monkeypatch):
    app = FastAPI()
    app.include_router(task_result_share.router)
    seen = []
    monkeypatch.setattr(task_result_share, 'view_task_result',
                        lambda key, viewer: seen.append(viewer) or {'viewer_is_owner': True})
    monkeypatch.setattr(task_result_share, 'fresh_web_subject',
                        lambda auth: {'user_id': 'owner', 'tenant_id': None, 'role': 'platform_admin'})
    with TestClient(app) as client:
        response = client.get('/api/public/task-result-shares/opaque_key',
                              headers={'Authorization': 'Bearer valid', 'X-Tenant-Id': 'tenant_1'})
    assert response.status_code == 200
    assert seen[0]['tenant_id'] == 'tenant_1'
    monkeypatch.setattr(task_result_share, 'fresh_web_subject',
                        lambda auth: {'user_id': 'owner', 'tenant_id': 'tenant_2', 'role': 'user'})
    with TestClient(app) as client:
        client.get('/api/public/task-result-shares/opaque_key',
                   headers={'Authorization': 'Bearer valid', 'X-Tenant-Id': 'tenant_1'})
    assert seen[1]['tenant_id'] == 'tenant_2'
