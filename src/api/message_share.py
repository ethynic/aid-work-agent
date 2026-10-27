"""Share a selected Web reply, including its images and downloadable files."""

import asyncio
import hashlib
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from src.api.rate_limit import SlidingWindowRateLimiter
from src.api.web_subject import owned_web_session
from src.services.message_sharing.service import create_share, fail, get_share, shared_file, view_share


def content_disposition_header(name: str, attachment: bool) -> str:
    """Starlette 对非 ASCII 文件名只发 filename*（RFC 5987），部分国产移动内核
    解析不出文件名会直接放弃下载；补 ASCII filename fallback（RFC 6266）。"""
    kind = 'attachment' if attachment else 'inline'
    fallback = ''.join(ch for ch in name if ch.isascii() and ch not in '"\\\r\n').strip() or 'download'
    if fallback == name:
        return f'{kind}; filename="{name}"'
    return f'{kind}; filename="{fallback}"; filename*=utf-8\'\'{quote(name, safe="")}'


router = APIRouter(tags=['回复分享'])
_password_limiter = SlidingWindowRateLimiter(max_requests=10)
_headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'X-Robots-Tag': 'noindex, nofollow', 'X-Content-Type-Options': 'nosniff'}


class SharePassword(BaseModel):
    password: str | None = Field(default=None, min_length=1, max_length=64)

    class Config:
        extra = 'forbid'


@router.post('/api/sessions/{session_id}/messages/{message_id}/share')
async def create_reply_share(session_id: str, message_id: str, body: SharePassword, request: Request):
    session = await asyncio.to_thread(owned_web_session, request.headers.get('Authorization', ''),
                                     request.headers.get('X-Tenant-Id'), session_id)
    # bcrypt accepts at most 72 UTF-8 bytes, independently of character count.
    if body.password and len(body.password.encode('utf-8')) > 72:
        fail(422, 'INVALID_PASSWORD', '密码长度过长，请缩短后重试')
    result = await asyncio.to_thread(create_share, session, message_id, body.password, str(request.base_url))
    return JSONResponse(result, headers=_headers)


@router.post('/api/public/reply-shares/{share_key}/view')
async def read_reply_share(share_key: str, body: SharePassword, request: Request):
    record = await asyncio.to_thread(get_share, share_key)
    if record.get('password_hash') and body.password:
        remote = request.client.host if request.client else 'unknown'
        limit_key = 'reply-share:' + hashlib.sha256((share_key + ':' + remote).encode()).hexdigest()
        if not await asyncio.to_thread(_password_limiter.is_allowed, limit_key):
            fail(429, 'PASSWORD_RATE_LIMITED', '密码尝试过于频繁，请稍后再试')
    result = await asyncio.to_thread(view_share, record, body.password)
    return JSONResponse(result, headers=_headers)


@router.get('/api/public/reply-shares/{share_key}/files/{file_id}')
async def read_shared_file(share_key: str, file_id: str, ticket: str = '', download: bool = False):
    record = await asyncio.to_thread(get_share, share_key)
    info = await asyncio.to_thread(shared_file, record, file_id, ticket, download)
    if info.get('url'):
        return RedirectResponse(info['url'], status_code=307, headers=_headers)
    headers = dict(_headers)
    if info.get('mime_type') in ('text/html', 'application/xhtml+xml', 'image/svg+xml') or Path(info['path']).suffix.lower() in ('.html', '.htm', '.svg'):
        headers['Content-Security-Policy'] = 'sandbox allow-scripts allow-downloads'
    if 'text' in info:
        return Response(info['text'], media_type=info.get('mime_type') or 'text/plain', headers=headers)
    headers['Content-Disposition'] = content_disposition_header(info.get('name') or 'download', download)
    return FileResponse(info['path'], media_type=info.get('mime_type') or 'application/octet-stream',
        filename=info.get('name') or 'download', content_disposition_type='attachment' if download else 'inline',
        headers=headers)
