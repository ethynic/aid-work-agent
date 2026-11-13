# 测试套件提速专项（Runtime/CLI/BOSS TS 三包）

> 任务：治理 TS 客户端测试套件过慢与基线红/摇摆问题。工作区 `C:\repos\runtime-optimization`（分支 runtime-optimization，基点 d4a60fa7）。本任务不提交不 push，CR 通过后由 CR 智能体提交。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 基线测量（整改前数字） | ✅ 完成（2026-11-13） | 见 §2；实测机器 28 核/Node 24.13，此前「boss ~10 分钟」观感主要来自 2 个挂死文件把 npm test 拖到永不结束 |
| Phase 2 | 挂死根因修复（mcp-conformance/mcp-server） | ✅ 完成（2026-11-13） | 见 §4.1；两文件 6.8s/6.3s 全绿，weixin-cli 同套件受益验证 2/2 |
| Phase 3 | 长期红/摇摆基线逐个处置 | ✅ 完成（2026-11-13） | 7 项全部闭环（5 修 2 摇摆根因修复），见 §4.2 |
| Phase 4 | 进程/时钟分级（sleep 注入/串行桶） | ✅ 完成（2026-11-13，部分余项登记） | operations-result 24s→0.17s；wecom Provider 文件入串行桶；余项见 §4.3 |
| Phase 5 | 测试脚本去强制重建（test / test:full 拆分） | ✅ 完成（2026-11-13） | ensure-build mtime 门禁 + test:full，三包验证 stale→build / fresh→skip |
| Phase 6 | 并行度检查 | ✅ 完成（2026-11-13） | 默认文件级并发未被人为限制；仅存设计使然的串行桶，见 §4.5 |
| Phase 7 | 前后对照表与验收 | ✅ 完成（2026-11-13） | 见 §5；四包全绿（boss 1132/0、runtime 372/0、host-core 131/0、desktop 111/0） |

## 1. 目标数字（达成情况）

| 套件 | 基线 | 目标 | 结果 |
|------|------|------|------|
| boss 全量（npm test） | 挂死永不结束（含 2 红基线） | 稳定跑完+全绿 | ✅ 14.1s，1132 tests（1127 pass + 5 既有环境 skip）/ 0 fail |
| runtime 全量（不含 build） | 2m38s，3 fail | 0 fail | ✅ 2m39s/2m41s 两次复跑 0 fail（1 环境跳过保留） |
| host-core build+test | 13.6s，1 fail + 摇摆 | 0 fail 稳定 | ✅ 连续 4 次全量 0 fail |
| agent-desktop test | 27.6s，0 fail | 保持 | ✅ 24.6s，111 pass / 0 fail |
| 挂死文件 | 前 2 | 后 0 | ✅ 后 0（稳定跑完） |
| 基线红/摇摆 | 前 8+（确定性 5 + 摇摆 2 + 竞态 1） | 后 0 | ✅ 全部闭环（见 §4.2） |
| npm test 强制全量重建 | 是 | 否 | ✅ 产物新鲜即跳过（test:full 兜底） |

## 2. 基线测量（整改前，d4a60fa7 工作区，2026-11-13 实测）

环境：Windows 11 x64、Node v24.13.0、28 逻辑核。计时方式：`node --test --test-reporter=<自定义聚合 reporter>`（test:summary 事件按文件聚合顶层时长），官方脚本墙钟时间另计。

### 2.1 boss-resume-assistant

- `build:main`（rm -rf dist 后冷构建）：build:native 1.3s + tsc 3.7s ≈ **5s**（npm 包装开销另计 ~1s）。
- bounded 全量（89 文件，排除 mcp-conformance/mcp-server，AID_BOSS_AUTO_CHROME=0）：**wall 25.5s**，逐文件时长合计 131.8s，1120 pass / 2 fail（dom-snapshot-owner-scroll ×2，长期红）。
- 逐文件耗时 Top10（基线）：

| 文件 | 时长 ms |
|------|---------|
| operations-result.test.js | 23991 |
| browser-page-recovery.test.js | 13662 |
| boss-open-chat.test.js | 9372 |
| operations-progress-cancel.test.js | 9360 |
| powershell-path-independent.test.js | 9215 |
| candidate-selection.test.js | 7574 |
| resume-obstruction-independent.test.js | 7448 |
| widget-hidden-recovery.test.js | 5455 |
| retained-read-cleanup.test.js | 4935 |
| powershell-path.test.js | 3977 |

- `npm run test` 全量含 mcp-conformance/mcp-server：**两文件挂死，永不结束**（单文件 90s 强杀仅完成 5/8 用例）——这是「boss 跑一次 ~10 分钟」观感的来源：bounded 靠手动排除，不排除则挂死。

### 2.2 agent-tool-runtime

- `build:main` 分步（冷构建）：build:core（host-core tsc）1.5s + build:native 1.2s + 主 tsc 5.1s + build-process-supervisor 0.7s ≈ **8.5s**。
- 全量测试（官方 run-tests.mjs，不含 build）：**wall 2m38.2s**；serial 批（pair/hostIndependent）22 tests 67.4s + 并行批 350 tests 90.7s；3 fail + 1 skip：
  - `processCleanup.test.js:33` SIGINT 协作中止 → 云端无终态（长期红）
  - `v2WritePath.test.js` shutdown 中止终态未回传（长期红）+ `:260` 断网回执摇摆（全量红/solo 绿）
- 逐文件 Top：v2WritePath ~8s、pair（serial）~60s 等；63 文件。

### 2.3 shared/local-tool-host-core

- build（冷）：**2.5s**；test：**wall 11.1s**（逐文件合计 18.2s），130 pass / 1 fail（wecomSendStatus「前提未进 instructions 前 512 字符」），wecomGroupPoll 8.4s 当时绿（后证实摇摆）。

### 2.4 agent-desktop

- `npm run test`：**wall 27.6s**（build ~24.6s 含 frontend build:renderer；测试本体仅 2.9s），111 pass / 0 fail。

### 2.5 挂死文件根因分析（mcp-conformance / mcp-server）

**现象**：两文件在「并发单飞 BUSY + 取消后锁释放」用例中永不结束；node --test 文件级等待无超时 → 整套挂死。

**根因（netstat/socket 取证，见取证脚本时间线）**：
1. 用例用「悬挂 TCP 服务器」（`net.createServer` 只挂 `sock.on('error')`，从不 end/destroy 接受的 socket）让 CDP connect 卡住；
2. 取消后的 CDP connect 对连接做半关闭（发 FIN，socket 进入 FIN_WAIT_2/CLOSE_WAIT 对），**悬挂服务器侧的应用层 socket 停在 CLOSE_WAIT 永不关闭**；
3. 用例 finally 里 `await new Promise(r => hang.close(() => r()))`——Node `net.Server.close(cb)` 的回调要等**全部连接真正结束**才触发，CLOSE_WAIT 连接不结束 → 回调永不触发 → 测试文件挂死。
4. `client.close()` 本身无责：SDK StdioClientTransport.close 已带 stdin.end → 2s → SIGTERM → SIGKILL 升级；boss CLI stdin 关闭即 `process.exit(0)`，无孤儿进程。

**取证**：`netstat -ano` 显示 hangServer 进程名下两条 CLOSE_WAIT 连接长期驻留；探针脚本在 `hang.close()` 处顶层 await 永不 resolve。

## 3. 改动文件清单

| # | 文件 | 改动 |
|---|------|------|
| 1 | clients/shared/mcp-conformance/suite.mjs | 挂死修复：悬挂服务器追踪 socket，close 后 destroy（boss/weixin-cli 共享套件） |
| 2 | clients/boss-resume-assistant/tests/mcp-server.test.ts | 同上（本文件内的悬挂服务器） |
| 3 | clients/shared/local-tool-host-core/src/legacy/resultOutbox.ts | shutdown 终态回传修复：已中止信号不再中止回传本体（attemptSignal） |
| 4 | clients/wecom-cli/src/mcp/server.ts | INSTRUCTIONS 关键前提块置顶（标准 §5.4 前 512 字符合规），推荐入口与目录原样保留 |
| 5 | clients/boss-resume-assistant/tests/dom-snapshot-owner-scroll.test.ts | fixture 重建：三层结构化快照（真实卡片结构+CANVAS），几何断言数字不变 |
| 6 | clients/agent-tool-runtime/tests/v2WritePath.test.ts | 两处等待条件并入 outbox 条目删除（消除 ACK→remove 竞态窗口，断言保留） |
| 7 | clients/boss-resume-assistant/src/main/operations/bossGreet.ts | 工厂加可选 `opts.sleep` 透传 GreetExecutor（缺省行为不变） |
| 8 | clients/boss-resume-assistant/src/main/operations/bossAcceptResume.ts | 同上（ResumeConsentExecutor） |
| 9 | clients/boss-resume-assistant/src/main/operations/bossOpenChat.ts | 同上（ChatOpenExecutor） |
| 10 | tests：operations-result / operations-progress-cancel / boss-open-chat（boss） | 注入 `noSleep`（断言不含时序语义，分级不删断言） |
| 11 | clients/*/scripts/ensure-build.mjs（boss/runtime/host-core，新） | 产物 mtime 新鲜度门禁，过旧才执行 build 命令 |
| 12 | clients/shared/local-tool-host-core/scripts/run-tests.mjs（新） | wecom Provider 两文件串行桶 + 其余并行（照搬 runtime 范式） |
| 13 | package.json ×3（boss/runtime/host-core） | `test` = ensure-build + run-tests；新增 `test:full`（强制重建） |

## 4. 整改记录

### 4.1 挂死根因修复（红→绿证明）

- 修复前：`node --test dist/tests/mcp-server.test.js` solo 90s 强杀只完成 5/8；mcp-conformance 同样挂死。
- 修法：悬挂服务器 `net.createServer` 改为登记每个接受的 socket，`close()` 注册回调后立即对残余 socket `destroy()`——语义不变（不再监听 + 连接清理），`close` 回调立即完成。
- 修复后：mcp-server 6.8s 8/8、mcp-conformance 6.3s 2/2；共享套件消费方 weixin-cli conformance 2/2 回归通过；boss 全量（91 文件）两连跑 0 fail。

### 4.2 长期红/摇摆基线处置登记

| # | 测试 | 现象 | 根因 | 处置 | 红绿证据 |
|---|------|------|------|------|---------|
| 1 | boss dom-snapshot-owner-scroll ×2 | locateCards 返回 []、readBatch 0 份 | fixture 多层过期：① locateCards 已切 loadedCandidates 结构配对，合成快照缺卡片 DOM 结构（card-list-wrap/list-body/card-item/name/btn-greet）；② readBatch 详情态 withDetailControls 需要真 CANVAS 节点 | 重建三层结构化 fixture（几何与断言数字原样保留；唯一机械调整：test 4 新增 owner 节点索引改为取当前节点数，避免与新增卡片节点冲突） | 前：2 用例红；后：7/7 绿 + typecheck 0 |
| 2 | runtime v2WritePath shutdown 中止 | `shutdown 终态必须回传 operation-result` 落空，solo ×3 全红（确定性） | 产品缺陷：shutdown() 先 abort runnerShutdown，协作式中止产生的 CANCELLED 终态 flush 携带**已中止信号**→ ApiClient 首个尝试必然 AbortLoopError → backoffSleep 立即放弃 → 终态本次运行永远送不出（只能等下次启动重投），与「stop() 等终态回传完成后才退出」契约冲突（P1 时 api.operationResult 不带信号，后来加信号时引入） | 产品最小修复（resultOutbox.ts）：信号已中止时回传本体改无信号尝试（ApiClient 自身 15s 超时兜底，stop 仍有界）；信号未中止时维持原语义；信号继续管退避重试。已核对既有 transport-recovery/outbox-poll-recovery 中断语义不受影响 | 前 solo ×3 红；后 solo ×3 绿 + 全量 ×2 绿 |
| 3 | runtime processCleanup SIGINT | `invocation 应有终态，实际 running` | 同 #2（同一 flush 缺陷的另一断言面） | 同 #2 | 前红；后 solo ×2 + 全量 ×2 绿 |
| 4 | host-core wecomSendStatus | 前提断言失败 | wecom-cli INSTRUCTIONS 新增「推荐业务入口」前导 ~530 字符，把「仅 Windows/UI操作/禁止重发…」等关键前提挤出前 512 字符，违反 first-party Provider 标准 §5.4 | 产品最小修复：前提块置顶（内容零增删，仅排序；前提 ~150 字符 + 推荐入口仍在 512 内） | 修复前探针实证 instructions.len=1581 且前提 >512；修复后 solo ×3 + 全套 ×4 绿 |
| 5 | runtime v2WritePath 断网回执（摇摆） | 全量红/solo 绿：`重投 ACK 后条目删除` 时条目仍在 | 竞态：云端 state 翻转 succeeded 与回传方 `outbox.remove`（先 recordOperationAck 落账再删）之间有窗口；并行负载放大 | 测试侧消竞态：waitFor 谓词并入「条目已删除」，断言原样保留（R21 同型两处一并处理） | 前全量红；后全量 ×2 绿 |
| 6 | host-core wecomGroupPoll（摇摆） | 全量偶发红（8.4s 绿→1.3s 快败）；solo ×9、轻载全套 ×4 绿 | 复现成功：8 个并发实例 7 败，报「另一个 aid-wecom 进程正在操作企业微信（跨进程互斥）」——wecom-cli Provider 用会话级跨进程互斥锁，wecomGroupPoll 与 wecomSendStatus 两个拉真 Provider 的文件并行跑互踩 | 串行桶（不删断言、不移除覆盖）：host-core 新增 run-tests.mjs，`{wecomGroupPoll, wecomSendStatus}` 以 `--test-concurrency=1` 串行，其余照旧并行（runtime 已有同款先例） | 复现：并发 8 败 7；修复后全套 ×4 连绿 + npm test ×3 绿 |
| 7 | （顺带）wecom-cli localdb-fingerprint 1 fail | `checkDbFingerprint 魔数不匹配` true!==false，solo 确定性红 | 与本任务无关（我方 wecom-cli diff 仅 server.ts 指令排序；fingerprint 链路零改动） | 不处置（wecom-cli 属本轮范围外的第四包），登记移交 | solo 复现 14 pass/1 fail |

### 4.3 进程/时钟分级登记

| # | 对象 | 处置 | 说明 |
|---|------|------|------|
| 1 | boss operations-result（24.0s，40 用例） | executor sleep 注入 | 慢点全在 greet/accept 的风控节奏 sleep（GreetExecutor/ResumeConsentExecutor 早已支持 `deps.sleep`），操作工厂未透传。三个工厂加可选 `opts.sleep`（缺省 setTimeout 不变，生产节奏零变化），测试注入空实现；断言不含时序语义 | 
| 2 | boss operations-progress-cancel（9.4s）、boss-open-chat（9.4s） | 同模式 | 24s→0.17s / 9.4s→<1s |
| 3 | host-core wecomGroupPoll/wecomSendStatus | 串行桶 | 见 §4.2 #6；两文件是「必须真实进程/真 Provider」级测试，保留在默认套件（快速），仅串行 |
| 4 | runtime pair/hostIndependent serial 批 | 维持现状 | pair 拉真实 Host 组合并依赖 OS 用户级 pairing 状态，串行是既有设计（注释明示），不动 |
| 5 | （余项，未做）browser-page-recovery 13.8s、powershell-path×2 合计 13.5s（真 PowerShell spawn）、candidate-selection 7.6s、resume-obstruction-independent 7.6s | 留档 | 可沿同一 `opts.sleep` 注入模式继续收敛（涉及 FilterSetter/PageNavigator 等约 10 个工厂）；powershell-path 两文件可按「真 PowerShell → integration 桶」处理。本轮投入产出截止于 headling 项，登记为后续 |

### 4.4 测试脚本去强制重建

- 新增 `scripts/ensure-build.mjs`（boss/runtime/host-core 三包同款）：比较 `src/**`+`tests/**` 最大 mtime 与 `dist/**` 最大 mtime（外加 `--pairs` 依赖包源码/产物对，如 host-core、win32-native-input），产物缺失或源码较新才执行构建命令，否则跳过。
- `package.json`：`test` = ensure-build 门禁 + 收集器；新增 `test:full` = 无条件 `build:main` + 收集器（发布前/CR 兜底）。
- 验证：改动源码后首次 `npm test` 触发重建、随后立即复跑打印「产物新鲜，跳过重建」；test:full 保留原语义。

### 4.5 并行度检查

- node --test 默认文件级并发 = `os.availableParallelism() - 1`（本机 27），boss/runtime 收集器未传 `--test-concurrency`（并行批）→ 无人为上限；实测 boss 基线逐文件合计 131.8s vs wall 25.5s，并行生效。
- 唯二的串行点都是跨文件真实资源约束，非并发缺陷：runtime `pair/hostIndependent`（OS 用户级 pairing 现场污染）、host-core `wecomGroupPoll/wecomSendStatus`（wecom-cli 会话级跨进程互斥锁，本次实证）。
- 结论：无需也无法安全再开更大并发；瓶颈为最长文件（boss 现 browser-page-recovery 13.8s，见 §4.3 余项）。

## 5. 前后对照表（交付物）

| 指标 | 前（d4a60fa7 基线） | 后（本工作区） |
|------|--------------------|----------------|
| boss `npm run test`（全量 91 文件） | 挂死永不结束；bounded（手排 2 文件）25.5s、2 fail | **14.1s，1132 tests（1127 pass + 5 既有环境 skip）/ 0 fail**（含原挂死 2 文件 7.5s/7.1s） |
| boss 逐文件合计 | 131.8s（Top1 operations-result 24.0s） | 105.0s（Top1 browser-page-recovery 13.8s；operations-result 0.17s） |
| runtime 全量测试 | 2m38s，3 fail + 1 skip | **2m39s / 2m41s 两次复跑，0 fail** + 1 skip（环境跳过保留） |
| runtime `build:main`（冷） | 8.5s | 不变（构建本身不慢）；`npm test` 产物新鲜时跳过重建 |
| host-core build+test | 13.6s，1 fail + 摇摆 | **~11s，131 tests 0 fail**（连续 4 次全量 + npm test ×3） |
| agent-desktop test | 27.6s，111 pass | 24.6s，111 pass / 0 fail |
| weixin-cli mcp-conformance（共享套件消费方） | 同样挂死风险 | 2/2 绿（回归验证） |
| 挂死文件 | 2 | 0 |
| 确定性基线红 | 5（dom-snapshot×2、v2WritePath shutdown、processCleanup SIGINT、wecomSendStatus） | 0 |
| 摇摆基线 | 3（断网回执、wecomGroupPoll、+ 顺带发现 wecom-cli localdb-fingerprint 确定性红属范围外） | 0（断网回执竞态消除；wecomGroupPoll 串行桶；localdb-fingerprint 登记移交） |
| 每次跑测试强制重建 | 是（boss/runtime/host-core） | 否（新鲜即跳过；test:full 兜底） |

## 6. 风险与遗留

1. **产品行为变更共 2 处**（均最小化并已验证）：
   - resultOutbox 回传本体在「信号已中止」场景不再被信号打断——shutdown 时 CANCELLED 终态可当场送达（原缺陷：永远只能等下次启动重投）。对已交付旧客户端无影响：旧客户端二进制内的旧逻辑不变，服务端零改动；新 Runtime 行为严格更优（回执更早落账），wire schema 未动。
   - wecom-cli INSTRUCTIONS 前提块置顶：内容零增删仅排序，对 Agent 可见性更符合标准 §5.4；工具目录与推荐入口原样保留。
2. **wecom-cli localdb-fingerprint 确定性红**（范围外）：魔数不匹配用例期望 false 实得 true，与本任务改动无交集（diff 仅 server.ts 指令排序），已登记移交第四包治理。
3. **提速余项**：browser-page-recovery（13.8s）等可沿 `opts.sleep` 注入模式继续收敛（§4.3 #5）；powershell-path 两文件可移 integration 桶。均为可选后续，不影响本轮验收。
4. **ensure-build 的保守口径**：极端场景（新增源文件后又有旧产物被触碰）可能漏判，`test:full` 兜底；日常改动源码必然触发重建，不会跑旧产物。
5. **CR 复验建议命令**：`npm run test`（boss，预期 ~15s 全绿）、`node scripts/run-tests.mjs`（runtime，预期 ~2m40s 全绿）、`npm run test`（host-core，预期 ~11s 全绿）、`node --test dist/tests/mcp-server.test.js dist/tests/mcp-conformance.test.js`（boss，预期 ~14s 全绿不挂死）。
