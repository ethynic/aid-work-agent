"""Test-only PostgreSQL admission. No product imports or business .env reads."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit, urlunsplit


class TestDatabaseSafetyError(RuntimeError):
    """Fixed messages only: connection strings/credentials must never leak."""


REDIRECT_ENV = ('PGHOST', 'PGHOSTADDR', 'PGPORT', 'PGDATABASE', 'PGSERVICE',
                'PGSERVICEFILE', 'PGSYSCONFDIR', 'PGOPTIONS')
SAFE_NO_DB_URL = 'postgresql://127.0.0.1:1/aid_test_disabled'
SOURCE_FILES = ('tests/conftest.py', 'scripts/testing_db_guard.py', 'scripts/dev_test_isolated_db.py')


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    database: str
    url: str = field(repr=False, compare=False)


def target_url(value, *, admin=False):
    try:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError()
        parts = urlsplit(value)
        host = parts.hostname
        name = unquote(parts.path[1:]) if parts.path.startswith('/') else ''
        port = 5432 if parts.port is None else parts.port
        if (parts.scheme not in {'postgres', 'postgresql'} or host not in {'localhost', '127.0.0.1', '::1'}
                or not 1 <= port <= 65535 or parts.query or parts.fragment or '%' in parts.path
                or parts.path != '/'+name or len(name) > 63
                or (name != 'postgres' if admin else not re.fullmatch(r'aid_test_[A-Za-z0-9_]+', name))):
            raise ValueError()
        return Target(host, port, name, urlunsplit(parts._replace(scheme='postgresql')))
    except (ValueError, TypeError):
        raise TestDatabaseSafetyError('TEST_DATABASE_CONFIGURATION_INVALID') from None


def clear_redirect_environment():
    for name in REDIRECT_ENV:
        os.environ.pop(name, None)


def assert_database(cursor, expected):
    cursor.execute('SELECT pg_catalog.current_database() AS database')
    row = cursor.fetchone()
    actual = row.get('database') if isinstance(row, dict) else row[0] if row else None
    if actual != expected:
        raise TestDatabaseSafetyError('TEST_DATABASE_ACTUAL_TARGET_MISMATCH')


def install_connect_guard(targets):
    """Protect normal psycopg2.connect and its ThreadedConnectionPool callers.

    This is not a sandbox against arbitrary Python using private native APIs.
    Every ordinary DSN/kwargs connection is checked before libpq can do IO.
    """
    import psycopg2
    from psycopg2.extensions import make_dsn, parse_dsn
    original = psycopg2.connect

    def connect(dsn=None, *args, **kwargs):
        if not targets:
            raise TestDatabaseSafetyError('TEST_DATABASE_DISABLED')
        if any(os.environ.get(name) for name in REDIRECT_ENV):
            raise TestDatabaseSafetyError('TEST_DATABASE_REDIRECTION_REJECTED')
        try:
            if isinstance(dsn, str) and dsn.startswith(('postgres://', 'postgresql://')):
                parts = urlsplit(dsn)
                if parts.query or parts.fragment:
                    raise ValueError()
            parameters = parse_dsn(dsn) if dsn else {}
            if any(key in parameters or key in kwargs for key in ('service', 'servicefile', 'hostaddr')):
                raise ValueError()
            connection_kwargs = {key: value for key, value in kwargs.items()
                                 if key not in {'connection_factory', 'cursor_factory', 'async', 'async_'}}
            effective = parse_dsn(make_dsn(dsn, **connection_kwargs))
            actual = (effective.get('host'), int(effective.get('port') or 5432), effective.get('dbname'))
            if not any(actual == (target.host, target.port, target.database) for target in targets):
                raise ValueError()
        except (ValueError, TypeError, psycopg2.Error):
            raise TestDatabaseSafetyError('TEST_DATABASE_CONNECTION_REJECTED') from None
        return original(dsn, *args, **kwargs)

    psycopg2.connect = connect


def prepare_pytest_environment():
    """Called before any src import. Absence of explicit URL means no DB IO."""
    value = os.environ.get('DATABASE_URL')
    target = target_url(value) if value and value != SAFE_NO_DB_URL else None
    logs = os.environ.get('LOGS_DATABASE_URL')
    if target and logs and target_url(logs) != target:
        raise TestDatabaseSafetyError('TEST_DATABASE_TRACE_TARGET_MISMATCH')
    clear_redirect_environment()
    os.environ['DATABASE_URL'] = target.url if target else SAFE_NO_DB_URL
    os.environ['LOGS_DATABASE_URL'] = target.url if target else ''
    install_connect_guard((target,) if target else ())
    return target


def source_hashes(root):
    return {name: hashlib.sha256((Path(root)/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for name in SOURCE_FILES}


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--source-hashes']:
            print(json.dumps(source_hashes(Path(__file__).resolve().parents[1]), sort_keys=True))
        elif sys.argv[1:] == ['--validate-admin']:
            target_url(os.environ.get('DATABASE_URL'), admin=True)
        else:
            raise TestDatabaseSafetyError('TEST_DATABASE_GUARD_COMMAND_INVALID')
    except TestDatabaseSafetyError as error:
        print(str(error)+'; use an explicit loopback test target or --isolated-db.', file=sys.stderr)
        raise SystemExit(2)
