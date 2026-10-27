"""Bootstrap-safe unittest suite: python -m unittest discover -s tests/unit -p test_testing_db_guard.py.

No pytest bootstrap is required. Every non-PG test replaces the native connect
boundary before installing a guard; Python socket blocking alone is insufficient.
"""
import ast
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import psycopg2

from scripts import testing_db_guard as guard

ROOT = Path(__file__).resolve().parents[2]
URL = 'postgresql://fixture@127.0.0.1:65431/aid_test_guard'
ADMIN = 'postgresql://fixture@127.0.0.1:65431/postgres'


def clean_environment():
    return {key: value for key, value in os.environ.items()
            if key not in {*guard.REDIRECT_ENV, 'DATABASE_URL', 'LOGS_DATABASE_URL'}}


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, clean_environment(), clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.native = Mock(return_value=object())
        self.native_patch = patch('psycopg2.connect', self.native)
        self.native_patch.start()
        self.addCleanup(self.native_patch.stop)

    def test_absent_url_blocks_direct_pool_and_reinherited_placeholder_connections(self):
        self.assertIsNone(guard.prepare_pytest_environment())
        self.assertEqual(os.environ['LOGS_DATABASE_URL'], '')
        with self.assertRaisesRegex(guard.TestDatabaseSafetyError, 'DISABLED'):
            psycopg2.connect(URL)
        from psycopg2.pool import ThreadedConnectionPool
        with self.assertRaisesRegex(guard.TestDatabaseSafetyError, 'DISABLED'):
            ThreadedConnectionPool(1, 1, host='127.0.0.1', dbname='aid_test_guard')
        self.assertIsNone(guard.prepare_pytest_environment())
        self.native.assert_not_called()

    def test_explicit_target_canonicalizes_alias_and_logs_share_the_exact_database(self):
        os.environ['DATABASE_URL'] = URL.replace('postgresql:', 'postgres:')
        os.environ['PGHOSTADDR'] = '203.0.113.19'
        target = guard.prepare_pytest_environment()
        self.assertEqual(target.database, 'aid_test_guard')
        self.assertEqual(os.environ['DATABASE_URL'], URL)
        self.assertEqual(os.environ['LOGS_DATABASE_URL'], URL)
        self.assertNotIn('PGHOSTADDR', os.environ)
        psycopg2.connect(URL, database='aid_test_guard')
        self.native.assert_called_once()
        os.environ['PGUSER'] = 'another-test-role'
        psycopg2.connect(host='127.0.0.1', port=65431, dbname='aid_test_guard', user='different-role')
        self.assertEqual(self.native.call_count, 2)

    def test_invalid_explicit_and_trace_targets_fail_before_native_without_secret_errors(self):
        invalid = [ADMIN, URL.replace('127.0.0.1', '203.0.113.19'), URL.replace('aid_test_guard', 'business'),
                   URL+'?host=203.0.113.19', URL+'#fragment', URL.replace(':65431/', ':0/'),
                   URL.replace('aid_test_guard', 'aid_test_'), URL.replace('aid_test_guard', 'aid_test_%2fother')]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(guard.TestDatabaseSafetyError) as failure:
                    guard.target_url(value.replace('fixture@', 'fixture:synthetic-secret@'))
                self.assertNotIn('synthetic-secret', str(failure.exception))
        os.environ.update(DATABASE_URL=URL, LOGS_DATABASE_URL=URL.replace('aid_test_guard', 'aid_test_other'))
        with self.assertRaisesRegex(guard.TestDatabaseSafetyError, 'TRACE_TARGET'):
            guard.prepare_pytest_environment()
        self.native.assert_not_called()

    def test_actual_dsn_kwargs_and_late_libpq_redirects_cannot_escape_the_selected_target(self):
        os.environ['DATABASE_URL'] = URL
        guard.prepare_pytest_environment()
        for dsn, options in [(URL, {'dbname': 'aid_test_other'}), (URL, {'database': 'business'}),
                             (URL, {'host': '203.0.113.19'}), (URL, {'hostaddr': '127.0.0.1'}),
                             ('service=fixture dbname=aid_test_guard', {'host': '127.0.0.1', 'port': 65431}),
                             (URL+'?dbname=business', {})]:
            with self.subTest(options=list(options)):
                with self.assertRaises(guard.TestDatabaseSafetyError):
                    psycopg2.connect(dsn, **options)
        for name in guard.REDIRECT_ENV:
            os.environ[name] = 'anonymous-redirection'
            with self.assertRaises(guard.TestDatabaseSafetyError):
                psycopg2.connect(URL)
            os.environ.pop(name)
        self.native.assert_not_called()
        # Prove the guard follows libpq's actual kwargs precedence, rather than
        # merely inspecting the original URL or a different database alias.
        psycopg2.connect(URL.replace('127.0.0.1', '203.0.113.19'), host='127.0.0.1')
        self.native.assert_called_once()
        psycopg2.connect('host=127.0.0.1 port=65431 dbname=aid_test_guard password=contains://query?ok')
        self.assertEqual(self.native.call_count, 2)

    def test_cleanup_actual_database_mismatch_prevents_even_tenant_reads_or_deletes(self):
        source = ast.parse((ROOT/'tests/conftest.py').read_text(encoding='utf-8'))
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                        and node.name == '_purge_test_tenants')
        cursor = Mock()
        cursor.fetchone.return_value = {'database': 'wrong_database'}
        connection = Mock(cursor=lambda: cursor)
        @contextmanager
        def connect():
            yield connection
        database = types.ModuleType('src.db.database')
        database._pg_connection_pool = object()
        database.get_db_connection = connect
        src, db = types.ModuleType('src'), types.ModuleType('src.db')
        src.db, db.database = db, database
        namespace = dict(_TEST_DATABASE=guard.target_url(URL), _TEST_TENANT_WHERE='TRUE',
                         TestDatabaseSafetyError=guard.TestDatabaseSafetyError, assert_database=guard.assert_database)
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'actual-conftest-purge', 'exec'), namespace)
        with patch.dict(sys.modules, {'src': src, 'src.db': db, 'src.db.database': database}):
            with self.assertRaisesRegex(guard.TestDatabaseSafetyError, 'ACTUAL_TARGET_MISMATCH'):
                namespace['_purge_test_tenants']()
        self.assertEqual(cursor.execute.call_count, 1)
        self.assertIn('pg_catalog.current_database', cursor.execute.call_args.args[0])
        self.native.assert_not_called()


class IsolatedRunnerTests(unittest.TestCase):
    def test_create_failure_never_drops_and_owned_database_is_cleaned_after_later_failures(self):
        from scripts import dev_test_isolated_db as runner
        for mode in ('admin-mismatch', 'create-failure', 'create-close-failure', 'schema-failure', 'pytest-failure', 'success'):
            statements, connections, child_envs, close_failures = [], [], [], []
            class Cursor:
                def __init__(self, database): self.database, self.created = database, False
                def __enter__(self): return self
                def __exit__(self, *args):
                    if mode == 'create-close-failure' and self.created:
                        close_failures.append(self.database)
                        raise RuntimeError('synthetic cursor close after successful create')
                    return False
                def fetchone(self): return (self.database,)
                def execute(self, query):
                    text = str(query)
                    statements.append((self.database, text))
                    is_create = text.startswith('Composed(') and 'CREATE DATABASE' in text
                    if mode == 'create-failure' and is_create: raise RuntimeError('synthetic')
                    if mode == 'schema-failure' and 'CREATE EXTENSION' in text: raise RuntimeError('synthetic')
                    if is_create: self.created = True
            class Connection:
                def __init__(self, name): self.name, self.closed = name, False
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def cursor(self): return Cursor(self.name)
                def close(self): self.closed = True
            def native(dsn, **kwargs):
                name = psycopg2.extensions.parse_dsn(dsn)['dbname']
                connection = Connection('wrong_actual_database' if mode == 'admin-mismatch' else name)
                connections.append(connection)
                return connection
            def child(*args, **kwargs):
                child_envs.append(kwargs['env'])
                return 1 if mode == 'pytest-failure' else 0
            with self.subTest(mode=mode), patch.dict(os.environ, {**clean_environment(), 'DATABASE_URL': ADMIN}, clear=True), \
                    patch('psycopg2.connect', side_effect=native), patch.object(runner.subprocess, 'call', side_effect=child), \
                    patch.object(sys, 'argv', ['isolated', 'synthetic-test.py']):
                result = runner.main()
            drops = [text for _, text in statements if text.startswith('Composed(') and 'DROP DATABASE' in text]
            creates = [text for _, text in statements if text.startswith('Composed(') and 'CREATE DATABASE' in text]
            self.assertEqual(len(creates), 0 if mode == 'admin-mismatch' else 1)
            self.assertEqual(len(drops), 0 if mode in ('create-failure', 'admin-mismatch') else 1)
            self.assertEqual(result, 0 if mode == 'success' else 1 if mode == 'pytest-failure' else 2)
            self.assertTrue(all(connection.closed for connection in connections))
            if drops:
                created_name = re.search(r'aid_test_[A-Za-z0-9_]+', creates[0]).group()
                self.assertIn(created_name, drops[0])
            self.assertEqual(len(close_failures), 1 if mode == 'create-close-failure' else 0)
            if child_envs:
                self.assertEqual(child_envs[0]['DATABASE_URL'], child_envs[0]['LOGS_DATABASE_URL'])
                self.assertEqual(guard.target_url(child_envs[0]['DATABASE_URL']).database, connections[1].name)


class EntrySubprocessTests(unittest.TestCase):
    def test_real_conftest_no_db_malicious_dotenv_collect_only_and_invalid_explicit_targets(self):
        with tempfile.TemporaryDirectory(prefix='db-guard-entry-') as folder:
            project = Path(folder)
            (project/'tests').mkdir()
            (project/'scripts').mkdir()
            for name in ('tests/conftest.py', 'scripts/testing_db_guard.py'):
                shutil.copyfile(ROOT/name, project/name)
            (project/'.env').write_text('DATABASE_URL=postgresql://fake:synthetic-secret@203.0.113.19/business\nPGHOSTADDR=203.0.113.19\n', encoding='utf-8')
            (project/'tests/test_probe.py').write_text('''
def test_no_db_boundary():
    import os, psycopg2
    assert os.environ['DATABASE_URL'].endswith('/aid_test_disabled')
    from src.db.database import init_postgres_pool
    from scripts.testing_db_guard import TestDatabaseSafetyError
    try: init_postgres_pool()
    except TestDatabaseSafetyError: pass
    else: raise AssertionError('native pool was allowed')
    from pathlib import Path
    import src
    from src.core.skill_loader import SkillLoader
    loader = SkillLoader(Path(next(iter(src.__path__)))/'skills')
    assert loader.skills
    from src.db.database import get_postgres_pool
    assert get_postgres_pool() is None
''', encoding='utf-8')
            script = '''
import os,sys,pathlib,psycopg2
def forbidden(*args,**kwargs):
    pathlib.Path('native-called').write_text('unexpected')
    raise AssertionError('native DB boundary escaped')
psycopg2.connect=forbidden
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
'''
            for mode in ('plain', 'collect', 'remote', 'shared', 'logs-other'):
                environment = {**clean_environment(), 'PYTHONPATH': str(ROOT), 'PYTHONUTF8': '1'}
                if mode == 'remote': environment['DATABASE_URL'] = 'postgresql://fake:synthetic-secret@203.0.113.19/aid_test_x'
                if mode == 'shared': environment['DATABASE_URL'] = ADMIN
                if mode == 'logs-other': environment.update(DATABASE_URL=URL, LOGS_DATABASE_URL=URL.replace('aid_test_guard', 'aid_test_other'))
                arguments = [sys.executable, '-c', script, 'tests/test_probe.py', '-p', 'no:cacheprovider', '-q']
                if mode == 'collect': arguments.append('--collect-only')
                result = subprocess.run(arguments, cwd=project, env=environment, text=True, encoding='utf-8',
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=25)
                with self.subTest(mode=mode):
                    self.assertEqual(result.returncode == 0, mode in ('plain', 'collect'), result.stdout)
                    self.assertFalse((project/'native-called').exists())
                    self.assertNotIn('synthetic-secret', result.stdout)

    def test_isolated_cli_does_not_read_business_dotenv_or_connect_to_invalid_admin(self):
        with tempfile.TemporaryDirectory(prefix='db-guard-admin-entry-') as folder:
            project = Path(folder)
            (project/'scripts').mkdir()
            for name in ('scripts/dev_test_isolated_db.py', 'scripts/testing_db_guard.py'):
                shutil.copyfile(ROOT/name, project/name)
            (project/'.env').write_text('DATABASE_URL=postgresql://fake:synthetic-secret@203.0.113.19/business\n', encoding='utf-8')
            script = '''
import pathlib,psycopg2,runpy,sys
def forbidden(*args,**kwargs):
    pathlib.Path('native-called').write_text('unexpected')
    raise AssertionError('invalid admin reached native DB boundary')
psycopg2.connect=forbidden
runpy.run_path(sys.argv[1],run_name='__main__')
'''
            for url in (None, URL, ADMIN.replace('127.0.0.1', '203.0.113.19'), ADMIN+'?hostaddr=203.0.113.19'):
                environment = {**clean_environment(), 'PYTHONUTF8': '1'}
                if url: environment['DATABASE_URL'] = url
                result = subprocess.run([sys.executable, '-c', script, str(project/'scripts/dev_test_isolated_db.py')],
                    cwd=project, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=15)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse((project/'native-called').exists())
                self.assertNotIn('synthetic-secret', result.stdout+result.stderr)

    def test_docker_forwarding_requires_current_safety_sources_and_never_inherits_business_dsn(self):
        git_bash = Path('C:/Program Files/Git/bin/bash.exe')
        bash = str(git_bash) if os.name == 'nt' and git_bash.is_file() else shutil.which('bash')
        if not bash or not Path(bash).is_file():
            self.skipTest('Bash unavailable; Docker forwarding stub requires Bash')
        with tempfile.TemporaryDirectory(prefix='db-guard-docker-') as folder:
            base = Path(folder)
            container = base/'container'
            for name in guard.SOURCE_FILES:
                destination = container/name
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Same code, intentionally different platform EOLs.
                destination.write_bytes((ROOT/name).read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
            docker = base/'docker'
            docker.write_text('''#!/usr/bin/env python
import json,os,pathlib,subprocess,sys
args=sys.argv[1:]
with open(os.environ['DOCKER_PROBE_LOG'],'a',encoding='utf-8') as out:
    out.write(json.dumps({'args':args,'db_empty':not os.environ.get('DATABASE_URL'),
        'logs_empty':not os.environ.get('LOGS_DATABASE_URL'),
        'admin_forwarded':os.environ.get('DATABASE_URL')==os.environ.get('EXPECTED_ADMIN')})+'\\n')
if args[0]=='ps': print('aid-agent-api'); raise SystemExit(0)
if '-c' in args:
    raise SystemExit(subprocess.call([sys.executable,'-c',args[args.index('-c')+1]],cwd=os.environ['DOCKER_PROBE_ROOT']))
raise SystemExit(0)
''', encoding='utf-8')
            docker.chmod(0o755)
            log = base/'calls.jsonl'
            environment = {**clean_environment(), 'PATH': str(base)+os.pathsep+str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH',''),
                'PYTHONUTF8': '1', 'DOCKER_PROBE_LOG': str(log), 'DOCKER_PROBE_ROOT': str(container), 'EXPECTED_ADMIN': ADMIN}
            for mode in ('normal', 'isolated', 'mismatch', 'invalid-admin'):
                if log.exists(): log.unlink()
                env = {**environment, 'DATABASE_URL': ADMIN if mode == 'isolated' else 'postgresql://fake@203.0.113.19/business',
                       'LOGS_DATABASE_URL': 'postgresql://fake@203.0.113.19/logs'}
                if mode == 'mismatch':
                    (container/'scripts/testing_db_guard.py').write_bytes(b'# old unguarded source\n')
                arguments = [bash, str(ROOT/'scripts/dev_test.sh')]
                if mode in ('isolated', 'invalid-admin'): arguments.append('--isolated-db')
                arguments += ['tests/synthetic.py', '-m', 'real_browser' if mode == 'mismatch' else 'unit']
                result = subprocess.run(arguments, cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
                self.assertTrue(log.exists(), result.stderr)
                records = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
                tests = [record for record in records if '-m' in record['args'] and 'pytest' in record['args']
                         or 'scripts/dev_test_isolated_db.py' in record['args']]
                with self.subTest(mode=mode):
                    self.assertEqual(result.returncode, 0 if mode in ('normal', 'isolated') else 2, result.stderr)
                    self.assertEqual(len(tests), 1 if mode in ('normal', 'isolated') else 0)
                    if tests:
                        record = tests[0]
                        self.assertIn('DATABASE_URL', record['args'])
                        self.assertIn('LOGS_DATABASE_URL', record['args'])
                        self.assertFalse(any('://' in argument for argument in record['args']))
                        self.assertTrue(record['logs_empty'])
                        self.assertTrue(record['admin_forwarded'] if mode == 'isolated' else record['db_empty'])
                    self.assertFalse(any('redis' in argument for record in records for argument in record['args']))
                if mode == 'mismatch':
                    (container/'scripts/testing_db_guard.py').write_bytes((ROOT/'scripts/testing_db_guard.py').read_bytes())


if __name__ == '__main__':
    unittest.main()
