# Runtime 桌面实时画面 · Phase 0 技术验证程序

对应[开发计划](../../docs/plans/plan-runtime-screen-stream.md)的 Phase 0 和[调研报告](../../docs/research/runtime-screen-stream-feasibility-research.md)第 6 节。**只用于验证，不进产品代码**；结论以调研报告为准。

## 隐私约定

- 采集到的画面只在隐藏窗口之间或本机浏览器之间经 WebRTC 传输，**不显示、不截图、不落盘**。
- 输出只有数字、时延和候选类型（内网、mDNS、公网映射等），不写本机地址。
- `out/` 已被 Git 忽略，里面是每次运行的日志与摘要，可随时删除。

## 验证程序按什么流程走

和[设计](../../docs/system/runtime-screen-stream-design.md)一致，只有点对点，没有中转：

1. 观看页发起请求，Runtime 先建立一条**不含画面**的连接。
2. Runtime 比对观看端的地址，得出三种结果之一：确认同机、确认非同机、无法确认。
3. 只有「确认非同机」才开始采集；另外两种直接结束，全程不传出画面。
4. 传画面期间 Runtime 必须持续从服务器得到确认（画面租约）；连续收不到就自己关闭采集。
5. 30 秒还没连上就结束会话，同样不采集。

第 2 步的规则在 `verdict.js`，只用三样明文可见的证据：网页内网候选解析出的地址、网页的公网映射地址、Runtime 自己的公网映射地址。连接统计不参与判定，浏览器也不提供对端的实际地址（规则 5、6 只用统计里的对端端口，见下文）。`node verdict.test.cjs` 用构造的地址跑 21 种组合。摘要里 `sameMachine` 的 `rule` 是判定用的规则编号，`evidence` 是当时手里的证据（只有计数和是否不同，不含地址）。

| 规则 | 条件 | 结论 |
| --- | --- | --- |
| 1 | 网页任一候选的地址是本机网卡地址 | 确认同机 |
| 2 | 网页的内网候选解析出的地址不是本机网卡地址 | 确认非同机，同网段 |
| 3 | 网页的公网映射地址与本机的可以比较，且有不在本机里的 | 确认非同机，另一个网络 |
| 4 | 以上都不满足 | 无法确认，不采集 |

规则 5、6 不在 `verdict.js` 里，由采集窗口和主进程配合实现，用 `POC_PEER_PORT_CHECK=1` 开启：连接已建立时，成功候选对里对端的端口全是本机套接字是同机，有不属于本机的是另一台电脑。跨网段时对方的名字解析不出来，靠它们判定。

## 运行

需要 Electron 43.1.0（与 Runtime 产品同版本）。本机可直接用任意一个已安装依赖的工作区里那一份，不需要重新下载：

```bash
ELECTRON=clients/agent-desktop/node_modules/electron/dist/electron.exe
cd spikes/runtime-screen-stream-poc
POC_MODE=pair POC_CODEC=h264 POC_SAME_MACHINE=skip POC_DURATION_SEC=60 "$ELECTRON" .
node summarize.cjs out          # 打印 out/ 里每次运行的摘要
```

三点提醒：

- 未签名的测试程序第一次建立 WebRTC 连接时，Windows 会弹出防火墙提示。点「取消」不影响单机验证，但 Windows 会为这个程序留下两条入站阻止规则；点「允许访问」则是放行规则。两种都可以在「高级安全 Windows Defender 防火墙」的入站规则里删除（需要管理员权限），验证结束后建议清掉。跨网段验证必须放行：点「允许访问」，或事先给 `electron.exe` 加一条放行入站 UDP 的规则。
- 默认端口 8791、8792 被占用时，用 `POC_HTTP_PORT`、`POC_LAN_PORT` 换端口，并把 `--port=`、`--lanport=` 改成一致的值。
- 只有设了 `POC_MOTION=1` 才会在屏幕右下角显示一个 480×270 的动态窗口，用来让画面有真实变化，结束时自动关闭。

## 两种信令方式

| 方式 | 怎么做 | 用于 |
| --- | --- | --- |
| 本机信令（默认） | 验证程序自己提供观看页和信令接口，监听 `POC_HTTP_PORT` | 同一台电脑；同网段的第二台电脑直接打开它提供的观看页 |
| 信令中转（设 `POC_SIGNAL_URL`） | 验证程序不监听任何端口，只主动连接 `signal-relay.cjs` 取送信令。中转脚本放到两端都能访问的机器上运行 | 跨网络；量服务器经手的字节数。这也是产品里的做法 |

`pair` 模式是两个隐藏窗口互连的对照组，用来量帧率、编码器和占用；接收窗口和发送窗口在同一台电脑上，要加 `POC_SAME_MACHINE=skip` 才会采集。它也可以用来在本机模拟采集负载，再并行运行别的程序，看采集会不会拖慢它们、编码器会不会受限（2026-11-25 的并行实测就是这么做的，结果见调研报告 6.1 节）。摘要里 `tx.cpuLimitedSamples` 是传画面期间编码器因 CPU 受限的采样次数，`node summarize.cjs out` 会打印。

## 单机上可以演练的场景

终端 1 运行验证程序，终端 2 运行 `browser-test.cjs`（它会启动本机已安装的 Chrome 或 Edge：独立临时配置目录、窗口移到屏幕外，做完自动关闭）。下面的「加」都是在同一条命令上追加环境变量或参数。

```bash
# 终端 1
POC_MODE=serve POC_HTTP_HOST=127.0.0.1 POC_CODEC=h264 POC_DURATION_SEC=60 "$ELECTRON" .
# 终端 2
node browser-test.cjs --browser=chrome --lanport=0
```

| 场景 | 终端 1 追加 | 终端 2 追加 | 预期结果 |
| --- | --- | --- | --- |
| 同机判定 | 不用加 | 不用加 | 规则 1：观看页收到结束原因 `same-machine`，摘要里 `capture.started` 为 `false` |
| 正常观看（跳过判定） | `POC_SAME_MACHINE=skip` | `--hold=30000` 可在首帧后停留 30 秒 | 收到首帧，H.264 720p 约 15 帧，`relayInPath` 为 `false` |
| 模拟另一台电脑 | 再加 `POC_LOCAL_LIST=none` | 不用加 | 规则 2：判定为确认非同机，开始采集。这是开关模拟，只验证判定之后的动作 |
| 模拟解析不出名字 | 用 `POC_RESOLVE=off` 代替 `POC_LOCAL_LIST=none` | 不用加 | 规则 4：结束原因 `machine-check-unknown`，不采集 |
| 没有 STUN | `POC_STUN=`（设为空），可再配 `POC_RESOLVE=off` | 不用加 | 本机没有公网映射地址：名字解析不出时不放行；解析得出本机地址时仍判同机 |
| 直连失败 | `POC_SAME_MACHINE=skip POC_CANDIDATES=none`，时长不短于 70 秒 | `--cand=none --wait=45000` | 约 30 秒后结束原因 `connect-failed`，全程不采集 |
| 画面租约 | `POC_SAME_MACHINE=skip POC_LEASE_SEC=20 POC_LEASE_OUT_AT_SEC=10`，时长不短于 70 秒 | `--hold=45000` | 采集在最后一次联系服务器后约 20 秒关闭，原因 `lease-expired` |

改用信令中转：另开一个终端运行 `node signal-relay.cjs`（默认 8799），终端 1 把 `POC_HTTP_HOST=…` 换成 `POC_SIGNAL_URL=http://127.0.0.1:8799`，终端 2 加 `--port=8799`。中转进程每 10 秒打印一行 `RELAY_STATS`（请求数和字节数），传画面期间每 10 秒只有约 0.5 KB。

`browser-test.cjs` 的参数一律写成 `--key=value`。观看页左上角显示连接状态（`pc`）、两端选中的地址类型（`path`）、路径里有没有中转（`relay`）、帧数与帧率、结束原因（`end`）。最下面两行是连接诊断：ICE 状态、候选收集状态、信令状态；本机候选按类型的计数；对端候选加进去几个、早到几个、失败几个及原因。对端候选可能先于连接请求到达（信令经服务器转交，先后没有保证），页面会先把它们缓存到连接请求处理完。

## 三组验证怎么做

单机实测不能当作跨网络的验收。调研报告 6.2 节列了每组的通过标准，做法如下。每组都记下 Runtime 摘要里 `sameMachine.rule` 和 `evidence`：规则 2 是同网段的放行证据，规则 3 是跨网络的放行证据，规则 6 是名字解析不出时（跨网段实测用的）的放行证据；规则 1、5 判同机，规则 4 不放行。

**一、同网段两台电脑。** 电脑 A 运行验证程序，电脑 B 用浏览器打开 `http://<A 的内网地址>:8791/viewer.html`。要同时量服务器字节，就在其中一台运行 `signal-relay.cjs`，A 改用 `POC_SIGNAL_URL` 启动，B 打开 `http://<中转机地址>:8799/viewer.html`。记录四件事：A 是否弹出防火墙提示；A 的摘要里 `sameMachine.verdict` 是不是 `other`、`capture.started` 是不是在判定之后才为 `true`；B 页面上的 `path` 是不是两端的内网地址、`relay` 是不是 `false`；中转的字节数是否不随码率增长。

**二、真实跨网络。** 需要一个两端都能访问的信令入口和一个自建 STUN。Runtime 一侧用 `POC_SIGNAL_URL=<公网中转地址> POC_STUN=<自建 STUN>` 启动，观看端在另一个网络打开中转地址的 `viewer.html`。办公网、家庭宽带、手机热点两两组合，逐组记录成功还是失败。成功时 `path` 里应有公网映射地址且 `relay` 为 `false`；失败时约 30 秒后出现 `connect-failed`，A 上不采集。

**三、直连失败。** 在两台电脑上按上表「直连失败」一行做一遍：A 加 `POC_CANDIDATES=none`，B 打开 `viewer.html?cand=none`。预期 B 提示结束原因 `connect-failed`，A 全程不采集，中转上新会话开始时旧信令被清空。

**排查某个网络能不能直连。** 在其中一端运行 `node net-check.cjs`，另一端用浏览器打开 `http://<本机内网地址>:8797/`，约 40 秒后依次给出四项结果：内网直达（UDP 能否往返）、公网出口（能否用 UDP 访问公网、与检测服务器是否同一个出口）、外网回环（同一个出口时能否经公网地址绕回，两边同时打洞）、出口 NAT 类型（锥形或对称型）。只发 STUN 探测包，不涉及画面，地址都打码。应答 socket 绑定本机的内网地址：绑定任意地址时，开着代理虚拟网卡的电脑会把回包送进代理，源端口被改写，对方认不出来，看上去像「UDP 只通一个方向」（2026-11-27 踩过）。出口 NAT 类型要在没有装代理的电脑上测，代理本身也会让映射端口随目的地变化。2026-11-27 在一个办公网上（两台电脑在不同 VLAN）测的结果：内网直达通，外网回环不通，出口是对称型，见调研报告 4.3。

**跨网段的内网直连（2026-11-27 实测通过）。** 不要求两台电脑在同一个网段。电脑 A 运行验证程序（经信令中转），并且 A 的防火墙放行验证程序（`electron.exe`）的入站 UDP；没有放行时要在 Windows 的防火墙提示里点「允许访问」。电脑 B 在另一个网段用浏览器打开观看页。加 `POC_PEER_PORT_CHECK=1` 开启名字解析不出时的同机判定：连接建立后看对端连入用的端口是不是本机的套接字。B 的 mDNS 名字在 A 上解析不出，不加这个开关时只能落到「无法确认」。数据见调研报告 6.1。

另外两项也要在有条件时做：

- 公网页面连接内网地址时是否弹「本地网络访问」权限：把 `signal-relay.cjs` 放到公网 HTTPS 入口后面，用浏览器打开后运行 `node cand-probe.cjs --port=<端口> --lanport=0`，对比本地候选有没有被拦。
- 锁屏、UAC 弹窗、远程桌面断开时的画面；多显示器；其他缩放比例：需要用户在场时触发一次。

## 环境变量

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `POC_MODE` | `pair` | `pair`（两个隐藏窗口互连）或 `serve`（等待观看页发起） |
| `POC_SIGNAL_URL` | 空 | 信令中转地址。设置后 `serve` 模式不监听端口，只主动连接 `signal-relay.cjs` |
| `POC_RELAY_POLL_SEC` | 5 | 中转模式下没有会话时的取信令间隔；会话开始后 0.5 秒一次，开始传画面后 5 秒一次 |
| `POC_DURATION_SEC` | 60 | `pair` 模式是传画面时长；`serve` 模式是程序运行时长 |
| `POC_BASELINE_SEC` | 20 | `pair` 模式开始采集前的空闲基线时长（秒） |
| `POC_CODEC` | `default` | `default`（Chromium 默认，VP8 软编码）或 `h264`（把 H.264 排到首位） |
| `POC_THROTTLE` | `off` | `off` 关闭后台节流；`default` 保持 Electron 默认 |
| `POC_PERMISSION` | `product` | `product`：只放行采集窗口会话的 `media` 请求并自动指定主显示器；`default`：不设置任何处理器 |
| `POC_ALLOW` | `media` | `product` 模式下放行的权限名 |
| `POC_CANDIDATES` | `lan` | 只把哪一类本机候选交给对端：`loopback`、`lan`、`all`、`none`。Chromium 不采集回环候选，实际用 `lan`；`none` 用来演练直连失败 |
| `POC_ORDER` | `connect-first` | `connect-first`：先建连并判定，再采集（设计的做法）；`capture-first`：先采集再建连（对照） |
| `POC_ATTACH` | `replaceTrack` | `replaceTrack`：画面接到预先建好的通道上；`addTrack`：对照 |
| `POC_SAME_MACHINE` | `check` | `check`：判定同机，只有确认非同机才采集；`skip`：跳过判定，只用于在单机上演练采集通路 |
| `POC_LOCAL_LIST` | `all` | `none` 时把本机地址表清空，观看端会被判成「另一台电脑」 |
| `POC_RESOLVE` | `on` | `off` 时不解析观看端的 mDNS 名字，用来演练「无法确认」 |
| `POC_STUN` | `stun.miwifi.com:3478` | ICE 服务器，逗号分隔；设为空则不配置。跨网络验证换成自建 STUN |
| `POC_LEASE_SEC` | 30 | 画面租约：多少秒收不到续期就关闭采集；0 表示关闭租约 |
| `POC_LEASE_OUT_AT_SEC` | 0 | 开始采集多少秒后不再联系服务器（模拟 Runtime 连不上服务器）；0 表示不模拟 |
| `POC_CONNECT_TIMEOUT_SEC` | 30 | 建连超时：到点还没连上就结束会话，全程不采集；0 表示不超时 |
| `POC_PEER_PORT_CHECK` | `0` | `1` 时，地址候选的规则没有判成同机，就在连接建立后按对端端口判定：成功候选对里对端的端口全是本机套接字是同机，有不属于本机的是另一台电脑，查不到保持原结论 |
| `POC_FPS` | 15 | 采集帧率上限 |
| `POC_SAMPLE_SEC` | 5 | 采样间隔 |
| `POC_MOTION` | `0` | `1` 时在屏幕右下角显示动态窗口 |
| `POC_HTTP_PORT` / `POC_HTTP_HOST` | `8791` / `0.0.0.0` | 本机信令模式下观看页与信令接口的端口与监听地址 |

`signal-relay.cjs` 用 `POC_RELAY_PORT`（默认 8799）和 `POC_STUN`。

## 文件

| 文件 | 作用 |
| --- | --- |
| `main.js` | 主进程：建窗口、权限设置、采样、汇总；本机信令模式下提供信令接口，中转模式下主动连接中转 |
| `sender.html` / `sender.js` | 充当 Runtime 采集窗口：先建连、判定同机、确认后采集、画面租约、建连超时；可重复开始以服务多个观看会话 |
| `receiver.html` / `receiver.js` | `pair` 模式的接收端 |
| `viewer.html` | 真实浏览器里的观看端（HTTP 轮询信令） |
| `signal-relay.cjs` | 信令中转：只保存和转交握手消息并统计字节数，放到两端都能访问的机器上运行 |
| `net-check.cjs` | 网络条件检测：一端运行，另一端用浏览器打开检测页，给出内网直达、公网出口、外网回环、出口 NAT 类型四项结果 |
| `p4.html` | 权限状态测试页：读取「本地网络访问」相关权限的状态，不含画面 |
| `motion.html` / `motion.js` | 动态内容窗口 |
| `stats.js` | 共用的统计与候选分类 |
| `verdict.js` / `verdict.test.cjs` | 同机判定的规则 1～4，以及用构造的地址跑的单测；规则 5、6（按对端端口）在 `sender.js` 和 `main.js` 里 |
| `preload.cjs` | 页面预加载脚本，只暴露收发消息和上报 |
| `lan-server.cjs` | 让测试页能从内网地址打开的转发服务，单机验证浏览器权限时用 |
| `browser-test.cjs` | 驱动本机已安装的 Chrome 或 Edge 观看，输出时延、路径和结束原因 |
| `cand-probe.cjs` | 只统计本地候选，判断权限是否拦住了内网候选 |
| `summarize.cjs` | 打印 `out/` 里的运行摘要 |

## 已验证的范围

2026-11-24 在一台电脑上完成：隐藏窗口采集与 10 分钟稳定性、编码与占用对照、权限与采集方式对照、真实 Chrome 157、158 与 Edge 156 的观看互通、同机判定（规则 1 实测，规则 4 在没有 STUN 时实测，规则 2 只用开关模拟，规则 3 只有单测）、先建连后采集、画面租约到期自动关闭、没有路径时 30 秒建连超时、Runtime 只主动连出、浏览器权限、防火墙提示、系统解析器解析本机浏览器的 mDNS 名字。**两台电脑之间、跨网络、经 Runtime 的真实任务并行、锁屏、公网 HTTPS 来源**没有验证；2026-11-25 用 `pair` 模式加已安装的微信插件做了一次单机模拟并行实测，结论以调研报告 6.1 节为准。2026-11-27 用第二台电脑（不同 VLAN）实测：跨网段的内网直连通过，连续 29 分钟；名字解析不出时按对端端口判定同机，本机对照 5 次全部判为同机；同一出口不支持 NAT 回环，出口是对称型 NAT（调研报告 6.1）。同网段和真实跨网络仍未验证；跨网段时用的是验证程序加临时防火墙规则，产品安装包的放行方式待 Phase 2 验证。所需条件见调研报告 6.2 节和第 7 节。
