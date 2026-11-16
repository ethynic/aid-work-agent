# BOSS 筛选容错与本机操作恢复开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|---|---|---|---|
| Phase 1 | ~~服务端统一筛选参数~~ | ⛔ 已取消（2026-11-13） | 用户决策：服务端按工具写字段级归一化属补丁代码（写死），容错收敛到 CLI 侧；服务端归一化实现已回退未合入 |
| Phase 2 | BOSS CLI/MCP 容错与结构化零输入回执 | ✅ 完成（2026-11-13，CR 无 P0/P1，commit f9766b2a） | Phase 2：sanitizeBossFilterInput 单一规则服务 MCP z.preprocess/CLI filter/operation 三入口；全量 bounded 89 文件除基线预存 dom-snapshot-owner-scroll 外全绿；打包 stage 复刻验证通过。R5：公开 schema 放宽四处（experience/salary/educations/first_degree_only 接受 null、educations 项接受任意字符串；educations:null 按未填写静默省略不进证据）+ 回执 filter_evidence（version 1）+ 云端 _map_terminal 逐项豁免；provider_version 1.0.2、digest 0a61d517→fea55461、tool_declaration 不变；旧 CLI 无证据回执行为不变。P2 范围外：数字薪资、age min≥50 等云端 pydantic 即失败，Agent 凭明确校验错误自纠、不产生核验卡；若放宽将向旧 CLI 设备下发其 schema 拒绝的值，制造更差回归。端侧 CLI 更新由用户安排 |
| Phase 3 | 可信结束证据与 Runner 自动接续 | ✅ 完成（2026-11-16，CR 无 P0/P1，commit 43bf0207） | Runtime 纯成功路径附加 _runtime_completion v1（剥离插件同名字段、五类禁止路径零写入）+ 能力 runtime_boss_completion_v1；服务端四要素核验只解除占用（state/effect/计费/原始回执零改写）；迟到可信回执核定；真实 PG 集成 hold 释放 2 例 + 五分支矩阵全覆盖 + 变异 3/3；未知招呼不自动重发语义未动 |
| Phase 4 | 原会话一键停止并继续 | 🔧 进行中（2026-11-16 开发+自测完成，待独立测试与 CR） | 服务端 verify-local 增 continue_after_stop（组合校验 422、chat 专属、协作取消在途原调用、BUSY 栅栏排除本 scope）；v3 停稳回执事务内服务端编排一条 accepted resume 控制（stop_continue.py，门不满足静默跳过）；public_view 投影 stop_and_continue（仅服务端可受理时）+ local_verification.continue_after_stop；Web 恢复卡主按钮「停止上一步并继续」+ 等待文案 + 输入附件保留。真实 PG 集成新用例 9/9（组合校验、运行中原调用受理+协作取消、停稳→自动 resume 单条投递、空闲受理+幂等回放、BUSY 栅栏与投影联动、scheduled/cancelled/detached 拒绝、在途 resume 不重复投递、closed 只解除占用）；useAgentRunner vitest +5、web build 通过。本机环境基线（干净 HEAD 复现，非本改动引入）：worker 子进程类 3 例不收敛（test_local_verification）、slot_supervisor 2 例（测试桩缺 kill）与 artifact symlink 1 例（WinError 1314 特权）；本机 PG 无 pgvector，隔离库仅过滤 vector 对象复刻 dev_test --isolated-db 机制 |

2026-11-12。设计见 [筛选参数容错](../design/recruiting/resume-job-matching-design.md#boss-筛选参数容错)、[恢复交互](../system/agent-application-architecture-design.md#boss-本机操作恢复) 和 [共同契约](../system/runner-desktop-runtime-integration-contract.md#boss-筛选容错与本机操作恢复共同检查点)。Phase 1 可独立修复今天的触发场景。Phase 3–4 涉及跨端回执、占用释放与并发停止，按高风险流程执行开发、独立测试和独立 CodeReview。

## Phase 1 · 服务端先兼容旧插件

- 在 BOSS 筛选参数入口统一处理可选值，早于模型工具严格校验及持久派发。覆盖普通 proxy、Runner 预绑定和恢复比较。新派发使用规范参数，已有调用按原绑定取回原结果。保留高级筛选能力探查、账号约束和同一调用的幂等键。
- 用今天的参数形态验收。给经验传 `null`，保留同批有效的学历、薪资、年龄或动态行。旧 BOSS 插件收到规范参数并继续筛选。补齐薪资非字符串、学历混合数组、动态行空选项、年龄上限 `null`、全空无点击和会员探查缺失用例。

## Phase 2 · 插件自身容错

- 在 MCP 严格校验前清洗可选筛选值。CLI `filter` 与 MCP 的共用 operation 使用同一份规则。全空以及确知动作前拒绝的情况走已有结构化回执。插件对必需动作目标、授权引用和发送语义沿原规则校验。
- 使用实际 MCP SDK 注册与调用进行回归，验证混合输入到达业务逻辑、空条件保持页面原样、同批有效条件按原语义生效。同步验收真实 `tools/list`、插件 manifest 与已交付 Runtime 导入。后端独立发布仍须验证旧 BOSS，不依赖本阶段升级。

## Phase 3 · 已停稳就继续

- 按共同契约由 Runtime 产生版本化结束事实，服务端在原受权回执路径消费，复用正常/迟到结果、Runner 持久接续和设备准入。可信结束后，旧调用的未知业务效果仍保留，依赖步骤先核对，其他步骤及原会话新动作可继续。
- 使用同一原调用分别验证可信正常结束且效果未知、Host 超时/崩溃、取消后子进程未停、无回执租约过期、迟到可信回执。只有能证明停稳的分支释放旧占用。回归未知招呼或消息在新旧 worker、重复提交和并发恢复下均不自动重发，迟到成功结算一次，其他会话与设备权限按原规则执行。

## Phase 4 · 一个按钮在原会话继续

- 复用现有取消、受管停止与接续入口，在服务端编排并按原调用范围持久化进度。Web 主按钮「停止上一步并继续」按服务端可用状态显示，等待和停止期间保留输入与附件。收到可信回执时自动推进，后台只读观察需要与前台业务依赖匹配。
- 验收停止按钮一次点击后，协作取消或受管停止完成即自动从安全步骤接续，或同会话可以直接发新指令。覆盖 Chrome 随受管进程关闭、页面观察失败、设备离线、另一任务占用、停止失败、晚回执、重复点击和刷新。宽屏、窄屏均核对状态、文案与输入框行为。

## 验证与交付

- 后端按仓库 `scripts/dev_test.sh` 运行相关 unit 和真实 PostgreSQL 隔离 integration；BOSS 运行 `npm run build:main`、`npm run typecheck` 及项目定向测试；Runtime 运行相关 Host/Core 测试与类型检查；Web 运行定向交互测试及 `npm run build`。
- 发布组合覆盖仅更新服务端＋既有 Runtime/BOSS，以及新 Runtime/BOSS＋新服务端。验证 BOSS 实际接单、筛选、同会话下一条指令、回执及计费。Windows 隔离验收使用可控测试目标与受管 Chrome；agent2 部署须由用户当场明确指示。
