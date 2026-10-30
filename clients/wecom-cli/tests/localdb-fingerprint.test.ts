import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { checkDbFingerprint, checkExeConstants, DB_HEADER_READ_BYTES, EXE_LCG_NEEDLE, EXE_SALT_NEEDLE, parseDbHeader } from '../src/platform/localdb/fingerprint.js'

/** 矩阵 5.0.11.6029 条目的指纹参数（version-matrix.json） */
const EXPECTED = { db_magic: 'b3df88da44b8102f', page_size: 4096 }
/** 明文页头期望字节：10 00（页大小 4096 大端）02 02（WAL 读写版本）00（保留）00 40 20 20（payload 分数） */
const PLAINTEXT_HEADER_4096 = '1000020200402020'

/** 造一个最小加密库头（魔数 8B + 任意 8B + 明文页头 8B + 余量）；total 不足时只写放得下的字段（截短 fixture） */
function makeHeader(opts: { magic?: string; plaintext?: string; total?: number } = {}): Buffer {
  const buf = Buffer.alloc(opts.total ?? 128)
  buf.write(opts.magic ?? EXPECTED.db_magic, 0, 8, 'hex')
  if (buf.length >= 24) buf.write(opts.plaintext ?? PLAINTEXT_HEADER_4096, 16, 8, 'hex')
  return buf
}

test('parseDbHeader：完整头部提取魔数/明文页头/页大小（65536 特殊编码还原为单独用例）', () => {
  const parsed = parseDbHeader(makeHeader())
  assert.equal(parsed.magic, EXPECTED.db_magic)
  assert.equal(parsed.plaintext_header, PLAINTEXT_HEADER_4096)
  assert.equal(parsed.page_size, 4096)
  // 65536 的 16 位编码为 0x0001（SQLite 头字段约定）
  const big = parseDbHeader(makeHeader({ plaintext: '0001020200402020' }))
  assert.equal(big.page_size, 65536)
})

test('parseDbHeader：截短输入按可得字段尽力解析，不可得字段落 null（不猜）', () => {
  assert.deepEqual(parseDbHeader(Buffer.alloc(0)), { magic: null, plaintext_header: null, page_size: null })
  const onlyMagic = parseDbHeader(makeHeader({ total: 10 }))
  assert.equal(onlyMagic.magic, EXPECTED.db_magic)
  assert.equal(onlyMagic.plaintext_header, null)
  assert.equal(onlyMagic.page_size, null) // [16:18] 不可得
  const noPage = parseDbHeader(makeHeader({ total: 17 }))
  assert.equal(noPage.plaintext_header, null)
  assert.equal(noPage.page_size, null)
})

test('checkDbFingerprint：魔数 + 明文页头均匹配 → match=true（Buffer 直传入参）', () => {
  const result = checkDbFingerprint(makeHeader({ total: DB_HEADER_READ_BYTES }), EXPECTED)
  assert.equal(result.match, true)
  assert.equal(result.observed.magic, EXPECTED.db_magic)
  assert.equal(result.observed.plaintext_header, PLAINTEXT_HEADER_4096)
})

test('checkDbFingerprint：魔数不匹配 → false + detail 含期望/实际（页头相同也不放行）', () => {
  const result = checkDbFingerprint(makeHeader({ magic: 'deadbeefdeadbeef' }), EXPECTED)
  assert.equal(result.match, false)
  assert.match(result.detail, /魔数不匹配/)
  assert.match(result.detail, /b3df88da44b8102f/)
  assert.equal(result.observed.magic, 'deadbeefdeadbeef')
})

test('checkDbFingerprint：明文页头不匹配（回滚日志版本 01 01）→ false，魔数命中不算 match', () => {
  // journal_mode=DELETE 库的页头 [18:20] = 01 01：WAL 方案特征不符，必须拒绝
  const result = checkDbFingerprint(makeHeader({ plaintext: '1000010100402020' }), EXPECTED)
  assert.equal(result.match, false)
  assert.match(result.detail, /明文页头不匹配/)
  assert.equal(result.observed.plaintext_header, '1000010100402020')
})

test('checkDbFingerprint：页大小期望不同（8192）→ 期望明文页头随之变化，4096 库不匹配', () => {
  const result = checkDbFingerprint(makeHeader(), { db_magic: EXPECTED.db_magic, page_size: 8192 })
  assert.equal(result.match, false)
  assert.match(result.detail, /明文页头不匹配/)
  // 8192 期望页头 = 20 00 02 02 00 40 20 20
  assert.match(result.detail, /2000020200402020/)
})

test('checkDbFingerprint：expected 契约校验（魔数非 16 位 hex / 页大小非 2 的幂）→ false 不抛出', () => {
  assert.equal(checkDbFingerprint(makeHeader(), { db_magic: 'xyz', page_size: 4096 }).match, false)
  assert.match(checkDbFingerprint(makeHeader(), { db_magic: 'xyz', page_size: 4096 }).detail, /db_magic 非法/)
  assert.equal(checkDbFingerprint(makeHeader(), { db_magic: EXPECTED.db_magic, page_size: 3000 }).match, false)
  assert.match(
    checkDbFingerprint(makeHeader(), { db_magic: EXPECTED.db_magic, page_size: 3000 }).detail,
    /page_size 非法/,
  )
})

test('checkDbFingerprint：文件路径 + 缺省 fs，文件不存在/读失败 → false + detail，不抛出', () => {
  const result = checkDbFingerprint('Z:\\不存在的路径\\message.db', EXPECTED)
  assert.equal(result.match, false)
  assert.match(result.detail, /库文件不可读/)
  assert.deepEqual(result.observed, { magic: null, plaintext_header: null })
})

test('checkDbFingerprint：文件过小（<24 字节）→ false + 过小 detail，可得字段照常输出', () => {
  const result = checkDbFingerprint(makeHeader({ total: 12 }), EXPECTED)
  assert.equal(result.match, false)
  assert.match(result.detail, /过小/)
  assert.equal(result.observed.magic, EXPECTED.db_magic)
  assert.equal(result.observed.plaintext_header, null)
})

test('checkDbFingerprint：readHead 注入（内存替身），注入实现抛错按不可读处理', () => {
  const hit = checkDbFingerprint('mem:db', EXPECTED, { readHead: () => makeHeader({ total: 4096 }) })
  assert.equal(hit.match, true)
  // 抛错 = 缺失/无权限（契约与缺省 fs 实现一致）
  const miss = checkDbFingerprint('mem:missing', EXPECTED, {
    readHead: () => {
      throw new Error('ENOENT: no such file')
    },
  })
  assert.equal(miss.match, false)
  assert.match(miss.detail, /ENOENT/)
})

test('checkDbFingerprint：真实临时文件端到端（缺省 fs 读头 4KB 即可判定，不整读）', () => {
  const dir = mkdtempSync(join(tmpdir(), 'fingerprint-'))
  try {
    // 头 4KB + 尾部垃圾（模拟大库文件：判定只看头）
    const file = join(dir, 'message.db')
    writeFileSync(file, Buffer.concat([makeHeader({ total: DB_HEADER_READ_BYTES }), Buffer.alloc(8192, 7)]))
    assert.equal(checkDbFingerprint(file, EXPECTED).match, true)
    rmSync(file)
    assert.equal(checkDbFingerprint(file, EXPECTED).match, false)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('checkExeConstants：sAlT 与 LCG 常量序列命中（注入前缀 Buffer，含 needle 位置无关）', () => {
  const body = Buffer.alloc(1024)
  EXE_SALT_NEEDLE.copy(body, 500)
  EXE_LCG_NEEDLE.copy(body, 900)
  const result = checkExeConstants('mem:exe', {
    statSize: () => 1024,
    readPrefix: () => body,
  })
  assert.deepEqual(result, {
    scanned: true,
    salt_found: true,
    lcg_found: true,
    scanned_bytes: 1024,
    truncated: false,
  })
})

test('checkExeConstants：只有其一命中 / 都不命中 → 对应 false，不抛出', () => {
  const onlySaltBody = Buffer.alloc(100, 1)
  EXE_SALT_NEEDLE.copy(onlySaltBody, 10)
  const onlySalt = checkExeConstants('mem:exe', { statSize: () => 100, readPrefix: () => onlySaltBody })
  assert.equal(onlySalt.salt_found, true)
  assert.equal(onlySalt.lcg_found, false)
  const none = checkExeConstants('mem:exe', { statSize: () => 64, readPrefix: () => Buffer.alloc(64) })
  assert.equal(none.scanned, true)
  assert.equal(none.salt_found, false)
  assert.equal(none.lcg_found, false)
})

test('checkExeConstants：文件超出扫描上限 → truncated=true + scanned_bytes=上限（截断注明）', () => {
  const result = checkExeConstants('mem:exe', {
    statSize: () => 64 * 1024 * 1024, // 64MB 文件
    readPrefix: (_p, len) => Buffer.alloc(len), // 缺省读满窗口
  })
  assert.equal(result.truncated, true)
  assert.equal(result.scanned_bytes, 16 * 1024 * 1024)
  assert.equal(result.error, undefined)
  // 大小不可得 → truncated 无法判定，附说明（诚实边界）
  const noSize = checkExeConstants('mem:exe', { statSize: () => null, readPrefix: () => Buffer.alloc(8) })
  assert.equal(noSize.truncated, false)
  assert.match(noSize.error ?? '', /大小不可得/)
})

test('checkExeConstants：读失败 → scanned=false + error，两个 found 恒 false，不抛出', () => {
  const result = checkExeConstants('mem:missing', {
    statSize: () => null,
    readPrefix: () => {
      throw new Error('EACCES')
    },
  })
  assert.deepEqual(result, {
    scanned: false,
    salt_found: false,
    lcg_found: false,
    scanned_bytes: 0,
    truncated: false,
    error: 'EACCES',
  })
})
