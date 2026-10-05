"""Actual legacy Browser tool in original API loop, with only external model IO.

No native Owner is manufactured. The real ToolContext is explicit, the original
BrowserAutomationTool creates legacy run/assistance records and original Hub.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path


def create_app():
    from .browser_observation_api import create_app as original_app
    app = original_app()
    infrastructure = app.router.lifespan_context
    data = json.loads(Path(os.environ['AID_TEST_LEGACY_BROWSER_INPUT']).read_text())
    report = Path(data['report'])

    async def original_tool():
        from src.tools.context import ExecutionContextFactory, tool_execution_scope
        from src.tools.browser.automation_tool import BrowserAutomationTool
        from src.core.tool_suspension import ToolSuspension
        context = ExecutionContextFactory.for_agent_call(tenant_id=data['tenant_id'],
            user_id=data['user_id'],session_id=data['session_id'],channel='web',
            agent_execution_id=data['execution_id'],tool_call_id=data['call_id'],
            request_data={},env_vars={},infer_legacy_identity=False)
        try:
            with tool_execution_scope(context):
                result = await BrowserAutomationTool().execute(task=data['task'],url=data['url'],headless=True)
            if not isinstance(result, ToolSuspension):
                report.write_text(json.dumps({'legacy_suspended':False}))
                return
            report.write_text(json.dumps({'legacy_suspended':True,
                'run_id':result.run_id,'assistance_id':result.assistance_id}))
        except Exception as error:
            # Never serialize tool content, URLs or credential-bearing logs.
            report.write_text(json.dumps({'legacy_suspended':False,'class':type(error).__name__}))

    @asynccontextmanager
    async def lifecycle(instance):
        async with infrastructure(instance):
            task = asyncio.create_task(original_tool())
            try:
                yield
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task,return_exceptions=True)
                from src.tools.browser.run_manager import close_all_active_browser_managers
                from src.tools.browser.human_control import stop_all_completion_monitors
                await close_all_active_browser_managers('fixture_shutdown')
                await stop_all_completion_monitors()

    app.router.lifespan_context = lifecycle
    return app
