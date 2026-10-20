# 招聘链路单点错误治理与 Win32 输入性能修复

来源：[招聘筛选耗时与恢复阻塞复盘（2026-10-20）](../incidents/recruiting-runtime-filter-timeout-and-resume-blocked-20261020.md)剩余未排期项 + 2026-10-20 会话代码审计。恢复交互出口（登录/设备分类、Worker 切换对账、同会话结束等待、unknown 分层核验）由 [恢复计划](plan-boss-resume-next-recovery-fix.md) Phase 6～10 承担，本计划不重复；本计划处理「一次报错杀死整任务」的**触发面**与**输入性能根因**。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 存活热点分段与替换前基线固定 | 📋 待开发 | |
| Phase 2 | model_phase 供应商结果分类与单份失败收敛 | 📋 待开发 | |
| Phase 3 | 恢复消费已取回简历成果 | 📋 待开发 | |
| Phase 4 | 180 秒持久截止语义拆除 | 📋 待开发 | |
| Phase 5 | Win32 输入链路 Node 原生化 | 📋 待开发 | |
| Phase 6 | 隔离授权环境 A/B 复现与性能目标 | 📋 待开发 | |
| Phase 7 | 进度语义分离与可观测性缺口 | 📋 待开发 | |
| Phase 8 | 独立测试与 CodeReview | 📋 待开发 | |

## 事实基础（2026-10-20 审计，开发前复核）

**错误升级链（d470 GLM 1210 案例）**：

1. `src/services/agent_runner/local_owner.py:262-266`：`model_phase()` 把 `invoke()` 的**所有**异常（除 `authoritative_storage_failure`）统一转成 `LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED`，不区分「供应商明确拒绝」与「无响应」。该包装覆盖每一份简历的每一次 VL 识别调用。
2. `src/services/resume_vl_service.py:283-285`：`evaluate_resume()` 的 2 次重试循环对带 `tool_continuation_required` 的异常立即抛出——而第 1 条把一切异常都变成了该标记，故 Runner 路径上**第 2 次重试对任何供应商错误不可达**（仅"返回空文本/JSON 不合法"可走到第 2 次）。
3. `src/local_tools/resume_flow.py:74-76` 已有正确的单份收敛设计：`ResumeVLError → _failure('RESUME_VL_FAILED')`，批次继续下一份——异常到不了这里。
4. 前端 parked guard（`frontend/web/composables/useAgent.ts:1044-1052`）拦住 waiting 下普通输入；后端 `_model_proof()` 因 unknown receipt 拒绝恢复，两层叠加成死锁。出口由恢复批次补，本计划拆触发面。

**会话占位机制**：`src/services/agent_runner/repository.py:151-152` 将 `running/waiting/paused/interrupted/finalizing` 视为活跃并占住会话；`useAgent.ts:1007-1051` 把后续消息转为 reply/resume 控制。出口修复在恢复批次；本计划减少进入这些死态的入口。

**180 秒截止**：`src/local_tools/proxy_tool.py` 基础 `timeout_seconds=180`；`src/local_tools/durable_flow.py:24-25` 首调持久化为 `deadline_at` 并在恢复时复用；到期抛 `LOCAL_DEADLINE_VERIFICATION_REQUIRED`。仅改前端 HTTP/SSE 超时无效。

**输入性能链（每次物理输入）**：`clients/boss-resume-assistant/src/main/input/powerShell.ts` 起 2 个 PowerShell 进程（ACL nonce 目录 + 主脚本）+ 拷贝脚本；`scripts/win-click.ps1:31` 每次调用 `Add-Type` 现场编译约 200 行 C#；绑定模式 `:155` 走 `Get-CimInstance`（WMI）反查 Chrome。`desktopCheck.ts` 同款"起 PowerShell + Add-Type"。故障实测：单次点击 26.235s / 拖动 27.495s（历史样本 2.2～2.4s，约 10～12 倍退化）；body 未覆盖 81.381s、调用层 13.784s 未细分；write 工具前置 desktopCheck 与前置 11～15s 延迟高度相关；列职位 3 次 snapshot 累计 14.030s 为另一独立热点。客户重启 Windows 后恢复正常（临时恢复，根因待取证）。

## Phase 1：存活热点分段与替换前基线固定

**范围决策（2026-10-20，用户确定）**：输入链路直接 Node 原生化（Phase 5），**不再对 PowerShell/C# 路径做任何新增子阶段取证**——给将被删除的实现埋计时是丢弃型工作。复盘验收清单第 1 项「细分单次约 27 秒输入成本」改由 Phase 5 native 实现的内建分段计时 + Phase 6 A/B 判别实验满足：同一机器同参数下 native 约 1s 而 PowerShell 25s+，即证实「进程创建/csc 编译/WMI 公共路径退化」假设，无需逐段测量旧路径。

本阶段只保留**替换也解决不了**的热点分段与基线：

1. **body 未覆盖 81.381s 互斥分段**：筛选结构解析、逐行预校验、可见区几何计算、年龄值读取/验证、失败后核验及未包装调用——均在插件 TS 侧（FilterSetter/bossContext），与输入通道无关，原生化后依然存在。
2. **快照热点**：列职位 3 次 snapshot 累计 14.030s 等前置工具快照采集分段（同上，TS/CDP 侧）。
3. **调用层非 desktopCheck 部分分段**：桌面锁等待、Provider 就绪/调度；desktopCheck 本体由 Phase 5 进程内化消除，其耗时在 Phase 6 A/B 中对比验证。
4. **基线固定**：以已采集 runtime.log 数据（单次点击均值 26.235s、拖动 27.495s、write 工具前置间隔 11～15s、body/call 层漏计值）登记为替换前基线，供 Phase 6 前后对比；不要求客户重复提供日志。
5. 守卫失败证据（预期/实际坐标、widget、命中 HWND、前台/几何变化）不在旧路径补做，作为 Phase 5 native 实现的内建能力交付。

## Phase 2：model_phase 供应商结果分类与单份失败收敛

按供应商结果三分，让已有的单份失败设计真正可达：

1. **明确拒绝**（收到完整 HTTP 响应体，如 400/1210、4xx/5xx 带错误码）：以可识别的业务异常穿透 `model_phase`，进入 `resume_vl_service` 既有重试（第 2 次可达），仍失败走 `resume_flow._failure('RESUME_VL_FAILED')` 单份跳过、批次继续。非 dict 响应同属"有响应"类。
2. **无响应/结果未知**（超时、连接中断）：保留 `LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED` 验证保护，防重复计费不放松。
3. **返回不可解析**（有响应但内容无效）：同明确拒绝处理，保留原始码与描述形成可审计单份失败事实。
4. 计费账本按实际回执记录，不伪造 observed/成功；网络无响应与设备外部写动作仍按各自证据保护。
5. 前端 verification 续聊入口：展示真实供应商错误码与单份上下文，提供「重试该份/跳过该份/结束任务」经授权选项，明确输入是否已受理；覆盖 waiting/verification 下输入「继续」的前后端组合（不能只修一端）。
6. 图像诊断：不含简历内容的格式、模式、尺寸、字节数、摘要及供应商错误分类。
7. 次要触发面收窄：`cloud_flow.py:17-18` 纯读恢复允许直接重读（读无副作用）；恢复被拒时 `*_INTENT_CHANGED` 类提示给出可执行指引（有 cancel 出口，仅优化提示）。

## Phase 3：恢复消费已取回简历成果

d470 案例第二批 3 份已取回但未处理即挂起，现无任何路径消费：

1. 恢复沿原 invocation 已保存的逐份结果与处理事实继续，保留前 3 份已入库记录。
2. 禁止重新点击/抓取前 6 份绕状态或重复计费；任何新视觉调用须有独立可追溯尝试身份与原业务授权。
3. 与 Phase 2 组合验收：1210 挂起 → 恢复 → 消费第二批 3 份 → 失败份按策略跳过 → 任务正常终局。

## Phase 4：180 秒持久截止语义拆除

1. 去掉「工具默认 timeout 作为健康后台任务总时限」语义：Runner 观察与执行解耦，用持久原调用及活动续租观察完成；前端离开/订阅重连不结束任务。
2. 兼容已保存旧 `deadline_at` 的存量任务，不忽略既有持久 deadline（迁移或投影方案在实施前细化）。
3. 不取消租约、身份校验、显式取消或效果未知保护；不得仅放大数字（180→600）冒充修复。
4. 验收对应复盘清单：模拟超过 180 秒但有有效执行归属的操作不进错误验证态；前端断开再订阅不取消或重发；旧 checkpoint/deadline 兼容测试。

## Phase 5：Win32 输入链路 Node 原生化

消除「每次输入起 2 个 PowerShell 进程 + 现场编译 C# + WMI 查询」的公共退化面（健康机器 ~2.2s → ~1.1s，拟人节奏为地板；病态机器直接消除进程创建/csc/WMI/AV 扫描敏感面）：

1. 选型评估 `koffi` / `ffi-rs` 级 N-API FFI（prebuild、Electron ABI 稳定、签名链路可过）；不用停维护的 ffi-napi，不自写 C++ addon（除非评估否决）。
2. `BossPhysicalInput.cs` 守卫 **1:1 移植、一条不删**：`RequireCursorOnWidget`、`PointOnWidget`、`ReadRect`、DPI 线程上下文（`SetThreadDpiAwarenessContext`）、`MarkInputStarted`/`BOSS_INPUT_UNKNOWN` 会计、`STALE_OBSERVATION`、render widget 面积最大选取、前台绑定与 AttachThreadInput。
3. **内建分段计时与守卫取证**（承接原 Phase 1 输入侧取证）：进程内逐段计时（前台激活、窗口探查、光标守卫、拟人移动、按下/拖动/键入、收尾）直接写 perf 日志，替代旧路径的 `win32:*` 整段计时；守卫失败分别记录两项谓词（光标坐标 vs WindowFromPoint 归属）及预期/实际坐标、目标 widget、命中 HWND、前台/几何变化。不采集简历正文。
4. `desktopCheck` 进程内化：`OpenInputDesktop` 单调用替换整套 PowerShell+Add-Type。
5. 消灭 WMI：宿主为 `ensureDebugChrome` 启动方，直接持有 PID，进程内传参，不再 `Get-CimInstance` 反查。
6. `clickAndType` 点击+逐字输入原子性保持（进程内天然成立）。
7. 按共同契约登记新 capability（生产/消费两端验证），旧客户端保留 PowerShell 回退；隔离验收通过前不切默认。
8. 打包链路更新：NSIS、插件签名、`verify-runtime-package` 清单纳入新原生依赖。
9. 守卫场景验收（复盘清单第 4 项）：年龄双端校准、页面位置变化、光标/遮挡变化——不盲点、不假成功、准确阶段提示与原始故障码。

## Phase 6：隔离授权环境 A/B 复现与性能目标

1. 先固定实际装机 release/digest，再在用户授权隔离环境 A/B（PowerShell 回退与 native 双通道同机同参数对比，作为「进程创建/编译公共路径退化」假设的归因判别实验；对照嫌疑提交 `47f25c16`、`c8e0c3b1` 仅用于旧路径残留归因，不阻塞 native 交付）。
2. 冷/热启动区分进程与浏览器成本；记录点击、拖动、快照次数及滑块是否反复修正。
3. 给出明确性能目标（如 native 后健康机器单次输入 ≤1.5s、单工具 span 不复现 40s+ 量级），与 Phase 1 固定的替换前基线作前后对比。
4. 登记真机下次卡顿的最小证据采集流程：系统可用/已提交内存、分页与磁盘延迟、CPU，Runtime/Provider/Chrome/PowerShell 进程私有内存、句柄、线程、CPU/IO；重启或关闭进程前保留。
5. 前置工具热点一并排查：列职位 3 次 snapshot 14.030s、列/选职位 40s+ 量级。

## Phase 7：进度语义分离与可观测性缺口

1. `ProgressForwarder` 心跳续租与业务语义进展分离：报告当前步骤、最近语义进展、实际无进展时长；无进展告警与诊断，不默认重放。不将重复同一句心跳当作业务推进（68 条相同「设置筛选面板」教训）。
2. `legacy/log.ts` 文件写失败静默降级增加可见告警；Electron supervisor 侧不依赖壳层 stdout 兜底。
3. 诊断导出跟随 `installation-v2/current.json` 指针定位实际 home，保留 legacy 路径兼容（`homeExists=false` 误报修复）。

## Phase 8：独立测试与 CodeReview

非平凡开发按 [开发流程规范](../../.claude/rules/dev_workflow.md) 执行：开发自测 → 独立测试 → 独立 CodeReview → 主控提交前验证。重点组合：

- 三类失败路径（明确 400/无响应/不可解析）分别断言单份状态、剩余批次处理、计费边界。
- 1210 挂起 → 恢复消费第二批 → 单份跳过 → 终局，全链路不重复抓取/计费。
- >180s 健康执行、前端断开重连、旧 deadline 存量兼容。
- native 输入守卫反例：遮挡、几何变化、光标被移、双端年龄滑块；PowerShell 回退可用性。
- 心跳/进展分离展示与无进展告警。

## 行为与安全边界

1. 不删除或削弱输入守卫；不自动重放效果未知的外部写动作（发消息/打招呼）；验证保护（设备效果、计费回执）按证据分类收窄，不为恢复便利伪造 observed/成功。
2. 任务权威仍是 AgentRunner，遵循 [Runner/Desktop/Runtime 共同契约](../system/runner-desktop-runtime-integration-contract.md)：不新增本地 Agent loop；新输入通道登记 capability 版本与生产/消费兼容；修改共同接口先登记版本与唯一写入者。
3. **与恢复批次的衔接**：Phase 2/3/4 均触及恢复链路（`domain_recovery.py`、`local_recovery.py`、恢复入口），必须基于恢复批次（Phase 6～10）提交后的代码实施，开发前先核对当前工作区未提交改动，避免冲突或重复实现。
4. 密钥/凭据不进日志与回复；图像诊断不含简历正文；真机性能复现与重放须用户另行授权，不在客户会话自动重跑。
5. 未获用户明确授权不提交、不部署；开发进度实时更新本表并同步 `docs/ideas.md` 索引行。

## 交付边界

- 本计划不含恢复批次已覆盖的出口修复（end-waiting、unknown 分层、页面核验），不重复登记。
- Phase 5 涉及 BOSS 插件与 Runtime 双端发布，需配套客户端打包与升级节奏；旧客户端无新 capability 时保持 PowerShell 路径行为不变。
- 性能根因结论以 Phase 1/6 实测为准；客户重启恢复是临时现象，不得据此关闭内存/进程状态排查。
