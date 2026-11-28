// 发送端（对应设计里的 Runtime 采集窗口）：先建立不含画面的连接并判定观看端是不是本机，确认不是之后才采集。
// 画面不显示、不落盘。每次收到 start 都先清理上一次的连接和采集。
const cfg = { codec: 'default', policy: 'lan', capture: 'getDisplayMedia', sourceId: null, fps: 15, iceServers: [], localAddresses: [], order: 'connect-first', sameMachine: 'check', peerPortCheck: false, leaseSec: 30, connectTimeoutSec: 30, degradeAtSec: 0, restoreAtSec: 0, degradeMode: 'sender' };
let pc = null;
let stream = null;
let transceiver = null;
let connectTimer = null;
let t0 = 0;
let captureStartedAt = 0;
let lastRenewAt = 0;
let leaseTimer = null;
let finished = false;
let resolveSeq = 0;
// 当前画质档：normal（原样）、degraded（已降低）、restored（降低后恢复）。只用于给采样打标签。
let quality = 'normal';
let currentTrack = null;
let savedConstraints = null;
const qualityTimers = [];
const remotes = []; // 观看端候选：{ id, kind, type, address }，address 是明文地址或解析出的地址，解析不出为 null
const ownMapped = []; // 本机经 STUN 得到的公网映射地址
const log = (name, data) => window.poc.event(name, data || {});
const toPeer = (kind, data) => window.poc.signal('receiver', kind, data);
const normalize = (a) => String(a || '').replace(/^::ffff:/i, '').replace(/%.*$/, '').toLowerCase();
const isLocalAddress = (a) => !!a && cfg.localAddresses.indexOf(normalize(a)) >= 0;

function wire(next) {
  next.onconnectionstatechange = () => {
    log('state', { pc: next.connectionState, dt: Date.now() - t0 });
    if (next !== pc) return;
    if (next.connectionState === 'connected' && !captureStartedAt && !finished) { verifyThenCapture(next); return; }
    if ((next.connectionState === 'failed' || next.connectionState === 'closed') && !finished) closeAll('ice-failed');
    else if (next.connectionState === 'disconnected' && captureStartedAt) log('disconnected', { dt: Date.now() - t0 });
  };
  next.oniceconnectionstatechange = () => log('ice', { state: next.iceConnectionState, dt: Date.now() - t0 });
  next.onicecandidateerror = (e) => log('canderr', { code: e.errorCode, text: String(e.errorText || '').slice(0, 80) });
  next.onicecandidate = (e) => {
    if (next !== pc) return;
    if (!e.candidate) { log('gatherend', { dt: Date.now() - t0 }); return; }
    const c = POC.classify(e.candidate);
    log('cand', c);
    // 采集窗口拿到过屏幕采集权限，公布的是真实地址，所以自己的公网映射地址是明文。
    if (c.type === 'srflx' && e.candidate.address && ownMapped.indexOf(e.candidate.address) < 0) ownMapped.push(e.candidate.address);
    if (POC.keep(e.candidate, cfg.policy)) toPeer('candidate', e.candidate.toJSON());
  };
}

function cleanup() {
  for (const id of qualityTimers) clearTimeout(id);
  qualityTimers.length = 0;
  quality = 'normal';
  currentTrack = null;
  savedConstraints = null;
  if (stream) { for (const track of stream.getTracks()) { try { track.stop(); } catch (e) { /* 已停止 */ } } }
  stream = null;
  transceiver = null;
  const old = pc;
  pc = null;
  if (old) { try { old.close(); } catch (e) { /* 已关闭 */ } }
}

// 关闭采集并结束会话。reason 是结束原因，会原样交给观看端。
function closeAll(reason) {
  if (finished) return;
  finished = true;
  if (leaseTimer) { clearInterval(leaseTimer); leaseTimer = null; }
  if (connectTimer) { clearTimeout(connectTimer); connectTimer = null; }
  const sinceCapture = captureStartedAt ? Date.now() - captureStartedAt : null;
  cleanup();
  log('capture-closed', { reason, captureStarted: !!captureStartedAt, sinceCaptureMs: sinceCapture, dt: Date.now() - t0 });
  toPeer('end', { reason });
}

function capture() {
  return navigator.mediaDevices.getDisplayMedia({
    audio: false,
    video: { width: { max: 1280 }, height: { max: 720 }, frameRate: { max: cfg.fps, ideal: cfg.fps } },
  });
}

async function pairInfo(mine) {
  try {
    const reports = await mine.getStats();
    let pair = null;
    reports.forEach((r) => { if (r.type === 'candidate-pair' && r.nominated && r.state === 'succeeded') pair = r; });
    if (!pair) return null;
    const local = reports.get(pair.localCandidateId);
    const remote = reports.get(pair.remoteCandidateId);
    const remoteAddress = remote && (remote.address || remote.ip);
    const remoteClass = POC.classify({ address: remoteAddress, type: remote && remote.candidateType });
    return {
      local: POC.classify({ address: local && local.address, type: local && local.candidateType }),
      remote: Object.assign(remoteClass, { hasAddress: !!remoteAddress, isLocal: isLocalAddress(remoteAddress) }),
      relay: (local && local.candidateType === 'relay') || !!(remote && remote.candidateType === 'relay'),
      rttMs: pair.currentRoundTripTime ? +(pair.currentRoundTripTime * 1000).toFixed(1) : null,
    };
  } catch (e) {
    return null;
  }
}

// 三种结果：same（确认同机）、other（确认非同机）、unknown（无法确认）。
// 规则在 verdict.js 里，只用两端明文可见的地址候选。连接统计只用来报告线路，不参与判定：浏览器不提供对端的实际地址。
function verdict() {
  return POC.verdict({ locals: cfg.localAddresses, mapped: ownMapped, remotes });
}

// 「同一台电脑」的第二种证据（cfg.peerPortCheck）：对端连入用的端口是不是本机的一个套接字，由主进程查本机端口表回答。
// 连接统计里对端候选不给地址，但给端口。全部成功候选对的对端端口都是本机的 → 同机；有不属于本机的 → 另一台电脑。
// 不依赖 mDNS 解析，适用于对端在另一个网段、名字解析不出来的情况。
const portQueries = {};
let portQuerySeq = 0;
function askLocalPorts(ports) {
  return new Promise((resolve) => {
    const id = ++portQuerySeq;
    portQueries[id] = resolve;
    window.poc.event('peer-ports', { id, ports });
    setTimeout(() => { if (portQueries[id]) { delete portQueries[id]; resolve(null); } }, 3000);
  });
}
async function peerPorts(mine) {
  try {
    const reports = await mine.getStats();
    const seen = new Map();
    reports.forEach((r) => {
      if (r.type !== 'candidate-pair' || r.state !== 'succeeded') return;
      const remote = reports.get(r.remoteCandidateId);
      if (remote && remote.port) seen.set(remote.protocol + ':' + remote.port, { port: remote.port, protocol: remote.protocol || 'udp' });
    });
    return [...seen.values()];
  } catch (e) {
    return [];
  }
}

async function verifyThenCapture(mine) {
  const skipped = cfg.sameMachine === 'skip';
  let result = verdict();
  // 候选名还在解析、或本机的公网映射地址还没到时等一小会儿，仍然拿不到确切结果就按「无法确认」处理。
  for (let i = 0; i < 15 && !skipped && result.verdict === 'unknown' && remotes.length; i++) { await new Promise((r) => setTimeout(r, 100)); result = verdict(); }
  let peer = null;
  if (!skipped && cfg.peerPortCheck && result.verdict !== 'same') {
    // 地址候选的规则没有判成同机：再看对端端口。等成功的候选对出现，最多约 1.5 秒。
    let ports = [];
    for (let i = 0; i < 15 && !ports.length; i++) { ports = await peerPorts(mine); if (!ports.length) await new Promise((r) => setTimeout(r, 100)); }
    const answer = ports.length ? await askLocalPorts(ports) : null;
    peer = { pairs: ports.length, local: answer ? answer.filter((r) => r.local).length : null };
    // 查不到结果时保持原结论（多半是无法确认，不采集）。规则 5：对端端口全是本机套接字，同机；规则 6：有不属于本机的，另一台电脑。
    if (answer && answer.length) result = answer.every((r) => r.local) ? { verdict: 'same', rule: 5, evidence: result.evidence } : { verdict: 'other', rule: 6, evidence: result.evidence };
  }
  const pair = await pairInfo(mine);
  const slim = pair ? { local: pair.local, remote: pair.remote, relay: pair.relay, rttMs: pair.rttMs } : null;
  // evidence 只有计数和是否不同，不含地址；peer 只有条数。
  log('machine-check', { verdict: skipped ? 'skipped(' + result.verdict + ')' : result.verdict, rule: result.rule, evidence: result.evidence, peer, pair: slim });
  if (!skipped && result.verdict !== 'other') return closeAll(result.verdict === 'same' ? 'same-machine' : 'machine-check-unknown');
  await startCapture(mine);
}

async function startCapture(mine) {
  try {
    stream = await capture();
  } catch (e) {
    log('capture-error', { name: e && e.name, text: String((e && e.message) || e).slice(0, 200), dt: Date.now() - t0 });
    return closeAll('capture-error');
  }
  if (mine !== pc) return cleanup();
  const track = stream.getVideoTracks()[0];
  const s = track.getSettings();
  track.addEventListener('ended', () => { log('track-ended', { dt: Date.now() - t0 }); closeAll('track-ended'); });
  try { track.contentHint = 'detail'; } catch (e) { log('hint-error', { text: String(e).slice(0, 80) }); }
  let sender;
  if (cfg.attach === 'addTrack') {
    // 对照组：传统写法，轨道在生成邀约之前就带上并关联到一个媒体流（只能在先采集的顺序下用）。
    sender = mine.addTransceiver(track, { direction: 'sendonly', streams: [stream] }).sender;
  } else {
    sender = transceiver.sender;
    try { await sender.replaceTrack(track); } catch (e) { log('replace-error', { text: String((e && e.message) || e).slice(0, 120) }); }
  }
  const p = sender.getParameters();
  p.degradationPreference = 'maintain-resolution';
  if (!p.encodings || !p.encodings.length) p.encodings = [{}];
  p.encodings[0].maxBitrate = 2500000;
  try { await sender.setParameters(p); } catch (e) { log('setparams-error', { text: String(e).slice(0, 120) }); }
  captureStartedAt = Date.now();
  currentTrack = track;
  savedConstraints = track.getConstraints ? track.getConstraints() : {};
  log('captured', { w: s.width, h: s.height, fps: s.frameRate, dt: captureStartedAt - t0 });
  // 画面租约：收不到续期超过 leaseSec 就自己关闭采集。
  if (cfg.leaseSec > 0) {
    leaseTimer = setInterval(() => {
      const gap = Date.now() - lastRenewAt;
      if (gap > cfg.leaseSec * 1000) { log('lease-expired', { msSinceLastRenew: gap, dt: Date.now() - t0 }); closeAll('lease-expired'); }
    }, 500);
  }
  // 降低画质与恢复的演练（设计 4.5）：开始采集 degradeAtSec 秒后降低，再过到 restoreAtSec 秒时恢复。
  // 产品里什么时候降由 Host 按编码器是否持续受限来决定；这里只验证降低的动作本身能不能减轻编码负载。
  if (cfg.degradeAtSec > 0) qualityTimers.push(setTimeout(() => setQuality(mine, sender, 'degraded'), cfg.degradeAtSec * 1000));
  if (cfg.restoreAtSec > 0) qualityTimers.push(setTimeout(() => setQuality(mine, sender, 'restored'), cfg.restoreAtSec * 1000));
}

// 两种降低画质的做法：sender 改发送端的编码参数（帧率上限 5 帧、按比例缩到约 480p、限制码率），
// track 改采集轨道的约束（同样的帧率和尺寸上限）。1280×720 缩小 1.5 倍是 853×480。
async function setQuality(mine, sender, target) {
  const started = Date.now();
  const lowered = target === 'degraded';
  let ok = true;
  let text = null;
  try {
    if (mine !== pc) throw new Error('connection closed');
    if (cfg.degradeMode === 'track' && currentTrack) {
      await currentTrack.applyConstraints(lowered ? { frameRate: { max: 5 }, width: { max: 854 }, height: { max: 480 } } : savedConstraints);
    } else {
      const p = sender.getParameters();
      if (!p.encodings || !p.encodings.length) p.encodings = [{}];
      p.encodings[0].maxFramerate = lowered ? 5 : cfg.fps;
      p.encodings[0].scaleResolutionDownBy = lowered ? 1.5 : 1;
      p.encodings[0].maxBitrate = lowered ? 600000 : 2500000;
      await sender.setParameters(p);
    }
  } catch (e) {
    ok = false;
    text = String((e && e.message) || e).slice(0, 160);
  }
  quality = target;
  log('quality', { target, mode: cfg.degradeMode, ok, text, ms: Date.now() - started, sinceCaptureMs: captureStartedAt ? Date.now() - captureStartedAt : null });
}

async function start() {
  cleanup();
  finished = false;
  captureStartedAt = 0;
  remotes.length = 0;
  ownMapped.length = 0;
  t0 = Date.now();
  lastRenewAt = Date.now();
  const mine = new RTCPeerConnection({ iceServers: cfg.iceServers, bundlePolicy: 'max-bundle' });
  pc = mine;
  wire(mine);
  // 没有可用路径时 ICE 不报失败（例如完全没有候选地址），由这里按时结束会话；此时还没有采集，全程不传出画面。
  if (cfg.connectTimeoutSec > 0) {
    connectTimer = setTimeout(() => {
      if (captureStartedAt || finished) return;
      log('connect-timeout', { sec: cfg.connectTimeoutSec, dt: Date.now() - t0 });
      closeAll('connect-failed');
    }, cfg.connectTimeoutSec * 1000);
  }
  // 先建一条不含画面的视频通道；确认观看端不是本机之后，用 replaceTrack 把画面接上，不需要重新协商。
  if (!(cfg.order === 'capture-first' && cfg.attach === 'addTrack')) {
    transceiver = mine.addTransceiver('video', { direction: 'sendonly' });
    if (cfg.codec === 'h264') {
      const caps = RTCRtpSender.getCapabilities('video');
      const isH264 = (c) => c.mimeType.toLowerCase() === 'video/h264';
      transceiver.setCodecPreferences([...caps.codecs.filter(isH264), ...caps.codecs.filter((c) => !isH264(c))]);
    }
  }
  if (cfg.order === 'capture-first') await startCapture(mine);
  try {
    const offer = await mine.createOffer();
    await mine.setLocalDescription(offer);
    const sd = mine.currentLocalDescription || mine.localDescription;
    log('offer', { dt: Date.now() - t0, bytes: sd.sdp.length, media: /m=video/.test(sd.sdp) });
    toPeer('offer', { type: sd.type, sdp: sd.sdp });
  } catch (e) {
    log('offer-error', { text: String((e && e.message) || e).slice(0, 200) });
  }
}

window.poc.onMessage(async (m) => {
  try {
    if (m.kind === 'config') { Object.assign(cfg, m.data); return; }
    if (m.kind === 'start') { await start(); return; }
    if (m.kind === 'stop') { return closeAll('stopped'); }
    if (m.kind === 'renew') { lastRenewAt = Date.now(); return; }
    if (m.kind === 'resolved') { const r = remotes.find((x) => x.id === m.data.id); if (r) r.address = m.data.address || null; return; }
    if (m.kind === 'peer-ports') { const done = portQueries[m.data.id]; if (done) { delete portQueries[m.data.id]; done(m.data.result); } return; }
    if (!pc) return;
    if (m.kind === 'answer') { await pc.setRemoteDescription(m.data); log('answer-set', { dt: Date.now() - t0 }); return; }
    if (m.kind === 'candidate') {
      const c = POC.classify(m.data);
      const entry = { id: ++resolveSeq, kind: c.kind, type: c.type, address: null };
      remotes.push(entry);
      // toJSON() 不带 address 字段，地址（或 mDNS 名字）要从 candidate 这一行里取。
      const host = String(m.data.candidate || '').split(' ')[4] || '';
      // 明文地址（公网映射地址）直接用；mDNS 名字交给主进程用系统解析器解析。
      if (c.kind !== 'mdns' && host) entry.address = host;
      else if (c.kind === 'mdns') window.poc.event('resolve', { id: entry.id, name: host });
      try { await pc.addIceCandidate(m.data); } catch (e) { log('addcand-error', { text: String((e && e.message) || e).slice(0, 80) }); }
      return;
    }
    if (m.kind === 'collect') { log('stats', await POC.collect(pc, cfg.localAddresses, { quality })); }
  } catch (e) {
    log('error', { where: m.kind, text: String((e && e.message) || e).slice(0, 200) });
  }
});
window.poc.ready();
