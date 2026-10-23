"""history 有序文本、结构化提及及用户名映射；仅使用合成 protobuf 与用户库。"""
import os
import sqlite3
import tempfile
import unittest

import test_localdb_query as fixtures

drv = fixtures.drv
GROUP = 'R:mentions'
SENDER = '100'
MAX_USER_ID = '18446744073709551615'
USERS = [(SENDER, '发言人', '', ''), ('200', '当前姓名', '', ''),
         ('201', '第二成员', '', ''), (MAX_USER_ID, '大ID成员', '', '')]


def varint(value):
    return fixtures._varint(value)


def field(number, wire, value):
    tag = varint((number << 3) | wire)
    if wire == 0:
        return tag + varint(value)
    if wire == 2:
        return tag + varint(len(value)) + value
    return tag + value


def segment(kind, payload, reverse=False):
    fields = [field(1, 0, kind), field(2, 2, payload)]
    return field(1, 2, b''.join(reversed(fields) if reverse else fields))


def text(value, reverse=False):
    return segment(0, field(1, 2, value.encode('utf-8')), reverse)


def mention(user_id, name=None, reverse=False):
    payload = field(1, 0, int(user_id))
    if name is not None:
        payload += field(2, 2, name.encode('utf-8'))
    return segment(5, payload, reverse)


class GroupMentionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wecom-history-mentions-')
        self.addCleanup(self.tmp.cleanup)
        self.sequence = 0

    def history(self, contents, users=USERS, content_type=2):
        self.sequence += 1
        fx = fixtures.WorkdirFixture(self.tmp.name, 'work%d' % self.sequence)
        fx.add_message_db([
            fixtures.msg_row(GROUP, SENDER, content_type, 1700000000 + index, content)
            for index, content in enumerate(contents)
        ])
        if users is not None:
            fx.add_user_db(users)
        return fx.query(['--task', 'history', '--conv', GROUP, '--limit', str(len(contents))])

    def one(self, content, **kwargs):
        result = self.history([content], **kwargs)
        self.assertTrue(result['ok'], result)
        return result, result['messages'][0]

    def test_plain_and_multiple_text_segments_preserve_whitespace_and_emoji(self):
        result, message = self.one(text('中文😀\n') + text(' \t') + text('尾段'))
        self.assertEqual(message['text'], '中文😀\n \t尾段')
        self.assertEqual(message['sender_id'], SENDER)
        self.assertEqual(message['sender_name'], '发言人')
        self.assertEqual(message['sender_name_source'], 'user_table')
        self.assertEqual(message['mentions'], [])
        self.assertTrue(message['trusted'])
        self.assertTrue(message['mentions_complete'])
        self.assertFalse(result['naming_incomplete'])

    def test_mention_only_uses_historical_name_before_current_user_name(self):
        result, message = self.one(mention('200', '历史姓名'))
        self.assertEqual(message['text'], '@历史姓名')
        self.assertEqual(message['mentions'], [
            {'kind': 'user', 'user_id': '200', 'name': '历史姓名', 'name_source': 'message'},
        ])
        self.assertFalse(result['naming_incomplete'])
        self.assertTrue(message['trusted'])

    def test_repeated_mentions_and_all_follow_segment_order(self):
        _, message = self.one(mention('200', '甲') + text(' ') + mention('201', '乙')
                              + text(' ') + mention('200', '甲') + text(' ') + mention('0'))
        self.assertEqual(message['text'], '@甲 @乙 @甲 @所有人')
        self.assertEqual([item['user_id'] for item in message['mentions']], ['200', '201', '200', '0'])
        self.assertEqual(message['mentions'][-1],
                         {'kind': 'all', 'user_id': '0', 'name': '所有人', 'name_source': 'message'})
        self.assertTrue(message['mentions_complete'])

    def test_empty_or_missing_mention_name_can_use_exact_user_id(self):
        for historical in ('', None, '  '):
            with self.subTest(historical=historical):
                _, message = self.one(mention('200', historical))
                self.assertEqual(message['text'], '@当前姓名')
                self.assertEqual(message['mentions'][0]['name_source'], 'user_table')

    def test_unknown_mention_name_keeps_id_and_explicit_unknown_display(self):
        result, message = self.one(mention('999'))
        self.assertEqual(message['text'], '@未知成员(999)')
        self.assertEqual(message['mentions'][0],
                         {'kind': 'user', 'user_id': '999', 'name': None, 'name_source': 'unresolved'})
        self.assertTrue(result['naming_incomplete'])

    def test_same_display_name_does_not_merge_different_users(self):
        _, message = self.one(mention('200', '同名') + mention('201', '同名'))
        self.assertEqual([item['user_id'] for item in message['mentions']], ['200', '201'])
        self.assertEqual(message['text'], '@同名@同名')

    def test_uint64_identity_remains_string(self):
        _, message = self.one(mention(MAX_USER_ID))
        self.assertEqual(message['mentions'][0]['user_id'], MAX_USER_ID)
        self.assertEqual(message['text'], '@大ID成员')

    def test_quote_prefix_and_literal_at_are_not_structured_mentions(self):
        quote = '"引用者：\n很长的前文 @字面成员，还用这个库么"\n------\n'
        _, message = self.one(text(quote) + text('是的') + mention('200', '历史姓名') + text(' '))
        self.assertEqual(message['text'], quote + '是的@历史姓名 ')
        self.assertEqual(len(message['mentions']), 1)
        self.assertEqual(message['mentions'][0]['user_id'], '200')
        self.assertTrue(message['trusted'])
        self.assertNotIn('quote', message)

    def test_quote_then_mention_and_current_reply_body_are_all_preserved(self):
        prefix = '"引用者：\n前文"\n------\n'
        _, message = self.one(text(prefix) + mention('200', '历史姓名') + text(' 表名是这个：t_bank '))
        self.assertEqual(message['text'], prefix + '@历史姓名 表名是这个：t_bank ')

    def test_valid_field_reordering_does_not_change_message(self):
        _, message = self.one(text('答复', reverse=True) + mention('200', '甲', reverse=True))
        self.assertEqual(message['text'], '答复@甲')
        self.assertTrue(message['trusted'])

    def test_unknown_segment_has_visible_gap_without_losing_known_neighbors(self):
        _, message = self.one(text('前') + segment(9, field(1, 2, b'unknown')) + text('后'))
        self.assertEqual(message['text'], '前' + drv._HISTORY_GAP + '后')
        self.assertFalse(message['trusted'])
        self.assertFalse(message['mentions_complete'])

    def test_full_multibyte_tag_is_consumed_and_later_segments_remain_readable(self):
        for wire, value in [(0, 123), (1, b'12345678'), (2, b'unknown'), (5, b'1234')]:
            with self.subTest(wire=wire):
                _, message = self.one(text('前') + field(16, wire, value) + text('后'))
                self.assertIn('前' + drv._HISTORY_GAP + '后', message['text'])
                self.assertFalse(message['trusted'])
                self.assertFalse(message['mentions_complete'])

    def test_unknown_wire_truncated_length_and_bad_tag_cannot_claim_complete(self):
        malformed = [varint((16 << 3) | 3), b'\x0a\x80', b'\x0a\x05\x01', b'\x00',
                     b'\x80' * 10, b'\x0a' + b'\x80' * 9 + b'\x02']
        for suffix in malformed:
            with self.subTest(suffix=suffix):
                _, message = self.one(text('已知') + suffix)
                self.assertTrue(message['text'].startswith('已知'))
                self.assertIn(drv._HISTORY_GAP, message['text'])
                self.assertFalse(message['trusted'])
                self.assertFalse(message['mentions_complete'])

    def test_bad_utf8_text_and_mention_name_are_explicit(self):
        malformed = [segment(0, field(1, 2, b'\xff')),
                     segment(5, field(1, 0, 200) + field(2, 2, b'\xff'))]
        for piece in malformed:
            with self.subTest(piece=piece):
                _, message = self.one(text('前') + piece + text('后'))
                self.assertIn('前', message['text'])
                self.assertIn('后', message['text'])
                self.assertIn(drv._HISTORY_GAP, message['text'])
                self.assertFalse(message['trusted'])
                self.assertFalse(message['mentions_complete'])

    def test_utf8_control_values_cannot_be_trusted_as_body_or_name(self):
        _, body = self.one(text('前') + text('二进制\x00值') + text('后'))
        self.assertEqual(body['text'], '前' + drv._HISTORY_GAP + '后')
        self.assertFalse(body['trusted'])
        self.assertFalse(body['mentions_complete'])
        _, named = self.one(mention('200', '脏\x01名'))
        self.assertEqual(named['mentions'][0]['name'], '当前姓名')
        self.assertEqual(named['mentions'][0]['name_source'], 'user_table')
        self.assertFalse(named['trusted'])
        self.assertFalse(named['mentions_complete'])

    def test_empty_text_and_allowed_line_controls_are_preserved(self):
        _, message = self.one(text('') + text('\n\r\t'))
        self.assertEqual(message['text'], '\n\r\t')
        self.assertTrue(message['trusted'])

    def test_missing_required_fields_and_duplicate_identity_are_not_guessed(self):
        malformed = [field(1, 2, field(2, 2, field(1, 2, b'text'))),
                     segment(5, field(2, 2, b'name')),
                     segment(5, field(1, 0, 200) + field(1, 0, 201)),
                     segment(0, field(1, 2, b'one') + field(1, 2, b'two'))]
        for piece in malformed:
            with self.subTest(piece=piece):
                _, message = self.one(text('前') + piece + text('后'))
                self.assertIn(drv._HISTORY_GAP, message['text'])
                self.assertEqual(message['mentions'], [])
                self.assertFalse(message['mentions_complete'])

    def test_unsupported_varint_identity_overflow_is_not_truncated(self):
        bad_id = b'\x08' + b'\xff' * 9 + b'\x02'
        _, message = self.one(text('前') + segment(5, bad_id) + text('后'))
        self.assertEqual(message['mentions'], [])
        self.assertFalse(message['trusted'])

    def test_no_user_source_returns_explicit_unresolved_names(self):
        result, message = self.one(text('答复') + mention('200'), users=None)
        self.assertIsNone(message['sender_name'])
        self.assertEqual(message['sender_name_source'], 'unresolved')
        self.assertEqual(message['mentions'][0]['name_source'], 'unresolved')
        self.assertTrue(result['naming_incomplete'])

    def test_duplicate_or_blank_user_names_are_not_selected_arbitrarily(self):
        for values in [('甲', '乙'), ('同名', '同名'), ('',), ('  ',), (None,), ('坏\x00名',)]:
            with self.subTest(values=values):
                users = [(SENDER, value, '', '') for value in values]
                result, message = self.one(text('内容'), users=users)
                self.assertIsNone(message['sender_name'])
                self.assertTrue(result['naming_incomplete'])

    def test_name_lookup_is_exact_and_batch_safe(self):
        with tempfile.TemporaryDirectory(prefix='wecom-names-batch-') as tmp:
            fx = fixtures.WorkdirFixture(tmp)
            users = [(str(index), '成员%d' % index, '', '') for index in range(1, 806)]
            users.append(('0001', '前导零成员', '', ''))
            fx.add_user_db(users)
            names = drv._history_user_names(fx.dir, {str(index) for index in range(1, 806)} | {'0001'})
            self.assertEqual(len(names), 806)
            self.assertEqual(names['1'], '成员1')
            self.assertEqual(names['0001'], '前导零成员')
            self.assertEqual(names['805'], '成员805')

    def test_missing_user_schema_is_explicitly_unresolved(self):
        with tempfile.TemporaryDirectory(prefix='wecom-names-schema-') as tmp:
            fx = fixtures.WorkdirFixture(tmp)
            conn = sqlite3.connect(os.path.join(fx.dir, 'user.db.dec.db'))
            try:
                conn.execute('CREATE TABLE user_table (id TEXT)')
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(drv._history_user_names(fx.dir, {SENDER}), {})

    def test_type_zero_and_old_extraction_contract_are_unchanged(self):
        raw = field(1, 2, 'type0 @字面'.encode('utf-8'))
        _, message = self.one(raw, content_type=0)
        self.assertEqual(message['text'], 'type0 @字面')
        self.assertEqual(message['mentions'], [])
        self.assertTrue(message['trusted'])
        self.assertEqual(drv.extract_content(fixtures.text_content('普通消息'), 2),
                         {'text': '普通消息', 'trusted': True})

    def test_type_zero_segmented_text_mentions_and_quote_are_recovered(self):
        quote = '"引用者：\n较长的前文 @字面成员"\n------\n'
        result = self.history([
            text('第一段') + text('第二段'),
            mention('200', '历史姓名') + text(' 正文 ') + mention('0'),
            text(quote) + text('是的') + mention('200', '历史姓名'),
        ], content_type=0)
        self.assertEqual([message['text'] for message in result['messages']], [
            '第一段第二段', '@历史姓名 正文 @所有人', quote + '是的@历史姓名',
        ])
        self.assertEqual([len(message['mentions']) for message in result['messages']], [0, 2, 1])
        self.assertTrue(all(message['trusted'] and message['mentions_complete']
                            for message in result['messages']))

    def test_literal_gap_text_is_recognized_without_duplication_or_lost_order(self):
        for content_type in (0, 2):
            with self.subTest(content_type=content_type):
                _, literal = self.one(text(drv._HISTORY_GAP), content_type=content_type)
                self.assertEqual(literal['text'], drv._HISTORY_GAP)
                self.assertTrue(literal['trusted'])
                _, mixed = self.one(text(drv._HISTORY_GAP) + segment(9, b'')
                                    + text('后'), content_type=content_type)
                self.assertEqual(mixed['text'], drv._HISTORY_GAP + drv._HISTORY_GAP + '后')
                self.assertFalse(mixed['trusted'])
                self.assertFalse(mixed['mentions_complete'])

    def test_literal_gap_text_before_root_damage_still_gets_missing_fragment_marker(self):
        _, message = self.one(text(drv._HISTORY_GAP) + b'\x0a\x80')
        self.assertEqual(message['text'], drv._HISTORY_GAP * 2)
        self.assertFalse(message['trusted'])

    def test_completely_unreadable_content_is_not_an_empty_success(self):
        _, message = self.one(b'\xff\xff')
        self.assertEqual(message['text'], drv._HISTORY_GAP)
        self.assertFalse(message['trusted'])
        self.assertFalse(message['mentions_complete'])


if __name__ == '__main__':
    unittest.main()
