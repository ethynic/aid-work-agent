// 同机判定规则（与设计 4.2 一致）。纯函数：浏览器里挂在 window.POC 上，Node 里可以直接 require，便于单测。
// 只用两端明文可见的地址候选，不使用连接统计：浏览器不提供对端的实际地址。
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.POC = Object.assign(root.POC || {}, { verdict: factory() });
})(typeof self !== 'undefined' ? self : this, function () {
  const normalize = (a) => String(a || '').replace(/^::ffff:/i, '').replace(/%.*$/, '').toLowerCase();
  const uniq = (list) => Array.from(new Set(list.map(normalize).filter(Boolean)));
  const family = (ip) => (ip.includes(':') ? 6 : 4);

  // locals：本机全部网卡地址；mapped：本机经 STUN 得到的公网映射地址；remotes：网页的地址候选 [{ type, address }]，
  // address 是明文地址，mDNS 名字解析成功时填解析出的地址，解析不出为 null。
  return function verdict(input) {
    const mine = uniq((input && input.locals) || []);
    const myMapped = uniq((input && input.mapped) || []);
    const all = (input && input.remotes) || [];
    const seen = all.map((r) => ({ type: r.type, address: normalize(r.address) || null })).filter((r) => r.address);
    const viewerMapped = seen.filter((r) => r.type === 'srflx');
    // 「可比」指同一种地址族里本机也拿到了映射地址。本机一个都没拿到时，网页的映射地址说明不了任何事。
    const comparable = (r) => myMapped.some((ip) => family(ip) === family(r.address));
    const differs = (r) => comparable(r) && !myMapped.includes(r.address);
    const evidence = {
      viewerCandidates: all.length,
      viewerResolved: seen.length,
      viewerMapped: viewerMapped.length,
      runtimeMapped: myMapped.length,
      // 是否有可比且与本机不同的网页映射地址
      mappedDiffers: viewerMapped.some(differs),
    };
    // 规则 1：网页任一候选的地址是本机网卡地址 → 同机。
    if (seen.some((r) => mine.includes(r.address))) return { verdict: 'same', rule: 1, evidence };
    // 规则 2：网页的内网候选解析出的地址不是本机网卡地址 → 同网段的另一台电脑。
    if (seen.some((r) => r.type === 'host' && !mine.includes(r.address))) return { verdict: 'other', rule: 2, evidence };
    // 规则 3：双方的公网映射地址可比，且网页的有不在本机映射地址里的 → 另一个网络的电脑。
    if (evidence.mappedDiffers) return { verdict: 'other', rule: 3, evidence };
    // 规则 4：其余 → 无法确认，不采集。
    return { verdict: 'unknown', rule: 4, evidence };
  };
});
