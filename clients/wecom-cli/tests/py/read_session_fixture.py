"""P1b 合成三库：独立参考加密，无真实企微数据或密钥。供 TS 整链验收调用。"""
from pathlib import Path
import os
import sqlite3
import sys
from ref_encryptor import ref_encrypt_page

KEY = bytes(range(16))
SELF = '9007199254740997'
PEER = '9007199254740999'
CONV = 'S:%s_%s' % (SELF, PEER)


def encrypted(base, logical, replace=False):
    conn = sqlite3.connect(base / (logical + '.plain.db'))
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        conn.close()
    plain = (base / (logical + '.plain.db')).read_bytes()
    data = b''.join(ref_encrypt_page(KEY, i // 4096 + 1, plain[i:i + 4096]) for i in range(0, len(plain), 4096))
    path = base / SELF / 'Data' / (logical + '.db')
    path.parent.mkdir(parents=True, exist_ok=True)
    if replace:
        temp = path.with_suffix('.new.db')
        temp.write_bytes(data)
        os.replace(temp, path)
    else:
        path.write_bytes(data)


def populate(conn, count, conv=CONV):
    offset = conn.execute('SELECT COALESCE(MAX(message_id),0) FROM message_table').fetchone()[0]
    for i in range(offset + 1, offset + count + 1):
        text = ('synthetic-%d' % i).encode()
        conn.execute('INSERT INTO message_table(server_id,sequence,sender_id,conversation_id,content_type,send_time,flag,content) VALUES(?,?,?,?,?,?,?,?)',
                     (str(10**18 + i), i, SELF if i % 2 == 0 else PEER, conv, 0, 1700000000 + i, 0,
                      b'\x0a' + bytes([len(text)]) + text))


def main():
    base, command = Path(sys.argv[1]), sys.argv[2]
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    base.mkdir(parents=True, exist_ok=True)
    if command == 'setup':
        with sqlite3.connect(base / 'message.plain.db') as conn:
            conn.execute('CREATE TABLE message_table(message_id INTEGER PRIMARY KEY AUTOINCREMENT,server_id TEXT,sequence INTEGER,sender_id TEXT,conversation_id TEXT,content_type INTEGER,send_time INTEGER,flag INTEGER,content BLOB)')
            populate(conn, count)
        with sqlite3.connect(base / 'user.plain.db') as conn:
            conn.execute('CREATE TABLE user_table(id TEXT,name TEXT,alias TEXT,remark TEXT)')
            conn.executemany('INSERT INTO user_table VALUES(?,?,?,?)', [(SELF, 'Self', '', ''), (PEER, 'Peer', '', '')])
        with sqlite3.connect(base / 'session.plain.db') as conn:
            conn.execute('CREATE TABLE conversation_table(conversation_id TEXT,name TEXT)')
            conn.executemany('INSERT INTO conversation_table VALUES(?,?)', [(CONV, 'Peer'), ('R:empty', 'Empty Group')])
        for logical in ('message', 'user', 'session'):
            encrypted(base, logical)
    elif command in ('append', 'append-empty', 'replace', 'ack', 'reuse'):
        with sqlite3.connect(base / 'message.plain.db') as conn:
            if command == 'ack':
                conn.execute("UPDATE message_table SET server_id='9999999999999999999', sequence=999")
            elif command == 'reuse':
                conn.execute("UPDATE message_table SET send_time=send_time+1000")
            elif command != 'replace':
                populate(conn, count, 'R:empty' if command == 'append-empty' else CONV)
        encrypted(base, 'message', replace=command == 'replace')
    elif command in ('missing-self', 'duplicate-name', 'rename-peer'):
        with sqlite3.connect(base / 'user.plain.db') as conn:
            if command == 'missing-self':
                conn.execute('DELETE FROM user_table WHERE id=?', (SELF,))
            elif command == 'rename-peer':
                conn.execute("UPDATE user_table SET name='Renamed',alias='',remark='' WHERE id=?", (PEER,))
            else:
                conn.execute("INSERT INTO user_table VALUES('333','Peer','','')")
        encrypted(base, 'user')
    elif command == 'bad-session-schema':
        with sqlite3.connect(base / 'session.plain.db') as conn:
            conn.execute('DROP TABLE conversation_table')
            conn.execute('CREATE TABLE conversation_table(unknown_field TEXT)')
        encrypted(base, 'session')


if __name__ == '__main__':
    main()
