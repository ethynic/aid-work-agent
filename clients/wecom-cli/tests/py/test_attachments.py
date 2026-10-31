"""Attachments belong to their message; metadata must not expand private content."""
import base64
import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import test_business_sessions as business
from test_localdb_query import drv, CONV_S, CONV_R1, SELF, PEER, msg_row, text_content
from attachment_fixture import field, file_content


@contextmanager
def connection(path):
    conn = sqlite3.connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


class AttachmentTest(unittest.TestCase):
    read = business.BusinessSessionsTest.read
    append = business.BusinessSessionsTest.append
    def setUp(self):
        business.BusinessSessionsTest.setUp(self)
        self.account = Path(self.temp.name) / 'account'
        (self.account / 'Cache' / 'Image').mkdir(parents=True)
        (self.account / 'Cache' / 'File').mkdir(parents=True)
        (self.account / 'CacheMapping').mkdir()
        self.mapping = self.account / 'CacheMapping' / 'cache.db'
        with connection(self.mapping) as conn:
            conn.execute('CREATE TABLE mapping(type INTEGER,key TEXT,file_name TEXT,last_modify_time INTEGER,file_md5 TEXT)')

    def attachment(self, mid, **options):
        result = self.read(targets=[{'conversation_id': CONV_R1}], account_dir=str(self.account),
                           attachment={'message_id': mid, **options})
        self.assertTrue(result['ok'], result)
        return result

    def add_file(self, content, name='sample.md', resource='https://example.invalid/synthetic-file'):
        path = self.account / 'Cache' / 'File' / name
        path.write_bytes(content)
        with connection(self.mapping) as conn:
            conn.execute('INSERT INTO mapping VALUES (1,?,?,0,?)', (resource, 'File/' + name, ''))
        self.append([msg_row(CONV_R1, PEER, 102, 1700000100, file_content(name, resource, content))])
        with connection(self.path) as conn:
            mid = conn.execute('select max(message_id) from message_table').fetchone()[0]
        return mid, path

    def add_image(self):
        out = io.BytesIO()
        Image.new('RGB', (9, 7), 'green').save(out, 'PNG')
        raw = out.getvalue()
        path = self.account / 'Cache' / 'Image' / 'cached.jpg'
        path.write_bytes(raw)
        key = 'https://example.invalid/synthetic-image'
        with connection(self.mapping) as conn:
            conn.execute('INSERT INTO mapping VALUES (1,?,?,0,?)', (key, 'Image/cached.jpg', ''))
        content = field(3, key.encode()) + field(8, b'0' * 32)
        self.append([msg_row(CONV_R1, PEER, 101, 1700000101, content)])
        with connection(self.path) as conn:
            mid = conn.execute('select max(message_id) from message_table').fetchone()[0]
        return mid, path, raw

    def test_metadata_does_not_expand_attachment_or_expose_location(self):
        self.add_image()
        self.add_file(b'private fixture body')
        data = self.read(targets=[{'conversation_id': CONV_R1}], account_dir=str(self.account))
        messages = data['sessions'][0]['messages']
        metadata = [m['attachment'] for m in messages if 'attachment' in m]
        self.assertEqual([a['status'] for a in metadata], ['available', 'available'])
        self.assertEqual(metadata[0]['mime_type'], 'image/png')
        encoded = json.dumps(metadata)
        for private in ('https://', str(self.account), 'private fixture body', 'base64'):
            self.assertNotIn(private, encoded)

    def test_mapped_image_accepts_reencoded_cache_and_actual_format(self):
        mid, path, raw = self.add_image()
        result = self.attachment(mid)
        item = result['attachment']
        self.assertEqual(item['status'], 'available')
        self.assertEqual(item['image']['mimeType'], 'image/png')
        self.assertEqual(base64.b64decode(item['image']['data']), raw)
        self.assertEqual((item['width'], item['height']), (9, 7))
        self.assertNotIn('state', result)
        self.assertNotIn('sessions', result)

    def test_utf8_byte_continuations_never_split_or_repeat_text(self):
        text = '\ufeff# Heading\n中文🙂end\n'
        mid, _ = self.add_file(text.encode())
        offset, chunks = 0, []
        for _ in range(len(text.encode()) + 1):
            item = self.attachment(mid, offset=offset, max_bytes=1)['attachment']
            self.assertEqual(item['status'], 'available', item)
            chunks.append(item['text'])
            self.assertGreater(item['next_offset'], offset)
            offset = item['next_offset']
            if not item['has_more']:
                break
        self.assertEqual(''.join(chunks), text.removeprefix('\ufeff'))
        self.assertEqual(offset, len(text.encode()))

    def test_long_line_large_file_reads_only_requested_byte_window(self):
        mid, path = self.add_file(b'x' * (8 * 1024 * 1024))
        reads = []
        original = builtins.open

        class Window:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def seek(self, *args): return self.stream.seek(*args)
            def read(self, size=-1):
                reads.append(size)
                return self.stream.read(size)

        def watched(name, mode='r', *args, **kwargs):
            stream = original(name, mode, *args, **kwargs)
            return Window(stream) if os.fspath(name) == str(path) and mode == 'rb' else stream
        with patch('builtins.open', watched):
            item = self.attachment(mid, offset=2000, max_bytes=100)['attachment']
        self.assertEqual(item['text'], 'x' * 100)
        self.assertEqual(item['next_offset'], 2100)
        self.assertTrue(item['has_more'])
        self.assertEqual(reads, [104], 'one bounded byte window; no whole-file decoding or hashing')

    def test_attachment_reads_original_message_outside_recent_window(self):
        mid, _ = self.add_file(b'original document')
        self.append([msg_row(CONV_R1, PEER, 2, 1700000200 + i, text_content('later')) for i in range(80)])
        self.assertEqual(self.attachment(mid)['attachment']['text'], 'original document')
        wrong = self.read(targets=[{'conversation_id': CONV_S}], account_dir=str(self.account), attachment={'message_id': mid})
        self.assertEqual(wrong['attachment']['status'], 'not_found', 'message ID alone cannot cross its conversation')

    def test_missing_cache_does_not_hide_neighboring_chat(self):
        _, path, _ = self.add_image()
        path.unlink()
        data = self.read(targets=[{'conversation_id': CONV_R1}], account_dir=str(self.account))
        self.assertTrue(data['ok'])
        messages = data['sessions'][0]['messages']
        self.assertTrue(any(m.get('text') == 'customer question' for m in messages))
        self.assertEqual(messages[-1]['attachment']['status'], 'not_cached')

    def test_image_parser_failure_stays_on_one_attachment_not_the_timeline(self):
        mid, _, _ = self.add_image()
        for error in (Image.DecompressionBombError, SyntaxError):
            with self.subTest(error=error.__name__), patch('PIL.Image.open', side_effect=error('anonymous broken image')):
                timeline = self.read(targets=[{'conversation_id': CONV_R1}], account_dir=str(self.account))
                self.assertTrue(timeline['ok'])
                messages = timeline['sessions'][0]['messages']
                self.assertTrue(any(m.get('text') == 'customer question' for m in messages))
                self.assertEqual(messages[-1]['attachment']['status'], 'unreadable')
                item = self.attachment(mid)['attachment']
                self.assertEqual(item['status'], 'unreadable')
                self.assertNotIn('image', item)

    def test_mapping_cannot_read_an_unrelated_path_outside_account_cache(self):
        mid, path = self.add_file(b'safe')
        elsewhere = Path(self.temp.name) / 'not-an-attachment.md'
        elsewhere.write_text('not permitted')
        path.unlink()
        with connection(self.mapping) as conn:
            conn.execute('UPDATE mapping SET file_name=?', (str(elsewhere),))
        self.assertEqual(self.attachment(mid)['attachment']['status'], 'not_cached')

    def test_binary_document_is_metadata_only_not_text_or_executable(self):
        mid, _ = self.add_file(b'\x00\xff\x01', name='sample.zip')
        item = self.attachment(mid)['attachment']
        self.assertEqual(item['status'], 'unsupported')
        self.assertNotIn('text', item)


if __name__ == '__main__':
    unittest.main()
