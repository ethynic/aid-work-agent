// 各端共用的统计和候选分类。只输出数字与类型，不输出画面内容，也不输出本机地址。
(function () {
  function classify(cand) {
    const text = String((cand && cand.candidate) || '');
    const parts = text.split(' ');
    const addr = String((cand && (cand.address || cand.ip)) || parts[4] || '');
    const type = String((cand && (cand.type || cand.candidateType)) || parts[7] || '').replace(/^a=/, '');
    let kind = 'other';
    if (/\.local$/i.test(addr)) kind = 'mdns';
    else if (/^(127\.|::1$)/i.test(addr)) kind = 'loopback';
    else if (/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.)/.test(addr) || /^f[cd]/i.test(addr)) kind = 'private';
    else if (/^(198\.1[89]\.|198\.19\.|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.)/.test(addr)) kind = 'shared';
    else if (addr) kind = 'public';
    return { type, kind, proto: parts[2] || '', port: Number(parts[5]) || 0 };
  }

  function keep(cand, policy) {
    if (policy === 'all') return true;
    if (policy === 'none') return false;
    const kind = classify(cand).kind;
    if (policy === 'loopback') return kind === 'loopback';
    if (policy === 'lan') return kind === 'private' || kind === 'shared' || kind === 'mdns';
    return true;
  }

  const normalize = (a) => String(a || '').replace(/^::ffff:/i, '').replace(/%.*$/, '').toLowerCase();

  // localAddresses：本机全部网卡地址，只用来判断对端地址是不是本机的，结果以布尔值输出。
  // extra：调用方想让每个采样多带的字段（例如当前的画质档），原样放进结果。
  async function collect(pc, localAddresses, extra) {
    const mine = localAddresses || [];
    const out = { vis: document.visibilityState };
    if (extra) Object.assign(out, extra);
    const reports = await pc.getStats();
    reports.forEach((r) => {
      if (r.type === 'outbound-rtp' && r.kind === 'video') {
        const codec = reports.get(r.codecId);
        out.tx = {
          framesEncoded: r.framesEncoded, framesSent: r.framesSent, bytes: r.bytesSent,
          w: r.frameWidth, h: r.frameHeight, fps: r.framesPerSecond,
          enc: r.encoderImplementation, codec: codec && codec.mimeType,
          // 设计 4.5 的任务优先靠这个信号：编码器是否因 CPU 跟不上而受限（none / cpu / bandwidth / other）。
          qlr: r.qualityLimitationReason || null,
          // 编码一帧平均用多少毫秒（累计编码时间 ÷ 已编码帧数）、累计编码时间（秒）、平均量化参数（越小越清晰）。
          // 用来直接比较降低画质前后编码本身的开销，不受验证程序其他进程固定开销的影响。
          encMsAvg: r.totalEncodeTime && r.framesEncoded ? +((r.totalEncodeTime * 1000) / r.framesEncoded).toFixed(2) : null,
          encTime: r.totalEncodeTime || 0,
          qpAvg: r.qpSum && r.framesEncoded ? +(r.qpSum / r.framesEncoded).toFixed(1) : null,
        };
      } else if (r.type === 'inbound-rtp' && r.kind === 'video') {
        const codec = reports.get(r.codecId);
        out.rx = {
          framesDecoded: r.framesDecoded, framesReceived: r.framesReceived, bytes: r.bytesReceived,
          w: r.frameWidth, h: r.frameHeight, fps: r.framesPerSecond,
          dec: r.decoderImplementation, freezeCount: r.freezeCount, freezeSec: r.totalFreezesDuration,
          lost: r.packetsLost, codec: codec && codec.mimeType,
        };
      } else if (r.type === 'candidate-pair' && r.nominated && r.state === 'succeeded') {
        const local = reports.get(r.localCandidateId);
        const remote = reports.get(r.remoteCandidateId);
        const localAddress = local && (local.address || local.ip);
        const remoteAddress = remote && (remote.address || remote.ip);
        out.pair = {
          local: Object.assign(classify({ address: localAddress, type: local && local.candidateType }), { hasAddress: !!localAddress }),
          remote: Object.assign(classify({ address: remoteAddress, type: remote && remote.candidateType }), { hasAddress: !!remoteAddress, isLocal: !!remoteAddress && mine.indexOf(normalize(remoteAddress)) >= 0 }),
          relay: !!(local && local.candidateType === 'relay') || !!(remote && remote.candidateType === 'relay'),
          rttMs: r.currentRoundTripTime ? +(r.currentRoundTripTime * 1000).toFixed(1) : null,
        };
      }
    });
    return out;
  }

  window.POC = { classify, keep, collect };
})();
