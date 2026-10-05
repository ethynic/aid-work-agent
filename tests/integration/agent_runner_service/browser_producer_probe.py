"""Fresh real worker with observational Browser boundary wrappers only.

Every wrapped operation calls its original method and returns its real result.
No state/result/owner/clock is substituted. Reports contain IDs and booleans,
never page text, model bodies, endpoints, credentials or Redis epoch tokens.
This is narrow observation DI, distinct from the undecorated CLI happy test.
"""
import json
from pathlib import Path
import runpy
import sys


def install(report_path):
    from src.db.database import get_db_connection
    from src.tools.browser.run_store import RedisRunStore
    from src.tools.browser.executor.local import LocalPlaywrightExecutor
    from src.tools.browser.human_control import HumanControlCoordinator
    from src.services.agent_runner.browser_binding import BrowserOwnerRepository
    from src.tools.browser.page_ops import PageOps

    report = Path(report_path)
    events = []

    def observed(boundary, **facts):
        events.append({'boundary': boundary, **facts})
        temporary = report.with_suffix('.pending')
        temporary.write_text(json.dumps(events))
        temporary.replace(report)

    def observe_page_method(name):
        original = getattr(PageOps, name)

        async def page_method(self, *arguments, **keywords):
            try:
                result = await original(self, *arguments, **keywords)
            except BaseException as error:
                observed('page_' + name, exception_class=type(error).__name__)
                raise
            allowed = {'SNAPSHOT_FAILED','SEQ_REJECTED','NAVIGATE_FAILED','COMMAND_TIMEOUT',
                       'WORKER_COMMAND_FAILED','CONTENT_FAILED','OWNER_LEASE_LOST','OWNER_FENCE_FAILED'}
            code = result.get('error')
            observed('page_' + name, success=bool(result.get('success')),
                     code=code if isinstance(code, str) and code in allowed else ('OTHER_ERROR' if code else None))
            return result

        setattr(PageOps, name, page_method)

    observe_page_method('navigate')
    observe_page_method('take_snapshot')

    original_create = RedisRunStore.create

    async def create(self, record):
        with get_db_connection() as connection:
            connection.execute('SELECT runner_id,runtime_state FROM bs_browser_runs WHERE tenant_id=%s AND run_id=%s',
                               (record.tenant_id, record.run_id))
            row = connection.fetchone()
        before = await self.get(record.tenant_id, record.run_id)
        observed('before_redis_publish', run_id=record.run_id,
                 mandatory_pg_starting=bool(row and row['runner_id'] and row['runtime_state'] == 'starting'),
                 redis_absent=before is None, distributed=self.distributed)
        return await original_create(self, record)

    RedisRunStore.create = create
    original_start = LocalPlaywrightExecutor.start

    async def start(self, spec):
        result = await original_start(self, spec)
        observed('actual_executor_start', run_id=spec.run_id, result_ok=result.status.value == 'ok',
                 pid=self._process.pid if self._process else None)
        return result

    LocalPlaywrightExecutor.start = start
    original_close = LocalPlaywrightExecutor.close

    async def close(self, reason):
        pid = self._process.pid if self._process else None
        run_id = self.run_id
        result = await original_close(self, reason)
        observed('actual_executor_close', run_id=run_id, pid=pid,
                 result_closed=result.closed, resource_confirmed=self.resource_close_confirmed)
        return result

    LocalPlaywrightExecutor.close = close
    original_wait = BrowserOwnerRepository.bind_wait

    def bind_wait(self, attempt, revision, **arguments):
        row = original_wait(self, attempt, revision, **arguments)
        assistance = arguments['assistance']
        with get_db_connection() as connection:
            connection.execute('SELECT runner_id,runner_wait_id FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                               (assistance['assistance_id'],))
            saved = connection.fetchone()
        observed('mandatory_wait_committed', runner_id=row['runner_id'], run_id=assistance['run_id'],
                 assistance_id=assistance['assistance_id'], wait_id=saved['runner_wait_id'] if saved else None,
                 same_runner=bool(saved and saved['runner_id'] == attempt.runner_id))
        return row

    BrowserOwnerRepository.bind_wait = bind_wait
    original_suspend = HumanControlCoordinator.suspend

    async def suspend(self, **arguments):
        result = await original_suspend(self, **arguments)
        with get_db_connection() as connection:
            connection.execute('SELECT runner_id,runner_wait_id FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                               (result.assistance_id,))
            saved = connection.fetchone()
        observed('before_suspension_return', run_id=result.run_id, assistance_id=result.assistance_id,
                 mandatory_wait_exists=bool(saved and saved['runner_id'] and saved['runner_wait_id']))
        return result

    HumanControlCoordinator.suspend = suspend


if __name__ == '__main__':
    path, *worker_arguments = sys.argv[1:]
    install(path)
    sys.argv = ['src.services.agent_runner.worker', *worker_arguments]
    runpy.run_module('src.services.agent_runner.worker', run_name='__main__')
