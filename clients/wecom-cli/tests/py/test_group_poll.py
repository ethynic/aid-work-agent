"""Poll pages must preserve exact mentions/text and detect cursor anchor loss, never silently skip."""
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
import test_localdb_query as f
from test_group_mentions import text, mention


class GroupPollTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wecom-group-poll-')
        self.addCleanup(self.temp.cleanup)
        self.fx = f.WorkdirFixture(self.temp.name)
        self.path = self.fx.add_message_db([
            f.msg_row('R:a', f.PEER, 2, 1700000000, text('一段\n') + mention(f.SELF, '本人') + text('尾段😀')),
            f.msg_row('R:b', f.PEER, 2, 1700000001, text('另一群')),
        ])
        self.fx.add_user_db([(f.PEER, '合成同事', '', '')])

    def poll(self, *args):
        return self.fx.query(['--task', 'group-poll', '--conv', 'R:a', '--limit', '10', *args])

    def test_history_projection_and_anchor_use_their_own_stable_formats(self):
        first = self.poll()
        self.assertTrue(first['ok'])
        self.assertEqual(first['messages'][0]['text'], '一段\n@本人尾段😀')
        self.assertEqual(first['messages'][0]['mentions'][0]['user_id'], f.SELF)
        self.assertTrue(first['messages'][0]['trusted'])
        # Anchor deliberately uses legacy identity fields, not the expanded mention display text.
        self.assertEqual(first['next_anchor'], f.drv._message_anchor(f.db_row_by_id(self.fx, 1)))
        again = self.poll('--after-id', str(first['next_cursor_id']), '--anchor', first['next_anchor'])
        self.assertTrue(again['ok'])
        self.assertEqual(again['messages'], [])
        self.assertEqual(again['next_anchor'], first['next_anchor'])

    def test_new_late_records_are_not_lost_and_other_group_does_not_advance_cursor(self):
        first = self.poll()
        with closing(sqlite3.connect(self.path)) as conn:
            f.insert_messages(conn, [f.msg_row('R:a', f.PEER, 2, 1600000000, text('迟到'))])
        next_page = self.poll('--after-id', '1', '--anchor', first['next_anchor'])
        self.assertEqual([row['message_id'] for row in next_page['messages']], [3])
        self.assertEqual(next_page['next_cursor_id'], 3)
        self.assertFalse(next_page['has_more'])

    def test_deleted_or_reused_anchor_fails_instead_of_replaying_or_rebasing(self):
        first = self.poll()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('DELETE FROM message_table WHERE message_id=1')
            conn.commit()
        self.assertEqual(self.poll('--after-id', '1', '--anchor', first['next_anchor'])['error'], 'CURSOR_STALE')

    def test_poll_refuses_filters_or_missing_anchors_that_could_hide_messages(self):
        for args in [('--since', '100'), ('--limit', '51'), ('--after-id', '1'),
                     ('--after-id', '0'), ('--anchor', '0' * 64), ('--after-id', '9007199254740992')]:
            self.assertEqual(self.poll(*args)['error'], 'BAD_ARGS', args)

    def test_payload_budget_pages_whole_messages_and_keeps_initial_latest_baseline(self):
        first = self.poll()
        with closing(sqlite3.connect(self.path)) as conn:
            f.insert_messages(conn, [f.msg_row('R:a', f.PEER, 2, 1700000010 + index, text('长' * 2000)) for index in range(5)])
        initial = self.poll()
        self.assertEqual(initial['next_cursor_id'], 7, 'initial context still establishes the current maximum')
        self.assertLess(initial['count'], 5)
        seen = []
        cursor, anchor = first['next_cursor_id'], first['next_anchor']
        while True:
            page = self.poll('--after-id', str(cursor), '--anchor', anchor)
            self.assertTrue(page['ok'])
            self.assertTrue(all(row['text'] == '长' * 2000 for row in page['messages']))
            seen.extend(row['message_id'] for row in page['messages'])
            cursor, anchor = page['next_cursor_id'], page['next_anchor']
            if not page['has_more']:
                break
        self.assertEqual(seen, [3, 4, 5, 6, 7])

    def test_single_oversize_message_is_explicit_failure_without_cursor_advance(self):
        first = self.poll()
        with closing(sqlite3.connect(self.path)) as conn:
            f.insert_messages(conn, [f.msg_row('R:a', f.PEER, 2, 1700000010, text('长' * 6000))])
        result = self.poll('--after-id', str(first['next_cursor_id']), '--anchor', first['next_anchor'])
        self.assertEqual(result, {'ok': False, 'error': 'CONTENT_TOO_LARGE'})


if __name__ == '__main__':
    unittest.main()
