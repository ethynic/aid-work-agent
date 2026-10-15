# tests/py/test_wecom_localdb.py — 冻结点 1 专项验证：可信快照判定规则（设计 §3.3 / 任务 1.4 场景表）
#
# 运行方式（不在 TS 默认 test 链内）：
#   npm run test:py
#   或 venv python -m unittest discover -s tests/py -v
# （需 pycryptodome；系统 python 装有该包亦可跑）
#
# fixture 策略：底座用【真实 SQLite】生成（WAL 模式两事务，打开连接期间拷贝 db+wal——与生产
# 捕获协议同构），帧页为真实明文页；再用 walgen 按生产驱动同源算法加密并重组缺陷场景。
# 场景表（冻结点 1）：
#   正常（含旧代尾帧正确截断）/ 坏校验和链 / 截断（撕裂尾帧）/ 未提交尾帧 /
#   坏魔数 / 头不完整 / 空 WAL / 无 WAL / 错钥 / 用法错误
# 每个失败场景同时断言「不返回半解密结果」（无输出文件 / SNAPSHOT_UNSTABLE）。
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))  # clients/wecom-cli
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, 'drivers', 'py'))

import walgen  # noqa: E402
import wecom_localdb as drv  # noqa: E402

DRIVER = os.path.join(_ROOT, 'drivers', 'py', 'wecom_localdb.py')
MKEY = bytes(range(16))  # 测试专用合成密钥（非真实密钥）
BASE_ROWS = ['base-1', 'base-2', 'base-3']
T2_ROWS = ['t2-a', 't2-b']
T3_ROWS = ['t3-x', 't3-y']


def run_driver(args, stdin_text=None):
    """起子进程跑驱动，解析最后一行 LOCALDB_JSON:（协议契约本身也是被测对象）。"""
    p = subprocess.run([sys.executable, DRIVER] + args, input=(stdin_text or '').encode('utf-8'),
                       capture_output=True, timeout=120)
    out = p.stdout.decode('utf-8', 'replace')
    lines = [l for l in out.splitlines() if l.startswith('LOCALDB_JSON: ')]
    assert lines, '驱动未输出 LOCALDB_JSON 行；stdout 尾部：%r stderr：%r' % (out[-300:], p.stderr.decode('utf-8', 'replace')[-300:])
    assert p.returncode == 0, '驱动退出码须为 0（协议约定），实际 %d' % p.returncode
    return json.loads(lines[-1][len('LOCALDB_JSON: '):]), out


class FixtureMixin:
    """类级共享 fixture：真实 SQLite 两事务底座（base 事务已 checkpoint 进主库，T2/T3 只在 WAL）。"""

    @classmethod
    def build_fixture(cls):
        tmp = tempfile.TemporaryDirectory(prefix='wecom-localdb-fixture-')
        cls._fixture_tmp = tmp
        base = os.path.join(tmp.name, 'src.db')
        conn = sqlite3.connect(base)
        conn.execute('PRAGMA page_size=4096')
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE t(id INTEGER PRIMARY KEY AUTOINCREMENT, txt TEXT)')
        conn.executemany('INSERT INTO t(txt) VALUES (?)', [(r,) for r in BASE_ROWS])
        conn.commit()
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        with open(base, 'rb') as f:
            cls.db_plain = f.read()  # 主库快照（只含 base 事务；与生产"打开连接期间拷主库"同构）
        conn.executemany('INSERT INTO t(txt) VALUES (?)', [(r,) for r in T2_ROWS])
        conn.commit()
        conn.executemany('INSERT INTO t(txt) VALUES (?)', [(r,) for r in T3_ROWS])
        conn.commit()
        with open(base + '-wal', 'rb') as f:
            wal_plain = f.read()  # WAL 快照（T2+T3 帧，真实 SQLite 生成）
        conn.close()
        frames = walgen.parse_plain_frames(wal_plain)
        first_commit = next(i for i, f in enumerate(frames) if f['dbsize'] > 0)
        cls.t2 = frames[:first_commit + 1]
        cls.t3 = frames[first_commit + 1:]
        assert cls.t2 and cls.t2[-1]['dbsize'] > 0, 'fixture 前提：T2 以提交帧收尾'
        assert cls.t3 and cls.t3[-1]['dbsize'] > 0, 'fixture 前提：T3 以提交帧收尾'
        cls.enc_db = walgen.build_encrypted_db(cls.db_plain, MKEY)

    @classmethod
    def teardown_fixture(cls):
        cls._fixture_tmp.cleanup()

    def scenario_dir(self, name):
        """每场景独立目录：写快照文件 + 解密输出目录（先保证不存在以断言"无半解密产物"）。"""
        d = os.path.join(self._fixture_tmp.name, name)
        os.makedirs(d, exist_ok=True)
        return d

    def write_scenario(self, name, wal_bytes):
        d = self.scenario_dir(name)
        db_path = os.path.join(d, 'snapshot.db')
        wal_path = os.path.join(d, 'snapshot.db-wal')
        with open(db_path, 'wb') as f:
            f.write(self.enc_db)
        with open(wal_path, 'wb') as f:
            f.write(wal_bytes)
        return db_path, wal_path

    def write_db_only(self, name):
        d = self.scenario_dir(name)
        db_path = os.path.join(d, 'snapshot.db')
        with open(db_path, 'wb') as f:
            f.write(self.enc_db)
        return db_path

    def query_rows(self, db_path):
        conn = sqlite3.connect(db_path)
        try:
            return [r[0] for r in conn.execute('SELECT txt FROM t ORDER BY id').fetchall()]
        finally:
            conn.close()


class WalCksumPureTest(unittest.TestCase):
    """纯函数单测：SQLite 标准 WAL 校验和（字节序分支）。"""

    def test_cksum_known_vector_endianness(self):
        data = b'\x01\x00\x00\x00\x02\x00\x00\x00'  # LE 解读 (1,2)；BE 解读 (0x01000000, 0x02000000)
        # 手算：s0 = 0 + a + 0 = a；s1 = 0 + b + s0
        self.assertEqual(drv.wal_cksum(data, 0, 0, big_endian=False), (1, 3))
        self.assertEqual(drv.wal_cksum(data, 0, 0, big_endian=True), (0x01000000, 0x03000000))
        # 链式推进：下一组在 (1,3) 上续链（手算 LE：s0 = 1+1+3=5；s1 = 3+2+5=10）
        self.assertEqual(drv.wal_cksum(data, 1, 3, big_endian=False), (5, 10))
        # 非 8 倍数尾部截齐（7 字节 = 只算前一组？前 8 字节不存在 → 0 组）
        self.assertEqual(drv.wal_cksum(b'\x01\x00\x00\x00\x02\x00\x00', 0, 0, big_endian=False), (0, 0))


class SnapshotVerifyTest(FixtureMixin, unittest.TestCase):
    """snapshot-verify 模式（子进程端到端）：场景表逐项断言判定结果。"""

    @classmethod
    def setUpClass(cls):
        cls.build_fixture()

    @classmethod
    def tearDownClass(cls):
        cls.teardown_fixture()

    def normal_bytes(self, with_stale=True):
        wal = walgen.build_wal(self.t2 + self.t3, mkey=MKEY)
        if with_stale:
            # 旧代尾帧（旧 salt = 新 salt − 1，P0 实测形态）：用 T2 页图构造旧代独立链条
            wal = walgen.append_stale_tail(wal, self.t2, 0x11111111, 0x22222222, MKEY)
        return wal

    def test_normal_with_stale_tail(self):
        """正常场景：有效前缀 = 全部新代帧，旧代尾帧自然截断（不是错误）。"""
        db_path, wal_path = self.write_scenario('verify-normal', self.normal_bytes())
        payload, _ = run_driver(['snapshot-verify', wal_path])
        expect_total = len(self.t2) + len(self.t3) + len(self.t2)  # 新代 + 2 个旧代帧
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['wal_magic_ok'])
        self.assertEqual(payload['page_size'], 4096)
        self.assertEqual(payload['total_frames'], expect_total)
        self.assertEqual(payload['valid_prefix_frames'], len(self.t2) + len(self.t3))
        self.assertEqual(payload['commit_frames'], 2)  # T2、T3 各一个提交帧
        self.assertEqual(payload['stale_tail_frames'], len(self.t2))

    def test_bad_checksum_chain(self):
        """坏校验和链：首个断链帧 = 有效前缀终点，其后全部不可信。"""
        wal = walgen.corrupt_frame_cksum(self.normal_bytes(), len(self.t2))  # 打断 T3 首帧
        db_path, wal_path = self.write_scenario('verify-badchain', wal)
        payload, _ = run_driver(['snapshot-verify', wal_path])
        self.assertTrue(payload['ok'])  # 断链是自然截断语义，不是判定失败
        self.assertEqual(payload['valid_prefix_frames'], len(self.t2))
        self.assertEqual(payload['commit_frames'], 1)
        self.assertEqual(payload['stale_tail_frames'], len(self.t3) + len(self.t2))

    def test_truncated_tail_frame_not_counted(self):
        """截断（撕裂尾帧）：不完整残段不成帧、不计入 total，完整帧前缀照常判定。"""
        wal = walgen.truncate_mid_frame(walgen.build_wal(self.t2 + self.t3, mkey=MKEY))
        db_path, wal_path = self.write_scenario('verify-truncated', wal)
        payload, _ = run_driver(['snapshot-verify', wal_path])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['total_frames'], len(self.t2) + len(self.t3) - 1)
        self.assertEqual(payload['valid_prefix_frames'], len(self.t2) + len(self.t3) - 1)
        self.assertEqual(payload['stale_tail_frames'], 0)

    def test_uncommitted_tail(self):
        """未提交尾帧：T3 帧全部置 dbsize=0（链仍完整）→ 前缀含它但提交边界在 T2。"""
        wal = walgen.with_uncommitted_tail(self.t2, self.t3, MKEY)
        db_path, wal_path = self.write_scenario('verify-uncommitted', wal)
        payload, _ = run_driver(['snapshot-verify', wal_path])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['valid_prefix_frames'], len(self.t2) + len(self.t3))
        self.assertEqual(payload['commit_frames'], 1)  # 只有 T2 提交
        self.assertEqual(payload['stale_tail_frames'], 0)

    def test_bad_magic_rejected(self):
        """头部魔数非法 → ok:false（wal_magic_ok=false）。"""
        wal = walgen.build_wal(self.t2, mkey=MKEY, magic=0xDEADBEEF)
        db_path, wal_path = self.write_scenario('verify-badmagic', wal)
        payload, _ = run_driver(['snapshot-verify', wal_path])
        self.assertFalse(payload['ok'])
        self.assertFalse(payload['wal_magic_ok'])

    def test_partial_header_rejected(self):
        """头不完整（3B，<32B）→ ok:false。"""
        db_path, wal_path = self.write_scenario('verify-partial', b'\x37\x7f\x06')
        payload, _ = run_driver(['snapshot-verify', wal_path])
        self.assertFalse(payload['ok'])

    def test_empty_wal_ok_zero_frames(self):
        """空 WAL（0 字节）→ 合法空（SQLite 语义），0 帧。"""
        db_path, wal_path = self.write_scenario('verify-empty', b'')
        payload, _ = run_driver(['snapshot-verify', wal_path])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['total_frames'], 0)
        self.assertFalse(payload['wal_magic_ok'])  # 无头可验（诊断字段，非错误）

    def test_missing_file(self):
        """WAL 文件不存在 → 结构化错误、退出码 0。"""
        payload, _ = run_driver(['snapshot-verify', os.path.join(self.scenario_dir('verify-missing'), 'nope-wal')])
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'WAL_UNREADABLE')

    def test_bad_args(self):
        """用法错误 → BAD_ARGS JSON、退出码 0（协议契约）。"""
        payload, _ = run_driver([])
        self.assertEqual(payload['error'], 'BAD_ARGS')


class VerifyDirectTest(FixtureMixin, unittest.TestCase):
    """进程内纯函数直测（不落盘）：verify_wal_frames 可导入复用（decrypt 内联同一实现）。"""

    @classmethod
    def setUpClass(cls):
        cls.build_fixture()

    @classmethod
    def tearDownClass(cls):
        cls.teardown_fixture()

    def test_verify_direct_normal(self):
        wal = walgen.build_wal(self.t2 + self.t3, mkey=MKEY)
        vr = drv.verify_wal_frames(wal)
        self.assertTrue(vr['ok'])
        self.assertEqual(vr['valid_prefix_frames'], len(self.t2) + len(self.t3))
        self.assertEqual(vr['commit_frames'], 2)
        # 大端 magic 形态（0x377f0683）：同帧数据、字节序分支生效，判定结果一致
        wal_be = walgen.build_wal(self.t2 + self.t3, mkey=MKEY, magic=0x377F0683)
        vr_be = drv.verify_wal_frames(wal_be)
        self.assertTrue(vr_be['ok'])
        self.assertEqual(vr_be['valid_prefix_frames'], len(self.t2) + len(self.t3))


class DecryptTest(FixtureMixin, unittest.TestCase):
    """decrypt 模式（子进程端到端）：验证→解密→WAL 明文重建→integrity；失败场景无半解密产物。"""

    @classmethod
    def setUpClass(cls):
        cls.build_fixture()

    @classmethod
    def tearDownClass(cls):
        cls.teardown_fixture()

    def out_dir(self, name):
        d = os.path.join(self._fixture_tmp.name, name + '-out')
        self.assertFalse(os.path.exists(d))
        return d

    def run_decrypt(self, name, wal_arg, key=MKEY):
        db_path = self.write_db_only(name)
        outdir = self.out_dir(name)
        payload, out = run_driver(['decrypt', db_path, wal_arg, outdir], stdin_text=key.hex())
        return payload, out

    def expect_rows(self, payload, rows, expect_wal=True):
        self.assertTrue(payload['ok'], payload.get('detail', ''))
        self.assertEqual(payload['integrity'], 'ok')
        self.assertEqual(payload['pages'], len(self.db_plain) // drv.PAGE)
        self.assertTrue(os.path.exists(payload['out_db']))
        # 行集断言同时证明 WAL 帧确实被回放（base 之外的内容只能来自明文 WAL 回放）
        self.assertEqual(self.query_rows(payload['out_db']), rows)
        if expect_wal:
            # integrity_check 的可写连接关闭会 checkpoint 消费 -wal：out_wal 仅在仍存在时报告
            if 'out_wal' in payload:
                self.assertTrue(os.path.exists(payload['out_wal']))
        else:
            self.assertNotIn('out_wal', payload)

    def test_decrypt_normal_with_stale_tail(self):
        """正常场景（含旧代尾帧）：只回放有效前缀，旧代尾帧内容不得出现；密钥不出现在输出。"""
        wal = walgen.append_stale_tail(walgen.build_wal(self.t2 + self.t3, mkey=MKEY),
                                       self.t2, 0x11111111, 0x22222222, MKEY)
        db_path, wal_path = self.write_scenario('dec-normal', wal)
        outdir = self.out_dir('dec-normal')
        payload, out = run_driver(['decrypt', db_path, wal_path, outdir], stdin_text=MKEY.hex())
        self.expect_rows(payload, BASE_ROWS + T2_ROWS + T3_ROWS)
        self.assertEqual(payload['wal_frames'], len(self.t2) + len(self.t3))
        self.assertEqual(payload['stale_tail_frames'], len(self.t2))
        # 密钥材料不进 stdout/JSON（§8）；T2 首帧明文页内容完整性由行集断言覆盖
        self.assertNotIn(MKEY.hex(), out)

    def test_decrypt_uncommitted_tail_not_replayed(self):
        """未提交尾帧：T3 帧（真实页图）虽在有效前缀内、但越过提交边界 → 不得出现在结果。"""
        wal = walgen.with_uncommitted_tail(self.t2, self.t3, MKEY)
        db_path, wal_path = self.write_scenario('dec-uncommitted', wal)
        outdir = self.out_dir('dec-uncommitted')
        payload, _ = run_driver(['decrypt', db_path, wal_path, outdir], stdin_text=MKEY.hex())
        self.expect_rows(payload, BASE_ROWS + T2_ROWS)
        self.assertEqual(payload['wal_frames'], len(self.t2))

    def test_decrypt_bad_chain_replays_prefix_only(self):
        """坏校验和链：只回放断点前最后提交边界（T2），断链后内容不得出现。"""
        wal = walgen.corrupt_frame_cksum(walgen.build_wal(self.t2 + self.t3, mkey=MKEY), len(self.t2))
        db_path, wal_path = self.write_scenario('dec-badchain', wal)
        outdir = self.out_dir('dec-badchain')
        payload, _ = run_driver(['decrypt', db_path, wal_path, outdir], stdin_text=MKEY.hex())
        self.expect_rows(payload, BASE_ROWS + T2_ROWS)
        self.assertEqual(payload['wal_frames'], len(self.t2))
        self.assertEqual(payload['stale_tail_frames'], len(self.t3))

    def test_decrypt_truncated_torn_commit_not_applied(self):
        """截断撕裂提交帧：T3 提交帧不完整 → 该事务不生效，库仍自洽（integrity ok）。"""
        wal = walgen.truncate_mid_frame(walgen.build_wal(self.t2 + self.t3, mkey=MKEY))
        db_path, wal_path = self.write_scenario('dec-truncated', wal)
        outdir = self.out_dir('dec-truncated')
        payload, _ = run_driver(['decrypt', db_path, wal_path, outdir], stdin_text=MKEY.hex())
        self.expect_rows(payload, BASE_ROWS + T2_ROWS)

    def test_decrypt_no_wal(self):
        """无 WAL（'-' 占位）：只有主库，base 事务内容，无 out_wal。"""
        payload, _ = self.run_decrypt('dec-nowal', '-')
        self.expect_rows(payload, BASE_ROWS, expect_wal=False)
        self.assertEqual(payload['wal_frames'], 0)

    def test_decrypt_empty_wal(self):
        """空 WAL（0 字节）：合法空回放，base 事务内容。"""
        db_path, wal_path = self.write_scenario('dec-empty', b'')
        outdir = self.out_dir('dec-empty')
        payload, _ = run_driver(['decrypt', db_path, wal_path, outdir], stdin_text=MKEY.hex())
        self.expect_rows(payload, BASE_ROWS)
        self.assertEqual(payload['wal_frames'], 0)
        self.assertEqual(payload['stale_tail_frames'], 0)

    def test_decrypt_partial_header_unstable_no_output(self):
        """头不完整 → SNAPSHOT_UNSTABLE，不产生任何输出文件（不返回半解密结果）。"""
        db_path, wal_path = self.write_scenario('dec-partial', b'\x37\x7f\x06')
        outdir = self.out_dir('dec-partial')
        payload, _ = run_driver(['decrypt', db_path, wal_path, outdir], stdin_text=MKEY.hex())
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'SNAPSHOT_UNSTABLE')
        self.assertEqual(payload['stage'], 'wal-verify')
        self.assertFalse(os.path.exists(outdir))

    def test_decrypt_bad_magic_unstable_no_output(self):
        """坏魔数 WAL → SNAPSHOT_UNSTABLE，无输出文件。"""
        wal = walgen.build_wal(self.t2, mkey=MKEY, magic=0xDEADBEEF)
        db_path, wal_path = self.write_scenario('dec-badmagic', wal)
        outdir = self.out_dir('dec-badmagic')
        payload, _ = run_driver(['decrypt', db_path, wal_path, outdir], stdin_text=MKEY.hex())
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'SNAPSHOT_UNSTABLE')
        self.assertFalse(os.path.exists(outdir))

    def test_decrypt_wrong_key_no_output(self):
        """错钥：页 1 先知校验快速失败 → KEY_VERIFY_FAILED，无输出文件。"""
        payload, _ = self.run_decrypt('dec-wrongkey', '-', key=bytes(range(16, 32)))
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'KEY_VERIFY_FAILED')
        self.assertEqual(payload['stage'], 'page-1')
        self.assertFalse(os.path.exists(os.path.join(self._fixture_tmp.name, 'dec-wrongkey-out')))

    def test_decrypt_bad_key_format(self):
        """stdin 密钥非法（非 hex/长度不符）→ BAD_KEY，不触碰文件。"""
        db_path = self.write_db_only('dec-badkey')
        outdir = os.path.join(self._fixture_tmp.name, 'dec-badkey-out')
        payload, _ = run_driver(['decrypt', db_path, '-', outdir], stdin_text='zz-not-hex')
        self.assertEqual(payload['error'], 'BAD_KEY')
        payload, _ = run_driver(['decrypt', db_path, '-', outdir], stdin_text='aabb')
        self.assertEqual(payload['error'], 'BAD_KEY')

    def test_decrypt_missing_db(self):
        """主库快照缺失 → 结构化错误、退出码 0。"""
        outdir = os.path.join(self._fixture_tmp.name, 'dec-missingdb-out')
        payload, _ = run_driver(['decrypt', os.path.join(self.scenario_dir('dec-missingdb'), 'nope.db'), '-', outdir],
                                stdin_text=MKEY.hex())
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'DB_UNREADABLE')


if __name__ == '__main__':
    unittest.main()
