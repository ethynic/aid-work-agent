"""Business intent: identify real customer senders and never lose one failed target."""
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from test_localdb_query import (drv, WorkdirFixture, SELF, PEER, CONV_S, CONV_R1,
                               msg_row, text_content, insert_messages)


class BusinessSessionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fx = WorkdirFixture(self.temp.name)
        self.path = self.fx.add_message_db([
            msg_row(CONV_S, PEER, 2, 100, text_content('old customer')),
            msg_row(CONV_S, SELF, 2, 101, text_content('already answered')),
            msg_row(CONV_R1, PEER, 2, 102, text_content('customer question')),
            msg_row(CONV_R1, SELF, 2, 103, text_content('staff context')),
        ])
        self.fx.add_user_db([(SELF, 'Staff', '', ''), (PEER, 'Customer', '', ''), ('300', 'Unknown external', '', '')])
        self.fx.add_session_db([(CONV_S, 'Customer'), (CONV_R1, 'Group')])
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'user.db.dec.db'))
        try:
            conn.execute('ALTER TABLE user_table ADD COLUMN corp_id INTEGER')
            conn.execute('UPDATE user_table SET corp_id=? WHERE id=?', (12345, SELF))
            conn.execute('UPDATE user_table SET corp_id=? WHERE id=?', (1970325134026788, PEER))
            conn.execute('UPDATE user_table SET corp_id=? WHERE id=?', (77777, '300'))
            conn.commit()
        finally:
            conn.close()

    def read(self, **request):
        return drv.cmd_sessions(self.fx.dir, SELF, request)

    def append(self, rows):
        conn = sqlite3.connect(self.path)
        try:
            insert_messages(conn, rows)
        finally:
            conn.close()

    def test_platform_is_exact_corp_not_external_guess(self):
        self.assertEqual(drv._sender_platforms(self.fx.dir, [SELF, PEER, '300', 'missing'], SELF),
                         {SELF: 'wecom', PEER: 'wechat', '300': 'unknown', 'missing': 'unknown'})

    def test_verified_wechat_corp_resolves_without_requiring_external_relation_table(self):
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'user.db.dec.db'))
        try:
            conn.execute('INSERT INTO user_table VALUES (?,?,?,?,?)', ('600', 'Customer', '', '', 12345))
            conn.commit()
        finally:
            conn.close()
        data = self.read(targets=[{'name': 'Customer@微信'}], limit=50)
        self.assertTrue(data['ok'], data)
        self.assertEqual(data['failed'], [])
        self.assertEqual(data['selected_sessions'], [CONV_S])
        metadata = self.read(targets=[{'conversation_id': CONV_S}], limit=1, metadata_only=True)
        self.assertEqual(metadata['failed'], [])

    def test_metadata_resolution_does_not_parse_oversized_bodies(self):
        self.append([msg_row(CONV_S, PEER, 2, 150, text_content('x' * 20000))])
        with patch.object(drv, '_history_messages', side_effect=AssertionError('resolution parsed message bodies')):
            resolved = self.read(targets=[{'conversation_id': CONV_S}], limit=1, metadata_only=True)
        self.assertTrue(resolved['ok'], resolved)
        self.assertEqual(resolved['selected_sessions'], [CONV_S])
        self.assertEqual(resolved['sessions'], [])
        self.assertEqual(resolved['failed'], [])

    def test_group_staff_tail_does_not_hide_customer_question(self):
        data = self.read(limit=50)
        self.assertTrue(data['ok'], data)
        self.assertTrue(data['baseline'])
        self.assertEqual([p['conversation_id'] for p in data['pending_sessions']], [CONV_R1])
        group = next(s for s in data['sessions'] if s['conversation_id'] == CONV_R1)
        self.assertEqual([m['sender_platform'] for m in group['messages']], ['wechat', 'wecom'])

    def test_incremental_new_old_customer_is_returned_without_unread_badge(self):
        first = self.read(limit=50)
        self.append([msg_row(CONV_S, PEER, 2, 104, text_content('new question'))])
        data = self.read(state=first['state'], limit=50)
        self.assertEqual([m['text'] for s in data['sessions'] for m in s['messages']], ['new question'])
        self.assertEqual(data['pending_sessions'][0]['conversation_id'], CONV_S)
        again = self.read(state=data['state'], limit=50)
        self.assertEqual(again['sessions'], [])

    def test_total_budget_paginates_without_advancing_unreturned_messages(self):
        first = self.read(limit=50)
        self.append([msg_row(CONV_R1, PEER, 2, 110 + n, text_content(str(n))) for n in range(5)])
        cursor = first['state']
        seen = []
        for _ in range(3):
            data = self.read(state=cursor, limit=2)
            seen.extend(m['text'] for s in data['sessions'] for m in s['messages'])
            cursor = data['state']
        self.assertEqual(seen, ['0', '1', '2', '3', '4'])

    def test_unresolved_target_does_not_lose_messages_when_it_later_resolves(self):
        targets = [{'name': 'Customer'}, {'name': 'Later'}]
        first = self.read(targets=targets, limit=50)
        self.assertEqual(len(first['failed']), 1)
        later = 'S:%s_400' % SELF
        self.append([msg_row(later, '400', 2, 120, text_content('arrived while unresolved')),
                     msg_row(CONV_S, PEER, 2, 121, text_content('other target progresses'))])
        second = self.read(targets=targets, state=first['state'], limit=50)
        self.assertEqual([m['text'] for s in second['sessions'] for m in s['messages']], ['other target progresses'])
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'user.db.dec.db'))
        try:
            conn.execute('INSERT INTO user_table VALUES (?,?,?,?,?)', ('400', 'Later', '', '', 1970325134026788))
            conn.commit()
        finally:
            conn.close()
        third = self.read(targets=targets, state=second['state'], limit=50)
        self.assertEqual([m['text'] for s in third['sessions'] for m in s['messages']], ['arrived while unresolved'])

    def test_oversized_message_is_reported_and_other_session_can_advance(self):
        first = self.read(limit=50)
        self.append([msg_row(CONV_R1, PEER, 2, 130, text_content('x' * 30000)),
                     msg_row(CONV_S, PEER, 2, 131, text_content('independent question'))])
        data = self.read(state=first['state'], limit=50)
        self.assertEqual(data['failed'][0]['code'], 'CONTENT_UNAVAILABLE')
        self.assertEqual([m['text'] for s in data['sessions'] for m in s['messages']], ['independent question'])
        self.assertIn(CONV_R1, data['state']['retry'])
        self.assertLess(len(json.dumps(data).encode()), 16384)

    def test_modified_anchor_rejects_without_new_baseline(self):
        first = self.read(limit=50)
        conn = sqlite3.connect(self.path)
        try:
            conn.execute('DELETE FROM message_table WHERE message_id=?', (first['state']['after_id'],))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.read(state=first['state'], limit=50)['error'], 'CURSOR_STALE')

    def test_baseline_pages_old_sessions_and_keeps_arrivals_during_initial_scan(self):
        old_conv = 'S:%s_500' % SELF
        self.append([msg_row(old_conv, PEER, 2, 90, text_content('old unanswered customer'))])
        self.append([msg_row(CONV_R1, PEER, 2, 200 + n, text_content('hot group')) for n in range(60)])
        first = self.read(limit=2)
        self.assertTrue(first['baseline'])
        self.assertTrue(first['has_more'])
        self.append([msg_row(CONV_S, PEER, 2, 300, text_content('arrived during baseline'))])
        second = self.read(state=first['state'], limit=2)
        self.assertTrue(second['baseline'])
        self.assertIn('old unanswered customer', [m['text'] for s in second['sessions'] for m in s['messages']])
        third = self.read(state=second['state'], limit=2)
        self.assertFalse(third['baseline'])
        self.assertEqual([m['text'] for s in third['sessions'] for m in s['messages']], ['arrived during baseline'])

    def test_unknown_sender_stays_a_named_pending_gap_not_a_false_customer(self):
        first = self.read(limit=50)
        unknown_conv = 'S:%s_300' % SELF
        self.append([msg_row(unknown_conv, '300', 2, 300, text_content('identity not synced yet'))])
        data = self.read(state=first['state'], limit=50)
        self.assertEqual(data['pending_sessions'][0]['message_ids'], [])
        self.assertEqual(data['pending_sessions'][0]['unknown_sender_message_ids'], [5])
        self.assertEqual(data['pending_sessions'][0]['basis'], 'sender_platform_unknown')

    def test_large_internal_session_list_does_not_consume_the_business_message_budget(self):
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'session.db.dec.db'))
        try:
            conn.executemany('INSERT INTO conversation_table (conversation_id,name) VALUES (?,?)',
                             [('R:z%040d' % n, 'Empty group %d' % n) for n in range(500)])
            conn.commit()
        finally:
            conn.close()
        data = self.read(limit=50)
        self.assertEqual(len(data['selected_sessions']), 502)
        self.assertEqual(data['failed'], [])
        self.assertIn('customer question', [m['text'] for s in data['sessions'] for m in s['messages']])
        self.assertFalse(data['has_more'], '500 empty sessions must not create 100 empty startup pages')
        self.assertIsNone(data['state']['context_after'])
        self.assertEqual(data['state']['start_fence'], 4)
        formerly_empty = 'R:z%040d' % 499
        self.append([msg_row(formerly_empty, PEER, 2, 800, text_content('new customer in an empty session'))])
        live = self.read(state=data['state'], limit=50)
        self.assertEqual(len(live['selected_sessions']), 502, 'empty sessions must remain in the monitored scope')
        self.assertEqual([m['text'] for s in live['sessions'] for m in s['messages']], ['new customer in an empty session'])
        self.assertFalse(live['sessions'][0]['messages'][0]['baseline'])
        self.assertEqual(live['pending_sessions'][0]['new_message_ids'], [5])

    def test_oversized_baseline_record_keeps_its_original_lower_bound(self):
        self.append([msg_row(CONV_S, PEER, 2, 400, text_content('x' * 20000))])
        first = self.read(targets=[{'conversation_id': CONV_S}], limit=50)
        self.assertEqual(first['failed'][0]['message_id'], 5)
        self.assertTrue(first['has_more'])
        self.assertLess(first['state']['retry'][CONV_S]['after_id'], 5)
        second = self.read(targets=[{'conversation_id': CONV_S}], state=first['state'], limit=50)
        self.assertEqual(second['failed'][0]['message_id'], 5)
        self.assertEqual(second['sessions'], [])
        # Once that same local record becomes readable, the retained cursor can return it.
        conn = sqlite3.connect(self.path)
        try:
            conn.execute('UPDATE message_table SET content=? WHERE message_id=5', (text_content('recovered'),))
            conn.commit()
        finally:
            conn.close()
        # The global anchor was also this record: its mutation must be explicit, never a silent reset.
        self.assertEqual(self.read(targets=[{'conversation_id': CONV_S}], state=second['state'], limit=50)['error'], 'CURSOR_STALE')

    def test_baseline_byte_page_does_not_repeat_the_same_small_context_forever(self):
        self.append([msg_row(CONV_S, PEER, 2, 500 + n, text_content(str(n) + 'x' * 5500)) for n in range(3)])
        cursor = None
        seen = []
        for _ in range(5):
            data = self.read(targets=[{'conversation_id': CONV_S}], state=cursor, limit=50) if cursor else self.read(targets=[{'conversation_id': CONV_S}], limit=50)
            self.assertEqual(data['failed'], [])
            seen.extend(m['message_id'] for s in data['sessions'] for m in s['messages'])
            self.assertTrue(all(m['baseline'] for s in data['sessions'] for m in s['messages']))
            cursor = data['state']
            if not data['has_more']:
                break
        else:
            self.fail('baseline byte pagination never made progress')
        self.assertEqual(sorted(seen), [1, 2, 5, 6, 7])

    def test_post_start_retry_keeps_new_identity_even_after_another_session_advances(self):
        first = self.read(limit=50)
        self.append([msg_row(CONV_R1, '300', 2, 600, text_content('x' * 30000)),
                     msg_row(CONV_S, PEER, 2, 601, text_content('independent progress'))])
        blocked = self.read(state=first['state'], limit=50)
        self.assertEqual(blocked['state']['after_id'], 6)
        self.assertEqual(blocked['state']['start_fence'], 4)
        conn = sqlite3.connect(self.path)
        try:
            conn.execute('UPDATE message_table SET content=? WHERE message_id=5', (text_content('now readable'),))
            conn.commit()
        finally:
            conn.close()
        recovered = self.read(state=blocked['state'], limit=50)
        message = recovered['sessions'][0]['messages'][0]
        self.assertEqual(message['message_id'], 5)
        self.assertFalse(message['baseline'])
        pending = recovered['pending_sessions'][0]
        self.assertFalse(pending['baseline'])
        self.assertFalse(pending['unknown_messages'][0]['baseline'])

    def test_unpublished_legacy_cursor_without_fence_never_guesses_ambiguous_retry_history(self):
        first = self.read(limit=50)
        legacy = dict(first['state'])
        del legacy['start_fence']
        self.append([msg_row(CONV_S, PEER, 2, 700, text_content('ordinary legacy continuation'))])
        compatible = self.read(state=legacy, limit=50)
        self.assertFalse(compatible['sessions'][0]['messages'][0]['baseline'])
        legacy['retry'][CONV_S] = {'after_id': legacy['after_id'], 'anchor': legacy['anchor']}
        self.assertEqual(self.read(state=legacy, limit=50)['error'], 'CURSOR_STALE')
