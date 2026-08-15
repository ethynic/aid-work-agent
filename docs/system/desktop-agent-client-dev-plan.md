# Agent 跨平台桌面客户端开发计划 v2.4

> 初版日期：2026-07-14
>
> v2.4 日期：2026-08-12
>
> 状态：🔧 部分完成
>
> 设计基线：[desktop-agent-client-design.md](./desktop-agent-client-design.md)
>
> 上位架构：[企业 Agent 平台总体架构](enterprise-agent-platform/enterprise-agent-platform-integration-design.md)
>
> 流程：每个非平凡 Phase 严格执行开发 → 独立测试 → Code Review；不自动提交
> 原则：Web 稳定优先；Local Agent Coordinator + Local Tool Host 是 Desktop 核心；Browser Runtime 独立门禁、按需启用

## 1. 当前状态

### 1.1 已完成

- Windows Electron 壳、安全 scheme、单实例、窗口状态、深链、CSP、凭证、下载、外链和自动更新状态机。
- Web/Desktop 双入口、独立构建产物和 `/portal` artifact 排除门禁。
- Windows x64 unsigned 开发安装包、ASAR 校验、SBOM、audit、license、release manifest 和 packaged smoke。
- 前端目录准备 Phase：`frontend/src` 的 220 个文件迁移至 `frontend/web`；Web/Desktop production build 与测试门禁通过。

### 1.2 未完成

- `frontend/desktop` 独立 UI 和 Desktop 自有路由。
- `frontend/shared` 的渐进式业务逻辑/组件提取。
- Desktop 与 `web` 页面、路由、全局样式彻底解耦。
- Windows 正式签名、生产更新源和真实升级/回滚。
- macOS main/platform 行为、builder、签名、notarization、更新与真机验收。
- Desktop MCP Host、第一方/第三方 CLI 管理、跨平台 Host core、诊断中心和完整本地能力体验。
- 版本化 Agent Turn Protocol、Remote Tool Gateway、语言无关协议源和 Desktop Local Agent Coordinator。

## 2. 完成定义

满足以下条件才可将 `docs/ideas.md` 条目移动到 `docs/ideas_finished.md`：

1. Web 全量测试、production build、关键路由/认证/视觉门禁无回归。
2. Desktop 不再引用 `frontend/web` 的页面、路由和全局样式；临时依赖 allowlist 为 0。
3. Windows 10/11 x64、macOS 13+ arm64/x64 签名包通过核心 Agent 真机闭环。
4. Windows/macOS 正式更新、损坏/坏签名拒绝和修复版本升级有证据。
5. `/portal`、portal credential、管理员 API 不进入 Desktop artifact。
6. Desktop 能执行受控本地命令、文件和系统工具，也能把 `LOCAL_REQUIRED` 工具路由到当前电脑或远端 `agent-tool-runtime` 节点，并统一管理第一方/第三方 MCP Provider。
7. `SERVER/LOCAL_REQUIRED/EITHER` 路由、授权、取消、未知结果和禁止静默 fallback 有端到端证据。
8. 当前电脑工具由 Coordinator 直接调用 Local Host 且不创建 cloud invocation；服务端/其他电脑工具只走 Remote Tool Gateway/Invocation Relay。
9. Agent Turn/Tool Invocation 协议完成版本协商、幂等、结果重放、滚动升级和不兼容 fail-loud 验收。
10. `DEVICE_OWNED` 会话以 Desktop 本地事件库为权威，用户/Agent 消息可同步为云端投影；Web/移动端接续始终回到原设备，离线时明确阻断且不排队、不换端、不转 Cloud Agent。
11. Browser Runtime 即使未启用或崩溃也不影响 Agent/Desktop 本地工具主链路。
12. 安装、卸载、深链、断网、睡眠唤醒、多显示器和服务端滚动升级完成验收记录。
13. Task/Execution/Action/Artifact/Evidence 企业对象贯穿 Desktop、Gateway、Host 和 Runtime；本机执行不绕云队列，但不绕策略与证据。
14. 本地 Event Store、至少一次 Relay、fencing、persist-before-effect、`STATUS_UNKNOWN` 和 schema-aware update 通过故障注入。
15. 已存在的 `DEVICE_OWNED` 会话在离线、版本或协议不兼容时绝不 fallback 到 Cloud Agent；云端继续只能显式新建/fork `CLOUD_OWNED`。

## 3. 阶段总览

计划采用“两级管理”：对外按 4 个里程碑跟踪，对内按 14 个可独立验收的执行阶段实施。Browser Runtime 是独立可选增强轨，不计入正式 Desktop 主线。

### 3.1 里程碑

| 里程碑 | 范围 | 可交付状态 | 状态 |
|---|---|---|---|
| M0 工程与独立桌面壳 | A～C | Internal Alpha：独立 Shell、登录、云端基础能力可用 | ✅ 完成 |
| M1 Desktop Agent 核心 | D1～D3 | Engineering Beta：本地 Coordinator、持久会话、跨端投影与恢复闭环 | 🔧 进行中 |
| M2 本地工具与产品化 | E1～G | Tool Beta：本机/远端工具、工作台、生命周期与诊断可用 | ⬜ |
| M3 双平台正式发布 | H/I + K | Signed RC → GA：双平台签名、更新、真机和滚动升级验收完成 | ⬜ |

### 3.2 执行阶段

| # | 执行阶段 | 交付 | 状态 | 预计 |
|---:|---|---|---|---:|
| 0 | A Web 目录基线 | `src → web`，双端构建与结构门禁 | ✅ 完成 | — |
| 1 | B 工程边界与 Shared 基础设施 | alias、依赖规则、测试发现、协议工具链 | ✅ 完成 | 2～3 天 |
| 2 | C Desktop Shell、启动与登录 | 独立 renderer、启动状态机、双平台 dev smoke | ✅ 完成 | 5～7 天 |
| 3 | D1 Agent Turn Protocol 与 Remote Gateway | 语言无关协议、版本协商、服务端远程工具入口 | ✅ 完成 | 7～10 天 |
| 4 | D2 Coordinator 与核心对话 | 本地 turn loop、基础对话、多会话与取消恢复 | ⬜ | 8～12 天 |
| 5 | D3 Event Store、Relay 与故障恢复 | 设备会话权威、投影、fencing、Golden Recovery Matrix | ⬜ | 10～15 天 |
| 6 | E1 Host Core 与 Runtime 兼容 | 共用 Host Core、隔离进程、既有 Runtime 行为不变 | ⬜ | 7～10 天 |
| 7 | E2 本地 Shell/File/System Executor | 授权目录、结构化文件工具、审批、进程回收 | ⬜ | 8～12 天 |
| 8 | E3 Provider 信任与权限 | 第一方/第三方 MCP、签名摘要、安装升级与重新授权 | ⬜ | 6～10 天 |
| 9 | E4 多节点路由与 UI Bridge | 当前 Desktop/远端 Runtime 选择、租约、窄 IPC | ⬜ | 6～10 天 |
| 10 | F Agent 工作台页面 | 数字员工、会话、知识库、本地工具、设置 | ⬜ | 10～15 天 |
| 11 | G 平台能力与生命周期收口 | driver、诊断、Quiesce、数据与遥测治理 | ⬜ | 8～12 天 |
| 12 | H/I 双平台发布双轨 | macOS 与 Windows 签名、更新、安装和回滚 | ⬜ | 8～12 天 + 外部等待 |
| 13 | K RC/GA 全量验收与收口 | 真机矩阵、滚动升级、故障注入、文档 | ⬜ | 10～15 天 |
| 可选 | J Browser Runtime | 满足 Browser 3R 门禁后独立实施 | ⏸ 等待 | 8～12 天 |

D1～D3 的 Local Agent Coordinator 和 E1～E4 的 Local Tool Host/MCP Host 都不可删除或后置到正式版之后；J 不阻塞正式 Desktop。

### 3.3 关键路径、并行关系与不可逆依赖

```text
A Web 基线
  └─> B 边界与协议工具链
       ├─> C 独立 Desktop Shell
       └─> 签名账号、runner、更新源等外部准备
            C ─> D1 协议/Gateway ─> D2 Coordinator/核心对话 ─> D3 Event Store/Relay
                                      ├─> F1～F3 工作台页面
                                      └─> E1 Host Core ─> E2 本地 Executor ─> E3 Provider 信任 ─> E4 多节点路由
                                                                                   └─> F4/F5 + G 生命周期
                                                                                         ├─> H macOS 发布轨
                                                                                         └─> I Windows 发布轨
                                                                                              └─> K RC/GA 验收
```

- D1 服务端协议必须先于 D2 Coordinator 闭环；E 依赖 D 的路由、审批、取消和幂等语义，不能先做成独立私有工具链。
- Web/渠道的 Cloud Agent 路径在 D/E 期间保持兼容，不要求改用本地 Coordinator。
- F1～F3 可在 D2 contract 稳定后与 D3/E 并行；F4 依赖 E4，F5 的基础设置可提前、诊断部分依赖 G。
- macOS runner、证书、entitlements、Windows 证书和生产更新源从 B/C 开始准备；H/I 是正式验证轨，不是准备工作的首次启动点。
- H/I 在 D/E contract 稳定后并行，二者都完成后才能进入 K；K 前必须完成双平台真机和服务端滚动升级矩阵。

### 3.4 每个执行阶段的统一交付物

每个执行阶段必须控制在一个可独立完成的三智能体流程内，并提供：

1. 一个可演示的纵向结果，不能只交付目录或接口空壳。
2. 允许修改目录与明确禁止修改范围。
3. 新增/回归/故障注入测试清单及实际结果。
4. 协议、数据库或本地状态变更的兼容与回滚点。
5. 未完成项、已知风险和下一阶段输入条件。

## 4. 全程强制门禁

### 4.1 每个 Phase 开始前

- `git status --short`，识别用户已有改动并避免覆盖。
- 记录 Web `npm run typecheck`、测试和 build 基线。
- 明确本 Phase 允许修改文件；禁止顺手重构 Web。
- 非平凡 Phase 建立三智能体计划并在 `docs/ideas.md` 更新进度。

### 4.2 每个 Phase 结束时

```bash
cd frontend
npm run typecheck
npm test
npm run build
npm run build:desktop

cd ../clients/agent-desktop
npm run typecheck
npm test
npm run smoke
```

若仓库存在与改动无关的既有失败，必须用迁移前版本/哈希或独立复现证明，不能宣称全绿，也不能越界修改业务。

### 4.3 Web 稳定性证据

每个 Phase 记录：

- Web route 集合与认证入口差异。
- 登录、租户登录、对话/SSE、多会话、附件、知识库和 Portal 定向结果。
- 关键页面固定视口截图差异。
- Web bundle 中 Desktop/Electron-only module 扫描结果。
- `deploy/agent_update.sh`、`agent2_update.sh`、`agent3_update.sh` 继续通过 `npm run build` 生成 `frontend/dist`；不得要求改部署脚本才能维持 Web。

## 5. Phase A：Web 目录基线

### 状态：✅ 2026-08-12 完成

完成内容：

- `frontend/src` → `frontend/web`，220/220 文件完整，218 个逐字节不变。
- `@/` 保持指向 Web，避免批量 import 改写。
- 更新 HTML、Vite、Vitest、TypeScript、Tailwind、Desktop verifier 和开发规范路径。
- 修复 `agentRoutes` 在 Desktop define 下的等价动态 import 解析问题。
- 新增结构测试，禁止恢复旧 `frontend/src`。

验收结果：Web typecheck/build、Desktop build/artifact verifier、Desktop 37/37 通过；前端既有 139/140 与异步测试债务已证明不是迁移引入。

## 6. Phase B：工程边界与 Shared 基础设施

### 状态：✅ 2026-08-12 完成

### 目标

建立长期依赖方向，但暂不迁移大块业务逻辑、不改变任何 Web 页面。

### 工作包

1. 新建空的 `frontend/desktop`、`frontend/shared` 最小骨架和 README。
2. 配置明确 alias：
   - `@web/* → web/*`；
   - `@desktop/* → desktop/*`；
   - `@shared/* → shared/*`；
   - `@/* → web/*` 暂时兼容 Web。
3. 新增 Desktop 专用 tsconfig/Vitest include 或等价 project 配置，确保两端测试均会被发现。
4. 增加依赖边界检查：
   - shared 禁止引用 web/desktop/Electron；
   - web 禁止引用 desktop；
   - desktop 对 web 使用临时 allowlist；
   - Desktop artifact 禁止 Portal/Web entry。
5. 建立 Shared contract test harness 和 Desktop renderer test setup。
6. 固化 Web route/auth/bundle 结构基线；Phase B 无 UI 改动且当前仓库无稳定跨平台截图设施，固定视口截图基线转入 Phase C，随独立 Desktop UI 一并建立并在后续 Phase 持续比较。
7. 建立 `contracts/desktop-agent` 语言无关协议目录、版本规则和最小生成/一致性校验工具链；只放 D1 首个纵向闭环需要的 envelope 与占位引用，不在缺少消费者时提前穷举全部 schema。
8. 建立 `clients/shared/agent-coordinator-core` 与 `local-tool-host-core` 的依赖边界骨架；此 Phase 不实现业务循环。

### 允许修改

- `frontend` 工具链配置、测试基础设施、新目录骨架。
- 不修改 Web 页面模板、样式和业务逻辑。

### 验收

- 四类 alias 和测试发现正确。
- 依赖边界测试具备正/反例，不能只检查目录存在。
- Web build 输出与 Phase A 行为一致。
- Desktop 旧 renderer 仍能构建，作为可回退基线。

验收证据：Phase B 门禁 56/56、两个 shared core 2/2、Agent Desktop 37/37；Web/Desktop typecheck、production build、artifact verifier 和协议生成一致性均通过。独立测试与 Code Review 修复依赖扫描绕过后复测全绿。Frontend 全量 179/180，唯一失败和一个异步错误均可在未修改业务文件中独立复现，记录为既有债务。固定视口截图门禁按上述范围转入 Phase C，不阻塞本阶段工程边界完成。

### 估算

2～3 个工作日。

## 7. Phase C：Desktop Shell、启动与登录

### 目标

让 Electron 首次加载真正独立的 Desktop renderer，但不在本 Phase 迁移对话主链路。

### 工作包

1. 新建：
   - `desktop/main.ts`；
   - `DesktopApp.vue`；
   - Desktop router；
   - `DesktopShell`、`DesktopSidebar`、`DesktopTitlebar`、`DesktopStatusBar`；
   - Desktop tokens/styles。
2. `desktop.html` 和 `vite.desktop.config.ts` 切换到 `/desktop/main.ts` 与 Desktop alias。
3. 实现 Desktop 启动状态机：booting、secure-store-ready、auth、online/offline、update-required、fatal-local。
4. 实现独立 Desktop 登录页；Shared 提取第一批：认证 DTO、CredentialStore contract、API resolver、纯类型。
5. 更新/离线/安全失败从 Web Sidebar/内联 DOM 移到 Desktop Shell。
6. 建立临时旧 UI 回退开关，仅开发构建可用；生产 Desktop 不加载 Web entry。

### macOS 同步要求

- `darwin` 窗口创建、traffic-light safe area、Dock activate 恢复、应用菜单基础行为。
- 不要求本 Phase 正式签名，但必须在 macOS arm64 开发机启动真实 renderer；x64 至少 CI compile/build。

### 验收

- Desktop module manifest 不包含 `web/App.vue`、`web/router/**`、`web/style.css`、Portal-only 模块。
- 登录成功/失败、safeStorage 失败、API 离线、最低版本阻断均有可访问 UI。
- 720×500、默认窗口、150%/200% 缩放可用。
- Windows 与 macOS 开发 smoke 通过；Web 全门禁通过。

### 实施进度（2026-08-12）

- 已完成独立 Desktop renderer、router、Shell、登录页、启动 reducer、离线/更新/fatal 状态 UI，以及首批 Shared 认证与平台 contract。
- production artifact 已实现 `web/**` 零引用硬门禁；旧 Web UI 仅保留 dev-only 独立入口。
- Electron bridge 升级为 v3，增加窄启动状态 contract；macOS driver 已完成 traffic-light、Dock activate 生命周期和应用菜单 compile/contract 测试。
- 独立测试与 Code Review 已完成；Phase B/C 门禁 80/80、Agent Desktop 39/39，Web/Desktop production build、artifact verifier、边界和协议一致性均通过。
- Windows 已使用锁定的 Electron 43.1.0 完成可执行 smoke，返回 `AGENT_DESKTOP_SMOKE_PASS`；macOS 真机 smoke/固定视口截图仍必须在 macOS arm64 设备补证，不以编译、jsdom 或 Windows 结果代替。
- 2026-08-14 macOS arm64 真机已完成可执行 smoke、默认/720×500/150%/200% 视口、Dock 关闭恢复、`Command+Q` 和登录页窗口拖动验收；M0 关闭并进入 M1/D1。

### 估算

5～7 个工作日。

## 8. Phase D：Local Agent Coordinator、核心对话与多会话

### 目标

Desktop 独立完成最重要的 Agent 使用链路，并让本地 Coordinator 成为 Desktop tool-call loop 的控制者；不把现有 Python Agent/Redis/数据库整体打包进客户端。

### Shared 提取顺序

1. Agent/session DTO、事件和纯 reducer。
2. API client 与 SSE parser/transport contract。
3. per-session 状态池和取消/恢复语义。
4. Markdown/message/attachment 的纯展示能力。

### D1：Agent Turn Protocol 与 Remote Tool Gateway（执行阶段 3）

1. 从现有 `Agent` 抽象版本化 `next` contract，返回 final、clarification、local tool call 或 remote tool call；Web 现有调用路径保持不变。
2. Remote Tool Gateway 暴露 catalog/invoke/events/cancel，服务端注入可信 tenant/user/secret，要求 schema version、idempotency key、权限和审计。
3. 模型供应商 Key、Prompt、数字员工/Skill 策略、长期记忆和计费继续在服务器；现有 `CLOUD_OWNED` 会话保持云端权威，`DEVICE_OWNED` 会话只向云端同步可阅读投影。
4. 首版使用 HTTPS JSON + SSE/WSS；不为形式统一强制引入公网 gRPC。
5. 协议封套加入 `task_id/session_ref/execution_id/attempt_id/action_id/invocation_id/artifact_id/evidence_stream_id/release_id/policy_decision_id`，语言无关 schema 同时约束 TypeScript/Python。
6. 服务器 PDP 签发绑定 action/args/schema/target/policy revision/expiry 的 authorization ticket；Desktop/Runtime PEP 验签。claim token 仅管理租约，不能代替授权票据。

#### D1 独立验收与退出条件

- TypeScript/Python 从同一协议源生成或通过一致性校验，兼容/不兼容版本样例均可复现。
- 使用测试客户端完成 `next → remote tool call → result → final` 最小纵向闭环，不依赖 Desktop UI。
- 重复请求、ticket 篡改/过期/换参、schema 不兼容和服务端滚动升级 contract test 通过。
- Web/渠道 Cloud Agent 既有入口和工具调用行为不变。

#### D1 实施进度（2026-08-14）

- 已完成语言无关 Agent Turn/Remote Gateway 1.0 schema、TypeScript/Python 同源生成、版本协商与兼容性样例。
- 已完成隔离且默认关闭的 `/api/desktop/v1` next/catalog/invoke/events/cancel API、授权票据、跨 worker 幂等、审计事件与 cursor SSE replay；默认启动不导入 API、不注册路由，D1 专用表也不进入三个常规更新脚本。
- 生产 `ExistingAgentBackend` 已通过现有 Agent 的专用分步 seam 完成 `next → remote tool → result → final`，Web/渠道默认 Agent 路径不变。
- 开发、独立测试与 CodeReview 已完成；D1 24/24、Agent 相邻主控回归 71/71，协议生成、后端启动导入、Web/Desktop 构建与既有客户端门禁通过。真实 PostgreSQL 已验证 DDL 可重复执行并回滚无残留。
- D1 只完成单 action 顺序闭环；provider 同批多 tool call 会 fail-closed 并要求重新决策，不会静默丢弃或并行执行。worker 硬崩溃恢复、持续 live event tail 和同 session 单写者语义留给 D2/D3。启用前运维需显式执行 repeat-safe `deploy/desktop_agent_d1.sql`、配置密钥/allowlist 并重启。

### D2：Desktop Local Agent Coordinator 与核心对话（执行阶段 4）

1. 在 `clients/shared/agent-coordinator-core` 实现无 UI Coordinator core，管理 turn loop、工具目标、审批等待、取消、并发和恢复 journal。
2. 当前 Desktop 的 local tool call 直接进入 Local Host；Server tool call 进入 Remote Tool Gateway；其他 device call 进入现有 invocation relay。
3. preload 只传递结构化状态和审批，不向 renderer 暴露 tool executor。
4. Coordinator/Server protocol version 不兼容时 fail-loud，现存 `DEVICE_OWNED` 会话进入 `update-required` 或只读态，绝不回退 Cloud Agent。用户只能显式新建或 fork `CLOUD_OWNED` 会话。
5. 每个 DEVICE_OWNED session 固定 `release_id/protocol_version/policy snapshot`；canary 按新会话分桶，不在进行中回合切换版本。

本阶段同步实现最小可用的 `DesktopChatPage`、`DesktopConversationPane`、`DesktopComposer` 和 `DesktopSessionRail`，只覆盖文本对话、多会话、取消、重试和基础错误恢复；附件、复杂展示和跨端接续留给后续阶段。

#### D2 独立验收与退出条件

- 登录 → 新建 `DEVICE_OWNED` 会话 → 文本流式回复 → 本机测试工具 → final 完成纵向闭环。
- 当前 Desktop 工具由 Coordinator 直达测试 Host，且不创建 cloud invocation；服务端工具只经 D1 Gateway。
- 多会话后台继续、取消、重试、进程重启 journal 恢复和协议不兼容 fail-loud 通过。
- preload/renderer 不获得 executor、token、任意命令或文件系统能力。

### D3：本地 Event Store、云端投影与远程接续（执行阶段 5）

1. Desktop 使用 SQLCipher（原生依赖验证不通过时采用经审计的字段级 envelope encryption）持久化完整会话事件、执行 journal、workspace 引用和恢复游标；safeStorage 只包装 DB key。明确 WAL/fsync、单写锁、迁移、轮换、完整性检查、配额、compact/snapshot、备份和损坏 safe mode。
2. 服务端保存 `session_id`、`session_type`、`owner_device_id`、在线状态以及按租户策略同步的用户/Agent 消息和进度摘要；该记录是阅读投影，不是设备会话执行权威。
3. Web/移动端通过 Session Relay 订阅投影和实时事件；提交新消息时携带 `command_id`、`expected_command_revision` 与 idempotency key，必须等原 Desktop 返回本地持久化 ACK 才显示发送成功。
4. `owner_device_id`、`coordinator_session_id`、`workspace_ref/fingerprint` 在会话执行期不可变。原设备离线时返回明确离线状态，不排队自动执行、不切换 Cloud Agent、不换 Desktop。
5. 显式远端工具调用仍可路由到固定 `agent-tool-runtime`，但 Coordinator 和 workspace 权威留在原 Desktop；换电脑只能新建会话或显式 fork/handoff 并重新校验和授权。
6. 分离 `local_event_seq/command_revision/projection_revision/evidence_seq`；实现 inbox/outbox、ACK 查询、cursor replay、gap detection 和 hash snapshot resync，明确至少一次投递。
7. Relay 下发 `connection_epoch/fencing_token`，拒绝旧进程、旧连接和迟到工具结果；本地/Web/移动输入进入单写者队列并定义 busy/approval/cancel 并发语义。
8. 工具 intent 在 effect 前落盘，结果与 Evidence 原子关联；恢复按 not_started/running/result_pending/unknown reconciliation，unknown 写动作禁止自动重放。
9. 云端投影采用字段级 retention/residency 策略，默认不上传本地 stdout、路径、文件正文和敏感审批参数；附件、索引和临时文件纳入相同加密/清理生命周期。

Web 原路径先保留兼容 re-export；不批量替换 `@/`。

#### D3 独立验收与退出条件

- 本地事件库是 `DEVICE_OWNED` 会话唯一执行权威；云端投影删除或延迟不影响本机恢复。
- Web/移动端命令必须由原 Desktop 持久化 ACK 后确认；离线、换端和版本不兼容均明确阻断。
- Golden Recovery Matrix 覆盖 ACK 丢失、重复/乱序、双连接、睡眠、强杀、磁盘满、DB 损坏和 effect 后崩溃。
- 未知写操作不自动重放，旧 fencing token 和迟到结果被拒绝。

### D3 Desktop 补全

- `DesktopChatPage`、`DesktopConversationPane`、`DesktopComposer`。
- `DesktopSessionRail` 和后台多会话状态。
- 上传队列、附件预览/下载、输入草稿和错误恢复。
- 窗口窄模式、键盘快捷键、焦点恢复、ARIA live 流式状态。
- 活跃 stream 对退出/更新的阻断信息。

### D 阶段整体验收

- 登录 → 新会话 → SSE → 切换会话后台继续 → 返回查看 → 上传/预览/下载 → 取消/重试闭环。
- 本机测试工具证明调用不创建 cloud invocation；服务端测试工具经 Remote Tool Gateway 执行；远端测试 Provider 仍经指定 device relay。
- Agent Turn Protocol 的重复请求、断线恢复、版本不兼容、tool result 重放和幂等门禁通过。
- authorization ticket 的篡改、重放、过期、换参、换节点和策略/schema 变化全部拒绝；claim token 与授权票据不可互换。
- Web/移动端能查看云端消息投影并接续 `DEVICE_OWNED` 会话；消息只由原 Desktop ACK 后确认，执行事件按序返回。
- ACK 丢失、重复 command、乱序/gap、双连接、睡眠、强杀、电源中断、DB full/corrupt 与 effect 后崩溃通过 Golden Recovery Matrix。
- 原 Desktop 断网、退出、重装后身份变化、workspace revision 冲突时均 fail-closed；验证不会在 Cloud Agent 或其他设备重复执行。
- 401、429、5xx、网络中断、SSE 非正常终止均可恢复，不重复用户写操作。
- Shared contract 同时由 Web 与 Desktop 消费并通过。
- Web 关键截图和路由无非预期变化。

### 分阶段估算

- D1：7～10 个工作日。
- D2：8～12 个工作日。
- D3：10～15 个工作日。

估算包含协议兼容、Event Store、Relay/fencing、Policy/Evidence envelope 与故障注入。三个阶段必须分别完成开发、测试、Code Review，不允许合并成一次长周期交付；可在 D1 contract 稳定后并行准备 D2 UI 与 D3 存储 spike，但不得并行修改同一协议权威源。

## 9. Phase E：通用 Local Tool Host 与第一方/第三方 CLI

### 目标

让 Desktop 同时成为通用本地工具执行器、现有边缘工具架构的控制台和可选执行节点，而不是新建一条 Desktop 私有工具链。正式版必须能安全执行本地命令/文件工具，完成至少一个第一方 Provider 在“当前 Desktop”和“远端 `agent-tool-runtime`”两种节点上的真实闭环，并具备安全接入第三方标准 MCP Provider 的能力。

### E1：冻结复用边界并抽取 Host Core（执行阶段 6）

1. 以现有 `clients/agent-tool-runtime`、`src/local_tools`、`ExecutionTarget` 和第一方 CLI/MCP 规范为 contract 基线。
2. 建立 Desktop Host 与 headless Runtime 共用测试：manifest/schema digest、pair/claim、progress/cancel/result、lease/unknown。
3. 明确 `clients/agent-tool-runtime` 长期保留为 Web/Desktop 共用的远端/headless 执行节点；Desktop 本机执行不要求另装 `aid-runtime`，远端电脑必须安装并配对它。
4. 新建 `clients/shared/local-tool-host-core`，渐进抽取 API client、poll loop、invocation runner、Provider manager 和 manifest verifier。
5. 把凭证、进程监管、日志和安装源做成 adapter；core 不依赖 Electron、renderer 或 Windows DPAPI。
6. `agent-tool-runtime` 接回该 core，现有 CLI 行为和安装包保持兼容。
7. 正式拓扑固定为 Electron main 仅作为 broker，Local Host 在隔离 utility/child process 消费同一 core，Provider 再作为 Host 子进程；禁止在 main 内执行工具。
8. IPC 绑定 authenticated window/session、版本化 schema、action digest、user-gesture nonce、大小和超时；Host 使用有界 restart backoff。

#### E1 独立验收与退出条件

- `agent-tool-runtime` 接回共用 core 后，CLI、npm 包、配对、长轮询和既有 Provider contract 无回归。
- Desktop 隔离 Host 能启动测试 Provider，完成调用、取消、崩溃重启和退出清理；Electron main 不执行工具。
- Desktop Host 与 headless Runtime 对同一测试 invocation 产出一致结果。

### E2：通用本地命令、文件与系统 Executor（执行阶段 7）

1. 定义结构化 `local_shell` contract：command、授权目录引用、timeout、期望 effect；shell executable 和基础 env 由 Host 固定。
2. Windows/macOS 实现受控 Shell adapter、stdout/stderr 流式输出、cancel、timeout 和整棵进程树回收。
3. 建立授权目录 registry 和结构化本地文件工具；renderer/LLM 不直接获得任意文件系统能力。
4. 定义统一 `FileRef` 和 `FileToolRouter`：server workspace/artifact 继续走现有 Python 工具，device workspace 走 Local File Executor；不长期暴露两组同义工具。
5. 本地 `read/write/edit` 支持 revision/hash、冲突拒绝、原子写入、diff、路径/软链接/reparse point 边界；第一期文本文件，复杂 Office/PDF 后续 Provider 化。
6. 实现逐次、会话和持久规则审批；高风险动作二次确认，远端无人值守节点只允许管理员 allowlist。
7. 增加剪贴板、通知、打开文件/应用等窄 capability，禁止退化为万能 IPC。
8. 为每次本机执行创建本地 Action/Attempt/Evidence journal 和有界审计 outbox；高风险 Evidence 无法持久化或同步策略要求在线时 fail-closed。
9. 远端文件 grant 必须在目标节点由用户/管理员创建，服务器只保存 opaque grant ID/范围摘要；定义过期、撤销和节点本地管理 UI。

#### E2 独立验收与退出条件

- Windows PowerShell 与 macOS zsh 分别通过安全命令、流式输出、拒绝、取消、超时和整棵进程树回收。
- 本地 `read/write/edit` 仅能访问授权目录；路径穿越、软链接/reparse point 逃逸和 revision 冲突全部拒绝。
- write intent、result 和 Evidence 满足 persist-before-effect；未知写操作不自动重试。
- renderer/LLM 不能设置 shell executable、基础 env 或任意 cwd。

### E3：Provider 信任与权限（执行阶段 8）

1. 第一方 Provider：受信 catalog、签名/摘要、受控捆绑或安装、版本兼容和更新来源。
2. 第三方 Provider：支持标准 MCP stdio/config 的显式本地添加；区分管理员批准与用户添加，并为服务端建立带 namespace/发布者/schema digest 的租户审批快照。
3. 云端/LLM 永远不能提供 Provider executable、Host 启动参数/env、安装 URL 或任意本地路径；`local_shell.command` 是唯一受控例外，cwd 必须使用授权目录引用。
4. Provider/工具默认权限、写操作授权、并发/输出/超时上限、`unknown` 禁止重试。
5. 第三方升级或 schema digest 变化后重新授权；不并入 Desktop 主更新信任链。
6. Provider 安装配置 canonicalize command/args/env；校验目录 owner/ACL、签名/hash、quarantine 与 TOCTOU，采用版本并存、原子切换、失败 quarantine/回滚。
7. 本地审批仅满足服务端 policy obligation，持久规则只能收窄服务器授权；变参、策略/设备/schema revision 变化后强制失效。

#### E3 独立验收与退出条件

- 一个第一方 Provider 和一个测试第三方 Provider 完成添加、授权、调用、取消、升级、重新授权、禁用和移除。
- Provider/schema digest、发布者或策略变化后旧授权失效；云端不能静默安装或改变启动配置。
- crash、timeout、oversize 和恶意输出不能拖垮 Host、Electron 或聊天主链路。
- 第一方受信更新与第三方本地配置保持两条独立信任链。

### E4：多节点路由与 UI Bridge（执行阶段 9）

1. 其他电脑上的 Runtime 首版复用现有出站 HTTPS 长轮询和设备状态机；WSS 仅作 transport 优化。当前 Desktop 工具由 Coordinator 直接调用 Host。
2. Desktop 登录身份与设备 token 分离，token 绑定 tenant/user/device 并由 `safeStorage` 加密。
3. 服务端增加统一 `DeviceRouter` 和 Provider 默认节点持久化；现有 `selected` 作为迁移期通用默认，不再由 `LocalToolProxy` 直接使用 `selected[0]`。
4. 支持列出当前 Desktop 与远端 Runtime 节点，并为 Provider/工具配置本次节点、默认节点和“必须当前电脑”约束。
5. 路由顺序固定为本次显式选择 → Provider 默认节点 → 唯一匹配在线节点；多候选无规则时询问用户，不随机调度、不在失败后改投其他节点。
6. invocation 固定并审计 `device_id`、provider/tool/digest 与选择理由；取消、超时、unknown 不改变目标节点。
7. Desktop Agent Turn Service 与 Cloud Agent 的有效工具集都按 `服务端租户策略 ∩ 授权节点能力 ∩ Provider/工具授权` 计算；未经服务端审批的第三方 schema 不注入 LLM，远端 claim 时复核 device/provider/tool/digest。
8. Desktop 与远端 Runtime 均只出站连接后台，不增加 P2P、局域网发现或 Desktop 直连 Runtime 协议。
9. `LocalToolsBridge` 仅暴露列举、状态、节点选择、启停、授权、取消、诊断等窄操作，不暴露 spawn/command/path。
10. 为 Phase F 的 `DesktopLocalToolsPage` 提供节点、Provider、工具、来源、签名、权限、默认路由、最近运行和诊断 DTO。
11. 节点支持 ownership scope、active/draining/maintenance/quarantined/revoked、minimum version、capability snapshot、Provider slots 和 GUI exclusive slot。
12. Node Lease 与 Execution Lease 分离；容量 reservation+claim 原子化，服务器 reaper 独立处理 stale execution。
13. `DEVICE_OWNED` session 固定 owner/workspace，每个 invocation 固定 target；多步 GUI workflow 使用显式 affinity group，不能把整个 Task 误绑同一 device。

#### E4 独立验收与退出条件

- 当前 Desktop 与至少一个远端 Runtime 可被列出、选择并按审计理由固定目标节点。
- 路由严格遵循显式选择 → Provider 默认 → 唯一匹配节点；多候选无规则时询问用户。
- 远端离线、取消、超时或 unknown 不改投当前电脑，也不随机选择其他节点。
- BOSS/weixin 可绑定不同节点，旧 `selected` 迁移兼容且 `LocalToolProxy` 不再直接取 `selected[0]`。

### E 阶段整体验收

- BOSS 或 weixin 第一方 Provider 分别完成：Desktop Coordinator → 当前 Desktop Local Host，以及 Desktop Coordinator → Cloud invocation → 远端 `agent-tool-runtime` → MCP stdio → result 真机闭环；Web Cloud Agent 的既有链路保持兼容。
- Windows PowerShell 与 macOS zsh 分别完成安全命令、流式输出、审批拒绝、取消、超时、输出截断和子进程回收真机闭环。
- 本地文件工具只能访问已授权目录；越界、软链接/重解析点逃逸和未确认上传均被拒绝。
- 同一 `read/write/edit` contract 对 ServerFileRef 与 DeviceFileRef 运行 golden tests；revision 冲突不覆盖用户修改，Web/渠道服务端文件链路保持兼容。
- UI 明确区分“文件在本机执行”和“内容片段会送往云端模型”，不得把本地副作用宣传为内容完全不离机。
- 选择远端节点后，当前 Desktop 不启动 Provider、不抢占鼠标键盘；远端节点离线/失败不自动转回当前电脑。
- 一个测试第三方 Provider 完成添加、禁用、逐工具授权、调用、取消、升级后重新授权和移除。
- `SERVER/LOCAL_REQUIRED/EITHER`、多设备选择、能力不匹配和无静默跨节点 fallback 测试通过。
- 从旧 `selected` 设备迁移后行为兼容；BOSS 与 weixin 可分别绑定不同节点，路由选择有审计记录。
- Desktop Host 与 headless Runtime 对同一 invocation golden contract 结果一致。
- Provider crash/timeout/oversize/app quit 后无残留；服务端工具和聊天仍可用。
- Windows kill-on-job-close/breakaway 与 macOS 禁止 daemonize/PID+create-time ownership 通过；严禁按进程名全局清理。
- Windows 与 macOS 至少各完成 Provider 启动/取消/退出的开发真机证据。

### 分阶段估算

- E1 Host Core 与 Runtime 兼容：7～10 个工作日。
- E2 Shell/File/System Executor：8～12 个工作日。
- E3 Provider 信任与权限：6～10 个工作日。
- E4 多节点路由与 UI Bridge：6～10 个工作日。

四个阶段必须分别形成可运行闭环并独立执行三智能体流程。E1 使用测试 Provider；E2 完成本机 Executor；E3 完成第一方和测试第三方 Provider；E4 最后接入远端节点和 Desktop 管理 UI，避免同时调试执行、供应链和分布式路由。

## 10. Phase F：Agent 工作台页面

按用户价值和复用难度分小 Phase，每项独立三智能体流程：

| 子 Phase | 页面 | 策略 |
|---|---|---|
| F1 | Task 工作台 | 目标、完成标准、状态、阻塞、审批、关联 sessions/executions/artifacts，以及会话创建/搜索 |
| F2 | 我的数字员工 | Shared DTO/API，Desktop 卡片/筛选 UI |
| F3 | 知识库 | Shared API，Desktop 文件/知识库 UI |
| F4 | 本地工具 | 当前/远端节点、Provider 来源/签名/版本、默认路由、工具权限、运行状态、最近执行与诊断 |
| F5 | 设置中心 | 账户、主题、缩放、更新、存储、诊断 |

依赖与并行规则：

- F1～F3 在 D2 的会话/API contract 稳定后即可实施，不等待 E 全部完成。
- F4 依赖 E4 的节点、Provider、权限和诊断 DTO，不得用临时私有接口抢跑。
- F5 的账户、主题、缩放可提前；更新、存储和诊断部分在 G contract 稳定后收口。

复杂业务管理页不自动纳入。每页先判断：高频桌面使用则实现 Desktop 页面；低频管理操作则受控打开 Web。外部 Web URL 必须由服务端/包内策略给出并经过 HTTPS/host allowlist。

### 验收

- Desktop router 只包含已实现页面，未实现入口显示明确说明或外部打开，不展示空壳。
- 每个页面具备 loading/empty/error/offline/permission 状态。
- 页面不引用 Web layout、route、global CSS。

### 估算

10～15 个工作日，按 F1～F5 分批交付。

## 11. Phase G：平台能力与生命周期收口

### 工作包

1. 将 Electron main 拆为跨平台 core + Windows/macOS driver，保持 preload contract 不变。
2. Command Registry 统一菜单、快捷键和命令面板，显示 Ctrl/Command 差异。
3. 明确关闭/退出/托盘策略；默认不静默常驻，用户可配置。
4. 大文件下载改为流式临时文件 + 原子替换。
5. Credential 文件 schemaVersion、迁移和损坏恢复。
6. Desktop 诊断中心：版本、API 可达性、更新状态、存储/权限、本地 runtime 状态；导出脱敏诊断包。
7. 系统睡眠/唤醒、网络变化、多显示器、DPI/Retina、主题变化恢复。
8. Electron Fuses 安全加固并扩展 ASAR/启动验证。
9. 实现统一 Quiesce：停止接单、checkpoint/fsync、reconcile unknown、刷新 Evidence/Artifact outbox、关闭子进程、释放锁和租约；覆盖 pending approval/WAITING_INPUT/强杀/断电恢复。
10. 本地数据管理 UI：占用、保留、导出/删除、投影级别、诊断开关和企业策略锁定原因。
11. 遥测 envelope 只包含关联 ID、设备 hash、版本与脱敏指标；离线 buffer 有界，诊断包用户预览后导出。

### 验收

- Platform Driver contract 在 Windows/macOS 通过。
- 活跃 stream/upload/download/local run 下退出和更新行为确定。
- 诊断包不含 Token、正文、文件内容和敏感绝对路径。
- app quit 后无 Electron/Worker/Chrome 残留。
- Quiesce 超时会延后更新；睡眠/唤醒、OS 强杀和断电后先 reconciliation 再接单。
- SLO/告警覆盖 Relay ACK P95、projection lag、replay/gap、DB corruption、recovery、unknown effect、重复执行、Provider crash 与版本分布，不采集正文。

### 估算

8～12 个工作日（包含 Quiesce、数据生命周期、遥测/SLO 与崩溃恢复）。

## 12. Phase H：macOS 交付链路

### 外部准备

- Apple Developer Program。
- Developer ID Application certificate。
- Apple API Key（优先）或受控 notarization 凭证。
- Apple Silicon runner/真机；Intel runner 或受控 Intel 真机。
- 正式 `.icns` 和品牌资料。

### 工作包

1. electron-builder 增加 mac arm64/x64 的 DMG + ZIP；暂不默认 universal。
2. 配置 Hardened Runtime、entitlements 和 inherit entitlements；只申请实际使用权限。
3. 签名、notarize、staple 和 Gatekeeper 校验脚本。
4. `aidagent` URL protocol、Dock/app menu、About/Preferences、窗口恢复。
5. 建立 capability→TCC 映射，覆盖 Accessibility、Automation/Apple Events、Screen Recording、Files & Folders、麦克风和通知的 usage description、preflight 与拒绝/撤销恢复；绝不要求 Full Disk Access。
6. `latest-mac.yml`、分架构更新目录和真实旧版→新版升级。
7. macOS release manifest/SBOM/audit/license/ASAR parity。
8. 对 helper/native addon/捆绑第一方 Provider 做 nested codesign/notarization；第三方 Provider 位于 Application Support，验证 quarantine/ACL/hash/exec bit。

### 验收

- `codesign --verify --deep --strict`、`spctl --assess`、stapler validate 通过。
- DMG 安装、首次启动、深链、登录、对话、附件、权限、更新和卸载数据策略通过。
- arm64 必须真机；x64 在全量发布前必须真机或受控 Intel runner 验收。
- 未签名/未 notarize/metadata 缺失的 release 命令 fail-closed。

### 估算

5～8 个工作日，不含证书采购等待。

## 13. Phase I：Windows 正式发布闭环

### 已有

- NSIS、electron-updater、development-unsigned、release fail-closed、manifest/SBOM/audit/license 和 0.0.2 开发包证据。

### 待完成

1. 正式 `.ico`、Authenticode 证书与受控 CI Secret。
2. 生产 HTTPS Generic Provider 和原子发布流程。
3. Windows 10/11：全新安装、覆盖升级、跳版本、降级拒绝。
4. 错误 publisher、损坏包、混用 metadata/blockmap 拒绝。
5. staged rollout 和更高 SemVer 修复版本演练。
6. SmartScreen/publisher 展示与企业代理网络验证。
7. manifest 固定 `asInvoker`，验证 Controlled Folder Access、Defender、AppLocker/WDAC、代理证书及 UIAutomation/input injection/screen capture 的阻断恢复。
8. 增加 Authenticode timestamp、证书续期/紧急轮换和 publisher 迁移 runbook。

### 验收

- Authenticode `Valid`；publisherName、latest.yml、app-update.yml 和安装包一致。
- 更新过程中有活跃任务时不会强制退出。
- 发布脚本失败不遗留可误认的正式产物。

### 估算

3～5 个工作日，不含证书/发布源等待。

## 14. Phase J：Browser Runtime

### 进入条件

Browser Phase 3R 的 PostgreSQL lease、确定性人工需求、跨事件循环隔离和双 Gunicorn worker 真实 E2E 全部有证据；仅代码合并不满足。

### 工作包

- `clients/agent-desktop/browser-runtime` 内置可选模块，不建第二产品。
- 短期 browser session、`browser/1.0`、Worker/Profile、人工接管和 Web launch ticket。
- Windows Job Object、macOS process group。
- Desktop UI 状态、继续/取消、权限和更新延期。

### 验收

- local/remote executor golden contract。
- success/error/cancel/timeout/app quit/断网/update 七终态零残留。
- runtime disabled/missing/crash/version mismatch 时 Agent 主链路全绿。

### 估算

8～12 个工作日，进入条件未满足前不排期实现。

## 15. Phase K：全量验收与收口

### 自动化

- Frontend Web/Desktop/Shared 全量测试和双 production build。
- Electron unit/integration/package tests；artifact/ASAR/security/supply-chain 门禁。
- Python 受影响 contract/integration 与服务启动检查。
- MCP Host、第一方/第三方 Provider 全量 contract/lifecycle/security；Browser 若启用则增加其专项门禁。
- Task/Execution/Action/Artifact/Evidence envelope、authorization ticket/claim 分离、fencing、固定 release/policy snapshot 的跨语言 golden contract。
- Session-sync/crash Golden Matrix：ACK 前后断网、ACK 丢失、重复/乱序/gap、双连接、睡眠、强杀/断电、磁盘满/DB corrupt、Keychain locked、effect 后崩溃和服务端滚动升级。
- 更新数据库迁移、备份点、migration marker、post-update health check、只读 safe mode 与禁止不兼容降级。

### 真机矩阵

| 场景 | Windows 10 | Windows 11 | macOS arm64 | macOS x64 |
|---|---:|---:|---:|---:|
| 安装/首次启动/登录 | 必须 | 必须 | 必须 | 必须 |
| 对话/SSE/多会话/附件 | 必须 | 必须 | 必须 | 必须 |
| 深链/菜单/快捷键/更新 | 必须 | 必须 | 必须 | 必须 |
| 断网/代理/睡眠唤醒 | 抽样 | 必须 | 必须 | 抽样 |
| 多显示器/DPI/Retina | 抽样 | 必须 | 必须 | 抽样 |
| MCP Provider/本地工具 | 必须 | 必须 | 必须 | 构建+真机抽样 |
| Browser Runtime（若启用） | 必须 | 必须 | 必须 | 构建+抽样 |
| 本地 DB/恢复/磁盘满 | 必须 | 必须 | 必须 | 构建+真机抽样 |
| GUI/RPA 系统权限拒绝与撤销 | 必须 | 必须 | 必须 | 真机抽样 |

### 文档收口

- Windows 与 macOS 分平台构建/安装/运维/更新/故障手册。
- 用户手册和隐私/本地数据说明。
- release checklist、回滚 runbook、支持矩阵和已知限制。
- 支持 runbook 覆盖投影卡住、在线但 session unavailable、事件 gap、DB 损坏、密钥不可解、crash loop、unknown action 和旧协议客户端。
- 更新 `docs/ideas.md`；完成定义全部满足后移至 `docs/ideas_finished.md`。

### 估算

10～15 个工作日。K 是独立 RC/GA 阶段，不得隐含在 H/I 的发布实现工期内；发现 P0/P1 时回到对应执行阶段修复并重新进入 RC，而不是在验收分支直接堆叠补丁。

## 16. 分级发布门槛

| 级别 | 最小范围 | 允许用途 | 不允许宣称 |
|---|---|---|---|
| Internal Alpha | A～C | 开发团队验证独立 Shell、登录、更新提示和 Web 无回归 | Desktop Agent 核心可用 |
| Engineering Beta | D1～D3 | 内部真实会话、Coordinator、投影与恢复演练 | 本地工具正式可用 |
| Tool Beta | E1～G | 受控租户试点本机/远端工具、工作台和诊断 | 双平台正式发布 |
| Signed RC | H/I 完成 | 签名安装包、真实升级/回滚、候选版本真机验收 | GA 或全部完成 |
| GA | K 完成 | 正式发布 | — |

任何级别都不得降低安全门禁。Beta 仅限制用户和能力范围，不允许通过静默 fallback、弱化授权、复用 Web 管理入口或跳过恢复语义来缩短周期。

## 17. 资源、排期与里程碑

### 17.1 最低资源假设

- 1 名前端/Desktop renderer 工程师。
- 1 名 Electron/本地 Host/发布工程师。
- D1、D3、E4 期间至少 0.5～1 名后端/平台工程师，负责 Python API、PostgreSQL、Relay、PDP、滚动升级和可观测性。
- macOS arm64 真机/runner 从 Phase C 可用；Windows 10/11 真机或受控 VM 从 Phase C 可用。
- Apple/Windows 证书、生产更新源和 CI Secret 的申请与审批不计入编码工期，但必须从 B/C 启动并单独跟踪阻塞状态。

如果没有后端/平台投入，D1/D3/E4 不得按下表承诺日期，应先降低并发、重新估算，不能把服务端工作隐含分配给前端或发布工程师。

### 17.2 建议排期

在上述资源具备、每个执行阶段严格三智能体、H/I 并行且 F1～F3 适度并行的前提下：

| 里程碑 | 包含 Phase | 预计 |
|---|---|---|
| M0 工程与独立桌面壳 | B～C | 2～3 周 |
| M1 Desktop Agent 核心 | D1～D3 | 6～8 周 |
| M2 本地工具与产品化 | E1～G | 9～13 周，可与 F1～F3 部分并行 |
| M3 双平台正式交付 | H/I + K | 4～6 周 + 外部等待 |
| 可选 Browser 增强 | J | 2～3 周，满足门禁后独立触发 |

主线日历时间目标为 18～26 周，取决于 D3 故障矩阵、E3 Provider 供应链、macOS 设备和签名审批。该范围不是固定承诺：每个里程碑结束后依据实际 throughput、未关闭风险和外部阻塞滚动更新。

建议先在 Windows 开发版完成 Desktop UI 与 MCP Host 的单 Provider 闭环，同时从 C 开始保持 macOS driver/build smoke；D2 稳定后并行推进 F1～F3，E4 稳定后完成 F4；双平台正式版包含 MCP Host，Browser Runtime 按服务端门禁另行启用。

## 18. 不允许的范围漂移

- 不为了 Desktop 整洁批量改写稳定 Web import、页面或样式。
- 不在一个 Phase 同时做目录搬迁、Shared 大提取和 Desktop UI 重写。
- 不把 Web 页面临时复制到 Desktop 后长期保留。
- 不使用 `webSecurity=false`、`nodeIntegration=true` 或通用 IPC 赶进度。
- 不在 Windows 结果上标记 macOS 完成，不把 unsigned/unnotarized 包称为正式包。
- 不让单个 Provider 或 Browser Runtime 的故障拖垮 Electron、服务端工具或聊天主链路。
- 不因 Desktop 已内置 Host 而删除 Web/headless 场景仍需要的 `clients/agent-tool-runtime`。
- 不把 `LOCAL_REQUIRED` 误解成“当前电脑执行”；它表示授权边缘节点执行，当前 Desktop 和远端 Runtime 是同级候选节点。
- 不建立 Desktop 到 Runtime 的 P2P 私有通道；设备选择、invocation、进度和结果统一经后台中转。
- 不为第一方 CLI 增加 Desktop 私有协议；不允许云端/LLM 下发 Provider executable、Host 环境或任意路径，但受控 `local_shell.command` 是明确支持的工具参数。
