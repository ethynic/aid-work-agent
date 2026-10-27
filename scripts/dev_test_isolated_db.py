"""Disposable PostgreSQL test database; invoke via dev_test.sh --isolated-db.

Only creates/drops its own randomly named database. Pytest runs in a fresh
process so imports and pools cannot keep the original DATABASE_URL.
"""
from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
import uuid

import psycopg2
from psycopg2 import sql

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.testing_db_guard import (
    TestDatabaseSafetyError, assert_database, clear_redirect_environment,
    install_connect_guard, target_url,
)


def database_url(base: str, name: str) -> str:
    parts = urlsplit(target_url(base, admin=True).url)
    return target_url(urlunsplit(parts._replace(path='/'+name))).url


def main() -> int:
    name = "aid_test_" + uuid.uuid4().hex
    admin = None
    created = False
    status = 2
    try:
        base = target_url(os.environ.get('DATABASE_URL'), admin=True)
        target = target_url(database_url(base.url, name))
        clear_redirect_environment()
        install_connect_guard((base, target))
        admin = psycopg2.connect(base.url, connect_timeout=10)
        admin.autocommit = True
        with admin.cursor() as cur:
            assert_database(cur, 'postgres')
            cur.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
            created = True
        with closing(psycopg2.connect(target.url, connect_timeout=10)) as conn:
            with conn:
                with conn.cursor() as cur:
                    assert_database(cur, name)
                    cur.execute('SET LOCAL search_path TO public')
                    cur.execute((ROOT / "deploy/init-postgres.sql").read_text(encoding="utf-8"))
        env = dict(os.environ, DATABASE_URL=target.url, LOGS_DATABASE_URL=target.url)
        print("Running pytest against a fresh disposable PostgreSQL database.", flush=True)
        status = subprocess.call([sys.executable, "-m", "pytest", *sys.argv[1:]], cwd=ROOT, env=env)
    except TestDatabaseSafetyError as error:
        print(str(error)+'; --isolated-db requires an explicit loopback PostgreSQL URL for the postgres management database.', file=sys.stderr)
        status = 2
    except Exception as error:
        print(f"Isolated database test setup failed ({type(error).__name__}); no connection details logged.", file=sys.stderr)
        status = 2
    finally:
        if admin is not None:
            try:
                if created:
                    with admin.cursor() as cur:
                        assert_database(cur, 'postgres')
                        cur.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            except Exception:
                print('TEST_DATABASE_CLEANUP_FAILED; only the owned database was eligible for cleanup.', file=sys.stderr)
                status = 2
            finally:
                try:
                    admin.close()
                except Exception:
                    print('TEST_DATABASE_CLEANUP_FAILED; management connection could not close.', file=sys.stderr)
                    status = 2
    return status


if __name__ == "__main__":
    raise SystemExit(main())
