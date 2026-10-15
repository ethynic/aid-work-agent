# tests/py/ref_encryptor.py — wxaes128 非遗留页加密【独立参考实现】（加密侧，任务 1.5）
#
# 独立参考实现来源 = sqlite3mc cipher_wxaes128.c 规格语义，按规格独立誊写：
#   - 页密钥派生：pagekey = MD5(mkey[16] ‖ pgno(u32 LE) ‖ "sAlT")（wx 盐 0x73 0x41 0x6C 0x54）；
#   - IV 派生：IV = MD5(LCG(pgno+1) 展开 16B)，LCG 为 MODMULT(52774, 40692, 3791, 2147483399)
#     的 Lehmer 变体（z = 40692*(z - 52774*(z//52774)) - 3791*(z//52774)，负则 +2147483399），
#     4 轮各产一个 u32 小端拼成 16B 初始块后整体过 MD5；
#   - 分组密码：AES-128-CBC（页 1 按两段独立 CBC，见 encrypt_page 注释）；
#   - 页 1 非遗留搬移布局：第二段（明文 [16:4096]）首密文块的 [16:24] 段被搬移——落盘时
#     其前 8 字节放 [8:16]、后 8 字节放 [24:32]，[16:24] 改存明文页头副本。
#
# 【独立性约束】本文件不 import drivers/py/wecom_localdb.py 的任何函数——算法按规格独立誊写，
# 与生产驱动解密路径（wecom_localdb.decrypt_page）互为对照：驱动能解开本实现产出的固定向量，
# 证明的是「解密实现与独立规格一致」，而非同一份代码的自洽往返（设计 §7，评审要求的
# 独立来源固定测试向量）。固定向量由 gen_vectors.py 调用本实现生成入库（fixtures/）。
#
# 依赖：标准库 hashlib/struct + pycryptodome（仅测试环境使用）。
import hashlib
import struct

from Crypto.Cipher import AES

WXA_SALT = b"sAlT"  # 0x73 0x41 0x6C 0x54（cipher_wxaes128 页密钥派生盐）
PAGE_SIZE = 4096
FIRST_BLOCK_SIZE = 16


def ref_page_key(mkey, pgno):
    """页密钥 = MD5(mkey ‖ pgno(u32 小端) ‖ "sAlT")。"""
    return hashlib.md5(mkey + struct.pack("<I", pgno) + WXA_SALT).digest()


def ref_iv(pgno):
    """IV = MD5(LCG(pgno+1) 展开 16B)：MODMULT Lehmer 变体 4 轮、每轮产 u32 小端。"""
    z = (pgno + 1) & 0xFFFFFFFF
    expanded = bytearray(16)
    for slot in range(4):
        quotient = z // 52774
        z = 40692 * (z - 52774 * quotient) - 3791 * quotient
        if z < 0:
            z += 2147483399
        expanded[slot * 4:slot * 4 + 4] = struct.pack("<I", z & 0xFFFFFFFF)
    return hashlib.md5(bytes(expanded)).digest()


def _cbc(key, iv, data):
    """AES-128-CBC 单段加密（data 须为 16 的整倍数，调用方保证）。"""
    return AES.new(key, AES.MODE_CBC, iv).encrypt(data)


def ref_encrypt_page(mkey, pgno, plain):
    """加密单页（明文 PAGE_SIZE 字节 → 密文 PAGE_SIZE 字节）。

    普通页：整页一段 CBC。
    页 1（非遗留搬移布局）：明文按 [0:16] / [16:4096] 两段各自以 IV 起 CBC（与解密侧按
    段解密同构——第二段不链第一段密文）；第二段首密文块（加密自明文 [16:32]）被搬移：
    前 8 字节落盘 [8:16]、后 8 字节落盘 [24:32]；[16:24] 改存【明文页头副本】；
    [0:16] 存第一段密文（其中 [8:16] 被搬移覆盖——该半块明文恒为页头常量，解密侧
    直接以 "SQLite format 3\\0" 写回，不需要恢复）。"""
    if len(plain) != PAGE_SIZE:
        raise ValueError("页大小须为 %d 字节，实际 %d" % (PAGE_SIZE, len(plain)))
    key = ref_page_key(mkey, pgno)
    iv = ref_iv(pgno)
    if pgno == 1:
        enc_first = _cbc(key, iv, plain[0:FIRST_BLOCK_SIZE])
        enc_rest = _cbc(key, iv, plain[FIRST_BLOCK_SIZE:PAGE_SIZE])
        out = bytearray(PAGE_SIZE)
        out[0:FIRST_BLOCK_SIZE] = enc_first
        out[8:16] = enc_rest[0:8]          # 搬移：第二段首密文块前半
        out[16:24] = plain[16:24]          # 明文页头副本
        out[24:PAGE_SIZE] = enc_rest[8:PAGE_SIZE - FIRST_BLOCK_SIZE]
        return bytes(out)
    return _cbc(key, iv, plain)
