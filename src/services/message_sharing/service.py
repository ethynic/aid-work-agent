"""Prepare a Web reply and its images/files for the same read-only renderer."""

import copy
import json
import re
import secrets
from datetime import datetime, timezone
from html import unescape
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlsplit

from fastapi import HTTPException

from src.core.download_ticket import issue_download_ticket, validate_download_ticket
from src.core.storage import resolve_scoped_uploaded_file
from src.db.message_share_db import MessageShareDB
from src.db.models import hash_password, verify_password
from src.services.files.asset_store import get_file_asset
from src.config.settings import settings


INTERNAL_FILE = re.compile(r'(?:(?:https?:)?//[^/\s<>"\'()]+)?/api/files/(?P<id>(?:[\w.-]|%[\da-fA-F]{2})+)(?:/download)?(?:\?[^\s<>"\'()\]]*)?')
FILE_SCHEME = re.compile(r'file_id:(?P<id>[\w.-]+)')
SHARE_FILE = re.compile(r'share-file:(?P<id>asset_\d+)')
ACCESS_TTL_SECONDS = 12 * 60 * 60


def fail(status, code, message):
    raise HTTPException(status, detail={'success': False, 'code': code, 'error': message, 'debug': code})


def share_path(share_key):
    return '/api/public/reply-shares/' + quote(share_key, safe='')


def _external_url(value, origin=''):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.netloc and parsed.scheme in ('', 'http', 'https') and not _file_reference(value, origin):
        return 'https:' + value if not parsed.scheme else value
    return None


def _file_reference(value, origin=''):
    value = unescape(value)
    if value.startswith('file_id:'):
        return unquote(value[8:])
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    match = INTERNAL_FILE.fullmatch(unquote(parsed.path))
    hosts = {urlsplit(item).netloc.lower() for item in (settings.app.public_base_url or '', origin) if item}
    if match and (not parsed.netloc or parsed.netloc.lower() in hosts):
        return match['id']
    return None


def _text_references(text):
    spans = [(match.start(), match.end(), match[0])
        for pattern in (INTERNAL_FILE, FILE_SCHEME) for match in pattern.finditer(text)]
    for pattern in (r'''(?:src|href|poster)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))''',
                    r'(?<=\]\()([^\s)]+)|url\(\s*["\']?([^\s)\'";]+)'):
        for match in re.finditer(pattern, text, re.IGNORECASE):
            group = next((index for index, value in enumerate(match.groups(), 1) if value), None)
            if group:
                spans.append((match.start(group), match.end(group), match[group]))
    spans += [(match.start(), match.end(), match[0])
        for match in re.finditer(r'(?:https?:)?//[^\s<>"\'()\]]+', text, re.IGNORECASE)]
    # A local-looking substring inside a whole external URL is never a separate reference.
    result = []
    for start, end, value in sorted(spans, key=lambda item: (item[0], -item[1])):
        if not result or start >= result[-1][1]:
            result.append((start, end, value))
    return result


def _rewrite_references(text, replace):
    for start, end, value in reversed(_text_references(text)):
        text = text[:start] + replace(value) + text[end:]
    return text


def reply_snapshot(source, origin=''):
    """Copy only visible reply fields, keeping internal resource IDs server-side."""
    metadata = source.get('metadata') or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    files = {}
    aliases = {}

    def resource(file_id, value=None):
        value = value or {}
        url = value.get('download_url') or value.get('url') or ''
        internal_id = _file_reference(url, origin) if isinstance(url, str) else None
        external = _external_url(url, origin)
        file_id = internal_id or file_id
        identity = str(external or file_id or url)
        if not identity:
            return None
        if identity not in aliases:
            alias = 'asset_' + str(len(aliases))
            aliases[identity] = alias
            files[alias] = {'file_id': file_id, 'url': external}
        return aliases[identity]

    def items(values, fields):
        result = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            alias = resource(item.get('file_id'), item)
            if not alias:
                continue
            visible = {key: item[key] for key in fields if key in item}
            visible['file_id'] = alias
            if 'download_url' in fields:
                visible['download_url'] = 'share-file:' + alias
            result.append(visible)
        return result

    message = {'role': 'assistant', 'content': str(source.get('content') or '')}
    message['images'] = items(metadata.get('images'), ('display_name', 'width', 'height', 'mime_type',
        'size_bytes', 'source', 'usage', 'placement', 'download_url'))
    for image in message['images']:
        image['placement'] = image.get('placement') or metadata.get('imagesPlacement') or 'after_text'
    message['attachments'] = items(metadata.get('attachments'), ('name', 'size', 'mime_type', 'type'))
    downloadable = list(metadata.get('downloadableFiles') or [])
    existing = {item.get('file_id') for item in downloadable if isinstance(item, dict)}
    for event in metadata.get('progressMessages') or []:
        if not isinstance(event, dict) or event.get('type') != 'tool_result' or not event.get('success'):
            continue
        value = event.get('result') or {}
        if not isinstance(value, dict) or not value.get('file_id') or value.get('visible') is False or value['file_id'] in existing:
            continue
        existing.add(value['file_id'])
        downloadable.append({'file_id': value['file_id'],
            'file_name': value.get('download_file_name') or value.get('file_name') or '未命名文件',
            'file_size': value.get('file_size') or 0, 'download_url': value.get('download_url') or '',
            'mime_type': value.get('mime_type') or ''})
    message['downloadableFiles'] = items(downloadable, ('file_name', 'file_size', 'mime_type', 'download_url'))

    def replace_reference(value):
        file_id = _file_reference(value, origin)
        return 'share-file:' + resource(file_id) if file_id else value

    message['content'] = _rewrite_references(message['content'], replace_reference)
    if metadata.get('cancelled'):
        message['cancelled'] = True
    created = source.get('created_at')
    if isinstance(created, datetime):
        message['timestamp'] = int((created if created.tzinfo else created.replace(tzinfo=timezone.utc)).timestamp() * 1000)
    if not message['content'].strip() and not any(message[key] for key in ('images', 'attachments', 'downloadableFiles')):
        fail(422, 'EMPTY_REPLY', '这条回复没有可分享的内容')
    return {'message': message, 'files': files, 'origin': origin}


def _bind_resources(snapshot, tenant):
    """Keep the selected document's own linked media usable in existing previews."""
    files = snapshot['files']
    pending = list(files)
    for alias in pending:
        reference = files[alias]
        if reference.get('url'):
            continue
        file_id = reference['file_id']
        inherited_owner = reference.get('tenant_id', tenant)
        grant = MessageShareDB.resource_grant(file_id, tenant)
        metadata = get_file_asset(file_id)
        owner = grant['tenant_id'] if grant and grant['allowed'] else inherited_owner
        if grant and not grant['allowed'] and grant['tenant_id'] is not None and grant['tenant_id'] != inherited_owner:
            fail(403, 'FILE_ACCESS_DENIED', '回复中的文件已无权访问，无法分享')
        info = resolve_scoped_uploaded_file(file_id, owner, metadata=metadata)
        if not info and grant and grant['tenant_id'] is None:
            anonymous = resolve_scoped_uploaded_file(file_id, None, metadata=metadata)
            if anonymous:
                owner, info = None, anonymous
        reference['tenant_id'] = owner
        if not info or Path(info['path']).suffix.lower() not in ('.md', '.markdown', '.html', '.htm', '.svg', '.css'):
            continue
        try:
            text = Path(info['path']).read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError):
            continue
        links = {}
        for _, _, value in _text_references(text):
            child_id = _file_reference(value, snapshot.get('origin', ''))
            if not child_id:
                try:
                    parsed = urlsplit(unescape(value))
                except ValueError:
                    continue
                if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith(('/', '#')):
                    continue
                try:
                    child_path = (Path(info['path']).parent / unquote(parsed.path)).resolve()
                except (OSError, ValueError):
                    continue
                child_id = child_path.name
            # Missing or foreign internal media must still use the share gate;
            # leaving a legacy public URL in the document bypasses that gate.
            target = next((key for key, item in files.items() if item.get('file_id') == child_id and not item.get('url')), None)
            if target is None:
                target = 'asset_' + str(len(files))
                files[target] = {'file_id': child_id, 'url': None, 'tenant_id': owner}
                pending.append(target)
            links[value] = target
        reference['links'] = links


def create_share(session, message_id, password=None, origin=''):
    source = MessageShareDB.source_message(session, message_id)
    if not source:
        fail(404, 'REPLY_NOT_FOUND', '回复不存在或暂未保存，请稍后重试')
    metadata = source.get('metadata') or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    if metadata.get('runner_id') and source.get('runner_status') not in ('completed', 'failed', 'cancelled'):
        fail(422, 'REPLY_NOT_READY', '请在回复生成完成后分享')
    share_key = secrets.token_urlsafe(24)
    snapshot = reply_snapshot(source, origin)
    _bind_resources(snapshot, session.get('tenant_id'))
    MessageShareDB.create({'share_key': share_key, 'tenant_id': session.get('tenant_id'),
        'user_id': session['user_id'], 'session_id': session['session_id'], 'message_id': source['message_id'],
        'reply_data': snapshot, 'password_hash': hash_password(password) if password else None})
    return {'share_key': share_key, 'share_path': '/share/reply/' + share_key}


def get_share(share_key):
    record = MessageShareDB.get(share_key)
    if not record:
        fail(404, 'SHARE_NOT_FOUND', '分享不存在或已不可用')
    return record


def view_share(record, password=None):
    if record.get('password_hash'):
        if not password:
            return {'state': 'password_required'}
        if not verify_password(password, record['password_hash']):
            fail(403, 'INCORRECT_PASSWORD', '密码不正确，请重新输入')
    key = record['share_key']
    base = share_path(key)
    ticket = issue_download_ticket(base, ttl_seconds=ACCESS_TTL_SECONDS)
    message = copy.deepcopy(record['reply_data']['message'])

    def resource_url(match):
        return base + '/files/' + match['id'] + '?' + urlencode({'ticket': ticket})

    message['content'] = SHARE_FILE.sub(resource_url, message['content'])
    for name in ('images', 'downloadableFiles'):
        for item in message.get(name) or []:
            item['download_url'] = SHARE_FILE.sub(resource_url, item.get('download_url') or '')
    return {'state': 'ready', 'message': message, 'access_ticket': ticket}


def shared_file(record, alias, ticket, download=False):
    try:
        access = validate_download_ticket(ticket, share_path(record['share_key']))
    except (ValueError, TypeError, OverflowError):
        access = None
    if access is None:
        fail(403, 'SHARE_ACCESS_REQUIRED', '请先打开分享页面并验证访问权限')
    reference = (record['reply_data'].get('files') or {}).get(alias)
    if not reference:
        fail(404, 'FILE_NOT_IN_REPLY', '文件不属于这条分享回复')
    if reference.get('url'):
        return {'url': reference['url']}
    file_id = reference['file_id']
    info = resolve_scoped_uploaded_file(file_id, reference.get('tenant_id', record.get('tenant_id')), metadata=get_file_asset(file_id))
    if not info:
        fail(404, 'FILE_UNAVAILABLE', '文件不存在或已被清理')
    if not info.get('mime_type') or info['mime_type'] == 'application/octet-stream':
        info = {**info, 'mime_type': guess_type(info['path'])[0] or 'application/octet-stream'}
    if not download and reference.get('links'):
        try:
            text = Path(info['path']).read_text(encoding='utf-8-sig')
            def replace_reference(value):
                target = reference['links'].get(value)
                return share_path(record['share_key']) + '/files/' + target + '?' + urlencode({'ticket': ticket}) if target else value

            text = _rewrite_references(text, replace_reference)
            return {**info, 'text': text}
        except (OSError, UnicodeError):
            pass
    return info
