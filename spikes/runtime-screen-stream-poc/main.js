// Runtime 桌面实时画面 Phase 0 技术验证主程序。只用于验证，不进产品代码；运行方式见 README.md。
// 画面只在隐藏窗口之间或本机浏览器之间传输，不显示、不落盘；输出只有数字和类型。
'use strict';
const { app, BrowserWindow, ipcMain, session, desktopCapturer, screen, powerMonitor } = require('electron');
const http = require('http');
const fs = require('fs');
const os = require('os');
const dns = require('dns');
const path = require('path');
const { execFile } = require('child_process');

const opt = (k, d) => (process.env[k] === undefined || process.env[k] === '' ? d : process.env[k]);
const num = (k, d) => Number(opt(k, d));
const MODE = opt('POC_MODE', 'pair');                  // pair：两个隐藏窗口互连；serve：等待真实浏览器经 HTTP 轮询接入
const DURATION = num('POC_DURATION_SEC', 60);          // 传画面时长（秒）
const BASELINE = num('POC_BASELINE_SEC', 20);          // 开始会话前的空闲基线时长（秒）
const THROTTLE = opt('POC_THROTTLE', 'off');           // off：关闭后台节流；default：保持 Electron 默认
const CODEC = opt('POC_CODEC', 'default');             // default | h264（把 H.264 排到首位）
const CANDIDATES = opt('POC_CANDIDATES', 'lan');       // 只把哪一类本机候选交给对端：loopback | lan | all | none
const PERMISSION = opt('POC_PERMISSION', 'product');   // default：Electron 默认；product：只放行屏幕采集并自动指定主屏
const ALLOWED = opt('POC_ALLOW', 'media');
const ORDER = opt('POC_ORDER', 'connect-first');       // connect-first：先建连并判定同机，再采集；capture-first：先采集（对照）
const SAME_MACHINE = opt('POC_SAME_MACHINE', 'check'); // check：判定同机；skip：跳过判定（只用于单机验证采集通路）
const STUN = process.env.POC_STUN === undefined ? 'stun.miwifi.com:3478' : process.env.POC_STUN; // ICE 服务器，逗号分隔；设为空则不配置
const LEASE_SEC = num('POC_LEASE_SEC', 30);            // 画面租约：多少秒收不到续期就关闭采集；0 表示关闭租约
const LEASE_OUT_AT_SEC = num('POC_LEASE_OUT_AT_SEC', 0); // 开始采集多少秒后停止续期（模拟服务端失联）；0 表示不模拟
const MOTION = opt('POC_MOTION', '0') === '1';
const SAMPLE = num('POC_SAMPLE_SEC', 5);
const CONNECT_TIMEOUT = num('POC_CONNECT_TIMEOUT_SEC', 30);
const FPS = num('POC_FPS', 15);
const HTTP_PORT = num('POC_HTTP_PORT', 8791);
const HTTP_HOST = opt('POC_HTTP_HOST', '0.0.0.0');
const SIGNAL_URL = opt('POC_SIGNAL_URL', '').replace(/\/+$/, ''); // 信令中转模式：Runtime 侧主动连接这个地址上的 signal-relay.cjs 取送信令，本机不再提供网页
const RELAY_POLL_SEC = num('POC_RELAY_POLL_SEC', 5);             // 中转模式下没有会话时的取信令间隔（对应心跳）

// 只配 STUN。验证程序不支持 TURN：设计里不做中转，连不上就是「无法直连」。
const ICE_SERVERS = STUN ? [{ urls: STUN.split(',').map((u) => (/^(stun|stuns):/.test(u) ? u : 'stun:' + u)) }] : [];
const normalize = (a) => String(a || '').replace(/^::ffff:/i, '').replace(/%.*$/, '').toLowerCase();
const localAddresses = [];
for (const list of Object.values(os.networkInterfaces())) for (const ni of list || []) localAddresses.push(normalize(ni.address));
// 单机演示用：POC_LOCAL_LIST=none 时把本机地址表清空，观看端就会被判成「另一台电脑」，用来演练确认非同机后的采集流程。
if (opt('POC_LOCAL_LIST', 'all') === 'none') localAddresses.length = 0;

const OUT_DIR = path.join(__dirname, 'out');
fs.mkdirSync(OUT_DIR, { recursive: true });
const RUN_ID = [new Date().toISOString().replace(/[:.]/g, '-'), MODE, SIGNAL_URL ? 'relay' : 'local', ORDER, CODEC, CANDIDATES, SAME_MACHINE, STUN ? 'stun' : 'nostun'].join('_');
const LOG = path.join(OUT_DIR, RUN_ID + '.jsonl');
app.setPath('userData', path.join(OUT_DIR, 'userdata', RUN_ID));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const writeLine = (obj) => { try { fs.appendFileSync(LOG, JSON.stringify(Object.assign({ t: Date.now() }, obj)) + '\n'); } catch (e) { /* 日志失败不影响验证 */ } };
const marks = {};
const samples = [];
const gpuRows = [];
const systemEvents = [];
const candCounts = { sender: {}, receiver: {}, viewer: {} };
const resolveStats = { asked: 0, ok: 0, isLocal: 0 };
const signaling = { bytesIn: 0, bytesOut: 0, requests: 0 };
let typeByPid = {};

function ps(script) {
  return new Promise((resolve) => {
    execFile('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script], { windowsHide: true, timeout: 25000, maxBuffer: 4 * 1024 * 1024 }, (err, stdout) => {
      const text = String(stdout || '').trim();
      if (err || !text) return resolve(null);
      try { const v = JSON.parse(text); resolve(Array.isArray(v) ? v : v ? [v] : null); } catch (e) { resolve(null); }
    });
  });
}
const gpuOf = (pids) => ps("Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine | ForEach-Object { $m=[regex]::Match($_.Name,'^pid_(\\d+)_'); if($m.Success -and @(" + pids.join(',') + ") -contains [int]$m.Groups[1].Value){ $e=[regex]::Match($_.Name,'engtype_(.+)$').Groups[1].Value; [pscustomobject]@{e=$e;u=[double]$_.UtilizationPercentage} } } | ConvertTo-Json -Compress");
const cpuTimes = (pid) => ps("Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq " + pid + " -or $_.ParentProcessId -eq " + pid + " } | ForEach-Object { [pscustomobject]@{pid=$_.ProcessId;cs=[double]($_.KernelModeTime+$_.UserModeTime)/1e7} } | ConvertTo-Json -Compress");

function sumGpu(rows) {
  const by = {};
  for (const r of rows || []) by[r.e] = +((by[r.e] || 0) + r.u).toFixed(1);
  let total = 0;
  for (const k in by) total += by[k];
  return { total: +total.toFixed(1), by };
}
function appMetrics() {
  typeByPid = {};
  const byType = {};
  const pids = [];
  let total = 0;
  for (const p of app.getAppMetrics()) {
    const type = p.type || 'Unknown';
    const usage = (p.cpu && p.cpu.percentCPUUsage) || 0;
    typeByPid[p.pid] = type;
    pids.push(p.pid);
    byType[type] = +((byType[type] || 0) + usage).toFixed(2);
    total += usage;
  }
  return { total: +total.toFixed(2), byType, pids };
}

function lookupHost(name) {
  if (!name || !/\.local$/i.test(name)) return Promise.resolve({ ok: false, code: 'NOT_MDNS' });
  // 单机演示用：POC_RESOLVE=off 时不解析观看端的 mDNS 名字，用来演练「无法确认」时不开采集。
  if (opt('POC_RESOLVE', 'on') === 'off') return Promise.resolve({ ok: false, code: 'OFF' });
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ ok: false, timeout: true }), 2000);
    dns.lookup(name, { family: 4 }, (err, addr) => {
      clearTimeout(timer);
      resolve(err ? { ok: false, code: err.code } : { ok: true, addr, isLocal: localAddresses.includes(normalize(addr)) });
    });
  });
}

const roles = new Map();
const ready = new Set();
const pending = {};
let sender = null;
let receiver = null;
let lastPids = [];

function createWindow(role) {
  const win = new BrowserWindow({
    show: false,
    width: 1280,
    height: 720,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      additionalArguments: ['--poc-role=' + role],
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: THROTTLE !== 'off',
      partition: 'poc-' + role,
    },
  });
  roles.set(win.webContents.id, role);
  win.webContents.on('console-message', (...args) => {
    const a = args[0];
    const o = (a && typeof a === 'object' && 'message' in a) ? a : { level: args[1], message: args[2] };
    writeLine({ ev: 'console', role, level: o.level, message: String(o.message).slice(0, 300) });
  });
  win.loadFile(path.join(__dirname, role + '.html'));
  return win;
}

function setupPermissions() {
  const denyAll = (ses) => {
    ses.setPermissionCheckHandler(() => false);
    ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  };
  denyAll(session.fromPartition('poc-receiver'));
  const ses = session.fromPartition('poc-sender');
  if (PERMISSION === 'default') return;
  // 「product」模式：对应设计中采集窗口的权限设置，只放行屏幕采集，并直接指定主显示器。
  const seen = new Set();
  const note = (kind, permission, details) => { const key = kind + ':' + permission; if (!seen.has(key)) { seen.add(key); writeLine({ ev: 'perm', kind, permission, mediaTypes: details && details.mediaTypes }); } };
  // media 请求里 mediaTypes 非空的是摄像头或麦克风，一律拒绝；屏幕采集的 mediaTypes 为空。
  const allow = (permission, details) => ALLOWED.split(',').includes(permission) && !(permission === 'media' && details && Array.isArray(details.mediaTypes) && details.mediaTypes.length > 0);
  ses.setPermissionCheckHandler((_wc, permission) => { note('check', permission); return allow(permission); });
  ses.setPermissionRequestHandler((_wc, permission, callback, details) => { note('request', permission, details); callback(allow(permission, details)); });
  ses.setDisplayMediaRequestHandler(async (_request, callback) => {
    try {
      const primary = screen.getPrimaryDisplay();
      const sources = await desktopCapturer.getSources({ types: ['screen'], thumbnailSize: { width: 0, height: 0 } });
      const source = sources.find((s) => String(s.display_id) === String(primary.id)) || sources[0];
      writeLine({ ev: 'display-request', granted: !!source, screens: sources.length });
      callback(source ? { video: source } : null);
    } catch (e) {
      writeLine({ ev: 'display-request-error', text: String(e).slice(0, 200) });
      callback(null);
    }
  });
}

const runtimeSignals = [];
let seq = 0;

function relay(event, m) {
  const from = roles.get(event.sender.id);
  writeLine({ ev: 'signal', from, kind: m.kind, bytes: m.data && m.data.sdp ? m.data.sdp.length : 0 });
  if (MODE === 'pair') {
    const target = m.to === 'receiver' ? receiver : sender;
    if (target && !target.isDestroyed()) target.webContents.send('poc:signal', { kind: m.kind, data: m.data });
    return;
  }
  if (from !== 'sender') return;
  if (m.kind === 'offer') marks.offerAt = Date.now();
  if (m.kind === 'end') marks.senderEndAt = Date.now();
  if (SIGNAL_URL) return postSignal(m.kind, m.data);
  runtimeSignals.push({ seq: ++seq, kind: m.kind, data: m.data });
}

// 观看端发来的信令。start 开启一次会话并清空上一轮；应答与候选转给采集窗口；viewer-log 只用于计时与摘要。
const senderAlive = () => sender && !sender.isDestroyed();
function handleViewer(kind, data) {
  if (kind === 'start') {
    // 每个观看会话都从头开始：清空上一轮信令与计时。
    runtimeSignals.length = 0;
    seq = 0;
    for (const k of Object.keys(marks)) delete marks[k];
    candCounts.viewer = {};
    candCounts.sender = {};
    marks.startSent = Date.now();
    startRenewals();
    if (senderAlive()) sender.webContents.send('poc:signal', { kind: 'start' });
  } else if (kind === 'answer' || kind === 'candidate') {
    if (kind === 'answer') marks.answerAt = Date.now();
    if (senderAlive()) sender.webContents.send('poc:signal', { kind, data });
  } else if (kind === 'viewer-log' || kind === 'viewer-stats') {
    onEvent('viewer', kind === 'viewer-stats' ? 'stats' : data.name, kind === 'viewer-stats' ? data : Object.assign({}, data));
  }
}

// 信令中转模式：Runtime 侧主动把信令交给服务器，对应产品里 Host 用设备身份访问服务器。
async function postSignal(kind, data) {
  const body = JSON.stringify({ kind, data });
  signaling.bytesOut += Buffer.byteLength(body);
  signaling.requests += 1;
  try {
    await fetch(SIGNAL_URL + '/api/runtime', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
  } catch (e) { /* 服务器连不上：由画面租约处理，这里不重试 */ }
}

// 模拟 Runtime 连不上服务器：开始采集 LEASE_OUT_AT_SEC 秒之后不再续期，也不再取信令。
const leaseOut = () => LEASE_OUT_AT_SEC > 0 && !!marks.capturedAt && Date.now() - marks.capturedAt >= LEASE_OUT_AT_SEC * 1000;

// 中转模式下没有会话时每 RELAY_POLL_SEC 秒取一次信令（对应心跳），会话开始后 0.5 秒一次，开始传画面后 5 秒一次。
// 取到一次就算给画面续期。
async function relayLoop() {
  let after = 0;
  for (;;) {
    const sessionOpen = !!marks.startSent && !marks.captureClosedAt;
    const interval = !sessionOpen ? RELAY_POLL_SEC : marks.capturedAt ? 5 : 0.5;
    await sleep(interval * 1000);
    if (leaseOut()) continue;
    try {
      const res = await fetch(SIGNAL_URL + '/api/runtime?after=' + after, { cache: 'no-store' });
      const text = await res.text();
      signaling.bytesIn += Buffer.byteLength(text);
      signaling.requests += 1;
      const list = JSON.parse(text).signals || [];
      for (const s of list) { after = s.seq; handleViewer(s.kind, s.data); }
      if (sessionOpen) touchRenewal();
    } catch (e) { /* 连不上服务器：不续期 */ }
  }
}

// 画面租约：模拟服务端每 5 秒续期一次；LEASE_OUT_AT_SEC 之后不再续期，采集窗口必须自己按时关闭。
// 中转模式下没有这个定时器，续期来自 relayLoop 里取信令成功。
let renewTimer = null;
let lastRenewAt = 0;
function touchRenewal() {
  lastRenewAt = Date.now();
  if (LEASE_SEC > 0 && senderAlive()) sender.webContents.send('poc:signal', { kind: 'renew', data: { leaseSec: LEASE_SEC } });
}
function startRenewals() {
  if (renewTimer) clearInterval(renewTimer);
  renewTimer = null;
  lastRenewAt = Date.now();
  if (SIGNAL_URL || LEASE_SEC <= 0) return;
  touchRenewal();
  renewTimer = setInterval(() => { if (!leaseOut()) touchRenewal(); }, 5000);
}

function onEvent(role, name, data) {
  const d = data || {};
  if (name === 'cand') {
    const key = d.kind + ':' + d.type + ':' + d.proto;
    candCounts[role] = candCounts[role] || {};
    candCounts[role][key] = (candCounts[role][key] || 0) + 1;
    return;
  }
  writeLine({ ev: 'page', role, name, data: d });
  const isViewer = role === 'viewer';
  if (role === 'sender') {
    if (name === 'state' && d.pc === 'connected') marks.txConnectedAt = marks.txConnectedAt || Date.now();
    if (name === 'machine-check') { marks.machineCheckAt = marks.machineCheckAt || Date.now(); marks.machineVerdict = d.verdict; marks.machineRule = d.rule; marks.machineEvidence = d.evidence; }
    if (name === 'captured') { marks.capturedAt = marks.capturedAt || Date.now(); }
    if (name === 'capture-closed') { marks.captureClosedAt = marks.captureClosedAt || Date.now(); marks.captureCloseReason = d.reason; marks.captureCloseStarted = !!d.captureStarted; }
    if (name === 'lease-expired') marks.leaseExpiredAt = marks.leaseExpiredAt || Date.now();
    if (name === 'ice-failed') marks.iceFailedAt = marks.iceFailedAt || Date.now();
    if (name === 'track-ended') systemEvents.push({ t: Date.now(), e: 'track-ended' });
  }
  if (role === 'receiver') {
    if (name === 'state' && d.pc === 'connected') marks.rxConnectedAt = marks.rxConnectedAt || Date.now();
    if (name === 'first-frame') marks.firstFrameAt = marks.firstFrameAt || Date.now();
  }
  if (isViewer) {
    if (name === 'start') marks.startSent = marks.startSent || Date.now();
    if (name === 'state' && d.pc === 'connected') { marks.rxConnectedAt = marks.rxConnectedAt || Date.now(); marks.viewerConnectedDt = d.dt; }
    if (name === 'first-frame') { marks.firstFrameAt = marks.firstFrameAt || Date.now(); marks.viewerFirstFrameDt = d.dt; }
  }
}

ipcMain.on('poc:ready', (e) => ready.add(roles.get(e.sender.id)));
ipcMain.on('poc:signal', relay);
ipcMain.on('poc:event', (e, m) => {
  const role = roles.get(e.sender.id);
  if (m.name === 'stats' && pending[role]) { const done = pending[role]; pending[role] = null; done(m.data); return; }
  if (m.name === 'resolve') {
    // 观看端的 mDNS 候选名交给系统解析器。解析出的地址只回给采集页面做判定，不写日志。
    resolveStats.asked += 1;
    lookupHost(m.data.name).then((r) => {
      if (r.ok) { resolveStats.ok += 1; if (r.isLocal) resolveStats.isLocal += 1; }
      writeLine({ ev: 'resolve', ok: !!r.ok, code: r.code || null });
      if (!e.sender.isDestroyed()) e.sender.send('poc:signal', { kind: 'resolved', data: { id: m.data.id, address: r.ok ? r.addr : null } });
    });
    return;
  }
  onEvent(role, m.name, m.data);
});

function collectFrom(win, role) {
  return new Promise((resolve) => {
    if (!win || win.isDestroyed() || !ready.has(role)) return resolve(null);
    const timer = setTimeout(() => { pending[role] = null; resolve(null); }, 4000);
    pending[role] = (data) => { clearTimeout(timer); resolve(data); };
    win.webContents.send('poc:signal', { kind: 'collect' });
  });
}

async function sample(phase) {
  const [tx, rx] = await Promise.all([
    collectFrom(sender, 'sender'),
    collectFrom(receiver, 'receiver'),
  ]);
  const cpu = appMetrics();
  lastPids = cpu.pids;
  samples.push({ t: Date.now(), phase, tx, rx, cpu: { total: cpu.total, byType: cpu.byType }, signaling: Object.assign({}, signaling) });
}

function serve(req, res) {
  const url = new URL(req.url, 'http://localhost');
  signaling.requests += 1;
  signaling.bytesIn += Number(req.headers['content-length'] || 0);
  const json = (code, obj) => {
    const body = JSON.stringify(obj);
    signaling.bytesOut += Buffer.byteLength(body);
    res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*' });
    res.end(body);
  };
  if (req.method === 'GET' && url.pathname === '/api/config') return json(200, { iceServers: ICE_SERVERS });
  if (req.method === 'GET' && url.pathname === '/api/session') {
    const after = Number(url.searchParams.get('after') || 0);
    return json(200, { seq, signals: runtimeSignals.filter((s) => s.seq > after) });
  }
  if (req.method === 'POST' && url.pathname === '/api/signal') {
    let body = '';
    req.on('data', (c) => { body += c; });
    return req.on('end', () => {
      let m = {};
      try { m = JSON.parse(body); } catch (e) { /* 非 JSON 按空处理 */ }
      handleViewer(m.kind, m.data);
      json(200, { ok: true });
    });
  }
  if (req.method === 'GET' && /^\/[A-Za-z0-9_-]+\.js$/.test(url.pathname)) {
    const script = path.join(__dirname, url.pathname);
    if (!fs.existsSync(script)) return json(404, { ok: false });
    res.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8', 'Cache-Control': 'no-store' });
    return res.end(fs.readFileSync(script));
  }
  if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/viewer.html' || url.pathname === '/p4.html')) {
    const file = url.pathname === '/p4.html' ? 'p4.html' : 'viewer.html';
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
    return res.end(fs.readFileSync(path.join(__dirname, file)));
  }
  json(404, { ok: false });
}

function buildSummary(cpu0, cpu1, wallSec) {
  const stream = samples.filter((s) => s.phase === 'stream');
  const base = samples.filter((s) => s.phase === 'baseline');
  const nums = (fn) => stream.map(fn).filter((v) => typeof v === 'number' && !isNaN(v));
  const avg = (a) => (a.length ? +(a.reduce((x, y) => x + y, 0) / a.length).toFixed(2) : null);
  const min = (a) => (a.length ? +Math.min(...a).toFixed(2) : null);
  const pct = (a, p) => (a.length ? [...a].sort((x, y) => x - y)[Math.min(a.length - 1, Math.floor(a.length * p))] : null);
  const first = stream[0];
  const last = stream[stream.length - 1];
  const seconds = first && last ? (last.t - first.t) / 1000 : 0;
  const rate = (fn) => (seconds > 0 && first && last ? +((fn(last) - fn(first)) / seconds).toFixed(2) : null);
  const txOf = (s) => (s && s.tx && s.tx.tx) || {};
  const rxOf = (s) => (s.rx && s.rx.rx) || {};
  const pairOf = (s) => (s && s.rx && s.rx.pair) || null;
  const txPair = last && last.tx && last.tx.pair ? last.tx.pair : null;
  const anyRelay = samples.some((s) => [s.tx && s.tx.pair, s.rx && s.rx.pair].some((p) => p && p.relay));
  const since = (k) => (marks[k] && marks.startSent ? marks[k] - marks.startSent : null);
  const cores = {};
  if (cpu0 && cpu1 && wallSec > 0) {
    for (const b of cpu1) {
      const a = cpu0.find((x) => x.pid === b.pid);
      if (!a) continue;
      const type = typeByPid[b.pid] || 'gone';
      cores[type] = +((cores[type] || 0) + (b.cs - a.cs) / wallSec * 100).toFixed(2);
    }
  }
  const typeAvg = {};
  for (const s of stream) for (const k in s.cpu.byType) { typeAvg[k] = typeAvg[k] || []; typeAvg[k].push(s.cpu.byType[k]); }
  for (const k in typeAvg) typeAvg[k] = avg(typeAvg[k]);
  const baseTypeAvg = {};
  for (const s of base) for (const k in s.cpu.byType) { baseTypeAvg[k] = baseTypeAvg[k] || []; baseTypeAvg[k].push(s.cpu.byType[k]); }
  for (const k in baseTypeAvg) baseTypeAvg[k] = avg(baseTypeAvg[k]);
  const gpuStream = gpuRows.filter((r) => r.phase === 'stream').map((r) => r.gpu.total);
  const gpuBase = gpuRows.filter((r) => r.phase === 'baseline').map((r) => r.gpu.total);
  return {
    runId: RUN_ID,
    versions: { electron: process.versions.electron, chrome: process.versions.chrome, node: process.versions.node },
    config: { mode: MODE, signal: SIGNAL_URL ? 'relay' : 'in-process', durationSec: DURATION, order: ORDER, attach: opt('POC_ATTACH', 'replaceTrack'), sameMachine: SAME_MACHINE, codec: CODEC, candidates: CANDIDATES, iceServers: ICE_SERVERS.map((s) => s.urls.join(',')), leaseSec: LEASE_SEC, leaseOutAtSec: LEASE_OUT_AT_SEC, connectTimeoutSec: CONNECT_TIMEOUT, permission: PERMISSION, fps: FPS },
    timingMs: {
      offer: since('offerAt'),
      senderConnected: since('txConnectedAt'),
      machineCheck: since('machineCheckAt'),
      captureStarted: since('capturedAt'),
      receiverConnected: since('rxConnectedAt'),
      firstFrame: since('firstFrameAt'),
      viewerConnected: marks.viewerConnectedDt || null,
      viewerFirstFrame: marks.viewerFirstFrameDt || null,
    },
    // rule 是放行或拒绝所用的规则编号，evidence 是当时手里有哪些证据（只有计数和是否不同，不含地址）。
    sameMachine: { verdict: marks.machineVerdict || null, rule: marks.machineRule || null, evidence: marks.machineEvidence || null, resolve: resolveStats, simulated: { localListEmpty: opt('POC_LOCAL_LIST', 'all') === 'none', resolveOff: opt('POC_RESOLVE', 'on') === 'off' } },
    connected: !!marks.txConnectedAt,
    capture: {
      started: !!marks.capturedAt,
      closed: marks.captureClosedAt ? { reason: marks.captureCloseReason, msAfterCapture: marks.capturedAt ? marks.captureClosedAt - marks.capturedAt : null } : null,
      lease: { leaseSec: LEASE_SEC, expired: !!marks.leaseExpiredAt, msSinceLastRenew: marks.leaseExpiredAt ? marks.leaseExpiredAt - lastRenewAt : null },
    },
    streamSeconds: +seconds.toFixed(1),
    samples: { baseline: base.length, stream: stream.length },
    tx: {
      reportedFpsAvg: avg(nums((s) => txOf(s).fps)), reportedFpsMin: min(nums((s) => txOf(s).fps)),
      encodedFps: rate((s) => txOf(s).framesEncoded || 0),
      bitrateKbps: seconds > 0 ? +((rate((s) => txOf(s).bytes || 0) || 0) * 8 / 1000).toFixed(0) : null,
      width: last ? txOf(last).w : null, height: last ? txOf(last).h : null, codec: last ? txOf(last).codec : null,
      encoder: last ? txOf(last).enc : null,
      totalEncoded: last ? txOf(last).framesEncoded : null,
      cpuLimitedSamples: stream.filter((s) => s.tx && s.tx.tx && s.tx.tx.qlr === 'cpu').length,
    },
    rx: {
      decodedFps: rate((s) => rxOf(s).framesDecoded || 0), reportedFpsAvg: avg(nums((s) => rxOf(s).fps)), reportedFpsMin: min(nums((s) => rxOf(s).fps)),
      freezeCount: last ? rxOf(last).freezeCount : null, freezeSec: last ? rxOf(last).freezeSec : null,
      packetsLost: last ? rxOf(last).lost : null,
    },
    path: { receiverSide: pairOf(last) || null, senderSide: txPair || null, relayInPath: !!anyRelay },
    localCandidates: candCounts,
    // 本机信令模式下这是服务器经手的字节数；中转模式下服务器经手的字节数由 signal-relay.cjs 自己打印。
    signaling: { side: SIGNAL_URL ? 'runtime' : 'server', bytesIn: signaling.bytesIn, bytesOut: signaling.bytesOut, requests: signaling.requests, streamSeconds: +seconds.toFixed(1) },
    cpu: { baselineTotalAvg: avg(base.map((s) => s.cpu.total)), streamTotalAvg: avg(stream.map((s) => s.cpu.total)), streamByTypeAvg: typeAvg, processCoresPct: cores },
    gpu: { baselineTotalAvg: avg(gpuBase), streamTotalAvg: avg(gpuStream) },
    systemEvents,
  };
}

async function main() {
  // 兜底看门狗：无论如何到点退出，避免验证程序留在后台。
  setTimeout(() => app.exit(3), (BASELINE + DURATION + 150) * 1000);
  for (const ev of ['lock-screen', 'unlock-screen', 'suspend', 'resume']) {
    powerMonitor.on(ev, () => systemEvents.push({ t: Date.now(), e: ev }));
  }
  const displays = screen.getAllDisplays().map((d) => ({ id: d.id, w: d.size.width, h: d.size.height, scaleFactor: d.scaleFactor }));
  writeLine({ ev: 'run-start', runId: RUN_ID, electron: process.versions.electron, chrome: process.versions.chrome, displays, localNetworkCount: localAddresses.length });
  setupPermissions();

  sender = createWindow('sender');
  if (MODE === 'pair') receiver = createWindow('receiver');
  let motion = null;
  const showMotion = () => {
    if (!MOTION || motion) return;
    const { width: sw, height: sh } = screen.getPrimaryDisplay().size;
    motion = new BrowserWindow({ show: false, width: 480, height: 270, x: sw - 500, y: sh - 340, alwaysOnTop: true, focusable: false, skipTaskbar: true, resizable: false, title: 'screen-stream-poc motion' });
    motion.loadFile(path.join(__dirname, 'motion.html'));
    motion.showInactive();
  };
  const deadline = Date.now() + 15000;
  while (ready.size < (receiver ? 2 : 1) && Date.now() < deadline) await sleep(100);
  const config = { codec: CODEC, policy: CANDIDATES, fps: FPS, iceServers: ICE_SERVERS, localAddresses, order: ORDER, sameMachine: SAME_MACHINE, leaseSec: LEASE_SEC, connectTimeoutSec: CONNECT_TIMEOUT, attach: opt('POC_ATTACH', 'replaceTrack') };
  sender.webContents.send('poc:signal', { kind: 'config', data: config });
  if (receiver) receiver.webContents.send('poc:signal', { kind: 'config', data: { policy: CANDIDATES, iceServers: ICE_SERVERS } });
  writeLine({ ev: 'ready', windowsReady: ready.size, config: Object.assign({}, config, { localAddresses: '(' + localAddresses.length + ' 项，不记录)' }) });

  let cpu0 = null;
  let wallStart = Date.now();
  let gpuTimer = null;
  const gpuLoop = () => { gpuTimer = setInterval(async () => { const pids = lastPids; const g = sumGpu(await gpuOf(pids)); gpuRows.push({ t: Date.now(), phase: marks.capturedAt ? 'stream' : 'baseline', gpu: g }); }, 30000); };

  if (MODE === 'pair') {
    appMetrics();
    gpuLoop();
    await sleep(1000);
    for (let i = 0; i < Math.max(1, Math.round(BASELINE / SAMPLE)); i++) { await sleep(SAMPLE * 1000); await sample('baseline'); }
    cpu0 = await cpuTimes(process.pid);
    wallStart = Date.now();
    marks.startSent = Date.now();
    startRenewals();
    showMotion();
    sender.webContents.send('poc:signal', { kind: 'start' });
    const startedAt = marks.startSent;
    // 结束条件：采集已关闭（同机、判定未知、建连失败、租约到期、到时），或建连超时。
    while (Date.now() < startedAt + (CONNECT_TIMEOUT + DURATION + 10) * 1000) {
      await sleep(SAMPLE * 1000);
      await sample(marks.capturedAt ? 'stream' : 'connecting');
      if (marks.captureClosedAt) break;
      if (!marks.txConnectedAt && Date.now() - startedAt > CONNECT_TIMEOUT * 1000) break;
      if (marks.capturedAt && Date.now() >= marks.capturedAt + DURATION * 1000) { sender.webContents.send('poc:signal', { kind: 'stop' }); await sleep(1500); break; }
    }
    if (!marks.captureClosedAt) marks.noConnect = true;
  } else {
    // serve：等待观看页发起，POC_DURATION_SEC 秒后无论是否有人接入都结束。
    gpuLoop();
    let server = null;
    if (SIGNAL_URL) {
      // 中转模式：本机不监听任何端口，Runtime 侧只主动连接服务器。
      relayLoop();
      console.log('POC_READY relay ' + SIGNAL_URL);
      writeLine({ ev: 'serving', signal: 'relay' });
    } else {
      // 本机信令模式：验证程序自己提供网页和信令，供同一台或同网段的电脑打开。
      server = http.createServer(serve);
      await new Promise((r) => server.listen(HTTP_PORT, HTTP_HOST, r));
      console.log('POC_READY http://127.0.0.1:' + HTTP_PORT + '/viewer.html');
      writeLine({ ev: 'serving', port: HTTP_PORT, host: HTTP_HOST });
    }
    appMetrics();
    const until = Date.now() + DURATION * 1000;
    while (Date.now() < until) {
      await sleep(SAMPLE * 1000);
      // 会话结束（对方离开、同机、超时、租约到期）之后不算传画面，否则摘要里会出现负的帧率。
      await sample(marks.capturedAt && !marks.captureClosedAt ? 'stream' : 'baseline');
    }
    if (server) server.close();
  }
  clearInterval(gpuTimer);
  if (renewTimer) clearInterval(renewTimer);
  const cpu1 = await cpuTimes(process.pid);
  const wallSec = (Date.now() - wallStart) / 1000;
  const summary = buildSummary(cpu0, cpu1, wallSec);
  writeLine({ ev: 'run-end', summary });
  fs.writeFileSync(path.join(OUT_DIR, RUN_ID + '.summary.json'), JSON.stringify(summary, null, 2));
  console.log('POC_SUMMARY ' + JSON.stringify(summary));
  if (motion && !motion.isDestroyed()) motion.destroy();
  if (sender && !sender.isDestroyed()) sender.destroy();
  if (receiver && !receiver.isDestroyed()) receiver.destroy();
  await sleep(300);
  app.quit();
}

app.whenReady().then(main).catch((e) => {
  writeLine({ ev: 'fatal', text: String((e && e.stack) || e).slice(0, 600) });
  console.error('POC_FATAL ' + String((e && e.stack) || e));
  app.exit(2);
});
