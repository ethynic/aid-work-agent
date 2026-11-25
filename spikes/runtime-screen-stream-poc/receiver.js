// 接收端：只收不看。画面只送进 video 元素解码，窗口隐藏，不显示、不落盘。
// 不预先加通道：协商时浏览器未必复用预加的通道，画面会落在另一条通道上，所以直接用 ontrack 给的轨道。
const cfg = { policy: 'lan', iceServers: [] };
let pc = null;
let attached = false;
let t0 = 0;
const video = document.createElement('video');
video.muted = true; video.autoplay = true; video.playsInline = true;
document.body.appendChild(video);
const log = (name, data) => window.poc.event(name, data || {});
const toPeer = (kind, data) => window.poc.signal('sender', kind, data);

function attachTrack(track) {
  if (!track || attached) return;
  attached = true;
  video.srcObject = new MediaStream([track]);
  video.play().catch((err) => log('play-error', { text: String(err).slice(0, 80) }));
  log('attached', { dt: Date.now() - t0 });
}

function build() {
  pc = new RTCPeerConnection({ iceServers: cfg.iceServers, bundlePolicy: 'max-bundle' });
  pc.ontrack = (e) => attachTrack(e.track);
  video.addEventListener('loadeddata', () => log('first-frame', { dt: Date.now() - t0, w: video.videoWidth, h: video.videoHeight }));
  pc.onconnectionstatechange = () => log('state', { pc: pc.connectionState, dt: Date.now() - t0 });
  pc.onicecandidate = (e) => {
    if (!e.candidate) return;
    log('cand', POC.classify(e.candidate));
    if (POC.keep(e.candidate, cfg.policy)) toPeer('candidate', e.candidate.toJSON());
  };
}

window.poc.onMessage(async (m) => {
  try {
    if (m.kind === 'config') { Object.assign(cfg, m.data); build(); return; }
    if (!pc) build();
    if (m.kind === 'offer') {
      t0 = Date.now();
      await pc.setRemoteDescription(m.data);
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      const sd = pc.currentLocalDescription || pc.localDescription;
      log('answer', { dt: Date.now() - t0, bytes: sd.sdp.length });
      toPeer('answer', { type: sd.type, sdp: sd.sdp });
      return;
    }
    if (m.kind === 'candidate') { try { await pc.addIceCandidate(m.data); } catch (e) { log('addcand-error', { text: String((e && e.message) || e).slice(0, 80) }); } return; }
    if (m.kind === 'collect') { log('stats', await POC.collect(pc)); }
  } catch (e) {
    log('error', { where: m.kind, text: String((e && e.message) || e).slice(0, 200) });
  }
});
window.poc.ready();
