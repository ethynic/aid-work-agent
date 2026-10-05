"""Serve actual main file delivery handlers without master app startup jobs.

This is a narrow route-composition fixture, not a full master startup or an
alternate file resolver. Business/log pools use the normal isolated lifecycle.
"""
import argparse
from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn


def application():
    from src.db.database import init_postgres_pool, close_postgres_pool, init_logs_pool, close_logs_pool
    from src.main import download_file

    @asynccontextmanager
    async def lifespan(app):
        init_postgres_pool()
        init_logs_pool()
        try:
            yield
        finally:
            close_logs_pool()
            close_postgres_pool()
    app = FastAPI(lifespan=lifespan)
    app.add_api_route('/api/files/{file_id}/download', download_file, methods=['GET'])
    @app.get('/health')
    async def health():
        return {'status': 'ok'}
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(application(), host='127.0.0.1', port=args.port, log_level='warning')
