/**
 * 1.13 遗留产物清扫单测（评审 v6 语义：owner pid 存活一律跳过——年龄不参与判定；
 * owner 文件完全缺失=从未有主删除；在位但不可解析/不可判=保守跳过；pid 死亡删除）：
 * 存活跳过 / 死亡删除 / 无主删除 / 不可解析跳过 / 失败收集 / 目录外不碰 / 非法形态
 * （真实 fs 临时目录 + 注入 isAlive/removeFn）。
 * 评审 v7 追加：两段发布配合——暂存目录（<final名>.creating-<pid>-<ts>）不走 owner 判定，
 * 按内嵌 pid 死活清扫；defaultCreateWorkDir 发布协议（暂存写 owner → rename）与清扫的
 * 交错安全性（真实 fs）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { tmpdir } from 'node:os'
import {
  OWNER_FILE_NAME,
  localdbArtifactsDir,
  parseStagingPid,
  sweepArtifacts,
} from '../src/platform/localdb/cleanup.js'
import { defaultCreateWorkDir } from '../src/platform/localdb/doctorLocaldb.js'

const OLD = '2020-01-01T00:00:00.000Z' // 很久以前的挂名时间——评审 v6 后年龄不再影响判定
const RECENT = '2026-10-14T11:00:00.000Z'

function makeBase(): string {
  return mkdtempSync(join(tmpdir(), 'wc-localdb-sweep-'))
}

/** 在 base 下建一个产物目录，可选写入 owner.json（raw 为任意 JSON 文本） */
function makeArtifactDir(base: string, name: string, ownerRaw?: string | object): string {
  const dir = join(base, name)
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'payload.bin'), 'x', 'utf8') // 目录内有产物内容，删除时一并清走
  if (ownerRaw !== undefined) {
    writeFileSync(join(dir, OWNER_FILE_NAME), typeof ownerRaw === 'string' ? ownerRaw : JSON.stringify(ownerRaw), 'utf8')
  }
  return dir
}

test('localdbArtifactsDir：默认按 LOCALAPPDATA 定位；缺失 → null；显式注入覆盖', () => {
  assert.equal(localdbArtifactsDir('C:\\la'), join('C:\\la', 'AidWorkAgent', 'wecom-cli', 'artifacts', 'localdb'))
  assert.equal(localdbArtifactsDir(null), null)
  const saved = process.env.LOCALAPPDATA
  try {
    delete process.env.LOCALAPPDATA
    assert.equal(localdbArtifactsDir(), null)
  } finally {
    if (saved === undefined) delete process.env.LOCALAPPDATA
    else process.env.LOCALAPPDATA = saved
  }
})

test('owner pid 存活 → skipped，目录保留（评审 v6：不论挂名多久——年龄兜底规则已删除）', () => {
  const base = makeBase()
  try {
    // 挂名 1h 的活动 owner 与挂名 6 年+ 的「陈旧」owner：一律跳过（年龄不能证明停止使用）
    const dirRecent = makeArtifactDir(base, 'a-recent', { pid: 4242, started_at: RECENT })
    const dirAged = makeArtifactDir(base, 'a-aged', { pid: 4243, started_at: OLD })
    const dirNoTime = makeArtifactDir(base, 'a-notime', { pid: 4244 }) // started_at 缺失不影响 pid 判定
    const r = sweepArtifacts(base, { isAlive: (pid) => pid >= 4242 && pid <= 4244 })
    assert.deepEqual(r.swept, [])
    assert.deepEqual(r.failed, [])
    assert.deepEqual(r.skipped.sort(), [dirAged, dirNoTime, dirRecent].sort())
    assert.ok(existsSync(dirAged) && existsSync(dirRecent), '存活所有者的目录不得删除（含长期挂名）')
  } finally {
    rmSync(base, { recursive: true, force: true })
  }
})

test('owner pid 死亡 → swept，目录连内容一起删除（与挂名时长无关）', () => {
  const base = makeBase()
  try {
    const dirRecent = makeArtifactDir(base, 'd-recent', { pid: 1111, started_at: RECENT })
    const dirOld = makeArtifactDir(base, 'd-old', { pid: 1112, started_at: OLD })
    const r = sweepArtifacts(base, { isAlive: () => false })
    assert.deepEqual(r.swept.sort(), [dirOld, dirRecent].sort())
    assert.deepEqual(r.skipped, [])
    assert.ok(!existsSync(dirRecent) && !existsSync(dirOld))
  } finally {
    rmSync(base, { recursive: true, force: true })
  }
})

test('owner.json 完全缺失 → 从未有主删除（评审 v6）', () => {
  const base = makeBase()
  try {
    const dMissing = makeArtifactDir(base, 'no-owner')
    const r = sweepArtifacts(base, { isAlive: () => true })
    assert.deepEqual(r.swept, [dMissing])
    assert.deepEqual(r.skipped, [])
    assert.ok(!existsSync(dMissing))
  } finally {
    rmSync(base, { recursive: true, force: true })
  }
})

test('owner 在位但 JSON 不可解析 / 非对象 / pid 缺失或非法 → 保守跳过计入 skipped（评审 v6：不把暂时不可读当确认无主）', () => {
  const base = makeBase()
  try {
    const dBroken = makeArtifactDir(base, 'broken-owner', '{not json')
    const dArray = makeArtifactDir(base, 'array-owner', '[1,2]')
    const dNull = makeArtifactDir(base, 'null-owner', 'null')
    const dNoPid = makeArtifactDir(base, 'no-pid', { started_at: RECENT })
    const dBadPid = makeArtifactDir(base, 'bad-pid', { pid: 'abc', started_at: RECENT })
    const dZeroPid = makeArtifactDir(base, 'zero-pid', { pid: 0, started_at: RECENT })
    const r = sweepArtifacts(base, { isAlive: () => true })
    assert.deepEqual(r.swept, [])
    assert.deepEqual(
      r.skipped.sort(),
      [dArray, dBadPid, dBroken, dNoPid, dNull, dZeroPid].sort(),
      'owner 在位但内容不可判的目录一律保留（宁可漏扫不误删活动所有者）',
    )
  } finally {
    rmSync(base, { recursive: true, force: true })
  }
})

test('删除失败 → 收集 {dir, error} 不抛，且不中断其余目录清扫', () => {
  const base = makeBase()
  try {
    const dKeep = makeArtifactDir(base, 'busy', { pid: 9, started_at: RECENT }) // 存活 → skip
    const dDead = makeArtifactDir(base, 'dead', { pid: 1, started_at: RECENT }) // 死亡 → 删
    const dNoOwner = makeArtifactDir(base, 'orphan') // 从未有主 → 删
    const r = sweepArtifacts(base, {
      isAlive: (pid) => pid === 9,
      removeFn: (dir) => {
        if (dir === dNoOwner) throw new Error('EBUSY: resource busy')
        rmSync(dir, { recursive: true, force: true })
      },
    })
    assert.deepEqual(r.swept, [dDead])
    assert.deepEqual(r.skipped, [dKeep])
    assert.equal(r.failed.length, 1)
    assert.equal(r.failed[0]!.dir, dNoOwner)
    assert.ok(r.failed[0]!.error.includes('EBUSY'))
    assert.ok(existsSync(dNoOwner), '删除失败的目录保留现场')
  } finally {
    rmSync(base, { recursive: true, force: true })
  }
})

test('目录外不碰：只动 base 的直接子目录，base 自身/上级/兄弟/散文件不碰', () => {
  const root = makeBase()
  try {
    const base = join(root, 'artifacts', 'localdb')
    mkdirSync(base, { recursive: true })
    const inside = makeArtifactDir(base, 'dead-dir', { pid: 2, started_at: RECENT })
    mkdirSync(join(root, 'sibling'), { recursive: true })
    writeFileSync(join(root, 'sibling', 'keep.txt'), 'keep', 'utf8')
    writeFileSync(join(base, 'loose-file.tmp'), 'loose', 'utf8') // base 内散文件

    const r = sweepArtifacts(base, { isAlive: () => false })
    assert.deepEqual(r.swept, [inside])
    assert.ok(existsSync(join(root, 'sibling', 'keep.txt')), 'base 外兄弟目录不碰')
    assert.ok(existsSync(join(base, 'loose-file.tmp')), 'base 内散文件不碰')
    assert.ok(existsSync(base), 'base 自身不删')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('base 目录缺失/不可读 → 空结果；显式 null → 空结果', () => {
  const missing = join(makeBase(), 'not-exist')
  try {
    const empty = { swept: [], skipped: [], failed: [] }
    assert.deepEqual(sweepArtifacts(missing, { isAlive: () => true }), empty)
    assert.deepEqual(sweepArtifacts(null), empty)
  } finally {
    rmSync(missing, { recursive: true, force: true })
  }
})

// ---------- 两段发布配合（评审 v7：暂存目录 <final名>.creating-<pid>-<ts> 不走 owner 判定） ----------

test('parseStagingPid：<final>.creating-<pid>-<ts> 解析内嵌 pid；无标记/非法 pid → null', () => {
  assert.equal(parseStagingPid('doctor_1700000000000_4321.creating-4321-1700000000000'), 4321)
  assert.equal(parseStagingPid('doctor_1_2.creating-987654-123'), 987654)
  assert.equal(parseStagingPid('doctor_1700000000000_4321'), null, '最终名目录不匹配暂存形态')
  assert.equal(parseStagingPid('doctor_1_2.creating-abc-123'), null, 'pid 段非数字 → 不可解析（保守跳过）')
  assert.equal(parseStagingPid('doctor_1_2.creating-'), null)
  assert.equal(parseStagingPid('doctor_1_2.creating-0-123'), null, 'pid ≤0 非法 → null')
})

test('两段发布空窗不误删：暂存目录（owner 尚未写入的中间态）不按「从未有主」删除——存活 pid 跳过', () => {
  const base = makeBase()
  try {
    // 模拟「目录创建后、owner 写入前」的确定性交错现场：盘上只有暂存名目录（空、无 owner）。
    // 旧实现该空窗落在最终名上会被 owner ENOENT 规则误删；暂存名对最终名规则不可见。
    const staging = join(base, 'doctor_1700000000000_9999.creating-9999-1700000000000')
    mkdirSync(staging, { recursive: true })
    const r = sweepArtifacts(base, { isAlive: (pid) => pid === 9999 })
    assert.deepEqual(r.swept, [], '暂存目录即使无 owner 也不得按「从未有主」删除')
    assert.deepEqual(r.skipped, [staging])
    assert.ok(existsSync(staging), '发布中的暂存目录必须存活到 rename')
  } finally {
    rmSync(base, { recursive: true, force: true })
  }
})

test('死亡 pid 的残留暂存目录被清扫（崩溃泄漏兜底）；内嵌 pid 不可解析 → 保守跳过', () => {
  const base = makeBase()
  try {
    const deadStaging = join(base, 'doctor_1700000000000_1111.creating-1111-1700000000000')
    mkdirSync(deadStaging, { recursive: true }) // 崩溃残留：无 owner、无内容
    const unparseable = join(base, 'doctor_1700000000001_2222.creating-abc-1')
    mkdirSync(unparseable, { recursive: true })
    const r = sweepArtifacts(base, { isAlive: (pid) => pid === 2222 })
    assert.deepEqual(r.swept, [deadStaging])
    assert.ok(!existsSync(deadStaging), '死亡 pid 的暂存目录删除')
    assert.deepEqual(r.skipped, [unparseable], 'pid 不可解析 → 保守跳过不删')
    assert.ok(existsSync(unparseable))
  } finally {
    rmSync(base, { recursive: true, force: true })
  }
})

test('defaultCreateWorkDir：暂存写 owner 后 rename 发布——最终名 owner 恒在、无暂存残留、cleanup 删除', () => {
  const base = makeBase()
  try {
    const work = defaultCreateWorkDir(base)
    assert.ok(work !== null)
    assert.equal(dirname(work.dir), base)
    assert.equal(work.run_id, work.dir.slice(base.length + 1))
    assert.ok(existsSync(join(work!.dir, OWNER_FILE_NAME)), '发布完成后最终名目录 owner 恒在')
    const owner = JSON.parse(readFileSync(join(work.dir, OWNER_FILE_NAME), 'utf8')) as {
      pid: number
      started_at: string
    }
    assert.equal(owner.pid, process.pid)
    assert.ok(!Number.isNaN(Date.parse(owner.started_at)))
    // base 下无 .creating-* 残留（暂存已 rename 为最终名）
    assert.deepEqual(readdirSync(base).filter((n) => n.includes('.creating-')), [])
    assert.ok(work.cleanup())
    assert.ok(!existsSync(work.dir))
  } finally {
    rmSync(base, { recursive: true, force: true })
  }
})

test('defaultCreateWorkDir 发布后的最终名目录对清扫可见可扫：owner 死亡 → swept；存活 → skipped', () => {
  const base = makeBase()
  try {
    const work = defaultCreateWorkDir(base)
    assert.ok(work !== null)
    const deadSweep = sweepArtifacts(base, { isAlive: () => false })
    assert.deepEqual(deadSweep.swept, [work.dir], 'rename 后最终名目录按 owner 规则参与清扫')
    assert.ok(!existsSync(work.dir))

    const work2 = defaultCreateWorkDir(base)
    assert.ok(work2 !== null)
    const aliveSweep = sweepArtifacts(base, { isAlive: (pid) => pid === process.pid })
    assert.deepEqual(aliveSweep.swept, [])
    assert.deepEqual(aliveSweep.skipped, [work2.dir], '存活 owner → 跳过（并行清扫不误删活动目录）')
    assert.ok(work2.cleanup())
  } finally {
    rmSync(base, { recursive: true, force: true })
  }
})

test('defaultCreateWorkDir 发布失败（最终名被目录占用）→ 返回 null、暂存目录清走、被占目录不动', () => {
  const base = makeBase()
  const realNow = Date.now
  Date.now = () => 1_700_000_000_000 // 固定时钟：预知 defaultCreateWorkDir 的最终名
  try {
    // 最终名被一个非空目录占用：rename(dir → 已存在目录) 在 Windows/POSIX 上都必失败
    //（Windows 的 rename 会静默覆盖同名【文件】，故用目录构造确定性失败）
    const occupied = join(base, `doctor_1700000000000_${process.pid}`)
    mkdirSync(occupied, { recursive: true })
    writeFileSync(join(occupied, 'keep.txt'), 'keep', 'utf8')
    const work = defaultCreateWorkDir(base)
    assert.equal(work, null, '最终名被占 → rename 失败 → 返回 null')
    assert.deepEqual(readdirSync(base).filter((n) => n.includes('.creating-')), [], '暂存目录必须被清走')
    assert.ok(existsSync(join(occupied, 'keep.txt')), '被占目录不被破坏')
    assert.equal(readFileSync(join(occupied, 'keep.txt'), 'utf8'), 'keep')
  } finally {
    Date.now = realNow
    rmSync(base, { recursive: true, force: true })
  }
})
