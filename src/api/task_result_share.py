"""Public read-only view of one scheduled task's terminal result."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from src.api.message_share import content_disposition_header
from src.api.rate_limit import SlidingWindowRateLimiter
from src.services.auth_service import AuthSubjectError, fresh_web_subject
from src.services.message_sharing.service import fail
from src.services.task_result_sharing.service import task_result_file, view_task_result


router = APIRouter(tags=['定时任务结果分享'])
_view_limiter = SlidingWindowRateLimiter(max_requests=60)
_headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'X-Robots-Tag': 'noindex, nofollow', 'X-Content-Type-Options': 'nosniff'}


def _optional_viewer(authorization, requested_tenant):
    if not authorization:
        return None
    try:
        user = fresh_web_subject(authorization)
        if user.get('role') == 'platform_admin' and requested_tenant:
            return {**user, 'tenant_id': requested_tenant}
        return user
    except AuthSubjectError:
        return None


@router.get('/api/public/task-result-shares/{share_key}')
async def read_task_result(share_key: str, request: Request):
    authorization = request.headers.get('Authorization', '')
    remote = request.client.host if request.client else 'unknown'
    if not await asyncio.to_thread(_view_limiter.is_allowed, 'task-result-view:' + remote):
        fail(429, 'SHARE_RATE_LIMITED', '访问过于频繁，请稍后再试')
    viewer = await asyncio.to_thread(_optional_viewer, authorization,
                                     request.headers.get('X-Tenant-Id'))
    result = await asyncio.to_thread(view_task_result, share_key, viewer)
    return JSONResponse(result, headers=_headers)


@router.get('/api/public/task-result-shares/{share_key}/files/{alias}')
async def read_task_result_file(share_key: str, alias: str, ticket: str = '', download: bool = False):
    info = await asyncio.to_thread(task_result_file, share_key, alias, ticket, download)
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
