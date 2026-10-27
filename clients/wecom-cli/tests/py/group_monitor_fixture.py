"""Anonymous encrypted group data. Never discovers a real account or window."""
import json
from pathlib import Path
import sqlite3
import sys
import time
from read_session_fixture import encrypted, SELF, PEER
from test_localdb_query import _varint, text_content
from test_group_mentions import mention

base = Path(sys.argv[1])
action = sys.argv[2]
if action == 'setup':
    base.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(base / 'message.plain.db') as conn:
        conn.execute('CREATE TABLE message_table(message_id INTEGER PRIMARY KEY AUTOINCREMENT,server_id TEXT,sequence INTEGER,sender_id TEXT,conversation_id TEXT,content_type INTEGER,send_time INTEGER,flag INTEGER,content BLOB)')
        conn.execute('INSERT INTO message_table(sender_id,conversation_id,content_type,send_time,content) VALUES(?,?,?,?,?)',
                     (PEER, 'R:fixture-a', 2, int(time.time()), text_content('旧上下文：合成项目代号 ORCHID')))
    with sqlite3.connect(base / 'user.plain.db') as conn:
        conn.execute('CREATE TABLE user_table(id TEXT,name TEXT)')
        conn.executemany('INSERT INTO user_table VALUES(?,?)', [(SELF, 'FixtureSelf'), (PEER, 'FixturePeer')])
    with sqlite3.connect(base / 'session.plain.db') as conn:
        conn.execute('CREATE TABLE conversation_table(conversation_id TEXT,name TEXT)')
        conn.executemany('INSERT INTO conversation_table VALUES(?,?)', [('R:fixture-a', '合成群甲'), ('R:fixture-b', '合成群乙')])
    for logical in ('message', 'user', 'session'):
        encrypted(base, logical)
elif action == 'append':
    item = json.loads(sys.argv[3])
    content = text_content(item['text'])
    if item.get('mention_self'):
        content = mention(SELF, 'FixtureSelf') + content
    with sqlite3.connect(base / 'message.plain.db') as conn:
        for _ in range(item.get('count', 1)):
            conn.execute('INSERT INTO message_table(sender_id,conversation_id,content_type,send_time,content) VALUES(?,?,?,?,?)',
                     (SELF if item.get('self') else PEER, item.get('conversation_id', 'R:fixture-a'), 2,
                      item.get('send_time', int(time.time())), content))
    encrypted(base, 'message')
elif action == 'replace':
    encrypted(base, 'message', replace=True)
elif action == 'reuse-name':
    with sqlite3.connect(base / 'session.plain.db') as conn:
        conn.execute("UPDATE conversation_table SET name='已改名' WHERE conversation_id='R:fixture-a'")
        conn.execute("INSERT INTO conversation_table VALUES('R:other', '合成群甲')")
    encrypted(base, 'session')
elif action == 'session-visible':
    item = json.loads(sys.argv[3])
    with sqlite3.connect(base / 'session.plain.db') as conn:
        conn.execute("DELETE FROM conversation_table WHERE conversation_id='R:fixture-b'")
        if item['visible']:
            conn.execute("INSERT INTO conversation_table VALUES('R:fixture-b','合成群乙')")
    encrypted(base, 'session')
elif action == 'verify-wechat-sender':
    with sqlite3.connect(base / 'user.plain.db') as conn:
        conn.execute('ALTER TABLE user_table ADD COLUMN corp_id INTEGER')
        conn.execute('UPDATE user_table SET corp_id=? WHERE id=?', (12345, SELF))
        conn.execute('UPDATE user_table SET corp_id=? WHERE id=?', (1970325134026788, PEER))
    encrypted(base, 'user')
else:
    raise ValueError('unknown fixture action')
