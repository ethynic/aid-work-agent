/**
 * 同一台电脑的识别（设计 4.2）。结论有三种，只有「确认不是本机」才开始采集，
 * 所以证据不足时必须落到无法确认，而不是放行。
 *
 * 规则 1～4 从验证程序 spikes/runtime-screen-stream-poc/verdict.js 原样搬来，行为不变，
 * 只用两端明文可见的地址候选。规则 5、6 是 2026-11-27 新增的：名字解析不出时（跨网段就是这样），
 * 看连接建立后对端连入用的端口是不是本机的套接字。浏览器不提供对端的实际地址，只给端口。
 */
import { normalizeAddress } from './candidate.js'
import type { PeerPort } from './protocol.js'

export type ScreenVerdict = 'same' | 'other' | 'unknown'
export type VerdictRule = 1 | 2 | 3 | 4 | 5 | 6

/** 证据摘要：只有计数和是否不同，不含地址，可以直接写日志 */
export interface VerdictEvidence {
  viewerCandidates: number
  viewerResolved: number
  viewerMapped: number
  runtimeMapped: number
  /** 是否有可比且与本机不同的网页映射地址 */
  mappedDiffers: boolean
}

/** 网页的一条地址候选。address 是明文地址，或 mDNS 名字解析出的地址；解析不出为 null */
export interface RemoteCandidate {
  type: string
  address: string | null
}

export interface AddressVerdictInput {
  /** 本机全部网卡地址 */
  locals: readonly string[]
  /** 本机经 STUN 得到的公网映射地址 */
  mapped: readonly string[]
  remotes: readonly RemoteCandidate[]
}

export interface AddressVerdict {
  verdict: ScreenVerdict
  rule: 1 | 2 | 3 | 4
  evidence: VerdictEvidence
}

/** 查本机端口表的结果：true / false 是查到了，null 是端口表查不到（不算证据） */
export type PortLookup = boolean | null
export type IsLocalPort = (port: number, protocol: PeerPort['protocol']) => PortLookup | Promise<PortLookup>

export interface PeerPortVerdict {
  verdict: ScreenVerdict
  rule: 4 | 5 | 6
}

function uniq(list: readonly string[]): string[] {
  return [...new Set(list.map(normalizeAddress).filter(Boolean))]
}

function family(ip: string): number {
  return ip.includes(':') ? 6 : 4
}

/** 规则 1～4：只凭地址候选下结论 */
export function judgeByAddresses(input: AddressVerdictInput): AddressVerdict {
  const mine = uniq(input.locals)
  const myMapped = uniq(input.mapped)
  const all = input.remotes
  const seen = all
    .map((remote) => ({ type: remote.type, address: normalizeAddress(remote.address) || null }))
    .filter((remote): remote is { type: string; address: string } => remote.address !== null)
  const viewerMapped = seen.filter((remote) => remote.type === 'srflx')
  // 「可比」指同一种地址族里本机也拿到了映射地址。本机一个都没拿到时，网页的映射地址说明不了任何事
  const comparable = (remote: { address: string }) => myMapped.some((ip) => family(ip) === family(remote.address))
  const differs = (remote: { address: string }) => comparable(remote) && !myMapped.includes(remote.address)
  const evidence: VerdictEvidence = {
    viewerCandidates: all.length,
    viewerResolved: seen.length,
    viewerMapped: viewerMapped.length,
    runtimeMapped: myMapped.length,
    mappedDiffers: viewerMapped.some(differs),
  }
  // 规则 1：网页任一候选的地址是本机网卡地址 → 同机。证据互相矛盾时同机优先
  if (seen.some((remote) => mine.includes(remote.address))) return { verdict: 'same', rule: 1, evidence }
  // 规则 2：网页的内网候选解析出的地址不是本机网卡地址 → 同网段的另一台电脑
  if (seen.some((remote) => remote.type === 'host' && !mine.includes(remote.address))) return { verdict: 'other', rule: 2, evidence }
  // 规则 3：双方的公网映射地址可比，且网页的有不在本机映射地址里的 → 另一个网络的电脑
  if (evidence.mappedDiffers) return { verdict: 'other', rule: 3, evidence }
  // 规则 4：其余 → 无法确认，不采集
  return { verdict: 'unknown', rule: 4, evidence }
}

/** 规则 5、6：凭连接建立后对端候选的端口下结论。一个端口都没报来时不去查端口表 */
export async function judgeByPeerPorts(ports: readonly PeerPort[], isLocalPort: IsLocalPort): Promise<PeerPortVerdict> {
  if (ports.length === 0) return { verdict: 'unknown', rule: 4 }
  const lookups = await Promise.all(ports.map((peer) => isLocalPort(peer.port, peer.protocol)))
  // 有任何一个端口确定不属于本机 → 另一台电脑（规则 6）
  if (lookups.includes(false)) return { verdict: 'other', rule: 6 }
  // 全部确定是本机的套接字 → 同机（规则 5）；夹杂着查不到的不能算
  if (lookups.every((lookup) => lookup === true)) return { verdict: 'same', rule: 5 }
  return { verdict: 'unknown', rule: 4 }
}
