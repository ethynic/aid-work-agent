"""群提及/姓名整链合成向量；独立构造 protobuf 并按参考算法加密。"""
from pathlib import Path
import sqlite3
import sys
from read_session_fixture import encrypted, SELF, PEER

base = Path(sys.argv[1])
base.mkdir(parents=True, exist_ok=True)


def vi(value):
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    return bytes(out + bytes([value]))


def field(number, value):
    if isinstance(value, int):
        return vi(number << 3) + vi(value)
    return vi((number << 3) | 2) + vi(len(value)) + value


def part(kind, payload):
    return field(1, field(1, kind) + field(2, payload))


def text(value):
    return part(0, field(1, value.encode('utf-8')))


def mention(uid, name=None):
    return part(5, field(1, int(uid)) + (field(2, name.encode('utf-8')) if name is not None else b''))


quote = '"王五：\n旧消息@某人"\n------\n'
messages = [
    (PEER, text('普通消息')),
    (SELF, text(quote) + text('是的') + mention(PEER, '张三') + text(' ')),
    (PEER, mention(PEER, '张三') + text(' ') + mention('9007199254740995', '李四旧名') + text(' 请确认🙂')),
    (PEER, mention('0', '所有人')),
    ('9007199254740993', mention('18446744073709551615')),
    (SELF, text('前') + part(99, b'unknown') + text('后')),
    (PEER, mention(SELF)),
    (PEER, text('旧类型片段') + mention(PEER, '张三')),
    (PEER, field(1, '旧格式纯文字'.encode('utf-8'))),
]
with sqlite3.connect(base / 'message.plain.db') as conn:
    conn.execute('CREATE TABLE message_table(message_id INTEGER PRIMARY KEY AUTOINCREMENT,server_id TEXT,sequence INTEGER,sender_id TEXT,conversation_id TEXT,content_type INTEGER,send_time INTEGER,flag INTEGER,content BLOB)')
    for index, (sender, content) in enumerate(messages):
        conn.execute('INSERT INTO message_table(sender_id,conversation_id,content_type,send_time,content) VALUES(?,?,?,?,?)',
                     (sender, 'R:project', 0 if index >= 7 else 2, 1700000000 + index, content))
with sqlite3.connect(base / 'user.plain.db') as conn:
    conn.execute('CREATE TABLE user_table(id TEXT,name TEXT)')
    conn.executemany('INSERT INTO user_table VALUES(?,?)', [(SELF, '本人'), (PEER, '张三'), ('9007199254740995', '李四现名')])
with sqlite3.connect(base / 'session.plain.db') as conn:
    conn.execute('CREATE TABLE conversation_table(id TEXT,name TEXT)')
    conn.execute('INSERT INTO conversation_table VALUES(?,?)', ('R:project', '项目群'))
for logical in ('message', 'user', 'session'):
    encrypted(base, logical)
