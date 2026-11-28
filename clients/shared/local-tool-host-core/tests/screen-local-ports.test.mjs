import assert from 'node:assert/strict'
import { test } from 'node:test'
import { NETSTAT_TIMEOUT_MS, parseNetstatPorts, readLocalPorts } from '../dist/screen/localPorts.js'

// 查本机端口表（Windows 的 netstat -ano），用于同机判定的规则 5、6。
// 查不到就是查不到：命令失败或超时返回「空集合加失败标记」，绝不能把半张表当成完整的结果。

const UDP_OUTPUT = [
  '\r\nActive Connections',
  '\r\n  Proto  Local Address          Foreign Address        State',
  '\r\n  UDP    0.0.0.0:5353           *:*                                    1234',
  '\r\n  UDP    192.168.1.5:50000      *:*                                    5678',
  '\r\n  UDP    [::]:5353              *:*                                    1234',
  '\r\n  UDP    [fe80::1%12]:61456     *:*                                    9',
  '\r\n  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       4',
  '\r\n',
].join('')
const TCP_OUTPUT = [
  '\r\n  Proto  Local Address          Foreign Address        State           PID',
  '\r\n  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1234',
  '\r\n  TCP    192.168.1.5:49700      1.2.3.4:443            ESTABLISHED     5678',
  '\r\n  TCP    [::1]:49701            [::1]:49702            ESTABLISHED     9',
  '\r\n  TCP    [2001:db8::5]:443      [2408:8207::99]:50001  TIME_WAIT       0',
  '\r\n  UDP    0.0.0.0:5353           *:*                                    1234',
  '\r\n',
].join('')

test('解析 UDP 表：只取本机地址一列的端口，IPv4、IPv6 和带区域标识的写法都认', () => {
  assert.deepEqual([...parseNetstatPorts(UDP_OUTPUT, 'udp')].sort((a, b) => a - b), [5353, 50000, 61456])
})

test('解析 TCP 表：多出来的状态列不影响，IPv6 地址只取最后一个冒号后面的数字', () => {
  assert.deepEqual([...parseNetstatPorts(TCP_OUTPUT, 'tcp')].sort((a, b) => a - b), [135, 443, 49700, 49701])
})

test('协议名不区分大小写；表头和不完整的行不产生端口', () => {
  const lower = '  udp    0.0.0.0:3702           *:*                                    1\r\n  Proto  Local Address          Foreign Address\r\n  UDP    broken                 *:*                                    1\r\n  UDP    0.0.0.0:abc            *:*                                    1\r\n'
  assert.deepEqual([...parseNetstatPorts(lower, 'udp')], [3702])
  assert.equal(parseNetstatPorts('', 'udp').size, 0)
  assert.equal(parseNetstatPorts(undefined, 'tcp').size, 0)
})

test('读端口表：执行 netstat -ano -p <协议>，带超时，成功时给出端口集合', async () => {
  const ran = []
  const exec = (file, args, options, callback) => {
    ran.push({ file, args, options })
    callback(null, args[2] === 'udp' ? UDP_OUTPUT : TCP_OUTPUT)
  }
  const udp = await readLocalPorts('udp', exec)
  assert.deepEqual(ran, [{ file: 'netstat', args: ['-ano', '-p', 'udp'], options: { windowsHide: true, timeout: NETSTAT_TIMEOUT_MS, maxBuffer: 8 * 1024 * 1024 } }])
  assert.deepEqual(udp, { ok: true, ports: new Set([5353, 50000, 61456]) })
  const tcp = await readLocalPorts('tcp', exec)
  assert.equal(tcp.ok, true)
  assert.deepEqual([...tcp.ports].sort((a, b) => a - b), [135, 443, 49700, 49701])
  assert.ok(NETSTAT_TIMEOUT_MS >= 1_000 && NETSTAT_TIMEOUT_MS <= 5_000, '超时既要容得下偶尔变慢的 netstat，也不能拖过 15 秒的验证时限')
})

test('命令失败或超时：返回查不到，不带上已经输出的半张表，也不抛异常', async () => {
  const failed = await readLocalPorts('udp', (_file, _args, _options, callback) => {
    callback(Object.assign(new Error('Command failed'), { killed: true, code: null, signal: 'SIGTERM' }), UDP_OUTPUT)
  })
  assert.deepEqual(failed, { ok: false, ports: new Set() })
  const empty = await readLocalPorts('udp', (_file, _args, _options, callback) => callback(new Error('spawn netstat ENOENT'), ''))
  assert.deepEqual(empty, { ok: false, ports: new Set() })
})

test('本机真实的 netstat 输出能被解析（仅 Windows）', { skip: process.platform !== 'win32' }, async () => {
  const tcp = await readLocalPorts('tcp')
  assert.equal(tcp.ok, true, 'Windows 上 netstat 应当可用')
  assert.ok(tcp.ports.size > 0, 'Windows 上总有正在监听的 TCP 端口')
  assert.ok([...tcp.ports].every(port => Number.isInteger(port) && port > 0 && port <= 65535))
})
