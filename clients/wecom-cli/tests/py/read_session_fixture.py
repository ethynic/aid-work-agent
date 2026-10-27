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


def insert(conn, state, conv, sender, ct=0, text=None):
    """unread-setup 专用：显式插入一行（content_type 可为系统类；send_time 递增保证 last 序）。"""
    state['id'] += 1
    content = b'' if text is None else b'\x0a' + bytes([len(text.encode('utf-8'))]) + text.encode('utf-8')
    conn.execute('INSERT INTO message_table(server_id,sequence,sender_id,conversation_id,content_type,send_time,flag,content) VALUES(?,?,?,?,?,?,?,?)',
                 (str(10**18 + state['id']), state['id'], sender, conv, ct, 1700000000 + state['id'], 0, content))


# P2 unread 场景（任务 3.1 待回复判定；会话覆盖：
#   S:SELF_PEER  [peer, self, peer, system1017]  last_participating=peer → 待回复（尾部系统消息不清除）
#   R:project    [peer, self]                    last_participating=self → 非待回复（本人最后发言不出现）
#   R:selfsys    [self, system38]                last_participating=self → 非待回复（系统消息不制造）
#   S:SELF_SELF  [self]                          自会话 → 不出现
#   R:pending    [peer]                          待回复（群，经 session R 名解析）
#   R:sysonly    [system31, system1017]          last_participating=null → 全系统类计数标注）
UNREAD_CONVS = {
    'project': 'R:project', 'selfsys': 'R:selfsys', 'selfconv': 'S:%s_%s' % (SELF, SELF),
    'pending': 'R:pending', 'sysonly': 'R:sysonly',
}


def unread_setup(base):
    with sqlite3.connect(base / 'message.plain.db') as conn:
        conn.execute('CREATE TABLE message_table(message_id INTEGER PRIMARY KEY AUTOINCREMENT,server_id TEXT,sequence INTEGER,sender_id TEXT,conversation_id TEXT,content_type INTEGER,send_time INTEGER,flag INTEGER,content BLOB)')
        st = {'id': 0}
        insert(conn, st, CONV, PEER, 0, '需求确认一下')
        insert(conn, st, CONV, SELF, 0, '收到')
        insert(conn, st, UNREAD_CONVS['project'], PEER, 0, 'kickoff')
        insert(conn, st, UNREAD_CONVS['project'], SELF, 0, '收到谢谢')
        insert(conn, st, UNREAD_CONVS['selfsys'], SELF, 0, 'ok')
        insert(conn, st, UNREAD_CONVS['selfsys'], SELF, 38)
        insert(conn, st, CONV, PEER, 0, '在吗')
        insert(conn, st, CONV, SELF, 1017)
        insert(conn, st, UNREAD_CONVS['selfconv'], SELF, 0, 'note to self')
        insert(conn, st, UNREAD_CONVS['pending'], PEER, 0, '请确认')
        insert(conn, st, UNREAD_CONVS['sysonly'], SELF, 31)
        insert(conn, st, UNREAD_CONVS['sysonly'], SELF, 1017)
    with sqlite3.connect(base / 'user.plain.db') as conn:
        conn.execute('CREATE TABLE user_table(id TEXT,name TEXT,alias TEXT,remark TEXT)')
        conn.executemany('INSERT INTO user_table VALUES(?,?,?,?)', [(SELF, 'Self', '', ''), (PEER, 'Peer', '', '')])
    with sqlite3.connect(base / 'session.plain.db') as conn:
        conn.execute('CREATE TABLE conversation_table(conversation_id TEXT,name TEXT)')
        conn.executemany('INSERT INTO conversation_table VALUES(?,?)', [
            (CONV, 'Peer'), (UNREAD_CONVS['project'], '项目群'), (UNREAD_CONVS['selfsys'], '自身系统群'),
            (UNREAD_CONVS['pending'], '待回复群'), (UNREAD_CONVS['sysonly'], '系统群'),
            (UNREAD_CONVS['selfconv'], '备忘'),
        ])
    for logical in ('message', 'user', 'session'):
        encrypted(base, logical)


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
    elif command == 'external-setup':
        unread_setup(base)
        with sqlite3.connect(base / 'user.plain.db') as conn:
            conn.execute('ALTER TABLE user_table ADD COLUMN corp_id INTEGER')
            conn.execute('UPDATE user_table SET corp_id=1 WHERE id=?', (SELF,))
            conn.execute('UPDATE user_table SET corp_id=2 WHERE id=?', (PEER,))
            conn.execute("INSERT INTO user_table(id,name,alias,remark,corp_id) VALUES('333','Peer','','',1)")
            conn.execute('CREATE TABLE external_user_relation_v3(user_id INTEGER)')
            conn.execute('INSERT INTO external_user_relation_v3 VALUES(?)', (PEER,))
        encrypted(base, 'user')
    elif command in ('external-duplicate', 'external-unknown'):
        with sqlite3.connect(base / 'user.plain.db') as conn:
            if command == 'external-duplicate':
                conn.execute("INSERT INTO user_table(id,name,alias,remark,corp_id) VALUES('9007199254740995','Peer','','',3)")
                conn.execute("INSERT INTO external_user_relation_v3 VALUES('9007199254740995')")
            else:
                conn.execute('DELETE FROM external_user_relation_v3 WHERE user_id=?', (PEER,))
        encrypted(base, 'user')
    elif command == 'unread-setup':
        unread_setup(base)
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
    elif command == 'rebuild-small':
        # 第八轮评审 P1 代际复现：同账号源库替换重建——DROP+CREATE 使 AUTOINCREMENT 归零
        # （新库 message_id 远小于旧水位），encrypted(replace=True) 经 os.replace 换文件
        # （新 fileIdentity → 代际变化）
        with sqlite3.connect(base / 'message.plain.db') as conn:
            conn.execute('DROP TABLE message_table')
            conn.execute('CREATE TABLE message_table(message_id INTEGER PRIMARY KEY AUTOINCREMENT,server_id TEXT,sequence INTEGER,sender_id TEXT,conversation_id TEXT,content_type INTEGER,send_time INTEGER,flag INTEGER,content BLOB)')
            insert(conn, {'id': 0}, CONV, PEER, 0, 'rebuilt-1')
        encrypted(base, 'message', replace=True)
    elif command == 'rewrite-same':
        # 第九轮评审 P1 复现：同文件重写——只做 DML（DELETE + sqlite_sequence 重置 + 重插
        # ID 1..count，内容/时间均不同），encrypted() 不 replace（write_bytes 原地截断重写，
        # fileIdentity 不变）且无 DDL（schema_version 不变）：TS 的代际校验对此完全不可见。
        with sqlite3.connect(base / 'message.plain.db') as conn:
            conn.execute('DELETE FROM message_table')
            conn.execute("DELETE FROM sqlite_sequence WHERE name='message_table'")
            for i in range(1, count + 1):
                text = ('rewritten-%d' % i).encode()
                conn.execute('INSERT INTO message_table(server_id,sequence,sender_id,conversation_id,content_type,send_time,flag,content) VALUES(?,?,?,?,?,?,?,?)',
                             (str(10**18 + i), i, PEER if i % 2 == 1 else SELF, CONV, 0, 1700200000 + i, 0,
                              b'\x0a' + bytes([len(text)]) + text))
        encrypted(base, 'message')
    elif command == 'delete-row':
        # 第九轮评审 P1：同文件删除 CONV 的指定 message_id 行（fileIdentity/schema_version
        # 不变；后续 populate 只会插入更大 id，不会重建同 id 行——锚点校验「行已不存在」路径）
        with sqlite3.connect(base / 'message.plain.db') as conn:
            conn.execute('DELETE FROM message_table WHERE conversation_id=? AND message_id=?', (CONV, count))
        encrypted(base, 'message')
    elif command == 'anon-pending':
        # 第九轮评审 P2：追加无命名来源的待回复会话（R:anon 不在 conversation_table、
        # 对方 id 不在 user_table）——反向取名应得 null
        with sqlite3.connect(base / 'message.plain.db') as conn:
            text = 'anon hello'.encode('utf-8')
            conn.execute('INSERT INTO message_table(server_id,sequence,sender_id,conversation_id,content_type,send_time,flag,content) VALUES(?,?,?,?,?,?,?,?)',
                         ('9000000000000000099', 99, 'anon-peer', 'R:anon', 0, 1700000099, 0,
                          b'\x0a' + bytes([len(text)]) + text))
        encrypted(base, 'message')
    elif command == 'peer-remark':
        # 第十轮评审 P2①：PEER 设备注（name 不变）——反向取名应输出 match_names=[name, remark]，
        # name 过滤按备注子串命中（与 resolve 的名称/备注/别名规则一致）
        with sqlite3.connect(base / 'user.plain.db') as conn:
            conn.execute('UPDATE user_table SET remark=? WHERE id=?', ('BossRemark', PEER))
        encrypted(base, 'user')
    elif command == 'bad-session-schema':
        with sqlite3.connect(base / 'session.plain.db') as conn:
            conn.execute('DROP TABLE conversation_table')
            conn.execute('CREATE TABLE conversation_table(unknown_field TEXT)')
        encrypted(base, 'session')


if __name__ == '__main__':
    main()
