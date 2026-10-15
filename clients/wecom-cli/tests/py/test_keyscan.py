# tests/py/test_keyscan.py — C6 keyscan 模式专项验证（任务 1.6；Windows-only，非 win32 skip）
#
# 三层：
#   ① 先知逻辑纯单测（平台无关）：verify_key/scan_buffer 对【独立向量】（ref_encryptor 生成的
#      fixtures/wxaes128-vectors.json）与合成 aes256 页断言——aes128 复用页 1 向量密文构造先知，
#      aes256 手工按同构派生（SHA256 页密钥）构造合成页；
#   ② 扫描纯函数单测（平台无关）：scan_buffer 对齐预筛 / 全量步长 1 回退 / 未命中语义；
#   ③ 合成进程端到端（Windows-only）：辅助进程 keyhold_helper.py 持已知 16B 随机密钥，
#      父进程经驱动协议 keyscan 扫该 pid，断言命中且 key_hex 一致——不依赖真实企微。
# 运行：npm run test:py。
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))  # clients/wecom-cli
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, 'drivers', 'py'))

import ref_encryptor as ref  # noqa: E402  （合成加密页 1 用：独立来源，与驱动解密互为对照）
import wecom_localdb as drv  # noqa: E402

DRIVER = os.path.join(_ROOT, 'drivers', 'py', 'wecom_localdb.py')
HELPER = os.path.join(_HERE, 'keyhold_helper.py')
VECTORS_PATH = os.path.join(_HERE, 'fixtures', 'wxaes128-vectors.json')
SQLITE_MAGIC = b'SQLite format 3\x00'

IS_WINDOWS = sys.platform == 'win32'


def synthetic_plain_page1():
    """合成明文页 1：真实 SQLite 头常量布局（magic + 页头 + dbsize），内容无关判定只需 [16:24]。"""
    plain = bytearray(ref.PAGE_SIZE)
    plain[0:16] = SQLITE_MAGIC
    plain[16:24] = bytes([0x10, 0x00, 0x01, 0x01, 0x00, 0x40, 0x20, 0x20])  # 页头（写版本/页大小等）
    plain[24:32] = struct.pack('>II', 4, 0)  # 库大小（页）/ freelist 占位
    return bytes(plain)


def synthetic_encrypted_page1(mkey):
    """合成加密页 1（ref_encryptor 独立加密）→ 写文件供 keyscan --db / build_oracle 用。"""
    return ref.ref_encrypt_page(mkey, 1, synthetic_plain_page1())


def run_driver(args, timeout=120):
    """起子进程跑驱动，解析最后一行 LOCALDB_JSON:（协议契约本身也是被测对象）。"""
    p = subprocess.run([sys.executable, DRIVER] + args, input=b'', capture_output=True, timeout=timeout)
    out = p.stdout.decode('utf-8', 'replace')
    lines = [l for l in out.splitlines() if l.startswith('LOCALDB_JSON: ')]
    assert lines, '驱动未输出 LOCALDB_JSON 行；stdout 尾部：%r stderr：%r' % (out[-300:], p.stderr.decode('utf-8', 'replace')[-300:])
    assert p.returncode == 0, '驱动退出码须为 0（协议约定），实际 %d' % p.returncode
    return json.loads(lines[-1][len('LOCALDB_JSON: '):]), out


def hashlib_sha256(data):
    import hashlib
    return hashlib.sha256(data).digest()


class OraclePureTest(unittest.TestCase):
    """先知逻辑纯单测（平台无关）：verify_key 对独立向量与合成 aes256 页。"""

    @classmethod
    def setUpClass(cls):
        with open(VECTORS_PATH, 'r', encoding='utf-8') as f:
            vectors = {v['name']: v for v in json.load(f)['vectors']}
        v = vectors['page1-relocated-header']
        cls.mkey = bytes.fromhex(v['mkey_hex'])
        cls.page1 = bytes.fromhex(v['ciphertext_hex'])  # 独立来源密文页 1

    def test_verify_key_aes128_with_independent_vector(self):
        """独立向量密文页 1 → 先知验证正确钥命中 aes128、错钥未命中。"""
        oracle = drv.build_oracle(self.page1)
        self.assertEqual(drv.verify_key(oracle, self.mkey), 'aes128')
        wrong = bytes(b ^ 0xFF for b in self.mkey)
        self.assertIsNone(drv.verify_key(oracle, wrong))
        self.assertIsNone(drv.verify_key(oracle, self.mkey[:8]))  # 长度不符直接不验证

    def test_verify_key_aes256_synthetic_page(self):
        """aes256 分支：按 SHA256 页密钥同构派生手工构造合成页 → 命中 aes256。"""
        from Crypto.Cipher import AES
        k32 = bytes(range(32))
        t1 = struct.pack('<I', 1) + drv.SALT
        iv = drv.gen_iv(1)
        p0 = bytes([0x10, 0x00, 0x01, 0x01, 0x00, 0x40, 0x20, 0x20]) + b'\x5a' * 8  # 明文首块
        c0 = AES.new(hashlib_sha256(k32 + t1), AES.MODE_ECB).encrypt(
            bytes(x ^ y for x, y in zip(p0, iv)))
        page1 = bytearray(ref.PAGE_SIZE)
        page1[8:16] = c0[0:8]
        page1[16:24] = p0[0:8]
        page1[24:32] = c0[8:16]
        oracle = drv.build_oracle(bytes(page1))
        self.assertEqual(drv.verify_key(oracle, k32), 'aes256')
        self.assertIsNone(drv.verify_key(oracle, k32[:16]))  # 同位置的 16B 窗口不得误判 aes128
        self.assertIsNone(drv.verify_key(oracle, bytes(range(32, 64))))

    def test_build_oracle_rejects_short_page(self):
        """页 1 不足 4096B → 无法构造先知（结构化错误路径的前提）。"""
        self.assertIsNone(drv.build_oracle(b'\x00' * 100))
        self.assertIsNone(drv.build_oracle(b''))


class ScanBufferTest(unittest.TestCase):
    """扫描纯函数单测（平台无关）：对齐预筛 / 全量步长 1 回退 / 零字节预筛不吞真钥。"""

    @staticmethod
    def _oracle_for(mkey):
        """先知与被扫密钥同源构造（每个用例独立，防止 A 钥先知误配 B 钥缓冲）。"""
        return drv.build_oracle(synthetic_encrypted_page1(mkey))

    def test_aligned_offset_hit_by_prescreen(self):
        """密钥在 16 对齐偏移：对齐预筛段直接命中。"""
        mkey = bytes(range(16))
        data = b'\xab' * 48 + mkey + b'\xcd' * 48
        self.assertEqual(drv.scan_buffer(data, self._oracle_for(mkey), full_stride=False),
                         ('aes128', mkey))

    def test_unaligned_offset_needs_full_stride(self):
        """密钥在非对齐偏移：对齐段 miss（None），全量步长 1 命中。"""
        mkey = bytes(range(16))
        data = b'\xab' * 8 + mkey + b'\xcd' * 48  # 偏移 8，非 16 对齐
        oracle = self._oracle_for(mkey)
        self.assertIsNone(drv.scan_buffer(data, oracle, full_stride=False))
        self.assertEqual(drv.scan_buffer(data, oracle, full_stride=True), ('aes128', mkey))

    def test_zero_heavy_window_skipped_without_false_negative(self):
        """零字节预筛跳过 >半数零字节窗口；密钥零字节 ≤ 半数时不被误吞。"""
        mkey = bytes(range(16))
        self.assertIsNone(drv.scan_buffer(bytes(4096), self._oracle_for(mkey), full_stride=True))
        key = bytes([0] * 4 + [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])  # 4 零 ≤ 8，预筛放行
        data = b'\xab' * 32 + key + b'\xcd' * 32
        self.assertEqual(drv.scan_buffer(data, self._oracle_for(key), full_stride=True),
                         ('aes128', key))

    def test_absent_key_not_found(self):
        """无钥缓冲（伪随机形态噪声，不含任何被验钥）→ None，不误报。"""
        mkey = bytes(range(16))
        noise = bytes(range(16, 256)) * 8  # 3840B 伪随机形态（刻意避开 00..0f 段）
        self.assertIsNone(drv.scan_buffer(noise, self._oracle_for(mkey), full_stride=True))

    def test_short_buffer_tolerated(self):
        """短于最小窗口的缓冲 → None（不越界）。"""
        oracle = self._oracle_for(bytes(range(16)))
        self.assertIsNone(drv.scan_buffer(b'\x01' * 15, oracle, full_stride=True))
        self.assertIsNone(drv.scan_buffer(b'', oracle, full_stride=False))


@unittest.skipUnless(IS_WINDOWS, 'keyscan 进程扫描依赖 Windows ctypes')
class KeyscanWindowsTest(unittest.TestCase):
    """Windows 进程扫描：OPEN_FAILED 分支 + 合成进程端到端（辅助进程持钥，不依赖真实企微）。"""

    @classmethod
    def setUpClass(cls):
        cls.mkey = os.urandom(16)
        cls.tmp = tempfile.TemporaryDirectory(prefix='wecom-keyscan-')
        db_path = os.path.join(cls.tmp.name, 'page1.db')
        with open(db_path, 'wb') as f:
            f.write(synthetic_encrypted_page1(cls.mkey))
        cls.db_path = db_path
        cls.oracle = drv.build_oracle(synthetic_encrypted_page1(cls.mkey))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_open_failed_on_dead_pid(self):
        """不可打开的 pid（0 非法）→ OPEN_FAILED 分支。"""
        status, hit, scanned = drv.scan_pid(0, self.oracle, drv.DEFAULT_MAX_REGION_BYTES)
        self.assertEqual(status, 'OPEN_FAILED')
        self.assertIsNone(hit)
        self.assertEqual(scanned, 0)

    def test_synthetic_process_keyscan_e2e(self):
        """合成进程端到端：辅助进程持已知密钥 → 驱动 keyscan 命中且 key_hex 一致；
        密钥 hex 只出现在 LOCALDB_JSON 行（stdout 其余位置与 stderr 不得出现）。"""
        helper = subprocess.Popen([sys.executable, HELPER, self.mkey.hex()],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            pid_line = helper.stdout.readline().decode('ascii', 'replace').strip()
            self.assertTrue(pid_line.isdigit(), '辅助进程首行应为 pid，实际：%r' % pid_line)
            pid = int(pid_line)

            payload, out = run_driver(
                ['keyscan', '--pids', str(pid), '--db', self.db_path], timeout=600)
            print('\n[keyscan e2e] pid=%s elapsed_ms=%s scanned_bytes=%s'
                  % (pid, payload.get('elapsed_ms'), payload.get('scanned_bytes')))
            self.assertTrue(payload['ok'], payload.get('detail', ''))
            self.assertEqual(payload['kind'], 'aes128')
            self.assertEqual(payload['key_hex'], self.mkey.hex())
            self.assertEqual(payload['pid'], pid)
            self.assertGreater(payload['elapsed_ms'], 0)
            self.assertGreater(payload['scanned_bytes'], 0)
            # 密钥只在 JSON 协议行出现一次（供 TS 内存接收），不落任何其他输出
            json_lines = [l for l in out.splitlines() if l.startswith('LOCALDB_JSON: ')]
            self.assertEqual(out.count(self.mkey.hex()), 1)
            self.assertIn(self.mkey.hex(), json_lines[-1])
        finally:
            helper.terminate()
            helper.wait(timeout=10)
            helper.stdout.close()  # 防 ResourceWarning（读 pid 行后管道不再使用）


class KeyscanProtocolTest(unittest.TestCase):
    """协议层（平台无关）：BAD_ARGS 用法；非 Windows 平台的结构化拒绝。"""

    def test_bad_args_missing_required(self):
        """缺 --pids / --db / 非法 pid 列表 → BAD_ARGS、退出码 0。"""
        for args in (['keyscan'],
                     ['keyscan', '--db', 'x.db'],
                     ['keyscan', '--pids', '1,2'],
                     ['keyscan', '--pids', 'a,b', '--db', 'x.db'],
                     ['keyscan', '--pids', '0', '--db', 'x.db'],
                     ['keyscan', '--pids', '1', '--db', 'x.db', '--max-region-bytes', 'oops']):
            payload, _ = run_driver(args)
            self.assertEqual(payload['error'], 'BAD_ARGS', args)

    def test_non_windows_structured_reject(self):
        """非 win32：合法参数也返回 WINDOWS_REQUIRED（win32 上跳过该断言）。"""
        if IS_WINDOWS:
            self.skipTest('仅非 Windows 平台验证 WINDOWS_REQUIRED 分支')
        with tempfile.TemporaryDirectory(prefix='wecom-keyscan-proto-') as tmp:
            db_path = os.path.join(tmp, 'page1.db')
            with open(db_path, 'wb') as f:
                f.write(synthetic_encrypted_page1(bytes(range(16))))
            payload, _ = run_driver(['keyscan', '--pids', '1', '--db', db_path])
            self.assertFalse(payload['ok'])
            self.assertEqual(payload['error'], 'WINDOWS_REQUIRED')

    def test_db_unreadable(self):
        """--db 文件缺失 → DB_UNREADABLE（win32 上有进程语义；非 win32 先落 WINDOWS_REQUIRED）。"""
        if not IS_WINDOWS:
            self.skipTest('非 Windows 平台按 WINDOWS_REQUIRED 分支处理')
        payload, _ = run_driver(['keyscan', '--pids', '1', '--db',
                                 os.path.join(tempfile.gettempdir(), 'no-such-keyscan-db.db')])
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'DB_UNREADABLE')


if __name__ == '__main__':
    unittest.main()
