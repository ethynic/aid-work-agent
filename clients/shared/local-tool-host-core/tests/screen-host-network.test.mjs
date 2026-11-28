import assert from 'node:assert/strict'
import { test } from 'node:test'
import { localAddresses, resolveHost } from '../dist/screen/hostNetwork.js'

// Runtime Host 自己能拿到的两样本机证据：全部网卡地址，以及用系统解析器解析观看端的 mDNS 名字。
// 名字解析只用于同机判定；解析不出、超时都不是错误，结论是「拿不到这条证据」。

test('本机全部网卡地址：归一化、去重，回环和 IPv6 都算', () => {
  const interfaces = {
    'Ethernet': [{ address: '192.168.1.5', netmask: '255.255.255.0', family: 'IPv4', mac: '00:00:00:00:00:00', internal: false, cidr: '192.168.1.5/24' },
      { address: 'fe80::a1%12', netmask: 'ffff:ffff:ffff:ffff::', family: 'IPv6', mac: '00:00:00:00:00:00', internal: false, cidr: 'fe80::a1%12/64' }],
    'Loopback': [{ address: '127.0.0.1', netmask: '255.0.0.0', family: 'IPv4', mac: '00:00:00:00:00:00', internal: true, cidr: '127.0.0.1/8' },
      { address: '::1', netmask: 'ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff', family: 'IPv6', mac: '00:00:00:00:00:00', internal: true, cidr: '::1/128' }],
    'vEthernet': [{ address: '::FFFF:192.168.1.5', netmask: 'ffff:ffff:ffff:ffff::', family: 'IPv6', mac: '00:00:00:00:00:00', internal: false, cidr: '::ffff:192.168.1.5/128' }],
    '虚拟网卡': null,
  }
  assert.deepEqual(localAddresses(interfaces), ['192.168.1.5', 'fe80::a1', '127.0.0.1', '::1'])
  assert.deepEqual(localAddresses({}), [])
})

test('不注入时读真实的网卡地址', () => {
  const addresses = localAddresses()
  assert.ok(Array.isArray(addresses))
  for (const address of addresses) {
    assert.match(address, /^[0-9a-f:.]+$/, `${address} 应当是归一化之后的地址`)
    assert.equal(address.includes('%'), false)
    assert.equal(address.includes('::ffff:'), false)
  }
})

test('只解析 .local 结尾的 mDNS 名字，其他名字不交给系统解析器', async () => {
  let asked = 0
  const lookup = (_name, _options, callback) => { asked++; callback(null, '192.168.1.5') }
  for (const name of ['', 'example.com', 'example.local.example.com', 'localhost', 'a.local.', 123, null, undefined]) {
    assert.deepEqual(await resolveHost(name, lookup), { ok: false, code: 'NOT_MDNS' }, String(name))
  }
  assert.equal(asked, 0)
  assert.deepEqual(await resolveHost('ABCD-1234.LOCAL', lookup), { ok: true, address: '192.168.1.5' }, '名字不区分大小写')
})

test('解析成功时只要 IPv4 地址', async () => {
  const asked = []
  const lookup = (name, options, callback) => { asked.push([name, options]); callback(null, '192.168.1.5') }
  assert.deepEqual(await resolveHost('abcd-1234.local', lookup), { ok: true, address: '192.168.1.5' })
  assert.deepEqual(asked, [['abcd-1234.local', { family: 4 }]])
})

test('解析不出时带上系统解析器的错误码，不抛异常', async () => {
  const notFound = (_name, _options, callback) => callback(Object.assign(new Error('getaddrinfo ENOTFOUND abcd-1234.local'), { code: 'ENOTFOUND' }))
  assert.deepEqual(await resolveHost('abcd-1234.local', notFound), { ok: false, code: 'ENOTFOUND' })
  const noCode = (_name, _options, callback) => callback(new Error('boom'))
  assert.deepEqual(await resolveHost('abcd-1234.local', noCode), { ok: false, code: 'ERROR' })
  const throws = () => { throw new TypeError('bad hostname') }
  assert.deepEqual(await resolveHost('abcd-1234.local', throws), { ok: false, code: 'ERROR' })
})

test('系统解析器 2 秒内不回答就按超时处理，迟到的回答不产生影响', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let late
  const lookup = (_name, _options, callback) => { late = callback }
  const pending = resolveHost('abcd-1234.local', lookup)
  await t.mock.timers.tick(1_999)
  let settled = false
  pending.then(() => { settled = true })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(settled, false, '2 秒之内不放弃')
  await t.mock.timers.tick(1)
  assert.deepEqual(await pending, { ok: false, code: 'TIMEOUT' })
  assert.doesNotThrow(() => late(null, '192.168.1.5'))
})
