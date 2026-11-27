// 网络条件检测（只用于验证和排查，不进产品代码）：在一台电脑上运行，另一台电脑用浏览器打开它提供的检测页。
// 检测页依次做四项探测，只发 STUN 探测包，不涉及画面，也不采集：
//   1. 内网直达：另一台电脑 → 本机的内网地址，UDP 能否往返；
//   2. 公网出口：另一台电脑能否用 UDP 访问公网，它的公网出口与本机是否相同；
//   3. 外网回环：同一出口时，经公网地址能否绕回本机（NAT 回环），两边同时打洞；
//   4. 出口 NAT 类型：映射端口是否随目的地变化（锥形 / 对称型），决定跨网络点对点能否打洞。
// 用法：node net-check.cjs，然后在另一台电脑上打开 http://<本机内网地址>:8797/。
// 输出里的地址一律打码：内网地址保留前三段，公网地址保留前两段。2026-11-27 的用法与结果见调研报告 4.3、6.1。
'use strict';
const http = require('http');
const dgram = require('dgram');
const dns = require('dns');
const os = require('os');
const crypto = require('crypto');

const HTTP_PORT = 8797;
const UDP_PORT = 8798;
const PUBLIC_STUN = { host: 'stun.miwifi.com', port: 3478 };
const COOKIE = [0x21, 0x12, 0xA4, 0x42];
const maskPrivate = (ip) => String(ip).replace(/^(\d+\.\d+\.\d+)\.\d+/, '$1.x');
const maskPublic = (ip) => String(ip).replace(/^(\d+\.\d+)\.\d+\.\d+/, '$1.x.x');
const isPrivate = (ip) => /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.)/.test(String(ip));

// 本机的内网地址：排除回环和代理虚拟网卡（198.18.0.0/15），优先 192.168.*，其次不是 Docker/WSL 虚拟网段的地址
const v4 = Object.values(os.networkInterfaces()).flat()
  .filter((i) => i && i.family === 'IPv4' && !i.internal && !/^198\.1[89]\./.test(i.address));
const lan = v4.find((i) => /^192\.168\./.test(i.address)) || v4.find((i) => !/^172\.(1[6-9]|2\d|3[01])\./.test(i.address)) || v4[0];
const LAN_IP = lan ? lan.address : null;

// 应答 socket 必须绑定到内网地址，不能绑任意地址（0.0.0.0）：Windows 对绑定任意地址的 UDP socket 按最优路由发包，
// 开着代理虚拟网卡的电脑上，发往其他网段的回包会被送进代理，代理改写源端口后对方就认不出来了（2026-11-27 踩过）。
// 绑定到具体地址后走强主机模型，只会从这个网卡发出。
const NAT_STUN = ['stun.miwifi.com', 'stun.chat.bilibili.com', 'stun.hitv.com', 'stun.cloudflare.com', 'stun.voip.blackberry.com'];
const NAT_KIND = (n) => (!n ? '未知' : !n.answers ? '无法判断（没有公网 STUN 应答）' : n.distinct === 1 ? '锥形' : '对称型');

function buildRequest() {
  const req = Buffer.alloc(20);
  req.writeUInt16BE(0x0001, 0);
  req.writeUInt32BE(0x2112A442, 4);
  crypto.randomFillSync(req, 8, 12);
  return req;
}
function parseMapped(msg) {
  if (msg.length < 20 || msg.readUInt16BE(0) !== 0x0101) return null;
  let offset = 20;
  while (offset + 4 <= msg.length) {
    const type = msg.readUInt16BE(offset);
    const len = msg.readUInt16BE(offset + 2);
    if ((type === 0x0020 || type === 0x0001) && len >= 8 && msg[offset + 5] === 0x01) {
      const port = msg.readUInt16BE(offset + 6) ^ (type === 0x0020 ? 0x2112 : 0);
      const ip = [0, 1, 2, 3].map((i) => msg[offset + 8 + i] ^ (type === 0x0020 ? COOKIE[i] : 0)).join('.');
      return { ip, port };
    }
    offset += 4 + len + ((4 - (len % 4)) % 4);
  }
  return null;
}
// 从 socket 向一个 STUN 服务器查询本机的映射地址
function mappingOf(sock, host, port) {
  return new Promise((resolve) => {
    let tid = null;
    let tries = 0;
    const onMessage = (msg) => {
      if (!tid || msg.length < 20 || msg.readUInt16BE(0) !== 0x0101 || !msg.slice(8, 20).equals(tid)) return;
      const mapped = parseMapped(msg);
      if (mapped) finish(mapped);
    };
    const finish = (value) => { clearTimeout(timer); sock.off('message', onMessage); resolve(value); };
    const timer = setTimeout(() => finish(null), 5000);
    const send = () => {
      tries += 1;
      const req = buildRequest();
      tid = req.slice(8, 20);
      sock.send(req, port, host);
      if (tries < 4) setTimeout(send, 700);
    };
    sock.on('message', onMessage);
    send();
  });
}
// 本机经公网 STUN 得到的公网映射地址
const publicMapping = (sock) => mappingOf(sock, PUBLIC_STUN.host, PUBLIC_STUN.port);
// 本机出口的映射行为：同一个 socket 向多个公网 STUN 服务器查询，比较公网端口
async function ownNatType(sock) {
  const ports = [];
  for (const host of NAT_STUN) {
    const mapped = await mappingOf(sock, host, 3478);
    if (mapped) ports.push(mapped.port);
  }
  return { answers: ports.length, distinct: new Set(ports).size };
}

let publicInfo = null; // { ip, port }：本应答 socket 经公网 STUN 查到的公网映射
let ownNat = null; // { answers, distinct }：本机出口的映射行为
// 模拟 ICE 打洞：本应答 socket 也向对方的公网映射地址连发几个包，让双方的出口设备放开对这个地址的过滤
function punch(ip, port) {
  let sent = 0;
  const timer = setInterval(() => {
    sent += 1;
    answer.send(buildRequest(), port, ip);
    if (sent >= 16) clearInterval(timer);
  }, 500);
  console.log('NET_CHECK punching ' + maskPublic(ip) + ':' + port + ' from this socket');
}

const answer = dgram.createSocket('udp4');
answer.on('message', (msg, rinfo) => {
  // 只应答 STUN Binding Request
  if (msg.length < 20 || msg.readUInt16BE(0) !== 0x0001 || msg.readUInt32BE(4) !== 0x2112A442) return;
  const res = Buffer.alloc(32);
  res.writeUInt16BE(0x0101, 0); res.writeUInt16BE(12, 2); msg.copy(res, 4, 4, 20);
  res.writeUInt16BE(0x0020, 20); res.writeUInt16BE(8, 22); res[24] = 0; res[25] = 0x01;
  res.writeUInt16BE(rinfo.port ^ 0x2112, 26);
  const addr = rinfo.address.split('.').map(Number);
  for (let i = 0; i < 4; i++) res[28 + i] = addr[i] ^ COOKIE[i];
  answer.send(res, rinfo.port, rinfo.address);
});
answer.on('error', (e) => console.log('NET_CHECK socket error ' + e.code));

const PAGE = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>网络条件检测</title></head>
<body style="margin:0;padding:24px;background:#030712;color:#e5e7eb;font:16px/1.8 'Segoe UI','Microsoft YaHei UI',sans-serif">
<h2 style="font-weight:600;margin:0 0 12px">网络条件检测</h2>
<p id="s" style="font-size:18px">正在检测…</p>
<ol id="list" style="font-size:16px"></ol>
<pre id="hud" style="font:13px/1.7 Consolas,monospace;background:#111827;padding:12px;border-radius:6px;white-space:pre-wrap"></pre>
<script>
const st = document.getElementById('s'), list = document.getElementById('list'), hud = document.getElementById('hud');
const mask = (a) => /^(10\\.|192\\.168\\.|172\\.(1[6-9]|2\\d|3[01])\\.|169\\.254\\.)/.test(a) ? a.replace(/^(\\d+\\.\\d+\\.\\d+)\\.\\d+/, '$1.x') : a.replace(/^(\\d+\\.\\d+)\\.\\d+\\.\\d+/, '$1.x.x');
const note = (s) => { hud.textContent += s + '\\n'; };
const item = (text, color) => { const li = document.createElement('li'); li.textContent = text; li.style.color = color || '#e5e7eb'; list.appendChild(li); };
// 向一个 STUN 服务器探测：拿到映射候选返回 { ip, port }，8 秒内拿不到返回 null
function probe(urls) {
  return new Promise((resolve) => {
    const pc = new RTCPeerConnection({ iceServers: [{ urls }] });
    let done = false;
    const finish = (value) => { if (done) return; done = true; clearTimeout(timer); try { pc.close(); } catch (e) {} resolve(value); };
    const timer = setTimeout(() => finish(null), 8000);
    pc.createDataChannel('probe');
    pc.onicecandidate = (e) => {
      if (!e.candidate) { finish(null); return; }
      if (e.candidate.type === 'srflx') { const parts = e.candidate.candidate.split(' '); finish({ ip: parts[4], port: parts[5] }); }
    };
    pc.setLocalDescription(pc.createOffer()).catch(() => finish(null));
  });
}
// 出口 NAT 的映射行为：同一个 socket 向多个公网 STUN 服务器查询，比较得到的公网端口。
// 端口都相同 → 映射与目的地无关（锥形），对点对点有利；端口随目的地变化 → 对称型，跨网络打洞很难成功。
// 开着代理虚拟网卡的电脑，这个结果可能反映的是代理而不是真实出口。
function natMapping() {
  const urls = ['stun:stun.miwifi.com:3478', 'stun:stun.chat.bilibili.com:3478', 'stun:stun.hitv.com:3478', 'stun:stun.cloudflare.com:3478', 'stun:stun.voip.blackberry.com:3478'];
  return new Promise((resolve) => {
    const pc = new RTCPeerConnection({ iceServers: urls.map((u) => ({ urls: u })) });
    const ports = [];
    let done = false;
    const finish = () => { if (done) return; done = true; clearTimeout(timer); try { pc.close(); } catch (e) {} resolve({ answers: ports.length, ports: [...new Set(ports)] }); };
    const timer = setTimeout(finish, 9000);
    pc.createDataChannel('probe');
    pc.onicecandidate = (e) => {
      if (!e.candidate) return finish();
      if (e.candidate.type === 'srflx') ports.push(e.candidate.candidate.split(' ')[5]);
    };
    pc.setLocalDescription(pc.createOffer()).catch(() => finish());
  });
}
(async () => {
  const conf = await (await fetch('/config')).json();
  const results = {};
  results.lan = await probe(conf.lan);
  item('1. 内网直达（另一台电脑 → 本机的内网地址）：' + (results.lan ? '通' : '不通'), results.lan ? '#34d399' : '#f87171');
  results.public = await probe(conf.public);
  item('2. 公网出口（能否用 UDP 访问公网）：' + (results.public ? '通，本机的公网映射地址 ' + mask(results.public.ip) : '不通'), results.public ? '#34d399' : '#f87171');
  let same = null;
  if (results.public) {
    const r = await (await fetch('/report?ip=' + encodeURIComponent(results.public.ip) + '&port=' + encodeURIComponent(results.public.port))).json();
    same = r.sameEgress;
    note('与检测服务器的公网出口是否相同：' + (same ? '相同（同一个出口）' : '不同（不同的出口）'));
    await new Promise((done) => setTimeout(done, 1500)); // 等检测服务器开始向本机发包，再发起回环探测
  }
  if (conf.hairpin) {
    results.hairpin = await probe(conf.hairpin);
    item('3. 外网回环（经公网地址绕回本机）：' + (results.hairpin ? '通' : '不通'), results.hairpin ? '#34d399' : '#f87171');
  } else {
    item('3. 外网回环：检测服务器没查到自己的公网映射地址，没法测');
  }
  const nat = await natMapping();
  let natText;
  if (!nat.answers) natText = '无法判断（没有公网 STUN 应答）';
  else if (nat.ports.length === 1) natText = '锥形（映射与目的地无关），对跨网络点对点有利';
  else natText = '对称型（映射随目的地变化），跨网络点对点直连很难成功';
  results.nat = nat;
  item('4. 出口 NAT 类型（本机的公网出口）：' + natText, nat.answers && nat.ports.length === 1 ? '#34d399' : '#f87171');
  note('本机出口 NAT：应答 ' + nat.answers + ' 次，不同的公网端口 ' + nat.ports.length + ' 个');
  note('检测服务器的公网映射地址：' + (conf.aPublicMasked || '未知'));
  note('检测服务器的出口 NAT 类型：' + (conf.aNatKind || '未知'));
  let text;
  if (!results.public) text = '结论：这台电脑不能用 UDP 访问公网，外网直连走不通。';
  else if (same === false) text = '结论：两台电脑在不同的公网出口后面，属于正常的跨网络直连，能不能通取决于两端的 NAT 类型，需要用验证程序实测。';
  else if (results.hairpin) text = '结论：两台电脑在同一个公网出口后面，出口设备支持回环，外网直连可行。';
  else text = '结论：两台电脑在同一个公网出口后面，出口设备不支持回环，这两台电脑之间外网直连不可行。';
  st.textContent = text;
  st.style.color = '#fbbf24';
  note('DONE');
})();</script></body></html>`;

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const json = (code, obj) => { res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(obj)); };
  if (url.pathname === '/config') {
    return json(200, {
      lan: 'stun:' + LAN_IP + ':' + UDP_PORT,
      public: 'stun:' + PUBLIC_STUN.host + ':' + PUBLIC_STUN.port,
      hairpin: publicInfo ? 'stun:' + publicInfo.ip + ':' + publicInfo.port : null,
      aPublicMasked: publicInfo ? maskPublic(publicInfo.ip) + ':' + publicInfo.port : null,
      aNatKind: NAT_KIND(ownNat),
    });
  }
  if (url.pathname === '/report') {
    const ip = url.searchParams.get('ip') || '';
    const masked = isPrivate(ip) ? maskPrivate(ip) : maskPublic(ip);
    const same = !!(publicInfo && ip === publicInfo.ip);
    console.log('NET_CHECK viewer public mapped ' + masked + ' sameEgress=' + same);
    const port = Number(url.searchParams.get('port') || 0);
    if (publicInfo && port > 0 && port < 65536 && /^(\d{1,3}\.){3}\d{1,3}$/.test(ip)) punch(ip, port);
    return json(200, { sameEgress: same });
  }
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(PAGE);
}).listen(HTTP_PORT, '0.0.0.0', () => console.log('NET_CHECK page http://' + LAN_IP + ':' + HTTP_PORT + '/'));

answer.bind(UDP_PORT, LAN_IP, async () => {
  console.log('NET_CHECK answering udp/' + UDP_PORT + ' on ' + maskPrivate(LAN_IP));
  publicInfo = await publicMapping(answer);
  ownNat = await ownNatType(answer);
  console.log('NET_CHECK own public mapping ' + (publicInfo ? maskPublic(publicInfo.ip) + ':' + publicInfo.port : 'unknown'));
  console.log('NET_CHECK own NAT type ' + NAT_KIND(ownNat) + ' (answers ' + ownNat.answers + ', distinct ports ' + ownNat.distinct + ')');
});
