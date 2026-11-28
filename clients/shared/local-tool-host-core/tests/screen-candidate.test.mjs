import assert from 'node:assert/strict'
import { test } from 'node:test'
import { candidateHost, classifyCandidate, normalizeAddress } from '../dist/screen/candidate.js'

// 地址候选的分类，从验证程序 stats.js 的 classify 搬来。它决定两件事：
// 两端公网出口不同时要不要隐藏内网候选，以及同机判定里一条候选是不是 mDNS 名字。

const line = (address, type = 'host', proto = 'udp', port = 50000) =>
  `candidate:1 1 ${proto} 2130706431 ${address} ${port} typ ${type}`

test('按地址形式分类', () => {
  const cases = [
    // mDNS 名字（浏览器隐藏地址时公布）
    ['abcd-1234-5678-9abc.local', 'mdns'],
    ['ABCD-1234.LOCAL', 'mdns'],
    // 回环
    ['127.0.0.1', 'loopback'], ['127.255.255.255', 'loopback'], ['::1', 'loopback'], ['::ffff:127.0.0.1', 'loopback'],
    // 内网
    ['192.168.1.5', 'private'], ['10.0.0.9', 'private'], ['172.16.0.1', 'private'], ['172.31.255.255', 'private'],
    ['169.254.10.20', 'private'], ['fd00::1', 'private'], ['fc00::1', 'private'],
    // 归一化之后才分类：IPv4 映射写法与带区域标识的写法
    ['::ffff:192.168.1.5', 'private'], ['::FFFF:10.0.0.9', 'private'], ['FE80::A1%12', 'private'], ['fe80::1%ethernet', 'private'],
    // 共享地址段（运营商级 NAT）
    ['100.64.0.1', 'shared'], ['100.127.255.254', 'shared'], ['198.18.0.1', 'shared'], ['198.19.255.1', 'shared'],
    // 公网
    ['8.8.8.8', 'public'], ['203.0.113.5', 'public'], ['172.15.0.1', 'public'], ['172.32.0.1', 'public'],
    ['100.63.0.1', 'public'], ['100.128.0.1', 'public'], ['198.20.0.1', 'public'], ['198.17.0.1', 'public'],
    ['2001:db8::1', 'public'], ['2408:8207::99', 'public'],
  ]
  for (const [address, kind] of cases) {
    const got = classifyCandidate(line(address))
    assert.equal(got.kind, kind, `${address} 应归为 ${kind}，实际 ${got.kind}`)
    assert.equal(got.type, 'host'); assert.equal(got.proto, 'udp'); assert.equal(got.port, 50000)
  }
})

test('类型、协议和端口取自 candidate 这一行', () => {
  assert.deepEqual(classifyCandidate('candidate:2 1 tcp 1518282910 192.168.1.5 9 typ host tcptype active'),
    { type: 'host', kind: 'private', proto: 'tcp', port: 9 })
  assert.deepEqual(classifyCandidate(line('1.2.3.4', 'srflx', 'udp', 50001)),
    { type: 'srflx', kind: 'public', proto: 'udp', port: 50001 })
  assert.deepEqual(classifyCandidate(line('192.168.1.5', 'prflx')), { type: 'prflx', kind: 'private', proto: 'udp', port: 50000 })
})

test('已经带 address / type 字段的对象，以及只给 candidate 字符串', () => {
  // 连接统计里的候选：地址和类型是现成的字段
  assert.deepEqual(classifyCandidate({ address: '192.168.1.5', type: 'host' }), { type: 'host', kind: 'private', proto: '', port: 0 })
  assert.deepEqual(classifyCandidate({ ip: '10.0.0.9', candidateType: 'srflx' }), { type: 'srflx', kind: 'private', proto: '', port: 0 })
  assert.deepEqual(classifyCandidate({ address: 'abcd-1234.local', candidateType: 'host' }), { type: 'host', kind: 'mdns', proto: '', port: 0 })
  // 页面交来的候选对象：地址和类型从 candidate 这一行里取
  assert.deepEqual(classifyCandidate({ candidate: line('8.8.8.8', 'srflx') }), { type: 'srflx', kind: 'public', proto: 'udp', port: 50000 })
  assert.deepEqual(classifyCandidate(line('8.8.8.8', 'srflx')), classifyCandidate({ candidate: line('8.8.8.8', 'srflx') }))
})

test('没有地址的候选归为 other，不抛异常', () => {
  assert.deepEqual(classifyCandidate(''), { type: '', kind: 'other', proto: '', port: 0 })
  assert.deepEqual(classifyCandidate('candidate:1 1 udp 2130706431'), { type: '', kind: 'other', proto: 'udp', port: 0 })
  assert.deepEqual(classifyCandidate({}), { type: '', kind: 'other', proto: '', port: 0 })
})

test('地址归一化：去掉 IPv4 映射前缀和区域标识，转小写', () => {
  assert.equal(normalizeAddress('::FFFF:192.168.1.5'), '192.168.1.5')
  assert.equal(normalizeAddress('FE80::A1%ethernet'), 'fe80::a1')
  assert.equal(normalizeAddress('fe80::1%12'), 'fe80::1')
  assert.equal(normalizeAddress('ABCD-1234.LOCAL'), 'abcd-1234.local')
  assert.equal(normalizeAddress('2001:DB8::1'), '2001:db8::1')
  // 只去掉第一个 % 之后的部分；IPv4 映射前缀即使还带着区域标识也能归一
  assert.equal(normalizeAddress('::ffff:192.168.1.5%12'), '192.168.1.5')
  for (const value of [undefined, null, 0, false, '']) assert.equal(normalizeAddress(value), '')
  assert.equal(normalizeAddress(123), '123')
})

test('从 candidate 这一行里取出地址或 mDNS 名字', () => {
  assert.equal(candidateHost(line('192.168.1.5')), '192.168.1.5')
  assert.equal(candidateHost(line('abcd-1234.local')), 'abcd-1234.local')
  assert.equal(candidateHost({ candidate: line('1.2.3.4', 'srflx') }), '1.2.3.4')
  assert.equal(candidateHost({ address: '10.0.0.9', type: 'host' }), '10.0.0.9')
  assert.equal(candidateHost('candidate:1 1 udp 2130706431'), '')
  assert.equal(candidateHost(''), '')
  assert.equal(candidateHost({}), '')
})
