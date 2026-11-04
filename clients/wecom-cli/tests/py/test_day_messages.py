"""日期模式 day-messages：[start,end) 半开范围、(send_time,message_id) keyset
续页、首轮固定上界/快照、背景（include_context）与空日语义。"""
import os
import tempfile
import unittest

import test_localdb_query as fixtures
from test_localdb_query import msg_row, text_content

drv = fixtures.drv
CONV = 'R:day'
ACCOUNT = 'acc1'

# 2026-11-03 Asia/Shanghai = [1791072000, 1791158400) UTC epoch 秒
DAY_START, DAY_END = 1793635200, 1793721600


def _rows():
    """背景（零点前最近3条之一）+ 当日 5 条 + 次日凌晨 1 条 + 无效时间 1 条。"""
    return [
        msg_row(CONV, 'peer1', 2, DAY_START - 7200, text_content('背景-2小时前')),
        msg_row(CONV, 'peer1', 2, DAY_START - 60, text_content('背景-1分钟前')),
        msg_row(CONV, 'peer1', 2, DAY_START + 60, text_content('当日第一条')),
        msg_row(CONV, ACCOUNT, 2, DAY_START + 120, text_content('当日本人')),
        msg_row(CONV, 'peer1', 2, DAY_START + 180, text_content('当日第三条')),
        msg_row(CONV, 'peer1', 2, DAY_START + 240, text_content('当日第四条')),
        msg_row(CONV, 'peer1', 2, DAY_END + 60, text_content('次日不该出现')),
        msg_row(CONV, 'peer1', 2, None, text_content('无效时间不计')),
    ]


class DayMessagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wecom-day-')
        self.addCleanup(self.tmp.cleanup)
        self.fx = fixtures.WorkdirFixture(self.tmp.name)
        self.fx.add_message_db(_rows())

    def query(self, *args):
        return self.fx.query(['--task', 'day-messages', '--conv', CONV,
                              '--account', ACCOUNT] + list(args))

    def test_first_page_bounds_and_keyset_pagination(self):
        payload = self.query('--date', '2026-11-03', '--tz', 'Asia/Shanghai', '--limit', '2')
        self.assertTrue(payload['ok'], payload)
        self.assertEqual(payload['start_utc'], DAY_START)
        self.assertEqual(payload['end_utc'], DAY_END)
        self.assertEqual([m['message_id'] for m in payload['messages']], [3, 4])
        self.assertTrue(payload['has_more'])
        self.assertIsNotNone(payload['snapshot_at'])
        self.assertEqual(payload['max_message_id'], 8)
        # 续页：显式携带首轮范围/上界与页尾键；次日与无效时间不进入
        page2 = self.query('--start', str(DAY_START), '--end', str(DAY_END),
                           '--max-msg-id', '8',
                           '--after-send-time', str(DAY_START + 120),
                           '--after-msg-id', '4', '--limit', '10')
        self.assertEqual([m['message_id'] for m in page2['messages']], [5, 6])
        self.assertFalse(page2['has_more'])
        texts = [m.get('text') for m in page2['messages']]
        self.assertNotIn('次日不该出现', str(texts))
        self.assertEqual(page2['invalid_time_count'], 1)

    def test_include_context_bring_100_before_midnight(self):
        payload = self.query('--date', '2026-11-03', '--tz', 'Asia/Shanghai',
                             '--include-context', '--limit', '10')
        self.assertTrue(payload['ok'], payload)
        ids = [m['message_id'] for m in payload['messages']]
        self.assertEqual(ids, [1, 2, 3, 4, 5, 6])  # 背景与当日按时间共同分页
        self.assertEqual(payload['context_start'], DAY_START - 7200)
        self.assertTrue(payload['include_context'])

    def test_context_boundary_keeps_composite_key_same_second(self):
        """评审 P2-13：零点前同秒大量消息时，背景恰为最近100条（复合边界）。"""
        rows = [msg_row(CONV, 'peer1', 2, DAY_START - 60, text_content('背景-1分钟前'))]
        # 150 条同秒（零点前 30 秒），message_id 顺序插入
        for index in range(150):
            rows.append(msg_row(CONV, 'peer1', 2, DAY_START - 30, text_content(f'同秒背景{index}')))
        rows.append(msg_row(CONV, 'peer1', 2, DAY_START + 60, text_content('当日第一条')))
        self.fx = fixtures.WorkdirFixture(self.tmp.name + '-same-second-bg')
        self.fx.add_message_db(rows)
        payload = self.query('--date', '2026-11-03', '--tz', 'Asia/Shanghai',
                             '--include-context', '--limit', '200')
        self.assertTrue(payload['ok'], payload)
        background = [m for m in payload['messages'] if m['send_time'] < DAY_START]
        # 复合边界（评审 P2-13）：最近 100 条全部落在同秒（id 52..151），
        # 边界为该秒 id=52；同秒更小 ID（2..51）与更早秒（id 1）都不纳入。
        self.assertEqual(len(background), 100, len(background))
        ids = [m['message_id'] for m in background]
        self.assertEqual(sorted(ids), list(range(52, 152)))
        self.assertNotIn(1, ids)
        day = [m for m in payload['messages'] if m['send_time'] >= DAY_START]
        self.assertEqual([m['message_id'] for m in day], [152])

    def test_no_background_day_pages_continue_without_context_args(self):
        """评审二 P1-1：零点前无消息时，首屏无背景下界，跨页续页不被拒。"""
        self.fx = fixtures.WorkdirFixture(self.tmp.name + '-nobg')
        rows = [msg_row(CONV, 'peer1', 2, DAY_START + 60 + i, text_content(f'当日{i}'))
                for i in range(5)]
        self.fx.add_message_db(rows)
        first = self.query('--date', '2026-11-03', '--tz', 'Asia/Shanghai',
                           '--include-context', '--limit', '2')
        self.assertTrue(first['ok'], first)
        self.assertIsNone(first['context_start'])
        self.assertIsNone(first['context_msg_id'])
        self.assertTrue(first['include_context'])
        self.assertTrue(first['has_more'])
        # 续页：include-context 继承但无背景参数（复现原 BAD_ARGS 场景）
        page2 = self.query('--start', str(DAY_START), '--end', str(DAY_END),
                           '--max-msg-id', '5', '--include-context',
                           '--after-send-time', str(DAY_START + 61),
                           '--after-msg-id', '2', '--limit', '10')
        self.assertTrue(page2['ok'], page2)  # 不再 BAD_ARGS
        self.assertEqual([m['message_id'] for m in page2['messages']], [3, 4, 5])
        self.assertFalse(page2['has_more'])

    def test_empty_day_returns_empty_messages(self):
        payload = self.query('--date', '2026-11-01', '--tz', 'Asia/Shanghai', '--limit', '10')
        self.assertTrue(payload['ok'], payload)
        self.assertEqual(payload['messages'], [])
        self.assertFalse(payload['has_more'])

    def test_empty_day_with_context_returns_background_only(self):
        payload = self.query('--date', '2026-11-01', '--tz', 'Asia/Shanghai',
                             '--include-context', '--limit', '10')
        self.assertTrue(payload['ok'], payload)
        # 2026-11-01 零点前无记录：背景为空，仍为合法空结果
        self.assertEqual(payload['messages'], [])
        self.assertFalse(payload['has_more'])

    def test_same_second_orders_by_message_id(self):
        # 重建整库：原 8 行 + 两条同秒消息（id=7/8，当日第四条顺延为 id=6）
        self.fx = fixtures.WorkdirFixture(self.tmp.name + '-same-second')
        self.fx.add_message_db(_rows() + [
            msg_row(CONV, 'peer1', 2, DAY_START + 300, text_content('同秒A')),
            msg_row(CONV, 'peer1', 2, DAY_START + 300, text_content('同秒B')),
        ])
        payload = self.query('--date', '2026-11-03', '--tz', 'Asia/Shanghai', '--limit', '3')
        ids = [m['message_id'] for m in payload['messages']]
        self.assertEqual(ids, [3, 4, 5])
        # 从同秒页尾（id=5）续页：同秒下一条（id=7）不得漏掉
        page2 = self.query('--start', str(DAY_START), '--end', str(DAY_END),
                           '--max-msg-id', '10',
                           '--after-send-time', str(DAY_START + 180),
                           '--after-msg-id', '5', '--limit', '10')
        ids2 = [m['message_id'] for m in page2['messages']]
        # 同秒内按 message_id 稳定顺序：6(+240s) 后是同秒对 9/10（id 7=次日、8=无效时间被排除）
        self.assertEqual(ids2, [6, 9, 10])

    def test_upper_bound_excludes_late_synced_rows(self):
        """首轮固定源消息上界：晚同步（更大 message_id）不进入本页。"""
        payload = self.query('--date', '2026-11-03', '--tz', 'Asia/Shanghai', '--limit', '10')
        self.assertEqual(payload['max_message_id'], 8)
        # 模拟晚同步：新行 message_id=9、send_time 在当日
        conn = fixtures.sqlite3.connect(os.path.join(self.fx.dir, 'message.db.dec.db'))
        try:
            row = msg_row(CONV, 'peer1', 2, DAY_START + 90, text_content('晚同步'))
            conn.execute('INSERT INTO message_table(%s) VALUES (%s)'
                         % (','.join(fixtures.MESSAGE_COLS), ','.join('?' * len(fixtures.MESSAGE_COLS))),
                         tuple(row[c] for c in fixtures.MESSAGE_COLS))
            conn.commit()
        finally:
            conn.close()
        page2 = self.query('--start', str(DAY_START), '--end', str(DAY_END),
                           '--max-msg-id', '8',
                           '--after-send-time', str(DAY_START + 60),
                           '--after-msg-id', '3', '--limit', '10')
        ids = [m['message_id'] for m in page2['messages']]
        self.assertNotIn(9, ids)  # 晚同步记录由下次读取纳入


if __name__ == '__main__':
    unittest.main()
