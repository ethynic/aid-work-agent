/**
 * imageOps 单测 + 双通道对照验收（2026-10-21 读取链路 PowerShell 迁移，核心验收）。
 *
 * 双通道：同一合成输入分别跑 cv-segdiff.ps1 / cv-stitch.ps1（真实 powershell.exe 子进程，
 * System.Drawing）与 Node 实现（src/main/boss/imageOps.ts），断言输出一致：
 *   - segdiff：misrate 数值（PS F4 字符串解析值 === Node 量化值）与 sameView 布尔
 *   - stitch：overlaps 数组、mis 数组（PS F2 解析值 === Node 量化值）、stitched 尺寸、
 *     拼接 PNG 像素逐字节一致（Node 解码 PS 输出 PNG 后对比）
 * 场景覆盖：完全相同（到底确认）、轻微重绘噪声、全不同、阈值附近错配带（0.01 口径）、
 * 已知重叠对齐、0 重叠（假对齐一致性）、高错配/可疑接缝（0.35 口径附近）、4 段 3 接缝、
 * 单段、非零 offset 裁剪、GDI+ 编码 PNG 输入（滤镜覆盖）、裁剪越界双通道 fail-loud。
 * PS 侧用例在非 Windows 或 ps1 已删除时自动 skip（迁移完成后仅保留 Node 侧回归）。
 *
 * 2026-10-21 追加 visual-region-compare.ps1（视觉恢复后关闭控件区域核验）双通道：
 * equal 布尔逐场景一致 + 非法区域/加载失败双通道 fail-loud（ps1 exit≠0 / ImageOpsError）；
 * 场景含区域首/末像素、区域外噪声（光标/插入符）、±1 最小色差（零容差边界）、
 * alpha-only 差异、1x1 与 32x32 上限尺寸。ps1 删除后该对照自动 skip。
 */
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import zlib from 'node:zlib'
import {
  decodePng,
  segdiffMisrate,
  encodePng,
  cropRect,
  downscaleToWidth180,
  findOverlap,
  mathRoundHalfEven,
  stitchVertical,
  stitchPartsImage,
  segdiffMisrateFiles,
  regionExactEqual,
  regionExactEqualFiles,
  ImageOpsError,
  type RgbaImage,
} from '../src/main/boss/imageOps.js'
import { sameViewAt, stitchParts, SAME_VIEW_MISRATE_MAX } from '../src/main/operations/bossResumeDetail.js'
import { SEAM_MIS_SUSPECT } from '../src/main/boss/ResumeReader.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const boss = path.resolve(here, path.basename(path.dirname(here)) === 'dist' ? '../..' : '..')
const scriptDir = path.join(boss, 'scripts')
const segdiffPs1 = path.join(scriptDir, 'cv-segdiff.ps1')
const stitchPs1 = path.join(scriptDir, 'cv-stitch.ps1')
const regionComparePs1 = path.join(scriptDir, 'visual-region-compare.ps1')
const systemPs = path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
const dualChannel = process.platform === 'win32' && existsSync(systemPs) && existsSync(segdiffPs1) && existsSync(stitchPs1)
const regionCompareDual = process.platform === 'win32' && existsSync(systemPs) && existsSync(regionComparePs1)

// ---------------------------------------------------------------------------
// 合成图工具（确定性 PRNG；不含任何真实简历内容）
// ---------------------------------------------------------------------------

function mulberry32(seed: number): () => number {
  let s = seed | 0
  return () => {
    s = (s + 0x6d2b79f5) | 0
    let t = Math.imul(s ^ (s >>> 15), 1 | s)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function baseResumeLike(width: number, totalHeight: number, seed = 42): Buffer {
  /** 仿简历画布：按 40 行「文字块」伪随机取色（块间色差 ≫ 60 保证错位即可判错配，块内
   * 平滑保证真对齐 mis≈0 不受降采样盒相位影响；块色由块号散列 → 无周期性，对齐唯一）。 */
  const px = Buffer.alloc(width * totalHeight * 4)
  const rnd = mulberry32(seed)
  const bandH = 40
  for (let y = 0; y < totalHeight; y++) {
    const band = Math.floor(y / bandH)
    const r0 = (band * 89 + seed) % 256
    const g0 = (band * 157 + seed * 3) % 256
    const b0 = (band * 233 + seed * 7) % 256
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4
      const stroke = ((x * 13 + band * 7) % 53) < 26 ? 70 : 0   // 块内横向笔画纹理（随块变化）
      px[i] = Math.max(0, Math.min(255, r0 - stroke))
      px[i + 1] = Math.max(0, Math.min(255, g0 - stroke))
      px[i + 2] = Math.max(0, Math.min(255, b0 - stroke))
      px[i + 3] = 255
      if (rnd() < 0.01) px[i] = px[i]! ^ 0x03
    }
  }
  return px
}

function noiseImage(width: number, height: number, seed: number): Buffer {
  const px = Buffer.alloc(width * height * 4)
  const rnd = mulberry32(seed)
  for (let i = 0; i < width * height; i++) {
    px[i * 4] = rnd() * 256 | 0
    px[i * 4 + 1] = rnd() * 256 | 0
    px[i * 4 + 2] = rnd() * 256 | 0
    px[i * 4 + 3] = 255
  }
  return px
}

/** 从底图截取段图：段 k 显示底图行 [k*step, k*step+H)（已知重叠 = H - step） */
function segmentOf(base: Buffer, width: number, segHeight: number, step: number, k: number): RgbaImage {
  const start = k * step
  const data = Buffer.alloc(width * segHeight * 4)
  base.copy(data, 0, start * width * 4, (start + segHeight) * width * 4)
  return { width, height: segHeight, data }
}

function withRowCorruption(img: RgbaImage, fromRow: number, rowCount: number, period: number, onSpan: number): RgbaImage {
  /** 周期性反转若干行（采样 y 步进 3 → 命中比例 ≈ onSpan/period），构造已知错配率 */
  const data = Buffer.from(img.data)
  for (let y = fromRow; y < Math.min(img.height, fromRow + rowCount); y++) {
    if (y % period >= onSpan) continue
    for (let x = 0; x < img.width; x++) {
      const i = (y * img.width + x) * 4
      data[i] = 255 - data[i]!
      data[i + 1] = 255 - data[i + 1]!
      data[i + 2] = 255 - data[i + 2]!
    }
  }
  return { ...img, data }
}

function withBandShift(img: RgbaImage, fromRow: number, rowCount: number): RgbaImage {
  /** 把一段行整体换成错配内容（错配带：构造 misrate ≈ rowCount/height 的采样错配） */
  const data = Buffer.from(img.data)
  for (let y = fromRow; y < Math.min(img.height, fromRow + rowCount); y++) {
    for (let x = 0; x < img.width; x++) {
      const i = (y * img.width + x) * 4
      data[i] = 255 - data[i]!
      data[i + 1] = 255 - data[i + 1]!
      data[i + 2] = 255 - data[i + 2]!
    }
  }
  return { ...img, data }
}

// ---------------------------------------------------------------------------
// PS 侧执行（真实 powershell.exe；仅迁移验收期的临时子进程通道）
// ---------------------------------------------------------------------------

type PsSegdiff = { ok: true; misrate: number } | { ok: false; exit: number | null }

function runSegdiffPs1(a: string, b: string, rect: { x: number; y: number; w: number; h: number }): PsSegdiff {
  const r = spawnSync(systemPs, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', segdiffPs1,
    '-A', a, '-B', b, '-X', String(rect.x), '-Y', String(rect.y), '-W', String(rect.w), '-H', String(rect.h)],
    { windowsHide: true, timeout: 120_000, encoding: 'utf8' })
  if (r.status !== 0) return { ok: false, exit: r.status }
  const m = /misrate=([\d.]+)/.exec(r.stdout)
  assert.ok(m, `cv-segdiff.ps1 输出异常：${r.stdout}`)
  return { ok: true, misrate: Number(m[1]) }
}

type PsStitch = {
  ok: true
  overlaps: number[]
  seamMis: number[]
  width: number
  height: number
} | { ok: false; exit: number | null }

function runStitchPs1(parts: string[], rect: { x: number; y: number; w: number; h: number }, outFile: string): PsStitch {
  const r = spawnSync(systemPs, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', stitchPs1,
    '-Parts', parts.join(','), '-X', String(rect.x), '-Y', String(rect.y), '-W', String(rect.w), '-H', String(rect.h), '-Out', outFile],
    { windowsHide: true, timeout: 180_000, encoding: 'utf8' })
  if (r.status !== 0) return { ok: false, exit: r.status }
  const stitched = /stitched=(\d+)x(\d+)/.exec(r.stdout)
  assert.ok(stitched, `cv-stitch.ps1 输出异常：${r.stdout}`)
  const seamLines = [...r.stdout.matchAll(/overlap\d+=(\d+)\s+mis=([\d.]+)/g)]
  return {
    ok: true,
    overlaps: seamLines.map((m) => Number(m[1])),
    seamMis: seamLines.map((m) => Number(m[2])),
    width: Number(stitched[1]),
    height: Number(stitched[2]),
  }
}

type PsRegionCompare = { ok: true; equal: boolean } | { ok: false; exit: number | null }

function runRegionComparePs1(original: string, fresh: string, region: { x: number; y: number; w: number; h: number }): PsRegionCompare {
  const r = spawnSync(systemPs, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', regionComparePs1,
    '-Original', original, '-Fresh', fresh, '-X', String(region.x), '-Y', String(region.y), '-Width', String(region.w), '-Height', String(region.h)],
    { windowsHide: true, timeout: 60_000, encoding: 'utf8' })
  if (r.status !== 0) return { ok: false, exit: r.status }
  const parsed = JSON.parse(r.stdout) as { equal?: unknown }
  assert.ok(parsed.equal === true || parsed.equal === false, `visual-region-compare.ps1 输出异常：${r.stdout}`)
  return { ok: true, equal: parsed.equal === true }
}

/** 单像素增量（dr/dg/db/da，钳到 0..255）；构造区域内外/通道级最小差异 */
function withPixelDelta(img: RgbaImage, x: number, y: number, dr: number, dg: number, db: number, da = 0): RgbaImage {
  const data = Buffer.from(img.data)
  const i = (y * img.width + x) * 4
  data[i] = Math.max(0, Math.min(255, data[i]! + dr))
  data[i + 1] = Math.max(0, Math.min(255, data[i + 1]! + dg))
  data[i + 2] = Math.max(0, Math.min(255, data[i + 2]! + db))
  data[i + 3] = Math.max(0, Math.min(255, data[i + 3]! + da))
  return { width: img.width, height: img.height, data }
}

// ---------------------------------------------------------------------------
// 纯 Node 单测（无 PS，恒跑）
// ---------------------------------------------------------------------------

test('mathRoundHalfEven 复刻 .NET Math.Round（银行家舍入）', () => {
  assert.equal(mathRoundHalfEven(0.5), 0)
  assert.equal(mathRoundHalfEven(1.5), 2)
  assert.equal(mathRoundHalfEven(2.5), 2)
  assert.equal(mathRoundHalfEven(3.5), 4)
  assert.equal(mathRoundHalfEven(2.4), 2)
  assert.equal(mathRoundHalfEven(2.6), 3)
  assert.equal(mathRoundHalfEven(0), 0)
  // o = round(bestO*W/180) 的典型 tie：bestO=150, W=727 → 605.83；bestO=90·? 制造 .5：150*727/180 = 605.833
  // 直接验证 606.5 → 606（偶）、605.5 → 606
  assert.equal(mathRoundHalfEven(606.5), 606)
  assert.equal(mathRoundHalfEven(605.5), 606)
})

test('PNG 编解码 roundtrip（自适应滤镜 + 调色板输入路径由 PS 侧用例覆盖）', () => {
  const img = { width: 61, height: 43, data: noiseImage(61, 43, 7) }
  const png = encodePng(img)
  const back = decodePng(png)
  assert.equal(back.width, 61)
  assert.equal(back.height, 43)
  assert.ok(back.data.equals(img.data))
})

test('decodePng 拒绝非 8-bit / 隔行 / 非 PNG 输入（fail-loud，不静默降级）', () => {
  assert.throws(() => decodePng(Buffer.from('not a png')), ImageOpsError)
  const img = { width: 4, height: 4, data: Buffer.alloc(4 * 4 * 4, 255) }
  const png = encodePng(img)
  // 篡改 bitDepth=16
  png[24] = 16
  assert.throws(() => decodePng(png), /位深/)
  const png2 = encodePng({ width: 4, height: 4, data: Buffer.alloc(4 * 4 * 4, 255) })
  png2[28] = 1 // interlace
  assert.throws(() => decodePng(png2), /隔行/)
})

test('cropRect 裁剪与越界 fail-loud（ps1 exit 1 语义）', () => {
  const img = { width: 100, height: 80, data: noiseImage(100, 80, 3) }
  const crop = cropRect(img, 10, 20, 50, 60)
  assert.equal(crop.width, 50)
  assert.equal(crop.height, 60)
  assert.equal(crop.data[(5 * 50 + 5) * 4], img.data[((20 + 5) * 100 + 10 + 5) * 4])
  assert.throws(() => cropRect(img, 10, 20, 91, 60), /超出图像/)
  assert.throws(() => cropRect(img, -1, 0, 10, 10), /X\/Y/)
  assert.throws(() => cropRect(img, 0, 0, 0, 10), /W\/H/)
})

test('downscaleToWidth180：常量图不变、宽恒 180、高按银行家舍入', () => {
  const flat = { width: 727, height: 1237, data: (() => { const b = Buffer.alloc(727 * 1237 * 4); for (let i = 0; i < 727 * 1237; i++) { b[i * 4] = 90; b[i * 4 + 1] = 120; b[i * 4 + 2] = 200; b[i * 4 + 3] = 255 } return b })() }
  const small = downscaleToWidth180(flat)
  assert.equal(small.width, 180)
  assert.equal(small.height, 306)
  for (let i = 0; i < 180 * 306; i++) {
    assert.equal(small.data[i * 4], 90)
    assert.equal(small.data[i * 4 + 1], 120)
    assert.equal(small.data[i * 4 + 2], 200)
  }
  // 高度 tie 案例：h*180/w = k+0.5 时取偶（1237*180/727=306.06→306；构造 359*180/716=90.225…）
  const img2 = { width: 716, height: 359, data: Buffer.alloc(716 * 359 * 4) }
  assert.equal(downscaleToWidth180(img2).height, 90)
})

test('findOverlap / stitchVertical 纯函数：已知平移恢复重叠与总高', () => {
  const W = 727, H = 900, step = 360
  const base = baseResumeLike(W, H + step, 11)
  const a = segmentOf(base, W, H, step, 0)
  const b = segmentOf(base, W, H, step, 1)
  const r = findOverlap(a, b)
  // 降采样盒相位差使真对齐处 mis 不恒为 0（真机密文区真对齐 ~0.03，同量级）
  assert.ok(r.mis <= 0.2, `mis=${r.mis}`)
  // bestO 量化到原图行：o = round(bestO*727/180)，与真重叠 540 差 ≤ 8（ps1 口径 ~4px 精度）
  assert.ok(Math.abs(r.overlap - (H - step)) <= 8, `overlap=${r.overlap}`)
  const stitched = stitchVertical([a, b], [r.overlap])
  assert.equal(stitched.width, W)
  // 总高 = 2H - ovl（ovl 有 ~4px 量化，与 ps1 口径一致，不恒等于 H+step）
  assert.equal(stitched.height, 2 * H - r.overlap)
})

test('sameViewAt / stitchParts 调用方契约（Node 通道，无 PS）', async () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'image-ops-contract-'))
  try {
    const img = { width: 64, height: 48, data: baseResumeLike(64, 48, 5) }
    const fileA = path.join(dir, 'a.png')
    const fileB = path.join(dir, 'b.png')
    writeFileSync(fileA, encodePng(img))
    writeFileSync(fileB, encodePng(img))
    const rect = { x: 0, y: 0, w: 64, h: 48 }
    assert.equal(await sameViewAt(fileA, fileB, rect), true)
    const out = path.join(dir, 'stitched.png')
    assert.deepEqual(await stitchParts([fileA], rect, out), { width: 64, height: 48, overlaps: [], seamMis: [] })
    assert.ok(existsSync(out))
    // 裁剪越界 → ResumeReadError fail-loud（对应 ps1 exit 1 包装路径）
    await assert.rejects(sameViewAt(fileA, fileB, { x: 0, y: 0, w: 65, h: 48 }), /裁剪区域|比对失败/)
    await assert.rejects(stitchParts([fileA], { x: 1, y: 1, w: 64, h: 48 }, out), /裁剪区域|拼接失败/)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('regionExactEqual：区域全等 / 区域外噪声 / alpha 通道 + 非法区域 fail-loud（visual-region-compare.ps1 语义）', () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'image-ops-region-'))
  try {
    const img: RgbaImage = { width: 40, height: 40, data: noiseImage(40, 40, 55) }
    const rect = { x: 4, y: 4, w: 32, h: 32 }
    assert.equal(regionExactEqual(img, img, rect), true)
    // 区域外差异（光标/插入符像素）不进入比较；区域首/末像素与 alpha-only 差异均判不等
    assert.equal(regionExactEqual(img, withPixelDelta(img, 3, 4, 40, 0, 0), rect), true)
    assert.equal(regionExactEqual(img, withPixelDelta(img, 36, 5, 0, -40, 0), rect), true)
    assert.equal(regionExactEqual(img, withPixelDelta(img, 4, 4, 1, 0, 0), rect), false)
    assert.equal(regionExactEqual(img, withPixelDelta(img, 35, 35, 0, 1, 0), rect), false)
    assert.equal(regionExactEqual(img, withPixelDelta(img, 20, 20, 0, 0, 0, -1), rect), false)
    // 文件级：合法路径 + 各非法口径（对应 ps1 单一 'Invalid visual comparison region' / 加载失败 exit 1）
    const fileA = path.join(dir, 'a.png')
    const fileB = path.join(dir, 'b.png')
    writeFileSync(fileA, encodePng(img))
    writeFileSync(fileB, encodePng(img))
    const read = (f: string) => readFileSync(f)
    assert.equal(regionExactEqualFiles(fileA, fileB, rect, read), true)
    assert.equal(regionExactEqualFiles(fileA, fileB, { x: 0, y: 0, w: 32, h: 1 }, read), true)
    // ps1 [int] 绑定口径：小数经 double→Math.Round 银行家舍入强转（4.5→4、5.5→6）
    assert.equal(regionExactEqualFiles(fileA, fileB, { x: 4.5, y: 4, w: 32, h: 32 }, read), true)
    assert.equal(regionExactEqualFiles(fileA, fileB, { x: 5.5, y: 4.4, w: 32, h: 32 }, read), true)
    assert.throws(() => regionExactEqualFiles(fileA, fileB, { x: 0, y: 0, w: 33, h: 32 }, read), /Invalid visual comparison region/)
    assert.throws(() => regionExactEqualFiles(fileA, fileB, { x: 0, y: 0, w: 32, h: 0 }, read), /Invalid visual comparison region/)
    assert.throws(() => regionExactEqualFiles(fileA, fileB, { x: -1, y: 0, w: 32, h: 32 }, read), /Invalid visual comparison region/)
    assert.throws(() => regionExactEqualFiles(fileA, fileB, { x: 9, y: 9, w: 32, h: 32 }, read), /Invalid visual comparison region/)
    assert.throws(() => regionExactEqualFiles(fileA, fileB, { x: Number.NaN, y: 4, w: 32, h: 32 }, read), /Invalid visual comparison region/)
    const mismatched = path.join(dir, 'mismatched.png')
    writeFileSync(mismatched, encodePng({ width: 40, height: 41, data: noiseImage(40, 41, 56) }))
    assert.throws(() => regionExactEqualFiles(fileA, mismatched, rect, read), /Invalid visual comparison region/)
    assert.throws(() => regionExactEqualFiles(fileA, path.join(dir, 'missing.png'), rect, read), ImageOpsError)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

// ---------------------------------------------------------------------------
// 双通道对照（核心验收；非 Windows / ps1 已删时 skip）
// ---------------------------------------------------------------------------

test('双通道对照：cv-segdiff 全场景（misrate 数值 + sameView 布尔一致）', { skip: !dualChannel ? '非 Windows 或 ps1 已删除（迁移完成后仅 Node 回归）' : false, timeout: 300_000 }, () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'image-ops-dual-segdiff-'))
  try {
    const W = 727, H = 1237
    const base = baseResumeLike(W, H, 42)
    const a: RgbaImage = { width: W, height: H, data: Buffer.from(base) }
    const fileA = path.join(dir, 'a.png')
    writeFileSync(fileA, encodePng(a))
    const rect = { x: 0, y: 0, w: W, h: H }
    const cases: Array<{ name: string; b: RgbaImage; boundaryTolerance?: boolean }> = [
      { name: 'identical（到底确认路径：字节差小但像素确认必过）', b: a },
      { name: 'repaint-noise（同画面 WASM 重绘亚像素噪声 → misrate < 0.01）', b: (() => {
        const data = Buffer.from(base)
        for (let i = 0; i < W * H; i++) if (i % 97 === 0) { data[i * 4] = data[i * 4]! ^ 0x01 }
        return { width: W, height: H, data }
      })() },
      { name: 'different（独立噪声替换 → misrate 接近 1 → 不同画面；逐点色差遍布容差 60 邻域，属边界对抗内容，允许 ≤2 采样点翻转、判定布尔必须一致）',
        b: { width: W, height: H, data: noiseImage(W, H, 43) }, boundaryTolerance: true },
      { name: 'band-12rows（错配带 → misrate 阈值 0.01 之下）', b: withBandShift(a, 600, 12) },
      { name: 'band-18rows（错配带 → misrate 阈值 0.01 之上）', b: withBandShift(a, 600, 18) },
      { name: 'offset-crop（非零 X/Y 裁剪口径；整页错位 1 行 → 采样点色差恰在容差附近，同为边界对抗内容）', b: (() => {
        const data = Buffer.alloc(W * H * 4)
        base.copy(data, 0, W * 4, W * H * 4)
        base.copy(data, (H - 1) * W * 4, 0, W * 4)
        return { width: W, height: H, data }
      })(), boundaryTolerance: true },
    ]
    for (const c of cases) {
      const fileB = path.join(dir, 'b.png')
      writeFileSync(fileB, encodePng(c.b))
      const ps = runSegdiffPs1(fileA, fileB, rect)
      assert.equal(ps.ok, true, `${c.name}: ps1 执行失败`)
      const node = segdiffMisrateFiles(fileA, fileB, rect, (f) => readFileSync(f))
      // 数值 1:1（PS F4 字符串解析值 === Node 量化值）；布尔判定同口径。
      // boundaryTolerance 场景（独立噪声/整页错位 1 行）是刻意构造的容差边界对抗内容：
      // 大量采样点色差恰在 |dR|+|dG|+|dB|≈60 邻域，GDI+ 降采样的内部定点舍入不可公开复刻
      // （字节级 97.5% 精确、100% ±1，见 imageOps.ts 文件头），允许 ≤2 采样点（2/2600≈0.0008）
      // 翻转；判定布尔必须一致。0.01 阈值附近行为由 band-12/18rows（精确相等）覆盖。
      if (c.boundaryTolerance) {
        assert.ok(Math.abs(node - ps.misrate) <= 0.0008, `${c.name}: misrate PS=${ps.misrate} Node=${node}（差异超过 2 采样点）`)
      } else {
        assert.equal(node, ps.misrate, `${c.name}: misrate PS=${ps.misrate} Node=${node}`)
      }
      assert.equal(node <= SAME_VIEW_MISRATE_MAX, ps.misrate <= SAME_VIEW_MISRATE_MAX, `${c.name}: sameView 判定不一致`)
    }
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('双通道对照：cv-stitch 全场景（overlap/mis/stitched 尺寸 + 拼接像素一致）', { skip: !dualChannel ? '非 Windows 或 ps1 已删除（迁移完成后仅 Node 回归）' : false, timeout: 600_000 }, () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'image-ops-dual-stitch-'))
  try {
    const W = 727, H = 900
    const rect = { x: 0, y: 0, w: W, h: H }
    const scenarios: Array<{ name: string; parts: RgbaImage[]; expectOverlap?: number }> = [
      { name: 'two-seg 60% overlap（已知重叠，mis≈0）', parts: (() => {
        const step = 360
        const base = baseResumeLike(W, H + step, 100)
        return [segmentOf(base, W, H, step, 0), segmentOf(base, W, H, step, 1)]
      })(), expectOverlap: H - 360 },
      { name: 'zero-overlap（相邻段无重叠 → 双通道假对齐也必须一致）', parts: (() => {
        const base = baseResumeLike(W, H * 2, 101)
        return [segmentOf(base, W, H, H, 0), segmentOf(base, W, H, H, 1)]
      })() },
      { name: 'high-mismatch 50%（重叠区半数采样行反转 → mis 大）', parts: (() => {
        const step = 360
        const base = baseResumeLike(W, H + step, 102)
        const a = segmentOf(base, W, H, step, 0)
        const b = withRowCorruption(segmentOf(base, W, H, step, 1), 0, H - step, 6, 3)
        return [a, b]
      })() },
      { name: 'near-suspect 1/3 corruption（mis 落 0.35 口径附近）', parts: (() => {
        const step = 360
        const base = baseResumeLike(W, H + step, 103)
        const a = segmentOf(base, W, H, step, 0)
        const b = withRowCorruption(segmentOf(base, W, H, step, 1), 0, H - step, 9, 3)
        return [a, b]
      })() },
      { name: 'four-seg 3 seams', parts: (() => {
        const step = 450
        const base = baseResumeLike(W, H + step * 3, 104)
        return [0, 1, 2, 3].map((k) => segmentOf(base, W, H, step, k))
      })(), expectOverlap: H - 450 },
      { name: 'single-seg（无接缝）', parts: (() => {
        const base = baseResumeLike(W, H, 105)
        return [segmentOf(base, W, H, H, 0)]
      })() },
    ]
    for (const s of scenarios) {
      const files = s.parts.map((p, i) => {
        const f = path.join(dir, `part-${i}.png`)
        writeFileSync(f, encodePng(p))
        return f
      })
      const psOut = path.join(dir, 'ps-stitched.png')
      const ps = runStitchPs1(files, rect, psOut)
      assert.equal(ps.ok, true, `${s.name}: ps1 执行失败`)
      const nodeOut = path.join(dir, 'node-stitched.png')
      const node = stitchPartsImage(files, rect, nodeOut, { readFile: (f) => readFileSync(f), writeFile: (f, d) => writeFileSync(f, d) })
      assert.deepEqual(node.overlaps, ps.overlaps, `${s.name}: overlaps PS=${ps.overlaps} Node=${node.overlaps}`)
      assert.deepEqual(node.seamMis, ps.seamMis, `${s.name}: seamMis PS=${ps.seamMis} Node=${node.seamMis}`)
      assert.equal(node.width, ps.width, `${s.name}: stitched 宽`)
      assert.equal(node.height, ps.height, `${s.name}: stitched 高`)
      // 可疑接缝分类（SEAM_MIS_SUSPECT=0.35，云端契约元数据口径）双通道一致
      assert.deepEqual(node.seamMis.map((m) => m > SEAM_MIS_SUSPECT), ps.seamMis.map((m) => m > SEAM_MIS_SUSPECT), `${s.name}: suspectSeams 分类`)
      if (s.expectOverlap !== undefined && node.overlaps.length > 0) {
        assert.ok(Math.abs(node.overlaps[0]! - s.expectOverlap) <= 8, `${s.name}: overlap=${node.overlaps[0]} 期望≈${s.expectOverlap}`)
      }
      // 拼接 PNG 像素逐字节一致（Node 解码 PS 输出后对比；编码字节本身允许不同）
      const psPixels = decodePng(readFileSync(psOut))
      const nodePixels = decodePng(readFileSync(nodeOut))
      assert.equal(psPixels.width, nodePixels.width, `${s.name}: 像素宽`)
      assert.equal(psPixels.height, nodePixels.height, `${s.name}: 像素高`)
      assert.ok(psPixels.data.equals(nodePixels.data), `${s.name}: 拼接像素不一致`)
    }
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('双通道对照：GDI+ 编码 PNG 输入（滤镜/编码差异下的解码等价）+ 非零 offset 裁剪 + 越界 fail-loud', { skip: !dualChannel ? '非 Windows 或 ps1 已删除（迁移完成后仅 Node 回归）' : false, timeout: 300_000 }, () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'image-ops-dual-gdi-'))
  try {
    // 用 System.Drawing 生成带渐变+文字行的 PNG（GDI+ 编码器 + 滤镜路径），喂给双通道
    const pageW = 1249, pageH = 1440, cx = 168, cy = 40, cw = 760, ch = 1264
    const genPs = [
      `$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Drawing`,
      `$w=${pageW}; $h=${pageH}; $bmp=New-Object System.Drawing.Bitmap($w,$h);`,
      `$g=[System.Drawing.Graphics]::FromImage($bmp); $rect=New-Object System.Drawing.Rectangle(0,0,$w,$h);`,
      `$brush=New-Object System.Drawing.Drawing2D.LinearGradientBrush($rect,[System.Drawing.Color]::FromArgb(250,248,244),[System.Drawing.Color]::FromArgb(120,130,150),[single]90);`,
      `$g.FillRectangle($brush,$rect);`,
      `$font=New-Object System.Drawing.Font('Arial',14);`,
      `for($i=0;$i -lt 40;$i++){ $g.DrawString(('ROW ' + $i + ' 中文简历纹理行 ABCDEFG 0123456789'),$font,[System.Drawing.Brushes]::DarkSlateGray,200,(60+$i*28)) }`,
      `$g.Dispose(); $bmp.Save('${path.join(dir, 'gdi-a.png').replace(/\\/g, '\\\\')}',[System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose();`,
    ].join('\n')
    const gen = spawnSync(systemPs, ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(genPs, 'utf16le').toString('base64')], { windowsHide: true, timeout: 60_000, encoding: 'utf8' })
    assert.equal(gen.status, 0, `GDI+ 生成失败：${gen.stderr}`)
    const fileA = path.join(dir, 'gdi-a.png')
    // Node 解码 GDI+ PNG：尺寸正确 + 与双通道段图一致地被 PS 读到
    const decoded = decodePng(readFileSync(fileA))
    assert.equal(decoded.width, pageW)
    assert.equal(decoded.height, pageH)
    // 垂直平移 300 行构造第二段（同一画布内容、已知重叠）
    const shifted = Buffer.alloc(pageW * pageH * 4)
    decoded.data.copy(shifted, 0, 300 * pageW * 4)
    decoded.data.copy(shifted, (pageH - 300) * pageW * 4, 0, 300 * pageW * 4)
    const fileB = path.join(dir, 'gdi-b.png')
    writeFileSync(fileB, encodePng({ width: pageW, height: pageH, data: shifted }))
    const rect = { x: cx, y: cy, w: cw, h: ch }
    // segdiff 双通道（不同画面）
    const psD = runSegdiffPs1(fileA, fileB, rect)
    assert.equal(psD.ok, true)
    assert.equal(segdiffMisrateFiles(fileA, fileB, rect, (f) => readFileSync(f)), psD.misrate)
    // stitch 双通道（非零 offset 裁剪 + GDI 输入）
    const psOut = path.join(dir, 'ps-stitched.png')
    const psS = runStitchPs1([fileA, fileB], rect, psOut)
    assert.equal(psS.ok, true)
    const nodeOut = path.join(dir, 'node-stitched.png')
    const nodeS = stitchPartsImage([fileA, fileB], rect, nodeOut, { readFile: (f) => readFileSync(f), writeFile: (f, d) => writeFileSync(f, d) })
    assert.deepEqual(nodeS.overlaps, psS.overlaps)
    assert.deepEqual(nodeS.seamMis, psS.seamMis)
    assert.equal(nodeS.width, psS.width)
    assert.equal(nodeS.height, psS.height)
    assert.ok(decodePng(readFileSync(psOut)).data.equals(decodePng(readFileSync(nodeOut)).data))
    // 裁剪越界：PS exit 1 / Node ImageOpsError（fail-loud 对齐）；宽越出页面右缘
    const badRect = { x: cx, y: cy, w: pageW - cx + 1, h: ch }
    const psBad = runSegdiffPs1(fileA, fileB, badRect)
    assert.equal(psBad.ok, false, 'ps1 越界应 exit 1')
    assert.notEqual(psBad.ok, true)
    assert.throws(() => segdiffMisrateFiles(fileA, fileB, badRect, (f) => readFileSync(f)), /超出图像/)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('双通道对照：visual-region-compare 全场景（equal 布尔 + 非法区域/加载失败双通道 fail-loud）',
  { skip: !regionCompareDual ? '非 Windows 或 ps1 已删除（迁移完成后仅 Node 回归）' : false, timeout: 300_000 }, () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'image-ops-dual-region-'))
  try {
    const base: RgbaImage = { width: 40, height: 40, data: noiseImage(40, 40, 77) }
    const cap: RgbaImage = { width: 32, height: 32, data: noiseImage(32, 32, 78) }
    const tiny: RgbaImage = { width: 1, height: 1, data: Buffer.from([10, 20, 30, 255]) }
    const rect = { x: 4, y: 4, w: 32, h: 32 }
    const outsideNoise = [[3, 4], [4, 3], [36, 5], [5, 36], [0, 0], [39, 39], [20, 3], [20, 36]]
      .reduce((acc, [x, y]) => withPixelDelta(acc, x!, y!, 60, -60, 60), base)
    const cases: Array<{ name: string; a: RgbaImage; b: RgbaImage | null; rect: { x: number; y: number; w: number; h: number }; invalid: boolean }> = [
      { name: 'identical 40x40（恢复核验通过路径）', a: base, b: base, rect, invalid: false },
      { name: '区域中心 ±1 最小色差（零容差边界：最小可能差异也判不等）', a: base, b: withPixelDelta(base, 20, 20, 1, 1, 1), rect, invalid: false },
      { name: '区域首像素 (4,4) 差异', a: base, b: withPixelDelta(base, 4, 4, 30, 0, 0), rect, invalid: false },
      { name: '区域末像素 (35,35) 差异', a: base, b: withPixelDelta(base, 35, 35, 0, 0, 30), rect, invalid: false },
      { name: '仅区域外差异（光标/插入符噪声 → equal=true）', a: base, b: outsideNoise, rect, invalid: false },
      { name: '区域外整行噪声带（0..3 与 36..39 行）→ equal=true', a: base, b: withBandShift(withBandShift(base, 0, 4), 36, 4), rect, invalid: false },
      { name: '区域内整带变化（关闭控件消失）→ false', a: base, b: withBandShift(base, 10, 10), rect, invalid: false },
      { name: 'alpha-only 差异（ToArgb 含 alpha 通道）→ false', a: base, b: withPixelDelta(base, 20, 20, 0, 0, 0, -1), rect, invalid: false },
      { name: '1x1 极端尺寸 region(0,0,1,1) 全等', a: tiny, b: tiny, rect: { x: 0, y: 0, w: 1, h: 1 }, invalid: false },
      { name: '32x32 整图 region（W/H 上限 32 恰好合法）全等', a: cap, b: cap, rect: { x: 0, y: 0, w: 32, h: 32 }, invalid: false },
      { name: '32x32 整图单像素差异（末像素边界）→ false', a: cap, b: withPixelDelta(cap, 31, 31, 0, -1, 0), rect: { x: 0, y: 0, w: 32, h: 32 }, invalid: false },
      { name: 'W=33 超 32 上限 → 非法', a: base, b: base, rect: { x: 0, y: 0, w: 33, h: 32 }, invalid: true },
      { name: 'H=0 → 非法', a: base, b: base, rect: { x: 0, y: 0, w: 32, h: 0 }, invalid: true },
      { name: 'X=-1 → 非法', a: base, b: base, rect: { x: -1, y: 0, w: 32, h: 32 }, invalid: true },
      { name: '区域越出图界（9+32>40）→ 非法', a: base, b: base, rect: { x: 9, y: 9, w: 32, h: 32 }, invalid: true },
      { name: '两图尺寸不一致 → 非法', a: base, b: { width: 40, height: 41, data: noiseImage(40, 41, 79) }, rect, invalid: true },
      { name: 'fresh 文件缺失 → 加载失败（ps1 Bitmap 构造 exit≠0 / Node ImageOpsError）', a: base, b: null, rect, invalid: true },
      { name: '非整数坐标 4.5/4.4 → [int] 绑定银行家舍入强转（4.5→4、4.4→4）→ 合法且全等', a: base, b: base, rect: { x: 4.5, y: 4.4, w: 32, h: 32 }, invalid: false },
      { name: '非整数坐标 5.5 tie → [int] 绑定取偶强转（5.5→6）→ 合法且全等', a: base, b: base, rect: { x: 5.5, y: 4, w: 32, h: 32 }, invalid: false },
      { name: 'NaN 坐标 → [int] 绑定失败 → 非法', a: base, b: base, rect: { x: Number.NaN, y: 4, w: 32, h: 32 }, invalid: true },
    ]
    for (const c of cases) {
      const fileA = path.join(dir, 'a.png')
      writeFileSync(fileA, encodePng(c.a))
      const fileB = path.join(dir, c.b ? 'b.png' : 'missing.png')
      if (c.b) writeFileSync(fileB, encodePng(c.b))
      const ps = runRegionComparePs1(fileA, fileB, c.rect)
      let node: { ok: boolean; equal?: boolean }
      try {
        node = { ok: true, equal: regionExactEqualFiles(fileA, fileB, c.rect, (f) => readFileSync(f)) }
      } catch (error) {
        // 非法区域/加载失败必须抛 ImageOpsError（非 CodedOperationError），
        // 才能被 visualRecovery.dismiss 捕获包装为 STALE_OBSERVATION（ps1 exit 1 同路径）
        assert.ok(error instanceof ImageOpsError, `${c.name}: 非 ImageOpsError：${error}`)
        node = { ok: false }
      }
      assert.equal(node.ok, ps.ok, `${c.name}: 通道成败不一致（PS ok=${ps.ok} Node ok=${node.ok}）`)
      if (ps.ok && node.ok) assert.equal(node.equal, ps.equal, `${c.name}: equal 判定不一致`)
      if (c.invalid) assert.equal(ps.ok, false, `${c.name}: 应双通道 fail-loud`)
    }
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('性能：7 段 760x1264 拼接（Node 通道毫秒~百毫秒级，不劣于 ps1 降采样口径）', { timeout: 120_000 }, () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'image-ops-perf-'))
  try {
    const W = 760, H = 1264, step = 632
    const base = baseResumeLike(W, H + step * 6, 9)
    const files: string[] = []
    for (let k = 0; k < 7; k++) {
      const f = path.join(dir, `seg-${k}.png`)
      writeFileSync(f, encodePng(segmentOf(base, W, H, step, k)))
      files.push(f)
    }
    const out = path.join(dir, 'stitched.png')
    const t0 = process.hrtime.bigint()
    const r = stitchPartsImage(files, { x: 0, y: 0, w: W, h: H }, out, { readFile: (f) => readFileSync(f), writeFile: (f, d) => writeFileSync(f, d) })
    const ms = Number(process.hrtime.bigint() - t0) / 1e6
    assert.equal(r.overlaps.length, 6)
    assert.equal(r.width, W)
    // 总高自洽：H + Σ(H - ovl)（对齐值由内容决定，不预设步距）
    assert.equal(r.height, 7 * H - r.overlaps.reduce((a, o) => a + o, 0))
    // ps1 同输入实测 ~秒级；Node 需求：760xN 长图毫秒~百毫秒级。上限放宽到 3s 防 CI 抖动。
    assert.ok(ms < 3000, `拼接耗时 ${ms.toFixed(0)}ms 超上限`)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

// ---- 首行滤镜解码回归（2026-10-21 外部评审 P1）----
// fixture 由 python 标准库（zlib/struct/binascii.crc32）独立编码生成，不依赖本模块自编码回环；
// 首行 Up/Average/Paeth 在 PNG 规格里上方像素按全 0——曾因空 prevRow（undefined→NaN）解码错。

const FIRST_ROW_FILTER_FIXTURES: Record<string, { w: number; h: number; rgba: number[] }> = {
  up3x2: {
    w: 3, h: 2,
    rgba: [10,20,30,255, 40,50,60,255, 70,80,90,255, 1,2,3,255, 4,5,6,255, 7,8,9,255],
  },
  avg3x2: {
    w: 3, h: 2,
    rgba: [17,99,200,255, 3,128,255,255, 60,61,62,255, 9,8,7,255, 6,5,4,255, 3,2,1,255],
  },
  paeth4x3: {
    w: 4, h: 3,
    rgba: [
      5,250,7,255, 200,100,50,255, 1,2,3,255, 254,253,252,255,
      11,22,33,255, 44,55,66,255, 77,88,99,255, 10,20,30,255,
      255,0,128,255, 64,64,64,255, 7,7,7,255, 200,200,1,255,
    ],
  },
}
const FIXTURE_PNG: Record<string, string> = {
  up3x2: 'iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAHElEQVR4nGPiEpHTMLJxC4hiYGRiZmFlY+fgBAAY5wHyogkEjAAAAABJRU5ErkJggg==',
  avg3x2: 'iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAHElEQVR4nGMWTD7x23+29d/9DJwc7GysLMxMjABKyQVJe6QERgAAAABJRU5ErkJggg==',
  paeth4x3: 'iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAMElEQVR4nGNh/cV+OEvbct7Fv79/MnCLKeqYO/lGJHOJyDH8Z2hwcHBgZ2c/cYIRAB7TDQykBOIrAAAAAElFTkSuQmCC',
  repA: 'iVBORw0KGgoAAAANSUhEUgAAAAgAAAADCAIAAAAhqtkfAAAAMElEQVR4nGP5//8/Axhwi8rv27rG3My0sbHx1u07LEzMLAwwYGlpCWezsLEiJJABAIZpCuGo+DKJAAAAAElFTkSuQmCC',
  repB: 'iVBORw0KGgoAAAANSUhEUgAAAAgAAAADCAIAAAAhqtkfAAAAMklEQVR4nGP59+8/IyMDEHCLyu/busbczLSxsfHW7TsszCwsDDBgaWkJZ7OwsSIkkAEAhsQK4wY+G1AAAAAASUVORK5CYII=',
}

for (const [name, expected] of Object.entries(FIRST_ROW_FILTER_FIXTURES)) {
  test(`首行 ${name.startsWith('up') ? 'Up' : name.startsWith('avg') ? 'Average' : 'Paeth'} 滤镜解码：上方像素按全 0（独立 fixture）`, () => {
    const img = decodePng(Buffer.from(FIXTURE_PNG[name]!, 'base64'))
    assert.equal(img.width, expected.w)
    assert.equal(img.height, expected.h)
    assert.deepEqual([...img.data], expected.rgba)
  })
}

test('首行 Paeth 轻微重绘（255→254）sameView 判定不翻转（评审复现最小化）', () => {
  const a = decodePng(Buffer.from(FIXTURE_PNG.repA!, 'base64'))
  const b = decodePng(Buffer.from(FIXTURE_PNG.repB!, 'base64'))
  // 两图仅首行一个像素通道 255→254（差 1 << 容差 60）：正确解码下 misrate 必为 0，
  // 修复前首行 Paeth 解码错会产生大量假差异（评审实测 727x1237 样本 misrate 0.0305 误判不同画面）
  const misrate = segdiffMisrate(a, b, { x: 0, y: 0, w: a.width, h: a.height })
  assert.equal(misrate, 0)
})

// ---- PNG 编码 CRC 回归（2026-10-21 外部评审 P1）----
// crc32 表索引曾缺 & 0xff 导致全部 chunk CRC 错（Pillow UnidentifiedImageError，
// 云端 slice_stitched_image 识别前置失败）。用 node:zlib 的 crc32 做独立校验源。

test('encodePng 输出的每个 chunk CRC 正确（node:zlib.crc32 独立校验）', () => {
  const w = 7, h = 5
  const data = Buffer.alloc(w * h * 4)
  for (let i = 0; i < data.length; i++) data[i] = (i * 37 + 11) & 0xff
  const png = encodePng({ width: w, height: h, data })
  let pos = 8
  let chunks = 0
  while (pos + 12 <= png.length) {
    const length = png.readUInt32BE(pos)
    const typeAndData = png.subarray(pos + 4, pos + 8 + length)
    const stored = png.readUInt32BE(pos + 8 + length)
    assert.equal(stored, zlib.crc32(typeAndData), `chunk #${chunks}（${png.subarray(pos + 4, pos + 8).toString('ascii')}）CRC 与 zlib.crc32 不一致`)
    chunks++
    pos += 12 + length
  }
  assert.ok(chunks >= 3, `至少 IHDR/IDAT/IEND 三块（got ${chunks}）`)
})
