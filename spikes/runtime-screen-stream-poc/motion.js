// 动态内容窗口：滚动文字加移动色块，模拟日常办公画面的变化，让编码器有真实负载。
const cv = document.getElementById('c');
const cx = cv.getContext('2d');
const W = cv.width;
const H = cv.height;
const ROWS = ['Runtime 任务进度', 'boss_filter 第 12 页 完成', '候选人沟通 已发送', '等待页面加载 326ms', 'boss_filter 第 13 页 进行中', '会话读取 完成', '定时任务 明日 09:00', '设备心跳 正常'];

function draw(now) {
  const t = now / 1000;
  const g = cx.createLinearGradient(0, 0, W, H);
  g.addColorStop((Math.sin(t * 0.4) + 1) / 4, '#16202b');
  g.addColorStop(1, '#0c1116');
  cx.fillStyle = g;
  cx.fillRect(0, 0, W, H);
  cx.font = '600 11px "Segoe UI", "Microsoft YaHei UI", sans-serif';
  cx.textBaseline = 'middle';
  for (let i = 0; i < ROWS.length; i++) {
    const y = ((i * 30 + t * 40) % (H + 30)) - 15;
    cx.fillStyle = i % 3 === 0 ? '#60a5fa' : i % 3 === 1 ? '#34d399' : '#cbd5e1';
    cx.fillText(ROWS[i], 14, y);
  }
  const x = (Math.sin(t * 1.3) * 0.5 + 0.5) * (W - 90) + 45;
  const y = (Math.cos(t * 0.9) * 0.5 + 0.5) * (H - 60) + 30;
  const rg = cx.createRadialGradient(x, y, 2, x, y, 44);
  rg.addColorStop(0, 'rgba(185,241,61,.9)');
  rg.addColorStop(1, 'rgba(185,241,61,0)');
  cx.fillStyle = rg;
  cx.fillRect(x - 44, y - 44, 88, 88);
  cx.fillStyle = '#e8ece9';
  cx.font = '600 13px Consolas, monospace';
  cx.fillText(new Date().toLocaleTimeString(), W - 96, 16);
  requestAnimationFrame(draw);
}
requestAnimationFrame(draw);
