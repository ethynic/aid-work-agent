# tests/py/test_localdb_query.py — 任务 1.8 / 1.9a：C8 读库查询 + C9a 内容提取（合成解密库 fixture）
#
# 运行方式（不在 TS 默认 test 链内）：
#   npm run test:py
#   或 venv python -m unittest discover -s tests/py -v
# （本文件零第三方依赖——query/extract 不触碰加密；同目录 test_wecom_localdb.py 才需要 pycryptodome）
#
# fixture 策略：sqlite3 直接建【明文库】（对齐真实 decrypt 产物语义：integrity ok 的自包含库）。
# message_table 按 P0 已确认列名建 14 列（其余 4 列未建模——查询按列名 SELECT 涉及列，前提成立）；
# content 构造器按 P0 冻结包装（0a<len>( 08<varint> 12<len>( 0a<len> <utf8> ) )）同源生成。
#
# 场景表（任务 1.8 / 1.9a）：
#   extract：严格合法（type0/type2、长文本多字节 varint、换行）/ 帧残留脏数据（trusted=false
#   仍有文本）/ 纯二进制 / 空 / None / 非文本类型 / 长度越界
#   messages：after-id/limit 边界 + 空批次 next_cursor=after-id / since 过滤 / 同秒多条入库序 /
#   会话隔离 / 非文本行 text=null / 排除表不影响 messages / 解密库两种命名
#   conversations：聚合与排序 / last_send_time 取水位行（迟到回退场景）/ last_anchor =
#   水位行消息摘要（watch 建基线锚，第九轮评审 P1）
#   last-per-conv：排除表双向语义（系统在最后回退 / 全系统 last_participating=null /
#   参与型最后=last）/ --exclude-types 追加语义（不缩减基础表）/ --with-names 反向取名
#   （R 群名认 conversation_table、S 联系人名认 user_table、无命中 null；第九轮评审 P2；
#   S: match_names=名称/别名/备注全部非空值、session 来源不可用 → naming_incomplete:true，
#   第十轮评审 P2）
#   resolve：群名多候选 / 零候选 / user name/alias/备注命中 / 来源库缺失 unavailable /
#   列名不可确认 schema-unknown / id 列值形态发现
#   协议：BAD_ARGS / 库缺失 DB_UNREADABLE（只读不建文件）/ 坏库 SQL_ERROR / 子进程端到端
#   schema 兼容（评审 v6）：缺 message_table/缺列的真实库 → 稳定 SCHEMA_UNSUPPORTED
#   （doctor 归 compat 的协议依据）；坏库保持 SQL_ERROR
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))  # clients/wecom-cli
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, 'drivers', 'py'))

import wecom_localdb as drv  # noqa: E402

DRIVER = os.path.join(_ROOT, 'drivers', 'py', 'wecom_localdb.py')

SELF = '1688850000000001'
PEER = '1688850000000002'
CONV_S = 'S:%s_%s' % (SELF, PEER)
CONV_R1 = 'R:wrTdKGroup01'
CONV_R2 = 'R:wrTdKGroup02'

# P0 已确认列名（14/18；其余 4 列与 C8 契约无关，fixture 不建模）
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
    """P0 冻结包装：0a<len1>( 08<varint> 12<len2>( 0a<len3> <utf8> ) )。"""
    raw = s.encode('utf-8')
    inner = b'\x0a' + _varint(len(raw)) + raw
    mid = b'\x08\x00' + b'\x12' + _varint(len(inner)) + inner
    return b'\x0a' + _varint(len(mid)) + mid


def msg_row(conv, sender, ct, send_time, content=b'', server_id=None, seq=None, flag=0):
    return dict(zip(MESSAGE_COLS, (server_id, seq, sender, conv, ct, send_time, flag, content,
                                   b'', '', b'', b'', None)))


def insert_messages(conn, rows):
    for r in rows:
        conn.execute('INSERT INTO message_table(%s) VALUES (%s)'
                     % (','.join(MESSAGE_COLS), ','.join('?' * len(MESSAGE_COLS))),
                     tuple(r[c] for c in MESSAGE_COLS))
    conn.commit()


class ExtractContentTest(unittest.TestCase):
    """C9a 纯函数：严格解析优先 / 递归兜底标 trusted=false / 都失败 null。"""

    def test_strict_type2_valid(self):
        self.assertEqual(drv.extract_content(text_content('你好'), 2),
                         {'text': '你好', 'trusted': True})

    def test_strict_type0_valid(self):
        raw = '顶层单字段'.encode('utf-8')
        content = b'\x0a' + _varint(len(raw)) + raw
        self.assertEqual(drv.extract_content(content, 0), {'text': '顶层单字段', 'trusted': True})

    def test_strict_long_text_multibyte_varint(self):
        """>127 字节文本（长度 varint 多字节）仍走严格路径。"""
        s = 'a' * 300 + '中文' * 50
        self.assertEqual(drv.extract_content(text_content(s), 2),
                         {'text': s, 'trusted': True})

    def test_strict_newline_tab_allowed(self):
        s = 'line1\nline2\tend\r'
        self.assertEqual(drv.extract_content(text_content(s), 2), {'text': s, 'trusted': True})

    def test_dirty_prefix_fallback_trusted_false(self):
        """帧残留脏数据（非 0a 开头）：严格失败 → 递归仍有文本 → trusted=False。"""
        content = b'\x08\x05' + text_content('尾随残留')
        self.assertEqual(drv.extract_content(content, 2), {'text': '尾随残留', 'trusted': False})

    def test_dirty_trailing_field_fallback_trusted_false(self):
        """合法包装后带额外顶层字段：严格（全覆盖校验）失败 → 兜底有文本 trusted=False。"""
        content = text_content('正文') + b'\x18\x01'
        self.assertEqual(drv.extract_content(content, 2), {'text': '正文', 'trusted': False})

    def test_pure_binary_null(self):
        content = b'\x08\x96\x01\x15\x00\x00\x00\x00'  # wt0+wt5 字段，无可打印 wt2 载荷
        self.assertEqual(drv.extract_content(content, 2), {'text': None, 'trusted': False})

    def test_binary_nested_null(self):
        content = b'\x0a\x03\x08\x96\x01'  # 0a 字段内容仍是二进制 → 递归无文本
        self.assertEqual(drv.extract_content(content, 2), {'text': None, 'trusted': False})

    def test_empty_and_none(self):
        self.assertEqual(drv.extract_content(b'', 2), {'text': None, 'trusted': False})
        self.assertEqual(drv.extract_content(None, 0), {'text': None, 'trusted': False})

    def test_non_text_types_null(self):
        """非 0/2 类型（含确认系统类 1017/38/31）→ {null, false}，占位语义归 TS。"""
        for ct in (1017, 38, 31, 5, 15):
            self.assertEqual(drv.extract_content(text_content('x'), ct),
                             {'text': None, 'trusted': False}, ct)

    def test_declared_length_overflow_fails_strict(self):
        """声明长度越界（截断帧）：严格失败；递归也补不全 → null。"""
        content = text_content('abcdef')[:-3]
        self.assertEqual(drv.extract_content(content, 2), {'text': None, 'trusted': False})

    def test_non_utf8_inner_falls_back(self):
        """严格路径 UTF-8 解码失败 → 兜底；兜底同样无合法文本 → null。"""
        content = b'\x0a\x03\xff\xfe\xfd'
        self.assertEqual(drv.extract_content(content, 0), {'text': None, 'trusted': False})

    def test_bad_content_type_value(self):
        self.assertEqual(drv.extract_content(b'\x0a\x01a', None), {'text': None, 'trusted': False})
        self.assertEqual(drv.extract_content(b'\x0a\x01a', 'x'), {'text': None, 'trusted': False})


class WorkdirFixture:
    """合成解密库工作目录（明文库直接落盘，命名对齐驱动定位规则）。"""

    def __init__(self, tmp, name='work'):
        self.dir = os.path.join(tmp, name)
        os.makedirs(self.dir, exist_ok=True)

    def add_message_db(self, rows, filename='message.db.dec.db'):
        path = os.path.join(self.dir, filename)
        conn = sqlite3.connect(path)
        try:
            conn.execute(MESSAGE_DDL)
            insert_messages(conn, rows)
        finally:
            conn.close()
        return path

    def add_session_db(self, conv_rows, id_col='conversation_id', with_name=True):
        """session 库 conversation_table。conv_rows: [(id, name)]；with_name=False 构造
        schema-unknown 变体（无 name 列）；id_col 非常规名构造值形态发现路径。"""
        path = os.path.join(self.dir, 'session.db.dec.db')
        conn = sqlite3.connect(path)
        try:
            if with_name:
                conn.execute('CREATE TABLE conversation_table (%s TEXT, name TEXT, room_info_pb BLOB)' % id_col)
                conn.executemany('INSERT INTO conversation_table(%s, name) VALUES (?,?)' % id_col, conv_rows)
            else:
                conn.execute('CREATE TABLE conversation_table (room_info_pb BLOB)')
                conn.execute("INSERT INTO conversation_table(room_info_pb) VALUES (x'00')")
            conn.commit()
        finally:
            conn.close()

    def add_user_db(self, user_rows, with_alias=True):
        """user 库 user_table。user_rows: [(id, name, alias, remark)]。"""
        path = os.path.join(self.dir, 'user.db.dec.db')
        conn = sqlite3.connect(path)
        try:
            if with_alias:
                conn.execute('CREATE TABLE user_table (id TEXT, name TEXT, alias TEXT, remark TEXT)')
                conn.executemany('INSERT INTO user_table VALUES (?,?,?,?)', user_rows)
            else:
                conn.execute('CREATE TABLE user_table (id TEXT, name TEXT)')
                conn.executemany('INSERT INTO user_table VALUES (?,?)',
                                 [(r[0], r[1]) for r in user_rows])
            conn.commit()
        finally:
            conn.close()

    def query(self, args):
        return drv.cmd_query(['--workdir', self.dir] + args)


def db_row_by_id(fx, message_id):
    """message.db.dec.db 中指定 message_id 的行（_message_row 投影，供锚点同源断言）。"""
    conn = sqlite3.connect(os.path.join(fx.dir, 'message.db.dec.db'))
    try:
        r = conn.execute('SELECT %s FROM message_table WHERE message_id=?'
                         % ', '.join(drv.QUERY_MESSAGE_COLUMNS), (message_id,)).fetchone()
    finally:
        conn.close()
    assert r is not None, 'db_row_by_id：行不存在 message_id=%s' % message_id
    return drv._message_row(r)


def sample_message_rows():
    """CONV_S 五条（含同秒三条、一条系统类 1017、一条更晚）+ CONV_R1 一条。"""
    return [
        msg_row(CONV_S, PEER, 2, 1700000000, text_content('你好'), server_id='srv-1', seq=1),
        msg_row(CONV_S, SELF, 2, 1700000000, text_content('在的'), server_id='srv-2', seq=2),  # 同秒第 2 条
        msg_row(CONV_S, PEER, 2, 1700000000, text_content('第三条同秒'), server_id='srv-3', seq=3),
        msg_row(CONV_S, PEER, 1017, 1700000001, b'\x08\x01', server_id='srv-4'),  # 系统类
        msg_row(CONV_S, PEER, 2, 1700000100, text_content('later'), server_id='srv-5'),
        msg_row(CONV_R1, PEER, 2, 1700000005, text_content('群消息'), server_id='srv-6'),
    ]


class QueryMessagesTest(unittest.TestCase):
    """messages 任务：游标边界 / since / 同秒入库序 / 会话隔离 / 非文本行 / 命名兼容。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='wecom-query-msg-')
        self.fx = WorkdirFixture(self._tmp.name)
        self.fx.add_message_db(sample_message_rows())

    def tearDown(self):
        self._tmp.cleanup()

    def rows_of(self, payload):
        return [m['message_id'] for m in payload['messages']]

    def test_first_page_and_cursor(self):
        payload = self.fx.query(['--task', 'messages', '--conv', CONV_S,
                                 '--after-id', '0', '--limit', '3'])
        self.assertTrue(payload['ok'], payload)
        self.assertEqual(self.rows_of(payload), [1, 2, 3])
        self.assertEqual(payload['next_cursor_id'], 3)
        self.assertEqual(payload['count'], 3)
        self.assertEqual(payload['conversation_id'], CONV_S)
        self.assertEqual([m['text'] for m in payload['messages']],
                         ['你好', '在的', '第三条同秒'])
        self.assertTrue(all(m['trusted'] for m in payload['messages']))

    def test_continue_after_cursor(self):
        payload = self.fx.query(['--task', 'messages', '--conv', CONV_S,
                                 '--after-id', '3', '--limit', '3'])
        self.assertEqual(self.rows_of(payload), [4, 5])
        self.assertEqual(payload['next_cursor_id'], 5)

    def test_empty_batch_cursor_keeps_after_id(self):
        """空批次：next_cursor_id = after-id 原值（游标只认返回集，不臆进）。"""
        payload = self.fx.query(['--task', 'messages', '--conv', CONV_S,
                                 '--after-id', '999', '--limit', '10'])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['count'], 0)
        self.assertEqual(payload['messages'], [])
        self.assertEqual(payload['next_cursor_id'], 999)

    def test_default_limit_and_after_id(self):
        payload = self.fx.query(['--task', 'messages', '--conv', CONV_S])
        self.assertEqual(self.rows_of(payload), [1, 2, 3, 4, 5])  # 缺省 limit=100 覆盖全部

    def test_since_filter(self):
        payload = self.fx.query(['--task', 'messages', '--conv', CONV_S,
                                 '--since', '1700000050'])
        self.assertEqual(self.rows_of(payload), [5])
        self.assertEqual(payload['next_cursor_id'], 5)

    def test_since_combined_with_cursor(self):
        payload = self.fx.query(['--task', 'messages', '--conv', CONV_S,
                                 '--after-id', '4', '--since', '0'])
        self.assertEqual(self.rows_of(payload), [5])

    def test_same_second_keeps_insertion_order(self):
        """同秒三条按 message_id（入库序）返回，不因同秒乱序；sequence 递增可见。"""
        payload = self.fx.query(['--task', 'messages', '--conv', CONV_S, '--after-id', '0', '--limit', '3'])
        ids = self.rows_of(payload)
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len({m['send_time'] for m in payload['messages']}), 1)
        self.assertEqual([m['sequence'] for m in payload['messages']], [1, 2, 3])

    def test_conversation_isolation(self):
        payload = self.fx.query(['--task', 'messages', '--conv', CONV_R1])
        self.assertEqual(self.rows_of(payload), [6])
        payload = self.fx.query(['--task', 'messages', '--conv', 'R:not-exist'])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['count'], 0)
        self.assertEqual(payload['next_cursor_id'], 0)  # 缺省 after-id=0

    def test_non_text_row_text_null_and_exclude_table_not_applied(self):
        """系统类行 text=null；排除表【不】影响 messages 任务返回（系统消息照常返回）。"""
        payload = self.fx.query(['--task', 'messages', '--conv', CONV_S, '--after-id', '3',
                                 '--limit', '1'])
        row = payload['messages'][0]
        self.assertEqual(row['content_type'], 1017)
        self.assertIsNone(row['text'])
        self.assertFalse(row['trusted'])
        self.assertEqual(row['server_id'], 'srv-4')
        self.assertEqual(row['flag'], 0)

    def test_row_projection_shape(self):
        payload = self.fx.query(['--task', 'messages', '--conv', CONV_S, '--limit', '1'])
        self.assertEqual(list(payload['messages'][0].keys()),
                         ['message_id', 'server_id', 'sequence', 'sender_id', 'conversation_id',
                          'content_type', 'send_time', 'flag', 'text', 'trusted',
                          'sender_name', 'sender_name_source', 'sender_platform'])

    def test_alt_decrypt_filename(self):
        """decrypt 自然产物命名（message.dec.db）同样可定位。"""
        src = os.path.join(self.fx.dir, 'message.db.dec.db')
        os.rename(src, os.path.join(self.fx.dir, 'message.dec.db'))
        payload = self.fx.query(['--task', 'messages', '--conv', CONV_S, '--limit', '1'])
        self.assertTrue(payload['ok'], payload)


class QueryConversationsTest(unittest.TestCase):
    """conversations 任务：聚合、按 last_message_id 降序、last_send_time 取水位行。"""

    def test_aggregation_and_ordering(self):
        with tempfile.TemporaryDirectory(prefix='wecom-query-conv-') as tmp:
            fx = WorkdirFixture(tmp)
            rows = [
                # 迟到回退形态：CONV_B 第二条入库（更大 message_id）send_time 更小
                msg_row(CONV_S, PEER, 2, 1700000200, text_content('b1'), server_id='b1'),
                msg_row(CONV_R1, PEER, 2, 1700000100, text_content('c1'), server_id='c1'),
                msg_row(CONV_S, SELF, 2, 1700000100, text_content('b2'), server_id='b2'),
            ]
            fx.add_message_db(rows)
            payload = fx.query(['--task', 'conversations'])
            self.assertTrue(payload['ok'])
            convs = payload['conversations']
            self.assertEqual([c['conversation_id'] for c in convs], [CONV_S, CONV_R1])
            self.assertEqual(convs[0]['last_message_id'], 3)
            self.assertEqual(convs[0]['count'], 2)
            # last_send_time 必须是水位行（message_id=3）的 send_time，而非 MAX(send_time)=1700000200
            self.assertEqual(convs[0]['last_send_time'], 1700000100)
            self.assertEqual(convs[1]['count'], 1)
            self.assertEqual(convs[1]['last_send_time'], 1700000100)
            # last_anchor = 水位行的消息稳定字段摘要（第九轮评审 P1：watch 建基线一并存储，
            # 下轮锚点有效性校验的比对基准；64-hex、与 _message_anchor 同源）
            self.assertEqual(convs[0]['last_anchor'],
                             drv._message_anchor(db_row_by_id(fx, 3)))
            self.assertRegex(convs[0]['last_anchor'], r'^[0-9a-f]{64}$')
            self.assertEqual(convs[1]['last_anchor'],
                             drv._message_anchor(db_row_by_id(fx, 2)))

    def test_empty_table(self):
        with tempfile.TemporaryDirectory(prefix='wecom-query-conv-') as tmp:
            fx = WorkdirFixture(tmp)
            fx.add_message_db([])
            payload = fx.query(['--task', 'conversations'])
            self.assertTrue(payload['ok'])
            self.assertEqual(payload['conversations'], [])


class LastPerConvTest(unittest.TestCase):
    """last-per-conv 任务：排除表双向语义（系统在最后回退 / 全系统 null / 参与型即最后），
    --exclude-types 追加语义，会话按 last_message_id 降序。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='wecom-query-lpc-')
        self.fx = WorkdirFixture(self._tmp.name)
        rows = [
            # CONV_S：…参与型…,系统 1017 收尾（回退场景，待回复=最后参与型消息方向）
            msg_row(CONV_S, PEER, 2, 1700000000, text_content('q'), server_id='s1'),
            msg_row(CONV_S, SELF, 2, 1700000001, text_content('a'), server_id='s2'),
            msg_row(CONV_S, PEER, 1017, 1700000002, b'\x08\x01', server_id='s3'),
            # CONV_R1：无系统消息，last==participating
            msg_row(CONV_R1, PEER, 2, 1700000005, text_content('group'), server_id='g1'),
            # CONV_R2：全是系统消息 → last_participating=null
            msg_row(CONV_R2, PEER, 38, 1700000006, b'\x08\x02', server_id='y1'),
            msg_row(CONV_R2, PEER, 31, 1700000007, b'\x08\x03', server_id='y2'),
        ]
        self.fx.add_message_db(rows)

    def tearDown(self):
        self._tmp.cleanup()

    def conv(self, payload, cid):
        return next(c for c in payload['conversations'] if c['conversation_id'] == cid)

    def test_system_last_falls_back_to_participating(self):
        """系统消息收尾：last_participating 回退到前一条可参与消息（不清除待回复的语义）。"""
        payload = self.fx.query(['--task', 'last-per-conv'])
        c = self.conv(payload, CONV_S)
        self.assertEqual(c['last_message']['content_type'], 1017)
        self.assertEqual(c['last_participating']['message_id'], 2)
        self.assertEqual(c['last_participating']['sender_id'], SELF)  # 自己最后发言 → 不制造新待回复

    def test_participating_last_equals_last_message(self):
        payload = self.fx.query(['--task', 'last-per-conv'])
        c = self.conv(payload, CONV_R1)
        self.assertEqual(c['last_participating']['message_id'],
                         c['last_message']['message_id'])

    def test_all_system_messages_null_participating(self):
        """全是系统消息 → last_participating=null（不制造也不清除待回复）。"""
        payload = self.fx.query(['--task', 'last-per-conv'])
        c = self.conv(payload, CONV_R2)
        self.assertIsNotNone(c['last_message'])
        self.assertIsNone(c['last_participating'])

    def test_ordering_by_last_message_id_desc(self):
        payload = self.fx.query(['--task', 'last-per-conv'])
        self.assertEqual([c['conversation_id'] for c in payload['conversations']],
                         [CONV_R2, CONV_R1, CONV_S])

    def test_exclude_types_append_semantics(self):
        """--exclude-types 追加（不缩减基础表）：999 追加生效，1017 仍被基础表排除。"""
        # 在原库追加一条 ct=999 收尾 CONV_S
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'message.db.dec.db'))
        try:
            insert_messages(conn, [msg_row(CONV_S, PEER, 999, 1700000010, b'\x08\x09', server_id='s9')])
        finally:
            conn.close()
        # 默认：999 不在排除表 → last_participating = ct999 行
        payload = self.fx.query(['--task', 'last-per-conv'])
        c = self.conv(payload, CONV_S)
        self.assertEqual(c['last_participating']['content_type'], 999)
        # 追加 999：回退越过 999，且 1017 仍被基础表排除 → 落到 message_id=2
        payload = self.fx.query(['--task', 'last-per-conv', '--exclude-types', '999'])
        c = self.conv(payload, CONV_S)
        self.assertEqual(payload['exclude_types'], [31, 38, 999, 1001, 1002, 1006, 1017, 1022])
        self.assertEqual(c['last_participating']['message_id'], 2)

    def test_participating_row_projection(self):
        payload = self.fx.query(['--task', 'last-per-conv'])
        c = self.conv(payload, CONV_S)
        self.assertEqual(list(c['last_participating'].keys()),
                         ['message_id', 'server_id', 'sequence', 'sender_id', 'conversation_id',
                          'content_type', 'send_time', 'flag', 'text', 'trusted'])


class LastPerConvNamesTest(unittest.TestCase):
    """last-per-conv --with-names 反向取名（第九轮评审 P2）：R 群名只认 conversation_table、
    S 联系人名只认 user_table（与 resolve 同一命名纪律）；无命中/来源缺失 → name=null；
    未传 --with-names 不附 name 键（契约面不变）。第十轮评审 P2：S: 行附 match_names =
    该行所有非空名称字段（名称/别名/备注候选列，与 resolve 同列集）；存在 R: 会话且
    session 来源不可用 → 顶层 naming_incomplete:true。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='wecom-query-lpcname-')
        self.fx = WorkdirFixture(self._tmp.name)
        self.fx.add_message_db([
            msg_row(CONV_S, PEER, 2, 1700000001, text_content('hi'), server_id='s1'),
            msg_row(CONV_R1, PEER, 2, 1700000002, text_content('group'), server_id='g1'),
            msg_row('R:anon', PEER, 2, 1700000003, text_content('no-name'), server_id='a1'),
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def names(self, extra=()):
        payload = self.fx.query(['--task', 'last-per-conv', '--with-names', '--account', SELF] + list(extra))
        self.assertTrue(payload['ok'], payload)
        return {c['conversation_id']: c.get('name') for c in payload['conversations']}

    def rows(self, extra=()):
        payload = self.fx.query(['--task', 'last-per-conv', '--with-names', '--account', SELF] + list(extra))
        self.assertTrue(payload['ok'], payload)
        return {c['conversation_id']: c for c in payload['conversations']}

    def test_reverse_names_group_contact_and_missing(self):
        self.fx.add_session_db([(CONV_R1, '项目群'), (CONV_S, 'S 标题不是命名来源')])
        self.fx.add_user_db([(PEER, '陆伟', '', ''), (SELF, '我自己', '', '')])
        names = self.names()
        self.assertEqual(names[CONV_R1], '项目群', 'R: 群名认 conversation_table')
        self.assertEqual(names[CONV_S], '陆伟', 'S: 联系人名认 user_table（会话 id 解析对方 id）')
        self.assertIsNone(names['R:anon'], '无命名来源（不在 conversation_table）→ null')

    def test_s_row_emits_match_names_alias_remark(self):
        """第十轮评审 P2①：S: 行 match_names = 该行所有非空名称字段（name/alias/remark），
        规范 name 不变；R: 行保持 name 单值、无 match_names 键。"""
        self.fx.add_session_db([(CONV_R1, '项目群')])
        self.fx.add_user_db([(PEER, '陆伟', 'luwei-alias', '陆总')])
        row = self.rows()[CONV_S]
        self.assertEqual(row['name'], '陆伟', '规范 name 列不变')
        self.assertEqual(row['match_names'], ['陆伟', 'luwei-alias', '陆总'],
                         '名称/别名/备注候选列全部非空值（与 resolve 同一列集）')
        group_row = self.rows()[CONV_R1]
        self.assertEqual(group_row['name'], '项目群')
        self.assertNotIn('match_names', group_row, 'R: 群保持 name 单值（无 match_names 键）')

    def test_s_match_names_without_canonical_name(self):
        """规范 name 列为空串但备注有值：name=None、match_names 仍带备注（TS 过滤按
        match_names 命中，输出 name 缺省）。"""
        self.fx.add_user_db([(PEER, '', '', '陆总')])
        row = self.rows()[CONV_S]
        self.assertIsNone(row['name'])
        self.assertEqual(row['match_names'], ['陆总'])

    def test_naming_incomplete_when_session_db_missing(self):
        """第十轮评审 P2②：存在 R: 会话且 session 解密库缺失 → 顶层 naming_incomplete:true
        （R: 行 name=null；S: 命名不受影响）——TS 据此区分「取不到群名」与「确实无匹配」。"""
        self.fx.add_user_db([(PEER, '陆伟', '', '')])
        payload = self.fx.query(['--task', 'last-per-conv', '--with-names', '--account', SELF])
        self.assertTrue(payload['ok'])
        self.assertTrue(payload.get('naming_incomplete'))
        names = self.names()
        self.assertIsNone(names[CONV_R1])
        self.assertIsNone(names['R:anon'])
        self.assertEqual(names[CONV_S], '陆伟', 'S: 命名认 user 库，不随 session 缺失效')

    def test_naming_incomplete_when_session_schema_unknown(self):
        """session 库在但 name/id 列无法确认（schema-unknown）→ 同样标 naming_incomplete。"""
        self.fx.add_session_db([], with_name=False)  # conversation_table 无 name 列
        self.fx.add_user_db([(PEER, '陆伟', '', '')])
        payload = self.fx.query(['--task', 'last-per-conv', '--with-names', '--account', SELF])
        self.assertTrue(payload['ok'])
        self.assertTrue(payload.get('naming_incomplete'))

    def test_no_flag_when_source_available_but_row_missing(self):
        """session 来源可用但个别 R: 行不在表内（无命中）→ 不标 naming_incomplete（name=null
        即可；标注只表达「来源不可用」而非「无命中」）。"""
        self.fx.add_session_db([(CONV_R1, '项目群')])  # R:anon 不在表内
        self.fx.add_user_db([(PEER, '陆伟', '', '')])
        payload = self.fx.query(['--task', 'last-per-conv', '--with-names', '--account', SELF])
        self.assertTrue(payload['ok'])
        self.assertNotIn('naming_incomplete', payload)
        names = self.names()
        self.assertIsNone(names['R:anon'], '无命中 → null（区别于来源不可用）')
        self.assertEqual(names[CONV_R1], '项目群')

    def test_no_flag_when_no_r_conversations(self):
        """session 缺失但全是 S: 会话 → 不标（用户库正常命名；无 R: 会话恒 False）。"""
        tmp = tempfile.TemporaryDirectory(prefix='wecom-query-lpcname-s-')
        self.addCleanup(tmp.cleanup)
        fx = WorkdirFixture(tmp.name)
        fx.add_message_db([msg_row(CONV_S, PEER, 2, 1700000001, text_content('hi'), server_id='s1')])
        fx.add_user_db([(PEER, '陆伟', '', '')])
        payload = fx.query(['--task', 'last-per-conv', '--with-names', '--account', SELF])
        self.assertTrue(payload['ok'])
        self.assertNotIn('naming_incomplete', payload)
        names = {c['conversation_id']: c.get('name') for c in payload['conversations']}
        self.assertEqual(names[CONV_S], '陆伟')

    def test_s_title_in_conversation_table_not_used_for_contact(self):
        """S: 会话名不认 conversation_table（session 的 S 标题可能是缓存旧名）：user 无命中 → null。"""
        self.fx.add_session_db([(CONV_R1, '项目群'), (CONV_S, '缓存旧名')])
        self.fx.add_user_db([(SELF, '我自己', '', '')])  # PEER 不在 user_table
        names = self.names()
        self.assertIsNone(names[CONV_S], 'S: 只认 user_table，conversation_table 的 S 标题不作来源')
        self.assertEqual(names[CONV_R1], '项目群')

    def test_missing_source_dbs_name_null(self):
        """session/user 解密库缺失：对应会话名 null，不报错。"""
        self.fx.add_user_db([(PEER, '陆伟', '', '')])
        names = self.names()
        self.assertIsNone(names[CONV_R1], 'session 库缺失 → R: 群名 null')
        self.assertEqual(names[CONV_S], '陆伟')
        self.assertIsNone(names['R:anon'])

    def test_without_flag_no_name_key(self):
        """未传 --with-names：行不附 name 键（既有契约面不变）。"""
        payload = self.fx.query(['--task', 'last-per-conv'])
        self.assertTrue(payload['ok'])
        for c in payload['conversations']:
            self.assertNotIn('name', c)

    def test_with_names_requires_account(self):
        """--with-names 缺 --account → BAD_ARGS（S: 会话解析对方 id 需要本人账号）。"""
        payload = self.fx.query(['--task', 'last-per-conv', '--with-names'])
        self.assertEqual(payload.get('error'), 'BAD_ARGS')


class ResolveTest(unittest.TestCase):
    """resolve 任务：多候选 / 零候选 / name-alias-备注命中 / 来源库缺失 unavailable /
    schema-unknown / id 列值形态发现。"""

    USER_ROWS = [(PEER, '陆伟', 'luwei-alias', '陆总'),
                 (SELF, '我自己', '', ''),
                 ('1688850000000099', '陆伟', '', '')]  # 同名第二人 → 多候选

    def setUp(self):
        self._tmps = []

    def make_fx(self, with_session=True, with_user=True, session_variant=None):
        tmp = tempfile.TemporaryDirectory(prefix='wecom-query-rsl-')
        self._tmps.append(tmp)
        fx = WorkdirFixture(tmp.name)
        fx.add_message_db([])
        if with_session:
            if session_variant == 'alt-id-col':
                fx.add_session_db([(CONV_R1, '项目群')], id_col='conv_key')
            elif session_variant == 'no-name-col':
                fx.add_session_db([], with_name=False)
            else:
                fx.add_session_db([(CONV_R1, '项目群'), (CONV_R2, '项目群'),
                                   ('R:other', '家人群')])
        if with_user:
            fx.add_user_db(self.USER_ROWS)
        return fx

    def tearDown(self):
        for tmp in self._tmps:
            tmp.cleanup()

    def test_group_multi_candidates_ambiguous(self):
        """群名无唯一索引：同名多候选全返回 → ambiguous=true。"""
        payload = self.make_fx().query(['--task', 'resolve', '--name', '项目群'])
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['ambiguous'])
        self.assertEqual(payload['sources']['conversation_table'], 'ok')
        cands = [c for c in payload['candidates'] if c['source'] == 'conversation_table']
        self.assertEqual(sorted(c['conversation_id'] for c in cands), sorted([CONV_R1, CONV_R2]))
        self.assertTrue(all(c['matched_column'] == 'name' for c in cands))

    def test_user_name_and_alias_and_remark(self):
        fx = self.make_fx()
        # name 精确命中（两个同名 → 多候选）
        payload = fx.query(['--task', 'resolve', '--name', '陆伟'])
        user_cands = [c for c in payload['candidates'] if c['source'] == 'user_table']
        self.assertEqual(len(user_cands), 2)
        self.assertTrue(payload['ambiguous'])
        self.assertEqual(sorted(c['user_id'] for c in user_cands),
                         sorted([PEER, '1688850000000099']))
        # 单人命中 → ambiguous=false；user 候选 conversation_id=null（S:<self>_<uid> 归 TS 组合）
        payload = fx.query(['--task', 'resolve', '--name', '我自己'])
        self.assertFalse(payload['ambiguous'])
        cand = payload['candidates'][0]
        self.assertEqual(cand['source'], 'user_table')
        self.assertEqual(cand['user_id'], SELF)
        self.assertIsNone(cand['conversation_id'])
        self.assertEqual(cand['matched_column'], 'name')
        # 别名 / 备注列命中（运行时 PRAGMA 交集，不猜列名）
        payload = fx.query(['--task', 'resolve', '--name', 'luwei-alias'])
        self.assertEqual([c['matched_column'] for c in payload['candidates']], ['alias'])
        payload = fx.query(['--task', 'resolve', '--name', '陆总'])
        self.assertEqual([c['matched_column'] for c in payload['candidates']], ['remark'])

    def test_exact_match_only(self):
        """精确匹配：子串不命中。"""
        payload = self.make_fx().query(['--task', 'resolve', '--name', '陆'])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['candidates'], [])
        self.assertFalse(payload['ambiguous'])

    def test_zero_candidates_sources_ok(self):
        payload = self.make_fx().query(['--task', 'resolve', '--name', '不存在'])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['candidates'], [])
        self.assertFalse(payload['ambiguous'])
        self.assertEqual(payload['sources'], {'conversation_table': 'ok', 'user_table': 'ok'})

    def test_missing_session_db_source_unavailable(self):
        fx = self.make_fx(with_session=False)
        payload = fx.query(['--task', 'resolve', '--name', '我自己'])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['sources']['conversation_table'], 'unavailable')
        self.assertEqual(len(payload['candidates']), 1)  # user 结果不受影响

    def test_missing_user_db_source_unavailable(self):
        fx = self.make_fx(with_user=False)
        payload = fx.query(['--task', 'resolve', '--name', '项目群'])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['sources']['user_table'], 'unavailable')
        self.assertEqual(len(payload['candidates']), 2)

    def test_both_missing_unavailable_empty(self):
        payload = self.make_fx(with_session=False, with_user=False).query(
            ['--task', 'resolve', '--name', '任意'])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['candidates'], [])
        self.assertEqual(payload['sources'], {'conversation_table': 'unavailable',
                                              'user_table': 'unavailable'})

    def test_alt_id_column_found_by_value_shape(self):
        """id 列名非常规（未经 P0 文档化）：字面名 miss → 按值形态（R:/S:/Y: 前缀）发现。"""
        payload = self.make_fx(session_variant='alt-id-col').query(
            ['--task', 'resolve', '--name', '项目群'])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['sources']['conversation_table'], 'ok')
        self.assertEqual([c['conversation_id'] for c in payload['candidates']], [CONV_R1])

    def test_no_name_column_schema_unknown(self):
        """conversation_table 无 name 列 → 来源 schema-unknown 降级，不猜列、不报错。"""
        payload = self.make_fx(session_variant='no-name-col').query(
            ['--task', 'resolve', '--name', '我自己'])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['sources']['conversation_table'], 'schema-unknown')
        self.assertEqual(len([c for c in payload['candidates']
                              if c['source'] == 'user_table']), 1)

    def test_user_table_without_alias_columns(self):
        """user_table 只有 id+name（真实 P0 实证形态）：只按 name 匹配，不猜其余列。"""
        fx = self.make_fx()
        src = os.path.join(fx.dir, 'user.db.dec.db')
        os.remove(src)
        fx.add_user_db([(PEER, '陆伟', '', '')], with_alias=False)
        payload = fx.query(['--task', 'resolve', '--name', '陆伟'])
        self.assertEqual([c['matched_column'] for c in payload['candidates']], ['name'])
        payload = fx.query(['--task', 'resolve', '--name', 'luwei-alias'])
        self.assertEqual(payload['candidates'], [])


class ExternalResolveTest(unittest.TestCase):
    """明确外部目标时才用关系及公司证据排除内部同名，未知证据必须阻断。"""

    INTERNAL = '1688850000000099'
    OTHER_EXTERNAL = '9007199254740993'

    def setUp(self):
        self._tmps = []

    def tearDown(self):
        for tmp in self._tmps:
            tmp.cleanup()

    def make_fx(self, *, relation_table=True, relation_column='user_id',
                self_corp=11, peer_corp=22, internal_corp=11, relations=None,
                second_external=False, duplicate_self=False, integer_ids=False):
        tmp = tempfile.TemporaryDirectory(prefix='wecom-query-external-')
        self._tmps.append(tmp)
        fx = WorkdirFixture(tmp.name)
        fx.add_message_db([msg_row(CONV_S, PEER, 2, 1700000000, text_content('external'))])
        fx.add_session_db([(CONV_R1, '陆伟')])  # 同名群绝不可混入明确外部单聊。
        conn = sqlite3.connect(os.path.join(fx.dir, 'user.db.dec.db'))
        try:
            conn.execute('CREATE TABLE user_table (id %s, name TEXT, alias TEXT, corp_id INTEGER)'
                         % ('INTEGER' if integer_ids else 'TEXT'))
            rows = [(SELF, '我自己', '', self_corp), (PEER, '陆伟', 'peer-alias', peer_corp),
                    (self.INTERNAL, '陆伟', '', internal_corp)]
            if second_external:
                rows.append((self.OTHER_EXTERNAL, '陆伟', '', 33))
            if duplicate_self:
                rows.append((SELF, '重复本人', '', self_corp))
            conn.executemany('INSERT INTO user_table VALUES (?,?,?,?)', rows)
            if relation_table:
                conn.execute('CREATE TABLE external_user_relation_v3 (%s INTEGER)' % relation_column)
                values = [PEER] if relations is None else relations
                if second_external:
                    values = list(values) + [self.OTHER_EXTERNAL]
                conn.executemany('INSERT INTO external_user_relation_v3 VALUES (?)', [(v,) for v in values])
            conn.commit()
        finally:
            conn.close()
        return fx

    def resolve(self, fx, *, name='陆伟', external=True):
        args = ['--task', 'resolve', '--name', name, '--account', SELF]
        return fx.query(args + (['--external-only'] if external else []))

    def assert_scope_unknown(self, fx):
        payload = self.resolve(fx)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['sources']['user_table'], 'schema-unknown')
        self.assertEqual(payload['candidates'], [], '未知证据不能消成唯一外部联系人')

    def test_explicit_scope_excludes_internal_without_discarding_message_identity(self):
        payload = self.resolve(self.make_fx())
        self.assertTrue(payload['ok'])
        self.assertFalse(payload['ambiguous'])
        self.assertEqual(payload['sources'], {'user_table': 'ok'})
        self.assertEqual([(c['user_id'], c['conversation_id'], c['contact_scope'])
                          for c in payload['candidates']], [(PEER, CONV_S, 'external')])

    def test_default_resolution_keeps_internal_and_group_ambiguity(self):
        payload = self.resolve(self.make_fx(), external=False)
        self.assertTrue(payload['ambiguous'])
        self.assertEqual(len(payload['candidates']), 3)
        self.assertTrue(any(c['conversation_id'] is None for c in payload['candidates']))
        self.assertTrue(all('contact_scope' not in c for c in payload['candidates']))

    def test_external_scope_alias_uses_same_proof(self):
        payload = self.resolve(self.make_fx(), name='peer-alias')
        self.assertEqual([c['user_id'] for c in payload['candidates']], [PEER])
        self.assertEqual(payload['candidates'][0]['matched_column'], 'alias')

    def test_two_external_people_remain_ambiguous_even_without_second_history(self):
        payload = self.resolve(self.make_fx(second_external=True, integer_ids=True))
        self.assertTrue(payload['ambiguous'])
        by_user = {c['user_id']: c for c in payload['candidates']}
        self.assertEqual(set(by_user), {PEER, self.OTHER_EXTERNAL})
        self.assertIsNone(by_user[self.OTHER_EXTERNAL]['conversation_id'])
        self.assertEqual(by_user[PEER]['conversation_id'], CONV_S)

    def test_duplicate_relation_rows_do_not_multiply_candidates(self):
        payload = self.resolve(self.make_fx(relations=[PEER, PEER], integer_ids=True))
        self.assertFalse(payload['ambiguous'])
        self.assertEqual(len(payload['candidates']), 1)
        self.assertEqual(payload['candidates'][0]['user_id'], PEER)

    def test_missing_relation_schema_is_unknown_not_empty_match(self):
        self.assert_scope_unknown(self.make_fx(relation_table=False))
        self.assert_scope_unknown(self.make_fx(relation_column='unverified_id'))

    def test_missing_corp_column_does_not_reclassify_by_name_or_relation(self):
        fx = self.make_fx()
        conn = sqlite3.connect(os.path.join(fx.dir, 'user.db.dec.db'))
        try:
            conn.execute('ALTER TABLE user_table DROP COLUMN corp_id')
            conn.commit()
        finally:
            conn.close()
        self.assert_scope_unknown(fx)

    def test_self_corp_must_be_present_and_uniquely_bound_to_account(self):
        for value in (None, 0, '', 'unverified', 1.5):
            with self.subTest(self_corp=value):
                self.assert_scope_unknown(self.make_fx(self_corp=value))
        self.assert_scope_unknown(self.make_fx(duplicate_self=True))
        fx = self.make_fx()
        conn = sqlite3.connect(os.path.join(fx.dir, 'user.db.dec.db'))
        try:
            conn.execute('DELETE FROM user_table WHERE id=?', (SELF,))
            conn.commit()
        finally:
            conn.close()
        self.assert_scope_unknown(fx)

    def test_unknown_or_conflicting_candidate_evidence_cannot_produce_unique(self):
        cases = [dict(peer_corp=None), dict(internal_corp=None), dict(peer_corp=11),
                 dict(relations=[]), dict(relations=[PEER, self.INTERNAL]),
                 dict(internal_corp=44)]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.assert_scope_unknown(self.make_fx(**kwargs))

    def test_zero_name_matches_is_allowed_only_after_scope_schema_validation(self):
        payload = self.resolve(self.make_fx(), name='不存在')
        self.assertEqual(payload['sources']['user_table'], 'ok')
        self.assertEqual(payload['candidates'], [])
        bad = self.resolve(self.make_fx(relation_table=False), name='不存在')
        self.assertEqual(bad['sources']['user_table'], 'schema-unknown')

    def test_external_flag_requires_resolve_and_account_before_opening_any_db(self):
        for args in (['--task', 'resolve', '--name', '陆伟'],
                     ['--task', 'messages', '--conv', CONV_S, '--account', SELF],
                     ['--task', 'conversations', '--account', SELF],
                     ['--task', 'last-per-conv', '--account', SELF],
                     ['--task', 'context', '--account', SELF],
                     ['--task', 'groups', '--account', SELF]):
            with self.subTest(args=args):
                result = drv.cmd_query(['--workdir', 'nonexistent-test-workdir'] + args + ['--external-only'])
                self.assertEqual(result.get('error'), 'BAD_ARGS')

    def test_external_scope_reaches_native_process_protocol(self):
        fx = self.make_fx(integer_ids=True)
        payload, _ = run_driver(['query', '--workdir', fx.dir, '--task', 'resolve',
                                 '--name', '陆伟', '--account', SELF, '--external-only'])
        self.assertTrue(payload['ok'])
        self.assertEqual([c['user_id'] for c in payload['candidates']], [PEER])


class QueryArgValidationTest(unittest.TestCase):
    """参数校验：BAD_ARGS 全形态（进程内，协议语义与子进程一致）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='wecom-query-arg-')
        self.dir = os.path.join(self._tmp.name, 'w')
        os.makedirs(self.dir)

    def tearDown(self):
        self._tmp.cleanup()

    def q(self, args):
        return drv.cmd_query(['--workdir', self.dir] + args)

    def test_bad_args_variants(self):
        cases = [
            [],                                   # 无 --task
            ['--task', 'messages'],               # 无 --workdir（真 BAD_ARGS，工作目录由外层补）
            ['--task', 'unknown'],
            ['--task', 'messages', '--conv', ''],  # 空 conv
            ['--task', 'messages', '--conv', 'x', '--limit', '0'],
            ['--task', 'messages', '--conv', 'x', '--after-id', '-1'],
            ['--task', 'messages', '--conv', 'x', '--since', '-1'],
            ['--task', 'messages', '--conv', 'x', '--exclude-types', 'a,b'],
            ['--task', 'messages', '--conv', 'x', '--exclude-types', ''],
            ['--task', 'resolve'],                # 缺 --name
            ['--task', 'resolve', '--name', ''],
            ['--task', 'messages', '--conv', 'x', '--bogus'],
            ['--task', 'conversations', 'stray'],
        ]
        for args in cases:
            payload = drv.cmd_query(args)
            self.assertEqual(payload.get('error'), 'BAD_ARGS', args)

    def test_missing_db_unreadable_no_file_created(self):
        """库缺失 → DB_UNREADABLE；只读语义：不创建任何库文件。"""
        payload = self.q(['--task', 'messages', '--conv', 'x'])
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'DB_UNREADABLE')
        self.assertEqual(os.listdir(self.dir), [])  # 只读打开不落文件

    def test_corrupt_db_sql_error(self):
        """坏库（非 SQLite 文件）→ SQL_ERROR 不抛。"""
        with open(os.path.join(self.dir, 'message.db.dec.db'), 'wb') as f:
            f.write(b'this is not a sqlite database' * 10)
        for task in (['--task', 'conversations'],
                     ['--task', 'messages', '--conv', 'x'],
                     ['--task', 'last-per-conv']):
            payload = self.q(task)
            self.assertFalse(payload['ok'], task)
            self.assertEqual(payload['error'], 'SQL_ERROR', task)

    def test_confirmed_system_types_frozen(self):
        self.assertEqual(drv.CONFIRMED_SYSTEM_TYPES, (1017, 38, 31, 1001, 1002, 1006, 1022))


def run_driver(args, stdin_text=None):
    """起子进程跑驱动，解析最后一行 LOCALDB_JSON:（协议契约本身也是被测对象）。"""
    p = subprocess.run([sys.executable, DRIVER] + args, input=(stdin_text or '').encode('utf-8'),
                       capture_output=True, timeout=120)
    out = p.stdout.decode('utf-8', 'replace')
    lines = [l for l in out.splitlines() if l.startswith('LOCALDB_JSON: ')]
    assert lines, '驱动未输出 LOCALDB_JSON 行；stdout 尾部：%r stderr：%r' % (out[-300:], p.stderr.decode('utf-8', 'replace')[-300:])
    assert p.returncode == 0, '驱动退出码须为 0（协议约定），实际 %d' % p.returncode
    return json.loads(lines[-1][len('LOCALDB_JSON: '):]), out


class QueryProtocolTest(unittest.TestCase):
    """子进程端到端：argv 分发 / LOCALDB_JSON 协议行 / 退出码 0 / 业务文本不走兜底脱敏。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='wecom-query-proto-')
        self.fx = WorkdirFixture(self._tmp.name)
        self.fx.add_message_db(sample_message_rows())

    def tearDown(self):
        self._tmp.cleanup()

    def test_end_to_end_messages_and_text_not_redacted(self):
        """端到端：输出协议行可解析；含 32-hex 样式子串的正文不得被兜底脱敏破坏
        （query 无密钥参与，redact=False 的契约）。"""
        hex_like = 'deadbeefdeadbeefdeadbeefdeadbeef'
        conn = sqlite3.connect(os.path.join(self.fx.dir, 'message.db.dec.db'))
        try:
            insert_messages(conn, [msg_row(CONV_S, PEER, 2, 1700000200,
                                           text_content('hash ' + hex_like), server_id='srv-h')])
        finally:
            conn.close()
        payload, out = run_driver(['query', '--workdir', self.fx.dir, '--task', 'messages',
                                   '--conv', CONV_S, '--after-id', '6'])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['messages'][0]['text'], 'hash ' + hex_like)
        self.assertIn(hex_like, out)

    def test_protocol_bad_args_exit_zero(self):
        payload, _ = run_driver(['query', '--task', 'messages'])
        self.assertEqual(payload['error'], 'BAD_ARGS')
        self.assertIn('用法', payload['message'])

    def test_protocol_missing_db(self):
        empty = os.path.join(self._tmp.name, 'empty')
        os.makedirs(empty)
        payload, _ = run_driver(['query', '--workdir', empty, '--task', 'conversations'])
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'DB_UNREADABLE')

    def test_protocol_no_query_mode_still_bad_args(self):
        payload, _ = run_driver([])
        self.assertEqual(payload['error'], 'BAD_ARGS')


class SchemaUnsupportedTest(unittest.TestCase):
    """评审 v6：真实协议下缺表/缺列的稳定分类。真实 SQLite 库（生产者协议）缺
    message_table / 缺列 → SCHEMA_UNSUPPORTED（doctor 据此归 compat）；其他 SQL 错误
    保持 SQL_ERROR（不误升兼容性分类）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='wecom-query-schema-')
        self.dir = os.path.join(self._tmp.name, 'w')
        os.makedirs(self.dir)

    def tearDown(self):
        self._tmp.cleanup()

    def _db_with(self, ddl):
        path = os.path.join(self.dir, 'message.db.dec.db')
        conn = sqlite3.connect(path)
        try:
            conn.execute(ddl)
            conn.commit()
        finally:
            conn.close()

    def test_missing_message_table_schema_unsupported(self):
        """缺 message_table 的真实库：conversations → SCHEMA_UNSUPPORTED，detail 含表名。"""
        self._db_with('CREATE TABLE unrelated_table (x INTEGER)')
        payload = drv.cmd_query(['--workdir', self.dir, '--task', 'conversations'])
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'SCHEMA_UNSUPPORTED')
        self.assertIn('message_table', payload['detail'])

    def test_missing_column_schema_unsupported(self):
        """message_table 在但缺 send_time 列（投影 9 列中缺一）：→ SCHEMA_UNSUPPORTED，detail 含列名。"""
        self._db_with('CREATE TABLE message_table (message_id INTEGER PRIMARY KEY, server_id TEXT,'
                      'sequence INTEGER, sender_id TEXT, conversation_id TEXT,'
                      'content_type INTEGER, flag INTEGER, content BLOB)')
        payload = drv.cmd_query(['--workdir', self.dir, '--task', 'conversations'])
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'SCHEMA_UNSUPPORTED')
        self.assertIn('send_time', payload['detail'])

    def test_messages_task_missing_table_also_schema_unsupported(self):
        """messages 任务同样命中缺表分类（同一 except 边界）。"""
        self._db_with('CREATE TABLE unrelated_table (x INTEGER)')
        payload = drv.cmd_query(['--workdir', self.dir, '--task', 'messages', '--conv', 'S:x_y'])
        self.assertEqual(payload['error'], 'SCHEMA_UNSUPPORTED')

    def test_corrupt_db_stays_sql_error(self):
        """坏库（非 SQLite 文件，DatabaseError 而非缺表/缺列）保持 SQL_ERROR，不误升 compat。"""
        with open(os.path.join(self.dir, 'message.db.dec.db'), 'wb') as f:
            f.write(b'this is not a sqlite database at all....')
        payload = drv.cmd_query(['--workdir', self.dir, '--task', 'conversations'])
        self.assertEqual(payload['error'], 'SQL_ERROR')

    def test_protocol_schema_unsupported_end_to_end(self):
        """子进程端到端：SCHEMA_UNSUPPORTED 经 LOCALDB_JSON 协议透出（跨进程契约）。"""
        self._db_with('CREATE TABLE unrelated_table (x INTEGER)')
        payload, _ = run_driver(['query', '--workdir', self.dir, '--task', 'conversations'])
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'SCHEMA_UNSUPPORTED')
        self.assertIn('message_table', payload['detail'])


if __name__ == '__main__':
    unittest.main()
