"""Independent AgentRunner API entry point. Does not migrate or execute on startup."""

import argparse
import asyncio
from contextlib import asynccontextmanager


def create_app():
    from src.config.settings import settings
    if settings.agent_runner.enabled:
        if not settings.agent_runner.peers or not settings.agent_runner.web_service_token:
            raise RuntimeError("AGENT_RUNNER_SERVICE_AUTH_REQUIRED")
    from .api import create_app as build_app
    app = build_app()

    @asynccontextmanager
    async def infrastructure(_app):
        owns_pool = False
        try:
            if settings.agent_runner.enabled:
                from src.db.database import get_postgres_pool, init_postgres_pool
                from .repository import RunnerRepository
                if get_postgres_pool() is None:
                    await asyncio.to_thread(init_postgres_pool)
                    owns_pool = True
                await asyncio.to_thread(RunnerRepository().assert_schema)
            yield
        finally:
            if owns_pool:
                from src.db.database import close_postgres_pool
                await asyncio.to_thread(close_postgres_pool)

    app.router.lifespan_context = infrastructure
    return app


def main():
    from src.config.settings import settings
    import uvicorn
    parser = argparse.ArgumentParser(description="Run the independent AgentRunner HTTP API")
    parser.add_argument("--host", default=settings.agent_runner.host)
    parser.add_argument("--port", default=settings.agent_runner.port, type=int)
    arguments = parser.parse_args()
    uvicorn.run(create_app(), host=arguments.host, port=arguments.port, access_log=False)


if __name__ == "__main__":
    main()
