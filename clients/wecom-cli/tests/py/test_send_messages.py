"""发送确认专用投影：匿名明文临时库，不访问真实账号、窗口或剪贴板。"""
import os
import sqlite3
import tempfile
import unittest

import test_localdb_query as fixtures

drv = fixtures.drv
CONV = 'R:send-confirmation'


def field(number, data):
    return fixtures._varint(number * 8 + 2) + fixtures._varint(len(data)) + data


def text_parts(*parts):
    return b''.join(fixtures.text_content(part) for part in parts)


class SendMessagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wecom-send-query-')
        self.addCleanup(self.tmp.cleanup)
        self.fx = fixtures.WorkdirFixture(self.tmp.name)

    def add_rows(self, rows):
        self.fx.add_message_db(rows)

    def query(self, *args, task='send-messages'):
        return self.fx.query(['--task', task, '--conv', CONV] + list(args))

    def row(self, content, time=1700000000, content_type=2, conv=CONV, sender=fixtures.SELF):
        return fixtures.msg_row(conv, sender, content_type, time, content)

    def test_complete_multisegment_text_is_usable_without_changing_old_messages(self):
        expected = 'line-one\nline-two \U0001f604\nlast'
        self.add_rows([self.row(text_parts('line-one\n', 'line-two \U0001f604', '\nlast'))])
        self.fx.add_user_db([(fixtures.SELF, 'Synthetic Employee', '', '')])
        before = self.query('--after-id', '0', '--account', fixtures.SELF, task='messages')
        sent = self.query('--after-id', '0', '--account', fixtures.SELF)
        self.assertTrue(sent['ok'], sent)
        self.assertEqual(sent['task'], 'send-messages')
        self.assertEqual(sent['messages'][0]['text'], expected)
        self.assertTrue(sent['messages'][0]['trusted'])
        self.assertFalse(before['messages'][0]['trusted'])
        self.assertNotEqual(before['messages'][0]['text'], expected)
        self.assertEqual(list(sent['messages'][0]), list(before['messages'][0]))
        for key in ('sender_name', 'sender_name_source', 'sender_platform'):
            self.assertEqual(sent['messages'][0][key], before['messages'][0][key],
                             'send-body completeness must not fork the sender identity projection')
        self.assertEqual(sent['messages'][0]['sender_name'], 'Synthetic Employee')
        self.assertEqual(sent['messages'][0]['sender_platform'], 'wecom')
        self.assertEqual(sent['next_anchor'], before['next_anchor'])
        self.assertEqual(before, self.query('--after-id', '0', '--account', fixtures.SELF, task='messages'))

    def test_plain_type_zero_and_whitespace_are_preserved_exactly(self):
        expected = ' leading\r\n\U0001f604\ttrailing '
        self.add_rows([self.row(field(1, expected.encode('utf-8')), content_type=0),
                       self.row(text_parts(' leading\r', '\n\U0001f604\ttrailing '))])
        sent = self.query('--after-id', '0')
        self.assertEqual([row['text'] for row in sent['messages']], [expected, expected])
        self.assertTrue(all(row['trusted'] for row in sent['messages']))

    def test_mentions_unknown_and_damaged_fragments_never_certify_a_send(self):
        mention = field(1, b'\x08\x05' + field(2, b'\x08\x64'))
        unknown_kind = field(1, b'\x08\x09' + field(2, field(1, b'text')))
        unknown_payload = field(1, b'\x08\x00' + field(2, field(1, b'text') + field(2, b'extra')))
        cases = {
            'mention': text_parts('before') + mention + text_parts('after'),
            'unknown_kind': text_parts('before') + unknown_kind,
            'unknown_outer': text_parts('before') + field(3, b'extra'),
            'unknown_payload': unknown_payload,
            'truncated': text_parts('before', 'after')[:-1],
            'invalid_utf8': field(1, b'\x08\x00' + field(2, field(1, b'\xff'))),
        }
        self.add_rows([self.row(blob) for blob in cases.values()])
        sent = self.query('--after-id', '0')
        self.assertEqual(sent['count'], len(cases))
        for name, row in zip(cases, sent['messages']):
            with self.subTest(name=name):
                self.assertFalse(row['trusted'], 'displayable fallback cannot prove a complete outgoing body')

    def test_nontext_content_cannot_become_trusted_through_text_shaped_bytes(self):
        self.add_rows([self.row(text_parts('looks like text'), content_type=1017)])
        row = self.query()['messages'][0]
        self.assertIsNone(row['text'])
        self.assertFalse(row['trusted'])

    def test_legacy_and_send_cursors_interoperate_in_both_directions(self):
        self.add_rows([self.row(text_parts('first', '-tail')),
                       self.row(text_parts('second', '-tail')),
                       self.row(text_parts('third', '-tail'))])
        old = self.query('--after-id', '0', '--limit', '1', task='messages')
        middle = self.query('--after-id', str(old['next_cursor_id']), '--anchor', old['next_anchor'], '--limit', '1')
        self.assertTrue(middle['ok'], middle)
        self.assertEqual(middle['messages'][0]['text'], 'second-tail')
        old_middle = self.query('--after-id', '1', '--limit', '1', task='messages')
        self.assertEqual(middle['next_anchor'], old_middle['next_anchor'])
        last = self.query('--after-id', str(middle['next_cursor_id']), '--anchor', middle['next_anchor'], task='messages')
        self.assertTrue(last['ok'], last)
        self.assertEqual(last['messages'][0]['message_id'], 3)
        empty = self.query('--after-id', str(last['next_cursor_id']), '--anchor', last['next_anchor'])
        self.assertEqual(empty['messages'], [])
        self.assertEqual((empty['next_cursor_id'], empty['next_anchor']), (3, last['next_anchor']))

    def test_stale_legacy_anchor_is_not_bypassed_by_send_projection(self):
        self.add_rows([self.row(text_parts('first', '-tail')), self.row(text_parts('second'))])
        first = self.query('--after-id', '0', '--limit', '1')
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'message.db.dec.db'))
        try:
            conn.execute('UPDATE message_table SET content=? WHERE message_id=1', (text_parts('changed'),))
            conn.commit()
        finally:
            conn.close()
        result = self.query('--after-id', '1', '--anchor', first['next_anchor'])
        self.assertEqual(result, {'ok': False, 'error': 'CURSOR_STALE'})

    def test_inclusive_time_bounds_do_not_change_insertion_order_or_conversation_scope(self):
        self.add_rows([self.row(text_parts('before'), time=99),
                       self.row(text_parts('upper'), time=102),
                       self.row(text_parts('lower-late'), time=100),
                       self.row(text_parts('middle'), time=101),
                       self.row(text_parts('after'), time=103),
                       self.row(text_parts('other-conversation'), time=101, conv='R:other')])
        result = self.query('--after-id', '0', '--since', '100', '--until', '102')
        self.assertEqual([row['message_id'] for row in result['messages']], [2, 3, 4])
        self.assertEqual(result['next_cursor_id'], 4)
        self.assertEqual([row['send_time'] for row in result['messages']], [102, 100, 101])
        latest = self.query('--since', '100', '--until', '102', '--limit', '2')
        self.assertEqual([row['message_id'] for row in latest['messages']], [3, 4])

    def test_bounded_paging_can_find_a_send_after_fifty_peer_records(self):
        self.add_rows([self.row(text_parts('other'), sender=fixtures.PEER) for _ in range(50)] +
                      [self.row(text_parts('expected', '-body'))])
        first = self.query('--after-id', '0', '--limit', '50')
        self.assertEqual(first['count'], 50)
        self.assertTrue(all(row['sender_id'] == fixtures.PEER for row in first['messages']))
        next_page = self.query('--after-id', str(first['next_cursor_id']), '--anchor', first['next_anchor'], '--limit', '50')
        self.assertEqual(next_page['count'], 1)
        own = next_page['messages'][0]
        self.assertEqual((own['sender_id'], own['text'], own['trusted']), (fixtures.SELF, 'expected-body', True))

    def test_send_specific_time_and_sqlite_bounds_are_validated_before_query(self):
        self.add_rows([])
        for args in [('--since', '2', '--until', '1'), ('--until', '-1'),
                     ('--until', str(1 << 63)), ('--since', str(1 << 63)),
                     ('--after-id', str(1 << 63)), ('--limit', str(1 << 63))]:
            with self.subTest(args=args):
                self.assertEqual(self.query(*args)['error'], 'BAD_ARGS')
        self.assertEqual(self.query('--until', '1', task='messages')['error'], 'BAD_ARGS')


if __name__ == '__main__':
    unittest.main()
