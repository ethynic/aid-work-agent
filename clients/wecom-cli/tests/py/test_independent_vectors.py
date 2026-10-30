# tests/py/test_independent_vectors.py — 独立来源固定测试向量消费（任务 1.5 / 设计 §7）
#
# 目的：加密/解密自往返（walgen 复用驱动算法）只证自洽，不证兼容。本文件消费
# fixtures/wxaes128-vectors.json（由独立参考实现 ref_encryptor.py 按 sqlite3mc
# cipher_wxaes128.c 规格生成、与生产驱动无代码同源关系）：
#   - decrypt_page 单元级：向量密文 + mkey → 输出 == 向量明文（页 1 校验明文页头写回）；
#   - decrypt 全链路（子进程）：向量密文拼整库 → 驱动解密 → 输出库 == 向量明文拼接、
#     integrity ok；错钥在页 1 先知快速失败；
#   - fixture 自检：向量密文确为 ref_encryptor 对向量明文的加密产物（防产物被手改后
#     与生成器脱钩）。
# 运行：npm run test:py（同 test_wecom_localdb.py，需 pycryptodome）。
import json
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))  # clients/wecom-cli
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, 'drivers', 'py'))

import ref_encryptor as ref  # noqa: E402  （仅 fixture 自检用；断言主链路只依赖向量文件）
import wecom_localdb as drv  # noqa: E402

DRIVER = os.path.join(_ROOT, 'drivers', 'py', 'wecom_localdb.py')
VECTORS_PATH = os.path.join(_HERE, 'fixtures', 'wxaes128-vectors.json')
SQLITE_MAGIC = b'SQLite format 3\x00'

with open(VECTORS_PATH, 'r', encoding='utf-8') as _f:
    _FIXTURE = json.load(_f)
VECTORS = {v['name']: v for v in _FIXTURE['vectors']}


def _vector_bytes(v, field):
    return bytes.fromhex(v[field])


def _vec(name):
    v = VECTORS[name]
    return {
        'name': name,
        'mkey': _vector_bytes(v, 'mkey_hex'),
        'pgno': v['pgno'],
        'plain': _vector_bytes(v, 'plaintext_hex'),
        'cipher': _vector_bytes(v, 'ciphertext_hex'),
    }


def run_driver(args, stdin_text=None):
    """起子进程跑驱动，解析最后一行 LOCALDB_JSON:（与 test_wecom_localdb 同一协议契约）。"""
    p = subprocess.run([sys.executable, DRIVER] + args, input=(stdin_text or '').encode('utf-8'),
                       capture_output=True, timeout=120)
    out = p.stdout.decode('utf-8', 'replace')
    lines = [l for l in out.splitlines() if l.startswith('LOCALDB_JSON: ')]
    assert lines, '驱动未输出 LOCALDB_JSON 行；stdout 尾部：%r stderr：%r' % (out[-300:], p.stderr.decode('utf-8', 'replace')[-300:])
    assert p.returncode == 0, '驱动退出码须为 0（协议约定），实际 %d' % p.returncode
    return json.loads(lines[-1][len('LOCALDB_JSON: '):]), out


class FixtureSelfCheckTest(unittest.TestCase):
    """向量文件自检：密文 == 独立参考实现对（mkey, pgno, 明文）的加密产物（防手改脱钩）。"""

    def test_vectors_match_reference_implementation(self):
        self.assertEqual(_FIXTURE['schema_version'], 1)
        for name, v in VECTORS.items():
            mkey = _vector_bytes(v, 'mkey_hex')
            plain = _vector_bytes(v, 'plaintext_hex')
            cipher = _vector_bytes(v, 'ciphertext_hex')
            self.assertEqual(len(mkey), 16, name)
            expect = ref.ref_encrypt_page(mkey, v['pgno'], plain)
            self.assertEqual(cipher, expect, name)

    def test_page1_layout_invariants(self):
        """页 1 向量布局不变量：[16:24] 存明文页头副本（搬移布局的落盘特征）。"""
        for name in ('page1-relocated-header', 'page1-second-key'):
            v = VECTORS[name]
            cipher = _vector_bytes(v, 'ciphertext_hex')
            plain = _vector_bytes(v, 'plaintext_hex')
            self.assertEqual(v['pgno'], 1, name)
            self.assertEqual(cipher[16:24], plain[16:24], name)
            self.assertEqual(plain[0:16], SQLITE_MAGIC, name)


class DecryptPageVectorTest(unittest.TestCase):
    """decrypt_page 单元级：独立向量逐组断言（密文 + mkey → 明文；页 1 页头写回）。"""

    def test_each_vector_roundtrips_via_driver(self):
        for name in VECTORS:
            v = _vec(name)
            dec = drv.decrypt_page(v['mkey'], v['pgno'], v['cipher'])
            self.assertIsNotNone(dec, '%s：页 1 先知校验不应失败（正确钥）' % name)
            if v['pgno'] == 1:
                self.assertEqual(dec[0:16], SQLITE_MAGIC, name)      # 明文页头写回
                self.assertEqual(dec[16:], v['plain'][16:], name)    # 与独立向量一致
            else:
                self.assertEqual(dec, v['plain'], name)

    def test_wrong_key_fails_page1_oracle(self):
        """错钥：页 1 先知校验失败 → None（每个页 1 向量各验一次）。"""
        for name in ('page1-relocated-header', 'page1-second-key'):
            v = _vec(name)
            wrong = bytes(b ^ 0xFF for b in v['mkey'])
            self.assertIsNone(drv.decrypt_page(wrong, 1, v['cipher']), name)


class DecryptFullChainVectorTest(unittest.TestCase):
    """decrypt 全链路（子进程）：向量密文拼整库 → 驱动解密 → 输出 == 向量明文拼接。"""

    CHAIN = ('page1-relocated-header', 'normal-page-2', 'normal-page-3', 'normal-page-4')

    def _build_chain(self, tmp):
        """同 mkey 连续页号向量拼成整库密文；返回 (密文库路径, 期望明文拼接, mkey)。"""
        vecs = [_vec(name) for name in self.CHAIN]
        self.assertEqual(len({v['mkey'] for v in vecs}), 1, '链向量须同 mkey')
        self.assertEqual([v['pgno'] for v in vecs], [1, 2, 3, 4], '链向量须连续页号')
        db_path = os.path.join(tmp, 'snapshot.db')
        with open(db_path, 'wb') as f:
            f.write(b''.join(v['cipher'] for v in vecs))
        return db_path, b''.join(v['plain'] for v in vecs), vecs[0]['mkey']

    def test_full_chain_matches_independent_vectors(self):
        with tempfile.TemporaryDirectory(prefix='wxaes128-vectors-dec-') as tmp:
            db_path, expected_plain, mkey = self._build_chain(tmp)
            outdir = os.path.join(tmp, 'out')
            payload, out = run_driver(['decrypt', db_path, '-', outdir], stdin_text=mkey.hex())
            self.assertTrue(payload['ok'], payload.get('detail', ''))
            self.assertEqual(payload['integrity'], 'ok')
            self.assertEqual(payload['pages'], 4)
            with open(payload['out_db'], 'rb') as f:
                self.assertEqual(f.read(), expected_plain)
            # 密钥不进 stdout（§8：仅 stdin 流转）
            self.assertNotIn(mkey.hex(), out)

    def test_full_chain_wrong_key_fails_fast_no_output(self):
        """错钥走独立向量密文库：页 1 先知快速失败，无半解密产物。"""
        with tempfile.TemporaryDirectory(prefix='wxaes128-vectors-badkey-') as tmp:
            db_path, _, mkey = self._build_chain(tmp)
            wrong = bytes(range(16, 32))
            self.assertNotEqual(wrong, mkey)
            outdir = os.path.join(tmp, 'out')
            payload, _ = run_driver(['decrypt', db_path, '-', outdir], stdin_text=wrong.hex())
            self.assertFalse(payload['ok'])
            self.assertEqual(payload['error'], 'KEY_VERIFY_FAILED')
            self.assertFalse(os.path.exists(outdir))


if __name__ == '__main__':
    unittest.main()
