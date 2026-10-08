"""Invoke the real opt-in main route without the legacy app startup side effects."""
import argparse
import asyncio
import json
import os
from pathlib import Path


async def run(arguments):
    from src.db.database import init_postgres_pool,close_postgres_pool,init_logs_pool,close_logs_pool
    from starlette.requests import Request
    init_postgres_pool()
    try:
        init_logs_pool()
        from src.main import chat
        payload=Path(arguments.input).read_bytes()
        delivered=False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered=True
                return {"type":"http.request","body":payload,"more_body":False}
            return {"type":"http.disconnect"}
        request=Request({"type":"http","method":"POST","path":"/api/chat","headers":[
            (b"authorization",("Bearer "+os.environ["RUNNER_TEST_OPAQUE_TOKEN"]).encode()),
            (b"x-agentrunner-transport",b"runner"),(b"content-type",b"application/json")],
            "query_string":b"","scheme":"http","server":("127.0.0.1",1),"client":("127.0.0.1",1)},receive)
        response=await chat(request)
        Path(arguments.report).write_text(json.dumps({"status":response.status_code,"body":json.loads(response.body)}))
    finally:
        close_logs_pool();close_postgres_pool()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--input",required=True)
    parser.add_argument("--report",required=True)
    asyncio.run(run(parser.parse_args()))
