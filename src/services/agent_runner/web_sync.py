"""Legacy JSON presentation over durable acceptance and read-only observation."""

import asyncio
from urllib.parse import quote

from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .contracts import RunnerError
from .web_client import RunnerServiceClient


async def synchronous_web_reply(request, body=None, *, config=None, client=None):
    # Imported at the application edge to avoid coupling service transports to
    # main's legacy Agent/router or duplicating the Web input normalization.
    from src.api.agent_runner_web import WebSubmission, error_response
    from src.config.settings import settings
    config = config or settings.agent_runner
    client = client or RunnerServiceClient(config)
    try:
        try:
            body = body or WebSubmission.model_validate(await request.json())
        except (ValidationError, ValueError):
            raise RunnerError('INVALID_REQUEST', 422) from None
        arguments = {'authorization':request.headers.get('Authorization',''),
                     'tenant':request.headers.get('X-Tenant-Id')}
        accepted = await client.request('POST', '/v1/runners', **arguments,
            body=body.runner_request().model_dump(mode='json'), accept_new=config.web_enabled)
        row = accepted['runner']
        if not isinstance(row.get('profile_id'),str) or not row['profile_id']:
            raise RunnerError('RUNNER_SERVICE_INVALID_RESPONSE', 502)
        while row['status'] in ('queued','running','finalizing'):
            if await request.is_disconnected():
                # The worker still owns the accepted runner. No cancellation,
                # checkpoint update or claim release belongs to this connection.
                return JSONResponse(status_code=499, content={'success':False,'error':'REQUEST_DETACHED',
                    'runner_id':row['runner_id'],'session_id':row['session']['session_id']})
            await asyncio.sleep(0.5)
            row = (await client.request('GET','/v1/runners/'+quote(row['runner_id'],safe=''), **arguments))['runner']
        waiting = row['snapshot'].get('waiting') or {}
        while isinstance(waiting.get('child_wait'),dict):
            waiting = waiting['child_wait']
        result = row.get('result') or {}
        reply = {'success':row['status'] in ('completed','cancelled','waiting'),
                 'response':result.get('output') or row['snapshot'].get('output') or waiting.get('question') or '',
                 'session_id':row['session']['session_id'],
                 'agent_type':'master' if row['profile_id']=='main' else 'standalone',
                 'runner_id':row['runner_id'],'status':row['status']}
        if not reply['success']:
            reply['error'] = result.get('error_code') or 'RUNNER_RESUME_REQUIRED'
        return JSONResponse(content=reply)
    except RunnerError as error:
        return error_response(error)
