"""Real CLI plus one Redis-method IO fault after the mandatory PG wait.

This is explicitly narrow transport-fault DI, not a real Redis outage. The
original suspension, Browser, PG binding and observed Redis read all execute.
"""
import json
from pathlib import Path
import runpy
import sys


def install(boundary, report_path):
    from src.db.database import get_db_connection
    from src.tools.browser.resume_store import ResumeStore
    report = Path(report_path)

    def committed(assistance_id):
        with get_db_connection() as connection:
            connection.execute('SELECT runner_id,runner_wait_id FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                               (assistance_id,))
            row = connection.fetchone()
        return bool(row and row['runner_id'] and row['runner_wait_id'])

    def fault(assistance_id, redis_read):
        assert committed(assistance_id)
        report.write_text(json.dumps({'boundary': boundary, 'mandatory_wait_committed': True,
                                      'original_redis_record_read': redis_read}))
        raise OSError('fixture_postwait_redis_io_failure')

    original_get = ResumeStore.get_assistance

    async def get_assistance(self, tenant_id, assistance_id):
        record = await original_get(self, tenant_id, assistance_id)
        if boundary == 'get' and committed(assistance_id):
            fault(assistance_id, record is not None)
        return record

    ResumeStore.get_assistance = get_assistance
    original_bind = ResumeStore.bind_agent

    async def bind_agent(self, record, agent_name):
        if boundary == 'bind' and committed(record.assistance_id):
            # get_assistance already returned this actual original Redis record.
            fault(record.assistance_id, True)
        return await original_bind(self, record, agent_name)

    ResumeStore.bind_agent = bind_agent
    from src.services.agent_runner.runtime.tools import ToolDispatcher
    original_dispatch = ToolDispatcher.dispatch

    async def dispatch(self, state, call):
        try:
            async for item in original_dispatch(self, state, call):
                yield item
        except BaseException as error:
            if report.exists():
                facts = json.loads(report.read_text())
                facts['exception_class'] = type(error).__name__
                facts['authoritative_failure'] = bool(getattr(error, 'authoritative_storage_failure', False))
                facts['expected_stable_code'] = str(error) == 'TOOL_SUSPENSION_STORAGE_FAILED'
                report.write_text(json.dumps(facts))
            raise

    ToolDispatcher.dispatch = dispatch


if __name__ == '__main__':
    boundary, report_path, *arguments = sys.argv[1:]
    assert boundary in {'get', 'bind'}
    install(boundary, report_path)
    sys.argv = ['src.services.agent_runner.worker', *arguments]
    runpy.run_module('src.services.agent_runner.worker', run_name='__main__')
