# CDP 页面实时帧（page stream）实验纪要

> 用途：独立会话开发「登录辅助与页面实时帧」前的快速对齐材料（简要版）。
> 完整设计：[网页操作实时视图设计](../design/recruiting/login-assist-page-stream-design.md)（v3.2 已冻结通用 `page-stream/1.0`，Boss CDP 为首期）。
> 开发计划：[web-operation-live-view-dev-plan.md](../plans/recruiting/web-operation-live-view-dev-plan.md)。
> 状态：实验完成、方案定型，**待开发**。本文只记「已经实验过什么、结论是什么」。

## 1. 背景与目标

扫码登录 originally 由智能体截图转发二维码，人工中转延迟过大（出码→用户看到隔了整个对话回合，
码已过期）。改为：**runtime 推页面实时帧 → 云端登录辅助页（Web 链接）→ 用户自己看画面扫码**，
过期自动刷新、成功自动回调，全程无人工中转。

## 2. 已真机验证的结论（2026-08-31，本机 CDP 9222，Chrome 151）

### 2.1 扫码登录流程七环节全部通过

| 环节 | 要点 |
|---|---|
| 进入招聘侧 | 登录页点「我要招聘」标签（坐标 821,492） |
| 切二维码登录 | 点 `DIV.btn-sign-switch.ewm-switch`（523,370,40x40），CDP 点击有效 |
| 二维码载体 | `IMG`（659,512,200x200），`captureFullpage` 可完整截取 |
| 过期检测 | QR 区出现「请重新刷新二维码」+「点击刷新」文本即过期（码有效期约 1-2 分钟） |
| 过期恢复 | 动态定位「点击刷新」CDP 点击 → 新码 |
| 登录成功检测 | URL 跳离 `web/user` → `web/chat/recommend` |
| 登录后弹窗 | 复用 overlay heal（inspect→dismiss），已真机验证 |

### 2.2 页面帧流性能数据

| 指标 | 裁剪模式（登录框 420x460） | 整页（1278x1304） |
|---|---|---|
| 单帧耗时 | 36-50ms | 42-43ms |
| 单帧体积（JPEG q70） | **14KB** | 48KB |
| 变化检测（md5 比对） | 静止页 20 帧仅推 1 帧 | 同左 |
| 1fps 带宽 | 峰值 ~0.11Mbps，静止页≈0 | 峰值 ~0.38Mbps |

## 3. 技术要点（实验事实与产品取舍）

- **raw CDP WebSocket**：实验用 Node 原生 WebSocket；产品 Runtime 基线 Node ≥20，显式依赖 `ws`，不依赖传递依赖
- **SHA-256 变化检测**：帧内容 hash 比对，静止页几乎零推流；实验曾用 MD5，产品统一使用 SHA-256
- **MJPEG 仅为实验播放器**：实验用 `multipart/x-mixed-replace` 验证可看性；产品跨网络协议唯一使用 WSS `page-stream/1.0` 的 metadata + binary JPEG
- **帧率余量**：单帧 ~40ms 可支撑 10fps+，产品按 1-2fps 设计即可，带宽忽略不计
- **路线结论**：CDP 页面帧优于桌面帧流——画面只含 BOSS 页面（隐私面小）、无锁屏/遮挡限制、无 GPU/WGC 依赖；桌面流（WGC/GDI）保留给「人工接管看整个桌面」场景（原 R1，本期不做）

## 4. 边界与坑（实验中确认）

- CDP 帧只覆盖网页内容；BOSS 弹**原生系统对话框**（罕见）不在画面内——届时再评估桌面流
- 二维码 1-2 分钟过期：刷新循环必须自动化（刷新点击已真机验证，勿再人肉）
- 观看 ticket 等同临时凭证：必须短期、一次性、租户/用户隔离，并经 WebSocket subprotocol 传递；产品不生成可分享链接
- ⚠️ 实验探针脚本 `.tmp/probe-frame-stream.mjs` 未入库已被清理——需重建（约 30 分钟，参数本文已固化）；今后探针请放 `scripts/` 入库

## 5. 待开发清单（2026-09-01 v3.2 设计同步）

> 下表替代 2026-08-31 的“HTTPS 推帧 + 企微登录链接”产品化设想。实验结论不变；正式方案要求
> 用户在 Web Agent/第一方客户端显式同意后才建立 producer WSS，第三方渠道暂不开放。

| 层 | 内容 |
|---|---|
| aid-runtime | 独立 stream-control loop + `BossCdpPageFrameSource`；consent 后才截图并通过 producer WSS 上行；登录监听继续负责过期刷新/成功检测/overlay heal |
| 云端 | offer/session/consent 控制面 + 独立内存 `PageStreamGateway`；JPEG 仅保留最新帧，不进 DB/Redis/磁盘 |
| Web/第一方客户端 | 可信同意卡片 + 通用只读 viewer；未点击时零截图、零 producer 连接 |
| 工具 | `boss_login_qr` / `boss_login_status` 只负责登录业务；新工具仍须同步 catalog、Runtime manifest、Boss manifest 和子智能体白名单 |
| 渠道 | 企微/钉钉/飞书等第三方渠道暂不生成链接或帧；只提示回到 Web Agent/第一方客户端查看 |

## 6. 建议验收标准

未登录真机触发「登录 BOSS」→ 辅助页 1-2s 内出码 → 手机扫码 → 期间至少自动刷新一次过期码（用户无感）→
扫码成功自动关弹窗 → 云端收到登录回调 → agent 汇报「登录完成」。全程无人工转发截图。
