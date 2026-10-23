/**
 * 简历读取链路图像处理（2026-10-21 自 cv-segdiff.ps1 / cv-stitch.ps1 迁移，语义 1:1；
 * 2026-10-21 再迁 visual-region-compare.ps1——视觉恢复后关闭控件区域核验）。
 *
 * 职责（与 ps1 完全对应，判定口径不得漂移——决定滚动停止与接缝质量元数据）：
 *   - PNG 解码/编码：纯 Node 实现（node:zlib inflate/deflate + PNG 滤镜反处理），零第三方依赖
 *     （输入 = CDP 截图 PNG Buffer 与段图文件；输出 = 拼接长图 PNG 文件）。
 *   - downscaleToWidth180：复刻 ps1 CvStitcher/CvSegDiff 的 Downscale（GDI+ Graphics.DrawImage
 *     高质量盒式降采样：目标宽 180，权重 = 盒覆盖有理分数，先水平后垂直、中间值取整）。
 *   - segdiffMisrate：复刻 cv-segdiff.ps1 MismatchRate（canvas 区域裁剪 → 双方降采样 →
 *     x 步进 4 / y 步进 3 采样，|dR|+|dG|+|dB| > 60 计错配，misrate = bad/compared）。
 *   - findOverlap / stitchVertical：复刻 cv-stitch.ps1（重叠搜索 floor(0.10h)..ceil(0.95h)、
 *     步进 y3/x4、错配分母 compared、o = round(bestO·W/180)（.NET Math.Round 银行家舍入）、
 *     mis 输出按 InvariantCulture F2 量化；垂直拼接 total = h0 + Σ(hi - ovl)）。
 *   - regionExactEqual：复刻 visual-region-compare.ps1（恢复核验链路）：区域 ≤ 32x32、
 *     32-bit ARGB 逐像素严格全等、零容差（与 cv 系容差/降采样口径无关），
 *     区域/尺寸非法抛 ImageOpsError（对应 ps1 exit 1 → 调用方包装 STALE_OBSERVATION）。
 *
 * GDI+ 降采样复刻精度（2026-10-21 双通道实证，Windows 10 gdiplus.dll）：字节级 97.5% 精确、
 * 100% 在 ±1/255 内；残差 = gdiplus 内部定点舍入细节（逐 tap 权重量化未公开），已验证
 * 不影响判定输出（错配判定需采样点色差恰在 60±2 且 misrate 恰在 F2/F4 舍入边界才会翻转，
 * 双通道对照测试全程 0 差异；见 tests/image-ops.test.ts）。
 *
 * 裁剪越界（窗口中途变化）→ 抛 ImageOpsError fail-loud（对应 ps1 exit 1，由调用方
 * 包装 ResumeReadError 上抛）。仅支持 8-bit 非隔行 PNG（CDP 截图输出即此规格）；
 * 16-bit / Adam7 显式报错拒绝，不做静默降级。
 */
import { deflateSync, inflateSync } from 'node:zlib'

export class ImageOpsError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ImageOpsError'
  }
}

/** 8-bit RGBA 位图（解码统一表示；alpha 对 CDP 截图恒 255） */
export interface RgbaImage {
  width: number
  height: number
  /** 长度 width*height*4，逐像素 R,G,B,A */
  data: Buffer
}

// ---------------------------------------------------------------------------
// PNG 解码（IHDR/PLTE/tRNS/IDAT；8-bit，非隔行；色型 0/2/3/4/6）
// ---------------------------------------------------------------------------

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

export function decodePng(buf: Buffer): RgbaImage {
  if (buf.length < 8 || !buf.subarray(0, 8).equals(PNG_SIGNATURE)) {
    throw new ImageOpsError('非 PNG 文件（签名不匹配）')
  }
  let pos = 8
  let width = 0, height = 0, bitDepth = 0, colorType = 0, interlace = 0
  let palette: Buffer | undefined
  let trns: Buffer | undefined
  const idat: Buffer[] = []
  while (pos + 8 <= buf.length) {
    const length = buf.readUInt32BE(pos)
    const type = buf.toString('ascii', pos + 4, pos + 8)
    const data = buf.subarray(pos + 8, pos + 8 + length)
    if (pos + 12 + length > buf.length) throw new ImageOpsError('PNG 数据截断')
    if (type === 'IHDR') {
      width = data.readUInt32BE(0)
      height = data.readUInt32BE(4)
      bitDepth = data[8]!
      colorType = data[9]!
      interlace = data[12]!
    } else if (type === 'PLTE') {
      palette = Buffer.from(data)
    } else if (type === 'tRNS') {
      trns = Buffer.from(data)
    } else if (type === 'IDAT') {
      idat.push(data)
    } else if (type === 'IEND') {
      break
    }
    pos += 12 + length
  }
  if (!width || !height) throw new ImageOpsError('PNG 缺少 IHDR')
  if (bitDepth !== 8) throw new ImageOpsError(`不支持的 PNG 位深 ${bitDepth}（仅支持 8-bit，CDP 截图即此规格）`)
  if (interlace !== 0) throw new ImageOpsError('不支持隔行（Adam7）PNG')
  const channels = CHANNELS_OF[colorType]
  if (!channels) throw new ImageOpsError(`不支持的 PNG 色型 ${colorType}`)
  if (colorType === 3 && (!palette || palette.length % 3 !== 0)) throw new ImageOpsError('调色板 PNG 缺少 PLTE')
  let raw: Buffer
  try {
    raw = inflateSync(Buffer.concat(idat))
  } catch (error) {
    throw new ImageOpsError(`PNG 解压失败：${error instanceof Error ? error.message : String(error)}`)
  }
  const stride = width * channels
  if (raw.length < (stride + 1) * height) throw new ImageOpsError('PNG 像素数据不完整')
  const out = Buffer.alloc(width * height * 4)
  // 滤镜反处理（0/1/2/3/4，PNG 规格）。首行上方像素按全 0（PNG 规格语义）——
  // 不能传空 Buffer：Up/Average/Paeth 读 prevRow[i] 得 undefined（NaN 参与运算），
  // 首行像素被清零或选错预测分支（真机 CDP 截图存在首行 Paeth，曾致 7560 像素解码错）
  let zeroPrevRow: Buffer | undefined
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)]
    const rowStart = y * (stride + 1) + 1
    const row = raw.subarray(rowStart, rowStart + stride)
    let prevRow: Buffer
    if (y > 0) {
      const prevStart = (y - 1) * (stride + 1) + 1
      prevRow = raw.subarray(prevStart, prevStart + stride)
    } else {
      zeroPrevRow ??= Buffer.alloc(stride)
      prevRow = zeroPrevRow
    }
    unfilterRow(filter ?? -1, row, prevRow, channels)
    writeRgbaRow(out, y * width * 4, row, width, channels, colorType, palette, trns)
  }
  return { width, height, data: out }
}

const CHANNELS_OF: Record<number, number> = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }

function unfilterRow(filter: number, row: Buffer, prevRow: Buffer, bpp: number): void {
  switch (filter) {
    case 0:
      return
    case 1:
      for (let i = bpp; i < row.length; i++) row[i] = (row[i]! + row[i - bpp]!) & 0xff
      return
    case 2:
      for (let i = 0; i < row.length; i++) row[i] = (row[i]! + prevRow[i]!) & 0xff
      return
    case 3:
      for (let i = 0; i < row.length; i++) {
        const left = i >= bpp ? row[i - bpp]! : 0
        row[i] = (row[i]! + ((left + prevRow[i]!) >> 1)) & 0xff
      }
      return
    case 4: {
      for (let i = 0; i < row.length; i++) {
        const a = i >= bpp ? row[i - bpp]! : 0
        const b = prevRow[i]!
        const c = i >= bpp ? prevRow[i - bpp]! : 0
        const p = a + b - c
        const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c)
        const pred = pa <= pb && pa <= pc ? a : pb <= pc ? b : c
        row[i] = (row[i]! + pred) & 0xff
      }
      return
    }
    default:
      throw new ImageOpsError(`PNG 未知滤镜类型 ${filter}`)
  }
}

function writeRgbaRow(out: Buffer, outStart: number, row: Buffer, width: number, channels: number, colorType: number, palette: Buffer | undefined, trns: Buffer | undefined): void {
  for (let x = 0; x < width; x++) {
    const s = x * channels
    const d = outStart + x * 4
    if (colorType === 6) {
      out[d] = row[s]!; out[d + 1] = row[s + 1]!; out[d + 2] = row[s + 2]!; out[d + 3] = row[s + 3]!
    } else if (colorType === 2) {
      out[d] = row[s]!; out[d + 1] = row[s + 1]!; out[d + 2] = row[s + 2]!; out[d + 3] = 255
    } else if (colorType === 0) {
      const g = row[s]!
      out[d] = g; out[d + 1] = g; out[d + 2] = g; out[d + 3] = 255
    } else if (colorType === 4) {
      const g = row[s]!
      out[d] = g; out[d + 1] = g; out[d + 2] = g; out[d + 3] = row[s + 1]!
    } else {
      const idx = row[s]! * 3
      out[d] = palette![idx]!; out[d + 1] = palette![idx + 1]!; out[d + 2] = palette![idx + 2]!
      out[d + 3] = trns && row[s]! < trns.length ? trns[row[s]!]! : 255
    }
  }
}

// ---------------------------------------------------------------------------
// PNG 编码（RGBA 8-bit；逐行自适应滤镜 0-4 按最小绝对差和选择）
// ---------------------------------------------------------------------------

export function encodePng(img: RgbaImage): Buffer {
  const { width, height, data } = img
  const stride = width * 4
  const raw = Buffer.alloc((stride + 1) * height)
  const filtered = [Buffer.alloc(stride), Buffer.alloc(stride), Buffer.alloc(stride), Buffer.alloc(stride), Buffer.alloc(stride)]
  const prevRow = Buffer.alloc(stride)
  for (let y = 0; y < height; y++) {
    const row = data.subarray(y * stride, (y + 1) * stride)
    let best = 0, bestSum = Number.MAX_SAFE_INTEGER
    for (let f = 0; f <= 4; f++) {
      applyFilter(f, row, prevRow, filtered[f]!)
      let sum = 0
      for (let i = 0; i < stride; i++) sum += Math.min(filtered[f]![i]!, 256 - filtered[f]![i]!)
      if (sum < bestSum) { bestSum = sum; best = f }
    }
    raw[y * (stride + 1)] = best
    filtered[best]!.copy(raw, y * (stride + 1) + 1)
    row.copy(prevRow)
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr[8] = 8
  ihdr[9] = 6
  const chunks: Buffer[] = [PNG_SIGNATURE, pngChunk('IHDR', ihdr), pngChunk('IDAT', deflateSync(raw, { level: 6 })), pngChunk('IEND', Buffer.alloc(0))]
  return Buffer.concat(chunks)
}

function applyFilter(filter: number, row: Buffer, prevRow: Buffer, out: Buffer): void {
  switch (filter) {
    case 0:
      row.copy(out)
      return
    case 1:
      for (let i = 0; i < row.length; i++) out[i] = (row[i]! - (i >= 4 ? row[i - 4]! : 0)) & 0xff
      return
    case 2:
      for (let i = 0; i < row.length; i++) out[i] = (row[i]! - prevRow[i]!) & 0xff
      return
    case 3:
      for (let i = 0; i < row.length; i++) out[i] = (row[i]! - (((i >= 4 ? row[i - 4]! : 0) + prevRow[i]!) >> 1)) & 0xff
      return
    default:
      for (let i = 0; i < row.length; i++) {
        const a = i >= 4 ? row[i - 4]! : 0
        const b = prevRow[i]!
        const c = i >= 4 ? prevRow[i - 4]! : 0
        const p = a + b - c
        const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c)
        const pred = pa <= pb && pa <= pc ? a : pb <= pc ? b : c
        out[i] = (row[i]! - pred) & 0xff
      }
      return
  }
}

function pngChunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4)
  len.writeUInt32BE(data.length)
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(body))
  return Buffer.concat([len, body, crc])
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c >>> 0
  }
  return table
})()

function crc32(buf: Buffer): number {
  let c = 0xffffffff
  // 索引必须取低 8 位：c ^ buf[i] 可达 32 位，直接做表索引会越界读出 undefined
  // （全部 chunk CRC 错，Pillow 等严格读取端直接 UnidentifiedImageError——云端识别前置失败）
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]!) & 0xff]! ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

// ---------------------------------------------------------------------------
// 裁剪 + GDI+ 等价降采样（ps1 Downscale 复刻）
// ---------------------------------------------------------------------------

/** 裁剪越界即抛（ps1：窗口中途变化 → exit 1 fail-loud，绝不猜测） */
export function cropRect(img: RgbaImage, x: number, y: number, w: number, h: number): RgbaImage {
  if (w <= 0 || h <= 0) throw new ImageOpsError(`W/H 必须为正（got ${w}x${h}）`)
  if (x < 0 || y < 0) throw new ImageOpsError(`X/Y 必须 ≥ 0（got (${x},${y})）`)
  if (x + w > img.width || y + h > img.height) {
    throw new ImageOpsError(`裁剪区域 (${x},${y},${w}x${h}) 超出图像 ${img.width}x${img.height}`)
  }
  const out = Buffer.alloc(w * h * 4)
  for (let row = 0; row < h; row++) {
    img.data.copy(out, row * w * 4, ((y + row) * img.width + x) * 4, ((y + row) * img.width + x + w) * 4)
  }
  return { width: w, height: h, data: out }
}

/**
 * .NET Math.Round 复刻（银行家舍入：恰为 .5 时取偶）。PS 侧 Downscale 高度与
 * FindOverlap 的 o 还原都经过它；JS Math.round 是 half-up，直接用会造成判定漂移。
 */
export function mathRoundHalfEven(v: number): number {
  if (v < 0) return -mathRoundHalfEven(-v)
  const floor = Math.floor(v)
  if (v - floor === 0.5) return floor % 2 === 0 ? floor : floor + 1
  return Math.round(v)
}

/** 降采样目标宽度（ps1 CvStitcher/CvSegDiff 常量 SmallW） */
export const DOWNSCALE_WIDTH = 180
/** 错配色差容差（|dR|+|dG|+|dB| > 60 视为不匹配；WASM 重绘亚像素差异的容忍口径） */
export const COLOR_TOL = 60
/** 降采样图采样步进（ps1 XStep/YStep：GetPixel 全分辨率遍历需数分钟，降采样后步进采样） */
export const SAMPLE_X_STEP = 4
export const SAMPLE_Y_STEP = 3

/**
 * 盒式降采样到宽 180（复刻 GDI+ Graphics.DrawImage 高质量缩小，2026-10-21 双通道实证）：
 *   - 目标高 sh = max(1, Math.Round(h·180/w))（.NET 银行家舍入，逐轴相同公式）
 *   - 权重 = 目标盒 [i·src/dst, (i+1)·src/dst) 对源像素区间的有理覆盖（Σ=1，边界无质量损失）
 *   - 先水平后垂直两趟，水平趟输出取整到 8-bit（对 gdiplus 内部行为的最优逼近，见文件头）
 * r < 1（源宽 < 180，生产中 canvas ≥ 200px 不出现）时按同式执行（盒退化为点采样），口径一致。
 */
export function downscaleToWidth180(img: RgbaImage): RgbaImage {
  const sw = img.width, sh0 = img.height
  const dw = DOWNSCALE_WIDTH
  const dh = Math.max(1, mathRoundHalfEven(sh0 * dw / sw))
  if (dw === sw && dh === sh0) return img
  const wx = boxNumerators(sw, dw)
  const wy = boxNumerators(sh0, dh)
  // 水平趟：逐源行 → 180 个中间值（取整，8-bit）
  const hAcc = Buffer.alloc(dw * sh0 * 4)
  for (let y = 0; y < sh0; y++) {
    const rowStart = y * sw * 4
    for (let dx = 0; dx < dw; dx++) {
      const taps = wx[dx]!
      let accR = 0, accG = 0, accB = 0, accA = 0
      for (let t = 0; t < taps.length; t++) {
        const n = taps[t]![1]
        const s = rowStart + taps[t]![0] * 4
        accR += n * img.data[s]!
        accG += n * img.data[s + 1]!
        accB += n * img.data[s + 2]!
        accA += n * img.data[s + 3]!
      }
      const d = (y * dw + dx) * 4
      hAcc[d] = Math.round(accR / sw)
      hAcc[d + 1] = Math.round(accG / sw)
      hAcc[d + 2] = Math.round(accB / sw)
      hAcc[d + 3] = Math.round(accA / sw)
    }
  }
  // 垂直趟：逐目标行聚合中间值
  const out = Buffer.alloc(dw * dh * 4)
  for (let dy = 0; dy < dh; dy++) {
    const taps = wy[dy]!
    for (let dx = 0; dx < dw; dx++) {
      let accR = 0, accG = 0, accB = 0, accA = 0
      for (let t = 0; t < taps.length; t++) {
        const n = taps[t]![1]
        const s = (taps[t]![0] * dw + dx) * 4
        accR += n * hAcc[s]!
        accG += n * hAcc[s + 1]!
        accB += n * hAcc[s + 2]!
        accA += n * hAcc[s + 3]!
      }
      const d = (dy * dw + dx) * 4
      out[d] = Math.round(accR / sh0)
      out[d + 1] = Math.round(accG / sh0)
      out[d + 2] = Math.round(accB / sh0)
      out[d + 3] = Math.round(accA / sh0)
    }
  }
  return { width: dw, height: dh, data: out }
}

/**
 * 盒权重表：dest i 覆盖源区间 [i·src/dst, (i+1)·src/dst)（分子单位 1/dst）。
 * 返回 [pixelIndex, numerator]，权重 = numerator/srcSize（有理精确，无浮点漂移）。
 */
function boxNumerators(srcSize: number, dstSize: number): Array<Array<[number, number]>> {
  const tab: Array<Array<[number, number]>> = []
  for (let i = 0; i < dstSize; i++) {
    const left = i * srcSize
    const right = left + srcSize
    const lo = Math.max(0, Math.floor(left / dstSize))
    const hi = Math.min(srcSize - 1, Math.ceil(right / dstSize) - 1)
    const entries: Array<[number, number]> = []
    for (let p = lo; p <= hi; p++) {
      const ov = Math.min(right, (p + 1) * dstSize) - Math.max(left, p * dstSize)
      if (ov > 0) entries.push([p, ov])
    }
    tab.push(entries)
  }
  return tab
}

// ---------------------------------------------------------------------------
// cv-segdiff.ps1 语义：采样错配率
// ---------------------------------------------------------------------------

/**
 * 采样错配率（cv-segdiff.ps1 MismatchRate 1:1）：
 * 双方裁剪 rect → 各自降采样（宽 180）→ 公共区域按 x步进4/y步进3 采样，
 * |dR|+|dG|+|dB| > 60 计错配；misrate = bad/compared（compared=0 时 1.0）。
 * 返回按 PS InvariantCulture F4 量化后的值（调用方判定 ≤ SAME_VIEW_MISRATE_MAX=0.01；
 * 量化口径必须一致：真实值 0.01004 → F4 "0.0100" → 0.01 → 判同画面）。
 */
export function segdiffMisrate(a: RgbaImage, b: RgbaImage, rect: { x: number; y: number; w: number; h: number }): number {
  const sa = downscaleToWidth180(cropRect(a, rect.x, rect.y, rect.w, rect.h))
  const sb = downscaleToWidth180(cropRect(b, rect.x, rect.y, rect.w, rect.h))
  const w = Math.min(sa.width, sb.width)
  const h = Math.min(sa.height, sb.height)
  let compared = 0, bad = 0
  for (let y = 0; y < h; y += SAMPLE_Y_STEP) {
    for (let x = 0; x < w; x += SAMPLE_X_STEP) {
      const ia = (y * sa.width + x) * 4
      const ib = (y * sb.width + x) * 4
      compared++
      const d = Math.abs(sa.data[ia]! - sb.data[ib]!) + Math.abs(sa.data[ia + 1]! - sb.data[ib + 1]!) + Math.abs(sa.data[ia + 2]! - sb.data[ib + 2]!)
      if (d > COLOR_TOL) bad++
    }
  }
  // compared ≥ 1 恒成立（降采样高 ≥ 1，(0,0) 必采样）；防御性保留 ps1 的 1.0 口径
  return compared > 0 ? quantizeRatio(bad, compared, 4) : 1.0
}

/**
 * 有理数 bad/compared 的 PS InvariantCulture "F<digits>" 量化（round-half-away-from-zero）。
 * q = floor((2·bad·10^d + compared) / (2·compared)) / 10^d（全整数运算；
 * 量化后 double 与调用方解析 ps1 stdout 的 Number("0.08") 完全一致）。
 */
function quantizeRatio(bad: number, compared: number, digits: number): number {
  const scale = 10 ** digits
  const q = Math.floor((2 * bad * scale + compared) / (2 * compared))
  return q / scale
}

// ---------------------------------------------------------------------------
// cv-stitch.ps1 语义：重叠对齐 + 垂直拼接
// ---------------------------------------------------------------------------

/** 重叠搜索范围（ps1 LoRatio/HiRatio：段高的 10%..95%） */
export const OVERLAP_LO_RATIO = 0.10
export const OVERLAP_HI_RATIO = 0.95

export interface OverlapResult {
  /** 原图（未降采样）像素行的重叠行数：o = round(bestO·a.width/180)（银行家舍入），钳到双段高 */
  overlap: number
  /** 最优错配率（PS 侧按 F2 量化后输出 mis=xx.xx） */
  mis: number
}

/**
 * 重叠对齐（cv-stitch.ps1 FindOverlap 1:1）：在降采样图上搜 os ∈ [floor(0.10h), ceil(0.95h)]
 * （hi 钳到 h-1），逐 os 对比「A 底部 os 行 vs B 顶部 os 行」采样错配率，mis 严格更小才更新
 * （并列取先到者，与 ps1 `mis < bestMis` 一致）；o 还原到原图行数并钳到段高。
 */
export function findOverlap(a: RgbaImage, b: RgbaImage): OverlapResult {
  const sa = downscaleToWidth180(a)
  const sb = downscaleToWidth180(b)
  const h = Math.min(sa.height, sb.height)
  const lo = Math.max(0, Math.floor(h * OVERLAP_LO_RATIO))
  let hi = Math.ceil(h * OVERLAP_HI_RATIO)
  if (hi >= h) hi = h - 1
  if (hi < lo) return { overlap: 0, mis: quantizeRatio(1, 1, 2) }
  let bestO = lo
  let bestBad = -1
  let bestCompared = 1
  for (let os = lo; os <= hi; os++) {
    let compared = 0, bad = 0
    for (let y = 0; y < os; y += SAMPLE_Y_STEP) {
      const ra = (sa.height - os + y) * sa.width * 4
      const rb = y * sb.width * 4
      for (let x = 0; x < sa.width; x += SAMPLE_X_STEP) {
        const ia = ra + x * 4
        const ib = rb + x * 4
        compared++
        const d = Math.abs(sa.data[ia]! - sb.data[ib]!) + Math.abs(sa.data[ia + 1]! - sb.data[ib + 1]!) + Math.abs(sa.data[ia + 2]! - sb.data[ib + 2]!)
        if (d > COLOR_TOL) bad++
      }
    }
    // mis = compared>0 ? bad/compared : 1.0（ps1 同款：os=0 无采样点 → 1.0）；
    // 严格小于才替换（首达并列保留），整数交叉相乘比较避免任何浮点歧义
    const misN = compared > 0 ? bad : 1
    const misD = compared > 0 ? compared : 1
    if (bestBad < 0 || misN * bestCompared < bestBad * misD) {
      bestBad = misN
      bestCompared = misD
      bestO = os
    }
  }
  const mis = quantizeRatio(bestBad, bestCompared, 2)
  let o = mathRoundHalfEven(bestO * a.width / DOWNSCALE_WIDTH)
  if (o > a.height) o = a.height
  if (o > b.height) o = b.height
  return { overlap: o, mis }
}

/**
 * 垂直拼接（cv-stitch.ps1 StitchVertical 1:1）：
 * total = h0 + Σ(hi - ovl[i-1])；越界/非正抛错（ps1 InvalidOperationException → exit 1）。
 * 按段序依次绘制，重叠区后段覆盖前段（DrawImage 直拷贝等价；段图不透明）。
 */
export function stitchVertical(parts: RgbaImage[], overlaps: number[]): RgbaImage {
  const w = parts[0]!.width
  let total = parts[0]!.height
  for (let i = 1; i < parts.length; i++) total += parts[i]!.height - overlaps[i - 1]!
  if (total < 1 || total > 2147483647) {
    throw new ImageOpsError(`拼接高度越界：${total}`)
  }
  const out = Buffer.alloc(w * total * 4)
  let y = 0
  for (let i = 0; i < parts.length; i++) {
    if (i > 0) y -= overlaps[i - 1]!
    const part = parts[i]!
    part.data.copy(out, y * w * 4)
    y += part.height
  }
  return { width: w, height: total, data: out }
}

// ---------------------------------------------------------------------------
// visual-region-compare.ps1 语义：区域逐像素全等（视觉恢复后核验）
// ---------------------------------------------------------------------------

/** 区域边长上限（ps1 Width/Height ≤ 32 守卫，限定 GetPixel 遍历规模） */
export const REGION_COMPARE_MAX_SIDE = 32

/**
 * 区域逐像素全等（visual-region-compare.ps1 1:1）：双方按 32-bit ARGB（R/G/B/A 四通道）
 * 严格相等比较，任一像素不等立即 false；无色差容差、无降采样、无采样步进——与 cv 系
 * COLOR_TOL/降采样口径完全不同：这里判定「关闭控件是否原样仍在」，区域外噪声（光标/
 * 插入符像素）不进入比较。双方尺寸已由文件级入口校验一致。
 */
export function regionExactEqual(a: RgbaImage, b: RgbaImage, rect: { x: number; y: number; w: number; h: number }): boolean {
  for (let dy = 0; dy < rect.h; dy++) {
    for (let dx = 0; dx < rect.w; dx++) {
      const i = ((rect.y + dy) * a.width + rect.x + dx) * 4
      const j = ((rect.y + dy) * b.width + rect.x + dx) * 4
      if (a.data[i] !== b.data[j] || a.data[i + 1] !== b.data[j + 1] || a.data[i + 2] !== b.data[j + 2] || a.data[i + 3] !== b.data[j + 3]) {
        return false
      }
    }
  }
  return true
}

// ---------------------------------------------------------------------------
// 文件级入口（读取链路调用方用；纯函数之上的薄封装）
// ---------------------------------------------------------------------------

/** 读 PNG 文件并解码（文件缺失/损坏 → ImageOpsError，fail-loud 对应 ps1 exit 1） */
export function readPngFile(file: string, readFile: (f: string) => Buffer): RgbaImage {
  let bytes: Buffer
  try {
    bytes = readFile(file)
  } catch (error) {
    throw new ImageOpsError(`图像读取失败 ${file}：${error instanceof Error ? error.message : String(error)}`)
  }
  return decodePng(bytes)
}

/** cv-stitch.ps1 主流程（1:1）：裁剪 rect → 相邻段重叠对齐 → 垂直拼接 → PNG 落盘。
 * 返回 stitched 尺寸与每接缝 overlap/mis（mis 已按 ps1 stdout 的 F2 口径量化）。 */
export function stitchPartsImage(
  parts: ReadonlyArray<string>,
  rect: { x: number; y: number; w: number; h: number },
  outFile: string,
  io: { readFile: (f: string) => Buffer; writeFile: (f: string, data: Buffer) => void },
): { width: number; height: number; overlaps: number[]; seamMis: number[] } {
  if (parts.length === 0) throw new ImageOpsError('Parts 为空')
  if (parts.length > 64) throw new ImageOpsError(`段数过多（${parts.length}），拒绝拼接`)
  if (rect.w <= 0 || rect.h <= 0) throw new ImageOpsError(`W/H 必须为正（got ${rect.w}x${rect.h}）`)
  if (rect.x < 0 || rect.y < 0) throw new ImageOpsError(`X/Y 必须 ≥ 0（got (${rect.x},${rect.y})）`)
  const crops: RgbaImage[] = []
  for (const file of parts) {
    const img = readPngFile(file, io.readFile)
    if (rect.x + rect.w > img.width || rect.y + rect.h > img.height) {
      throw new ImageOpsError(`裁剪区域 (${rect.x},${rect.y},${rect.w}x${rect.h}) 超出图像 ${img.width}x${img.height}：${file}`)
    }
    crops.push(cropRect(img, rect.x, rect.y, rect.w, rect.h))
  }
  const overlaps: number[] = []
  const seamMis: number[] = []
  for (let i = 1; i < crops.length; i++) {
    const r = findOverlap(crops[i - 1]!, crops[i]!)
    overlaps.push(r.overlap)
    seamMis.push(r.mis)
  }
  const stitched = stitchVertical(crops, overlaps)
  try {
    io.writeFile(outFile, encodePng(stitched))
  } catch (error) {
    throw new ImageOpsError(`拼接图写入失败 ${outFile}：${error instanceof Error ? error.message : String(error)}`)
  }
  return { width: stitched.width, height: stitched.height, overlaps, seamMis }
}

/** cv-segdiff.ps1 主流程（1:1）：两段整页截图的 canvas 区域像素级同画面确认。
 * 返回按 ps1 stdout misrate F4 量化后的错配率（调用方与 SAME_VIEW_MISRATE_MAX 比较）。 */
export function segdiffMisrateFiles(
  a: string,
  b: string,
  rect: { x: number; y: number; w: number; h: number },
  readFile: (f: string) => Buffer,
): number {
  if (rect.w <= 0 || rect.h <= 0) throw new ImageOpsError(`W/H 必须为正（got ${rect.w}x${rect.h}）`)
  if (rect.x < 0 || rect.y < 0) throw new ImageOpsError(`X/Y 必须 ≥ 0（got (${rect.x},${rect.y})）`)
  const imgA = readPngFile(a, readFile)
  const imgB = readPngFile(b, readFile)
  for (const [img, name] of [[imgA, a], [imgB, b]] as const) {
    if (rect.x + rect.w > img.width || rect.y + rect.h > img.height) {
      throw new ImageOpsError(`裁剪区域 (${rect.x},${rect.y},${rect.w}x${rect.h}) 超出图像 ${img.width}x${img.height}：${name}`)
    }
  }
  return segdiffMisrate(imgA, imgB, rect)
}

/** visual-region-compare.ps1 主流程（1:1）：两图指定区域逐像素全等比较，返回 equal 布尔
 * （ps1 stdout `{"equal":bool}`，调用方消费 JSON.parse(...).equal === true）。
 * 图像加载失败（文件缺失/非 PNG）与区域非法（ps1 单一条件 throw 'Invalid visual
 * comparison region' → exit 1）统一抛 ImageOpsError，由调用方包装为 STALE_OBSERVATION
 * ——判定口径与 ps1 一致：
 *   - 坐标/宽高按 ps1 [int] 参数绑定口径强转：数值经 double→Math.Round 银行家舍入
 *     （'4.5'→4、'5.5'→6，2026-10-21 双通道实证），非有限值对应绑定失败 → 非法；
 *   - X/Y ≥ 0，1 ≤ W/H ≤ 32，两图尺寸一致，区域不越界。生产调用方恒传整数，
 *     强转复刻仅为语义完备。 */
export function regionExactEqualFiles(
  original: string,
  fresh: string,
  rect: { x: number; y: number; w: number; h: number },
  readFile: (f: string) => Buffer,
): boolean {
  const imgA = readPngFile(original, readFile)
  const imgB = readPngFile(fresh, readFile)
  const rounded = [rect.x, rect.y, rect.w, rect.h].map(v =>
    typeof v === 'number' && Number.isFinite(v) ? mathRoundHalfEven(v) : Number.NaN)
  const [x, y, w, h] = rounded as [number, number, number, number]
  if (Number.isNaN(x) || Number.isNaN(y) || Number.isNaN(w) || Number.isNaN(h)
    || x < 0 || y < 0 || w < 1 || h < 1
    || w > REGION_COMPARE_MAX_SIDE || h > REGION_COMPARE_MAX_SIDE
    || imgA.width !== imgB.width || imgA.height !== imgB.height
    || x + w > imgA.width || y + h > imgA.height) {
    throw new ImageOpsError('Invalid visual comparison region')
  }
  return regionExactEqual(imgA, imgB, { x, y, w, h })
}
