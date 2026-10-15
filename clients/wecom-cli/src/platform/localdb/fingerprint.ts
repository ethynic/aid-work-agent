/**
 * C5 指纹预检：库文件头比对（魔数 + 明文页头）+ 可选 exe 常量指纹（设计 §3.2 C5 / §3.3）。
 *
 * 判定规则（矩阵 db_magic/page_size 字段 + P0 实测：加密页 1 的字节 [16:24] 落地为明文页头）：
 * - 加密库文件头 [0:8] 为算法魔数（5.0.11.6029 = b3df88da44b8102f，hex 比较，大小写不敏感）；
 * - 字节 [16:24] 为【明文】SQLite 页头（该 8 字节不加密存储）：
 *   [16:18]=页大小大端（4096 表示为 10 00；65536 特殊表示 00 01，SQLite 头字段约定）、
 *   [18]=02 [19]=02（WAL 读写版本）、[20]=00（每页保留字节数）、[21:24]=00 40 20 20
 *   （SQLite 三个 payload 分数常量：max 64 / min 32 / leaf 32）；
 * - 魔数与明文页头【都】匹配才算 match：只比魔数会漏掉页参数漂移（页大小/版本位变化），
 *   明文页头是方案绑定特征的一部分；任一不符即指纹漂移 → 兼容性失败方向（§2 ui(kind=compat)）。
 *
 * 头部解析 parseDbHeader 单独导出供 fixture 单测（对齐 datadir.parseCefLog 先例）。
 * 严格只读：只读库文件头 4KB / exe 前缀窗口，不写不改。文件缺失/过小/读失败 → match=false +
 * detail，不抛出（doctor 深检要快速返回结构化结果，对齐 datadir/version 的 best-effort 约定）。
 *
 * 可选 exe 常量指纹 checkExeConstants：【默认关闭】（不在 checkDbFingerprint 内联动），由调用方
 * （runbook §6 常量指纹步骤 / P3 validate 动词）显式启用——best-effort 辅助信号：exe 前缀窗口内
 * "sAlT" 常量与 gen_iv LCG 常量序列（52774/40692/3791/2147483399，小端 u32 顺序拼接）是否仍在，
 * 算法被魔改时常量缺失/漂移。找不到不判死（加壳/分段/编译器内联都可能漏检），命中也不单独构成
 * 兼容结论；文件超出扫描上限截断并在结果注明（truncated + scanned_bytes）。
 * 依赖全部可注入（readHead/statSize/readPrefix），测试无需真实企微文件。
 */
import { closeSync, openSync, readSync, statSync } from 'node:fs'

/** 库指纹判定窗口：头 4KB（任务规格；[0:24] 判定字段都在其中） */
export const DB_HEADER_READ_BYTES = 4096

/** exe 常量指纹扫描上限（前 16MB 窗口；启发式——加密实现通常在主映像前段，全量扫描大 exe 太慢） */
export const EXE_SCAN_MAX_BYTES = 16 * 1024 * 1024

/** "sAlT" 常量（页密钥派生 MD5 输入的盐，73 41 6C 54；见实验 decrypt_wxaes128.py SALT） */
export const EXE_SALT_NEEDLE = Buffer.from([0x73, 0x41, 0x6c, 0x54])

/**
 * gen_iv LCG 常量序列（52774/40692/3791/2147483399，与 decrypt_wxaes128.gen_iv 内出现的
 * 顺序一致，各按小端 u32 拼接为 16 字节连续序列）。编译器可能把常量内联散开——本序列搜不到
 * 只说明"此形态未命中"，不是算法不存在的证明（best-effort，见模块头注释）。
 */
export const EXE_LCG_NEEDLE = Buffer.concat(
  [52774, 40692, 3791, 2147483399].map((v) => {
    const b = Buffer.alloc(4)
    b.writeUInt32LE(v, 0)
    return b
  }),
)

export interface FingerprintDeps {
  /**
   * 读取文件前 length 字节（EOF 截短返回更短 Buffer）；文件缺失/无权限/IO 失败 → 抛错
   * （由 checkDbFingerprint 捕获落 match=false + detail）。缺省为真实 fs 有界读实现。
   */
  readHead?: (path: string, length: number) => Buffer
}

export interface DbFingerprintExpected {
  /** 加密库魔数（8 字节 hex，矩阵 db_magic 字段，如 'b3df88da44b8102f'；大小写不敏感） */
  db_magic: string
  /** 页大小（矩阵 page_size 字段，如 4096；用于推导明文页头 [16:18] 的期望字节） */
  page_size: number
}

export interface DbFingerprintResult {
  /** 魔数与明文页头均匹配（缺一即 false） */
  match: boolean
  /** 判定说明（中文，含期望/实际或失败原因；对齐 running/datadir 的 detail 风格） */
  detail: string
  observed: {
    /** 文件头 [0:8] hex；不足 8 字节/不可读 → null */
    magic: string | null
    /** 明文页头 [16:24] hex；不足 24 字节/不可读 → null */
    plaintext_header: string | null
  }
}

export interface ParsedDbHeader {
  /** 文件头 [0:8] hex；不足 8 字节 → null */
  magic: string | null
  /** 明文页头 [16:24] hex；不足 24 字节 → null */
  plaintext_header: string | null
  /** 明文页头 [16:18] 大端解析出的页大小（1 → 65536 特殊还原）；不足 18 字节 → null */
  page_size: number | null
}

/**
 * 解析库文件头（单独导出供 fixture 单测）。只做字节提取，不做匹配判断；
 * 截短输入按可得字段尽力解析，不可得字段落 null（不猜）。
 */
export function parseDbHeader(head: Buffer): ParsedDbHeader {
  const magic = head.length >= 8 ? head.subarray(0, 8).toString('hex') : null
  const plaintext_header = head.length >= 24 ? head.subarray(16, 24).toString('hex') : null
  let page_size: number | null = null
  if (head.length >= 18) {
    const raw = head.readUInt16BE(16)
    page_size = raw === 1 ? 65536 : raw // SQLite 约定：16 位字段值 1 表示 65536
  }
  return { magic, plaintext_header, page_size }
}

/**
 * 由期望页大小推导明文页头 [16:24] 期望字节；page_size 非法（512-65536 的 2 的幂）→ null。
 * 布局：[16:18]=页大小大端（65536 编码为 0x0001）、[18]=02 [19]=02（WAL 读写版本）、
 * [20]=00（每页保留字节数）、[21:24]=00 40 20 20（payload 分数常量）。
 */
function expectedPlaintextHeader(pageSize: number): Buffer | null {
  if (
    !Number.isInteger(pageSize) ||
    pageSize < 512 ||
    pageSize > 65536 ||
    (pageSize & (pageSize - 1)) !== 0
  ) {
    return null
  }
  const sizeField = pageSize === 65536 ? 1 : pageSize
  return Buffer.from([(sizeField >> 8) & 0xff, sizeField & 0xff, 0x02, 0x02, 0x00, 0x40, 0x20, 0x20])
}

/** 缺省头部读取：真实 fs 只读有界读（ENOENT 等异常向上抛，由调用方捕获） */
function defaultReadHead(path: string, length: number): Buffer {
  const fd = openSync(path, 'r')
  try {
    const buf = Buffer.alloc(length)
    const n = readSync(fd, buf, 0, length, 0)
    return buf.subarray(0, n)
  } finally {
    try {
      closeSync(fd)
    } catch {
      // 关闭失败忽略（只读 fd，不影响结果）
    }
  }
}

/** 文件大小（字节）；不存在/无权限 → null */
function defaultStatSize(path: string): number | null {
  try {
    return statSync(path).size
  } catch {
    return null
  }
}

/**
 * C5 库指纹判定：魔数（hex 比较）+ 明文页头（字节比较）双匹配才算 match。
 * source 二态：Buffer = 已读入的头部字节（前 4KB 或更短）；字符串 = 文件路径（读前 4KB）。
 * 任何失败（expected 非法/文件不可读/过小）→ match=false + detail，不抛出。
 */
export function checkDbFingerprint(
  source: string | Buffer,
  expected: DbFingerprintExpected,
  deps: FingerprintDeps = {},
): DbFingerprintResult {
  const result = (match: boolean, detail: string, magic: string | null, header: string | null): DbFingerprintResult => ({
    match,
    detail,
    observed: { magic, plaintext_header: header },
  })

  // 期望值契约校验：db_magic 需 16 位 hex；page_size 需能推导合法明文页头
  const dbMagic = typeof expected.db_magic === 'string' ? expected.db_magic.trim().toLowerCase() : ''
  if (!/^[0-9a-f]{16}$/.test(dbMagic)) {
    return result(false, `expected.db_magic 非法（需 16 位 hex）：${String(expected.db_magic)}`, null, null)
  }
  const expectHeader = expectedPlaintextHeader(expected.page_size)
  if (expectHeader === null) {
    return result(false, `expected.page_size 非法（512-65536 的 2 的幂）：${String(expected.page_size)}`, null, null)
  }

  let head: Buffer
  if (Buffer.isBuffer(source)) {
    head = source
  } else {
    const readHead = deps.readHead ?? defaultReadHead
    try {
      head = readHead(source, DB_HEADER_READ_BYTES)
    } catch (err) {
      return result(false, `库文件不可读：${err instanceof Error ? err.message : String(err)}`, null, null)
    }
  }

  const parsed = parseDbHeader(head)
  if (parsed.magic === null || parsed.plaintext_header === null) {
    return result(
      false,
      `库文件过小（${head.length} 字节，不足 24 字节判定窗口）`,
      parsed.magic,
      parsed.plaintext_header,
    )
  }

  const expectHex = expectHeader.toString('hex')
  const magicOk = parsed.magic === dbMagic
  const headerOk = parsed.plaintext_header === expectHex
  if (!magicOk && !headerOk) {
    return result(false, `魔数与明文页头均不匹配：期望 ${dbMagic} / ${expectHex}`, parsed.magic, parsed.plaintext_header)
  }
  if (!magicOk) {
    return result(false, `魔数不匹配：期望 ${dbMagic}，实际 ${parsed.magic}`, parsed.magic, parsed.plaintext_header)
  }
  if (!headerOk) {
    return result(false, `明文页头不匹配：期望 ${expectHex}，实际 ${parsed.plaintext_header}`, parsed.magic, parsed.plaintext_header)
  }
  return result(true, '魔数与明文页头均匹配', parsed.magic, parsed.plaintext_header)
}

export interface ExeConstantsDeps {
  /** 文件大小（字节）；不可得 → null（truncated 无法判定，恒 false 并附说明）。缺省 statSync */
  statSize?: (path: string) => number | null
  /** 读取文件前缀（EOF 截短）；缺失/IO 失败 → 抛错（捕获落 scanned=false）。缺省同 defaultReadHead */
  readPrefix?: (path: string, length: number) => Buffer
}

export interface ExeConstantsResult {
  /** 是否实际执行了扫描（false=文件不可读，两个 found 恒 false，error 附原因） */
  scanned: boolean
  /** "sAlT" 常量是否命中 */
  salt_found: boolean
  /** LCG 常量序列是否命中 */
  lcg_found: boolean
  /** 实际扫描字节数（0=未扫描） */
  scanned_bytes: number
  /** 文件超出扫描上限被截断（结果只覆盖前 scanned_bytes 字节） */
  truncated: boolean
  /** 附加说明（大小不可得/读失败原因）；正常缺省 */
  error?: string
}

/**
 * exe 常量指纹（可选，默认关闭——见模块头注释）：在 exe 前缀窗口内搜 "sAlT" 与 LCG 常量序列。
 * best-effort：找不到/读失败不抛出，结果如实呈现（scanned=false 或 found=false + 截断注明）。
 */
export function checkExeConstants(exePath: string, deps: ExeConstantsDeps = {}): ExeConstantsResult {
  const statSize = deps.statSize ?? defaultStatSize
  const readPrefix = deps.readPrefix ?? defaultReadHead
  try {
    const size = statSize(exePath)
    const buf = readPrefix(exePath, EXE_SCAN_MAX_BYTES)
    return {
      scanned: true,
      salt_found: buf.indexOf(EXE_SALT_NEEDLE) !== -1,
      lcg_found: buf.indexOf(EXE_LCG_NEEDLE) !== -1,
      scanned_bytes: buf.length,
      truncated: size !== null && size > buf.length,
      ...(size === null ? { error: '文件大小不可得，truncated 无法判定' } : {}),
    }
  } catch (err) {
    return {
      scanned: false,
      salt_found: false,
      lcg_found: false,
      scanned_bytes: 0,
      truncated: false,
      error: err instanceof Error ? err.message : String(err),
    }
  }
}
