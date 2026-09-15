"""Disposable PostgreSQL test database; invoke via dev_test.sh --isolated-db.

Only creates/drops its own randomly named database. Pytest runs in a fresh
process so imports and pools cannot keep the original DATABASE_URL.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
import uuid

from dotenv import load_dotenv
import psycopg2
from psycopg2 import sql

ROOT = Path(__file__).resolve().parents[1]


def database_url(base: str, name: str) -> str:
    parts = urlsplit(base)
    if parts.scheme not in {"postgres", "postgresql"} or not parts.netloc:
        raise ValueError("PostgreSQL URL required")
    # Query options could override dbname or supply an unexpected search_path.
    if parts.query or parts.fragment:
        raise ValueError("Use a PostgreSQL URL without query parameters for isolated tests")
    if not name.startswith("aid_test_") or not name[9:].isalnum():
        raise ValueError("Invalid isolated database name")
    return urlunsplit(parts._replace(path="/" + name))


def main() -> int:
    load_dotenv(ROOT / ".env")
    name = "aid_test_" + uuid.uuid4().hex
    admin = None
    created = False
    try:
        base = os.environ.get("DATABASE_URL", "")
        target = database_url(base, name)
        admin = psycopg2.connect(base, connect_timeout=10)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        created = True
        with psycopg2.connect(target, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute((ROOT / "deploy/init-postgres.sql").read_text(encoding="utf-8"))
        env = dict(os.environ, DATABASE_URL=target)
        # Existing fixtures may load .env, but load_dotenv does not override this.
        print("Running pytest against a fresh disposable PostgreSQL database.", flush=True)
        return subprocess.call([sys.executable, "-m", "pytest", *sys.argv[1:]], cwd=ROOT, env=env)
    except Exception as error:
        print(f"Isolated database test setup failed ({type(error).__name__}); no connection details logged.", file=sys.stderr)
        return 2
    finally:
        if admin is not None:
            try:
                if created:
                    with admin.cursor() as cur:
                        cur.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            finally:
                admin.close()


if __name__ == "__main__":
    raise SystemExit(main())
