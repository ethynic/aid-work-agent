"""Credential-safe observations of original cancellation/IPC methods only."""
import asyncio
import json
from pathlib import Path
import re
import sys
import threading


def install_cancel_observer(destination):
    from src.services.agent_runner.worker import RuntimeFactory
    from src.services.agent_runner.browser_human_repository import BrowserHumanRepository
    from src.services.agent_runner.browser_owner import RunnerBrowserOwner
    from src.tools.browser.run_manager import BrowserRunManager
    from src.tools.browser.executor.local import LocalPlaywrightExecutor
    from src.tools.browser.executor.models import CloseCommand
    from src.tools.browser.owner_port import current_resource_close_guard

    path = Path(destination).with_suffix('.cancel.json')
    observations = {'events': []}
    publication_lock = threading.Lock()
    def publish():
        temporary = path.with_suffix('.pending')
        temporary.write_text(json.dumps(observations))
        temporary.replace(path)
    def event(label, phase, error=None, result=None, local=None, extra=None):
        entry = {'method': label, 'phase': phase}
        if error is not None:
            entry['exception_class'] = type(error).__name__
            cause = error.__cause__
            if cause is not None:
                entry['cause_class'] = type(cause).__name__
            code = getattr(error, 'code', None)
            if not code and re.fullmatch('[A-Z][A-Z0-9_]{3,80}', str(error)):
                code = str(error)
            if isinstance(code, str) and re.fullmatch('[A-Z][A-Z0-9_]{3,80}', code):
                entry['code'] = code
        if result is not None:
            entry['closed'] = bool(getattr(result, 'closed', False))
            status = getattr(result, 'status', None)
            if status is not None:
                entry['status'] = str(getattr(status, 'value', status))
        if local is not None:
            entry['io_locked'] = local._io_lock.locked()
            entry['close_locked'] = local._close_lock.locked()
            entry['process_live'] = local._process is not None and local._process.returncode is None
            entry['resource_confirmed'] = bool(local.resource_close_confirmed)
            operation = current_resource_close_guard()
            if operation is not None:
                entry['write_started'] = bool(operation.write_started)
                entry['frame_written'] = bool(operation.frame_written)
        if extra is not None:
            entry.update(extra)
        # PG guards run to_thread. Lock only the observer's append/file IO;
        # never hold it across an original method, await or SQL operation.
        with publication_lock:
            observations['events'].append(entry)
            observations['events'] = observations['events'][-60:]
            publish()

    def task_state(task):
        if task is None:
            return {'present': False}
        chain, pending = [], task.get_coro()
        for _ in range(14):
            code = getattr(pending, 'cr_code', None) or getattr(pending, 'ag_code', None)
            frame = getattr(pending, 'cr_frame', None) or getattr(pending, 'ag_frame', None)
            if code is not None:
                filename = code.co_filename
                safe_file = filename.split('/app/', 1)[-1] if '/app/' in filename else Path(filename).name
                chain.append({'file': safe_file, 'function': code.co_name,
                    'line': frame.f_lineno if frame is not None else None})
            pending = getattr(pending, 'cr_await', None) or getattr(pending, 'ag_await', None)
            if pending is None:
                break
        return {'present': True, 'done': task.done(), 'cancelled': task.cancelled(),
            'cancelling_count': task.cancelling(), 'await_chain': chain,
            'stack': [{'file': Path(frame.f_code.co_filename).name, 'function': frame.f_code.co_name,
                'line': frame.f_lineno} for frame in task.get_stack(limit=4)]}

    def async_wrap(cls, name, label):
        original = getattr(cls, name)
        async def observed(self, *args, **kwargs):
            local = self if cls is LocalPlaywrightExecutor else None
            event(label, 'entered', local=local)
            watcher = None
            if cls is LocalPlaywrightExecutor and name == 'close' and current_resource_close_guard() is not None:
                close_task, frame_task = asyncio.current_task(), self._frame_task
                stderr_task = getattr(self, '_stderr_task', None)
                async def watch_original_tasks():
                    await asyncio.sleep(10)
                    event('original_close_wait', 'observed', local=self, extra={
                        'close_task': task_state(close_task), 'frame_task': task_state(frame_task),
                        'stderr_task': task_state(stderr_task),
                        'frame_task_is_close_task': frame_task is close_task})
                watcher = asyncio.create_task(watch_original_tasks())
            try:
                result = await original(self, *args, **kwargs)
            except BaseException as error:
                event(label, 'raised', error=error, local=local)
                raise
            else:
                event(label, 'returned', result=result, local=local)
                return result
            finally:
                if watcher is not None and not watcher.done():
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
        setattr(cls, name, observed)

    async_wrap(RuntimeFactory, 'request_resource_cancellation', 'resource_cancel')
    async_wrap(BrowserRunManager, 'request_cancel', 'manager_cancel')
    async_wrap(BrowserRunManager, 'finalize', 'manager_finalize')
    async_wrap(LocalPlaywrightExecutor, 'close', 'local_close')
    async_wrap(LocalPlaywrightExecutor, '_force_reap', 'local_reap')
    async_wrap(LocalPlaywrightExecutor, '_finish_stderr', 'stderr_finish')
    async_wrap(RunnerBrowserOwner, 'record_state', 'owner_state')
    async_wrap(RunnerBrowserOwner, 'close_browser_owner', 'owner_closed')
    request = LocalPlaywrightExecutor._request
    async def close_request(self, command, result_type, **kwargs):
        closing = kwargs.get('close_request') or isinstance(command, CloseCommand)
        if closing:
            event('close_request', 'entered', local=self)
        try:
            result = await request(self, command, result_type, **kwargs)
        except BaseException as error:
            if closing:
                event('close_request', 'raised', error=error, local=self)
            raise
        else:
            if closing:
                event('close_request', 'returned', result=result, local=self)
            return result
    LocalPlaywrightExecutor._request = close_request
    original = BrowserHumanRepository.assert_cancel_owner
    def guard(self, *args, **kwargs):
        event('pg_cancel_guard', 'entered')
        try:
            result = original(self, *args, **kwargs)
        except BaseException as error:
            event('pg_cancel_guard', 'raised', error=error)
            raise
        else:
            event('pg_cancel_guard', 'returned')
            return result
    BrowserHumanRepository.assert_cancel_owner = guard


if __name__ == '__main__':
    report, *arguments = sys.argv[1:]
    from tests.integration.agent_runner_service.browser_human_service_probe import install
    install(report)
    install_cancel_observer(report)
    sys.argv = ['worker', *arguments]
    from src.services.agent_runner.worker import main
    main()
