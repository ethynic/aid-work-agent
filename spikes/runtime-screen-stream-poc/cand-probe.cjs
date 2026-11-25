// 候选探测：在真实浏览器里只创建 RTCPeerConnection 并统计本地候选，判断「本地网络访问」权限是否拦住了内网候选。
// 不建立任何连接，不涉及画面。用于调研报告 6.2 节「公网页面」一项，需要验证程序的 serve 模式在运行。
// 用法：node cand-probe.cjs --browser=chrome|edge [--port=8791] [--lanport=8792]
'use strict';
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const args = Object.fromEntries(process.argv.slice(2).filter((a) => a.startsWith('--')).map((a) => { const [k, v] = a.slice(2).split('='); return [k, v === undefined ? '1' : v]; }));
const BROWSERS = { chrome: 'C:/Program Files/Google/Chrome/Application/chrome.exe', edge: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' };
const exe = BROWSERS[args.browser || 'chrome'];
const PORT = Number(args.port || 8791);
const LAN_PORT = Number(args.lanport || 8792);
const DEBUG_PORT = 9800 + Math.floor(Math.random() * 150);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const lanIp = (() => { for (const list of Object.values(os.networkInterfaces())) for (const ni of list || []) if (ni.family === 'IPv4' && !ni.internal && /^192\.168\./.test(ni.address)) return ni.address; return null; })();

class Cdp {
  constructor(url) {
    this.id = 0; this.waiting = new Map(); this.listeners = [];
    this.open = new Promise((resolve, reject) => { this.ws = new WebSocket(url); this.ws.onopen = () => resolve(); this.ws.onerror = () => reject(new Error('CDP 连接失败')); });
    this.ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.id && this.waiting.has(msg.id)) { const w = this.waiting.get(msg.id); this.waiting.delete(msg.id); if (msg.error) w.reject(new Error(msg.error.message)); else w.resolve(msg.result); }
      else if (msg.method) this.listeners.forEach((l) => l(msg));
    };
  }
  send(method, params) { const id = ++this.id; this.ws.send(JSON.stringify({ id, method, params: params || {} })); return new Promise((resolve, reject) => this.waiting.set(id, { resolve, reject })); }
  waitEvent(method, timeoutMs) {
    return new Promise((resolve) => {
      const listener = (msg) => { if (msg.method === method) { clearTimeout(timer); off(); resolve(msg.params); } };
      const off = () => { this.listeners = this.listeners.filter((x) => x !== listener); };
      const timer = setTimeout(off, timeoutMs || 15000);
      this.listeners.push(listener);
    });
  }
}

// 页面内执行：创建连接、收集 3 秒候选、读统计后关闭。
const RUN = `(async () => {
  const out = { cands: [], states: [], localCandidateStats: [] };
  const pc = new RTCPeerConnection({ iceServers: [] });
  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.onicecandidate = (e) => { if (e.candidate) out.cands.push(window.POC.classify(e.candidate)); };
  pc.onicegatheringstatechange = () => out.states.push(pc.iceGatheringState);
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((r) => setTimeout(r, 3000));
  (await pc.getStats()).forEach((s) => { if (s.type === 'local-candidate') { const k = window.POC.classify({ address: s.address, type: s.candidateType }); k.proto = s.protocol; k.hasAddress = !!s.address; out.localCandidateStats.push(k); } });
  out.gathering = pc.iceGatheringState;
  pc.close();
  return out;
})()`;

(async () => {
  const profile = path.join(os.tmpdir(), 'aid-screen-poc-cand-' + process.pid);
  const child = spawn(exe, ['--remote-debugging-port=' + DEBUG_PORT, '--user-data-dir=' + profile, '--no-first-run', '--no-default-browser-check', '--disable-search-engine-choice-screen', '--window-size=640,400', 'about:blank'], { stdio: 'ignore' });
  const report = { browser: args.browser || 'chrome', runs: [] };
  let cdp = null;
  try {
    let version = null;
    for (let i = 0; i < 60 && !version; i++) { await sleep(500); try { version = await (await fetch('http://127.0.0.1:' + DEBUG_PORT + '/json/version')).json(); } catch (e) { /* 未就绪 */ } }
    report.browserVersion = version.Browser;
    const target = await (await fetch('http://127.0.0.1:' + DEBUG_PORT + '/json/new?about:blank', { method: 'PUT' })).json();
    cdp = new Cdp(target.webSocketDebuggerUrl);
    await cdp.open;
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    try { const { windowId } = await cdp.send('Browser.getWindowForTarget'); await cdp.send('Browser.setWindowBounds', { windowId, bounds: { left: -30000, top: -30000, width: 640, height: 400 } }); } catch (e) { /* 忽略 */ }
    const evaluate = async (expression) => (await cdp.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })).result.value;
    const cases = [['回环地址页面', 'http://127.0.0.1:' + PORT, null]];
    if (lanIp) cases.push(['内网地址页面', 'http://' + lanIp + ':' + LAN_PORT, null]);
    cases.push(['回环地址页面+授予 local-network-access', 'http://127.0.0.1:' + PORT, ['local-network-access']]);
    cases.push(['回环地址页面+授予 local-network 与 loopback-network', 'http://127.0.0.1:' + PORT, ['local-network', 'loopback-network']]);
    cases.push(['回环地址页面+拒绝 local-network-access', 'http://127.0.0.1:' + PORT, null, 'denied']);
    for (const [label, origin, grant, setting] of cases) {
      const loaded = cdp.waitEvent('Page.loadEventFired', 15000);
      await cdp.send('Page.navigate', { url: origin + '/p4.html' });
      await loaded;
      await sleep(500);
      await cdp.send('Browser.resetPermissions', { origin }).catch(() => {});
      if (grant) await cdp.send('Browser.grantPermissions', { origin, permissions: grant }).catch((e) => report.note = String(e.message).slice(0, 120));
      if (setting) await cdp.send('Browser.setPermission', { origin, permission: { name: 'local-network-access' }, setting }).catch(() => {});
      const run = await evaluate(RUN);
      run.label = label;
      run.secure = await evaluate('isSecureContext');
      run.perms = await evaluate('window.p4.perms()');
      report.runs.push(run);
    }
  } catch (e) {
    report.error = String((e && e.stack) || e).slice(0, 400);
  } finally {
    try { if (cdp) await cdp.send('Browser.close'); } catch (e) { /* 已退出 */ }
    await sleep(1200);
    try { child.kill(); } catch (e) { /* 已退出 */ }
    for (let i = 0; i < 5; i++) { try { fs.rmSync(profile, { recursive: true, force: true }); break; } catch (e) { await sleep(800); } }
  }
  console.log(JSON.stringify(report));
})();
