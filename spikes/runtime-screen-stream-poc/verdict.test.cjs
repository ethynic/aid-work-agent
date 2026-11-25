// 同机判定规则的单测。用法：node verdict.test.cjs
// 用构造的地址跑一遍四条规则，覆盖「拿不到证据时保持不采集」和各条放行路径。这不是真实设备的证据。
'use strict';
const assert = require('assert');
const verdict = require('./verdict.js');

const PC = ['192.168.1.5', 'fe80::a1%ethernet', '127.0.0.1', '::1']; // Runtime 这台电脑的网卡地址
const MAP = ['1.2.3.4']; // Runtime 的公网映射地址
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
  // —— 已知局限（设计 4.2 接受的例外）：同一台电脑上的浏览器单独走另一条出口。若改为必须判同机，这两条和规则 3 都要改 ——
  ['同机但浏览器走了另一条出口，名字解析不出来：会被判成另一台电脑（已知局限）', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: null }, { type: 'srflx', address: '5.6.7.8' }] }, 'other', 3],
  ['同机但浏览器走了另一条出口，名字解析出本机地址：仍然判同机', { locals: PC, mapped: MAP, remotes: [{ type: 'host', address: '192.168.1.5' }, { type: 'srflx', address: '5.6.7.8' }] }, 'same', 1],
];

let failed = 0;
for (const [name, input, expected, rule] of CASES) {
  const got = verdict(input);
  const ok = got.verdict === expected && got.rule === rule;
  if (!ok) failed++;
  console.log((ok ? '通过  ' : '失败  ') + name + '  → ' + got.verdict + '（规则 ' + got.rule + '）' + (ok ? '' : '，期望 ' + expected + '（规则 ' + rule + '）'));
}
// 证据摘要里不能出现地址，只有计数和是否不同。
const sample = verdict({ locals: PC, mapped: MAP, remotes: [{ type: 'host', address: null }, { type: 'srflx', address: '5.6.7.8' }] });
assert.deepStrictEqual(sample.evidence, { viewerCandidates: 2, viewerResolved: 1, viewerMapped: 1, runtimeMapped: 1, mappedDiffers: true });
assert.strictEqual(verdict(undefined).verdict, 'unknown');
console.log(failed ? '\n' + failed + ' 条失败' : '\n全部 ' + CASES.length + ' 条通过');
process.exit(failed ? 1 : 0);
