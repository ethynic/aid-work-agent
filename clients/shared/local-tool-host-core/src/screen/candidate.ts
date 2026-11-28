/**
 * 地址候选的分类，从验证程序 spikes/runtime-screen-stream-poc/stats.js 的 classify 搬来并类型化。
 *
 * 分类决定两件事：两端公网出口不同时，内网类候选（private、shared、mdns）不发给服务器（设计 4.1）；
 * 同机判定里，一条候选是 mDNS 名字还是明文地址（设计 4.2）。
 *
 * candidate 这一行形如 `candidate:<foundation> <component> <proto> <priority> <address> <port> typ <type> ...`，
 * 连接统计里的候选则直接带 address / type 字段，两种输入都认。
 */

export type CandidateKind = 'mdns' | 'loopback' | 'private' | 'shared' | 'public' | 'other'

/** 一条候选：candidate 这一行本身，或已经带 address / type 字段的对象 */
export type CandidateInput = string | {
  candidate?: unknown
  address?: unknown
  ip?: unknown
  type?: unknown
  candidateType?: unknown
}

export interface CandidateClass {
  /** 候选类型：host、srflx、prflx、relay */
  type: string
  kind: CandidateKind
  proto: string
  port: number
}

/** 去掉 IPv4 映射前缀和区域标识（% 之后），转小写；不是字符串或为空时返回空串 */
export function normalizeAddress(value: unknown): string {
  return String(value || '').replace(/^::ffff:/i, '').replace(/%.*$/, '').toLowerCase()
}

function partsOf(candidate: CandidateInput): string[] {
  const text = typeof candidate === 'string' ? candidate : String(candidate.candidate || '')
  return text.split(' ')
}

/** 候选里的地址或 mDNS 名字（第 5 段）；对象带 address / ip 字段时取字段 */
export function candidateHost(candidate: CandidateInput): string {
  const explicit = typeof candidate === 'string' ? '' : candidate.address || candidate.ip
  const parts = partsOf(candidate)
  return String(explicit || parts[4] || '')
}

export function classifyCandidate(candidate: CandidateInput): CandidateClass {
  const parts = partsOf(candidate)
  const address = normalizeAddress(candidateHost(candidate))
  const given = typeof candidate === 'string' ? '' : candidate.type || candidate.candidateType
  const type = String(given || parts[7] || '').replace(/^a=/, '')
  let kind: CandidateKind = 'other'
  if (/\.local$/i.test(address)) kind = 'mdns'
  else if (/^(127\.|::1$)/i.test(address)) kind = 'loopback'
  // 私有地址段、IPv6 唯一本地地址（fc00::/7）和链路本地地址（fe80::/10，本机网卡上常见的 IPv6 地址）都算内网
  else if (/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.)/.test(address) || /^f[cd]/.test(address) || /^fe[89ab]/.test(address)) kind = 'private'
  // 共享地址段：运营商级 NAT 用的 100.64.0.0/10 和基准测试用的 198.18.0.0/15
  else if (/^(198\.1[89]\.|198\.19\.|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.)/.test(address)) kind = 'shared'
  else if (address) kind = 'public'
  return { type, kind, proto: parts[2] || '', port: Number(parts[5]) || 0 }
}
