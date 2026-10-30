"""Anonymous cards/system events: visible reading must not alter cursors or prove sends."""
import hashlib
import json
import sqlite3
import tempfile
import unittest

from test_localdb_query import (drv, WorkdirFixture, SELF, PEER, CONV_R1, _varint,
                               msg_row, text_content)


def field(number, value):
    return _varint(number * 8 + 2) + _varint(len(value)) + value


def card_content(texts, summary=None, sections=None):
    document = {'msgtype': 'sections', 'sections_flag': 0, 'userid_mapping': {'opaque-user': 'not body'},
                'sections': sections if sections is not None else [
                    {'type': 'basic', 'text': {'type': 'markdown', 'content': text},
                     'components': [], 'fields': []} for text in texts]}
    summary = '\n\n'.join(texts) if summary is None else summary
    return field(1, summary.encode()) + field(4, field(1, json.dumps(document).encode())
                                            + field(2, b'opaque-card-id'))


class CardSystemContentTest(unittest.TestCase):
    def test_sections_keep_order_paragraphs_and_markdown_not_json_metadata(self):
        parts = ['**Interview notice**\n\n> Candidate A\n> Candidate B',
                 'Schedule: tomorrow 14:00\n\n[Details](https://example.invalid)']
        result = drv.extract_content(card_content(parts, summary='summary only'), 80)
        self.assertEqual(result, {'text': '\n\n'.join(parts), 'trusted': True})

    def test_confirmed_scalar_metadata_preserves_complete_card_body(self):
        body = '**Notice**\n\n> anonymous customer\n\nNext steps'
        blob = card_content([body])
        self.assertEqual(drv.extract_content(blob + _varint(5 * 8) + _varint(123), 80),
                         {'text': body, 'trusted': True})
        # Only the observed scalar is metadata; an unknown payload may contain visible content.
        self.assertEqual(drv.extract_content(blob + field(5, b'unknown payload'), 80),
                         {'text': body, 'trusted': False})

    def test_unsupported_sections_fall_back_to_visible_root_without_claiming_completeness(self):
        sections = [{'type': 'basic', 'text': {'type': 'markdown', 'content': 'first'},
                     'components': [], 'fields': []},
                    {'type': 'image', 'url': 'opaque-image-ref'}]
        result = drv.extract_content(card_content([], summary='first\n\nvisible caption', sections=sections), 80)
        self.assertEqual(result, {'text': 'first\n\nvisible caption', 'trusted': False})

    def test_damaged_json_and_truncated_envelope_keep_available_body(self):
        root = field(1, b'visible body')
        for blob in (root + field(4, field(1, b'{broken json') + field(2, b'opaque-id')),
                     root + b'\x22\x7fbroken', root):
            with self.subTest(blob=blob):
                self.assertEqual(drv.extract_content(blob, 80), {'text': 'visible body', 'trusted': False})

    def test_unknown_card_without_root_keeps_known_section_and_never_extracts_opaque_id(self):
        partial = {'msgtype': 'sections', 'sections': [
            {'type': 'basic', 'text': {'type': 'markdown', 'content': 'known paragraph'},
             'components': [{'type': 'image', 'id': 'opaque-image-id'}]}]}
        blob = field(4, field(1, json.dumps(partial).encode()) + field(2, b'opaque-card-id'))
        self.assertEqual(drv.extract_content(blob, 80), {'text': 'known paragraph', 'trusted': False})
        self.assertEqual(drv.extract_content(field(4, field(2, b'opaque-id')), 80),
                         {'text': None, 'trusted': False})

    def test_exact_system_types_distinguish_rename_from_same_literal_chat(self):
        self.assertEqual(drv.extract_content('新群名'.encode(), 1001),
                         {'text': '群名变更为“新群名”', 'trusted': True})
        literal = '群名变更为“新群名”'
        self.assertEqual(drv.extract_content(text_content(literal), 2), {'text': literal, 'trusted': True})
        self.assertNotIn(2, drv.CONFIRMED_SYSTEM_TYPES)
        for ct, label in ((1002, '群成员邀请通知'), (1006, '群消息推送设置变更')):
            self.assertEqual(drv.extract_content(b'10000000000000001', ct), {'text': label, 'trusted': False})
        self.assertEqual(drv.extract_content(field(1, field(2, '机器人仅接收@消息'.encode())), 1022),
                         {'text': '机器人仅接收@消息', 'trusted': True})

    def test_readable_card_and_system_cannot_certify_outbound_plain_text(self):
        for ct, body in ((80, card_content(['outgoing body'])), (1001, b'outgoing body'),
                         (1022, field(1, field(2, b'outgoing body')))):
            row = (1, 'server', 1, SELF, CONV_R1, ct, 100, 0, body)
            with self.subTest(content_type=ct):
                self.assertTrue(drv._message_row(row)['trusted'])
                self.assertFalse(drv._send_message_row(row)['trusted'])

    def test_old_nontext_anchor_still_accepts_cursor_after_reading_upgrade(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = WorkdirFixture(temp)
            path = fixture.add_message_db([
                msg_row(CONV_R1, PEER, 80, 100, card_content(['readable now'])),
                msg_row(CONV_R1, SELF, 1001, 101, b'new name'),
                msg_row(CONV_R1, PEER, 2, 102, text_content('next')),
                msg_row(CONV_R1, SELF, 1001, 103, b'new name'),
                msg_row(CONV_R1, SELF, 1002, 104, b'10000000000000001'),
                msg_row(CONV_R1, SELF, 1006, 105, b'opaque-setting'),
                msg_row(CONV_R1, SELF, 1022, 106, field(1, field(2, b'bot hint')))])
            conn = sqlite3.connect(path)
            try:
                for mid, sender, stamp in ((1, PEER, 100), (2, SELF, 101)):
                    legacy = hashlib.sha256(json.dumps([mid, sender, CONV_R1, stamp, None],
                                                       separators=(',', ':')).encode()).hexdigest()
                    page = drv._query_messages(conn, CONV_R1, mid, 1, None, legacy)
                    self.assertTrue(page['ok'], page)
                    self.assertEqual(page['next_cursor_id'], mid + 1)
                last = fixture.query(['--task', 'last-per-conv'])
                self.assertEqual(last['conversations'][0]['last_participating']['message_id'], 3)
            finally:
                conn.close()

    def test_unrecognized_card_does_not_abort_a_message_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = WorkdirFixture(temp)
            fixture.add_message_db([msg_row(CONV_R1, PEER, 80, 100, b'\xff'),
                                    msg_row(CONV_R1, PEER, 2, 101, text_content('still readable'))])
            result = fixture.query(['--task', 'messages', '--conv', CONV_R1])
            self.assertTrue(result['ok'], result)
            self.assertEqual([m['text'] for m in result['messages']], [None, 'still readable'])
