"""Prepared same-origin built Web app and original real API composition.

No src.main scheduler/channel lifespan or artificial identity endpoint is used.
The isolated fixture must provide actual PG principals/permissions and original
gateway configuration. Unknown API paths remain real 404s, never SPA success.
"""
from pathlib import Path


def create_app():
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from src.api.agent_runner_web import create_app as gateway
    from src.api.browser_runs import agent_router as agent_continuations
    from src.api.browser_runs import router as browser
    from src.saas.api.tenant_auth import router as auth
    from src.saas.api.permissions import router as permissions
    from src.saas.api.billing_balance import router as billing
    from src.services.content_sync.api import router as sources

    app = gateway()
    for router in (browser, agent_continuations, auth, permissions, billing, sources):
        app.include_router(router)
    built = Path('/app/frontend/dist')
    if not (built / 'index.html').is_file() or not (built / 'assets').is_dir():
        raise RuntimeError('CARD_UI_ACTUAL_BUILD_UNAVAILABLE')
    app.mount('/assets', StaticFiles(directory=built / 'assets'), name='actual-built-assets')

    @app.get('/{path:path}', include_in_schema=False)
    async def actual_spa(path: str):
        if path.startswith('api/') or path.startswith('internal/'):
            raise HTTPException(404, 'NOT_FOUND')
        # Only root/ordinary tenant UI paths, no arbitrary disk-path resolver.
        if path and not path.startswith('t/'):
            raise HTTPException(404, 'NOT_FOUND')
        return FileResponse(built / 'index.html')

    return app
