"""群查询整链合成库：独立加密、乱序发送时间，不接触真实用户数据。"""
from pathlib import Path
import sqlite3
import sys
from read_session_fixture import encrypted, SELF, PEER

base = Path(sys.argv[1])
base.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(base / 'message.plain.db') as conn:
    conn.execute('CREATE TABLE message_table(message_id INTEGER PRIMARY KEY AUTOINCREMENT,server_id TEXT,sequence INTEGER,sender_id TEXT,conversation_id TEXT,content_type INTEGER,send_time INTEGER,flag INTEGER,content BLOB)')
    for i in range(105):
        stamp = 1700000000 + (i * 37) % 105
        text = ('synthetic-%d' % i).encode()
        conn.execute('INSERT INTO message_table(sender_id,conversation_id,content_type,send_time,content) VALUES(?,?,?,?,?)',
                     (SELF if i % 2 else PEER, 'R:project', 0, stamp, b'\x0a' + bytes([len(text)]) + text))
    conn.execute('INSERT INTO message_table(sender_id,conversation_id,content_type,send_time,content) VALUES(?,?,?,?,?)',
                 (PEER, 'R:project', 0, 1600000000, b'\x0a\x04late'))
with sqlite3.connect(base / 'user.plain.db') as conn:
    conn.execute('CREATE TABLE user_table(id TEXT,name TEXT)')
    conn.executemany('INSERT INTO user_table VALUES(?,?)', [(SELF, 'Self'), (PEER, '项目群')])
with sqlite3.connect(base / 'session.plain.db') as conn:
    conn.execute('CREATE TABLE conversation_table(conversation_id TEXT,name TEXT)')
    conn.executemany('INSERT INTO conversation_table VALUES(?,?)', [('R:project', '项目群'), ('R:empty', '空群'), ('R:literal', '100%_群')])
for logical in ('message', 'user', 'session'):
    encrypted(base, logical)
