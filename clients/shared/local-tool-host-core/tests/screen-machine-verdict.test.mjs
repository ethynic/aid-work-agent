import assert from 'node:assert/strict'
import { test } from 'node:test'
import { judgeByAddresses, judgeByPeerPorts } from '../dist/screen/machineVerdict.js'

// 同一台电脑的识别（设计 4.2）。只有「确认不是本机」才开始采集，所以拿不到证据时必须落到无法确认。
// 规则 1～4 的 21 条用例从验证程序 verdict.test.cjs 原样搬来，行为不变；规则 5、6 是 2026-11-27 新增的端口判定。

const PC = ['192.168.1.5', 'fe80::a1%ethernet', '127.0.0.1', '::1'] // Runtime 这台电脑的网卡地址
const MAP = ['1.2.3.4'] // Runtime 的公网映射地址
const CASES = [
  // —— 同一台电脑 ——
  ['同机，名字解析出本机地址', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: '192.168.1.5' }, { type: 'srflx', address: '1.2.3.4' }] }, 'same', 1],
  ['同机，名字解析不出来：两端映射地址相同，不能放行', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: null }, { type: 'srflx', address: '1.2.3.4' }] }, 'unknown', 4],
  ['同机，Runtime 的 STUN 不通：本机没有映射地址，不能放行', { locals: PC, mapped: [], remotes: [{ type: 'host', address: null }, { type: 'srflx', address: '1.2.3.4' }] }, 'unknown', 4],
  ['同机，两端都没有 STUN，名字解析不出来', { locals: PC, mapped: [], remotes: [{ type: 'host', address: null }] }, 'unknown', 4],
  ['同机，没有 STUN，但名字解析出本机地址', { locals: PC, mapped: [], remotes: [{ type: 'host', address: '127.0.0.1' }] }, 'same', 1],
  ['同机，网页没有公布任何候选', { locals: PC, mapped: MAP, remotes: [] }, 'unknown', 4],
  ['同机，网页公布的是明文本机地址（不隐藏地址的浏览器）', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: '192.168.1.5' }] }, 'same', 1],
  ['同机，IPv6 带区域标识', { locals: PC, mapped: [], remotes: [{ type: 'host', address: 'FE80::A1%12' }] }, 'same', 1],
  ['同机，IPv4 映射的 IPv6 写法', { locals: PC, mapped: [], remotes: [{ type: 'host', address: '::ffff:192.168.1.5' }] }, 'same', 1],
  ['同机，证据互相矛盾时同机优先', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: '192.168.1.5' }, { type: 'srflx', address: '5.6.7.8' }] }, 'same', 1],
  // —— 同网段的另一台电脑 ——
  ['同网段另一台，名字解析出别的地址', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: '192.168.1.7' }, { type: 'srflx', address: '1.2.3.4' }] }, 'other', 2],
  ['同网段另一台，公布明文地址', { locals: PC, mapped: [], remotes: [{ type: 'host', address: '10.0.0.9' }] }, 'other', 2],
  ['同网段另一台，名字解析不出来：保持不采集', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: null }, { type: 'srflx', address: '1.2.3.4' }] }, 'unknown', 4],
  // —— 另一个网络的电脑 ——
  ['跨网络，两端映射地址不同', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: null }, { type: 'srflx', address: '5.6.7.8' }] }, 'other', 3],
  ['跨网络，Runtime 的 STUN 不通：没有可比的映射地址，不能放行', { locals: PC, mapped: [], remotes: [{ type: 'host', address: null }, { type: 'srflx', address: '5.6.7.8' }] }, 'unknown', 4],
  ['本机只有 IPv4 映射地址，网页只有 IPv6 映射地址：不可比，不能放行', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: null }, { type: 'srflx', address: '2408:8207::99' }] }, 'unknown', 4],
  ['跨网络，IPv6 映射地址不同', { locals: PC, mapped: [...MAP, '2408:8207::5'], remotes: [{ type: 'host', address: null }, { type: 'srflx', address: '240e:1::7' }] }, 'other', 3],
  ['跨网络，网页的 STUN 不通，名字也解析不出来', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: null }] }, 'unknown', 4],
  ['两个网络共用同一个公网出口（运营商级 NAT），名字解析不出来', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: null }, { type: 'srflx', address: '1.2.3.4' }] }, 'unknown', 4],
  // —— 已知局限（设计 4.2 接受的例外）：同一台电脑上的浏览器单独走另一条出口 ——
  ['同机但浏览器走了另一条出口，名字解析不出来：会被判成另一台电脑（已知局限）', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: null }, { type: 'srflx', address: '5.6.7.8' }] }, 'other', 3],
  ['同机但浏览器走了另一条出口，名字解析出本机地址：仍然判同机', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: '192.168.1.5' }, { type: 'srflx', address: '5.6.7.8' }] }, 'same', 1],
]

test('规则 1～4：验证程序的 21 条用例', () => {
  assert.equal(CASES.length, 21)
  for (const [name, input, verdict, rule] of CASES) {
    const got = judgeByAddresses(input)
    assert.equal(got.verdict, verdict, `${name}：应为 ${verdict}，实际 ${got.verdict}`)
    assert.equal(got.rule, rule, `${name}：应为规则 ${rule}，实际规则 ${got.rule}`)
  }
})

test('先判同机：网页的内网候选里既有本机地址、又有别的地址时，判同机（设计 4.2 的优先级）', () => {
  const got = judgeByAddresses({ locals: PC, mapped: MAP, remotes: [{ type: 'host', address: '192.168.1.5' }, { type: 'host', address: '10.0.0.9' }] })
  assert.deepEqual([got.verdict, got.rule], ['same', 1])
})

test('本机地址先归一化再去重，所以写法不同也能比对', () => {
  const input = { locals: ['::FFFF:192.168.1.5', '192.168.1.5', 'FE80::A1%12'], mapped: [], remotes: [{ type: 'host', address: 'fe80::a1%ethernet' }] }
  assert.equal(judgeByAddresses(input).verdict, 'same')
  const evidence = judgeByAddresses({ ...input, remotes: [{ type: 'srflx', address: '::ffff:1.2.3.4' }] })
  assert.deepEqual(evidence, {
    verdict: 'unknown', rule: 4,
    evidence: { viewerCandidates: 1, viewerResolved: 1, viewerMapped: 1, runtimeMapped: 0, mappedDiffers: false },
  })
})

test('证据摘要里只有计数和是否不同，不含地址', () => {
  const { evidence } = judgeByAddresses({ locals: PC, mapped: MAP, remotes: [{ type: 'host', address: null }, { type: 'srflx', address: '5.6.7.8' }] })
  assert.deepEqual(evidence, { viewerCandidates: 2, viewerResolved: 1, viewerMapped: 1, runtimeMapped: 1, mappedDiffers: true })
})

test('规则 5：对端端口全部是本机的套接字，判同机；每种协议各查各的表', async () => {
  const asked = []
  const isLocalPort = async (port, protocol) => { asked.push([port, protocol]); return true }
  const got = await judgeByPeerPorts([{ port: 53000, protocol: 'udp' }, { port: 53000, protocol: 'tcp' }, { port: 40001, protocol: 'udp' }], isLocalPort)
  assert.deepEqual(asked, [[53000, 'udp'], [53000, 'tcp'], [40001, 'udp']])
  assert.deepEqual(got, { verdict: 'same', rule: 5 })
})

test('规则 6：有任何一个对端端口不属于本机，判另一台电脑', async () => {
  const mixed = await judgeByPeerPorts([{ port: 53000, protocol: 'udp' }, { port: 40001, protocol: 'udp' }], async port => port === 53000)
  assert.deepEqual(mixed, { verdict: 'other', rule: 6 })
  const none = await judgeByPeerPorts([{ port: 53000, protocol: 'tcp' }], async () => false)
  assert.deepEqual(none, { verdict: 'other', rule: 6 })
})

test('一个对端端口都没查到时保持无法确认，不去查端口表', async () => {
  let asked = 0
  const got = await judgeByPeerPorts([], async () => { asked++; return true })
  assert.deepEqual(got, { verdict: 'unknown', rule: 4 })
  assert.equal(asked, 0)
})

test('端口表查不到（null）不算证据：全查不到或夹杂着查不到都不能放行，也不能判同机', async () => {
  const answers = { 1: null, 2: true, 3: false }
  const lookup = async port => answers[port]
  assert.deepEqual(await judgeByPeerPorts([{ port: 1, protocol: 'udp' }], lookup), { verdict: 'unknown', rule: 4 })
  assert.deepEqual(await judgeByPeerPorts([{ port: 2, protocol: 'udp' }, { port: 1, protocol: 'udp' }], lookup), { verdict: 'unknown', rule: 4 })
  // 已经有一个端口确定不属于本机，就足以按规则 6 判定，其余的查不到不影响
  assert.deepEqual(await judgeByPeerPorts([{ port: 3, protocol: 'udp' }, { port: 1, protocol: 'udp' }], lookup), { verdict: 'other', rule: 6 })
})

test('查端口表的函数抛出异常时向上传递，由调用方决定怎么处理', async () => {
  await assert.rejects(judgeByPeerPorts([{ port: 53000, protocol: 'udp' }], async () => { throw new Error('netstat unavailable') }), /netstat unavailable/)
})
