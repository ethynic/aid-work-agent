"""群列表与历史时间线：使用合成本地库，验证查询意图与旧游标兼容。"""
import os
import sqlite3
import tempfile
import unittest

import test_localdb_query as fixtures

drv = fixtures.drv
GROUP = 'R:timeline'


class GroupQueryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wecom-groups-')
        self.addCleanup(self.tmp.cleanup)
        self.fx = fixtures.WorkdirFixture(self.tmp.name)

    def query(self, *args):
        return self.fx.query(['--task', 'groups'] + list(args))

    def test_filter_deduplicate_sort_before_limit_and_include_empty_groups(self):
        """列表以 session 为准；重复/非群不能耗尽 limit，无消息的同名群仍可发现。"""
        self.fx.add_session_db([
            ('S:one_two', 'A群'), ('R:z', 'B群'), ('R:a', 'A群'),
            ('R:a', 'A群'), ('R:b', 'A群'), ('R:empty', 'C群'),
            ('R:unnamed', ''), ('R:whitespace', '   '), ('R:null', None),
        ])
        payload = self.query('--limit', '2')
        self.assertTrue(payload['ok'], payload)
        self.assertEqual(payload['groups'], [
            {'conversation_id': 'R:a', 'name': 'A群'},
            {'conversation_id': 'R:b', 'name': 'A群'},
        ])
        self.assertEqual(payload['count'], 2)
        self.assertTrue(payload['has_more'])
        full = self.query('--limit', '4')
        self.assertEqual(full['groups'][-1]['conversation_id'], 'R:empty')
        self.assertEqual(full['count'], 4)
        self.assertFalse(full['has_more'])
        self.assertFalse(os.path.exists(os.path.join(self.fx.dir, 'message.db.dec.db')))

    def test_literal_keyword_wildcards_and_sql_characters(self):
        """用户关键词是字面子串，SQL 通配符及引号不改变匹配范围。"""
        self.fx.add_session_db([
            ('R:a', '项目%_群'), ('R:b', '项目AB群'), ('R:c', "项目' OR 1=1群"),
        ])
        for keyword, expected in [('%_', 'R:a'), ("' OR 1=1", 'R:c')]:
            payload = self.query('--name', keyword, '--limit', '1')
            self.assertEqual([g['conversation_id'] for g in payload['groups']], [expected])
            self.assertFalse(payload['has_more'])
        self.assertEqual(self.query('--name', '无匹配')['groups'], [])

    def test_conflicting_duplicate_names_have_deterministic_representative(self):
        self.fx.add_session_db([('R:a', 'Z群'), ('R:a', 'A群'), ('R:b', 'B群')])
        self.assertEqual(self.query()['groups'], [
            {'conversation_id': 'R:a', 'name': 'A群'},
            {'conversation_id': 'R:b', 'name': 'B群'},
        ])

    def test_empty_session_table_succeeds(self):
        self.fx.add_session_db([])
        payload = self.query()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['groups'], [])
        self.assertEqual(payload['count'], 0)
        self.assertFalse(payload['has_more'])

    def test_alt_id_column_uses_schema_evidence(self):
        self.fx.add_session_db([('R:a', '项目群')], id_col='conv_key')
        self.assertEqual(self.query()['groups'][0]['conversation_id'], 'R:a')

    def test_real_session_schema_text_id_with_mixed_non_chat_prefixes(self):
        """已实证版本的 id TEXT 混有非聊天标识，不能误选 con_numeric_id 或拒绝群来源。"""
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'session.db.dec.db'))
        try:
            conn.execute('CREATE TABLE conversation_table '
                         '(con_numeric_id INTEGER, id TEXT, name TEXT, room_info_pb BLOB)')
            conn.executemany('INSERT INTO conversation_table VALUES (?,?,?,NULL)', [
                (1, 'AN', '其他记录'), (2, 'AP', '其他记录'), (3, 'CL', '其他记录'),
                (4, 'E:one', '其他记录'), (5, 'FI', '其他记录'), (6, 'M:one', '其他记录'),
                (7, 'O:one', '其他记录'), (8, 'R:room', '项目群'),
                (9, 'S:self_peer', '同事'), (10, 'Y:one', '其他会话'),
            ])
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.query()['groups'], [
            {'conversation_id': 'R:room', 'name': '项目群'},
        ])
        self.fx.add_message_db([])
        resolved = self.fx.query(['--task', 'resolve', '--name', '项目群'])
        self.assertTrue(resolved['ok'], resolved)
        self.assertEqual(resolved['sources']['conversation_table'], 'ok')
        self.assertEqual(resolved['candidates'][0]['conversation_id'], 'R:room')

    def test_existing_explicit_conversation_id_column_keeps_priority(self):
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'session.db.dec.db'))
        try:
            conn.execute('CREATE TABLE conversation_table '
                         '(id TEXT, conversation_id TEXT, name TEXT)')
            conn.execute('INSERT INTO conversation_table VALUES (?,?,?)',
                         ('R:legacy', 'R:preferred', '项目群'))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.query()['groups'][0]['conversation_id'], 'R:preferred')

    def test_integer_id_does_not_establish_conversation_schema(self):
        """SQLite 整数列甚至可存文本，不能仅因列名 id 就信任数值主键为群会话 ID。"""
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'session.db.dec.db'))
        try:
            conn.execute('CREATE TABLE conversation_table (id INTEGER, name TEXT)')
            conn.executemany('INSERT INTO conversation_table VALUES (?,?)',
                             [(1, '甲群'), ('R:stored-text', '乙群')])
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.query()['error'], 'SCHEMA_UNSUPPORTED')

    def test_missing_source_fails_instead_of_reporting_no_groups(self):
        payload = self.query()
        self.assertEqual(payload['error'], 'DB_UNREADABLE')
        self.assertEqual(os.listdir(self.fx.dir), [])

    def test_missing_columns_fail_instead_of_reporting_no_groups(self):
        self.fx.add_session_db([], with_name=False)
        self.assertEqual(self.query()['error'], 'SCHEMA_UNSUPPORTED')

    def test_protocol_preserves_group_names(self):
        self.fx.add_session_db([('R:a', '项目%_群')])
        payload, _ = fixtures.run_driver([
            'query', '--workdir', self.fx.dir, '--task', 'groups', '--name', '%_',
        ])
        self.assertEqual(payload['groups'], [{'conversation_id': 'R:a', 'name': '项目%_群'}])


class GroupHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wecom-group-history-')
        self.addCleanup(self.tmp.cleanup)
        self.fx = fixtures.WorkdirFixture(self.tmp.name)
        self.fx.add_message_db([
            fixtures.msg_row(GROUP, fixtures.PEER, 2, 300, fixtures.text_content('new')),
            fixtures.msg_row(GROUP, fixtures.SELF, 2, 100, fixtures.text_content('old')),
            fixtures.msg_row(GROUP, fixtures.PEER, 1017, 200),
            fixtures.msg_row(GROUP, fixtures.PEER, 2, 200, fixtures.text_content('same second')),
            fixtures.msg_row('R:other', fixtures.PEER, 2, 500),
            fixtures.msg_row(GROUP, fixtures.PEER, 2, 50, fixtures.text_content('late sync')),
            fixtures.msg_row(GROUP, fixtures.PEER, 2, None),
            fixtures.msg_row(GROUP, fixtures.PEER, 2, -1),
            fixtures.msg_row(GROUP, fixtures.PEER, 2, 'bad-time'),
            fixtures.msg_row(GROUP, fixtures.PEER, 2, 1.5),
            fixtures.msg_row('R:other', fixtures.PEER, 2, None),
        ])

    def query(self, *args):
        return self.fx.query(['--task', 'history', '--conv', GROUP] + list(args))

    def test_recent_selection_uses_send_time_not_insertion_id(self):
        """迟到入库的旧消息不能挤掉最近消息；同秒消息稳定地按 ID 升序展示。"""
        payload = self.query('--limit', '3')
        self.assertTrue(payload['ok'], payload)
        self.assertEqual([m['message_id'] for m in payload['messages']], [3, 4, 1])
        self.assertEqual([m['send_time'] for m in payload['messages']], [200, 200, 300])
        self.assertEqual(payload['count'], 3)
        self.assertTrue(payload['has_more'])
        self.assertEqual(payload['invalid_time_count'], 4)
        self.assertIsNone(payload['messages'][0]['text'])
        self.assertFalse(payload['messages'][0]['trusted'])
        self.assertNotIn('next_cursor_id', payload)

    def test_time_bounds_are_inclusive_and_limit_is_applied_after_filter(self):
        payload = self.query('--since', '100', '--until', '200', '--limit', '3')
        self.assertEqual([m['message_id'] for m in payload['messages']], [2, 3, 4])
        self.assertFalse(payload['has_more'])
        self.assertEqual(payload['invalid_time_count'], 4)
        single = self.query('--since', '200', '--until', '200', '--limit', '1')
        self.assertEqual([m['message_id'] for m in single['messages']], [4])
        self.assertTrue(single['has_more'])

    def test_one_sided_bounds_and_empty_range(self):
        self.assertEqual([m['message_id'] for m in self.query('--since', '300')['messages']], [1])
        self.assertEqual([m['message_id'] for m in self.query('--until', '50')['messages']], [6])
        empty = self.query('--since', '301')
        self.assertEqual(empty['messages'], [])
        self.assertEqual(empty['count'], 0)
        self.assertFalse(empty['has_more'])
        self.assertEqual(empty['invalid_time_count'], 4)

    def test_zero_time_is_valid_and_invalid_count_is_group_scoped(self):
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'message.db.dec.db'))
        try:
            fixtures.insert_messages(conn, [fixtures.msg_row(GROUP, fixtures.PEER, 2, 0)])
        finally:
            conn.close()
        payload = self.query('--since', '0', '--until', '0')
        self.assertEqual([m['send_time'] for m in payload['messages']], [0])
        self.assertEqual(payload['invalid_time_count'], 4)

    def test_last_beijing_four_digit_year_second_is_included_but_later_is_invalid(self):
        """输出时间可用北京时间四位年份展示；超过最后一秒需显式计入异常记录。"""
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'message.db.dec.db'))
        try:
            fixtures.insert_messages(conn, [
                fixtures.msg_row(GROUP, fixtures.PEER, 2, 253402271999),
                fixtures.msg_row(GROUP, fixtures.PEER, 2, 253402272000),
                fixtures.msg_row(GROUP, fixtures.PEER, 2, 9223372036854775807),
            ])
        finally:
            conn.close()
        payload = self.query('--since', '253402271999')
        self.assertEqual([m['send_time'] for m in payload['messages']], [253402271999])
        self.assertEqual(payload['invalid_time_count'], 6)
        self.assertFalse(payload['has_more'])

    def test_empty_group_is_successful(self):
        payload = self.fx.query(['--task', 'history', '--conv', 'R:empty'])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['messages'], [])
        self.assertEqual(payload['invalid_time_count'], 0)
        self.assertFalse(payload['has_more'])

    def test_old_messages_task_keeps_insertion_cursor_contract(self):
        payload = self.fx.query(['--task', 'messages', '--conv', GROUP, '--after-id', '2', '--limit', '2'])
        self.assertEqual([m['message_id'] for m in payload['messages']], [3, 4])
        self.assertEqual(payload['next_cursor_id'], 4)

    def test_protocol_history(self):
        payload, _ = fixtures.run_driver([
            'query', '--workdir', self.fx.dir, '--task', 'history', '--conv', GROUP, '--limit', '1',
        ])
        self.assertEqual([m['message_id'] for m in payload['messages']], [1])
        self.assertEqual(payload['invalid_time_count'], 4)

    def test_bad_args_are_rejected_before_database_access(self):
        cases = [
            ['--task', 'history'], ['--task', 'history', '--conv', 'S:a_b'],
            ['--task', 'history', '--conv', 'R:'],
            ['--task', 'history', '--conv', GROUP, '--since', '3', '--until', '2'],
            ['--task', 'history', '--conv', GROUP, '--until', '-1'],
            ['--task', 'history', '--conv', GROUP, '--until', '1.5'],
            ['--task', 'history', '--conv', GROUP, '--after-id', '0'],
            ['--task', 'history', '--conv', GROUP, '--limit', '0'],
            ['--task', 'history', '--conv', GROUP, '--until', '9223372036854775808'],
            ['--task', 'groups', '--limit', '9223372036854775807'],
            ['--task', 'groups', '--since', '1'],
            ['--task', 'groups', '--until', '1'],
            ['--task', 'messages', '--conv', GROUP, '--until', '1'],
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(self.fx.query(args)['error'], 'BAD_ARGS')

    def test_missing_history_schema_fails_explicitly(self):
        with tempfile.TemporaryDirectory(prefix='wecom-history-schema-') as tmp:
            fx = fixtures.WorkdirFixture(tmp)
            conn = sqlite3.connect(os.path.join(fx.dir, 'message.db.dec.db'))
            try:
                conn.execute('CREATE TABLE unrelated (x INTEGER)')
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(fx.query(['--task', 'history', '--conv', GROUP])['error'],
                             'SCHEMA_UNSUPPORTED')


if __name__ == '__main__':
    unittest.main()
