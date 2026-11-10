"""Independent database-only long-running Runner alert scanner."""

import argparse
import signal
import threading

from src.db.database import init_postgres_pool, close_postgres_pool

from .runner_alerts import RunnerAlertRepository


class RunnerMonitor:
    def __init__(self, repository=None):
        self.repository = repository or RunnerAlertRepository()
        self.stopping = threading.Event()

    def stop(self, *_):
        self.stopping.set()

    def run(self):
        from src.config.settings import settings
        from .repository import RunnerRepository

        if not settings.agent_runner.enabled:
            raise RuntimeError('AGENT_RUNNER_DISABLED')
        init_postgres_pool(minconn=1, maxconn=2)
        try:
            RunnerRepository().assert_schema()
            while not self.stopping.is_set():
                # An incomplete scan must not advance the health timestamp.
                # Exit on storage failure so the container restart policy retries.
                self.repository.scan_once()
                self.stopping.wait(60)
        finally:
            close_postgres_pool()


def main():
    parser = argparse.ArgumentParser(description='Scan running Runner attempts for platform alerts')
    parser.add_argument('--healthcheck', action='store_true')
    args = parser.parse_args()
    if args.healthcheck:
        init_postgres_pool(minconn=1, maxconn=1)
        try:
            healthy = RunnerAlertRepository().healthy()
        finally:
            close_postgres_pool()
        raise SystemExit(0 if healthy else 1)
    monitor = RunnerMonitor()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, monitor.stop)
    monitor.run()


if __name__ == '__main__':
    main()
