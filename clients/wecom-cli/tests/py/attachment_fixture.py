"""Add anonymous attachments to an existing group_monitor_fixture encrypted account."""
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager
import sys

from PIL import Image
from read_session_fixture import encrypted, SELF, PEER
from test_localdb_query import _varint



@contextmanager
def db_connection(path):
    conn = sqlite3.connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def field(number, value):
    return _varint(number * 8 + 2) + _varint(len(value)) + value


def file_content(name, resource, data):
    return (field(2, name.encode()) + field(3, resource.encode())
            + _varint(4 * 8) + _varint(len(data))
            + field(8, hashlib.md5(data).hexdigest().encode()))


def add_attachments(base):
    base = Path(base)
    account = base / SELF
    image_dir, file_dir = account / 'Cache' / 'Image', account / 'Cache' / 'File'
    image_dir.mkdir(parents=True, exist_ok=True)
    file_dir.mkdir(parents=True, exist_ok=True)
    image = io.BytesIO()
    Image.new('RGB', (12, 8), 'blue').save(image, format='PNG')
    image_data = image.getvalue()
    # Deliberately mismatched extension and remote digest: the cache is a decoded resource.
    (image_dir / 'anonymous.jpg').write_bytes(image_data)
    text = '# Anonymous fixture\n\nCustomer 示例：read only requested bytes.\n' * 150
    (file_dir / 'sample.md').write_bytes(text.encode('utf-8'))
    (account / 'CacheMapping').mkdir(exist_ok=True)
    resources = [('https://example.invalid/image?test=synthetic', 'Image/anonymous.jpg'),
                 ('https://example.invalid/document?test=synthetic', 'File/sample.md')]
    with db_connection(account / 'CacheMapping' / 'cache.db') as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS mapping(type INTEGER,key TEXT,file_name TEXT,last_modify_time INTEGER,file_md5 TEXT)')
        connection.executemany('INSERT INTO mapping VALUES (1,?,?,0,?)', [(k, p, '') for k, p in resources])
    image_content = field(3, resources[0][0].encode()) + _varint(4 * 8) + _varint(12345) + field(8, b'0' * 32)
    with db_connection(base / 'message.plain.db') as connection:
        ids = []
        for ct, content in [(101, image_content), (102, file_content('sample.md', resources[1][0], text.encode()))]:
            row = connection.execute('INSERT INTO message_table(sender_id,conversation_id,content_type,send_time,content) VALUES(?,?,?,?,?)',
                                     (PEER, 'R:fixture-a', ct, 1700000000 + ct, content))
            ids.append(row.lastrowid)
    encrypted(base, 'message')
    return {'image_message_id': ids[0], 'file_message_id': ids[1], 'text': text}


if __name__ == '__main__':
    print(json.dumps(add_attachments(sys.argv[1]), ensure_ascii=True))
