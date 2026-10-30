import test from 'node:test'
import assert from 'node:assert/strict'
import { captureSnapshot, WAL_HEADER_BYTES, type CaptureSnapshotDeps } from '../src/platform/localdb/snapshot.js'

const SRC_DB = '/src/Data/message.db'
const SRC_WAL = '/src/Data/message.db-wal'
const WORK_DIR = '/work'

/** 造假 WAL：合法形态头（magic/version/pagesize/ckptseq/salt）+ N 个几何完整帧；校验和字节置 0（TS 捕获层不做帧校验和语义判定，语义归 py） */
function buildWalBytes(frameCount: number, salt1 = 0x11111111): Buffer {
  const header = Buffer.alloc(WAL_HEADER_BYTES)
  header.writeUInt32BE(0x377f0682, 0)
  header.writeUInt32BE(3007000, 4)
  header.writeUInt32BE(4096, 8)
  header.writeUInt32BE(0, 12)
  header.writeUInt32BE(salt1, 16)
  header.writeUInt32BE(0x22222222, 20)
  const frames = Buffer.alloc(frameCount * (24 + 4096))
  for (let i = 0; i < frameCount; i++) {
    frames.writeUInt32BE(1 + i, i * (24 + 4096)) // 帧头 pgno
  }
  return Buffer.concat([header, frames])
}

interface FakeFsOpts {
  /** 每次写快照后触发（writes 计数从 1 起；写完主库=1，写完 WAL=2）——模拟复制完成后的并发变化 */
  onAfterWrite?: (files: Map<string, Buffer>, writes: number) => void
  /** 每次 readRange 前触发（reads 计数从 1 起）——模拟复制过程中的并发变化 */
  beforeRead?: (path: string, reads: number) => void
  writeFails?: boolean
}

/** 内存假 fs（可注入并发变化钩子）；路径用 '/' 拼接便于断言 */
function fakeFs(opts: FakeFsOpts = {}): { files: Map<string, Buffer>; deps: CaptureSnapshotDeps } {
  const files = new Map<string, Buffer>()
  let reads = 0
  let writes = 0
  const deps: CaptureSnapshotDeps = {
    statSize: (p) => files.get(p)?.length ?? null,
    readRange: (p, off, len) => {
      reads++
      opts.beforeRead?.(p, reads)
      const b = files.get(p)
      if (b === undefined) return null
      return b.subarray(off, Math.min(off + len, b.length))
    },
    writeFile: (p, data) => {
      writes++
      if (opts.writeFails) return false
      files.set(p, Buffer.from(data))
      opts.onAfterWrite?.(files, writes)
      return true
    },
    joinPath: (...parts) => parts.join('/'),
  }
  return { files, deps }
}

test('captureSnapshot：正常路径 → ok + 快照内容与源一致 + h0/h1 头证据 + sizes（代表性断言合并）', async () => {
  const db = Buffer.alloc(4096 * 3, 7)
  const wal = buildWalBytes(2)
  const { files, deps } = fakeFs()
  files.set(SRC_DB, db)
  files.set(SRC_WAL, wal)

  const result = await captureSnapshot(SRC_DB, SRC_WAL, WORK_DIR, deps)
  assert.equal(result.ok, true)
  if (!result.ok) return // 类型收窄
  assert.equal(result.dbSnapshot, '/work/message.db')
  assert.equal(result.walSnapshot, '/work/message.db-wal')
  // 快照内容 = 源文件前缀（主库恰 D0 字节、WAL 按复制时刻大小）
  const snapDb = files.get(result.dbSnapshot)!
  const snapWal = files.get(result.walSnapshot)!
  assert.ok(snapDb.equals(db))
  assert.ok(snapWal.equals(wal))
  assert.equal(result.capture.h0, wal.subarray(0, WAL_HEADER_BYTES).toString('hex'))
  assert.equal(result.capture.h1, result.capture.h0)
  assert.deepEqual(result.capture.sizes, { db_size: db.length, wal_size: wal.length })
})

test('captureSnapshot：源无 WAL（srcWal=null）→ ok + walSnapshot=null（合法只有主库），复读核验 D1==D0', async () => {
  const db = Buffer.alloc(4096, 3)
  const { files, deps } = fakeFs()
  files.set(SRC_DB, db)
  const result = await captureSnapshot(SRC_DB, null, WORK_DIR, deps)
  assert.equal(result.ok, true)
  if (!result.ok) return
  assert.equal(result.walSnapshot, null)
  assert.equal(result.capture.h0, null)
  assert.equal(result.capture.h1, null)
  assert.deepEqual(result.capture.sizes, { db_size: db.length, wal_size: 0 })
  assert.ok(files.get(result.dbSnapshot)!.equals(db))
})

test('captureSnapshot：复制期间 WAL reset（写后头 salt 变化）→ ok:false wal-header-changed（H0≠H1）', async () => {
  const db = Buffer.alloc(4096)
  const wal = buildWalBytes(2)
  const { files, deps } = fakeFs({
    onAfterWrite: (fsMap, writes) => {
      if (writes === 2) fsMap.get(SRC_WAL)!.writeUInt32BE(0x99999999, 16) // reset 改写头 salt-1
    },
  })
  files.set(SRC_DB, db)
  files.set(SRC_WAL, wal)
  const result = await captureSnapshot(SRC_DB, SRC_WAL, WORK_DIR, deps)
  assert.equal(result.ok, false)
  if (result.ok) return
  assert.equal(result.reason, 'wal-header-changed')
  assert.match(result.detail, /WAL 头变化/)
})

test('captureSnapshot：复制后 WAL 缩小（S1 < 已复制字节）→ ok:false wal-shrunk', async () => {
  const wal = buildWalBytes(3)
  const { files, deps } = fakeFs({
    onAfterWrite: (fsMap, writes) => {
      if (writes === 2) fsMap.set(SRC_WAL, fsMap.get(SRC_WAL)!.subarray(0, wal.length - 100)) // 复制后被截断
    },
  })
  files.set(SRC_DB, Buffer.alloc(4096))
  files.set(SRC_WAL, wal)
  const result = await captureSnapshot(SRC_DB, SRC_WAL, WORK_DIR, deps)
  assert.equal(result.ok, false)
  if (result.ok) return
  assert.equal(result.reason, 'wal-shrunk')
  assert.match(result.detail, /缩小/)
})

test('captureSnapshot：复制后主库增长（D1≠D0）→ ok:false db-changed（增长重试归调用方，单次语义报变化）', async () => {
  const db = Buffer.alloc(4096)
  const { files, deps } = fakeFs({
    onAfterWrite: (fsMap, writes) => {
      if (writes === 2) fsMap.set(SRC_DB, Buffer.concat([fsMap.get(SRC_DB)!, Buffer.alloc(4096)]))
    },
  })
  files.set(SRC_DB, db)
  files.set(SRC_WAL, buildWalBytes(1))
  const result = await captureSnapshot(SRC_DB, SRC_WAL, WORK_DIR, deps)
  assert.equal(result.ok, false)
  if (result.ok) return
  assert.equal(result.reason, 'db-changed')
  assert.match(result.detail, /主库大小变化/)
})

test('captureSnapshot：主库复制期间被截短（readRange 截短）→ ok:false db-changed（缩小方向）', async () => {
  const db = Buffer.alloc(4096 * 2)
  const { files, deps } = fakeFs({
    beforeRead: (p, reads) => {
      // readRange 序列：① WAL 头预读 ② 主库复制 ③ WAL 复制 ④ WAL 头复读——第 ② 步前截短主库
      if (p === SRC_DB && reads === 2) files.set(SRC_DB, files.get(SRC_DB)!.subarray(0, 4096))
    },
  })
  files.set(SRC_DB, db)
  files.set(SRC_WAL, buildWalBytes(1))
  const result = await captureSnapshot(SRC_DB, SRC_WAL, WORK_DIR, deps)
  assert.equal(result.ok, false)
  if (result.ok) return
  assert.equal(result.reason, 'db-changed')
  assert.match(result.detail, /缩小/)
})

test('captureSnapshot：调用方传了 WAL 路径但文件不存在 → ok:false no-wal（窗口内 checkpoint+delete 语义）', async () => {
  const { files, deps } = fakeFs()
  files.set(SRC_DB, Buffer.alloc(4096))
  const result = await captureSnapshot(SRC_DB, SRC_WAL, WORK_DIR, deps)
  assert.equal(result.ok, false)
  if (result.ok) return
  assert.equal(result.reason, 'no-wal')
  assert.match(result.detail, /不存在/)
})

test('captureSnapshot：主库 stat 失败 / 快照写失败 → ok:false copy-failed（含 reason 区分）', async () => {
  const missing = await captureSnapshot(SRC_DB, null, WORK_DIR, fakeFs().deps)
  assert.equal(missing.ok, false)
  if (!missing.ok) {
    assert.equal(missing.reason, 'copy-failed')
    assert.match(missing.detail, /stat 失败/)
  }
  const { files, deps } = fakeFs({ writeFails: true })
  files.set(SRC_DB, Buffer.alloc(4096))
  const writeFail = await captureSnapshot(SRC_DB, null, WORK_DIR, deps)
  assert.equal(writeFail.ok, false)
  if (!writeFail.ok) {
    assert.equal(writeFail.reason, 'copy-failed')
    assert.match(writeFail.detail, /写入失败/)
  }
})

test('captureSnapshot：空 WAL（0 字节）→ ok 捕获 0 字节，h0/h1 空串（语义判定归 py）', async () => {
  const { files, deps } = fakeFs()
  files.set(SRC_DB, Buffer.alloc(4096))
  files.set(SRC_WAL, Buffer.alloc(0))
  const result = await captureSnapshot(SRC_DB, SRC_WAL, WORK_DIR, deps)
  assert.equal(result.ok, true)
  if (!result.ok) return
  assert.equal(result.capture.h0, '')
  assert.equal(result.capture.h1, '')
  assert.deepEqual(result.capture.sizes, { db_size: 4096, wal_size: 0 })
  assert.equal(files.get(result.walSnapshot!)!.length, 0)
})

test('captureSnapshot：WAL 头不完整（20B < 32B）→ 按实际字节捕获透传（ok，公共前缀一致），语义判定归 py', async () => {
  const partial = Buffer.alloc(20, 0xab)
  const { files, deps } = fakeFs()
  files.set(SRC_DB, Buffer.alloc(4096))
  files.set(SRC_WAL, partial)
  const result = await captureSnapshot(SRC_DB, SRC_WAL, WORK_DIR, deps)
  assert.equal(result.ok, true)
  if (!result.ok) return
  assert.equal(result.capture.h0, partial.toString('hex'))
  assert.equal(result.capture.h1, partial.toString('hex'))
  assert.equal(files.get(result.walSnapshot!)!.length, 20)
})

test('captureSnapshot：WAL 捕获后追加增长合法（公共前缀一致即通过，快照只含复制时刻字节）', async () => {
  const wal = buildWalBytes(2)
  const grown = Buffer.concat([wal, Buffer.alloc(24 + 4096, 1)]) // 复制完成后源 WAL 又追加了一帧
  const { files, deps } = fakeFs({
    onAfterWrite: (fsMap, writes) => {
      if (writes === 2) fsMap.set(SRC_WAL, grown)
    },
  })
  files.set(SRC_DB, Buffer.alloc(4096))
  files.set(SRC_WAL, wal)
  const result = await captureSnapshot(SRC_DB, SRC_WAL, WORK_DIR, deps)
  assert.equal(result.ok, true)
  if (!result.ok) return
  assert.equal(result.capture.h0, wal.subarray(0, WAL_HEADER_BYTES).toString('hex'))
  assert.equal(result.capture.h1, result.capture.h0) // 头未变：增长只追加帧，公共前缀一致
  assert.deepEqual(result.capture.sizes, { db_size: 4096, wal_size: wal.length })
})
