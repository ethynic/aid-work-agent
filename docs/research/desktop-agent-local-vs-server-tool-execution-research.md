# Agent Desktop 本地与服务端工具执行调研

> 日期：2026-08-12
>
> 关联设计：[Agent 跨平台桌面客户端设计](../system/desktop-agent-client-design.md)

## 1. 结论

Agent Desktop 必须在客户端原生 Host 中实现本地文件、命令和系统工具。用户选择本机文件或工作目录后，`read/write/edit` 的文件副作用应发生在对应设备，不应先把文件上传服务器再用服务端路径工具处理。

但服务端现有 `read/write/edit` 不能删除。Web、企业微信/钉钉/飞书渠道、后台任务、上传文件、云端技能和生成产物仍需要服务端工作区。长期架构应是：

- 模型看到一套稳定的文件工具语义；
- 服务端与客户端分别实现执行器；
- 根据受信 `FileRef` 的数据位置路由，而不是根据“Web/Desktop 页面”或 LLM 自报位置路由；
- 不复制 Python 服务端工具到 Vue renderer，本地实现位于 Desktop/Runtime Host。

## 2. 竞品公开事实

### 2.1 Codex

OpenAI 官方 Codex CLI 文档明确说明 Codex 可以检查本地仓库、修改文件并运行机器上已安装的工具；同时允许用户控制权限和命令。配置参考把命令执行 sandbox 分为 `read-only`、`workspace-write`、`danger-full-access`，并提供 writable roots、网络访问和 shell 环境变量策略。

这说明其本地产品形态不是把文件读写发送到远端服务器文件系统执行，而是在本地 runtime 中执行受权限约束的文件和 Shell 操作。Codex 也另有 cloud environment，证明本地与云端是两种执行环境，而不是只保留其中一种。

来源：

- [OpenAI Codex CLI](https://learn.chatgpt.com/docs/codex/cli)
- [OpenAI Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)

### 2.2 腾讯 WorkBuddy

WorkBuddy 官方文档明确说明：

- 工作空间是任务主要读取和保存文件的本地文件夹；
- 客户端可以读写文件、执行脚本、命令和外部程序；
- 默认权限对工作空间外、高风险写入、删除、脚本和网络操作要求确认；
- 即使从微信远程发起任务，实际仍由电脑上的 WorkBuddy 使用本地文件、Shell、凭证、插件和工具执行；
- 文件处理默认在本地，服务端只处理需要的片段。

来源：

- [WorkBuddy 默认权限与安全沙箱](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Permission-Modes)
- [WorkBuddy 任务对话与文件边界](https://www.workbuddy.cn/docs/workbuddy/Conversation)
- [WorkBuddy 微信远程执行说明](https://www.workbuddy.cn/docs/workbuddy/Wechat-Guide)

## 3. 当前项目事实

当前 `src/tools/file/` 的三个工具均继承 `BaseTool` 默认值，因此执行位置是 `SERVER`：

| 工具 | 当前文件空间 | 现有安全边界 | 结论 |
|---|---|---|---|
| `read` | 服务器项目根目录或系统临时目录 | 路径 resolve 后限制在上述目录；支持 Word/Excel/PPT 服务端解析 | 保留为 Cloud Workspace reader |
| `write` | 服务器 `storage/output` 或临时目录 | 后缀白名单、覆盖控制；随后通过 `cp` 注册下载 | 保留为云端产物生成器 |
| `edit` | 服务器项目根目录或临时目录 | 唯一匹配、先校验后写、临时文件原子替换 | 保留为 Cloud Workspace editor |

它们不能读取用户 Desktop 的磁盘路径。把 `C:\Users\...` 从 Desktop 直接传给服务端既无效，也会混淆服务器路径与用户路径。

## 4. 推荐的双执行器模型

### 4.1 文件空间

文件必须先有明确归属：

| 文件空间 | 典型来源 | 执行节点 |
|---|---|---|
| `server_workspace` | Web 上传、渠道附件、云端生成文件、服务端 skill 临时产物 | 服务端 Python ToolExecutor |
| `device_workspace` | 用户在 Desktop 选择的文件夹、当前电脑项目、远端 Runtime 的授权目录 | 对应 `device_id` 的 Local File Executor |
| `server_artifact` | 已登记 `file_id`、知识库对象、可下载交付物 | 服务端对象/文件存储工具 |

“Desktop 发起任务”不等于一定本机执行；“Web 发起任务”也不等于一定服务端执行。路由只看资源位置、授权与所选设备。

### 4.2 统一 FileRef

长期不应把裸绝对路径作为跨网络工具参数。建议统一使用受信引用：

```json
{
  "scope": "device_workspace",
  "device_id": "device_xxx",
  "grant_id": "grant_xxx",
  "relative_path": "reports/weekly.md",
  "revision": "sha256:..."
}
```

服务端引用则使用 `workspace_id/file_id + relative_path/revision`。`device_id`、`grant_id` 和 scope 来源于登录上下文、文件选择器或服务端 registry；LLM 可以引用，但不能伪造出授权。

### 4.3 一套语义，两类实现

模型层继续使用 `read/write/edit` 语义，内部通过 `FileToolRouter` 分派：

```text
read/write/edit
      │
      ├─ ServerFileRef ──> 现有 Python ServerFileExecutor
      └─ DeviceFileRef ──> invocation ──> Desktop/agent-tool-runtime LocalFileExecutor
```

不建议长期暴露 `server_read`、`local_read` 两组同义工具，否则模型容易选错，并把执行位置当成推理参数。迁移期可以使用内部 alias，但最终 tool schema 应统一。

### 4.4 客户端实现位置

- `frontend/desktop`：选择工作空间、展示 diff、授权和执行状态；不直接访问文件系统。
- `clients/shared/local-tool-host-core`：FileRef、授权目录 registry、路由 contract、审计、取消和结果模型。
- `clients/agent-desktop`：Windows/macOS 本机 File/Shell adapter、safeStorage 和进程监管。
- `clients/agent-tool-runtime`：远端节点复用同一 LocalFileExecutor；只访问该节点批准的目录。
- `src/tools/file`：继续承载服务端实现，通过统一 contract 适配。

## 5. read/write/edit 的本地语义

| 工具 | 本地要求 |
|---|---|
| `read` | 只读授权目录；分页/大小上限；编码检测；软链接/Windows reparse point 不能逃逸；返回 revision |
| `write` | 授权目录内创建；默认不覆盖；临时文件 + fsync/rename 原子提交；返回新 revision 和 diff 摘要 |
| `edit` | 必须携带读取时 revision 或内容 hash；不一致返回 conflict，禁止覆盖用户同时发生的修改；原子替换并返回 diff |

Office/PDF 等格式不要在第一期复制全部 Python parser。先支持文本文件；复杂格式由标准第一方 Local Document Provider 逐步补充，或由用户明确选择上传服务器处理。

## 6. 路由规则

1. 用户选择本地工作空间后生成 `DeviceFileRef`，文件工具固定路由到该设备。
2. Web/渠道上传得到 `file_id`，继续在服务端处理。
3. 云端生成产物默认进入 `server_artifact`；用户选择“保存到此电脑”时，再创建本地 write invocation。
4. 本地文件不得为方便而隐式上传；需要服务器或模型读取内容时，UI 必须说明数据会传输。
5. 本地执行只代表文件副作用发生在设备。当前 Agent/模型仍在云端时，`read` 返回的内容片段会经过后台和模型；真正的“内容不离机”需要未来本地模型/本地推理模式，不能通过文案混淆。
6. 任务一旦选择执行节点和 FileRef，不因失败静默切换位置。

## 7. 最终判断

用户的判断是正确的：Agent Desktop 应有客户端文件工具执行器。需要修正的只有“替代服务器工具”这一点——不是替代，而是形成同一工具契约下的本地/服务端双实现。这样 Desktop 具备 Codex/WorkBuddy 类本地 Agent 能力，同时不破坏稳定 Web、渠道接入和云端产物链路。

## 8. Agent Kernel 是否应在本地

### 8.1 本地工具不构成逻辑上的强制条件

云端 Agent 也能通过 WSS/HTTPS 把调用发送给本地 Runtime，因此“工具在本地”不必然要求完整 Agent 在本地。中转增加的是一次设备网络往返和调度持久化开销：

- BOSS/weixin、Office 生成等秒级或分钟级工具，额外网络开销通常不是主导；
- `read/grep/edit` 这种高频、毫秒级、小步循环，连续中转会明显影响交互；
- 模型仍使用 Qwen/ZhipuAI 云服务时，即使 Agent loop 在本地，每轮模型推理依旧需要网络，不能因此宣称离线或零网络延迟。

### 8.2 三种方案

| 方案 | 优点 | 代价 | 判断 |
|---|---|---|---|
| 完整 Cloud Agent + 设备工具中转 | 最大复用现有系统，Web/渠道一致 | 高频本地工具多一跳；本地状态控制弱 | 可作为迁移兼容，不是 Desktop 最终形态 |
| 把现有 Python Agent 全量打进 Desktop | 本地工具直调 | 当前 `Agent` 约 4113 行并依赖 Redis、租户 Skill/缓存、记忆、子智能体、数据库、计费和大量 Python 工具；跨平台打包与双实现风险极高 | 否决 |
| Desktop Local Agent Coordinator + 云端决策/模型/远端工具网关 | 本地工具直调，云端治理保留，Web 不动 | 需要新 Agent Turn Protocol 和 Remote Tool Gateway | 推荐目标架构 |

### 8.3 推荐拆分

Desktop 本地 Agent Coordinator 负责：

- 一个回合的生命周期和 tool-call loop；
- 本地工作空间、FileRef、审批、取消、并发和进程监管；
- 当前设备 Local File/Shell/MCP 工具直接调用；
- 将远端节点和服务端工具调用交给后台网关；
- 本地短期 journal，断线后与服务端会话恢复。

服务器继续负责：

- 模型网关和供应商密钥；
- Prompt、租户策略、数字员工/Skill 配置、长期记忆和会话权威记录；
- 计费、审计、工具授权和有效 catalog；
- 服务端 ToolExecutor；
- 其他 `agent-tool-runtime` 节点的 invocation 中转。

建议定义两个稳定接口：

```text
POST /api/desktop/agent/next
  输入：session/version/messages/tool results/device capabilities
  输出：final | local_tool_call | remote_tool_call | clarification

POST /api/desktop/tools/invoke + events/cancel
  输入：tool name/schema version/args/idempotency key
  服务端注入：tenant/user/secret/policy
```

互联网链路首选 HTTPS JSON + SSE/WSS，复用 FastAPI、企业代理和现有认证。gRPC 可以用于内部服务或未来高吞吐节点，但它只是 transport，不是架构目标，也不应让 Desktop 直连内部工具服务。

### 8.4 执行路径

```text
本机工具：Desktop Coordinator → Local Host → result → Agent next
服务端工具：Desktop Coordinator → Remote Tool Gateway → Server ToolExecutor → result
其他电脑：Desktop Coordinator → Cloud invocation → agent-tool-runtime → Provider → result
模型调用：Desktop Coordinator → Agent Turn/Model Gateway → Qwen/ZhipuAI
```

这使当前电脑的 `read/write/edit/shell` 不再经过 invocation 表和轮询；远端 GUI 工具仍由后台中转。Web、企微、钉钉、飞书继续使用现有 Cloud Agent，不被 Desktop 架构改造牵动。
