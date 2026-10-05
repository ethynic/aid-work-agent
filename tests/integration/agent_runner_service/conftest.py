"""Real PostgreSQL and subprocess fixtures; no production auth/kernel/store mocks."""
from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import contextmanager
from datetime import datetime, timedelta
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

import psycopg2
from psycopg2.extras import RealDictCursor
import pytest


@dataclass(frozen=True)
class Actor:
    user_id: str
    tenant_id: str | None
    session_id: str
    token: str = field(repr=False)


class IsolatedDatabase:
    def __init__(self):
        self._url = os.environ.get("DATABASE_URL", "")
        self.name = urlsplit(self._url).path.lstrip("/")
        if not self.name.startswith("aid_test_"):
            raise RuntimeError("Runner service tests require --isolated-db")
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT current_database() AS name")
            assert cursor.fetchone()["name"] == self.name

    @contextmanager
    def connect(self):
        connection = psycopg2.connect(self._url, cursor_factory=RealDictCursor)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def rows(self, statement, parameters=()):
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return cursor.fetchall() if cursor.description else []


@pytest.fixture(scope="module")
def service_database():
    return IsolatedDatabase()


@pytest.fixture
def actors(service_database):
    """Real active principals, own sessions and opaque tokens, including NULL scope."""
    suffix = uuid.uuid4().hex
    tenants = {label: f"runner_test_{label}_{suffix}" for label in ("a", "b")}
    result = {}
    with service_database.connect() as connection, connection.cursor() as cursor:
        for label, tenant in tenants.items():
            cursor.execute("INSERT INTO tenants (tenant_id, company_name, tenant_code, credit_balance) VALUES (%s, %s, %s, 1000)",
                           (tenant, f"测试租户-runner-{suffix}-{label}", f"T{uuid.uuid4().hex[:6].upper()}"))
        for label, tenant, role in (("a", tenants["a"], "user"), ("a_other", tenants["a"], "user"),
                                    ("b", tenants["b"], "user"), ("global", None, "platform_admin"),
                                    ("global_other", None, "platform_admin"), ("null_user", None, "user")):
            user = f"runner_actor_{label}_{suffix}"
            session = f"runner_session_{label}_{suffix}"
            token = secrets.token_urlsafe(32)
            cursor.execute("INSERT INTO users (user_id, tenant_id, role, status, username) VALUES (%s, %s, %s, 'active', %s)",
                           (user, tenant, role, user))
            cursor.execute("INSERT INTO chat_sessions (session_id, user_id, tenant_id, title) VALUES (%s, %s, %s, 'Runner acceptance')",
                           (session, user, tenant))
            cursor.execute("INSERT INTO tokens (token, user_id, expires_at) VALUES (%s, %s, %s)",
                           (token, user, datetime.now() + timedelta(days=6)))
            result[label] = Actor(user, tenant, session, token)
    try:
        yield result
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            user_ids = [actor.user_id for actor in result.values()]
            # All IDs belong to this fixture, including nullable-tenant admins.
            cursor.execute("DELETE FROM agent_runner_usage_receipts WHERE runner_id IN (SELECT runner_id FROM agent_runners WHERE user_id=ANY(%s))", (user_ids,))
            cursor.execute("DELETE FROM agent_runner_session_claims WHERE owner_runner_id IN (SELECT runner_id FROM agent_runners WHERE user_id=ANY(%s))", (user_ids,))
            cursor.execute("DELETE FROM agent_runner_controls WHERE runner_id IN (SELECT runner_id FROM agent_runners WHERE user_id=ANY(%s))", (user_ids,))
            cursor.execute("DELETE FROM agent_runners WHERE user_id=ANY(%s)", (user_ids,))
            cursor.execute("DELETE FROM channel_messages WHERE session_id IN (SELECT session_id FROM channel_sessions WHERE user_id=ANY(%s))", (user_ids,))
            cursor.execute("DELETE FROM channel_sessions WHERE user_id=ANY(%s)", (user_ids,))
            cursor.execute("DELETE FROM chat_records WHERE user_id=ANY(%s)", (user_ids,))
            cursor.execute("DELETE FROM chat_messages WHERE session_id IN (SELECT session_id FROM chat_sessions WHERE user_id=ANY(%s))", (user_ids,))
            cursor.execute("DELETE FROM chat_sessions WHERE user_id=ANY(%s)", (user_ids,))
            cursor.execute("DELETE FROM user_agent_permissions WHERE user_id=ANY(%s)", (user_ids,))
            for actor in result.values():
                cursor.execute("DELETE FROM tokens WHERE user_id = %s", (actor.user_id,))
                cursor.execute("DELETE FROM users WHERE user_id = %s", (actor.user_id,))
            for tenant in tenants.values():
                cursor.execute("DELETE FROM subscriptions WHERE tenant_id = %s", (tenant,))
                cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant,))
        # Runtime skill catalog lookup creates tenant directories under the
        # configured shared project root even with a private worker cwd. Reclaim
        # only these exact fictional IDs, and only empty directory scaffolding.
        from src.config.settings import settings
        configured = Path(settings.saas.tenant_skills_dir)
        base = configured if configured.is_absolute() else Path(__file__).resolve().parents[3] / configured
        for tenant in tenants.values():
            owned = base / tenant
            if owned.is_dir() and not owned.is_symlink():
                for directory in sorted((item for item in owned.rglob("*") if item.is_dir() and not item.is_symlink()),
                                        key=lambda item: len(item.parts), reverse=True):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                try:
                    owned.rmdir()
                except OSError:
                    pass


class Processes:
    """Own only child process groups and temporary logs created by this fixture."""
    def __init__(self, root: Path, database: IsolatedDatabase):
        self.root = root
        self.database = database
        self.children = []
        self.additional_owned_directories = []

    def start(self, arguments, *, environment=None, private_working_directory=False):
        environment = dict(os.environ, **(environment or {}))
        assert urlsplit(environment.get("DATABASE_URL", "")).path.lstrip("/") == self.database.name
        # A worker must never inherit the developer's separate trace database.
        environment.update(TMPDIR=str(getattr(self, 'browser_temporary_directory', self.root)), PYTHONUNBUFFERED="1",
                           LOGS_DATABASE_URL=environment["DATABASE_URL"])
        working_directory = None
        if private_working_directory:
            working_directory = self.root / f"work-{len(self.children)}"
            working_directory.mkdir()
            repository = str(Path(__file__).resolve().parents[3])
            environment["PYTHONPATH"] = os.pathsep.join(
                part for part in (repository, environment.get("PYTHONPATH")) if part)
        log = self.root / f"child-{len(self.children)}.log"
        with log.open("wb") as output:
            child = subprocess.Popen([sys.executable, *arguments], env=environment,
                                     cwd=working_directory,
                                     stdout=output, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        self.children.append(child)
        return child

    def stop(self, child, *, force=False):
        assert child in self.children
        try:
            os.killpg(child.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass
        if child.poll() is None:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)

    def close(self):
        for child in reversed(self.children):
            self.stop(child)
        shutil.rmtree(self.root, ignore_errors=True)
        for directory in self.additional_owned_directories:
            shutil.rmtree(directory)
            assert not directory.exists()


@pytest.fixture(scope="module")
def service_processes(tmp_path_factory, service_database):
    processes = Processes(tmp_path_factory.mktemp("agent-runner-processes"), service_database)
    try:
        yield processes
    finally:
        processes.close()


@pytest.fixture
def provider_peer():
    from .provider import ProviderPeer
    peer = ProviderPeer()
    try:
        yield peer
    finally:
        peer.close()


def wait_for(predicate, *, timeout=10):
    """Poll observable state with a deadline; assertions never rely on fixed sleeps."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.025)
    raise AssertionError("Observable runner service state did not converge before deadline")
