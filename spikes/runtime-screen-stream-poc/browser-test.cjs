// 真实浏览器验证：从回环地址页面和内网地址页面各观看一次，输出时延、结束原因、候选类型，
// 以及系统解析器能否解析浏览器公布的 mDNS 名字。需要验证程序（或 signal-relay.cjs）已在 --port 上运行。
// 启动本机已安装的 Chrome 或 Edge：独立临时配置目录、独立调试端口、窗口移到屏幕外，用 CDP 驱动。
// 用法：node browser-test.cjs --browser=chrome|edge [--port=8791] [--lanport=8792] [--wait=25000] [--cand=lan]
// 参数一律写成 --key=value。--lanport=0 时跳过内网地址页面；--cand=none 用于演练直连失败，此时把 --wait 设到 45000 以上。
// --hold=<毫秒>：首帧之后继续停留这么久，用来观察 Runtime 主动停止（画面租约到期等）时网页是否收到结束。
// 只输出时延、候选类型和结束原因，不输出画面，也不输出本机地址。
'use strict';
const { spawn } = require('child_process');
const dns = require('dns');
const fs = require('fs');
const os = require('os');
const path = require('path');

const args = Object.fromEntries(process.argv.slice(2).filter((a) => a.startsWith('--')).map((a) => { const [k, v] = a.slice(2).split('='); return [k, v === undefined ? '1' : v]; }));
const BROWSERS = {
  chrome: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  edge: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
};
const browserKey = args.browser || 'chrome';
const exe = BROWSERS[browserKey];
const SERVER_PORT = Number(args.port || 8791);
const DEBUG_PORT = 9400 + Math.floor(Math.random() * 400);
const WAIT = Number(args.wait || 25000);
const CAND = args.cand || 'lan';
const HOLD = Number(args.hold || 0);
const OUT = path.join(__dirname, 'out');
fs.mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const lanIp = (() => {
  for (const list of Object.values(os.networkInterfaces())) {
    for (const ni of list || []) {
      if (ni.family === 'IPv4' && !ni.internal && /^192\.168\./.test(ni.address)) return ni.address;
    }
  }
  return null;
})();

class Cdp {
  constructor(url) {
    this.id = 0;
    this.waiting = new Map();
    this.listeners = [];
    this.open = new Promise((resolve, reject) => {
      this.ws = new WebSocket(url);
      this.ws.onopen = () => resolve();
      this.ws.onerror = () => reject(new Error('CDP 连接失败'));
    });
    this.ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.id && this.waiting.has(msg.id)) {
        const w = this.waiting.get(msg.id);
        this.waiting.delete(msg.id);
        if (msg.error) w.reject(new Error(msg.error.message + (msg.error.data ? ' ' + msg.error.data : '')));
        else w.resolve(msg.result);
      } else if (msg.method) {
        this.listeners.forEach((l) => l(msg));
      }
    };
  }
  send(method, params) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params: params || {} }));
    return new Promise((resolve, reject) => this.waiting.set(id, { resolve, reject }));
  }
  waitEvent(method, timeoutMs) {
    return new Promise((resolve, reject) => {
      const listener = (msg) => { if (msg.method === method) { clearTimeout(timer); off(); resolve(msg.params); } };
      const off = () => { this.listeners = this.listeners.filter((x) => x !== listener); };
      const timer = setTimeout(() => { off(); reject(new Error('等待超时 ' + method)); }, timeoutMs || 15000);
      this.listeners.push(listener);
    });
  }
}

async function evaluate(cdp, expression) {
  const r = await cdp.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error('页面脚本异常: ' + ((r.exceptionDetails.exception && r.exceptionDetails.exception.description) || r.exceptionDetails.text).slice(0, 200));
  return r.result.value;
}
async function navigate(cdp, url) {
  const loaded = cdp.waitEvent('Page.loadEventFired', 15000);
  await cdp.send('Page.navigate', { url });
  await loaded.catch(() => {});
  await sleep(400);
}

function lookupHost(name, ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ ok: false, timeout: true }), ms);
    dns.lookup(name, { family: 4 }, (err, addr) => {
      clearTimeout(timer);
      resolve(err ? { ok: false, code: err.code } : { ok: true, isLan: addr === lanIp, isLoopback: addr === '127.0.0.1' });
    });
  });
}

const SNAP = 'JSON.stringify({pc: window.__poc.pcState, connectedMs: window.__poc.connectedMs, firstFrameMs: window.__poc.firstFrameMs, endReason: window.__poc.endReason, hasStats: !!window.__poc.stats})';

async function viewerCase(cdp, origin, cand, label) {
  await navigate(cdp, origin + '/viewer.html?cand=' + cand);
  const t0 = Date.now();
  let snap = null;
  // 三种结束：收到首帧；Runtime 报告结束（例如判定为同一台电脑）；超时。
  while (Date.now() - t0 < WAIT) {
    await sleep(500);
    snap = JSON.parse(await evaluate(cdp, SNAP));
    if (snap.firstFrameMs || snap.endReason) break;
  }
  if (snap.firstFrameMs) {
    for (let i = 0; i < 14; i++) { if (await evaluate(cdp, '!!window.__poc.stats')) break; await sleep(700); }
    await sleep(2500);
    const holdUntil = Date.now() + HOLD;
    while (Date.now() < holdUntil) {
      await sleep(500);
      snap = JSON.parse(await evaluate(cdp, SNAP));
      if (snap.endReason) break;
    }
  }
  const full = JSON.parse(await evaluate(cdp, 'JSON.stringify({ pc: window.__poc.pcState, connectedMs: window.__poc.connectedMs, firstFrameMs: window.__poc.firstFrameMs, endReason: window.__poc.endReason, path: window.__poc.path, events: (window.__poc.events || []).map((e) => ({ name: e.name, dt: e.dt, pc: e.pc, reason: e.reason, w: e.w, h: e.h })), stats: window.__poc.stats, mdns: window.__poc.mdns || [], cands: window.__poc.cands || [] })'));
  const out = { label, cand, secure: await evaluate(cdp, 'isSecureContext') };
  out.outcome = full.firstFrameMs ? 'streaming' : full.endReason ? 'ended:' + full.endReason : 'no-frame';
  out.pc = full.pc;
  out.connectedMs = full.connectedMs;
  out.firstFrameMs = full.firstFrameMs;
  out.endReason = full.endReason;
  out.path = full.path;
  out.relayInViewersPath = !!(full.stats && full.stats.pair && full.stats.pair.relay);
  out.events = full.events.map((e) => e.name + (e.pc ? '=' + e.pc : '') + (e.reason ? '=' + e.reason : '') + (e.dt != null ? '@' + e.dt : ''));
  out.viewerCandidates = full.cands.map((e) => e.kind + ':' + e.type + ':' + e.proto);
  if (full.stats && full.stats.rx) {
    const rx = full.stats.rx;
    out.rx = { framesDecoded: rx.framesDecoded, fps: rx.fps, w: rx.w, h: rx.h, codec: rx.codec, freezeCount: rx.freezeCount };
  }
  out.osResolvesBrowserMdns = [];
  for (const name of full.mdns.slice(0, 2)) out.osResolvesBrowserMdns.push(await lookupHost(name, 4000));
  return out;
}

(async () => {
  if (!fs.existsSync(exe)) throw new Error('未找到浏览器: ' + browserKey);
  const base = 'http://127.0.0.1:' + SERVER_PORT;
  const lanPort = Number(args.lanport === undefined ? 8792 : args.lanport);
  const lan = lanIp && lanPort ? 'http://' + lanIp + ':' + lanPort : null;
  const profile = path.join(os.tmpdir(), 'aid-screen-poc-profile-' + process.pid);
  const report = { browser: browserKey, startedAt: new Date().toISOString(), hasLanOrigin: !!lan, viewers: [] };

  const child = spawn(exe, [
    '--remote-debugging-port=' + DEBUG_PORT, '--user-data-dir=' + profile, '--no-first-run', '--no-default-browser-check',
    '--disable-search-engine-choice-screen', '--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding',
    '--disable-background-timer-throttling', '--autoplay-policy=no-user-gesture-required', '--window-size=960,600', 'about:blank',
  ], { stdio: 'ignore' });

  let cdp = null;
  try {
    let version = null;
    for (let i = 0; i < 60 && !version; i++) {
      await sleep(500);
      try { version = await (await fetch('http://127.0.0.1:' + DEBUG_PORT + '/json/version')).json(); } catch (e) { /* 浏览器还没起来 */ }
    }
    if (!version) throw new Error('浏览器调试端口没有就绪');
    report.browserVersion = version.Browser;
    const target = await (await fetch('http://127.0.0.1:' + DEBUG_PORT + '/json/new?about:blank', { method: 'PUT' })).json();
    cdp = new Cdp(target.webSocketDebuggerUrl);
    await cdp.open;
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    // 把窗口移到屏幕外，避免验证期间在桌面上显示画面。
    // POC_BROWSER_VISIBLE=1 时改为留在屏幕内（小窗），用来区分「窗口被遮挡」造成的影响。
    try {
      const { windowId } = await cdp.send('Browser.getWindowForTarget');
      const visible = process.env.POC_BROWSER_VISIBLE === '1';
      await cdp.send('Browser.setWindowBounds', { windowId, bounds: visible ? { left: 40, top: 40, width: 480, height: 320 } : { left: -30000, top: -30000, width: 960, height: 600 } });
    } catch (e) {
      report.windowMove = String(e.message).slice(0, 100);
    }
    report.viewers.push(await viewerCase(cdp, base, CAND, '回环地址页面观看'));
    if (lan) report.viewers.push(await viewerCase(cdp, lan, CAND, '内网地址页面观看'));
  } catch (e) {
    report.error = String((e && e.stack) || e).slice(0, 400);
  } finally {
    try { if (cdp) await cdp.send('Browser.close'); } catch (e) { /* 浏览器可能已经退出 */ }
    await sleep(1500);
    try { child.kill(); } catch (e) { /* 已退出 */ }
    for (let i = 0; i < 5; i++) { try { fs.rmSync(profile, { recursive: true, force: true }); break; } catch (e) { await sleep(800); } }
  }
  report.finishedAt = new Date().toISOString();
  const file = path.join(OUT, 'browser-' + browserKey + '-' + report.startedAt.replace(/[:.]/g, '-') + '.json');
  fs.writeFileSync(file, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
})();
