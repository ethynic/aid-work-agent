# 桌面 Agent 执行边界调研：Codex、DeepSeek Harness、Hermes

> 日期：2026-10-08
>
> 方法：官方文档 + 三个官方仓库固定 commit 的源码阅读；未运行产品、调用模型或压测。
>
> 关联：[桌面设计 v3](../system/desktop-agent-client-design.md)、[桌面计划](../plans/plan-desktop-agent-client.md)、[Runtime 插件宿主](../system/runtime-plugin-host-architecture-design.md)

## 1. 针对本项目的结论

**保留云端 AgentRunner 作为各入口共用的推理与任务核心，让同一 Runner 使用服务器或指定客户机的执行能力。** 桌面 UI、任务权威、模型服务和工具位置应分别设计。

用户本轮明确：AgentRunner 是企业核心，桌面也必须接入；已有 Runtime 用于客户机软件操作与工具执行。这已确定本次路线，不继续把本地 loop 作为首期悬而未决的选项。

三个项目提供的主要启发是：同核心多入口、执行环境独立于 loop，以及一次程序完成更多确定性工作。文件遍历、搜索、解析、聚合和生成可在客户机完成，只将必要结果交给模型。

需要纠正一个前提：loop 在本机并不会使云端模型无法计费，模型 API 的认证、配额和账单仍可由服务端实施。本项目保留云端 loop 的更强理由是共用已有租户授权、数字员工、任务生命周期、恢复和费用账本，而不是本地 loop 技术上不能计费。

## 2. 对象与证据口径

| 对象 | 官方仓库与固定版本 | 核对重点 |
|---|---|---|
| Codex | [openai/codex @ ea27864f99f0b086cec2f9f0251b7190fb9844f1](https://github.com/openai/codex/tree/ea27864f99f0b086cec2f9f0251b7190fb9844f1)，commit 日期 2026-10-08 | session/turn、ModelClient、执行环境、exec-server、app-server 与官方托管 harness 文档 |
| DeepSeek Harness | [deepseek-ai/deepseek-harness @ 5badb15009ae1756c3afe0ae0cef1faafc290ccc](https://github.com/deepseek-ai/deepseek-harness/tree/5badb15009ae1756c3afe0ae0cef1faafc290ccc)，commit 日期 2026-10-03 | Desktop Host、agent-loop、fs/subprocess/sandbox、调度、PTC、usage |
| Hermes | [NousResearch/hermes-agent @ dde8800ed91c6e128064a17d5db914d74622594b](https://github.com/NousResearch/hermes-agent/tree/dde8800ed91c6e128064a17d5db914d74622594b)，commit 日期 2026-10-08 | 同核心多入口、桌面 headless backend、文件/终端后端、execute_code、usage、安全 |

只引用官方源码与文档。默认分支会变化，源码判断以固定版本为准；托管服务是文档证据，不称其内部实现已公开。未检查全部模块，未证明这些项目具有本项目的多租户隔离或不可撤销动作恰好一次保证。

## 3. Codex：harness 与 execution environment 分开

### 3.1 本地模式

官方说明 app-server 提供 thread、turn、事件和审批，应用可连接本地 Codex 进程；开源 harness 与模型访问、托管服务独立。这支持“本地 harness + 远端模型”，不表示模型权重在客户端内运行。[官方介绍](https://developers.openai.com/blog/codex-as-a-platform)

源码核对：

- [`session/turn.rs`](https://github.com/openai/codex/blob/ea27864f99f0b086cec2f9f0251b7190fb9844f1/codex-rs/core/src/session/turn.rs)：`run_turn` 建立 turn 级模型会话，经 sampling 与 ToolCallRuntime 推进后续步骤，模型完成事件消费 token usage。
- [`client.rs`](https://github.com/openai/codex/blob/ea27864f99f0b086cec2f9f0251b7190fb9844f1/codex-rs/core/src/client.rs)：ModelClient 保存 session 级认证/provider/连接，ModelClientSession 保存 turn 级流式状态；认证所有权变化清理连接与增量状态。
- [`environment_selection.rs`](https://github.com/openai/codex/blob/ea27864f99f0b086cec2f9f0251b7190fb9844f1/codex-rs/core/src/environment_selection.rs)、[`unified_exec.rs`](https://github.com/openai/codex/blob/ea27864f99f0b086cec2f9f0251b7190fb9844f1/codex-rs/core/src/tools/runtimes/unified_exec.rs)：工具携带 TurnEnvironment、环境 ID、权限和网络策略，也处理远端环境。

借鉴：UI 调相同核心；任务设置显式传递；工具按照 execution environment 路由。不能照搬让企业客户端持有模型供应商凭证的方式。

### 3.2 托管 harness + 客户环境 executor

官方 self-hosted sandbox 文档明确：harness 在 OpenAI，客户环境运行 `codex exec-server`，通过出站连接收命令、回结果；应用 key 与受限环境 key 分开。[官方文档](https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted)

这在结构上直接支持我们的 **云端 Runner + 本机 Runtime**，但不证明现有设备协议已有相同传输和安全保证。

[`exec-server/README.md`](https://github.com/openai/codex/blob/ea27864f99f0b086cec2f9f0251b7190fb9844f1/codex-rs/exec-server/README.md) 提供 process/start/read/write/terminate、PTY/输出和文件 RPC；进程与文件都在 executor。文档明确 forwarding 不重放请求、不持久保存执行状态，恢复依赖目标会话/输出保留；直连也有断连终止受管进程规则。因此重连不等于任意写动作可以重试。

我们的 claim、attempt、journal 和原结果核对仍需保留，快速连接不能替代副作用账本。

## 4. DeepSeek Harness：可组合能力和 PTC

### 4.1 核心与 Desktop Host

[`architecture.md`](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/docs/architecture.md) 将 agent-loop、LLM、tools、session、sandbox 分成 Cordis 插件。Desktop 在 Electron 中启动 Host，由共享 profile runner 装配 runtime；UI 经认证 Host 的 RPC/流式连接消费结果。不是 renderer 自写模型循环。

[`desktop-host/src/index.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/apps/desktop-host/src/index.ts) 实际调用 `runProfile`，处理启动、退出与控制；其 Electron Node 模式有专门封装，不能把 `process.execPath` 不加区分复制到我们的 Provider 启动代码。

借鉴核心/Host/UI 分层和按部署组合能力。不建议将本项目全部换成 Cordis 或“所有对象都是插件”；已有 Engine、ToolDispatcher、Runtime 边界足以承接增量设计。

### 4.2 文件、子进程和沙箱

- [`fs-local`](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/fs/fs-local/README.md) 及其 [`实现`](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/fs/fs-local/src/index.ts)：文件身份、原子写和可选 version guard；local backend 不限制绝对路径/父路径访问。
- [`subprocess`](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/subprocess/subprocess/README.md)：显式 argv/cwd/stdio/env/取消、受管进程范围、有界且按 offset 可读的输出、环境凭证清理。
- [`sandbox`](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/docs/subsystems/sandbox.md)：文件效果策略与网络/进程可见性分开；平台可以报告 partial，不把部分限制宣称完整隔离。

借鉴：read 与 shell 指向同一执行世界；版本防覆盖、环境清理、进程树和输出读取独立设计。local 能力不天然等于沙箱。

### 4.3 调度与程序化调用

[`tool-calls.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/core/agent-loop/src/tool-calls.ts) 用有界池并行执行，exclusive 调用形成屏障；结果按模型顺序提交，取消停止补充派发并收尾已开始操作，保留调用结果配对。

[`ptc.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/core/tools/src/ptc.ts) 允许一次程序组合工具，只返回/打印必要内容；子调用仍经权限和调度，完整程序批准不免除子工具策略，程序不自动重放。独立 [`ptc-runtime`](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/ptc-runtime/ptc-runtime/README.md) 不理解会话/工具，执行 substrate 也不自动承诺安全边界。

本项目先做结构化 batch read/search、受信脚本入口；以后再评估完整 PTC。设备程序执行确定性步骤，不自行调 LLM 推进业务，不旁路调用企业工具；有副作用的子动作仍需许可和回执。

### 4.4 用量

[`assembler.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/llm/llm/src/assembler.ts) 收集流式 usage；[`translate.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/llm/llm-deepseek/src/translate.ts) 归一供应商用量。这不能证明它具有本项目租户积分和幂等恢复账本，不据此移除 Runner usage receipts。

## 5. Hermes：同一 Agent、多入口和工具后端

### 5.1 核心与桌面

[`architecture.md`](https://github.com/NousResearch/hermes-agent/blob/dde8800ed91c6e128064a17d5db914d74622594b/website/docs/developer-guide/architecture.md) 描述 CLI、Gateway、ACP、API 使用 AIAgent，循环位于 conversation_loop 与 turn 模块。[Desktop 文档](https://github.com/NousResearch/hermes-agent/blob/dde8800ed91c6e128064a17d5db914d74622594b/website/docs/user-guide/desktop.md) 描述应用启动 `hermes serve` headless backend，通过 tui_gateway JSON-RPC/WebSocket 使用同一 runtime，也支持远端 backend。

借鉴同核心多入口与 backend/UI 分离。个人 profile、SQLite 和 API keys 共享模式不能直接变成本项目多租户会话与凭证治理。

### 5.2 文件和终端共用任务环境

[`file_tools.py`](https://github.com/NousResearch/hermes-agent/blob/dde8800ed91c6e128064a17d5db914d74622594b/tools/file_tools.py) 的 `_get_file_ops`、`_create_terminal_env_for_file_ops` 从同一终端配置及 task overrides 创建环境；文件先于命令执行也使用该后端，并处理宿主 cwd 与容器 cwd 混淆。

[`environments/base.py`](https://github.com/NousResearch/hermes-agent/blob/dde8800ed91c6e128064a17d5db914d74622594b/tools/environments/base.py) 管理命令、cwd、进程与等待。后端可本地、容器或远端；持久化/取消能力由实际后端决定，不因共用接口就有一致恢复保证。

我们的 read/edit/shell/skill 应共用固定设备与 workspace。不能让 read 取客户机文件，而 shell 默认访问云端同名路径。

### 5.3 execute_code

[`code_execution_tool.py`](https://github.com/NousResearch/hermes-agent/blob/dde8800ed91c6e128064a17d5db914d74622594b/tools/code_execution_tool.py) 允许一次 Python 程序经 RPC 组合工具，把中间数据处理留在程序中，只回精选输出；本地 kernel 与远端方式分别适配，不等于所有部署都在 UI 机器执行。

[`code_execution_rpc.py`](https://github.com/NousResearch/hermes-agent/blob/dde8800ed91c6e128064a17d5db914d74622594b/tools/code_execution_rpc.py) 验证 RPC token、允许工具和调用预算。借鉴其组合效率，同时限制本地可用工具；云端业务 API 继续走企业授权。

### 5.4 usage 与安全

[`turn_usage.py`](https://github.com/NousResearch/hermes-agent/blob/dde8800ed91c6e128064a17d5db914d74622594b/agent/turn_usage.py) 消费 response.usage，累计 token/cost 与状态库增量；[`billing_usage.py`](https://github.com/NousResearch/hermes-agent/blob/dde8800ed91c6e128064a17d5db914d74622594b/agent/billing_usage.py) 从 Nous Portal 查询账户额度。客户端统计和服务商账户视图分离，不表示客户端自报能成为服务端扣费权威。

[`SECURITY.md`](https://github.com/NousResearch/hermes-agent/blob/dde8800ed91c6e128064a17d5db914d74622594b/SECURITY.md) 区分 OS 隔离与进程内启发式检查。我们的命令关键词过滤、cwd 或批准弹窗不能冒充 OS 沙箱，也不直接采用个人 Agent 的信任假设。

## 6. 对照归纳

| 问题 | Codex | DeepSeek Harness | Hermes | 本项目建议 |
|---|---|---|---|---|
| 多入口 | 共用 harness/app-server | 共用 profile/agent-loop | 共用 AIAgent/backend | 共用 AgentRunner |
| loop 与执行位置 | 本地 harness；另有托管 harness + 自托管 executor | loop 与 fs/subprocess provider 分层 | runtime 与 terminal backend 分层 | 云端 Runner + 指定设备 Runtime |
| 文件和命令 | environment 的文件/进程能力 | 同世界 fs/subprocess/sandbox | 同任务终端后端 | 设备/workspace 一致绑定 |
| 减少模型轮次 | Shell/程序完成确定性工作 | PTC、有界并行、独占屏障 | execute_code、工具 RPC | batch、过滤、聚合和受信脚本 |
| 用量 | 云模型响应 usage | 流式 usage 归一 | usage + Portal 额度 | 原模型网关/Runner 账本 |
| 写动作恢复 | 依实际连接/会话策略 | 日志与程序不自动重放 | 后端能力不同 | invocation、journal/outbox、效果核对 |

表格归纳已读模块，不是完整产品能力比较或安全认证。

## 7. 客户机执行场景

以下为设计建议，不是已实现能力。

| 场景 | 本机完成 | 给 Runner 的内容 | 价值 |
|---|---|---|---|
| 文件夹检索/grep/日志筛选 | 遍历、过滤、截取上下文 | 命中、受信路径引用、revision | 不上传目录/全部日志 |
| Excel/CSV 统计 | 本地解析、筛选、聚合、图表生成 | 统计、必要样本、产物引用 | 原始行不必进模型 |
| Word/PDF 阅读 | 解析、按页/标题提取 | 必要片段和图片 | 原文件可留本机 |
| 编辑/格式转换 | 版本检查、转换、原子写、校验 | diff、结果及效果证据 | 副作用落在真实目录 |
| Python/Node/PowerShell | 确切程序的循环、排序、转换、测试 | 有界输出、退出码、结果引用 | 一次派发做多步工作 |
| BOSS/微信操作 | 使用本机登录态与应用环境 | 文本/截图和动作结果 | 云端没有客户应用状态 |
| 用户预览/选目录/打开产物 | Main 窄 IPC | 无需模型则不建任务 | UI 操作不必绕 Agent |
| 数字员工/知识库/企业 API | 默认云端处理 | 原业务结果 | 复用租户授权与密钥管理 |

模型发起的本地操作来自 Runner 受权调用。“Runtime 直接访问本机”不等于 renderer 随意执行并自报成功。用户明确点击的预览等可直达窄 IPC，但不能成为模型执行旁路。

### 7.1 不上传文件的准确含义

不必先上传整个原文件到服务器存储，再调用服务端路径工具。客户机可以解析/计算，只回传模型需要的片段或统计。

云端模型要理解某段文字，该文字仍经过服务器和模型请求。云端视觉分析需要真实像素，opaque 截图引用不够。未传原文件、未上传为云端附件、内容完全不离机是不同承诺。

### 7.2 程序循环与 Agent loop

一次授权程序可以执行 for、搜索、解析、转换、合并和已批准的确定性动作链；Runtime 不根据新模型回答自主决定业务，也不创建新的 runner。

需要新模型判断、澄清或子智能体时回到原 Runner。普通 SKILL.md 是模型手册，不等于可本地执行的完整流程；只有实际实现的脚本/流程入口才能一次执行。

## 8. 保留 Runner 如何提高效率

### 8.1 先减少轮次和输出

总耗时由模型、工具、跨设备往返、持久化/排队组成，未测量前不判断谁占主导。将“逐个读 100 个文件，每次回模型”改成结构化 batch read/search 或一次本机脚本，只回命中和必要原文。脚本做确定性筛选，不暗中调用模型摘要；模型总结仍由 Runner 记账。

并行适用于明确无互斥的读/纯计算；应用操作、同文件写、共享桌面资源和需审批动作保留屏障。批内逐项失败必须可见，不能只返回一项总 success。

### 8.2 再优化设备传输

首版复用现有 local-tools claim/progress/result，先获得真实延迟；不立即换掉客户协议。若高频小文件读取的往返实测成为瓶颈，可给同一设备执行端口增加认证连接复用/批处理：

- 读调用可重试，仍绑定任务、设备、grant/版本、调用 ID，并限制超时与输出。
- 写、发送、Shell 等效果未知操作保留派发前持久事实和 journal/回执；快传输不减授权和恢复责任。
- 设备不能经新端口创建业务 runner、自行审批或替代服务端费用账本。
- UI SSE 与设备执行连接分离；关观察页面不影响调用归属。

读也要校验权限并记录任务工具结果，但不机械套用所有领域 v2 写的重型许可状态机。快路径须由测量推动，不提前承诺毫秒性能。

### 8.3 测量建议

后续记录模型耗时、接单、invocation 排队/领取、Provider 启动/执行、回执接纳、字节、轮数与 token；比较逐项调用与 batch 的同等正确结果。负载覆盖文本检索、CSV 聚合、数十次小文件读取和长脚本。当前报告没有这些测量值。

## 9. 仓库的最小调整方向

1. Desktop 接既有公开 Runner API、会话和控制，不启用旧 Agent Turn/Remote Tool Gateway。
2. 在工具应用层补 execution environment 引用，固定设备/workspace、授权版本与能力；不给 Engine 增桌面分支。
3. Runtime 提供文件与受控程序能力，文件/命令/skill 共用任务环境；云端资源空间保留。
4. 共用[Runtime 插件宿主](../system/runtime-plugin-host-architecture-design.md)的配对、生命周期和安装能力；独立 Runtime 与完整 Desktop 可不同发行，执行机制相同。
5. 优先 batch 与受信脚本，PTC 后续评估，不新建通用工作流 DSL 或本地自治模型循环。
6. Runner 保持历史、模型用量、恢复和任务权威，设备记录副作用证据并补传结果。

旧 selected 路径兼容客户；新任务使用固定 binding，不能 select 一次就当作永久绑定。Host 提取以实际复用为准，不创建并行调度器。

## 10. 未验证范围

本轮未运行三家产品，没有证明沙箱、性能、账户权限或任意副作用恢复保证。本项目文件、通用命令、固定设备/目录与设备快路径仍未开发。

实现前需定稿 grant、审批、文件/进程与效果证据；Windows 真机验证后才承诺能力。首场景和 Shell 首发范围仍可调整，不改变已确认的云端 Runner 核心。若实际复用开源代码，另行核对许可证、依赖与维护范围。
