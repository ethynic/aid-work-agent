"""Normal RuntimeFactory DI: short timeout on the actual registered boss proxy.

No method/clock/result replacements. The real bind persists its native deadline,
real polling elapses, and a later undecorated CLI resumes that same stored fact.
"""
import asyncio
import signal


def main():
    from src.config.settings import settings
    from src.db.database import init_postgres_pool,close_postgres_pool,init_logs_pool,close_logs_pool
    from src.services.agent_runner.repository import RunnerRepository
    from src.services.agent_runner.worker import RunnerWorker,RuntimeFactory
    from src.local_tools.proxy_tool import LocalToolProxyTool

    class TimedFactory(RuntimeFactory):
        async def create(self,row,principal,control):
            runtime=await super().create(row,principal,control)
            proxy=runtime.resources.tool_registry.get_tool('boss_select_job')
            assert isinstance(proxy,LocalToolProxyTool)
            assert runtime.resources.tool_executor.registry.get_tool('boss_select_job') is proxy
            proxy.timeout_seconds=1
            return runtime

    async def run():
        await asyncio.to_thread(init_postgres_pool)
        try:
            await asyncio.to_thread(init_logs_pool)
            await asyncio.to_thread(RunnerRepository().assert_schema)
            worker=RunnerWorker(settings.agent_runner,worker_id='fixture-local-deadline-owner',runtime_factory=TimedFactory())
            loop=asyncio.get_running_loop()
            for signum in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(signum,worker.stop)
            await worker.run(once=True)
        finally:
            await asyncio.to_thread(close_logs_pool)
            await asyncio.to_thread(close_postgres_pool)
    asyncio.run(run())


if __name__=='__main__':main()
