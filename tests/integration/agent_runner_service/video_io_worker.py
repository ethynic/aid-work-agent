"""Normal worker composition with only the remote video service IO replaced."""
import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--report",required=True)
    args=parser.parse_args()
    from src.video_agent import chat_integration
    from src.tools.context import current_tool_execution_context
    from src.services.agent_runner.worker import run_worker

    class ExternalVideoService:
        async def handle_user_message(self, **values):
            context=current_tool_execution_context()
            params=values.pop("params")
            reported={**values,"params":dict(vars(params)),"trusted_context":{
                "tenant_id":context.tenant_id,"user_id":context.user_id,
                "session_id":context.session_id,"request_data":{
                    "video_params":dict(context.request_data["video_params"])}}}
            Path(args.report).write_text(json.dumps(reported))
            return {"message":"fictional-remote-video-task-accepted","cards":[]}

    chat_integration.get_video_chat_service=lambda **kwargs: ExternalVideoService()
    asyncio.run(run_worker(SimpleNamespace(worker_id="fixture-video-worker",once=True,max_tasks=None)))


if __name__=="__main__":
    main()
