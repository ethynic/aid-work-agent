"""A shared reply must retain all visible products without opening other files."""

from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from fastapi import HTTPException
import pytest

from src.services.message_sharing import service


@pytest.fixture(autouse=True)
def isolate_asset_grants(monkeypatch):
    monkeypatch.setattr(service.MessageShareDB, 'resource_grant', lambda *args: None)
    monkeypatch.setattr(service, 'get_file_asset', lambda *args: None)
    monkeypatch.setattr(service, 'resolve_scoped_uploaded_file', lambda *args, **kwargs: None)


def source(content='A reply', **metadata):
    return {'message_id': 'message-final', 'content': content, 'metadata': metadata,
            'created_at': datetime(2026, 10, 22)}


def record(snapshot=None, password=None):
    return {'share_key': 'share-a', 'tenant_id': 'tenant-a',
            'reply_data': snapshot or service.reply_snapshot(source()),
            'password_hash': service.hash_password(password) if password else None}


def test_snapshot_keeps_images_attachments_and_legacy_tool_product_without_private_metadata():
    snapshot = service.reply_snapshot(source('![diagram](file_id:file_image) [report](/api/files/file_report/download)',
        images=[{'file_id': 'file_image', 'display_name': 'diagram', 'download_url': '/api/files/file_image/download',
                 'mime_type': 'image/png', 'placement': 'before_text', 'source_ref': 'private-source'}],
        attachments=[{'file_id': 'file_sheet', 'name': 'sheet.xlsx', 'size': 120, 'mime_type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'type': 'file'}],
        progressMessages=[{'type': 'tool_result', 'success': True,
            'toolArgs': {'password': 'never-copy'}, 'result': {'file_id': 'file_report', 'file_name': 'report.pdf',
                'download_file_name': 'My report.pdf', 'file_size': 300, 'mime_type': 'application/pdf'}}],
        reasoning_content='private-thought'))
    message = snapshot['message']
    assert len(message['images']) == len(message['attachments']) == len(message['downloadableFiles']) == 1
    assert message['images'][0]['placement'] == 'before_text'
    assert message['downloadableFiles'][0]['file_name'] == 'My report.pdf'
    assert 'progressMessages' not in message and 'reasoning_content' not in message
    assert 'source_ref' not in message['images'][0]
    assert 'file_image' not in str(message) and '/api/files/' not in str(message)
    assert len(snapshot['files']) == 3


@pytest.mark.parametrize('metadata', [
    {'images': [{'file_id': 'file_image', 'download_url': '/api/files/file_image/download'}]},
    {'attachments': [{'file_id': 'file_document', 'name': 'document.pdf'}]},
    {'downloadableFiles': [{'file_id': 'file_document', 'file_name': 'document.pdf'}]},
])
def test_non_text_replies_are_shareable(metadata):
    assert service.reply_snapshot(source('', **metadata))['files']


def test_shared_image_order_matches_runner_global_placement_fallback():
    snapshot = service.reply_snapshot(source('Analysis', imagesPlacement='before_text',
        images=[{'file_id': 'file_chart'}, {'file_id': 'file_detail', 'placement': 'inline'}]))
    assert [item['placement'] for item in snapshot['message']['images']] == ['before_text', 'inline']


def test_password_gate_releases_no_partial_body_or_resource_reference():
    shared = record(service.reply_snapshot(source('private report', images=[{'file_id': 'file_secret'}])), 'viewer-test')
    assert service.view_share(shared) == {'state': 'password_required'}
    with pytest.raises(HTTPException) as wrong:
        service.view_share(shared, 'wrong')
    assert wrong.value.status_code == 403
    result = service.view_share(shared, 'viewer-test')
    assert result['state'] == 'ready'
    assert result['message']['content'] == 'private report'
    assert '/api/public/reply-shares/share-a/files/asset_0?ticket=' in result['message']['images'][0]['download_url']
    assert 'file_secret' not in str(result)


def test_share_ticket_cannot_open_another_share_or_unlisted_file(monkeypatch):
    shared = record(service.reply_snapshot(source('', attachments=[{'file_id': 'file_document', 'name': 'report.pdf'}])))
    ticket = service.view_share(shared)['access_ticket']
    with pytest.raises(HTTPException) as other:
        service.shared_file({**shared, 'share_key': 'share-b'}, 'asset_0', ticket)
    assert other.value.status_code == 403
    with pytest.raises(HTTPException) as unlisted:
        service.shared_file(shared, 'file_other', ticket)
    assert unlisted.value.status_code == 404
    monkeypatch.setattr(service, 'get_file_asset', lambda file_id: {'path': '/not-used'})
    resolve = Mock(return_value={'path': '/test/report.pdf', 'name': 'report.pdf'})
    monkeypatch.setattr(service, 'resolve_scoped_uploaded_file', resolve)
    assert service.shared_file(shared, 'asset_0', ticket)['name'] == 'report.pdf'
    assert resolve.call_args.args[:2] == ('file_document', 'tenant-a')


def test_external_attachment_redirect_reference_is_preserved_without_server_fetch():
    shared = record(service.reply_snapshot(source('', downloadableFiles=[
        {'file_id': 'external-report', 'file_name': 'report.pdf', 'download_url': 'https://example.org/report.pdf'}])))
    ticket = service.view_share(shared)['access_ticket']
    assert service.shared_file(shared, 'asset_0', ticket) == {'url': 'https://example.org/report.pdf'}


def test_external_file_like_urls_stay_external_but_own_absolute_urls_are_bound():
    cdn = 'https://cdn.example.org/api/files/photo.png'
    snapshot = service.reply_snapshot(source('![remote](' + cdn + ') ![own](https://app.example/api/files/file_own)',
        images=[{'file_id': 'remote', 'download_url': cdn}]), 'https://app.example/')
    assert cdn in snapshot['message']['content']
    assert snapshot['files']['asset_0']['url'] == cdn
    assert snapshot['files']['asset_1']['file_id'] == 'file_own'


def test_document_media_dependencies_are_bound_and_previewed_but_download_remains_original(monkeypatch, tmp_path):
    folder = tmp_path / 'tenants' / 'tenant-a'
    folder.mkdir(parents=True)
    html = folder / 'file_report.html'
    markdown = folder / 'file_details.md'
    html.write_text('<a href="file_details.md">Details</a><img src="file_image.png">', encoding='utf-8')
    markdown.write_text('![chart](file_id:file_image)', encoding='utf-8')
    (folder / 'file_image.png').write_bytes(b'chart-bytes')
    from src.core.storage import resolve_scoped_uploaded_file
    monkeypatch.setattr(service, 'resolve_scoped_uploaded_file', lambda file_id, tenant_id, metadata=None:
        resolve_scoped_uploaded_file(file_id, tenant_id, storage_root=tmp_path, metadata=metadata))
    snapshot = service.reply_snapshot(source('', downloadableFiles=[{'file_id': 'file_report', 'file_name': 'report.html'}]))
    service._bind_resources(snapshot, 'tenant-a')
    shared = record(snapshot)
    ticket = service.view_share(shared)['access_ticket']
    preview = service.shared_file(shared, 'asset_0', ticket)
    assert '/files/asset_1?ticket=' in preview['text'] and '/files/asset_2?ticket=' in preview['text']
    nested = service.shared_file(shared, 'asset_1', ticket)
    assert '/api/public/reply-shares/share-a/files/' in nested['text']
    assert 'file_id:file_image' not in nested['text']
    assert 'text' not in service.shared_file(shared, 'asset_0', ticket, download=True)
    assert html.read_text(encoding='utf-8').startswith('<a href="file_details.md">')
    assert Path(service.shared_file(shared, 'asset_2', ticket)['path']).read_bytes() == b'chart-bytes'


def test_missing_nested_media_never_falls_back_to_original_public_file_url(monkeypatch, tmp_path):
    path = tmp_path / 'report.html'
    path.write_text('<img src="/api/files/file_missing/download"><img src="missing.png">', encoding='utf-8')
    monkeypatch.setattr(service, 'resolve_scoped_uploaded_file', lambda file_id, *args, **kwargs:
        {'path': str(path), 'mime_type': 'text/html'} if file_id == 'file_report' else None)
    snapshot = service.reply_snapshot(source('', attachments=[{'file_id': 'file_report', 'name': 'report.html'}]))
    service._bind_resources(snapshot, 'tenant-a')
    shared = record(snapshot)
    ticket = service.view_share(shared)['access_ticket']
    preview = service.shared_file(shared, 'asset_0', ticket)['text']
    assert '/api/files/file_missing' not in preview and 'src="missing.png"' not in preview
    assert '/files/asset_1?ticket=' in preview and '/files/asset_2?ticket=' in preview
    with pytest.raises(HTTPException) as missing:
        service.shared_file(shared, 'asset_1', ticket)
    assert missing.value.status_code == 404


@pytest.mark.parametrize('attribute', ['src', 'SRC'])
@pytest.mark.parametrize('reference', ['/api/files/file_foreign/download', '/api/files/%66ile_foreign/download',
    '&#x2f;api/files/file_foreign/download', '/%61pi/files/file_foreign/download'])
def test_foreign_nested_media_is_subject_to_the_same_grant_check_as_top_level_media(monkeypatch, tmp_path, reference, attribute):
    path = tmp_path / 'report.html'
    path.write_text('<img ' + attribute + '="' + reference + '">', encoding='utf-8')
    monkeypatch.setattr(service, 'resolve_scoped_uploaded_file', lambda file_id, *args, **kwargs:
        {'path': str(path)} if file_id == 'file_report' else None)
    monkeypatch.setattr(service.MessageShareDB, 'resource_grant', lambda file_id, *args:
        {'tenant_id': 'other', 'allowed': False} if file_id == 'file_foreign' else None)
    snapshot = service.reply_snapshot(source('', attachments=[{'file_id': 'file_report', 'name': 'report.html'}]))
    with pytest.raises(HTTPException) as denied:
        service._bind_resources(snapshot, 'tenant-a')
    assert denied.value.status_code == 403


def test_encoded_content_links_are_rewritten_without_changing_external_hosts():
    snapshot = service.reply_snapshot(source('![chart](/%61pi/files/%66ile_chart/download) '
        '<img src="&#x2f;api/files/file_chart/download"> '
        '[external](https://cdn.example/api/files/%66ile_chart/download)'))
    assert snapshot['message']['content'].count('share-file:asset_0') == 2
    assert 'https://cdn.example/api/files/%66ile_chart/download' in snapshot['message']['content']


def test_url_examples_with_invalid_placeholders_do_not_prevent_sharing(monkeypatch, tmp_path):
    content = 'Example: http://[HOST]/endpoint\n\n```html\n<img src="http://[HOST]/chart">\n```'
    assert service.reply_snapshot(source(content))['message']['content'] == content
    path = tmp_path / 'report.html'
    path.write_text('<img src="http://[HOST]/chart">', encoding='utf-8')
    monkeypatch.setattr(service, 'resolve_scoped_uploaded_file', lambda *args, **kwargs: {'path': str(path)})
    snapshot = service.reply_snapshot(source('', attachments=[{'file_id': 'file_report'}]))
    service._bind_resources(snapshot, 'tenant-a')
    assert len(snapshot['files']) == 1


def test_local_and_external_urls_with_the_same_path_never_rewrite_each_other(monkeypatch, tmp_path):
    local = '/api/files/file_chart/download'
    external = 'https://cdn.example/assets' + local
    text = '![local](' + local + ') ![external](' + external + ')'
    snapshot = service.reply_snapshot(source(text))
    assert external in snapshot['message']['content']
    assert len(snapshot['files']) == 1
    path = tmp_path / 'report.html'
    path.write_text('<img src="' + local + '"><img src="' + external + '">', encoding='utf-8')
    monkeypatch.setattr(service, 'resolve_scoped_uploaded_file', lambda file_id, *args, **kwargs:
        {'path': str(path)} if file_id == 'file_report' else None)
    snapshot = service.reply_snapshot(source('', attachments=[{'file_id': 'file_report', 'name': 'report.html'}]))
    service._bind_resources(snapshot, 'tenant-a')
    shared = record(snapshot)
    preview = service.shared_file(shared, 'asset_0', service.view_share(shared)['access_ticket'])['text']
    assert external in preview
    assert 'https://cdn.example/assets/api/public' not in preview


def test_html_stylesheet_images_use_the_same_share_gate(monkeypatch, tmp_path):
    html, css = tmp_path / 'report.html', tmp_path / 'style.css'
    html.write_text('<link href="style.css" rel="stylesheet">', encoding='utf-8')
    css.write_text('body { background: url(/api/files/file_missing/download) }', encoding='utf-8')
    paths = {'file_report': html, 'style.css': css}
    monkeypatch.setattr(service, 'resolve_scoped_uploaded_file', lambda file_id, *args, **kwargs:
        {'path': str(paths[file_id])} if file_id in paths else None)
    snapshot = service.reply_snapshot(source('', attachments=[{'file_id': 'file_report', 'name': 'report.html'}]))
    service._bind_resources(snapshot, 'tenant-a')
    shared = record(snapshot)
    text = service.shared_file(shared, 'asset_1', service.view_share(shared)['access_ticket'])['text']
    assert '/api/files/file_missing' not in text and '/files/asset_2?ticket=' in text
    monkeypatch.setattr(service.MessageShareDB, 'resource_grant', lambda file_id, *args:
        {'tenant_id': 'other', 'allowed': False} if file_id == 'file_missing' else None)
    with pytest.raises(HTTPException) as denied:
        service._bind_resources(service.reply_snapshot(source('', attachments=[{'file_id': 'file_report'}])), 'tenant-a')
    assert denied.value.status_code == 403


@pytest.mark.parametrize('root_name,expected_owner', [('tenants/tenant-a', 'tenant-a'), ('tenants/_anonymous', None)])
def test_legacy_null_owner_asset_uses_existing_scoped_storage(monkeypatch, tmp_path, root_name, expected_owner):
    folder = tmp_path / root_name
    folder.mkdir(parents=True)
    (folder / 'file_legacy.png').write_bytes(b'legacy-image')
    from src.core.storage import resolve_scoped_uploaded_file
    monkeypatch.setattr(service.MessageShareDB, 'resource_grant', lambda *args: {'tenant_id': None, 'allowed': False})
    monkeypatch.setattr(service, 'resolve_scoped_uploaded_file', lambda file_id, tenant_id, metadata=None:
        resolve_scoped_uploaded_file(file_id, tenant_id, storage_root=tmp_path, metadata=metadata))
    snapshot = service.reply_snapshot(source('', images=[{'file_id': 'file_legacy'}]))
    service._bind_resources(snapshot, 'tenant-a')
    assert snapshot['files']['asset_0']['tenant_id'] == expected_owner
    shared = record(snapshot)
    info = service.shared_file(shared, 'asset_0', service.view_share(shared)['access_ticket'])
    assert Path(info['path']).read_bytes() == b'legacy-image'


def test_signed_resource_ticket_expires_and_malformed_ticket_is_forbidden(monkeypatch):
    shared = record()
    monkeypatch.setattr('src.core.download_ticket.time.time', lambda: 100)
    ticket = service.view_share(shared)['access_ticket']
    monkeypatch.setattr('src.core.download_ticket.time.time', lambda: 100 + service.ACCESS_TTL_SECONDS + 1)
    for value in (ticket, 'bad', 'abc.\u2603'):
        with pytest.raises(HTTPException) as expired:
            service.shared_file(shared, 'asset_0', value)
        assert expired.value.status_code == 403


def test_create_uses_persisted_reply_and_keeps_password_only_as_hash(monkeypatch):
    saved = []
    monkeypatch.setattr(service.MessageShareDB, 'source_message', lambda session, message_id: source('', images=[{'file_id': 'file_image'}]))
    monkeypatch.setattr(service.MessageShareDB, 'create', saved.append)
    result = service.create_share({'session_id': 's', 'user_id': 'u', 'tenant_id': 't'}, 'projected:assistant', 'viewer-test')
    assert result['share_path'].endswith(result['share_key'])
    assert saved[0]['message_id'] == 'message-final'
    assert saved[0]['password_hash'] != 'viewer-test'
    assert service.verify_password('viewer-test', saved[0]['password_hash'])
    assert 'password' not in result


def test_nonterminal_runner_cannot_publish_still_changing_reply(monkeypatch):
    monkeypatch.setattr(service.MessageShareDB, 'source_message', lambda *args:
        {**source('partial', runner_id='runner-a'), 'runner_status': 'running'})
    with pytest.raises(HTTPException) as waiting:
        service.create_share({'session_id': 's', 'user_id': 'u'}, 'message-final')
    assert waiting.value.status_code == 422


def test_shared_kb_image_freezes_verified_resource_owner(monkeypatch):
    saved = []
    monkeypatch.setattr(service.MessageShareDB, 'source_message', lambda *args:
        source('', images=[{'file_id': 'file_shared_kb'}]))
    monkeypatch.setattr(service.MessageShareDB, 'resource_grant', lambda *args:
        {'tenant_id': 'knowledge-owner', 'allowed': True})
    monkeypatch.setattr(service.MessageShareDB, 'create', saved.append)
    service.create_share({'session_id': 's', 'user_id': 'u', 'tenant_id': 'reader'}, 'm')
    shared = saved[0]
    resolve = Mock(return_value={'path': '/test/image.png', 'name': 'image.png'})
    monkeypatch.setattr(service, 'resolve_scoped_uploaded_file', resolve)
    monkeypatch.setattr(service, 'get_file_asset', lambda *args: None)
    service.shared_file(shared, 'asset_0', service.view_share(shared)['access_ticket'])
    assert resolve.call_args.args[:2] == ('file_shared_kb', 'knowledge-owner')


def test_foreign_asset_without_shared_kb_grant_is_not_publishable(monkeypatch):
    monkeypatch.setattr(service.MessageShareDB, 'source_message', lambda *args:
        source('', images=[{'file_id': 'file_other'}]))
    monkeypatch.setattr(service.MessageShareDB, 'resource_grant', lambda *args:
        {'tenant_id': 'other', 'allowed': False})
    with pytest.raises(HTTPException) as denied:
        service.create_share({'session_id': 's', 'user_id': 'u', 'tenant_id': 'reader'}, 'm')
    assert denied.value.status_code == 403


def test_schema_added_to_both_supported_migration_paths():
    root = Path(__file__).resolve().parents[3]
    for relative in ('deploy/init-postgres.sql', 'deploy/db_update.yaml'):
        assert 'CREATE TABLE IF NOT EXISTS chat_message_shares' in (root / relative).read_text(encoding='utf-8')
