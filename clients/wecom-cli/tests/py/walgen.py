# tests/py/walgen.py — 合成 WAL / 加密库构造器（冻结点 1 专项验证 fixture，测试专用）
#
# 用途：给定明文页与 mkey 生成合法加密 WAL（正确校验和链、提交帧），并可注入缺陷：
# 坏校验和、旧代尾帧、截断、未提交尾帧、头 salt 变更（reset 形态）、坏魔数。
# 页加密/校验和算法【复用生产驱动 wecom_localdb 的纯函数】（page_key/gen_iv/wal_cksum），
# 保证 fixture 与被测实现同源——加密自往返只证自洽，兼容性结论仍依赖独立来源向量
# （设计 §7，属任务 1.5 范围），本文件只服务 §3.3 判定规则专项验证。
# 依赖 pycryptodome（venv python 运行）；仅测试环境导入。
import struct

import wecom_localdb as drv

DEFAULT_MAGIC = 0x377F0682  # 小端校验和形态（真机 P0 样本验证过的分支）
DEFAULT_VERSION = 3007000


def encrypt_page(mkey, pgno, plain):
    """decrypt_page 的逆变换（构造加密库/加密 WAL 帧页用；布局见驱动 decrypt_page 注释）。"""
    out = bytearray(drv.PAGE)
    if pgno == 1:
        region = drv._cipher(mkey, 1).encrypt(bytes(plain[16:drv.PAGE]))
        out[0:16] = drv._cipher(mkey, 1).encrypt(bytes(plain[0:16]))
        out[8:16] = region[0:8]          # 页头密文搬移到 [8:16]
        out[16:24] = plain[16:24]        # 明文页头副本落盘 [16:24]
        out[24:drv.PAGE] = region[8:drv.PAGE]
    else:
        out[0:drv.PAGE] = drv._cipher(mkey, pgno).encrypt(bytes(plain))
    return bytes(out)


def build_wal(frames, salt1=0x11111111, salt2=0x22222222, ckptseq=0,
              magic=DEFAULT_MAGIC, version=DEFAULT_VERSION, mkey=b'\x00' * 16):
    """由帧规格生成完整合法加密 WAL（头校验和 + 帧累计校验和链，链对【密文】页计算）。

    frames: [{'pgno': int, 'plain': 4096B 明文页, 'dbsize': int}]（dbsize>0 = 提交帧）。"""
    header = struct.pack('>IIII', magic, version, drv.PAGE, ckptseq) + struct.pack('>II', salt1, salt2)
    big = magic == drv.WAL_MAGICS[1]
    s0, s1 = drv.wal_cksum(header, 0, 0, big)
    out = bytearray(header + struct.pack('>II', s0, s1))
    for f in frames:
        fh = struct.pack('>II', f['pgno'], f['dbsize']) + struct.pack('>II', salt1, salt2)
        content = encrypt_page(mkey, f['pgno'], f['plain'])
        s0, s1 = drv.wal_cksum(fh[0:8] + content, s0, s1, big)
        out += fh + struct.pack('>II', s0, s1) + content
    return bytes(out)


def parse_plain_frames(raw):
    """解析【明文】WAL（如真实 SQLite 生成的 wal 文件）为帧规格列表（plain=明文页）。"""
    frames = []
    off = drv.WAL_HEADER_SIZE
    while off + drv.FRAME_HEADER_SIZE + drv.PAGE <= len(raw):
        fh = raw[off:off + drv.FRAME_HEADER_SIZE]
        pgno, dbsize = struct.unpack('>II', fh[0:8])
        frames.append({
            'pgno': pgno,
            'dbsize': dbsize,
            'plain': raw[off + drv.FRAME_HEADER_SIZE:off + drv.FRAME_HEADER_SIZE + drv.PAGE],
            'salt': fh[8:16],
        })
        off += drv.FRAME_HEADER_SIZE + drv.PAGE
    return frames


def corrupt_frame_cksum(wal, frame_index):
    """翻转第 frame_index 帧（0 起）存储校验和的 1 字节 → 该帧校验和断链。"""
    off = drv.WAL_HEADER_SIZE + frame_index * (drv.FRAME_HEADER_SIZE + drv.PAGE)
    out = bytearray(wal)
    out[off + 16] ^= 0xFF
    return bytes(out)


def truncate_mid_frame(wal, keep_tail_bytes=100):
    """截掉最后一帧的一部分（保留尾部 keep_tail_bytes 字节残段）→ 撕裂尾帧形态。"""
    frame_size = drv.FRAME_HEADER_SIZE + drv.PAGE
    return wal[:len(wal) - frame_size + keep_tail_bytes]


def with_uncommitted_tail(prefix_frames, tail_frames, mkey, **kw):
    """prefix 段保持原样（含其提交帧），tail 段全部置 dbsize=0 后按规格重建合法链
    → 未提交尾帧形态。传入帧通常来自真实 SQLite WAL（页内容携带真实变更，
    若被误回放即可观测），链由 build_wal 按改后的帧头重算、仍然完整。"""
    mutated = [dict(f, dbsize=0) for f in tail_frames]
    return build_wal(list(prefix_frames) + mutated, mkey=mkey, **kw)


def append_stale_tail(wal, frames, salt1, salt2, mkey, delta=1):
    """追加旧代尾帧：按旧代头（salt−delta，对应 checkpoint+reset 后的上一代）独立起链生成
    完整旧代 WAL，只把其帧拼到当前代尾部（P0 实测形态：旧 salt = 新 salt − 1）。
    对有效前缀判定而言，首个旧代帧 salt 不匹配即截断——旧帧自身链条合法性无关判定。"""
    old_gen = build_wal(frames, salt1=(salt1 - delta) & 0xFFFFFFFF, salt2=(salt2 - delta) & 0xFFFFFFFF, mkey=mkey)
    return wal + old_gen[drv.WAL_HEADER_SIZE:]


def build_encrypted_db(db_plain, mkey):
    """明文库整库逐页加密（构造加密快照 db；页号从 1 起，不足整页的尾部按整页补齐截断在调用方）。"""
    out = bytearray()
    n = len(db_plain) // drv.PAGE
    for pgno in range(1, n + 1):
        out += encrypt_page(mkey, pgno, db_plain[(pgno - 1) * drv.PAGE: pgno * drv.PAGE])
    return bytes(out)
