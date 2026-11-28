/**
 * 实时画面统计解析（纯函数）测试
 *
 * 验证点：线路判定（提名成功候选对两端地址分类）、inbound 采样差值（帧率/码率）、
 * 展示格式化。线路规则与 Runtime 采集页 screenCaptureClient.ts 保持一致。
 */
import { describe, expect, it } from 'vitest'
import {
  classifyAddressKind,
  derivePath,
  formatBitrate,
  formatDuration,
  formatFps,
  inboundVideoDelta,
  inboundVideoStats,
  pathFromStats,
} from '@/composables/screenViewerStats'

/** 构造一个可遍历、可按 id 取记录的假 stats report */
function makeReport(records: Array<Record<string, unknown>>) {
  return {
    forEach(callback: (record: unknown) => void) {
      records.forEach((record) => callback(record))
    },
    get(id: string) {
      return records.find((record) => record['id'] === id)
    },
  } as unknown as RTCStatsReport
}

describe('classifyAddressKind / derivePath', () => {
  it('内网地址与 mDNS 名字归为内网类，其余为公网', () => {
    expect(classifyAddressKind('192.168.1.10')).toBe('private')
    expect(classifyAddressKind('10.0.0.5')).toBe('private')
    expect(classifyAddressKind('172.20.3.4')).toBe('private')
    expect(classifyAddressKind('fd00::1234')).toBe('private')
    // IPv6 链路本地地址（fe80::/10）算内网，与 Runtime candidate.ts 同规则
    expect(classifyAddressKind('fe80::1234')).toBe('private')
    expect(classifyAddressKind('FE80::ABCD')).toBe('private')
    expect(classifyAddressKind('abcd-1234-5678-90ab.local')).toBe('mdns')
    expect(classifyAddressKind('127.0.0.1')).toBe('loopback')
    expect(classifyAddressKind('100.64.0.1')).toBe('shared')
    expect(classifyAddressKind('203.0.113.8')).toBe('public')
    expect(classifyAddressKind('')).toBe('other')
  })

  it('两端都是内网地址为局域网直连，否则点对点直连', () => {
    expect(derivePath('192.168.1.10', '192.168.1.20')).toBe('lan')
    expect(derivePath('abcd-1234.local', '10.0.0.5')).toBe('lan')
    expect(derivePath('192.168.1.10', '203.0.113.8')).toBe('p2p')
    expect(derivePath(null, null)).toBe('p2p')
  })
})

describe('pathFromStats', () => {
  function pairRecords(localAddress: string, remoteAddress: string) {
    return [
      { id: 'lc', type: 'local-candidate', address: localAddress },
      { id: 'rc', type: 'remote-candidate', address: remoteAddress },
      { id: 'pair', type: 'candidate-pair', state: 'succeeded', nominated: true, localCandidateId: 'lc', remoteCandidateId: 'rc' },
      { id: 'pair2', type: 'candidate-pair', state: 'succeeded', nominated: false, localCandidateId: 'lc', remoteCandidateId: 'rc' },
    ]
  }

  it('提名成功候选对两端都是内网地址 → 局域网直连', () => {
    expect(pathFromStats(makeReport(pairRecords('192.168.1.10', '192.168.5.20')))).toBe('lan')
  })

  it('远端是公网映射地址 → 点对点直连', () => {
    expect(pathFromStats(makeReport(pairRecords('192.168.1.10', '203.0.113.8')))).toBe('p2p')
  })

  it('没有成功候选对时为 null（线路未知）', () => {
    expect(pathFromStats(makeReport([
      { id: 'pair', type: 'candidate-pair', state: 'in-progress', nominated: true },
    ]))).toBe(null)
  })
})

describe('inboundVideoStats / inboundVideoDelta', () => {
  it('取 inbound-rtp 里 kind 为 video 的记录', () => {
    const report = makeReport([
      { type: 'inbound-rtp', kind: 'audio', bytesReceived: 1 },
      { type: 'inbound-rtp', kind: 'video', frameWidth: 1280, frameHeight: 720, framesPerSecond: 15, bytesReceived: 1000, framesDecoded: 10, timestamp: 1000 },
    ])
    const sample = inboundVideoStats(report)
    expect(sample).toMatchObject({ width: 1280, height: 720, framesPerSecond: 15, bytesReceived: 1000, framesDecoded: 10, timestampMs: 1000 })
  })

  it('没有视频记录时为 null', () => {
    expect(inboundVideoStats(makeReport([{ type: 'outbound-rtp', kind: 'video' }]))).toBe(null)
  })

  it('第一次采样没有参照：帧率与码率为 null', () => {
    const sample = { width: 1280, height: 720, framesPerSecond: null, bytesReceived: 1000, framesDecoded: 10, timestampMs: 1000 }
    expect(inboundVideoDelta(null, sample)).toEqual({ fps: null, bitrateBps: null })
  })

  it('按两次采样的差值计算：统计缺帧率时用帧数差，码率按字节差', () => {
    const previous = { width: 1280, height: 720, framesPerSecond: null, bytesReceived: 1000, framesDecoded: 10, timestampMs: 1000 }
    const current = { width: 1280, height: 720, framesPerSecond: null, bytesReceived: 250_000, framesDecoded: 40, timestampMs: 3000 }
    const delta = inboundVideoDelta(previous, current)
    expect(delta.fps).toBe(15) // 30 帧 / 2 秒
    expect(delta.bitrateBps).toBe((249_000 * 8) / 2)
  })

  it('统计自带瞬时帧率时优先使用', () => {
    const previous = { width: 1280, height: 720, framesPerSecond: null, bytesReceived: 0, framesDecoded: 0, timestampMs: 0 }
    const current = { width: 1280, height: 720, framesPerSecond: 5, bytesReceived: 0, framesDecoded: 99, timestampMs: 2000 }
    expect(inboundVideoDelta(previous, current).fps).toBe(5)
  })
})

describe('展示格式化', () => {
  it('时长：分秒，满一小时带小时位', () => {
    expect(formatDuration(0)).toBe('00:00')
    expect(formatDuration(65_000)).toBe('01:05')
    expect(formatDuration(3_723_000)).toBe('1:02:03')
  })

  it('码率：1 Mbps 以上一位小数，否则 kbps 取整；无数据为 -', () => {
    expect(formatBitrate(2_500_000)).toBe('2.5 Mbps')
    expect(formatBitrate(850_400)).toBe('850 kbps')
    expect(formatBitrate(null)).toBe('-')
    expect(formatBitrate(NaN)).toBe('-')
  })

  it('帧率：整数不带小数，小数保留一位；无数据为 -', () => {
    expect(formatFps(15)).toBe('15')
    expect(formatFps(14.87)).toBe('14.9')
    expect(formatFps(null)).toBe('-')
  })
})
