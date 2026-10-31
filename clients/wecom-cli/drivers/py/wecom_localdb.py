# drivers/py/wecom_localdb.py — 本地库驱动：可信快照判定 + 解密（设计 §3.3，任务 1.4）
# 对齐 chat_ocr.py 单脚本多模式先例：argv 模式分发、stdout 最后一行单行 JSON、
# \u 转义中文（ensure_ascii，规避控制台编码）、任何错误（含用法/环境缺失）也输出 JSON 且退出码 0。
#
# 模式（argv 分发）：
#   snapshot-verify <wal快照路径>
#       对【密文】快照 WAL 做设计 §3.3 步骤 4-6 判定（有效前缀规则与 SQLite 自身读 WAL 语义一致）：
#       ① 头判定：magic ∈ {0x377f0682, 0x377f0683}、页大小合法（512-65536 的 2 的幂）、头校验和；
#       ② 从 WAL 头校验和起链，对「帧头前 8B + 帧页密文」复算累计校验和（SQLite 标准算法，
#          字节序由 magic 决定：…82=小端、…83=大端；存储值一律大端），且帧 salt == 头 salt；
#       ③ 首个链断裂或 salt 不匹配的帧 = 有效前缀终点——WAL 重用保留的旧代尾帧按此自然截断，
#          属正常现象不是错误、不据此判 reset（评审 v3 #24：reset 判定只在 TS 捕获侧 H0/H1 头比较）；
#       ④ 有效前缀内统计提交帧（帧头 bytes[4:8] dbsize>0）。
#       → {ok, total_frames, valid_prefix_frames, commit_frames, stale_tail_frames,
#          wal_magic_ok, page_size, detail}
#   decrypt <db快照路径> <wal快照路径|-> <输出目录>
#       先内联执行 snapshot-verify 语义（不可信 → {ok:false,error:'SNAPSHOT_UNSTABLE'}，【不产生
#       任何输出文件】——失败场景不返回半解密结果，§3.3 步骤 8）；再逐页解密主库（页 1 校验 =
#       密钥先知快速失败）→ 按「有效前缀 + 提交边界」重建明文 WAL：帧页按帧头 pgno 解密，明文
#       校验和【重算】（原校验和是对密文的；实验 decrypt_wxaes128.decrypt_wal 不重算的逻辑不可
#       照搬，正确算法参考 decrypt_full）；最后 sqlite3 PRAGMA integrity_check 验收（可写打开让
#       WAL 回放，只读打开会跳过 WAL——decrypt_full 注释）。密钥从 stdin 读 hex（评审定稿：
#       不进 argv/日志/JSON 输出）。
#       → {ok, pages, wal_frames, stale_tail_frames, integrity, out_db, out_wal?}
#       wal_frames = 有效前缀内、最后一个提交帧边界内的帧数（实际重建写入数）；有效前缀内提交
#       边界之后未提交的帧不入库（snapshot-verify 的 valid/commit 计数可看出该差值）。
#       integrity_check 的可写连接关闭时 checkpoint：回放内容落库、-wal 被消费删除，成功后
#       out_db 自包含——out_wal 仅在文件仍存在时才出现在输出（实践中通常已被消费）。
#   keyscan --pids <pid[,pid…]> --db <页1源库副本> [--max-region-bytes N] [--aligned-first|--full]
#       C6 内存密钥提取（Windows-only，只读观察：OpenProcess(PROCESS_VM_READ|QUERY_INFORMATION)
#       + ReadProcessMemory，无注入/无写/无提权）。从 --db 读页 1 构造验证先知（对齐实验
#       find_key6.py：C0=[8:16]‖[24:32]、TARGET=[16:24]、aes128 页密钥=MD5(k‖1‖sAlT)、
#       aes256=SHA256、IV=gen_iv(1)——复用本文件既有纯函数，单一实现）。逐 pid 扫描
#       MEM_COMMIT/MEM_PRIVATE/PAGE_READWRITE 且 ≤max-region-bytes 的区域；先 16 字节对齐
#       偏移预筛（假设：EVP 上下文堆分配 16 对齐，P1a 真机验证点 §9.5），miss 再同进程
#       全量步长 1（正确性不依赖对齐假设）；每 16/32B 窗口零字节计数预筛沿用实验（>半数
#       零字节跳过）。命中即返回（先到先得，多 pid 顺序尝试）。
#       → {ok:true, kind:'aes128'|'aes256', key_hex, pid, scanned_bytes, elapsed_ms}
#         或 {ok:false, error:'KEY_NOT_FOUND'|'OPEN_FAILED'|'WINDOWS_REQUIRED', pids_tried?, elapsed_ms}
#       key_hex 只出现在该 JSON（emit 豁免该字段的 32-hex 脱敏，供 TS 内存接收），绝不出现
#       在任何日志/异常消息；其余字段仍逐字段脱敏兜底。
#   query --workdir <dir> --task <t> [参数]
#       C8 读库查询（任务 1.8）：对 workdir 内【已解密明文库】做只读 SQL（uri ro，不写不改）。
#       除 groups 外 message.db.dec.db 必需（兼容 decrypt 自然产物命名 message.dec.db）；user.db.dec.db /
#       session.db.dec.db 用于 resolve；context 还需要 user 库确认本人 ID 映射。
#       任务：
#         groups [--name <关键词>] [--limit N]
#           只读 session 库 conversation_table 的 R: 群，群名字面子串过滤、按会话 ID
#           去重后按名称/ID 稳定排序；包含无消息群，不依赖 message 库。返回 groups /
#           count / has_more；缺少群名来源明确失败，不冒充空列表。
#         history --conv <R:群id> [--limit N] [--since <秒>] [--until <秒>]
#           时间边界均包含；按 send_time DESC,message_id DESC 选最近 N 条，再按时间
#           升序输出。仅接纳北京时间四位年份可展示的非负整数秒时间，invalid_time_count 为当前群异常时间记录
#           总量，has_more 明示范围内仍有消息。独立于 messages 的入库水位与锚点。
#           type0 优先已知直接文字包装，否则与 type2 一同按已实证有序文字/提及片段恢复，
#           附 mentions/mentions_complete；未知或
#           损坏片段明确占位并标 trusted=false。sender_name/source 按 user_table.id
#           精确补名，提及优先保留消息内历史名，缺名才查用户库；naming_incomplete
#           明示本批存在未确定姓名。无独立引用身份实证，不从前置引用文字猜测作者。
#         messages --conv <id> [--after-id N] [--anchor <摘要>] [--limit N] [--since <秒>]
#           未传 after-id 取最近 limit 条；显式 after-id（含 0）按 message_id 升序取
#           大于该水位的 limit 条（缺省 100）；since 为 send_time（秒）可选过滤。
#           每行输出 message_id/server_id/sequence/sender_id/conversation_id/content_type/
#           send_time/flag/text/trusted——text/trusted 由 extract_content 就地提取（非文本
#           text=null）。附 next_cursor_id = 本批次实际返回行的最大 message_id（空批次
#           = after-id 原值）——游标只认返回集，展示排序不推进游标（评审 v5 规则）。
#         conversations
#           全表按 conversation_id 聚合 → [{conversation_id, count, last_send_time,
#           last_message_id}] 按 last_message_id 降序。last_send_time 取水位行（最大
#           message_id）那一行的 send_time——迟到同步下与 MAX(send_time) 可能不同，语义
#           按"水位末端那条"（与 cursor 同一基元）。
#         last-per-conv [--exclude-types <t[,t…]>] [--with-names --account <id>]
#           每会话输出 last_message（无条件最后一条）与 last_participating（跳过
#           CONFIRMED_SYSTEM_TYPES 排除表；--exclude-types 为【追加】语义，不缩减基础表）。
#           供待回复判定（TS 侧按 sender_id 判断方向）；排除表只影响本任务，不影响
#           messages 任务返回。全是系统消息 → last_participating=null。
#           --with-names：反向取名附 name 字段（R: 群名只认 conversation_table、S: 联系人
#           名只认 user_table——与 resolve 同一命名纪律；无命中/来源缺失 → name=null），
#           供 TS 在未传 focus 的 name 过滤快照中按实际名称匹配（须与 --account 同传）。
#           第十轮评审 P2：S: 行另附 match_names=[该行所有非空名称字段]（名称/别名/备注
#           候选列，与 resolve 同一列集——备注/别名同语义参与命名），TS 过滤按 name/
#           match_names 任意一项子串命中即算命中；存在 R: 会话但 session 来源不可用 →
#           顶层 naming_incomplete:true（无 R: 会话或未要求 names 不标），TS 指定 name
#           过滤时明确失败，不把「取不到群名」冒充「无匹配」空结果。
#         context --account <目录账号>
#           user_table.id 必须唯一命中目录账号；返回字符串 self_user_id 与 schema_version。
#         resolve --name <名称> [--account <已核实本人ID>]
#           先查 session 库 conversation_table（R: 群名精确匹配，多候选全返回——name 无
#           唯一索引需防重），再查 user 库 user_table（name/别名/备注列精确匹配；列名经
#           PRAGMA table_info 运行时确认，不猜）。输出 {candidates: [{source,
#           conversation_id, name, …}], ambiguous, sources}；列名无法确认 → 该来源
#           schema-unknown。未传 account 时 user 候选 conversation_id 为 null；传入已核实
#           本人 ID 时只返回 message/session 中实际存在的 S 会话，未建会话的同名候选仍保留。
#       库缺失/SQL 错误 → {ok:false, error,…} 不抛；缺表/缺列（schema 不兼容）→ 稳定错误码
#       SCHEMA_UNSUPPORTED（评审 v6，doctor 归 compat）。本模式无密钥参与，输出不走 32-hex
#       兜底脱敏（业务文本不得被破坏，见 emit redact 参数）。
#   watch --workdir <dir> --watermarks <json文件路径> [--account <已核实本人ID>]
#         [--limit N] [--exclude-types <t[,t…]>]
#       P2 任务 3.2：watch_poll 本地库通道的多会话增量查询。watermarks JSON 形态
#       {"<conversation_id>": <last_message_id | {last_message_id, anchor}>}（TS 侧 DB
#       水位存储的导出；anchor = 水位行消息摘要，第九轮评审 P1 起非零水位必带）。每会话
#       独立 limit，只取 message_id > 水位、按入库序（message_id 升序）；非零水位先做
#       锚点有效性校验（行缺失/摘要不符 → 水位失效按 0 全量重报并标注 watermark_reset）：
#       → {ok, task:'watch', per_conversation: [{conversation_id, messages:[行投影同
#         messages 任务，含 text/trusted], last_message_id(该会话当前最大), last_sender_state:
#         'self'|'peer'|null, has_more, watermark_reset?, next_anchor}], next_watermarks:{...}}
#       last_sender_state 按该会话最后一条可参与回复判定消息（排除表 CONFIRMED_SYSTEM_TYPES
#       + --exclude-types 追加，尾部系统消息不顶掉判定）的 sender 相对 --account 判向；
#       会话全是系统类 / 未传 --account → null。has_more=true 表示该会话水位之后仍有未返回
#       消息（本轮被 limit 截断，下轮继续）。next_watermarks 只包含返回过消息的会话、值 =
#       已返回消息的最大 message_id（水位只推进到已返回记录；不读/无新消息的会话不出现，
#       水位不推进）。会话在表中无任何行（账号切换残留水位等）→ 不出现在输出中。
#       本模式无密钥参与，输出不走 32-hex 兜底脱敏（同 query）。
#   active-account <操作主窗口PID>
#       只查询该 WXWork 进程的磁盘句柄路径；唯一普通本地 Data/message.db → {ok,pid,message_db}。
#       无匹配/多账号/枚举不完整均失败，不读进程内存或数据库内容。
#
# 依赖：标准库 hashlib/struct/sqlite3/json/os/re/sys/time/urllib.parse；pycryptodome 仅
# decrypt/keyscan 用到（venv 依赖，延迟导入——snapshot-verify/query 模式零第三方依赖）；
# ctypes 仅 keyscan 的 Windows 分支延迟导入（非 win32 直接 WINDOWS_REQUIRED）。算法来源：
# 实验 decrypt_wxaes128.py（本机 5.0.11.6029 全链路验证通过）；判定规则以设计 §3.3 为准。
# v1 快照/库整读入内存（生产 WAL checkpoint 后很小、P0 实测库+WAL 合计 1.28MB；超大文件
# 整读为已知限制）。
import hashlib
import json
import os
import re
import sqlite3
import struct
import sys
import time
import urllib.parse

SALT = bytes([0x73, 0x41, 0x6C, 0x54])  # "sAlT"（页密钥派生 MD5 输入盐）
PAGE = 4096
WAL_HEADER_SIZE = 32
FRAME_HEADER_SIZE = 24
WAL_MAGICS = (0x377F0682, 0x377F0683)  # 大端存储；…82=校验和按小端机序计算，…83=按大端

# 输出脱敏兜底（§8 redaction 层）：32-hex 样式串一律遮蔽——密钥本就不进 payload，此为防御性兜底
_HEX32_RE = re.compile(r'[0-9a-fA-F]{32}')


def emit(payload, keep_hex_fields=(), redact=True):
    r"""协议出口：stdout 最后一行 LOCALDB_JSON: {...}（单行 JSON、\u 转义中文、32-hex 脱敏）。

    keep_hex_fields：豁免整行脱敏的字段名集合（仅 keyscan 的 key_hex——密钥 hex 是该模式
    JSON 的协议载荷本身，供 TS 内存接收；豁免时其余字符串字段仍逐一脱敏兜底，密钥依旧
    不进任何日志/异常消息）。
    redact=False：整行明文输出——仅 query 模式使用（该模式全程无密钥参与，message 正文等
    业务载荷不得被 32-hex 兜底脱敏破坏；密钥防线仍在 decrypt 的 stdin 与 keyscan 的
    受控字段，不因本豁免弱化）。"""
    if not redact:
        print('LOCALDB_JSON: ' + json.dumps(payload, ensure_ascii=True))
        return
    if not keep_hex_fields:
        line = json.dumps(payload, ensure_ascii=True)
        print('LOCALDB_JSON: ' + _HEX32_RE.sub('[REDACTED-HEX]', line))
        return
    out = {}
    for k, v in payload.items():
        if isinstance(v, str) and k not in keep_hex_fields:
            v = _HEX32_RE.sub('[REDACTED-HEX]', v)
        out[k] = v
    print('LOCALDB_JSON: ' + json.dumps(out, ensure_ascii=True))


# ---------- 页加密原语（纯函数，供 decrypt 与合成 WAL fixture 复用同一实现） ----------

def page_key(mkey, pgno):
    """页密钥 = MD5(mkey[16] ‖ pgno 小端 u32 ‖ "sAlT")（实验 decrypt_wxaes128.page_key 同源）。"""
    return hashlib.md5(mkey + struct.pack('<I', pgno) + SALT).digest()


def gen_iv(seed):
    """IV = MD5(LCG(seed+1) 展开 16B)；LCG 为 wxaes128 方案绑定的 Lehmer 变体。"""
    z = (seed + 1) & 0xFFFFFFFF
    init = bytearray(16)
    for j in range(4):
        q = z // 52774
        z = 40692 * (z - 52774 * q) - 3791 * q
        if z < 0:
            z += 2147483399
        init[4 * j:4 * j + 4] = struct.pack('<I', z & 0xFFFFFFFF)
    return hashlib.md5(bytes(init)).digest()


def _cipher(mkey, pgno):
    from Crypto.Cipher import AES  # 延迟导入：snapshot-verify 模式零第三方依赖
    return AES.new(page_key(mkey, pgno), AES.MODE_CBC, gen_iv(pgno))


def decrypt_page(mkey, pgno, page):
    """解密单页（密文 4096B → 明文 4096B）；页 1 明文页头校验失败（错钥/方案不符）→ None。

    页 1 特殊布局（P0 实测）：密文页 [8:16] 存放被搬移的页头密文、[16:24] 为明文页头副本；
    解密时先把密文归位 [16:24]，解 [16:4096] 与 [0:16]，校验解密结果 [16:24] 与落盘明文页头
    一致后，[0:16] 写回 "SQLite format 3\\0"。"""
    out = bytearray(page)
    if pgno == 1:
        db_header = bytes(page[16:24])
        out[16:24] = page[8:16]  # 还原被搬移的页头密文
        out[16:PAGE] = _cipher(mkey, 1).decrypt(bytes(out[16:PAGE]))
        out[0:16] = _cipher(mkey, 1).decrypt(bytes(page[0:16]))
        if out[16:24] != db_header:
            return None  # 密钥错误或方案不符（页 1 先知校验）
        out[0:16] = b"SQLite format 3\x00"
    else:
        out[0:PAGE] = _cipher(mkey, pgno).decrypt(bytes(page[0:PAGE]))
    return bytes(out)


# ---------- WAL 校验/判定（纯函数，供 snapshot-verify、decrypt 内联与 py 单测导入） ----------

def wal_cksum(data, s0, s1, big_endian):
    """SQLite 标准 WAL 累计校验和：数据按 8 字节一组拆两个 u32（读取字节序由 magic 决定），
    s0' = s0 + x[i] + s1；s1' = s1 + x[i+1] + s0'（模 2^32）。存储值一律大端（由调用方 pack）。
    长度非 8 倍数时截齐（WAL 头 24B 参与计算、帧头前 8B + 4096 页均整倍，不会触发截断）。"""
    fmt = '>II' if big_endian else '<II'
    end = len(data) - (len(data) % 8)
    for i in range(0, end, 8):
        a, b = struct.unpack_from(fmt, data, i)
        s0 = (s0 + a + s1) & 0xFFFFFFFF
        s1 = (s1 + b + s0) & 0xFFFFFFFF
    return s0, s1


def parse_wal_header(raw):
    """WAL 头判定。返回 (info, error_detail)：
    - 0 字节 = 合法空 WAL（SQLite 语义，无帧）→ ({'empty': True}, None)；
    - <32B / 魔数非法 / 页大小非法 / 头校验和不匹配 → (None 或含 wal_magic_ok 的局部 info, 原因)；
    - 合法 → 完整 info（page_size/salt/big_endian/cksum）。"""
    if len(raw) == 0:
        return {'empty': True}, None
    if len(raw) < WAL_HEADER_SIZE:
        return None, 'WAL 头不完整（%d 字节 < 32B）' % len(raw)
    magic, _version, page_size, ckptseq = struct.unpack('>IIII', raw[0:16])
    info = {
        'empty': False,
        'wal_magic_ok': magic in WAL_MAGICS,
        'page_size': page_size,
        'ckptseq': ckptseq,
        'salt': raw[16:24],
        'big_endian': magic == WAL_MAGICS[1],
        'cksum': struct.unpack('>II', raw[24:32]),
    }
    if not info['wal_magic_ok']:
        return info, 'WAL 魔数非法：0x%08x' % magic
    if page_size < 512 or page_size > 65536 or (page_size & (page_size - 1)) != 0:
        return info, 'WAL 页大小非法：%d' % page_size
    if wal_cksum(raw[0:24], 0, 0, info['big_endian']) != info['cksum']:
        return info, 'WAL 头校验和不匹配'
    return info, None


def verify_wal_frames(raw, with_frames=False):
    """§3.3 步骤 4-6 对密文 WAL 的有效前缀判定（纯函数）。

    返回 dict：{ok, total_frames, valid_prefix_frames, commit_frames, stale_tail_frames,
    wal_magic_ok, page_size, detail}。with_frames=True 时附 '_frames'（有效前缀帧明细，
    供 decrypt 进程内重建复用；含 bytes 不可 JSON 序列化，emit 前不得带出）。
    注意：ok=True 只代表「头部合法 + 前缀判定完成」；valid_prefix_frames < total_frames
    是旧代尾帧/断链/撕裂的自然截断，不是错误（评审 v3 #24）。"""
    base = {
        'ok': False,
        'total_frames': 0,
        'valid_prefix_frames': 0,
        'commit_frames': 0,
        'stale_tail_frames': 0,
        'wal_magic_ok': False,
        'page_size': None,
        'detail': '',
    }
    info, err = parse_wal_header(raw)
    if info is not None and info.get('empty'):
        out = dict(base, ok=True, detail='空 WAL（0 字节，无帧）')
        if with_frames:
            out['_frames'] = []
        return out
    out = dict(base)
    if info is not None:
        out['wal_magic_ok'] = bool(info['wal_magic_ok'])
        out['page_size'] = info['page_size']
    if err is not None:
        out['detail'] = err
        return out

    ps = info['page_size']
    frame_size = FRAME_HEADER_SIZE + ps
    salt = info['salt']
    big = info['big_endian']
    s0, s1 = info['cksum']  # 链起点 = WAL 头校验和（覆盖头前 24B，SQLite 规范）
    # total = 文件内几何完整帧数（与 P0 表述"6 帧中 4 帧旧代"同口径）；断点之后的帧虽几何完整、
    # 但 SQLite 读 WAL 语义下不可达（首个断点即终点），一并计入 stale_tail。
    total = max(0, len(raw) - WAL_HEADER_SIZE) // frame_size
    valid = 0
    commits = 0
    frames = []
    off = WAL_HEADER_SIZE
    while off + frame_size <= len(raw):
        fh = raw[off:off + FRAME_HEADER_SIZE]
        pgno, dbsize = struct.unpack('>II', fh[0:8])
        content = raw[off + FRAME_HEADER_SIZE:off + frame_size]
        c0, c1 = wal_cksum(fh[0:8] + content, s0, s1, big)
        if fh[8:16] != salt or (c0, c1) != struct.unpack('>II', fh[16:24]):
            break  # 首个断点 = 有效前缀终点（旧代尾帧/salt 不匹配/校验和断链/撕裂帧自然截断）
        s0, s1 = c0, c1
        valid += 1
        if dbsize > 0:
            commits += 1
        frames.append({'pgno': pgno, 'dbsize': dbsize, 'content': content, 'fh': bytes(fh)})
        off += frame_size
    out.update(
        ok=True,
        total_frames=total,
        valid_prefix_frames=valid,
        commit_frames=commits,
        stale_tail_frames=total - valid,
        detail='有效前缀 %d 帧（含 %d 个提交帧），其后 %d 帧不可信（旧代尾帧/断链/撕裂，自然截断）'
               % (valid, commits, total - valid),
    )
    if with_frames:
        out['_frames'] = frames
    return out


def _verify_summary(vr):
    """verify 结果中可 JSON 序列化的公开字段（剥离 '_frames' 等内部键）。"""
    return {k: v for k, v in vr.items() if not k.startswith('_')}


def cmd_snapshot_verify(wal_path):
    """snapshot-verify 模式：读快照 WAL 文件 → 有效前缀判定 → 结果 JSON。"""
    try:
        with open(wal_path, 'rb') as f:
            raw = f.read()
    except OSError as e:
        return {'ok': False, 'error': 'WAL_UNREADABLE', 'detail': 'WAL 快照不可读：%s' % e}
    return _verify_summary(verify_wal_frames(raw))


# ---------- decrypt 模式（先验证后解密，不产生半解密产物） ----------

def cmd_decrypt(db_path, wal_path, outdir, mkey):
    """§3.3 步骤 4-8：内联验证 → 整库解密 → 有效前缀+提交边界重建明文 WAL → integrity 验收。"""
    # ① WAL 内联验证（不可信 → 不产生任何输出文件）
    raw_wal = None
    vr = None
    if wal_path is not None:
        try:
            with open(wal_path, 'rb') as f:
                raw_wal = f.read()
        except OSError as e:
            return {'ok': False, 'error': 'SNAPSHOT_UNSTABLE', 'stage': 'wal-verify',
                    'detail': 'WAL 快照不可读：%s' % e}
        vr = verify_wal_frames(raw_wal, with_frames=True)
        if not vr['ok']:
            return {'ok': False, 'error': 'SNAPSHOT_UNSTABLE', 'stage': 'wal-verify',
                    'detail': vr['detail'], 'wal': _verify_summary(vr)}
        # 算法绑定页大小（矩阵 page_size=4096）；空 WAL（page_size=None）放行走空回放
        if vr['page_size'] is not None and vr['page_size'] != PAGE:
            return {'ok': False, 'error': 'SNAPSHOT_UNSTABLE', 'stage': 'wal-verify',
                    'detail': 'WAL 页大小 %s 与算法常量 %d 不符' % (vr['page_size'], PAGE)}

    # ② 解密主库（页 1 校验 = 密钥先知快速失败）
    try:
        with open(db_path, 'rb') as f:
            raw_db = f.read()
    except OSError as e:
        return {'ok': False, 'error': 'DB_UNREADABLE', 'detail': '主库快照不可读：%s' % e}
    if len(raw_db) == 0 or len(raw_db) % PAGE != 0:
        return {'ok': False, 'error': 'SNAPSHOT_UNSTABLE', 'stage': 'db-size',
                'detail': '主库快照大小非法：%d 字节（应为 %d 整数倍）' % (len(raw_db), PAGE)}
    pages = len(raw_db) // PAGE
    plain_db = bytearray()
    for pgno in range(1, pages + 1):
        page = raw_db[(pgno - 1) * PAGE: pgno * PAGE]
        dec = decrypt_page(mkey, pgno, page)
        if dec is None:
            return {'ok': False, 'error': 'KEY_VERIFY_FAILED', 'stage': 'page-1',
                    'detail': '页 1 明文页头校验失败（密钥错误或算法不符）'}
        plain_db += dec

    # ③ 有效前缀 + 提交边界 → 重建明文 WAL（帧页按帧头 pgno 解密；明文校验和重算）
    out_wal_path = None
    wal_frames = 0
    stale_tail = 0
    plain_wal = None
    if wal_path is not None:
        frames = vr['_frames']
        last_commit = -1
        for i, f in enumerate(frames):
            if f['dbsize'] > 0:
                last_commit = i
        replay = frames[:last_commit + 1]  # 有效前缀内只回放到最后一个提交帧（§3.3 步骤 6）
        # 头部原样保留（其校验和仅覆盖前 24B，不受帧过滤影响，SQLite 仍认可）；字节序重新从头部推导
        info, _hdr_err = parse_wal_header(raw_wal)
        big = bool(info['big_endian']) if info is not None and not info.get('empty') else False
        # 链起点 = 头校验和（空 WAL 无头，回放集为空，占位值不参与计算）
        s0, s1 = struct.unpack('>II', raw_wal[24:32]) if len(raw_wal) >= WAL_HEADER_SIZE else (0, 0)
        out = bytearray(raw_wal[:WAL_HEADER_SIZE])
        for f in replay:
            plain = decrypt_page(mkey, f['pgno'], f['content'])
            if plain is None:
                return {'ok': False, 'error': 'KEY_VERIFY_FAILED', 'stage': 'wal-page',
                        'detail': 'WAL 帧（页 %d）解密页头校验失败' % f['pgno']}
            s0, s1 = wal_cksum(f['fh'][0:8] + plain, s0, s1, big)
            out += f['fh'][0:16] + struct.pack('>II', s0, s1) + plain
        wal_frames = len(replay)
        stale_tail = vr['stale_tail_frames']
        plain_wal = bytes(out)  # 无提交帧时为仅头 WAL（SQLite 视为空），仍与主库配对

    # ④ 写明文库 + integrity_check（可写打开让 WAL 回放；只读 URI 会跳过 WAL）。
    # out_wal 命名必须是 <out_db>-wal：SQLite 按 '<库路径>-wal' 定位 WAL，命名错会静默跳过回放。
    os.makedirs(outdir, exist_ok=True)
    out_db = os.path.join(outdir, _out_base(db_path) + '.db')
    with open(out_db, 'wb') as f:
        f.write(bytes(plain_db))
    if plain_wal is not None:
        out_wal_path = out_db + '-wal'
        with open(out_wal_path, 'wb') as f:
            f.write(plain_wal)
    integrity = 'unknown'
    try:
        conn = sqlite3.connect(out_db)
        try:
            rows = conn.execute('PRAGMA integrity_check').fetchall()
            integrity = ';'.join(str(r[0]) for r in rows) if rows else 'unknown'
        finally:
            conn.close()
    except sqlite3.Error as e:
        integrity = 'sqlite3 error: %s' % e
    # integrity_check 的可写连接关闭时会对 WAL checkpoint（回放内容落库、-wal 被消费删除），
    # 成功后 out_db 自包含；out_wal 仅在文件仍存在时报告（实践中通常已被 checkpoint 消费）。
    if out_wal_path is not None and not os.path.exists(out_wal_path):
        out_wal_path = None
    ok = integrity == 'ok'
    payload = {
        'ok': ok,
        'pages': pages,
        'wal_frames': wal_frames,
        'stale_tail_frames': stale_tail,
        'integrity': integrity,
        'out_db': out_db,
    }
    if out_wal_path is not None:
        payload['out_wal'] = out_wal_path
    if not ok:
        payload['error'] = 'INTEGRITY_FAILED'
    return payload


def _out_base(db_path):
    """输出产物基名：message.db → message.dec（dec.db / dec.db-wal）。"""
    return os.path.splitext(os.path.basename(db_path))[0] + '.dec'


# ---------- keyscan 模式（C6 内存密钥提取；Windows-only，只读观察：无注入/无写/无提权） ----------

# Windows 常量（数值仅定义，ctypes 在 Windows 分支才导入）
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_READWRITE = 0x04
_MAX_USER_ADDR = 0x7FFFFFFEFFFF
# 默认单区域上限（对齐实验 find_key6 的 419MB：绕开超大保留段，覆盖 WXWork 堆主区域）
DEFAULT_MAX_REGION_BYTES = 419 * 1024 * 1024


def build_oracle(page1):
    """从加密库页 1 构造密钥验证先知（对齐实验 find_key6.py，真机验证过的布局）。

    页 1 非遗留落盘布局：[0:8]=首段密文、[8:16]=第二段首密文块前半（被搬移）、
    [16:24]=明文页头副本、[24:32]=第二段首密文块后半。第一个完整密文块须重组：
    C0 = file[8:16] ‖ file[24:32]（不是 file[8:24]）。验证式：
    AES_dec-ECB(pagekey(k), C0) ⊕ IV(1) 前 8B == TARGET=file[16:24]。"""
    if len(page1) < PAGE:
        return None
    return {
        'c0': bytes(page1[8:16]) + bytes(page1[24:32]),
        'target': bytes(page1[16:24]),
        'iv': gen_iv(1),
        't1': struct.pack('<I', 1) + SALT,
    }


def verify_key(oracle, key):
    """候选密钥验证：16B → aes128（页密钥 = MD5(k‖pgno‖sAlT)，即 page_key(k,1)）；
    32B → aes256（SHA256 同构派生）。命中返回方案名，未命中 None。"""
    from Crypto.Cipher import AES  # 延迟导入：snapshot-verify 模式零第三方依赖
    if len(key) == 16:
        pagekey = hashlib.md5(key + oracle['t1']).digest()
        kind = 'aes128'
    elif len(key) == 32:
        pagekey = hashlib.sha256(key + oracle['t1']).digest()
        kind = 'aes256'
    else:
        return None
    # 单块 ECB ⊕ IV == CBC 首块解密（不建 CBC 上下文，热路径省开销；与 decrypt 语义一致）
    p0 = AES.new(pagekey, AES.MODE_ECB).decrypt(oracle['c0'])
    if bytes(x ^ y for x, y in zip(p0[:8], oracle['iv'][:8])) == oracle['target']:
        return kind
    return None


def scan_buffer(data, oracle, full_stride=False):
    """单内存块密钥扫描（纯函数，可脱离 Windows 单测）。

    full_stride=False：只试 16 字节对齐偏移（预筛假设：EVP 上下文堆分配 16 对齐，
    P1a 真机验证点 §9.5——miss 由调用方回退全量，正确性不依赖该假设）；
    full_stride=True：全量步长 1。每窗口先做零字节计数预筛（>半数零字节跳过，
    沿用实验 find_key6 的廉价筛——全零堆页占多数时显著省 MD5/SHA+AES 调用）。
    命中 → (kind, key 字节)；未命中 None。"""
    n = len(data)
    end = max(0, n - 15)  # 保证 16B 窗口完整（32B 窗口单独判界）
    offsets = range(0, end) if full_stride else range(0, end, 16)
    for off in offsets:
        k16 = data[off:off + 16]
        if k16.count(0) <= 8:
            kind = verify_key(oracle, k16)
            if kind is not None:
                return kind, k16
        if off + 32 > n:
            continue
        k32 = data[off:off + 32]
        if k32.count(0) <= 16:
            kind = verify_key(oracle, k32)
            if kind is not None:
                return kind, k32
    return None


def _winapi():
    """Windows API 装配（仅 Windows 分支调用；非 win32 不会走到这里）。"""
    import ctypes
    import ctypes.wintypes as wt

    kernel32 = ctypes.windll.kernel32

    class MBI(ctypes.Structure):
        _fields_ = [
            ('BaseAddress', ctypes.c_void_p),
            ('AllocationBase', ctypes.c_void_p),
            ('AllocationProtect', wt.DWORD),
            ('RegionSize', ctypes.c_size_t),
            ('State', wt.DWORD),
            ('Protect', wt.DWORD),
            ('Type', wt.DWORD),
        ]

    return kernel32, MBI


def _readable_regions(kernel32, mbi_type, hp, max_region_bytes):
    """VirtualQueryEx 枚举 MEM_COMMIT / MEM_PRIVATE / PAGE_READWRITE 且 ≤ 上限的区域
    （超大保留段跳过——WXWork 堆主区域远小于上限，跳过项非密钥驻留形态）。"""
    import ctypes
    addr = 0
    while addr < _MAX_USER_ADDR:
        mbi = mbi_type()
        if not kernel32.VirtualQueryEx(hp, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize or 0
        if size == 0:
            break
        if (mbi.State == MEM_COMMIT and mbi.Type == MEM_PRIVATE
                and mbi.Protect == PAGE_READWRITE and size <= max_region_bytes):
            yield base, size
        addr = base + size


def _read_region(kernel32, hp, base, size):
    """ReadProcessMemory 读整区域（只读；失败 → None 跳过该区域）。"""
    import ctypes
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if not kernel32.ReadProcessMemory(hp, ctypes.c_void_p(base), buf,
                                      ctypes.c_size_t(size), ctypes.byref(got)):
        return None
    return buf.raw[:got.value]


def scan_pid(pid, oracle, max_region_bytes, full_stride=False):
    """扫描单进程。返回 (status, hit, scanned_bytes)：
    status='OPENED'/'OPEN_FAILED'；hit=(kind, key) 或 None；scanned_bytes 为两段式
    累计读取量（回退段重读同区域按重复计）。两段式：先全部区域 16B 对齐预筛，miss
    再同进程全量步长 1（回退语义按进程而非按区域——对齐假设成立时全程免全量）。"""
    kernel32, mbi_type = _winapi()
    hp = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, int(pid))
    if not hp:
        return 'OPEN_FAILED', None, 0
    scanned = 0
    try:
        # 两段式：对齐段先行（EVP 上下文 16 对齐假设），miss 再全量段回退；
        # full_stride=True 时跳过对齐段直入全量（False=全量段，True=对齐段）。
        for aligned in ((True, False) if not full_stride else (False,)):
            for base, size in _readable_regions(kernel32, mbi_type, hp, max_region_bytes):
                data = _read_region(kernel32, hp, base, size)
                if not data:
                    continue
                scanned += size
                hit = scan_buffer(data, oracle, full_stride=not aligned)
                if hit is not None:
                    return 'OPENED', hit, scanned
        return 'OPENED', None, scanned
    finally:
        kernel32.CloseHandle(hp)


def cmd_keyscan(pids, db_path, max_region_bytes, full_stride):
    """keyscan 模式：页 1 先知构造 → 逐 pid 扫描 → 命中即返回（key_hex 仅进 JSON）。"""
    if sys.platform != 'win32':
        return {'ok': False, 'error': 'WINDOWS_REQUIRED', 'pids_tried': 0, 'elapsed_ms': 0}
    try:
        with open(db_path, 'rb') as f:
            page1 = f.read(PAGE)
    except OSError as e:
        return {'ok': False, 'error': 'DB_UNREADABLE', 'detail': '页 1 源库不可读：%s' % e}
    oracle = build_oracle(page1)
    if oracle is None:
        return {'ok': False, 'error': 'DB_UNREADABLE',
                'detail': '页 1 源库不足 %d 字节，无法构造先知' % PAGE}
    t0 = time.time()
    opened_any = False
    for pid in pids:
        status, hit, scanned = scan_pid(pid, oracle, max_region_bytes, full_stride)
        if status != 'OPENED':
            continue
        opened_any = True
        if hit is not None:
            kind, key = hit
            return {'ok': True, 'kind': kind, 'key_hex': key.hex(), 'pid': int(pid),
                    'scanned_bytes': scanned,
                    'elapsed_ms': int((time.time() - t0) * 1000)}
    elapsed_ms = int((time.time() - t0) * 1000)
    if not opened_any:
        return {'ok': False, 'error': 'OPEN_FAILED', 'pids_tried': len(pids), 'elapsed_ms': elapsed_ms}
    return {'ok': False, 'error': 'KEY_NOT_FOUND', 'pids_tried': len(pids), 'elapsed_ms': elapsed_ms}


def _parse_keyscan_args(args):
    """keyscan 参数解析：--pids <pid[,pid…]> --db <path> [--max-region-bytes N]
    [--aligned-first|--full]（--aligned-first 为默认，显式可写）。非法 → None（BAD_ARGS）。"""
    pids_raw = None
    db = None
    max_region = DEFAULT_MAX_REGION_BYTES
    full = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--pids' and i + 1 < len(args):
            pids_raw = args[i + 1]
            i += 2
        elif arg == '--db' and i + 1 < len(args):
            db = args[i + 1]
            i += 2
        elif arg == '--max-region-bytes' and i + 1 < len(args):
            try:
                max_region = int(args[i + 1])
            except ValueError:
                return None
            if max_region <= 0:
                return None
            i += 2
        elif arg == '--aligned-first':
            full = False
            i += 1
        elif arg == '--full':
            full = True
            i += 1
        else:
            return None
    if pids_raw is None or db is None:
        return None
    try:
        pids = [int(p) for p in pids_raw.split(',') if p.strip()]
    except ValueError:
        return None
    if not pids or any(p <= 0 for p in pids):
        return None
    return pids, db, max_region, full


# ---------- C9a 内容提取（extract_content 纯函数；query 就地使用，任务 1.9a） ----------

# P0 观察确认的系统/控制类 content_type 最小排除表（不参与待回复判定；P3 全量表再扩）。
# 只影响 last-per-conv 任务与 TS 待回复判定，不影响 messages 任务返回。
CONFIRMED_SYSTEM_TYPES = (1017, 38, 31, 1001, 1002, 1006, 1022)

# 普通文本类型：type=2 文本 protobuf 包装、type=0 顶层单字段。
# 其他已确认的卡片/系统类型只用于阅读，不改变旧文本锚点或纯文本发送确认。
_TEXT_CONTENT_TYPES = (0, 2)


def _read_varint(b, i):
    """严格 varint 读取：越界/超 10 字节 → None；成功返回 (value, 下一位置)。"""
    v = 0
    for n in range(10):
        if i >= len(b):
            return None
        x = b[i]
        i += 1
        v |= (x & 0x7F) << (7 * n)
        if not x & 0x80:
            return v, i
    return None


def _is_printable_text(s):
    """可打印判定（对齐实验 parse_messages：可见字符 + 换行/回车/制表放行；空串不算文本）。"""
    return bool(s) and all(ch.isprintable() or ch in '\n\r\t' for ch in s)


def _pb_fields(b):
    """宽松遍历 protobuf 字段，返回 [(field_no, wire_type, value_bytes)]。

    对齐实验 parse_messages.pb_fields 的容错语义：非法结构即停（不抛、不猜）；
    varint 读取用本文件的严格实现（越界即停，等效于原实现的异常中断路径）。"""
    out = []
    i = 0
    n = len(b)
    while i < n:
        key = b[i]
        f = key >> 3
        wt = key & 7
        i += 1
        if wt == 2:
            r = _read_varint(b, i)
            if r is None:
                break
            ln, i = r
            if i + ln > n:
                break
            out.append((f, 2, b[i:i + ln]))
            i += ln
        elif wt == 0:
            r = _read_varint(b, i)
            if r is None:
                break
            _, i = r
        elif wt == 5:
            if i + 4 > n:
                break
            i += 4
        elif wt == 1:
            if i + 8 > n:
                break
            i += 8
        else:
            break
    return out


def _strict_extract(data, content_type):
    """严格解析已知包装（结构与长度精确覆盖校验，越界/不匹配 → None）：
    type=2：0a<len1>( 08<varint> 12<len2>( 0a<len3> <utf8> ) )，各级字段须恰好覆盖本级
    （P0 实测形态，EVIDENCE.md：0a<len> 08 00 12 <len> 0a <len> <UTF-8文本>）；
    type=0：顶层单字段 0a<len> <utf8>，须恰好覆盖整段。成功返回文本，失败 None。"""
    if not data or data[0] != 0x0A:
        return None
    r = _read_varint(data, 1)
    if r is None:
        return None
    ln1, i = r
    if ln1 <= 0 or i + ln1 != len(data):
        return None
    payload = data[i:i + ln1]
    try:
        if content_type == 0:
            text = payload.decode('utf-8')
            return text if _is_printable_text(text) else None
        # type=2：payload = 08<varint> 12<len2>( 0a<len3> <utf8> )
        if not payload or payload[0] != 0x08:
            return None
        r = _read_varint(payload, 1)
        if r is None:
            return None
        _v, j = r  # 08 字段的 varint 值（内部状态）不参与文本判定
        if j >= len(payload) or payload[j] != 0x12:
            return None
        r = _read_varint(payload, j + 1)
        if r is None:
            return None
        ln2, j = r
        if ln2 <= 0 or j + ln2 != len(payload):
            return None
        wrap = payload[j:j + ln2]
        if not wrap or wrap[0] != 0x0A:
            return None
        r = _read_varint(wrap, 1)
        if r is None:
            return None
        ln3, k = r
        if ln3 <= 0 or k + ln3 != len(wrap):
            return None
        text = wrap[k:k + ln3].decode('utf-8')
        return text if _is_printable_text(text) else None
    except UnicodeDecodeError:
        return None


def _recursive_text(data):
    """递归找可打印 UTF-8 文本，取最深层、最长者（对齐实验 parse_messages.extract_text 思路）。"""
    texts = []

    def rec(b, depth):
        for _f, wt, v in _pb_fields(b):
            if wt != 2:
                continue
            try:
                s = v.decode('utf-8')
            except UnicodeDecodeError:
                rec(v, depth + 1)
                continue
            if _is_printable_text(s):
                texts.append((depth, len(s), s))
            else:
                rec(v, depth + 1)

    rec(data, 0)
    if not texts:
        return None
    return max(texts, key=lambda t: (t[0], t[1]))[2]


def extract_content(content, content_type):
    """C9a：content blob → {text, trusted}（任务 1.9a；query 的 messages 行就地使用）。

    - type 0/2：先严格解析已知包装（_strict_extract，长度精确覆盖校验）→ {text, trusted:True}；
      严格失败 → 递归找可打印 UTF-8（_recursive_text）→ {text, trusted:False}；
      仍无 → {None, False}。
    - type 80：已确认的 sections Markdown 卡片；系统事件按已确认的类型/路径读取。
      未确认类型保留 {None, False}，不从任意二进制片段猜正文。
    - content 兼容 bytes/bytearray/memoryview/str（str 按 UTF-8 编码后同路径）/None。"""
    if isinstance(content, str):
        content = content.encode('utf-8')
    elif isinstance(content, memoryview):
        content = content.tobytes()
    if not isinstance(content, (bytes, bytearray)) or not content:
        return {'text': None, 'trusted': False}
    try:
        ct = int(content_type)
    except (TypeError, ValueError):
        return {'text': None, 'trusted': False}
    data = bytes(content)
    if ct == 80:
        return _card_content(data)
    if ct == 1001:
        name = _readable_utf8(data)
        return {'text': '群名变更为“%s”' % name if name else '群名变更', 'trusted': name is not None}
    if ct == 1002:
        return {'text': '群成员邀请通知', 'trusted': False}
    if ct == 1006:
        return {'text': '群消息推送设置变更', 'trusted': False}
    if ct == 1022:
        outer, outer_complete = _history_fields(data)
        payloads = [value for number, wire, value in outer if (number, wire) == (1, 2)]
        if len(payloads) == 1:
            inner, inner_complete = _history_fields(payloads[0])
            texts = [_readable_utf8(value) for number, wire, value in inner if (number, wire) == (2, 2)]
            if len(texts) == 1 and texts[0] is not None:
                return {'text': texts[0], 'trusted': outer_complete and inner_complete
                        and len(outer) == 1 and len(inner) == 1}
        return {'text': '群机器人提示', 'trusted': False}
    if ct not in _TEXT_CONTENT_TYPES:
        return {'text': None, 'trusted': False}
    strict = _strict_extract(data, ct)
    if strict is not None:
        return {'text': strict, 'trusted': True}
    fallback = _recursive_text(data)
    if fallback is not None:
        return {'text': fallback, 'trusted': False}
    return {'text': None, 'trusted': False}


def _readable_utf8(data):
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return None
    return text if _is_printable_text(text) else None


def _card_content(data):
    """只读取实证的 root.4.1 sections 文本，root.1 Markdown 可作可读回退。"""
    fields, complete = _history_fields(data)
    summaries = [_readable_utf8(value) for number, wire, value in fields if (number, wire) == (1, 2)]
    fallback = next((text for text in summaries if text is not None), None)
    cards = [value for number, wire, value in fields if (number, wire) == (4, 2)]
    # 实证 root.5 为 scalar 元数据，不是缺失的可见正文。
    complete = complete and len(summaries) == 1 and len(cards) == 1
    complete = complete and all((number, wire) in ((1, 2), (4, 2), (5, 0)) for number, wire, _ in fields)
    texts = []
    for card in cards:
        envelope, envelope_complete = _history_fields(card)
        bodies = [value for number, wire, value in envelope if (number, wire) == (1, 2)]
        complete = complete and envelope_complete and len(bodies) == 1
        complete = complete and all((number, wire) in ((1, 2), (2, 2)) for number, wire, _ in envelope)
        for body in bodies:
            try:
                document = json.loads(body.decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                complete = False
                continue
            if not isinstance(document, dict) or document.get('msgtype') != 'sections':
                complete = False
                continue
            sections = document.get('sections')
            if not isinstance(sections, list) or not sections:
                complete = False
                continue
            for section in sections:
                if not isinstance(section, dict):
                    complete = False
                    continue
                text = section.get('text')
                if (section.get('type') != 'basic' or not isinstance(text, dict)
                        or text.get('type') != 'markdown' or not isinstance(text.get('content'), str)
                        or not _is_printable_text(text['content'])):
                    complete = False
                    continue
                texts.append(text['content'])
                # 未支持的组件/字段可能携带可见内容，不把部分正文称为完整。
                complete = complete and not section.get('components') and not section.get('fields')
    text = '\n\n'.join(texts) if texts else fallback
    if not complete and fallback is not None:
        text = fallback
    return {'text': text, 'trusted': complete and bool(texts)}


# ---------- query 模式（C8 读库查询；对 workdir 内已解密明文库做只读 SQL，任务 1.8） ----------

DEFAULT_QUERY_LIMIT = 100
# message_table 查询投影列（P0 冻结 18 列中与 C8 契约相关的 9 列；content 仅就地提取为
# text/trusted，不原样输出）。其余列（devinfo/from_app_id/extra_content 等为已知列名、
# 尚有 4 列未建模）不进查询契约。
QUERY_MESSAGE_COLUMNS = ('message_id', 'server_id', 'sequence', 'sender_id', 'conversation_id',
                         'content_type', 'send_time', 'flag', 'content')

QUERY_USAGE = ('用法：wecom_localdb.py query --workdir <dir> '
               '--task messages|send-messages|conversations|last-per-conv|resolve|groups|history '
               '[--conv <会话id>] [--after-id N] [--limit N] [--since <秒>] [--until <秒>] '
               '[--exclude-types <t[,t…]>] [--name <名称>] '
               '[--with-names（仅 last-per-conv，须与 --account 同传）] '
               '[--external-only（仅 resolve，须与 --account 同传）]')

# workdir 内解密库定位名：优先任务契约名 <logical>.db.dec.db，兼容 decrypt 自然产物名
# <logical>.dec.db（_out_base 产物）。两命名都是显式约定，运行时确认存在才用。
_DEC_DB_NAME_PATTERNS = ('%s.db.dec.db', '%s.dec.db')


def _resolve_dec_db(workdir, logical):
    """定位 workdir 内某逻辑库的解密文件；都不在 → None（调用方按缺失语义处理）。"""
    for pattern in _DEC_DB_NAME_PATTERNS:
        path = os.path.join(workdir, pattern % logical)
        if os.path.isfile(path):
            return path
    return None


def _open_ro(path):
    """只读打开 SQLite（uri ro；不创建文件、不写不改——§8 本地库原文永不修改）。"""
    uri = 'file:' + urllib.parse.quote(path.replace('\\', '/'), safe='/') + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True)
    conn.text_factory = lambda b: b.decode('utf-8', errors='replace')
    return conn


def _qi(ident):
    """SQL 标识符引用（列名来自 PRAGMA table_info，仍做转义兜底；表名为代码内常量）。"""
    return '"%s"' % ident.replace('"', '""')


def _table_columns(conn, table):
    """表列名（PRAGMA table_info 运行时确认——user/conversation 表列名只有部分经 P0 实证，
    缺失列不猜不查）。"""
    return [r[1] for r in conn.execute('PRAGMA table_info(%s)' % table).fetchall()]


# ---------- P10 attachments: metadata on the timeline, bytes only on explicit read ----------

_ATTACHMENT_IMAGE_BYTES = 5 * 1024 * 1024
_ATTACHMENT_TEXT_EXTENSIONS = {'.md', '.markdown', '.txt', '.csv', '.tsv', '.json', '.jsonl',
                               '.log', '.xml', '.yaml', '.yml', '.html', '.htm', '.ini'}


def _attachment_fields(content):
    fields, _ = _history_fields(bytes(content or b''))
    return fields


def _attachment_descriptor(content, content_type):
    fields = _attachment_fields(content)
    result = {'kind': 'image' if content_type == 101 else 'file', 'status': 'not_cached'}
    sizes = [v for n, w, v in fields if n == 4 and w == 0 and isinstance(v, int) and v >= 0]
    if sizes:
        result['size_bytes'] = sizes[0]
    if content_type == 102:
        names = [_readable_utf8(v) for n, w, v in fields if (n, w) == (2, 2)]
        if len(names) == 1 and names[0]:
            result['name'] = names[0]
    return result


def _attachment_cache_path(content, content_type, account_dir, send_time):
    """Use the client's exact resource mapping. Never infer images from time or filename."""
    if not isinstance(account_dir, str) or not os.path.isdir(account_dir):
        return None
    root = os.path.realpath(account_dir)
    cache = os.path.realpath(os.path.join(root, 'Cache'))
    if os.path.commonpath([root, cache]) != root:
        return None
    fields = _attachment_fields(content)
    resources = []
    for number in ((3, 26) if content_type == 101 else (3,)):
        resources.extend(t for n, w, v in fields if (n, w) == (number, 2)
                         for t in [_readable_utf8(v)] if t)
    folder = 'Image' if content_type == 101 else 'File'
    month = time.strftime('%Y-%m', time.localtime(send_time)) if type(send_time) is int and send_time >= 0 else ''

    def cached(name):
        if not isinstance(name, str) or not name:
            return None
        name = name.replace('\\', os.sep).replace('/', os.sep)
        for candidate in [os.path.join(root, name), os.path.join(cache, name),
                          os.path.join(cache, folder, name), os.path.join(cache, folder, month, name)]:
            candidate = os.path.realpath(candidate)
            try:
                if os.path.commonpath([cache, candidate]) == cache and os.path.isfile(candidate):
                    return candidate
            except ValueError:
                continue
        return None

    mapping_dir = os.path.join(root, 'CacheMapping')
    try:
        mapping_files = sorted(entry.path for entry in os.scandir(mapping_dir)
                               if entry.name.endswith('.db') and entry.is_file(follow_symlinks=False))
    except OSError:
        mapping_files = []
    # Resource preference (clear image before thumbnail) is independent of DB file order.
    for resource in resources:
        for path in mapping_files:
            connection = None
            try:
                connection = _open_ro(path)
                rows = connection.execute('SELECT file_name FROM mapping WHERE key=?', (resource,)).fetchall()
                for (name,) in rows:
                    found = cached(name)
                    if found:
                        return found
            except (sqlite3.Error, OSError):
                continue
            finally:
                if connection is not None:
                    connection.close()
    # Some downloaded text files have no mapping row. A bounded exact-name + size +
    # digest fallback is sufficient for those files; it does not scan the account tree.
    if content_type == 102:
        descriptor = _attachment_descriptor(content, content_type)
        name, size = descriptor.get('name'), descriptor.get('size_bytes')
        digests = {t.lower() for n, w, v in fields if w == 2 for t in [_readable_utf8(v)]
                   if t and re.fullmatch(r'[0-9a-fA-F]{32}', t)}
        if (name and not any(ch in name for ch in '/\\:') and type(size) is int
                and size <= 16 * 1024 * 1024 and digests):
            for relative in (os.path.join('File', month, name), os.path.join('File', name)):
                found = cached(relative)
                if found and os.path.getsize(found) == size:
                    digest = hashlib.md5()
                    with open(found, 'rb') as stream:
                        for chunk in iter(lambda: stream.read(65536), b''):
                            digest.update(chunk)
                    if digest.hexdigest() in digests:
                        return found
    return None


def _attachment_image_info(path, *, verify=False):
    from PIL import Image
    try:
        with Image.open(path) as image:
            info = {'width': image.width, 'height': image.height,
                    'mime_type': Image.MIME.get(image.format, 'application/octet-stream')}
            if verify:
                image.verify()
            return info
    except (Image.DecompressionBombError, SyntaxError):
        # These are image parser failures, not failures of the neighboring chat history.
        raise ValueError('ATTACHMENT_IMAGE_UNREADABLE') from None


def _describe_attachment(content, content_type, account_dir, send_time):
    descriptor = _attachment_descriptor(content, content_type)
    try:
        path = _attachment_cache_path(content, content_type, account_dir, send_time)
        if path:
            descriptor.update(status='available', size_bytes=os.path.getsize(path))
            if content_type == 101:
                descriptor.update(_attachment_image_info(path))
            else:
                import mimetypes
                descriptor['mime_type'] = mimetypes.guess_type(descriptor.get('name', path))[0] or 'application/octet-stream'
    except (OSError, ValueError, ImportError):
        descriptor['status'] = 'unreadable'
    return descriptor


def _read_attachment(conn, conversation_id, account_dir, request):
    """Explicit exact message read; never advances a conversation cursor."""
    if (not isinstance(request, dict) or set(request) - {'message_id', 'offset', 'max_bytes'}
            or type(request.get('message_id')) is not int or not 1 <= request['message_id'] <= 9007199254740991
            or type(request.get('offset', 0)) is not int or not 0 <= request.get('offset', 0) <= 9007199254740991
            or type(request.get('max_bytes', 4096)) is not int or not 1 <= request.get('max_bytes', 4096) <= 8192):
        return {'ok': False, 'error': 'BAD_ARGS'}
    mid = request['message_id']
    row = conn.execute('SELECT content_type,content,send_time FROM message_table '
                       'WHERE conversation_id=? AND message_id=?', (conversation_id, mid)).fetchone()
    if row is None:
        return {'ok': True, 'attachment': {'message_id': mid, 'status': 'not_found'}}
    ct, content, stamp = row
    if ct not in (101, 102):
        return {'ok': True, 'attachment': {'message_id': mid, 'status': 'unsupported'}}
    result = {'message_id': mid, **_attachment_descriptor(content, ct)}
    try:
        path = _attachment_cache_path(content, ct, account_dir, stamp)
        if not path:
            return {'ok': True, 'attachment': result}
        size = os.path.getsize(path)
        result.update(size_bytes=size, status='available')
        if ct == 101:
            if size > _ATTACHMENT_IMAGE_BYTES:
                result['status'] = 'too_large'
            else:
                import base64
                result.update(_attachment_image_info(path, verify=True))
                mime = result['mime_type']
                if mime not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
                    result['status'] = 'unsupported'
                else:
                    with open(path, 'rb') as stream:
                        data = stream.read(_ATTACHMENT_IMAGE_BYTES + 1)
                    if len(data) > _ATTACHMENT_IMAGE_BYTES:
                        result['status'] = 'too_large'
                    else:
                        result.update(mime_type=mime, image={'type': 'image', 'mimeType': mime,
                                                          'data': base64.b64encode(data).decode('ascii')})
        elif os.path.splitext(result.get('name', path))[1].lower() not in _ATTACHMENT_TEXT_EXTENSIONS:
            result['status'] = 'unsupported'
        else:
            import codecs
            offset, budget = request.get('offset', 0), request.get('max_bytes', 4096)
            if offset > size:
                result['status'] = 'invalid_offset'
            else:
                with open(path, 'rb') as stream:
                    stream.seek(offset)
                    raw = stream.read(budget + 4)
                # Generated continuation offsets are UTF-8 boundaries. If a caller
                # requests another offset, report the invalid range rather than guess.
                decoder = codecs.getincrementaldecoder('utf-8')('strict')
                chunk = raw[:budget]
                text = decoder.decode(chunk, final=offset + len(chunk) >= size)
                consumed = len(chunk) - len(decoder.getstate()[0])
                if consumed == 0 and raw:
                    for extra in raw[budget:]:
                        text += decoder.decode(bytes([extra]), final=False)
                        consumed += 1
                        if text:
                            consumed = budget + consumed
                            break
                    else:
                        raise UnicodeDecodeError('utf-8', raw, 0, len(raw), 'incomplete character')
                if offset == 0:
                    text = text.removeprefix('\ufeff')
                next_offset = offset + consumed
                result.update(text=text, offset=offset, next_offset=next_offset, has_more=next_offset < size,
                              mime_type='text/markdown' if os.path.splitext(result.get('name', path))[1].lower() in ('.md', '.markdown') else 'text/plain')
    except (OSError, ValueError, ImportError):
        result['status'] = 'unreadable'
    return {'ok': True, 'attachment': result}


def _message_row(row):
    """message_table 行 → C8 契约投影（content 就地提取为 text/trusted，不输出原始 bytes）。"""
    rec = dict(zip(QUERY_MESSAGE_COLUMNS, row))
    for key in ('server_id', 'sender_id', 'conversation_id'):
        if rec[key] is not None:
            rec[key] = str(rec[key])  # SQLite INTEGER 可能超出 JS 安全整数，身份字段不经 Number。
    extracted = extract_content(rec.get('content'), rec.get('content_type'))
    rec['text'] = extracted['text']
    rec['trusted'] = extracted['trusted']
    del rec['content']
    order = ('message_id', 'server_id', 'sequence', 'sender_id', 'conversation_id',
             'content_type', 'send_time', 'flag', 'text', 'trusted')
    result = {k: rec.get(k) for k in order}
    if rec['content_type'] in (101, 102):
        result['attachment'] = _attachment_descriptor(row[-1], rec['content_type'])
    return result


def _message_anchor(row):
    # 服务端确认可能补齐 server_id/sequence；包装字段不是稳定消息身份。
    fields = [row.get(k) for k in ('message_id', 'sender_id', 'conversation_id', 'send_time', 'text')]
    # 旧 v2/wb1 游标的非文本正文始终为 None；新增阅读解码不改变既有锚点。
    if row.get('content_type') not in _TEXT_CONTENT_TYPES:
        fields[-1] = None
    return hashlib.sha256(json.dumps(fields, separators=(',', ':')).encode('utf-8')).hexdigest()


def _query_messages(conn, conv, after_id, limit, since, anchor=None, *, until=None, send_projection=False):
    """首次取最近 limit 条；显式 after-id（含 0）取水位之后最早 limit 条。
    返回集按入库序，next_cursor_id 只认返回集末端；空批次保留原水位/锚点。
    since 可选过滤，排除表不影响本任务。"""
    sql = ('SELECT %s FROM message_table WHERE conversation_id = ? AND message_id > ?'
           % ', '.join(QUERY_MESSAGE_COLUMNS))
    params = [conv, after_id if after_id is not None else 0]
    if after_id is not None and after_id > 0 and anchor is not None:
        previous = conn.execute('SELECT %s FROM message_table WHERE message_id=? AND conversation_id=?'
                                % ', '.join(QUERY_MESSAGE_COLUMNS), (after_id, conv)).fetchone()
        if previous is None or anchor != _message_anchor(_message_row(previous)):
            return {'ok': False, 'error': 'CURSOR_STALE'}
    if since is not None:
        sql += ' AND send_time >= ?'
        params.append(since)
    if until is not None:
        sql += ' AND send_time <= ?'
        params.append(until)
    sql += ' ORDER BY message_id %s LIMIT ?' % ('DESC' if after_id is None else 'ASC')
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    if after_id is None:
        rows.reverse()
    legacy_messages = [_message_row(row) for row in rows]
    messages = [_send_message_row(row) for row in rows] if send_projection else legacy_messages
    next_cursor_id = messages[-1]['message_id'] if messages else (after_id or 0)
    return {'ok': True, 'task': 'send-messages' if send_projection else 'messages', 'conversation_id': conv,
            'count': len(messages), 'next_cursor_id': next_cursor_id, 'messages': messages,
            'next_anchor': _message_anchor(legacy_messages[-1]) if messages else (anchor or '')}


def _query_conversations(conn):
    """conversations 任务：全表按 conversation_id 聚合，按 last_message_id 降序。
    last_send_time 取水位行（最大 message_id）的 send_time——迟到同步（更大 message_id、
    更小 send_time）下与 MAX(send_time) 不同，语义与 cursor 同基元（水位末端那条）。
    last_anchor = 水位行的消息稳定字段摘要（_message_anchor，与 read-session 游标锚同源）：
    watch 侧按 max 建基线时一并存储，供下轮 watch 的锚点有效性校验（第九轮评审 P1）。"""
    cols = ', '.join(QUERY_MESSAGE_COLUMNS)
    rows = conn.execute(
        'SELECT %s, a.cnt '
        'FROM (SELECT conversation_id AS _grp_conv, COUNT(*) AS cnt, MAX(message_id) AS max_id '
        '      FROM message_table GROUP BY conversation_id) AS a '
        'JOIN message_table AS m ON m.message_id = a.max_id '
        'ORDER BY m.message_id DESC' % cols).fetchall()
    conversations = []
    for r in rows:
        row = _message_row(r[:len(QUERY_MESSAGE_COLUMNS)])
        conversations.append({'conversation_id': row['conversation_id'], 'count': r[-1],
                              'last_send_time': row['send_time'],
                              'last_message_id': row['message_id'],
                              'last_anchor': _message_anchor(row)})
    return {'ok': True, 'task': 'conversations', 'conversations': conversations}


def _query_groups(conn, keyword, limit):
    """session 库群名列表：字面子串过滤、按 ID 去重后稳定排序，包含无消息群。"""
    columns = _table_columns(conn, 'conversation_table')
    id_col = (_find_conversation_id_column(conn, 'conversation_table', columns)
              if 'name' in columns else None)
    if id_col is None:
        return {'ok': False, 'error': 'SCHEMA_UNSUPPORTED',
                'detail': 'conversation_table 缺少可确认的群会话 ID 或 name 列'}
    cid = _qi(id_col)
    sql = ('SELECT %s, MIN(name) AS group_name FROM conversation_table '
           "WHERE typeof(%s) = 'text' AND %s GLOB 'R:*' "
           "AND typeof(name) = 'text' AND trim(name) <> '' "
           'AND instr(name, ?) > 0 GROUP BY %s '
           'ORDER BY group_name, %s LIMIT ?' % (cid, cid, cid, cid, cid))
    rows = conn.execute(sql, (keyword or '', limit + 1)).fetchall()
    groups = [{'conversation_id': row[0], 'name': row[1]} for row in rows[:limit]]
    return {'ok': True, 'task': 'groups', 'groups': groups, 'count': len(groups),
            'has_more': len(rows) > limit}


def _history_varint(data, offset):
    """history 严格读取 uint64；不改变旧 extract_content 的容错契约。"""
    value = 0
    for index in range(10):
        if offset >= len(data):
            return None
        byte = data[offset]
        offset += 1
        if index == 9 and byte > 1:
            return None
        value |= (byte & 0x7f) << (7 * index)
        if not byte & 0x80:
            return value, offset
    return None


def _history_fields(data):
    """完整 tag/wire 读取，保留损坏位置之前的字段并显式返回完整性。"""
    fields = []
    offset = 0
    while offset < len(data):
        tag = _history_varint(data, offset)
        if tag is None:
            return fields, False
        key, offset = tag
        number, wire = key >> 3, key & 7
        if number == 0 or number > 0x1fffffff:
            return fields, False
        if wire == 0:
            item = _history_varint(data, offset)
            if item is None:
                return fields, False
            value, offset = item
        elif wire == 2:
            item = _history_varint(data, offset)
            if item is None:
                return fields, False
            size, offset = item
            if size > len(data) - offset:
                return fields, False
            value = data[offset:offset + size]
            offset += size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            if size > len(data) - offset:
                return fields, False
            value = data[offset:offset + size]
            offset += size
        else:
            return fields, False
        fields.append((number, wire, value))
    return fields, True


_HISTORY_GAP = '[消息片段未解析]'


def _history_segment(data):
    """已实证的富文本片段；返回内容、完整性及是否识别，展示文字不充当状态哨兵。"""
    fields, complete = _history_fields(data)
    kinds = [value for number, wire, value in fields if (number, wire) == (1, 0)]
    payloads = [value for number, wire, value in fields if (number, wire) == (2, 2)]
    if len(kinds) != 1 or len(payloads) != 1:
        return [_HISTORY_GAP], False, False
    complete = complete and len(fields) == 2
    payload, payload_complete = _history_fields(payloads[0])
    complete = complete and payload_complete
    kind = kinds[0]
    if kind == 0:
        texts = [value for number, wire, value in payload if (number, wire) == (1, 2)]
        if len(texts) != 1:
            return [_HISTORY_GAP], False, False
        try:
            text = texts[0].decode('utf-8')
        except UnicodeDecodeError:
            return [_HISTORY_GAP], False, False
        if text and not _is_printable_text(text):
            return [_HISTORY_GAP], False, False
        complete = complete and len(payload) == 1
        return [text] + ([] if complete else [_HISTORY_GAP]), complete, True
    if kind == 5:
        ids = [value for number, wire, value in payload if (number, wire) == (1, 0)]
        names = [value for number, wire, value in payload if (number, wire) == (2, 2)]
        if len(ids) != 1 or len(names) > 1:
            return [_HISTORY_GAP], False, False
        name = None
        if names:
            try:
                decoded = names[0].decode('utf-8')
                if decoded and not _is_printable_text(decoded):
                    complete = False
                elif decoded.strip():
                    name = decoded
            except UnicodeDecodeError:
                complete = False
        complete = complete and len(payload) == 1 + len(names)
        user_id = str(ids[0])
        mention = {'kind': 'all' if user_id == '0' else 'user', 'user_id': user_id,
                   'name': '所有人' if user_id == '0' else name,
                   'name_source': 'message' if user_id == '0' or name else 'unresolved'}
        return [mention] + ([] if complete else [_HISTORY_GAP]), complete, True
    return [_HISTORY_GAP], False, False


def _history_content(content, content_type, fallback):
    """history 专用有序片段；旧 messages/watch 的正文与锚点继续使用原解析。"""
    if content_type not in (0, 2):
        return [fallback['text']] if fallback['text'] is not None else [], fallback['trusted']
    if isinstance(content, str):
        content = content.encode('utf-8')
    elif isinstance(content, memoryview):
        content = content.tobytes()
    if not isinstance(content, (bytes, bytearray)) or not content:
        return [_HISTORY_GAP], False
    if content_type == 0:
        legacy_text = _strict_extract(bytes(content), 0)
        if legacy_text is not None:
            return [legacy_text], True
    fields, root_complete = _history_fields(content)
    complete = root_complete
    parts = []
    known = False
    for number, wire, value in fields:
        if (number, wire) != (1, 2):
            parts.append(_HISTORY_GAP)
            complete = False
            continue
        segment, segment_complete, recognized = _history_segment(value)
        parts.extend(segment)
        known = known or recognized
        complete = complete and segment_complete
    if not root_complete or not fields:
        parts.append(_HISTORY_GAP)
    if not known and fallback['text'] is not None:
        parts = [fallback['text'], _HISTORY_GAP]
    return parts, complete and bool(fields)


def _send_message_row(row):
    """发送确认只信完整纯文本片段；不改变旧消息正文或游标锚点。"""
    message = _message_row(row)
    parts, complete = _history_content(row[-1], message['content_type'], message)
    message['trusted'] = (message['content_type'] in _TEXT_CONTENT_TYPES and complete
                          and all(isinstance(part, str) for part in parts))
    if message['trusted']:
        message['text'] = ''.join(parts)
    return message


def _history_user_names(workdir, user_ids):
    """只查本批 ID；重复、空名或来源缺失不猜。分批绑定避免 SQLite 参数上限。"""
    path = _resolve_dec_db(workdir, 'user') if workdir else None
    if path is None or not user_ids:
        return {}
    user = None
    try:
        user = _open_ro(path)
        columns = _table_columns(user, 'user_table')
        if 'id' not in columns or 'name' not in columns:
            return {}
        names = {}
        wanted = sorted(user_ids)
        for start in range(0, len(wanted), 400):
            batch = wanted[start:start + 400]
            found = {}
            sql = ('SELECT CAST(id AS TEXT), name FROM user_table '
                   'WHERE CAST(id AS TEXT) IN (%s)' % ','.join('?' * len(batch)))
            for uid, name in user.execute(sql, batch):
                found.setdefault(uid, []).append(name)
            for uid, values in found.items():
                if (len(values) == 1 and isinstance(values[0], str) and values[0].strip()
                        and _is_printable_text(values[0])):
                    names[uid] = values[0]
        return names
    except sqlite3.Error:
        return {}  # 降级由每条 unresolved 和顶层 naming_incomplete 明示。
    finally:
        if user is not None:
            user.close()


def _sender_platforms(workdir, user_ids, account=None):
    """Exact current-account/WeChat virtual-corp facts; external relation is not platform."""
    result = {uid: 'unknown' for uid in user_ids}
    path = _resolve_dec_db(workdir, 'user')
    if path is None or not user_ids:
        return result
    conn = _open_ro(path)
    try:
        if not {'id', 'corp_id'} <= set(_table_columns(conn, 'user_table')):
            return result
        # The client uses one virtual corp for personal WeChat accounts. Its
        # meaning and current 5.0.13 rows were independently checked on 2026-10-27.
        wechat_corp = '1970325134026788'
        own_corp = None
        if account:
            own_rows = conn.execute('SELECT CAST(corp_id AS TEXT) FROM user_table WHERE CAST(id AS TEXT)=?',
                                    (account,)).fetchall()
            if len(own_rows) == 1 and isinstance(own_rows[0][0], str) and own_rows[0][0].isdigit() and int(own_rows[0][0]) > 0:
                own_corp = own_rows[0][0]
        wanted = sorted(set(user_ids))
        for start in range(0, len(wanted), 400):
            batch = wanted[start:start + 400]
            placeholders = ','.join('?' for _ in batch)
            found = {}
            for uid, corp in conn.execute(
                    'SELECT CAST(id AS TEXT), CAST(corp_id AS TEXT) FROM user_table '
                    'WHERE CAST(id AS TEXT) IN (%s)' % placeholders, batch):
                found.setdefault(uid, set()).add(corp)
            for uid, corps in found.items():
                if corps == {wechat_corp}:
                    result[uid] = 'wechat'
                elif own_corp and corps == {own_corp}:
                    result[uid] = 'wecom'
        return result
    finally:
        conn.close()


def _annotate_senders(messages, workdir, account=None):
    ids = {row.get('sender_id') for row in messages if isinstance(row.get('sender_id'), str)}
    missing_names = {row.get('sender_id') for row in messages if 'sender_name_source' not in row and isinstance(row.get('sender_id'), str)}
    names = _history_user_names(workdir, missing_names)
    platforms = _sender_platforms(workdir, ids, account)
    for message in messages:
        uid = message.get('sender_id')
        if 'sender_name_source' not in message:
            message['sender_name'] = names.get(uid)
            message['sender_name_source'] = 'user_table' if names.get(uid) else 'unresolved'
        message['sender_platform'] = platforms.get(uid, 'unknown')
        if account and uid == account:
            message['sender_platform'] = 'wecom'
    return messages


def _history_messages(rows, workdir, account=None):
    projected = []
    user_ids = set()
    for row in rows:
        message = _message_row(row)
        parts, complete = _history_content(row[-1], message['content_type'], message)
        mentions = [part for part in parts if isinstance(part, dict)]
        if message['sender_id'] is not None:
            user_ids.add(message['sender_id'])
        user_ids.update(mention['user_id'] for mention in mentions
                        if mention['kind'] == 'user' and mention['name'] is None)
        projected.append((message, parts, mentions, complete))
    names = _history_user_names(workdir, user_ids)
    messages = []
    naming_incomplete = False
    for message, parts, mentions, complete in projected:
        sender_name = names.get(message['sender_id'])
        message['sender_name'] = sender_name
        message['sender_name_source'] = 'user_table' if sender_name else 'unresolved'
        naming_incomplete = naming_incomplete or sender_name is None
        for mention in mentions:
            if mention['name'] is None:
                mention['name'] = names.get(mention['user_id'])
                mention['name_source'] = 'user_table' if mention['name'] else 'unresolved'
            naming_incomplete = naming_incomplete or mention['name'] is None
        if message['content_type'] in (0, 2):
            message['text'] = ''.join(
                part if isinstance(part, str) else '@' + (part['name'] or '未知成员(%s)' % part['user_id'])
                for part in parts)
            message['trusted'] = complete
        message['mentions'] = mentions
        message['mentions_complete'] = complete
        messages.append(message)
    _annotate_senders(messages, workdir, account)
    return messages, naming_incomplete


def _query_group_poll(conn, conv, after_id, limit, anchor, workdir, account=None):
    """Stateless insertion-order pages; the first batch is context, never old work to replay."""
    if after_id is not None and after_id > 0:
        previous = conn.execute('SELECT %s FROM message_table WHERE message_id=? AND conversation_id=?'
                                % ', '.join(QUERY_MESSAGE_COLUMNS), (after_id, conv)).fetchone()
        if previous is None or anchor != _message_anchor(_message_row(previous)):
            return {'ok': False, 'error': 'CURSOR_STALE'}
    rows = conn.execute('SELECT %s FROM message_table WHERE conversation_id=? AND message_id>? '
                        'ORDER BY message_id %s LIMIT ?' % (', '.join(QUERY_MESSAGE_COLUMNS),
                        'DESC' if after_id is None else 'ASC'),
                        (conv, after_id or 0, limit + 1)).fetchall()
    selected = rows[:limit]
    if after_id is None:
        selected.reverse()
    messages, incomplete = _history_messages(selected, workdir, account)
    # Keep a complete bounded JSON message for the model, rather than truncating
    # its JSON/text after a cursor was advanced. Large single rows fail loudly.
    total = 0
    kept = []
    indices = range(len(messages) - 1, -1, -1) if after_id is None else range(len(messages))
    for index in indices:
        size = len(json.dumps(messages[index], ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        if total + size > 16384:
            if not kept:
                return {'ok': False, 'error': 'CONTENT_TOO_LARGE'}
            break
        kept.append(index)
        total += size
    if after_id is None:
        kept.reverse()
    more = after_id is not None and (len(rows) > limit or len(kept) < len(selected))
    selected = [selected[index] for index in kept]
    messages = [messages[index] for index in kept]
    last = _message_row(selected[-1]) if selected else None
    return {'ok': True, 'task': 'group-poll', 'conversation_id': conv, 'messages': messages,
            'count': len(messages), 'has_more': more,
            'next_cursor_id': last['message_id'] if last else (after_id or 0),
            'next_anchor': _message_anchor(last) if last else (anchor or ''),
            'invalid_time_count': 0, 'naming_incomplete': incomplete}


def _query_history(conn, conv, limit, since, until, workdir=None, account=None):
    """最近 N 条有效时间消息升序展示；发送时间相同以 message_id 保持稳定顺序。"""
    valid_time = ("typeof(send_time) = 'integer' "
                  'AND send_time >= 0 AND send_time <= 253402271999')
    invalid_time_count = conn.execute(
        'SELECT COUNT(*) FROM message_table WHERE conversation_id = ? AND NOT (%s)'
        % valid_time, (conv,)).fetchone()[0]
    sql = ('SELECT %s FROM message_table WHERE conversation_id = ? AND %s'
           % (', '.join(QUERY_MESSAGE_COLUMNS), valid_time))
    params = [conv]
    if since is not None:
        sql += ' AND send_time >= ?'
        params.append(since)
    if until is not None:
        sql += ' AND send_time <= ?'
        params.append(until)
    sql += ' ORDER BY send_time DESC, message_id DESC LIMIT ?'
    params.append(limit + 1)
    rows = conn.execute(sql, params).fetchall()
    messages, naming_incomplete = _history_messages(reversed(rows[:limit]), workdir, account)
    return {'ok': True, 'task': 'history', 'conversation_id': conv,
            'messages': messages, 'count': len(messages), 'has_more': len(rows) > limit,
            'invalid_time_count': invalid_time_count, 'naming_incomplete': naming_incomplete}


def _peer_id_of(conv_id, account):
    """S: 会话 id（S:<a>_<b>）解析对方用户 id（与 account 相异的一端；自会话取 account 本身）；
    解析失败 → None（调用方按无名处理，不猜）。"""
    parts = conv_id[2:].split('_')
    other = next((p for p in parts if p and p != account), None)
    if other is None and account in parts:
        other = account  # 自会话（S:self_self）：对方即本人
    return other or None


def _conversation_display_names(workdir, conv_ids, account):
    """反向取名（last-per-conv --with-names）：与 resolve 同一命名纪律——
    R: 群名只认 session 库 conversation_table（id→name 单值，值为 str 或 None）；
    S: 联系人认 user 库 user_table（会话 id 解析对方 id→该行），复用 resolve 的名称/别名/
    备注候选列交集（_USER_NAME_COLUMN_CANDIDATES 与 PRAGMA 取交集）：值为
    {'name': 规范 name 列值（可 None）, 'match_names': [该行所有非空名称字段]}
    （第十轮评审 P2①：备注/别名与 resolve 同语义参与命名，TS 过滤按 name/match_names
    任意一项子串命中即算命中；来源库缺失 / 列名无法确认 / 无命中 / 全空 → name=None）。
    返回 (names, session_unavailable)：存在 R: 会话且 session 来源不可用（解密库缺失或
    name/id 列无法确认）→ True（第十轮评审 P2②，TS 指定 name 过滤时明确失败而非把
    「取不到群名」冒充「无匹配」；无 R: 会话恒 False；session 可用但个别行无命中不标）。"""
    names = {}
    for cid in conv_ids:
        names[cid] = {'name': None, 'match_names': []} if cid.startswith('S:') else None
    session_unavailable = False
    r_ids = sorted({c for c in conv_ids if c.startswith('R:')})
    s_ids = sorted({c for c in conv_ids if c.startswith('S:')})
    if r_ids:
        path = _resolve_dec_db(workdir, 'session')
        if path is None:
            session_unavailable = True
        else:
            conn = _open_ro(path)
            try:
                columns = _table_columns(conn, 'conversation_table')
                id_col = (_find_conversation_id_column(conn, 'conversation_table', columns)
                          if 'name' in columns else None)
                if id_col is None:
                    session_unavailable = True
                else:
                    ph = ','.join('?' * len(r_ids))
                    for cid, name in conn.execute(
                            'SELECT %s, %s FROM conversation_table WHERE %s IN (%s)'
                            % (_qi(id_col), _qi('name'), _qi(id_col), ph), r_ids).fetchall():
                        if isinstance(cid, str) and isinstance(name, str) and name:
                            names[cid] = name
            finally:
                conn.close()
    if s_ids:
        path = _resolve_dec_db(workdir, 'user')
        if path is not None:
            conn = _open_ro(path)
            try:
                columns = _table_columns(conn, 'user_table')
                name_cols = [c for c in _USER_NAME_COLUMN_CANDIDATES if c in columns]
                if 'id' in columns and name_cols:
                    wanted = sorted({p for c in s_ids
                                     for p in [_peer_id_of(c, account)] if p is not None})
                    found = {}
                    if wanted:
                        ph = ','.join('?' * len(wanted))
                        select = ', '.join([_qi('id')] + [_qi(c) for c in name_cols])
                        for row in conn.execute(
                                'SELECT %s FROM user_table WHERE %s IN (%s)'
                                % (select, _qi('id'), ph), wanted).fetchall():
                            entry = {'name': None, 'match_names': []}
                            for offset, col in enumerate(name_cols, start=1):
                                value = row[offset]
                                if isinstance(value, str) and value:
                                    entry['match_names'].append(value)
                                    if col == 'name':
                                        entry['name'] = value
                            found[str(row[0])] = entry
                    for cid in s_ids:
                        peer = _peer_id_of(cid, account)
                        if peer is not None:
                            found_entry = found.get(str(peer))
                            if found_entry is not None:
                                names[cid] = found_entry
            finally:
                conn.close()
    return names, session_unavailable


def _query_last_per_conv(conn, exclude_extra, workdir=None, account=None, with_names=False):
    """last-per-conv 任务：每会话输出 last_message（无条件最后一条）与 last_participating
    （跳过排除表）。排除表 = CONFIRMED_SYSTEM_TYPES + --exclude-types（追加语义，只扩不缩）；
    全是系统消息 → last_participating=null。方向判定（sender_id 比对本人）在 TS 侧。
    with_names=True 时附 name（反向取名，见 _conversation_display_names；account 必传——
    S: 会话解析对方 id 需要；缺省路径不附 name 键，契约面不变）。S: 行另附 match_names =
    该行所有非空名称字段（名称/别名/备注，第十轮评审 P2①，列表非空才带键）；存在 R: 会话
    且 session 来源不可用 → 顶层 naming_incomplete:true（第十轮评审 P2②；无 R: 会话或
    未要求 names 不标）。"""
    exclude = tuple(sorted(set(CONFIRMED_SYSTEM_TYPES) | set(exclude_extra)))
    cols = ', '.join(QUERY_MESSAGE_COLUMNS)
    last_rows = conn.execute(
        'SELECT %s FROM message_table WHERE message_id IN '
        '(SELECT MAX(message_id) FROM message_table GROUP BY conversation_id) '
        'ORDER BY message_id DESC' % cols).fetchall()
    ph = ','.join('?' * len(exclude))
    part_rows = conn.execute(
        'SELECT %s FROM message_table WHERE content_type NOT IN (%s) AND message_id IN '
        '(SELECT MAX(message_id) FROM message_table WHERE content_type NOT IN (%s) '
        ' GROUP BY conversation_id) ORDER BY message_id DESC' % (cols, ph, ph),
        list(exclude) + list(exclude)).fetchall()
    participating = {}
    for r in part_rows:
        row = _message_row(r)
        participating[row['conversation_id']] = row
    conv_col = QUERY_MESSAGE_COLUMNS.index('conversation_id')
    names, session_unavailable = (
        _conversation_display_names(workdir, [r[conv_col] for r in last_rows], account)
        if with_names and workdir else ({}, False))
    conversations = []
    for r in last_rows:
        row = _message_row(r)
        entry = {
            'conversation_id': row['conversation_id'],
            'last_message': row,
            'last_participating': participating.get(row['conversation_id']),
        }
        if with_names:
            info = names.get(row['conversation_id'])
            if isinstance(info, dict):  # S: 联系人：规范 name + 全部非空名称字段
                entry['name'] = info.get('name')
                if info.get('match_names'):
                    entry['match_names'] = list(info['match_names'])
            else:  # R: 群：name 单值（str 或 None）
                entry['name'] = info
        conversations.append(entry)
    result = {'ok': True, 'task': 'last-per-conv', 'exclude_types': list(exclude),
              'conversations': conversations}
    if with_names and session_unavailable:
        result['naming_incomplete'] = True
    return result


# conversation_table 的 id 列发现：① 字面候选列名优先（PRAGMA 确认存在才用）；
# ② 5.0.12.6018 实证 id TEXT（同表 con_numeric_id 为整数，不能用作会话 ID）；
# ③ 值形态证据——采样文本列，全部非空样本呈 conversation_id 形态（R:/S:/Y: 前缀）→ 认定。
# 都失败 → None（调用方 schema-unknown 降级）。id 列真实列名未经 P0 文档化，不硬编码猜测。
_CONV_ID_NAME_CANDIDATES = ('conversation_id', 'conv_id')
_CONV_ID_VALUE_PREFIXES = ('R:', 'S:', 'Y:')
# user_table 的名字/别名/备注列候选（与 PRAGMA 结果取交集；P0 只实证 id + name）。
_USER_NAME_COLUMN_CANDIDATES = ('name', 'alias', 'nick_name', 'nickname', 'remark', 'remark_name',
                                'display_name', 'display')


def _find_conversation_id_column(conn, table, columns):
    for cand in _CONV_ID_NAME_CANDIDATES:
        if cand in columns:
            return cand
    if 'id' in columns:
        id_info = next((r for r in conn.execute('PRAGMA table_info(%s)' % table)
                        if r[1] == 'id'), None)
        if id_info and id_info[2].strip().upper() == 'TEXT':
            return 'id'  # 同列可包含 AN/AP/CL 等非聊天标识，不能要求所有样本均为 R:/S:/Y:。
    for col in columns:
        if col == 'id':
            continue  # 已实证适配只接受声明 TEXT 的 id，不盲信数值标识或宽松 SQLite 存储。
        sql = 'SELECT %s FROM %s WHERE %s IS NOT NULL LIMIT 20' % (_qi(col), table, _qi(col))
        values = [r[0] for r in conn.execute(sql).fetchall()]
        if values and all(isinstance(v, str) and v.startswith(_CONV_ID_VALUE_PREFIXES)
                          for v in values):
            return col
    return None


def _resolve_conversation_table(conn, name):
    """R: 群名 → conversation_table 精确匹配。name 无唯一索引（P0）：多候选全返回由调用方
    判 ambiguous。列名无法确认 → ('schema-unknown', [])。"""
    columns = _table_columns(conn, 'conversation_table')
    if 'name' not in columns:
        return 'schema-unknown', []
    id_col = _find_conversation_id_column(conn, 'conversation_table', columns)
    if id_col is None:
        return 'schema-unknown', []
    rows = conn.execute('SELECT %s, %s FROM conversation_table WHERE %s = ?'
                        % (_qi(id_col), _qi('name'), _qi('name')), (name,)).fetchall()
    candidates = [{'source': 'conversation_table', 'conversation_id': str(r[0]), 'name': r[1],
                   'matched_column': 'name'} for r in rows]
    return 'ok', candidates


def _corp_identifier(value):
    """只接受已落库的正整数公司标识；空值/浮点/其他形态不能充当内外部证据。"""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return str(value)
    if (isinstance(value, str) and len(value) <= 19 and value.isascii() and
            value.isdigit() and int(value) > 0):
        return str(int(value))
    return None


def _resolve_user_table(conn, name, account=None, external_only=False):
    """人名 → user_table 精确匹配（name/别名/备注列运行时交集）。候选附 user_id（P0 实证
    列 id=消息 sender_id 域）；conversation_id 仅在表内有字面 conversation_id 列时给出，
    否则置 null——单聊会话 id（S:<from>_<to>）需结合本人账号组合，py 侧无法单独决定方向，
    由 TS 消费侧完成（设计 §4.1 定位与消歧）。external_only 以关系及公司证据收紧外部范围，
    不推断微信类型；任一匹配人的证据未知或矛盾都返回 schema-unknown。"""
    columns = _table_columns(conn, 'user_table')
    if 'id' not in columns:
        return 'schema-unknown', []
    match_cols = [c for c in _USER_NAME_COLUMN_CANDIDATES if c in columns]
    if not match_cols:
        return 'schema-unknown', []
    own_corp = None
    if external_only:
        if (not account or 'corp_id' not in columns or
                'user_id' not in _table_columns(conn, 'external_user_relation_v3')):
            return 'schema-unknown', []
        own_rows = conn.execute('SELECT corp_id FROM user_table WHERE CAST(id AS TEXT)=?',
                                (account,)).fetchall()
        if len(own_rows) != 1 or _corp_identifier(own_rows[0][0]) is None:
            return 'schema-unknown', []
        own_corp = _corp_identifier(own_rows[0][0])
    conv_col = 'conversation_id' if 'conversation_id' in columns else None
    seen = set()
    candidates = []
    for col in match_cols:
        select_cols = [_qi('id')] + ([_qi(conv_col)] if conv_col else []) + [_qi(col)]
        if external_only:
            select_cols.extend([_qi('corp_id'),
                                'EXISTS (SELECT 1 FROM external_user_relation_v3 AS er '
                                'WHERE er.user_id = user_table.id)'])
        rows = conn.execute('SELECT %s FROM user_table WHERE %s = ?' % (', '.join(select_cols), _qi(col)),
                            (name,)).fetchall()
        for r in rows:
            if r[0] in seen:
                continue  # 同一行命中多个名字列只出一个候选（按行去重）
            seen.add(r[0])
            if external_only:
                corp = _corp_identifier(r[-2])
                relation = bool(r[-1])
                if corp is None or (corp == own_corp and relation):
                    return 'schema-unknown', []  # 未知或相互矛盾的证据不得消成唯一目标。
                if corp == own_corp:
                    continue  # 同公司且没有外部关系：明确内部证据，才允许排除。
                if not relation:
                    return 'schema-unknown', []  # 不同公司并不足以证明外部身份。
            candidates.append({
                'source': 'user_table',
                'conversation_id': str(r[1]) if conv_col and r[1] is not None else None,
                'user_id': str(r[0]),
                'name': r[2] if conv_col else r[1],
                'matched_column': col,
                **({'contact_scope': 'external'} if external_only else {}),
            })
    return 'ok', candidates


def _query_context(workdir, conn, account):
    """只支持有直接 id 对应证据的本人映射；不猜 wid/公司 id 等其他字段。"""
    path = _resolve_dec_db(workdir, 'user')
    if path is None:
        return {'ok': False, 'error': 'DB_UNREADABLE'}
    user = _open_ro(path)
    try:
        if 'id' not in _table_columns(user, 'user_table'):
            return {'ok': False, 'error': 'SCHEMA_UNSUPPORTED'}
        rows = user.execute('SELECT CAST(id AS TEXT) FROM user_table WHERE CAST(id AS TEXT)=?', (account,)).fetchall()
        if len(rows) != 1:
            return {'ok': False, 'error': 'IDENTITY_UNCONFIRMED'}
        return {'ok': True, 'self_user_id': rows[0][0],
                'schema_version': conn.execute('PRAGMA schema_version').fetchone()[0]}
    finally:
        user.close()


def _query_resolve(workdir, name, account=None, external_only=False):
    """resolve 任务：群名经 session 库、人名经 user 库。对应 .dec.db 缺失 → 该来源标
    unavailable（不报错）；零候选 → ambiguous=false 空列表；多候选 → ambiguous=true 全返回。
    message 解密库在位仍为 workdir 契约（缺失已在 cmd_query 报 DB_UNREADABLE）。
    external_only 只查询联系人；无实际会话的外部同名人仍参与身份消歧。"""
    candidates = []
    sources = {}
    resolvers = [('user_table', 'user', _resolve_user_table)] if external_only else [
        ('conversation_table', 'session', _resolve_conversation_table),
        ('user_table', 'user', _resolve_user_table)]
    for source, logical, resolver in resolvers:
        db_path = _resolve_dec_db(workdir, logical)
        if db_path is None:
            sources[source] = 'unavailable'
            continue
        conn = _open_ro(db_path)
        try:
            status, found = (resolver(conn, name, account, True) if external_only
                             else resolver(conn, name))
        finally:
            conn.close()
        sources[source] = status
        candidates.extend(found)
    if account is not None:
        # 只选实际存在的 S: 会话；不因知道两端用户 id 就虚构一个 conversation_id。
        msg = _open_ro(_resolve_dec_db(workdir, 'message'))
        session_path = _resolve_dec_db(workdir, 'session')
        session = _open_ro(session_path) if session_path else None
        resolved = []
        try:
            id_col = (_find_conversation_id_column(session, 'conversation_table',
                       _table_columns(session, 'conversation_table')) if session else None)
            for item in candidates:
                if item['source'] != 'user_table':
                    resolved.append(item)
                    continue
                ids = ('S:%s_%s' % (account, item['user_id']), 'S:%s_%s' % (item['user_id'], account))
                existing = {str(r[0]) for r in msg.execute(
                    'SELECT DISTINCT conversation_id FROM message_table WHERE conversation_id IN (?,?)', ids)}
                if session and id_col:
                    existing.update(str(r[0]) for r in session.execute(
                        'SELECT %s FROM conversation_table WHERE %s IN (?,?)' % (_qi(id_col), _qi(id_col)), ids))
                if existing:
                    resolved.extend({**item, 'conversation_id': cid} for cid in sorted(existing))
                else:
                    resolved.append({**item, 'conversation_id': None})  # 同名但无历史者仍须参与身份消歧。
            candidates = resolved
        finally:
            msg.close()
            if session:
                session.close()
    return {'ok': True, 'task': 'resolve', 'name': name, 'candidates': candidates,
            'ambiguous': len(candidates) > 1, 'sources': sources}


def _parse_query_args(args):
    """query 参数解析（--workdir/--task 必需；任务相关参数按任务校验）。非法 → None（BAD_ARGS）。"""
    workdir = None
    task = None
    conv = None
    name = None
    after_id = None  # None=首次最近N；显式0仍是增量，不能把空会话游标当首次。
    account = None
    anchor = None
    limit = DEFAULT_QUERY_LIMIT
    since = None
    until = None
    exclude_types = []
    with_names = False
    external_only = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--with-names':
            with_names = True
            i += 1
        elif arg == '--external-only':
            external_only = True
            i += 1
        elif arg in ('--workdir', '--task', '--conv', '--name', '--account', '--anchor') and i + 1 < len(args):
            value = args[i + 1]
            if arg == '--workdir':
                workdir = value
            elif arg == '--task':
                task = value
            elif arg == '--conv':
                conv = value
            elif arg == '--account':
                account = value
            elif arg == '--anchor':
                anchor = value
            else:
                name = value
            i += 2
        elif arg == '--after-id' and i + 1 < len(args):
            try:
                after_id = int(args[i + 1])
            except ValueError:
                return None
            if after_id < 0:
                return None
            i += 2
        elif arg == '--limit' and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                return None
            if limit <= 0:
                return None
            i += 2
        elif arg in ('--since', '--until') and i + 1 < len(args):
            try:
                value = int(args[i + 1])
            except ValueError:
                return None
            if value < 0:
                return None  # 负秒数大概率是单位（ms）误传，显式拒绝
            if arg == '--since':
                since = value
            else:
                until = value
            i += 2
        elif arg == '--exclude-types' and i + 1 < len(args):
            try:
                exclude_types = [int(t) for t in args[i + 1].split(',') if t.strip()]
            except ValueError:
                return None
            if not exclude_types or any(t < 0 for t in exclude_types):
                return None
            i += 2
        else:
            return None
    if not workdir or not task:
        return None
    if task not in ('messages', 'send-messages', 'conversations', 'last-per-conv', 'resolve', 'context',
                    'groups', 'history', 'group-poll'):
        return None
    if task in ('messages', 'send-messages') and not conv:
        return None
    if task == 'resolve' and not name:
        return None
    if external_only and (task != 'resolve' or not account):
        return None
    if task == 'context' and not account:
        return None
    if task == 'last-per-conv' and with_names and not account:
        return None  # S: 会话解析对方 id 需要本人账号（--with-names 必须与 --account 同传）
    if until is not None and task not in ('history', 'send-messages'):
        return None
    if task == 'send-messages':
        if (with_names or exclude_types or limit >= 9223372036854775807
                or any(value is not None and value > 9223372036854775807
                       for value in (after_id, since, until))
                or (since is not None and until is not None and since > until)):
            return None
    if task == 'history':
        if not conv or not conv.startswith('R:') or len(conv) <= 2:
            return None
        if since is not None and until is not None and since > until:
            return None
    if task == 'group-poll':
        if (not conv or not conv.startswith('R:') or len(conv) <= 2 or with_names or exclude_types
                or since is not None or until is not None or limit > 50
                or (after_id is not None and after_id > 9007199254740991)
                or (after_id is None and anchor is not None)
                or (after_id == 0 and anchor != '')
                or (after_id is not None and after_id > 0 and (anchor is None or not re.fullmatch(r'[0-9a-f]{64}', anchor)))):
            return None
    if task in ('groups', 'history'):
        if (after_id is not None or anchor is not None or with_names or exclude_types
                or limit >= 9223372036854775807
                or any(t is not None and t > 9223372036854775807 for t in (since, until))):
            return None
        if task == 'groups' and (conv is not None or since is not None):
            return None
    return (task, workdir, conv, after_id, limit, since, exclude_types, name, account,
            anchor, with_names, until, external_only)


def cmd_sessions(workdir, account, request):
    """One bounded DB snapshot read. Cursor state belongs to the caller, never watch state."""
    if (not isinstance(request, dict) or set(request) - {'targets', 'state', 'limit', 'metadata_only', 'account_dir', 'attachment'}
            or type(request.get('limit', 50)) is not int or not 1 <= request.get('limit', 50) <= 50):
        return {'ok': False, 'error': 'BAD_ARGS'}
    targets = request.get('targets')
    if targets is not None and (not isinstance(targets, list) or not 1 <= len(targets) <= 50):
        return {'ok': False, 'error': 'BAD_ARGS'}
    path = _resolve_dec_db(workdir, 'message')
    if path is None:
        return {'ok': False, 'error': 'DB_UNREADABLE'}
    conn = _open_ro(path)
    try:
        context = _query_context(workdir, conn, account)
        if not context.get('ok') or context.get('self_user_id') != account:
            return {'ok': False, 'error': 'IDENTITY_UNCONFIRMED'}
        cols = ', '.join(QUERY_MESSAGE_COLUMNS)
        known = {r[0] for r in conn.execute('SELECT DISTINCT conversation_id FROM message_table')
                 if isinstance(r[0], str) and (r[0].startswith('R:') or
                    r[0].startswith('S:') and account in r[0][2:].split('_'))}
        session_path = _resolve_dec_db(workdir, 'session')
        if session_path:
            session_conn = _open_ro(session_path)
            try:
                columns = _table_columns(session_conn, 'conversation_table')
                id_col = _find_conversation_id_column(session_conn, 'conversation_table', columns)
                if id_col:
                    known.update(r[0] for r in session_conn.execute('SELECT %s FROM conversation_table' % _qi(id_col))
                                 if isinstance(r[0], str) and (r[0].startswith('R:') or
                                     r[0].startswith('S:') and account in r[0][2:].split('_')))
            finally:
                session_conn.close()
        failed = []
        selected = set()
        state = request.get('state')
        first = state is None
        if first:
            state = {'after_id': 0, 'anchor': '', 'retry': {}, 'unresolved': {}, 'context_after': ''}
        if (not isinstance(state, dict) or set(state) - {'after_id', 'anchor', 'retry', 'unresolved', 'context_after', 'start_fence'}
                or not {'after_id', 'anchor', 'retry', 'unresolved', 'context_after'} <= set(state)
                or type(state.get('after_id')) is not int or state['after_id'] < 0
                or not isinstance(state.get('anchor'), str)
                or state.get('context_after') is not None and not isinstance(state.get('context_after'), str)
                or not isinstance(state.get('retry'), dict) or not isinstance(state.get('unresolved'), dict)):
            return {'ok': False, 'error': 'CURSOR_STALE'}
        state = json.loads(json.dumps(state))
        if 'start_fence' not in state:
            if not first and state['context_after'] is None and state['retry']:
                return {'ok': False, 'error': 'CURSOR_STALE'}
            # Older cursors still in baseline retain the original floor; clean incremental cursors need no replay.
            state['start_fence'] = state['after_id']
        if type(state['start_fence']) is not int or not 0 <= state['start_fence'] <= 9007199254740991:
            return {'ok': False, 'error': 'CURSOR_STALE'}
        def valid_point(point):
            return (isinstance(point, dict) and set(point) == {'after_id', 'anchor'}
                    and type(point.get('after_id')) is int and 0 <= point['after_id'] <= 9007199254740991
                    and isinstance(point.get('anchor'), str)
                    and (point['anchor'] == '' if point['after_id'] == 0 else re.fullmatch(r'[0-9a-f]{64}', point['anchor']) is not None))
        if (not valid_point({'after_id': state['after_id'], 'anchor': state['anchor']})
                or any(not valid_point(point) for point in list(state['retry'].values()) + list(state['unresolved'].values()))):
            return {'ok': False, 'error': 'CURSOR_STALE'}
        after_id = state['after_id']
        if not first and after_id:
            anchor_row = conn.execute('SELECT %s FROM message_table WHERE message_id=?' % cols, (after_id,)).fetchone()
            if anchor_row is None or _message_anchor(_message_row(anchor_row)) != state['anchor']:
                return {'ok': False, 'error': 'CURSOR_STALE'}
        global_latest = conn.execute('SELECT %s FROM message_table ORDER BY message_id DESC LIMIT 1' % cols).fetchone()
        def before_point(message_id):
            previous = conn.execute('SELECT %s FROM message_table WHERE message_id<? ORDER BY message_id DESC LIMIT 1' % cols,
                                    (message_id,)).fetchone()
            return ({'after_id': previous[0], 'anchor': _message_anchor(_message_row(previous))} if previous
                    else {'after_id': 0, 'anchor': ''})
        floor = _message_row(global_latest) if global_latest else None
        floor_point = {'after_id': floor['message_id'] if floor else 0,
                       'anchor': _message_anchor(floor) if floor else ''}
        if first:
            state.update(floor_point)
            state['start_fence'] = floor_point['after_id']
        baseline = state['context_after'] is not None
        if targets is None:
            selected = known
        else:
            for target in targets:
                if not isinstance(target, dict) or set(target) - {'conversation_id', 'name'}:
                    return {'ok': False, 'error': 'BAD_ARGS'}
                key = json.dumps(target, sort_keys=True, ensure_ascii=False)
                cid = target.get('conversation_id')
                reason = None
                if cid is not None:
                    if not isinstance(cid, str) or cid not in known:
                        reason = 'TARGET_NOT_FOUND'
                elif isinstance(target.get('name'), str) and target['name'].strip():
                    name = target['name'].strip()
                    external = name.endswith('@微信')
                    resolved = _query_resolve(workdir, name[:-3].strip() if external else name, account)
                    matching = resolved.get('candidates', [])
                    platform_unknown = False
                    if external:
                        matching = [r for r in matching if r.get('source') == 'user_table']
                        platform = _sender_platforms(workdir, [r['user_id'] for r in matching], account)
                        platform_unknown = any(platform.get(r['user_id']) == 'unknown' for r in matching)
                        matching = [r for r in matching if platform.get(r['user_id']) == 'wechat']
                    candidates = {r.get('conversation_id') for r in matching
                                  if r.get('conversation_id') in known}
                    unknown = any(r.get('conversation_id') is None for r in matching)
                    if len(candidates) == 1 and not unknown:
                        cid = next(iter(candidates))
                    else:
                        reason = 'TARGET_AMBIGUOUS' if len(candidates) + int(unknown) > 1 else 'TARGET_NOT_FOUND'
                    if platform_unknown:
                        reason = 'IDENTITY_UNCONFIRMED'
                    if any(v != 'ok' for k, v in (resolved.get('sources') or {}).items()
                           if k in (('user_table',) if external else ('user_table', 'conversation_table'))):
                        reason = 'LOCALDB_UNAVAILABLE'
                else:
                    return {'ok': False, 'error': 'BAD_ARGS'}
                if reason:
                    failed.append({'target': target, 'code': reason, 'stage': 'resolve', 'effect': 'none'})
                    state['unresolved'].setdefault(key, floor_point if first else {'after_id': after_id, 'anchor': state['anchor']})
                    continue
                selected.add(cid)
                if key in state['unresolved']:
                    state['retry'][cid] = state['unresolved'].pop(key)
        if not selected:
            state['context_after'] = None
            return {'ok': True, 'sessions': [], 'pending_sessions': [], 'failed': failed,
                    'state': state, 'has_more': False, 'baseline': first, 'self_user_id': account,
                    'selected_sessions': []}
        # A failed target owns its own lower bound; advancing other targets cannot lose it.
        for cid, point in state['retry'].items():
            if (not isinstance(cid, str) or not isinstance(point, dict)
                    or type(point.get('after_id')) is not int or point['after_id'] < 0):
                return {'ok': False, 'error': 'CURSOR_STALE'}
            if point['after_id']:
                row = conn.execute('SELECT %s FROM message_table WHERE message_id=?' % cols,
                                   (point['after_id'],)).fetchone()
                if row is None or _message_anchor(_message_row(row)) != point.get('anchor'):
                    return {'ok': False, 'error': 'CURSOR_STALE'}
        selected_list = sorted(selected)
        placeholders = ','.join('?' for _ in selected_list)
        retry_ids = set(state['retry']) & selected
        clauses = []
        params = []
        normal_ids = [cid for cid in selected_list if cid not in retry_ids]
        if normal_ids:
            clauses.append('(conversation_id IN (%s)%s)' % (
                ','.join('?' for _ in normal_ids), '' if first else ' AND message_id>?'))
            params.extend(normal_ids)
            if not first:
                params.append(after_id)
        for cid in sorted(retry_ids):
            clauses.append('(conversation_id=? AND message_id>?)')
            params.extend((cid, state['retry'][cid]['after_id']))
        limit = request.get('limit', 50)
        context_page = []
        history_truncated = False
        if baseline:
            # Empty synchronized sessions have no startup context and must not delay live-message reads.
            context_ids = {r[0] for r in conn.execute(
                'SELECT DISTINCT conversation_id FROM message_table WHERE message_id<=?',
                (state['start_fence'],))}
            remaining = [cid for cid in selected_list if cid in context_ids and cid > state['context_after']]
            context_page = remaining[:min(5, limit)]
            per_session = max(1, limit // max(1, len(context_page)))
            rows = []
            for cid in context_page:
                page = conn.execute('SELECT %s FROM message_table WHERE conversation_id=? AND message_id<=? '
                                    'ORDER BY message_id DESC LIMIT ?' % cols,
                                    (cid, state['after_id'], per_session + 1)).fetchall()
                history_truncated = history_truncated or len(page) > per_session
                rows.extend(reversed(page[:per_session]))
        else:
            rows = conn.execute('SELECT %s FROM message_table WHERE %s ORDER BY message_id ASC LIMIT ?'
                                % (cols, ' OR '.join(clauses)), params + [limit + 1]).fetchall()
        names, _ = _conversation_display_names(workdir, {r[4] for r in rows} | (selected if targets else set()), account)
        details = []
        peer_ids = {_peer_id_of(cid, account) for cid in selected_list if cid.startswith('S:')}
        peer_platforms = _sender_platforms(workdir, peer_ids, account)
        for cid in selected_list if targets else sorted({r[4] for r in rows}):
            named = names.get(cid)
            peer = _peer_id_of(cid, account) if cid.startswith('S:') else None
            details.append({'conversation_id': cid, 'name': named.get('name') if isinstance(named, dict) else named,
                'kind': 'group' if cid.startswith('R:') else 'private',
                'peer_platform': peer_platforms.get(peer, 'unknown') if peer else 'unknown'})
        output = {'ok': True, 'sessions': [], 'pending_sessions': [], 'failed': failed,
                  'state': state, 'has_more': baseline and len(remaining) > len(context_page) or not baseline and len(rows) > limit,
                  'baseline': baseline, 'history_truncated': history_truncated, 'self_user_id': account,
                  'selected_sessions': selected_list, 'session_details': details}
        if request.get('attachment') is not None:
            if request.get('metadata_only') or request.get('state') is not None or len(selected_list) != 1 or failed:
                return {'ok': False, 'error': failed[0]['code'] if failed else 'BAD_ARGS'}
            result = _read_attachment(conn, selected_list[0], request.get('account_dir'), request['attachment'])
            return {**result, 'selected_sessions': selected_list, 'session_details': details}
        if request.get('metadata_only') is True:
            for detail in details:
                if not detail['name']:
                    continue
                found = _query_resolve(workdir, detail['name'], account).get('candidates', [])
                if detail['kind'] == 'group':
                    found = [r for r in found if r.get('source') == 'conversation_table']
                else:
                    found = [r for r in found if r.get('source') == 'user_table']
                    if detail['peer_platform'] == 'wechat':
                        platform = _sender_platforms(workdir, [r['user_id'] for r in found], account)
                        found = [r for r in found if platform.get(r['user_id']) != 'wecom']
                identities = {(r.get('user_id'), r.get('conversation_id')) for r in found}
                if len(identities) > 1:
                    failed.append({'conversation_id': detail['conversation_id'], 'code': 'TARGET_AMBIGUOUS',
                                   'stage': 'resolve', 'effect': 'none'})
            return output
        by_conv = {}
        consumed = 0
        stopped_context = None
        context_points = {}
        blocked_context = set()
        parsed_messages, _ = _history_messages(rows[:limit], workdir, account)
        for row, message in zip(rows[:limit], parsed_messages):
            if message.get('content_type') in (101, 102):
                message['attachment'] = _describe_attachment(row[-1], message['content_type'],
                    request.get('account_dir'), message.get('send_time'))
            cid = message['conversation_id']
            message['baseline'] = message['message_id'] <= state['start_fence']
            if cid in blocked_context:
                continue
            named = names.get(cid)
            name = named.get('name') if isinstance(named, dict) else named
            session = by_conv.get(cid)
            if session is None:
                session = {'conversation_id': cid, 'name': name, 'kind': 'group' if cid.startswith('R:') else 'private',
                           'messages': []}
                by_conv[cid] = session
                output['sessions'].append(session)
            session['messages'].append(message)
            # Internal enumeration metadata is not part of the bounded business result.
            budget = {key: value for key, value in output.items()
                      if key not in ('selected_sessions', 'session_details')}
            if len(json.dumps(budget, ensure_ascii=False).encode('utf-8')) > 15000:
                session['messages'].pop()
                if not session['messages']:
                    output['sessions'].remove(session)
                    by_conv.pop(cid, None)
                if not consumed or baseline and len(json.dumps(message, ensure_ascii=False).encode('utf-8')) > 12000:
                    output['failed'].append({'conversation_id': cid, 'message_id': message['message_id'],
                        'code': 'CONTENT_UNAVAILABLE', 'stage': 'read', 'effect': 'none'})
                    state['retry'].setdefault(cid, before_point(message['message_id']) if baseline else
                                              {'after_id': state['after_id'], 'anchor': state['anchor']})
                    if baseline:
                        blocked_context.add(cid)
                    if cid not in retry_ids and not baseline:
                        state['after_id'], state['anchor'] = message['message_id'], _message_anchor(_message_row(row))
                    continue
                output['has_more'] = True
                stopped_context = cid
                if baseline:
                    state['retry'].setdefault(cid, context_points.get(cid) or before_point(message['message_id']))
                break
            consumed += 1
            if baseline:
                context_points[cid] = {'after_id': message['message_id'], 'anchor': _message_anchor(_message_row(row))}
            if not baseline:
                point = {'after_id': message['message_id'], 'anchor': _message_anchor(_message_row(row))}
                if cid in state['retry']:
                    state['retry'][cid] = point
                elif message['message_id'] > state['after_id']:
                    state.update(point)
        if baseline:
            completed_context = [cid for cid in context_page if stopped_context is None or cid <= stopped_context]
            if completed_context:
                state['context_after'] = completed_context[-1]
            if not remaining or completed_context and completed_context[-1] == remaining[-1]:
                state['context_after'] = None
            output['has_more'] = state['context_after'] is not None or bool(state['retry'])
        else:
            for cid in list(state['retry']):
                point = state['retry'][cid]
                if cid in selected and conn.execute(
                        'SELECT 1 FROM message_table WHERE conversation_id=? AND message_id>? AND message_id<=? LIMIT 1',
                        (cid, point['after_id'], state['after_id'])).fetchone() is None:
                    state['retry'].pop(cid)
        for session in output['sessions']:
            last_self = max((m['message_id'] for m in session['messages']
                             if m.get('sender_id') == account
                             and m.get('content_type') not in CONFIRMED_SYSTEM_TYPES), default=-1)
            candidates = [m['message_id'] for m in session['messages']
                          if m.get('sender_id') != account and m.get('sender_platform') == 'wechat'
                          and (session['kind'] == 'group' or m['message_id'] > last_self)
                          and m.get('content_type') not in CONFIRMED_SYSTEM_TYPES]
            unknown = [m['message_id'] for m in session['messages']
                       if m.get('sender_id') != account and m.get('sender_platform') == 'unknown'
                       and (session['kind'] == 'group' or m['message_id'] > last_self)
                       and m.get('content_type') not in CONFIRMED_SYSTEM_TYPES]
            if candidates or unknown:
                output['pending_sessions'].append({'conversation_id': session['conversation_id'],
                    'message_ids': candidates, 'unknown_sender_message_ids': unknown,
                    'baseline': all(mid <= state['start_fence'] for mid in candidates + unknown),
                    'new_message_ids': [mid for mid in candidates if mid > state['start_fence']],
                    'history_message_ids': [mid for mid in candidates if mid <= state['start_fence']],
                    'unknown_messages': [{'msg_id': m['message_id'], 'sender_id': m['sender_id'],
                                          'baseline': m['baseline'],
                                          'recovery_point': before_point(m['message_id'])}
                                         for m in session['messages'] if m['message_id'] in unknown],
                    'basis': ('history_customer_messages' if all(mid <= state['start_fence'] for mid in candidates)
                              else 'new_customer_messages') if candidates else 'sender_platform_unknown'})
        return output
    except sqlite3.OperationalError as error:
        return {'ok': False, 'error': 'SCHEMA_UNSUPPORTED' if 'no such table' in str(error) or 'no such column' in str(error) else 'SQL_ERROR'}
    except sqlite3.Error:
        return {'ok': False, 'error': 'DB_UNREADABLE'}
    finally:
        conn.close()


def cmd_query(args):
    """query 模式（C8）：参数解析 → 只读打开解密库 → 任务分发 → 结果 JSON。
    库缺失/打开失败 → DB_UNREADABLE；缺表/缺列（schema 不兼容）→ SCHEMA_UNSUPPORTED
    （稳定分类，评审 v6：doctor 据此归 compat，不混入 SQL_ERROR 的 transient 语义）；
    其他 SQL 错误 → SQL_ERROR；均不抛（协议契约）。"""
    parsed = _parse_query_args(args)
    if parsed is None:
        return {'ok': False, 'error': 'BAD_ARGS', 'message': QUERY_USAGE}
    (task, workdir, conv, after_id, limit, since, exclude_extra, name, account,
     anchor, with_names, until, external_only) = parsed
    logical_db = 'session' if task == 'groups' else 'message'
    query_db = _resolve_dec_db(workdir, logical_db)
    if query_db is None:
        return {'ok': False, 'error': 'DB_UNREADABLE',
                'detail': 'workdir 内未找到 %s 解密库（%s.db.dec.db 或 %s.dec.db）：%s'
                          % (logical_db, logical_db, logical_db, workdir)}
    try:
        conn = _open_ro(query_db)
    except sqlite3.Error as e:
        return {'ok': False, 'error': 'DB_UNREADABLE', 'detail': '解密库只读打开失败：%s' % e}
    try:
        if task == 'messages':
            data = _query_messages(conn, conv, after_id, limit, since, anchor)
            if data.get('ok'):
                _annotate_senders(data.get('messages', []), workdir, account)
            return data
        if task == 'send-messages':
            data = _query_messages(conn, conv, after_id, limit, since, anchor,
                                   until=until, send_projection=True)
            if data.get('ok'):
                _annotate_senders(data.get('messages', []), workdir, account)
            return data
        if task == 'groups':
            return _query_groups(conn, name, limit)
        if task == 'history':
            return _query_history(conn, conv, limit, since, until, workdir, account)
        if task == 'group-poll':
            return _query_group_poll(conn, conv, after_id, limit, anchor, workdir, account)
        if task == 'context':
            return _query_context(workdir, conn, account)
        if task == 'conversations':
            return _query_conversations(conn)
        if task == 'last-per-conv':
            return _query_last_per_conv(conn, exclude_extra, workdir, account, with_names)
        return _query_resolve(workdir, name, account, external_only)  # task == 'resolve'
    except sqlite3.OperationalError as e:
        # 缺表/缺列（sqlite3.OperationalError "no such table/column"）= 真实 schema 不支持：
        # 返回稳定错误码 SCHEMA_UNSUPPORTED（评审 v6），detail 保留原始消息（sqlite 消息
        # 自带缺失表/列名，如 "no such table: message_table"）
        msg = str(e)
        if 'no such table' in msg or 'no such column' in msg:
            return {'ok': False, 'error': 'SCHEMA_UNSUPPORTED',
                    'detail': '解密库 schema 缺少查询所需表/列：%s' % e}
        return {'ok': False, 'error': 'SQL_ERROR', 'detail': '%s: %s' % (type(e).__name__, e)}
    except sqlite3.Error as e:
        return {'ok': False, 'error': 'SQL_ERROR', 'detail': '%s: %s' % (type(e).__name__, e)}
    finally:
        conn.close()


# ---------- watch 模式（P2 任务 3.2：watch_poll 本地库通道的多会话增量查询） ----------

WATCH_USAGE = ('用法：wecom_localdb.py watch --workdir <dir> --watermarks <json文件路径> '
               '[--account <已核实本人ID>] [--limit N] [--exclude-types <t[,t…]>]')


def _load_watermarks(path):
    """水位文件 → {conversation_id: (last_message_id, anchor)}；读取失败/结构非法 → None（BAD_ARGS）。
    JSON 形态（TS 侧 DB 水位存储的导出，第九轮评审 P1 起每条带锚点）：
      {"<conversation_id>": <非负整数 message_id> |
                            {"last_message_id": <非负整数>, "anchor": "<消息摘要，缺省 ''>"}, ...}
    整数形态为旧桥兼容（anchor=''）；非零水位携带 anchor 才能通过 watch 的锚点有效性校验。"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    marks = {}
    for conv, value in raw.items():
        if not isinstance(conv, str) or not conv:
            return None
        if isinstance(value, dict):
            mid = value.get('last_message_id')
            anchor = value.get('anchor', '')
            if isinstance(anchor, bool) or not isinstance(anchor, str):
                return None
        else:
            mid, anchor = value, ''
        if isinstance(mid, bool) or not isinstance(mid, int) or mid < 0:
            return None
        marks[conv] = (mid, anchor)
    return marks


def _query_watch(conn, watermarks, limit, account, exclude_extra):
    """多会话增量（每会话独立 limit；只取 message_id > 水位、按入库序 message_id 升序）。
    - 锚点有效性校验（第九轮评审 P1）：非零水位先 SELECT 该 message_id 行，计算
      _message_anchor（与 read-session 游标锚同一函数，排除 server_id/sequence）与存储
      anchor 比对——不匹配或行已不存在 → 旧水位不可信（同文件重写可原地重生更小 id 且
      fileIdentity/schema_version 不变，TS 代际校验不可见）：按 0 处理（返回该会话当前
      全部行）并在该会话输出 watermark_reset:true，绝不静默漏掉重生的早期 id；
    - last_message_id = 该会话当前最大 message_id（整表末端，不是返回集末端——has_more
      截断时两者不同，区分报表语义与水位语义）；
    - next_watermarks 只包含返回过消息的会话、值 = 已返回消息的最大 message_id（水位只
      推进到已返回记录；不读/无新消息的会话不出现，水位不推进）；
    - next_anchor = 已返回消息末端行的消息摘要（TS 存入水位，供下轮锚点校验）；
    - last_sender_state：该会话最后一条可参与回复判定消息（排除表 = CONFIRMED_SYSTEM_TYPES
      + --exclude-types 追加；尾部系统消息不顶掉判定）的 sender 相对 --account 判向
      self/peer；会话全是系统类 / 未传 --account → null；
    - has_more：水位之后仍有未返回消息（total > len(messages)）；
    - 水位指向的会话在表中无任何行（账号切换残留水位等）→ 不出现在输出与
      next_watermarks（水位不推进，基线决策归调用方）。"""
    exclude = tuple(sorted(set(CONFIRMED_SYSTEM_TYPES) | set(exclude_extra)))
    cols = ', '.join(QUERY_MESSAGE_COLUMNS)
    ph = ','.join('?' * len(exclude))
    per_conversation = []
    next_watermarks = {}
    for conv in sorted(watermarks):
        after_id, stored_anchor = watermarks[conv]
        watermark_reset = False
        if after_id > 0:
            previous = conn.execute(
                'SELECT %s FROM message_table WHERE message_id=? AND conversation_id=?' % cols,
                (after_id, conv)).fetchone()
            if previous is None or _message_anchor(_message_row(previous)) != stored_anchor:
                after_id = 0  # 水位失效：按 0 全量重报（宁重报不漏报），标注由调用方透传消费侧
                watermark_reset = True
        rows = conn.execute(
            'SELECT %s FROM message_table WHERE conversation_id = ? AND message_id > ? '
            'ORDER BY message_id ASC LIMIT ?' % cols, (conv, after_id, limit)).fetchall()
        if not rows:
            continue
        messages = [_message_row(r) for r in rows]
        total_new = conn.execute(
            'SELECT COUNT(*) FROM message_table WHERE conversation_id = ? AND message_id > ?',
            (conv, after_id)).fetchone()[0]
        max_id = conn.execute(
            'SELECT MAX(message_id) FROM message_table WHERE conversation_id = ?',
            (conv,)).fetchone()[0]
        part = conn.execute(
            'SELECT sender_id FROM message_table WHERE conversation_id = ? '
            'AND content_type NOT IN (%s) ORDER BY message_id DESC LIMIT 1' % ph,
            tuple([conv] + list(exclude))).fetchone()
        if account is not None and part is not None and part[0] is not None:
            state = 'self' if str(part[0]) == str(account) else 'peer'
        else:
            state = None
        per_conversation.append({
            'conversation_id': conv,
            'messages': messages,
            'last_message_id': max_id,
            'last_sender_state': state,
            'has_more': total_new > len(messages),
            **({'watermark_reset': True} if watermark_reset else {}),
            'next_anchor': _message_anchor(messages[-1]) if messages else '',
        })
        next_watermarks[conv] = messages[-1]['message_id']
    return {'ok': True, 'task': 'watch', 'per_conversation': per_conversation,
            'next_watermarks': next_watermarks}


def _parse_watch_args(args):
    """watch 参数解析：--workdir/--watermarks 必需；--account/--limit/--exclude-types 可选。
    非法 → None（BAD_ARGS）。"""
    workdir = None
    watermarks_path = None
    account = None
    limit = DEFAULT_QUERY_LIMIT
    exclude_types = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ('--workdir', '--watermarks', '--account') and i + 1 < len(args):
            value = args[i + 1]
            if arg == '--workdir':
                workdir = value
            elif arg == '--watermarks':
                watermarks_path = value
            else:
                account = value
            i += 2
        elif arg == '--limit' and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                return None
            if limit <= 0:
                return None
            i += 2
        elif arg == '--exclude-types' and i + 1 < len(args):
            try:
                exclude_types = [int(t) for t in args[i + 1].split(',') if t.strip()]
            except ValueError:
                return None
            if not exclude_types or any(t < 0 for t in exclude_types):
                return None
            i += 2
        else:
            return None
    if not workdir or not watermarks_path:
        return None
    watermarks = _load_watermarks(watermarks_path)
    if watermarks is None:
        return None
    return workdir, watermarks, account, limit, exclude_types


def cmd_watch(args):
    """watch 模式（P2 任务 3.2）：多会话增量查询（与 query 同协议：错误 JSON 不抛、退出码 0）。
    解密库缺失/打开失败 → DB_UNREADABLE；schema 不兼容（缺表/缺列）→ SCHEMA_UNSUPPORTED；
    其他 SQL 错误 → SQL_ERROR。本模式无密钥参与，输出不走兜底脱敏（业务正文同 query）。"""
    parsed = _parse_watch_args(args)
    if parsed is None:
        return {'ok': False, 'error': 'BAD_ARGS', 'message': WATCH_USAGE}
    workdir, watermarks, account, limit, exclude_extra = parsed
    msg_db = _resolve_dec_db(workdir, 'message')
    if msg_db is None:
        return {'ok': False, 'error': 'DB_UNREADABLE',
                'detail': 'workdir 内未找到 message 解密库（message.db.dec.db 或 message.dec.db）：%s'
                          % workdir}
    try:
        conn = _open_ro(msg_db)
    except sqlite3.Error as e:
        return {'ok': False, 'error': 'DB_UNREADABLE', 'detail': '解密库只读打开失败：%s' % e}
    try:
        data = _query_watch(conn, watermarks, limit, account, exclude_extra)
        if data.get('ok'):
            for item in data.get('per_conversation', []):
                _annotate_senders(item.get('messages', []), workdir, account)
        return data
    except sqlite3.OperationalError as e:
        msg = str(e)
        if 'no such table' in msg or 'no such column' in msg:
            return {'ok': False, 'error': 'SCHEMA_UNSUPPORTED',
                    'detail': '解密库 schema 缺少查询所需表/列：%s' % e}
        return {'ok': False, 'error': 'SQL_ERROR', 'detail': '%s: %s' % (type(e).__name__, e)}
    except sqlite3.Error as e:
        return {'ok': False, 'error': 'SQL_ERROR', 'detail': '%s: %s' % (type(e).__name__, e)}
    finally:
        conn.close()


def _account_from_message_paths(pid, paths):
    """只接受目标进程当前打开的唯一消息主库；历史目录和其他库不构成身份证据。"""
    import ntpath
    candidates = [p for p in paths if re.search(r'\\\d+\\Data\\message\.db$', p, re.IGNORECASE)]
    if any(not re.match(r'^[A-Za-z]:\\', p) for p in candidates):
        return {'ok': False, 'error': 'ACCOUNT_UNAVAILABLE'}
    matches = {ntpath.normcase(ntpath.normpath(p)): p for p in candidates}
    if len(matches) != 1:
        return {'ok': False, 'error': 'ACCOUNT_AMBIGUOUS' if matches else 'ACCOUNT_UNAVAILABLE'}
    return {'ok': True, 'pid': pid, 'message_db': next(iter(matches.values()))}


def cmd_active_account(pid):
    """只查询操作主窗口所属 WXWork 的磁盘句柄路径，不读库内容/进程内存，不提权。
    持有进程句柄直到枚举结束；任一文件路径无法确认或进程已退出均不返回部分证据。
    ProcessHandleInformation (51) 避免扫描系统全部进程的句柄。
    """
    if sys.platform != 'win32':
        return {'ok': False, 'error': 'WINDOWS_REQUIRED'}
    import ctypes as c
    import msvcrt
    from ctypes import wintypes as w
    k = c.WinDLL('kernel32', use_last_error=True)
    nt = c.WinDLL('ntdll')
    class Entry(c.Structure):
        _fields_ = [('handle', w.HANDLE), ('handles', c.c_size_t), ('pointers', c.c_size_t),
                    ('access', w.ULONG), ('type', w.ULONG), ('attrs', w.ULONG), ('reserved', w.ULONG)]
    k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.OpenProcess.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    k.GetCurrentProcess.restype = w.HANDLE
    k.DuplicateHandle.argtypes = [w.HANDLE, w.HANDLE, w.HANDLE, c.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD]
    k.GetFileType.argtypes = [w.HANDLE]
    k.GetFinalPathNameByHandleW.argtypes = [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD]
    k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
    k.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    nt.NtQueryInformationProcess.argtypes = [w.HANDLE, w.ULONG, c.c_void_p, w.ULONG, c.POINTER(w.ULONG)]
    proc = k.OpenProcess(0x440, False, pid)  # QUERY_INFORMATION | DUP_HANDLE
    if not proc:
        return {'ok': False, 'error': 'HANDLE_QUERY_FAILED'}
    try:
        image = c.create_unicode_buffer(32768)
        length = w.DWORD(len(image))
        if not k.QueryFullProcessImageNameW(proc, 0, image, c.byref(length)) or os.path.basename(image.value).lower() != 'wxwork.exe':
            return {'ok': False, 'error': 'PROCESS_MISMATCH'}
        def snapshot(process_handle):
            size = 65536
            while size <= 64 * 1024 * 1024:
                buf = c.create_string_buffer(size)
                needed = w.ULONG()
                status = nt.NtQueryInformationProcess(process_handle, 51, buf, size, c.byref(needed))
                if status == 0:
                    count = c.c_size_t.from_buffer(buf).value
                    offset = 2 * c.sizeof(c.c_size_t)
                    if offset + count * c.sizeof(Entry) > size:
                        break
                    return [Entry.from_buffer_copy(buf, offset + i * c.sizeof(Entry)) for i in range(count)]
                if status not in (-1073741820, -1073741789):
                    break
                size = max(size * 2, needed.value)
            raise OSError('HANDLE_QUERY_FAILED')
        # 对象类型索引由本机运行态取得，不硬编码；非文件对象有些不能 DuplicateHandle。
        with open(__file__, 'rb') as own_file:
            own_handle = msvcrt.get_osfhandle(own_file.fileno())
            file_type = next(e.type for e in snapshot(k.GetCurrentProcess()) if e.handle == own_handle)
        paths = []
        for entry in snapshot(proc):
            if entry.type != file_type:
                continue
            handle = w.HANDLE()
            if not k.DuplicateHandle(proc, entry.handle, k.GetCurrentProcess(), c.byref(handle), 0, False, 2):
                return {'ok': False, 'error': 'HANDLE_QUERY_FAILED'}
            try:
                c.set_last_error(0)
                file_kind = k.GetFileType(handle)
                if file_kind == 0 and c.get_last_error() != 0:
                    return {'ok': False, 'error': 'HANDLE_QUERY_FAILED'}
                if file_kind != 1:  # FILE_TYPE_DISK；跳过管道，避免路径查询阻塞
                    continue
                path = c.create_unicode_buffer(32768)
                n = k.GetFinalPathNameByHandleW(handle, path, len(path), 0)
                if not n or n >= len(path):
                    return {'ok': False, 'error': 'HANDLE_QUERY_FAILED'}
                value = path.value
                if value.startswith('\\\\?\\UNC\\'):
                    value = '\\\\' + value[8:]
                elif value.startswith('\\\\?\\'):
                    value = value[4:]
                if re.search(r'\\\d+\\Data\\message\.db$', value, re.IGNORECASE) and not os.path.isfile(value):
                    return {'ok': False, 'error': 'HANDLE_QUERY_FAILED'}
                paths.append(value)
            finally:
                k.CloseHandle(handle)
        exit_code = w.DWORD()
        if not k.GetExitCodeProcess(proc, c.byref(exit_code)) or exit_code.value != 259:
            return {'ok': False, 'error': 'PROCESS_EXITED'}
        return _account_from_message_paths(pid, paths)
    finally:
        k.CloseHandle(proc)


# ---------- 入口（argv 分发；任何错误也输出 JSON 且退出码 0） ----------

def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == 'active-account' and args[1].isdigit() and int(args[1]) > 0:
        emit(cmd_active_account(int(args[1])))
        return
    if len(args) == 2 and args[0] == 'snapshot-verify':
        emit(cmd_snapshot_verify(args[1]))
        return
    if args and args[0] == 'query':
        emit(cmd_query(args[1:]), redact=False)  # query 无密钥参与，业务文本不走兜底脱敏
        return
    if len(args) == 5 and args[0] == 'sessions' and args[1] == '--workdir' and args[3] == '--account':
        raw = sys.stdin.buffer.read(65537).decode('utf-8')
        try:
            request = json.loads(raw) if len(raw) <= 65536 else None
        except ValueError:
            request = None
        emit(cmd_sessions(args[2], args[4], request), redact=False)
        return
    if args and args[0] == 'watch':
        emit(cmd_watch(args[1:]), redact=False)  # watch 同 query：业务文本不走兜底脱敏
        return
    if args and args[0] == 'keyscan':
        parsed = _parse_keyscan_args(args[1:])
        if parsed is None:
            emit({'ok': False, 'error': 'BAD_ARGS',
                  'message': '用法：wecom_localdb.py keyscan --pids <pid[,pid…]> --db <页1源库副本> '
                             '[--max-region-bytes N] [--aligned-first|--full]'})
            return
        pid_list, db, max_region, full = parsed
        emit(cmd_keyscan(pid_list, db, max_region, full), keep_hex_fields=('key_hex',))
        return
    if len(args) == 4 and args[0] == 'decrypt':
        db_path, wal_arg, outdir = args[1], args[2], args[3]
        wal_path = None if wal_arg in ('-', '') else wal_arg
        key_text = sys.stdin.read()
        try:
            mkey = bytes.fromhex(''.join(key_text.split()))
        except ValueError:
            emit({'ok': False, 'error': 'BAD_KEY', 'detail': 'stdin 密钥不是合法 hex'})
            return
        if len(mkey) != 16:
            emit({'ok': False, 'error': 'BAD_KEY',
                  'detail': '密钥长度须为 16 字节（wxaes128 主密钥），实际 %d 字节' % len(mkey)})
            return
        emit(cmd_decrypt(db_path, wal_path, outdir, mkey))
        return
    emit({'ok': False, 'error': 'BAD_ARGS',
          'message': '用法：wecom_localdb.py snapshot-verify <wal> | decrypt <db> <wal|-> <outdir>'
                     '（密钥经 stdin 传 hex） | keyscan --pids <pid[,pid…]> --db <页1源库副本>'
                     ' [--max-region-bytes N] [--aligned-first|--full]'
                     ' | query --workdir <dir> --task messages|conversations|last-per-conv|resolve'
                     ' [参数见 QUERY_USAGE]'
                     ' | watch --workdir <dir> --watermarks <json文件路径>'
                     ' [--account <id>] [--limit N] [--exclude-types <t[,t…]>]'})


if __name__ == '__main__':
    try:
        main()
    except Exception as e:  # noqa: BLE001 — 协议兜底：任何未预期异常也按 JSON 协议输出（退出码 0）
        try:
            emit({'ok': False, 'error': 'INTERNAL_ERROR',
                  'message': '%s: %s' % (type(e).__name__, e)})
        except Exception:
            pass
