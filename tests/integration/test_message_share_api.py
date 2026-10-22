"""HTTP/service integration with only persistence/identity/storage at the boundary."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from src.api import message_share as api
from src.services.message_sharing import service


@pytest.fixture
def share_client(monkeypatch, tmp_path):
    records = {}
    session = {'session_id': 'session-a', 'tenant_id': 'tenant-a', 'user_id': 'user-a'}
    source = {'message_id': 'message-a', 'content': 'A report and ![image](/api/files/file_image/download)',
        'metadata': {'images': [{'file_id': 'file_image', 'download_url': '/api/files/file_image/download'}],
                     'attachments': [{'file_id': 'file_pdf', 'name': 'report.pdf', 'mime_type': 'application/pdf', 'size': 4}]}}
    def owned(authorization, tenant, session_id):
        if authorization != 'Bearer test-principal' or tenant != session['tenant_id'] or session_id != session['session_id']:
            raise HTTPException(403)
        return session
    monkeypatch.setattr(api, 'owned_web_session', owned)
    monkeypatch.setattr(service.MessageShareDB, 'source_message', lambda owned_session, message_id: source if message_id == 'message-a' else None)
    monkeypatch.setattr(service.MessageShareDB, 'create', lambda record: records.update({record['share_key']: record}))
    monkeypatch.setattr(service.MessageShareDB, 'get', records.get)
    monkeypatch.setattr(service.MessageShareDB, 'resource_grant', lambda *args: None)
    monkeypatch.setattr(api._password_limiter, 'is_allowed', lambda key: True)
    folder = tmp_path / 'tenants' / 'tenant-a'
    folder.mkdir(parents=True)
    for name in ('file_image.png', 'file_pdf.pdf', 'file_report.html', 'file_vector.svg'):
        (folder / name).write_bytes(b'data')
    source['metadata']['downloadableFiles'] = [
        {'file_id': 'file_report', 'file_name': 'report.html', 'mime_type': 'text/html'},
        {'file_id': 'file_vector', 'file_name': 'vector.svg', 'mime_type': 'image/svg+xml'}]
    monkeypatch.setattr(service, 'get_file_asset', lambda file_id: None)
    original = service.resolve_scoped_uploaded_file
    monkeypatch.setattr(service, 'resolve_scoped_uploaded_file', lambda file_id, tenant_id, metadata=None:
        original(file_id, tenant_id, storage_root=tmp_path, metadata=metadata))
    application = FastAPI()
    application.include_router(api.router)
    return TestClient(application)


def test_password_protects_reply_image_and_real_file_bytes(share_client):
    response = share_client.post('/api/sessions/session-a/messages/message-a/share', json={'password': 'viewer-test'},
        headers={'Authorization': 'Bearer test-principal', 'X-Tenant-Id': 'tenant-a'})
    assert response.status_code == 200
    key = response.json()['share_key']
    base = '/api/public/reply-shares/' + key
    assert share_client.post(base + '/view', json={}).json() == {'state': 'password_required'}
    assert share_client.post(base + '/view', json={'password': 'bad'}).status_code == 403
    assert share_client.get(base + '/files/asset_0').status_code == 403
    reading = share_client.post(base + '/view', json={'password': 'viewer-test'})
    assert reading.status_code == 200
    result = reading.json()
    assert '/api/files/file_image' not in str(result)
    image = share_client.get(result['message']['images'][0]['download_url'])
    assert image.status_code == 200 and image.content == b'data'
    pdf = share_client.get(base + '/files/asset_1', params={'ticket': result['access_ticket'], 'download': '1'})
    assert pdf.status_code == 200 and pdf.content == b'data'
    assert 'attachment' in pdf.headers['content-disposition']
    assert share_client.get(base + '/files/file_other', params={'ticket': result['access_ticket']}).status_code == 404


def test_wrong_owner_and_wrong_source_cannot_create(share_client):
    assert share_client.post('/api/sessions/session-a/messages/message-a/share', json={}).status_code == 403
    assert share_client.post('/api/sessions/session-a/messages/other/share', json={},
        headers={'Authorization': 'Bearer test-principal', 'X-Tenant-Id': 'tenant-a'}).status_code == 404


def test_password_optional_does_not_require_viewer_login(share_client):
    response = share_client.post('/api/sessions/session-a/messages/message-a/share', json={},
        headers={'Authorization': 'Bearer test-principal', 'X-Tenant-Id': 'tenant-a'})
    key = response.json()['share_key']
    response = share_client.post('/api/public/reply-shares/' + key + '/view', json={})
    assert response.status_code == 200 and response.json()['state'] == 'ready'


def test_active_document_new_tab_is_sandboxed_as_well_as_embedded_preview(share_client):
    created = share_client.post('/api/sessions/session-a/messages/message-a/share', json={},
        headers={'Authorization': 'Bearer test-principal', 'X-Tenant-Id': 'tenant-a'}).json()
    base = '/api/public/reply-shares/' + created['share_key']
    result = share_client.post(base + '/view', json={}).json()
    for file in result['message']['downloadableFiles']:
        response = share_client.get(file['download_url'])
        assert response.status_code == 200
        assert response.headers['content-security-policy'] == 'sandbox allow-scripts allow-downloads'
