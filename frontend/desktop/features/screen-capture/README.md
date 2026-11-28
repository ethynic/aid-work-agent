# 画面采集 feature

隐藏采集窗口的页面（设计 6.1）：入口 `screen.html`（由 `vite.desktop.config.ts` 多入口构建，随包发布在 `dist/renderer/screen.html`），壳经 `aidagent://app/screen.html` 加载。无界面：加载后立即建立不含画面的 WebRTC 连接，等 Host 经壳转交的同机判定，确认非本机后才 `getDisplayMedia` 采集主屏幕并用 `replaceTrack` 接到已协商的通道上。

- `bridge.ts`：`electron/screen-preload.cts` 暴露的 `window.screenCapture` 的类型与取用。只暴露启动参数（会话编号、stun_urls）、收 Host 消息、发页面消息；页面不访问网络，没有其他能力。
- `screenCaptureClient.ts`：连接与采集逻辑，参照 `spikes/runtime-screen-stream-poc/sender.js`。纯函数（地址分类、线路判定、H.264 排序、统计提取）导出供单测。

关键约定：

- `verdict` 只有 `other` 才采集；`same` 报 `closed {reason:'same_machine'}`，`unknown` 报 `closed {reason:'machine_check_unknown'}`（设计 §10，网页据此提示「无法确认」）。
- 网页候选经桥到达时，页面回发 `remote-candidate` 作为同机判定证据并 `addIceCandidate`；名字解析在 Host 完成，页面忽略 `resolved`。
- `peer-ports` 在连接建立后等成功的候选对（最多约 1.5 秒）上报一次，供 Host 查本机端口表（规则 5、6）。
- 传画面后每 5 秒读统计：编码器因 CPU 受限只在状态变化时上报（信令按会话计数，上限 128 条）；线路随 `streaming {path}` 上报一次。
- 降低画质用采集轨道的 `applyConstraints`（5 帧、854×480），恢复用保存的初始约束；都不需要重新协商。
- 结束原因按阶段：出画面前 `connect_failed`，之后 `runtime_stopped`。

feature 内 Vitest 配置可独立运行：`npx vitest run --config desktop/features/screen-capture/vitest.config.ts`。测试用 jsdom + 假 `RTCPeerConnection`/`RTCRtpSender`/`navigator.mediaDevices`/桥与 mock 时钟，不启动 Electron。
