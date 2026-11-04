"""The picker must list synchronized empty targets without changing monitoring state."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_localdb_query import drv, WorkdirFixture, SELF, PEER, CONV_S, CONV_R1, msg_row, text_content


class SessionDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fx = WorkdirFixture(self.temp.name)
        self.fx.add_message_db([msg_row(CONV_S, PEER, 2, 100, text_content('hello'))])
        self.fx.add_user_db([(SELF, 'Me', '', ''), (PEER, 'Alex', '', ''), ('300', 'Alex', '', '')])
        self.empty_person = f'S:{SELF}_300'
        self.empty_group = 'R:empty'
        self.fx.add_session_db([(CONV_S, 'old title'), (self.empty_person, 'old title'),
                                (CONV_R1, 'Team'), (self.empty_group, 'Team')])

    def directory(self, **request):
        return drv.cmd_directory(self.fx.dir, SELF, request)

    def test_empty_private_and_group_are_listed_and_same_names_keep_ids(self):
        first = self.directory(mode='list', query='', after='', limit=2)
        self.assertTrue(first['ok'], first)
        second = self.directory(mode='list', query='', after=first['next_after'], limit=2)
        rows = first['conversations'] + second['conversations']
        self.assertEqual({row['conversation_id'] for row in rows},
                         {CONV_S, CONV_R1, self.empty_person, self.empty_group})
        self.assertEqual(len([row for row in rows if row['name'] == 'Alex' and row['same_name']]), 2)
        self.assertEqual(len([row for row in rows if row['name'] == 'Team' and row['same_name']]), 2)

    def test_preview_of_empty_target_is_empty_and_resolve_is_all_or_nothing(self):
        preview = self.directory(mode='preview', conversation_id=self.empty_group, limit=3)
        self.assertTrue(preview['ok'], preview)
        self.assertEqual(preview['messages'], [])
        resolved = self.directory(mode='resolve', conversation_ids=[self.empty_group, self.empty_person])
        self.assertEqual([row['conversation_id'] for row in resolved['resolved_targets']],
                         [self.empty_group, self.empty_person])
        self.assertEqual(self.directory(mode='resolve', conversation_ids=[self.empty_group, 'R:missing'])['error'],
                         'TARGET_NOT_FOUND')

    def test_preview_text_is_bounded_before_python_bridge_output(self):
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'message.db.dec.db'))
        try:
            from test_localdb_query import insert_messages
            insert_messages(conn, [msg_row(CONV_S, PEER, 2, 101, text_content('x' * 20000))])
        finally:
            conn.close()
        preview = self.directory(mode='preview', conversation_id=CONV_S, limit=3)
        self.assertTrue(preview['ok'], preview)
        self.assertLessEqual(len(preview['messages'][-1]['text']), 256)
        self.assertLess(len(json.dumps(preview).encode()), 2048)

    def test_foreign_account_conversation_is_not_a_candidate(self):
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'session.db.dec.db'))
        try:
            conn.execute('INSERT INTO conversation_table(conversation_id,name) VALUES (?,?)',
                         ('S:other_300', 'Alex'))
            conn.commit()
        finally:
            conn.close()
        rows = self.directory(mode='list', query='Alex', after='', limit=50)['conversations']
        self.assertNotIn('S:other_300', [row['conversation_id'] for row in rows])
        self.assertEqual(self.directory(mode='preview', conversation_id='S:other_300')['error'],
                         'TARGET_NOT_FOUND')

    def test_large_empty_catalog_pages_with_legacy_sqlite_variable_limit(self):
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'session.db.dec.db'))
        try:
            conn.executemany('INSERT INTO conversation_table(conversation_id,name) VALUES (?,?)',
                             [(f'R:large-{i:04d}', f'群{i:04d}') for i in range(1200)])
            conn.commit()
        finally:
            conn.close()
        original = drv._open_ro
        def limited(path):
            opened = original(path)
            opened.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
            return opened
        with patch.object(drv, '_open_ro', limited):
            page = self.directory(mode='list', query='', after='', limit=50)
        self.assertTrue(page['ok'], page)
        self.assertEqual(len(page['conversations']), 50)
        self.assertIsNotNone(page['next_after'])

    def test_resolve_fifty_long_ids_fits_directory_stdin_contract(self):
        ids = [f'R:{"x" * 390}{i:02d}' for i in range(50)]
        unicode_ids = [f'R:{"群" * 480}{i:02d}' for i in range(50)]
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'session.db.dec.db'))
        try:
            conn.executemany('INSERT INTO conversation_table(conversation_id,name) VALUES (?,?)',
                             [(cid, '长标识群') for cid in ids + unicode_ids])
            conn.commit()
        finally:
            conn.close()
        for batch, minimum in ((ids, 8192), (unicode_ids, 32768)):
            request = json.dumps({'mode': 'resolve', 'conversation_ids': batch}, ensure_ascii=False)
            self.assertGreater(len(request.encode('utf-8')), minimum)
            result = subprocess.run([sys.executable, drv.__file__, 'directory', '--workdir', self.fx.dir,
                                     '--account', SELF], input=request, text=True,
                                    encoding='utf-8', capture_output=True, check=True)
            payload = json.loads(result.stdout.strip().removeprefix('LOCALDB_JSON: '))
            self.assertTrue(payload['ok'], payload)
            self.assertEqual([row['conversation_id'] for row in payload['resolved_targets']], batch)
