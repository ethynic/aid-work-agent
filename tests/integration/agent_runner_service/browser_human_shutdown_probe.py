"""Original unknown Close stays an error; observe remaining cleanup returns."""
import json
from pathlib import Path
import sys
import threading


def install_shutdown_observer(destination):
    from src.services.agent_runner.worker import RuntimeFactory
    from src.services.agent_runner.browser_sidecar import BrowserViewSidecar
    import src.db.database as database

    report = Path(destination).with_suffix('.shutdown.json')
    lock = threading.Lock()
    observations = {}
    def publish(**values):
        with lock:
            observations.update(values)
            temporary = report.with_suffix('.pending')
            temporary.write_text(json.dumps(observations))
            temporary.replace(report)

    original_factory_close, original_sidecar_close = RuntimeFactory.close, BrowserViewSidecar.close
    async def factory_close(self):
        try:
            return await original_factory_close(self)
        except Exception as error:
            publish(factory_exception=type(error).__name__, factory_code=(
                'BROWSER_CLOSE_VERIFICATION_REQUIRED' if error.args
                and error.args[0] == 'BROWSER_CLOSE_VERIFICATION_REQUIRED' else None))
            raise
    async def sidecar_close(self):
        result = await original_sidecar_close(self)
        publish(sidecar_close_returned=True,
                sidecar_task_done=self.task is None or self.task.done(),
                sidecar_socket_closed=self.socket is None or self.socket.fileno() == -1)
        return result
    def pool_close(original, label):
        def close():
            result = original()
            publish(**{label + '_returned': True})
            return result
        return close
    RuntimeFactory.close = factory_close
    BrowserViewSidecar.close = sidecar_close
    database.close_logs_pool = pool_close(database.close_logs_pool, 'logs_pool_close')
    database.close_postgres_pool = pool_close(database.close_postgres_pool, 'postgres_pool_close')


if __name__ == '__main__':
    report, *arguments = sys.argv[1:]
    from tests.integration.agent_runner_service.browser_human_service_probe import install
    from tests.integration.agent_runner_service.browser_human_partial_write_probe import install_partial_write
    install(report)
    install_partial_write(report)
    install_shutdown_observer(report)
    sys.argv = ['worker', *arguments]
    from src.services.agent_runner.worker import main
    main()
