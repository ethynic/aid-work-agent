/**
 * Runtime Host 与隐藏采集窗口之间的消息（设计 6.1）。两者不直接通信：Host 经壳转交，采集页经壳上报。
 * 这些类型是两端的共同约定，壳和采集页照此实现。
 *
 * 采集页不访问网络，信令全部经这里转交；同一会话内消息顺序保持不变。
 */
import type { PeerPort, ScreenPath } from './protocol.js'
import type { ScreenVerdict, VerdictRule } from './machineVerdict.js'

/** 采集页 → Host（经壳转交）。kind 不认识或必需字段缺失的消息由 Host 忽略 */
export type FromPageMessage =
  /** 连接邀约，不含画面 */
  | { kind: 'offer'; sdp: string }
  /** 本机新收集到的地址候选（candidate 这一行） */
  | { kind: 'candidate'; candidate: string; sdp_mid?: string | null; sdp_mline_index?: number | null }
  /** 本机经 STUN 得到的公网映射地址（明文） */
  | { kind: 'mapped'; address: string }
  /** 网页的一条地址候选，作为同机判定的证据 */
  | { kind: 'remote-candidate'; candidate: string }
  /** 不含画面的连接已建立 */
  | { kind: 'connected' }
  /** 连接建立后，每个成功的候选对里对端候选的端口 */
  | { kind: 'peer-ports'; ports: PeerPort[] }
  /** 开始出画面；path 是实际线路，取不到时省略 */
  | { kind: 'streaming'; path?: ScreenPath | null }
  /** 采集页自己结束了连接。reason 是 Runtime 的结束原因；不是七个合法原因之一时由 Host 按当前阶段兜底 */
  | { kind: 'closed'; reason?: string }
  /** 每 5 秒一次：编码器是否因 CPU 受限 */
  | { kind: 'cpu-limited'; limited: boolean }

/** Host → 采集页（经壳转交） */
export type ToPageMessage =
  /** 网页的应答 */
  | { kind: 'answer'; sdp: string }
  /** 网页的地址候选，按到达顺序 */
  | { kind: 'candidate'; candidate: string; sdp_mid: string | null; sdp_mline_index: number | null }
  /**
   * 网页的 mDNS 候选名解析出来了。id 是 Host 按收到 remote-candidate 的顺序从 1 开始编的号，
   * 采集页据此对上是哪一条候选；address 为 null 表示解析不出
   */
  | { kind: 'resolved'; id: number; address: string | null }
  /** 同机判定的结论和规则编号。只有 other 才可以开始采集 */
  | { kind: 'verdict'; verdict: ScreenVerdict; rule: VerdictRule }
  /** 任务运行中资源不足：降低画质（改采集轨道的约束） */
  | { kind: 'degrade' }
  /** 任务结束：恢复原来的约束 */
  | { kind: 'restore' }
