/**
 * 实时画面的连接统计解析（纯函数，便于单测）。
 *
 * 线路判定与设计 4.1 / Runtime 侧 screenCaptureClient.ts 的 stats.js 分类规则一致：
 * getStats() 里提名成功候选对的两端地址都属内网类为「局域网直连」，否则「点对点直连」。
 * 观看统计（分辨率、帧率、码率）从 inbound-rtp 读出，按采样差值计算。
 */

type AddressKind = 'mdns' | 'loopback' | 'private' | 'shared' | 'public' | 'other'
export type ScreenPath = 'lan' | 'p2p'

export function normalizeAddress(value: unknown): string {
  return String(value ?? '').replace(/^::ffff:/i, '').replace(/%.*$/, '').toLowerCase()
}

/** 地址分类：内网类含 mDNS 名字、回环与共享地址段（与 Runtime 采集页同一套规则） */
export function classifyAddressKind(address: string | null | undefined): AddressKind {
  const normalized = normalizeAddress(address)
  if (!normalized) return 'other'
  if (/\.local$/i.test(normalized)) return 'mdns'
  if (/^(127\.|::1$)/i.test(normalized)) return 'loopback'
  // 私有地址段、IPv6 唯一本地地址（fc00::/7）与链路本地地址（fe80::/10）都算内网，与 Runtime 采集页同一套规则
  if (/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.)/.test(normalized) || /^f[cd]/i.test(normalized) || /^fe[89ab]/i.test(normalized)) return 'private'
  if (/^(198\.1[89]\.|198\.19\.|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.)/.test(normalized)) return 'shared'
  return 'public'
}

const PRIVATE_KINDS: ReadonlySet<AddressKind> = new Set(['private', 'shared', 'mdns'])

/** 两端选中的都是内网地址为局域网直连，其余为点对点直连 */
export function derivePath(local: string | null | undefined, remote: string | null | undefined): ScreenPath {
  return PRIVATE_KINDS.has(classifyAddressKind(local)) && PRIVATE_KINDS.has(classifyAddressKind(remote)) ? 'lan' : 'p2p'
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function statAddress(candidate: Record<string, unknown> | null): string | null {
  if (!candidate) return null
  const address = candidate['address']
  if (typeof address === 'string' && address) return address
  const ip = candidate['ip']
  return typeof ip === 'string' && ip ? ip : null
}

/** 提名成功候选对两端的地址分类，得出实际线路；取不到时为 null */
export function pathFromStats(report: RTCStatsReport): ScreenPath | null {
  let path: ScreenPath | null = null
  report.forEach((raw: unknown) => {
    const pair = asRecord(raw)
    if (!pair || pair['type'] !== 'candidate-pair' || pair['state'] !== 'succeeded' || pair['nominated'] !== true) return
    const localId = typeof pair['localCandidateId'] === 'string' ? pair['localCandidateId'] : ''
    const remoteId = typeof pair['remoteCandidateId'] === 'string' ? pair['remoteCandidateId'] : ''
    path = derivePath(
      statAddress(localId ? asRecord(report.get(localId)) : null),
      statAddress(remoteId ? asRecord(report.get(remoteId)) : null),
    )
  })
  return path
}

/** 一次 inbound-rtp 视频采样：累计接收字节数与已解码帧数，用于差值计算 */
export interface InboundVideoSample {
  width: number | null
  height: number | null
  /** 统计自带的瞬时帧率（Chrome 提供），可能为空 */
  framesPerSecond: number | null
  bytesReceived: number
  framesDecoded: number
  timestampMs: number
}

/** 取 inbound-rtp 里 kind 为 video 的第一条记录；没有时为 null */
export function inboundVideoStats(report: RTCStatsReport): InboundVideoSample | null {
  let sample: InboundVideoSample | null = null
  report.forEach((raw: unknown) => {
    if (sample) return
    const record = asRecord(raw)
    if (!record || record['type'] !== 'inbound-rtp') return
    const kind = record['kind']
    const mediaType = record['mediaType']
    if (kind !== 'video' && mediaType !== 'video') return
    const width = typeof record['frameWidth'] === 'number' ? record['frameWidth'] : null
    const height = typeof record['frameHeight'] === 'number' ? record['frameHeight'] : null
    const fps = typeof record['framesPerSecond'] === 'number' ? record['framesPerSecond'] : null
    const bytes = typeof record['bytesReceived'] === 'number' ? record['bytesReceived'] : 0
    const frames = typeof record['framesDecoded'] === 'number' ? record['framesDecoded'] : 0
    const timestamp = typeof record['timestamp'] === 'number' ? record['timestamp'] : 0
    sample = { width, height, framesPerSecond: fps, bytesReceived: bytes, framesDecoded: frames, timestampMs: timestamp }
  })
  return sample
}

/** 两次采样的差值：帧率与码率；第一次没有参照时帧率与码率为 null */
export interface InboundVideoDelta {
  fps: number | null
  bitrateBps: number | null
}

export function inboundVideoDelta(previous: InboundVideoSample | null, current: InboundVideoSample): InboundVideoDelta {
  if (!previous) return { fps: null, bitrateBps: null }
  let fps: number | null = current.framesPerSecond
  if (fps === null) {
    const frames = current.framesDecoded - previous.framesDecoded
    const seconds = (current.timestampMs - previous.timestampMs) / 1000
    if (seconds > 0 && frames >= 0) fps = frames / seconds
  }
  const bytes = current.bytesReceived - previous.bytesReceived
  const seconds = (current.timestampMs - previous.timestampMs) / 1000
  const bitrateBps = seconds > 0 && bytes >= 0 ? (bytes * 8) / seconds : null
  return { fps, bitrateBps }
}

/** 时长：mm:ss，满一小时用 hh:mm:ss */
export function formatDuration(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000))
  const pad = (n: number) => String(n).padStart(2, '0')
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${pad(minutes)}:${pad(seconds)}`
}

/** 码率：1 Mbps 以上一位小数，否则 kbps 取整 */
export function formatBitrate(bitrateBps: number | null): string {
  if (bitrateBps === null || !Number.isFinite(bitrateBps) || bitrateBps < 0) return '-'
  if (bitrateBps >= 1_000_000) return `${(bitrateBps / 1_000_000).toFixed(1)} Mbps`
  return `${Math.round(bitrateBps / 1000)} kbps`
}

/** 帧率显示：一位小数以内 */
export function formatFps(fps: number | null): string {
  if (fps === null || !Number.isFinite(fps) || fps < 0) return '-'
  return Number.isInteger(fps) ? String(fps) : fps.toFixed(1)
}
