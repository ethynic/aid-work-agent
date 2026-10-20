# tests/py/test_localdb_watch.py — P2 任务 3.2：watch 模式（多会话增量查询，合成解密库 fixture）
#
# 运行方式（不在 TS 默认 test 链内）：npm run test:py
# （本文件零第三方依赖——watch 不触碰加密；fixture 策略同 test_localdb_query.py：
#   sqlite3 直接建【明文库】，命名对齐驱动定位规则 message.db.dec.db）
#
# 场景表（任务 3.2 py 契约 + 第九轮评审 P1 锚点校验）：
#   多会话增量：watermarks 之后的新消息按入库序返回（跨会话独立、互不串扰）；next_anchor =
#     已返回末端行摘要（TS 存入水位供下轮校验）
#   锚点有效性校验（第九轮评审 P1）：非零水位行缺失/摘要不符/旧桥整数水位（无锚）→ 水位失效
#     按 0 全量重报 + watermark_reset:true（宁重报不漏报）；0 水位不参与校验
#   水位不推进：无新消息 / 会话在表中无行的会话不出现在输出与 next_watermarks
#   last_sender_state 双向：self/peer 按最后一条可参与判定消息判向；尾部系统消息
#     （1017/38/31）不顶掉判定（回退 last_participating）；全系统类 → null；未传 --account → null
#   limit 截断：每会话独立 limit；has_more=true；next_watermarks 只推进到已返回消息
#   --exclude-types 追加语义；协议错误（BAD_ARGS / DB_UNREADABLE / SCHEMA_UNSUPPORTED）
import json
import os
import sqlite3
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))  # clients/wecom-cli
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, 'drivers', 'py'))

import wecom_localdb as drv  # noqa: E402

SELF = '1688850000000001'
PEER = '1688850000000002'
CONV_S = 'S:%s_%s' % (SELF, PEER)
CONV_R1 = 'R:wrTdKGroup01'
CONV_R2 = 'R:wrTdKGroup02'

MESSAGE_DDL = """
CREATE TABLE message_table (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id TEXT,
    sequence INTEGER,
    sender_id TEXT,
    conversation_id TEXT,
    content_type INTEGER,
    send_time INTEGER,
    flag INTEGER,
    content BLOB,
    devinfo BLOB,
    from_app_id TEXT,
    extra_content BLOB,
    local_extra_content BLOB,
    client_id TEXT
)
"""
MESSAGE_COLS = ('server_id', 'sequence', 'sender_id', 'conversation_id', 'content_type',
                'send_time', 'flag', 'content', 'devinfo', 'from_app_id', 'extra_content',
                'local_extra_content', 'client_id')


def _varint(n):
    out = bytearray()
    while True:
        b7 = n & 0x7F
        n >>= 7
        if n:
            out.append(b7 | 0x80)
        else:
            out.append(b7)
            return bytes(out)


def text_content(s):
    raw = s.encode('utf-8')
    inner = b'\x0a' + _varint(len(raw)) + raw
    mid = b'\x08\x00' + b'\x12' + _varint(len(inner)) + inner
    return b'\x0a' + _varint(len(mid)) + mid


def msg_row(conv, sender, ct, send_time, content=b'', server_id=None, seq=None):
    return dict(zip(MESSAGE_COLS, (server_id, seq, sender, conv, ct, send_time, 0, content,
                                   b'', '', b'', b'', None)))


def insert_messages(conn, rows):
    for r in rows:
        conn.execute('INSERT INTO message_table(%s) VALUES (%s)'
                     % (','.join(MESSAGE_COLS), ','.join('?' * len(MESSAGE_COLS))),
                     tuple(r[c] for c in MESSAGE_COLS))
    conn.commit()


class WatchFixture:
    """合成解密库工作目录 + 水位文件（watch 模式经文件传水位，与 TS 桥一致）。"""

    def __init__(self, tmp):
        self.dir = os.path.join(tmp, 'work')
        os.makedirs(self.dir, exist_ok=True)

    def add_message_db(self, rows):
        path = os.path.join(self.dir, 'message.db.dec.db')
        conn = sqlite3.connect(path)
        try:
            conn.execute(MESSAGE_DDL)
            insert_messages(conn, rows)
        finally:
            conn.close()

    def append_rows(self, rows):
        conn = sqlite3.connect(os.path.join(self.dir, 'message.db.dec.db'))
        try:
            insert_messages(conn, rows)
        finally:
            conn.close()

    def write_watermarks(self, marks):
        path = os.path.join(self.dir, 'watermarks.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(marks, f)
        return path

    def anchor_of(self, conv, message_id):
        """库中指定行的消息稳定字段摘要（与驱动 _message_anchor 同一函数，测试侧独立取行）。"""
        conn = sqlite3.connect(os.path.join(self.dir, 'message.db.dec.db'))
        try:
            row = conn.execute(
                'SELECT %s FROM message_table WHERE message_id=? AND conversation_id=?'
                % ', '.join(drv.QUERY_MESSAGE_COLUMNS), (message_id, conv)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise AssertionError('anchor_of：行不存在 conv=%s message_id=%s' % (conv, message_id))
        return drv._message_anchor(drv._message_row(row))

    def anchored(self, marks):
        """水位条目补锚点（第九轮评审 P1：非零水位须带 _message_anchor 摘要才能通过校验；
        0 水位与显式 {'last_message_id','anchor'} 条目原样保留）。"""
        out = {}
        for conv, value in marks.items():
            if isinstance(value, dict):
                out[conv] = value
            elif value > 0:
                out[conv] = {'last_message_id': value, 'anchor': self.anchor_of(conv, value)}
            else:
                out[conv] = value
        return out

    def watch(self, marks, account=SELF, extra=None):
        args = ['--workdir', self.dir, '--watermarks', self.write_watermarks(marks)]
        if account is not None:
            args += ['--account', account]
        if extra:
            args += extra
        return drv.cmd_watch(args)


def base_rows():
    """CONV_S 三条（self 末尾）+ CONV_R1 两条（peer 开头）+ CONV_R2 一条系统类。"""
    return [
        msg_row(CONV_S, PEER, 2, 1700000001, text_content('你好'), server_id='a1', seq=1),
        msg_row(CONV_S, SELF, 2, 1700000002, text_content('在的'), server_id='a2', seq=2),
        msg_row(CONV_S, SELF, 2, 1700000003, text_content('还有事吗'), server_id='a3', seq=3),
        msg_row(CONV_R1, PEER, 2, 1700000004, text_content('群消息一'), server_id='b1', seq=4),
        msg_row(CONV_R1, PEER, 2, 1700000005, text_content('群消息二'), server_id='b2', seq=5),
        msg_row(CONV_R2, PEER, 1017, 1700000006, b'\x08\x01', server_id='c1', seq=6),
    ]


class WatchIncrementTest(unittest.TestCase):
    """多会话增量 + 跨会话独立 + 水位推进语义。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='wecom-watch-')
        self.fx = WatchFixture(self._tmp.name)
        self.fx.add_message_db(base_rows())

    def tearDown(self):
        self._tmp.cleanup()

    def test_multi_conversation_increment_independent(self):
        """多会话增量：各会话只返回水位之后的消息；next_watermarks = 已返回末端。"""
        payload = self.fx.watch(self.fx.anchored({CONV_S: 1, CONV_R1: 0}))
        self.assertTrue(payload['ok'], payload)
        per = {c['conversation_id']: c for c in payload['per_conversation']}
        self.assertEqual([m['message_id'] for m in per[CONV_S]['messages']], [2, 3])
        self.assertEqual([m['message_id'] for m in per[CONV_R1]['messages']], [4, 5])
        self.assertEqual(per[CONV_S]['messages'][0]['text'], '在的')
        self.assertTrue(per[CONV_S]['messages'][0]['trusted'])
        self.assertEqual(payload['next_watermarks'], {CONV_S: 3, CONV_R1: 5})
        # next_anchor = 已返回末端行的消息摘要（TS 存入水位供下轮校验）；正常轮无 watermark_reset
        self.assertEqual(per[CONV_S]['next_anchor'], self.fx.anchor_of(CONV_S, 3))
        self.assertNotIn('watermark_reset', per[CONV_S])

    def test_no_new_message_not_in_next_watermarks(self):
        """水位已到末端：该会话无输出、水位不推进（不出现在 next_watermarks）。"""
        payload = self.fx.watch(self.fx.anchored({CONV_S: 3, CONV_R1: 5}))
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['per_conversation'], [])
        self.assertEqual(payload['next_watermarks'], {})
        partial = self.fx.watch(self.fx.anchored({CONV_S: 3, CONV_R1: 4}))
        ids = [c['conversation_id'] for c in partial['per_conversation']]
        self.assertEqual(ids, [CONV_R1], '只有出现新消息的会话进输出')
        self.assertEqual(partial['next_watermarks'], {CONV_R1: 5})

    def test_anchor_mismatch_resets_watermark(self):
        """锚点不匹配（同 message_id、内容已变的同文件重写，第九轮评审 P1）：水位失效按 0
        处理——全量返回该会话当前行 + watermark_reset:true + next_anchor 换新。"""
        wrong = self.fx.anchor_of(CONV_R1, 4)  # 同长度合法摘要，但不是 CONV_S 水位行的
        payload = self.fx.watch({CONV_S: {'last_message_id': 2, 'anchor': wrong}})
        self.assertTrue(payload['ok'])
        per = {c['conversation_id']: c for c in payload['per_conversation']}
        self.assertEqual([m['message_id'] for m in per[CONV_S]['messages']], [1, 2, 3],
                         '锚点失效 → 按 0 全量重报（绝不只报 3 漏 1、2）')
        self.assertTrue(per[CONV_S]['watermark_reset'])
        self.assertEqual(payload['next_watermarks'], {CONV_S: 3})
        self.assertEqual(per[CONV_S]['next_anchor'], self.fx.anchor_of(CONV_S, 3))

    def test_watermark_row_missing_resets(self):
        """水位行已不存在（同文件删行，max 不回退）：同样判失效按 0 全量重报。"""
        payload = self.fx.watch({CONV_S: {'last_message_id': 999, 'anchor': 'a' * 64}})
        self.assertTrue(payload['ok'])
        per = {c['conversation_id']: c for c in payload['per_conversation']}
        self.assertEqual([m['message_id'] for m in per[CONV_S]['messages']], [1, 2, 3])
        self.assertTrue(per[CONV_S]['watermark_reset'])

    def test_legacy_int_watermark_without_anchor_resets(self):
        """旧桥整数水位（无锚点）：非零水位缺锚视为不可信 → 按 0 全量重报（宁重报不漏报）。"""
        payload = self.fx.watch({CONV_S: 2, CONV_R1: 0})
        per = {c['conversation_id']: c for c in payload['per_conversation']}
        self.assertEqual([m['message_id'] for m in per[CONV_S]['messages']], [1, 2, 3])
        self.assertTrue(per[CONV_S]['watermark_reset'])
        self.assertEqual([m['message_id'] for m in per[CONV_R1]['messages']], [4, 5],
                         '0 水位不参与锚点校验，语义不变')

    def test_unknown_conversation_omitted(self):
        """水位指向表中无行的会话（账号切换残留）：不输出、不推进。"""
        payload = self.fx.watch(self.fx.anchored({'R:missing': 0, CONV_S: 0}))
        self.assertTrue(payload['ok'])
        ids = [c['conversation_id'] for c in payload['per_conversation']]
        self.assertEqual(ids, [CONV_S])
        self.assertEqual(payload['next_watermarks'], {CONV_S: 3})

    def test_limit_truncates_per_conversation_and_has_more(self):
        """limit 截断：只返回 limit 条、has_more=true、水位只推进到已返回末端（不跳过余量）。"""
        payload = self.fx.watch({CONV_S: 0, CONV_R1: 0}, extra=['--limit', '1'])
        per = {c['conversation_id']: c for c in payload['per_conversation']}
        self.assertEqual([m['message_id'] for m in per[CONV_S]['messages']], [1])
        self.assertTrue(per[CONV_S]['has_more'])
        self.assertEqual(per[CONV_S]['last_message_id'], 3, 'last_message_id 报整表当前最大')
        self.assertEqual([m['message_id'] for m in per[CONV_R1]['messages']], [4])
        self.assertTrue(per[CONV_R1]['has_more'])
        self.assertEqual(payload['next_watermarks'], {CONV_S: 1, CONV_R1: 4}, '只推进到已返回消息')
        # 续传：从上轮水位（带返回末端锚点）继续取到剩余消息，R1 本轮取完 has_more=false
        follow = self.fx.watch(self.fx.anchored(payload['next_watermarks']), extra=['--limit', '1'])
        per2 = {c['conversation_id']: c for c in follow['per_conversation']}
        self.assertEqual([m['message_id'] for m in per2[CONV_S]['messages']], [2])
        self.assertTrue(per2[CONV_S]['has_more'])
        self.assertEqual([m['message_id'] for m in per2[CONV_R1]['messages']], [5])
        self.assertFalse(per2[CONV_R1]['has_more'])

    def test_row_projection_matches_messages_task(self):
        """行投影同 messages 任务（含 text/trusted；系统类行 text=null/trusted=false）。"""
        payload = self.fx.watch({CONV_R2: 0})
        row = payload['per_conversation'][0]['messages'][0]
        self.assertEqual(row['content_type'], 1017)
        self.assertIsNone(row['text'])
        self.assertFalse(row['trusted'])
        self.assertEqual(
            list(row.keys()),
            ['message_id', 'server_id', 'sequence', 'sender_id', 'conversation_id',
             'content_type', 'send_time', 'flag', 'text', 'trusted'])

    def test_protocol_errors(self):
        """BAD_ARGS（缺参/坏水位文件）/ DB_UNREADABLE（缺库）/ SCHEMA_UNSUPPORTED。"""
        self.assertFalse(drv.cmd_watch(['--workdir', self.fx.dir])['ok'])
        self.assertFalse(drv.cmd_watch(['--watermarks', 'x.json'])['ok'])
        bad = os.path.join(self.fx.dir, 'bad.json')
        with open(bad, 'w', encoding='utf-8') as f:
            f.write('{"conv": "not-an-int"}')
        self.assertEqual(drv.cmd_watch(['--workdir', self.fx.dir, '--watermarks', bad])['error'],
                         'BAD_ARGS')
        empty = os.path.join(self.fx.dir, 'missing-work')
        os.makedirs(empty)
        r = drv.cmd_watch(['--workdir', empty, '--watermarks', self.fx.write_watermarks({CONV_S: 0})])
        self.assertEqual(r['error'], 'DB_UNREADABLE')
        schema_dir = os.path.join(self.fx.dir, 'schema-work')
        os.makedirs(schema_dir)
        conn = sqlite3.connect(os.path.join(schema_dir, 'message.db.dec.db'))
        try:
            conn.execute('CREATE TABLE other_table(x TEXT)')
        finally:
            conn.close()
        r2 = drv.cmd_watch(['--workdir', schema_dir,
                            '--watermarks', self.fx.write_watermarks({CONV_S: 0})])
        self.assertEqual(r2['error'], 'SCHEMA_UNSUPPORTED')
        negative = drv.cmd_watch(['--workdir', self.fx.dir, '--watermarks',
                                  self.fx.write_watermarks({CONV_S: -1})])
        self.assertEqual(negative['error'], 'BAD_ARGS', '负水位 BAD_ARGS')


class WatchLastSenderTest(unittest.TestCase):
    """last_sender_state 双向判定（排除表回退语义）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='wecom-watch-sender-')
        self.fx = WatchFixture(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_self_and_peer_directions(self):
        self.fx.add_message_db([
            msg_row(CONV_S, SELF, 2, 1700000001, text_content('我说的'), server_id='s1'),
            msg_row(CONV_R1, PEER, 2, 1700000002, text_content('对方说的'), server_id='s2'),
        ])
        per = {c['conversation_id']: c for c in self.fx.watch({CONV_S: 0, CONV_R1: 0})['per_conversation']}
        self.assertEqual(per[CONV_S]['last_sender_state'], 'self')
        self.assertEqual(per[CONV_R1]['last_sender_state'], 'peer')

    def test_trailing_system_message_falls_back_to_participating(self):
        """尾部系统消息不顶掉判定：按最后一条可参与判定消息判向（对齐 unread 语义）。"""
        self.fx.add_message_db([
            msg_row(CONV_S, PEER, 2, 1700000001, text_content('在吗'), server_id='s1'),
            msg_row(CONV_S, SELF, 1017, 1700000002, b'\x08\x01', server_id='s2'),
            msg_row(CONV_R1, SELF, 2, 1700000003, text_content('好的'), server_id='s3'),
            msg_row(CONV_R1, SELF, 38, 1700000004, b'\x08\x01', server_id='s4'),
        ])
        per = {c['conversation_id']: c for c in self.fx.watch({CONV_S: 0, CONV_R1: 0})['per_conversation']}
        self.assertEqual(per[CONV_S]['last_sender_state'], 'peer', '系统在最后 → 回退可参与判定消息')
        self.assertEqual(per[CONV_R1]['last_sender_state'], 'self')

    def test_all_system_conversation_and_missing_account(self):
        """全系统类 → null；未传 --account → null；--exclude-types 追加后同语义。"""
        self.fx.add_message_db([
            msg_row(CONV_S, SELF, 31, 1700000001, b'\x08\x01', server_id='s1'),
            msg_row(CONV_S, SELF, 1017, 1700000002, b'\x08\x01', server_id='s2'),
            msg_row(CONV_R1, SELF, 2, 1700000003, text_content('普通'), server_id='s3'),
        ])
        per = {c['conversation_id']: c
               for c in self.fx.watch({CONV_S: 0, CONV_R1: 0}, account=None)['per_conversation']}
        self.assertIsNone(per[CONV_S]['last_sender_state'], '全会话皆系统类 → null')
        self.assertIsNone(per[CONV_R1]['last_sender_state'], '未传 --account → null')
        per2 = {c['conversation_id']: c
                for c in self.fx.watch({CONV_S: 0, CONV_R1: 0})['per_conversation']}
        self.assertIsNone(per2[CONV_S]['last_sender_state'])
        self.assertEqual(per2[CONV_R1]['last_sender_state'], 'self')
        # --exclude-types 追加：把 type=2 追加进排除表后，R1 亦无可参与判定消息 → null
        per3 = {c['conversation_id']: c
                for c in self.fx.watch({CONV_R1: 0}, extra=['--exclude-types', '2'])['per_conversation']}
        self.assertIsNone(per3[CONV_R1]['last_sender_state'])

    def test_increment_only_new_rows_state_over_whole_conversation(self):
        """last_sender_state 按【整会话】最后可参与判定消息判（不是仅返回集）：
        增量只含旧消息时仍报当前最后发言方。"""
        self.fx.add_message_db([
            msg_row(CONV_S, PEER, 2, 1700000001, text_content('早'), server_id='s1'),
            msg_row(CONV_S, SELF, 2, 1700000002, text_content('早，开会吗'), server_id='s2'),
        ])
        per = {c['conversation_id']: c
               for c in self.fx.watch(self.fx.anchored({CONV_S: 1}))['per_conversation']}
        self.assertEqual([m['message_id'] for m in per[CONV_S]['messages']], [2])
        self.assertEqual(per[CONV_S]['last_sender_state'], 'self')


if __name__ == '__main__':
    unittest.main()
