# weixin-cli：第一方微信操作 CLI / MCP Provider 设计

> 状态：📋 设计完成，待开发
>
> 日期：2026-08-11
>
> 上位规范：[第一方 CLI / MCP Provider 架构与开发规范](../../system/first-party-cli-mcp-provider-standard.md)
>
> 第一参考实现：[BOSS CLI 接入设计](../recruiting/recruiting-cli-agent-integration-design.md) / [M0.2 实施规格](../../plans/recruiting/m02-implementation-spec.md)
>
> 已验证自动化基线：[微信搜一搜 RPA 设计](../../tools/wechat-souyisou-rpa-design.md) / [开发计划](../../tools/wechat-souyisou-rpa-dev-plan.md)
>
> 开发计划：[weixin-cli 开发计划](../../plans/weixin/plan-weixin-cli.md)

## 1. 定位与目标

`weixin-cli` 是项目第二个第一方 CLI，也是一个独立、标准、可分发的 Windows MCP Provider。它面向未来项目统一提供“操作已登录的微信 Windows 客户端”能力，供以下 Host 复用：

- 人工终端；
- aid-work-agent Web 的 Local Tool Runtime；
- 未来 aid-work-agent Desktop；
- Codex、WorkBuddy 等标准 MCP Host；
- 未来其他本地业务编排程序。

首期不是从零重做微信自动化。现有 PowerShell 已在微信 `4.1.11.24`、Windows 11 上真机验证窗口识别、激活、搜一搜、剪贴板取文、UIA 候选、详情闭环、DPAPI 证据和会话清理。新 CLI 参考这些已验证实现建立自己的独立自动化内核，并在外层补齐与 BOSS CLI 相同的 operation / human CLI / MCP / manifest / conformance 架构。

成功标准：

1. `clients/weixin-cli/` 成为未来项目使用微信操作的标准实现源；
2. 微信能力不依赖 aid-work-agent 云端、协会业务、租户或模型供应商；
3. 同一 operation 同时服务 human CLI 与 MCP，不维护两套流程；
4. 先发布已真机验证的只读能力，新增能力必须经过 probe 门禁；
5. 写动作具备精确目标、硬上限、写后校验、`unknown` 不重试；
6. 不要求已经交付的协会客户端反向迁移、升级或建立依赖。

## 2. 依据、现状与隔离边界

### 2.1 可复用基础设施与一次性复制来源

这里必须区分“长期共享依赖”和“一次性复制来源”：

- `clients/boss-resume-assistant/`：复用 Node.js 22 + TypeScript、operation 注册表、MCP stdio、manifest digest、doctor/version、单飞和取消的架构模式；不依赖招聘领域源码；
- `clients/shared/mcp-conformance/`：作为长期共享的第一方 Provider 统一契约测试；
- `clients/association-client-cli/scripts/`：作为本次微信能力的主要一次性复制来源；只读取并复制需要的 Win32/UIA/剪贴板/DPAPI 代码到 `clients/weixin-cli/`；
- `clients/wechat-souyisou-rpa/scripts/`：作为补充实验与测试参考，用来核对已验证算法和历史踩坑；
- `src/services/association_enrichment_providers.py`：只读理解现有微信查询、会话交接、失败分级和 artifact 消费语义，不修改、不迁移调用方。

复制完成后，`weixin-cli` 内的代码形成自己的实现、测试和版本线。它不会 import、链接、调用或打包协会客户端目录中的任何文件。

### 2.2 历史实现与新产品边界

现有协会客户端已经交付并自成体系。本项目不把它视为需要纠正的架构问题，也不以“消除历史复制”为目标。边界明确如下：

1. 本次只发生一次单向复制：`协会客户端已有微信能力 → weixin-cli`；
2. 不修改、不删除、不移动协会客户端的任何代码、测试、配置、文档和打包文件；
3. 不把协会微信代码抽成共享包，不创建 symlink/submodule，不让协会客户端反向调用 `weixin-cli`；
4. 复制后双方彻底独立演进，任何修复或功能都不自动双向同步；
5. 协会客户端继续使用自己的微信实现，行为和交付方式不变；
6. 未来其他项目的微信需求统一使用 `weixin-cli`，不再复制协会客户端代码。

因此，“协会客户端是否完成切换”“历史脚本是否删除”“全仓库是否单源化”都不是 `weixin-cli` 的验收项。

## 3. 架构决策

### 3.1 技术栈

- 控制面：Node.js 22 + TypeScript，与 BOSS CLI reference provider 一致；
- MCP：精确锁定与项目当前 reference provider 相同的官方 SDK 版本；
- 自动化执行面：首期继续使用 Windows PowerShell 5.1 兼容脚本；
- 打包：最终发布自包含 `weixin-cli.exe`，不要求用户安装 Node、Python 或源码；
- 平台：`win32-x64`、`LOCAL_REQUIRED`、必须运行于已登录且未锁屏的交互桌面会话。

选择 TypeScript 控制面而不立即将 PowerShell 重写为 C#，原因是当前风险集中在微信 UI 稳定性。重写会同时改变语言、进程边界和已验证输入路径，无法区分回归来源。只有当 probe 证明 PowerShell 子进程成为明确瓶颈时，才单独设计执行层迁移。

### 3.2 强制分层

```text
human CLI adapter ─┐
                   ├─> domain operations ─> WeixinAutomationDriver
MCP stdio adapter ─┘                         ├─ PowerShell kernel（首期）
                                             ├─ Win32 / UIA / Clipboard
                                             └─ ArtifactStore（DPAPI）
```

- operation 只接收结构化参数，返回统一结果；
- adapter 不复制微信操作流程；
- PowerShell driver 通过 stdin UTF-8 JSON 接收固定 schema，不接收任意脚本、cwd、env 或 raw argv；
- PowerShell stdout 只返回单行 driver JSON，TypeScript 负责映射为 Provider 结果；
- MCP stdout 仅允许 JSON-RPC，日志写 stderr 且脱敏。

### 3.3 目录

```text
clients/weixin-cli/
├─ package.json
├─ provider-manifest.json
├─ src/
│  ├─ cli/
│  │  ├─ index.ts
│  │  └─ commands/{doctor,version,mcp,souyisou,contacts,message,officialAccount,lab}.ts
│  ├─ operations/
│  │  ├─ types.ts
│  │  ├─ errorMapping.ts
│  │  ├─ registry.ts
│  │  ├─ probe.ts
│  │  ├─ souyisouSearch.ts
│  │  ├─ souyisouCollect.ts
│  │  ├─ articleRead.ts
│  │  ├─ officialAccountFollow.ts
│  │  ├─ chatSearch.ts
│  │  └─ messageSend.ts
│  ├─ automation/
│  │  ├─ driver.ts
│  │  ├─ powershellDriver.ts
│  │  ├─ session.ts
│  │  └─ targetRef.ts
│  ├─ mcp/{server,toolDefs,manifest}.ts
│  └─ security/{artifactStore,redaction,audit}.ts
├─ automation/powershell/
│  ├─ weixin-driver.ps1
│  └─ lib/*.ps1
├─ experiments/
│  ├─ README.md
│  └─ probes/<probe-id>/
└─ tests/
```

`experiments/` 不进入正式 MCP tool 注册表，也不进入默认签名发布包。

## 4. Provider 契约

### 4.1 身份与命令面

```text
provider_id: ai.aidwork.weixin
binary:      weixin-cli.exe
transport:   stdio
target:      local_required
```

标准命令：

```text
weixin-cli mcp --stdio
weixin-cli doctor [--json]
weixin-cli version --json
weixin-cli souyisou search ...
weixin-cli souyisou collect ...
weixin-cli contacts search ...
weixin-cli message send ...
weixin-cli official-account follow ...
weixin-cli lab list|run ...        # 仅开发包/人工实验，不注册 MCP
```

`doctor` 只能读取：平台和交互会话、微信进程/版本、登录态特征、主窗口唯一性、PowerShell/依赖、DPAPI artifact 目录权限。不得激活窗口、发送按键、改剪贴板或打开搜一搜。

### 4.2 统一结果

沿用项目规范：

```json
{
  "success": true,
  "code": "OK",
  "message": "操作完成",
  "effect": "none|applied|partial|unknown",
  "data": {},
  "retryable": false,
  "run_id": "uuid"
}
```

微信领域额外约定：

- `data.session_closed`：会创建临时插件窗口的 operation 必须返回；
- `data.artifact_ref`：敏感或大体积证据只返回本地不透明 handle；
- `data.target_ref`：好友、群或公众号的短期不透明目标 handle；
- 不把手机号、聊天正文、联系人完整列表、群成员或剪贴板原文写入普通日志；
- 公开公众号文章正文可作为 tool data 返回，但必须有长度上限，超限落 artifact。

### 4.3 稳定错误码

| 类别 | code | 语义 |
|---|---|---|
| 输入 | `INVALID_ARGUMENT` | schema、范围或组合非法 |
| 环境 | `WINDOWS_REQUIRED` / `INTERACTIVE_SESSION_REQUIRED` | 平台或桌面会话不满足 |
| 微信 | `WEIXIN_NOT_FOUND` / `NOT_LOGGED_IN` / `WINDOW_AMBIGUOUS` | 客户端或账号状态不满足 |
| 安全 | `FOREGROUND_LOST` / `WINDOW_UNTRUSTED` | 不能确认输入目标，立即停止 |
| 页面 | `UI_CHANGED` / `RESULT_TIMEOUT` / `CONTENT_UNAVAILABLE` | UI 或内容无法验证 |
| 目标 | `TARGET_NOT_FOUND` / `TARGET_AMBIGUOUS` / `TARGET_REF_STALE` | 不能唯一确定好友、群或公众号 |
| 风控 | `BLOCKED` / `RISK_CONTROL` | 验证码、风险提示或人工确认 |
| 生命周期 | `BUSY` / `CANCELLED` / `SESSION_CLEANUP_FAILED` | 单飞、取消或窗口收口失败 |
| 写动作 | `EXECUTION_UNKNOWN` | 动作可能已发出但写后校验失败 |
| 兜底 | `INTERNAL_ERROR` | 未分类实现错误 |

`retryable` 只对写动作开始前的环境型失败开放；`partial` 和 `unknown` 永不自动重试。

## 5. Tool 路线图

### 5.1 v0.1：产品化已验证能力

| tool | 输入核心 | 输出核心 | effect | 发布状态 |
|---|---|---|---|---|
| `weixin_probe` | 无 | 版本、登录态、窗口和能力矩阵 | `none` | 首期 |
| `weixin_souyisou_search` | `query`、`category=all`、`limit<=10` | 结果摘要、链接（若剪贴板可得）、`result_set_ref` | `none` | 首期 |
| `weixin_souyisou_collect` | `query`、`required_terms<=5`、`limit<=10` | 已打开条目摘要、加密证据 refs | `none` | 首期 |

`weixin_souyisou_collect` 是通用证据采集，不包含“协会”“秘书长”“手机号归属”或任何 LLM Provider。未来调用方可以在自己的业务层解释证据；现有协会客户端不会切换到这个 operation。

### 5.2 v0.2：公众号文章

| tool | 作用 | 关键门禁 |
|---|---|---|
| `weixin_article_search` | 在搜一搜“文章”域查公众号文章 | 必须先 probe 证明分类切换稳定；否则内部使用可验证的查询策略 |
| `weixin_article_read` | 打开指定结果并提取标题、公众号、时间、正文 | 目标由 `result_set_ref + result_id` 唯一确定；正文超限落 artifact |
| `weixin_article_get_url` | 获取可验证文章地址 | 只接受 CF_HTML、复制链接或浏览器地址等真实证据；拿不到就 `CONTENT_UNAVAILABLE`，禁止猜 URL |

文章结果引用不是永久 ID。它绑定本机账号、微信版本、原查询和短 TTL；过期后必须重新搜索，不能按旧序号盲点。

### 5.3 v0.3：好友与群检索

| tool | 输入 | 输出 |
|---|---|---|
| `weixin_chat_search` | `query`、`type=friend|group|any`、`limit<=20` | 脱敏 label、类型、exact_match、短期 `target_ref` |

检索不打开聊天、不读取历史消息。重名时返回 `TARGET_AMBIGUOUS` 或候选 refs，不自动选择第一个。`target_ref` 用本机密钥签名，绑定账号、目标指纹、微信进程和过期时间，默认 5 分钟。

### 5.4 v0.4：受控写动作

| tool | 输入 | 硬限制 | 写后校验 |
|---|---|---|---|
| `weixin_message_send` | `target_ref`、`text` | 单次 1 个目标、1 条、首版最多 500 字；不支持批量/文件/图片 | 发送前回读目标，发送后验证会话和最后一条消息特征 |
| `weixin_official_account_follow` | `account_ref` | 单次 1 个公众号 | 重新读取“已关注”状态 |

MCP 写动作不接受裸显示名，必须先通过只读 tool 获取未过期 target ref。human CLI 可提供显示名便利入口，但必须唯一解析并展示目标确认；非交互 `--yes` 仍受同样的唯一性和上限约束。

## 6. 自动化与会话模型

### 6.1 继承的安全不变量

- 只操作普通微信 `Weixin.exe` 和经过路径、进程、类名、标题联合验证的插件窗口；
- 不使用 Hook、注入、私有协议、修改启动参数或自动绕过安全验证；
- 每次按键/点击前后复验可信前台；焦点丢失立即 fail closed；
- 所有组合键异常路径逐键 KeyUp；
- UIA 物理坐标使用 Per-Monitor V2，不做二次 DPI 换算；
- 需要关闭的临时窗口按精确 HWND 清理；隐藏旧 HWND 不重新激活；
- 一个进程内单飞，加跨进程命名 Mutex；用户操作与 Provider 调用不得并行控制微信；
- Provider 不启动、退出或登录微信。

### 6.2 取消与超时

- operation 接收 `AbortSignal`；
- 安全检查点之间协作取消，写动作的鼠标按下/抬起或按键组合不能中途撕裂；
- 取消发生在写动作前：`CANCELLED/effect=none`；
- 取消发生在动作发出后且无法确认：`CANCELLED/effect=unknown`；
- Host 杀进程不能视为成功，下一次 doctor 必须检查残留窗口和按键状态；
- 搜一搜沿用总工作预算，并为 cleanup 预留固定时间。

## 7. Probe 实验体系

新增微信能力不得直接写正式 operation。每个 probe 都要经过“假设—最小实验—证据—结论—产品化”流程。

### 7.1 Probe 分级

| 级别 | 允许动作 | 示例 | 要求 |
|---|---|---|---|
| P0 观察 | 枚举窗口/UIA、截图哈希、读剪贴板格式 | 文章页是否暴露链接 | 无业务副作用 |
| P1 导航 | 激活、打开页面、输入但不提交或只读提交 | 搜一搜文章分类 | 严格前台门禁与自动清理 |
| P2 沙箱写 | 对专用测试账号/测试群执行一次写动作 | 发送测试消息、关注测试号 | 显式人工确认、固定白名单目标 |
| P3 稳定性 | 重复、异常、DPI/主题/版本矩阵 | 连续 30 次发送后校验 | 达标前不得注册 MCP tool |

### 7.2 每个 probe 的必备产物

```text
experiments/probes/<id>/probe.json       # id、假设、风险、允许动作、目标白名单
experiments/probes/<id>/run.*            # 最小实验代码
docs/research/weixin-cli/<id>.md          # 环境、步骤、证据摘要、结论、后续决策
```

记录微信版本、Windows 版本、DPI、主题、窗口身份、UIA 摘要、剪贴板 format、截图 hash、耗时和稳定错误码。不得提交聊天正文、联系人明文、手机号、cookie、token 或未脱敏截图。

### 7.3 产品化门禁

只有同时满足以下条件，probe 才能进入正式 tool：

1. 目标和成功终态可被机器验证，而不只是“点击没报错”；
2. 有无副作用测试与真机矩阵；
3. 有清理/恢复路径；
4. 写动作定义 `applied/partial/unknown`，且 unknown 不重放；
5. tool schema 无任意执行参数，有硬上限；
6. 通过统一 MCP conformance、Codex 和一个国内主流 Host smoke test；
7. 设计、开发计划和 `docs/ideas.md` 状态同步更新。

首批 probe 顺序：

1. 文章搜索分类与结果节点；
2. 文章链接的 CF_HTML / 复制链接 / 外部浏览器证据；
3. 文章正文复制、滚动、重复段去重和最大长度；
4. 通讯录/顶部搜索对好友与群的 UIA 结构；
5. 重名、同名群、备注名和微信号的消歧；
6. 测试账号单条文本消息发送与写后校验；
7. 公众号搜索、详情与已关注状态；
8. 测试公众号关注与写后校验。

## 8. 数据、安全与审计

- 微信自身登录态是唯一账号凭据，Provider 不导出凭据；
- artifact 默认写 `%LOCALAPPDATA%\AidWorkAgent\weixin-cli\artifacts`，DPAPI CurrentUser 加密；
- artifact ref 必须限制在受控根目录，拒绝调用方任意路径；
- result/target refs 为不透明、短 TTL、带完整性校验的本地 handle；
- 审计只记 tool、规范化参数摘要、run_id、effect、耗时、版本和错误码；
- 消息正文只记录长度与 hash，不记原文；搜索词和联系人名按敏感字段处理；
- 公开文章 URL/标题可以返回，文章正文设字符上限；
- MCP instructions 前 512 字符说明仅 Windows、需已登录未锁屏、会占用前台/剪贴板、写动作上限和操作期间勿动鼠标；
- 正式包不包含实验截图、样本、调试日志或测试目标。

## 9. 与协会客户端的隔离边界

协会客户端已经交付并保持现状，本项目不执行任何反向改造：

- 不修改 `clients/association-client-cli/` 的脚本、Python、PyInstaller 或 Electron 打包；
- 不修改 `src/services/association_enrichment_providers.py` 的现有调用链；
- 不删除、转发或替换协会客户端当前使用的 PowerShell 文件；
- 不要求协会客户端安装、发现或调用 `weixin-cli`；
- 不以协会客户端真机回归作为 `weixin-cli` 发布的阻塞条件。

允许的关系只有“一次性读取和复制”：开发 `weixin-cli` 时，从协会现有实现复制已验证的窗口识别、前台守卫、搜一搜、DPAPI 和 cleanup 代码到新工程，随后在 `weixin-cli` 内独立维护。复制不是抽取共享模块，也不产生反向链接。未来其他项目必须使用 `weixin-cli`，不能继续以协会客户端为模板复制微信代码。

同时禁止在 `weixin-cli` 中引入 `association_name`、`secretary_general`、积分、租户 access token 或协会服务端 LLM gateway。`weixin-cli` 只提供通用微信能力。

## 10. 测试与发布门禁

### 10.1 离线测试

- operation 参数、错误码、effect、run_id、progress、cancel；
- driver stdin/stdout UTF-8 与 PowerShell 5.1/7；
- 窗口身份、企业微信拒绝、前台丢失、KeyUp、DPI/多屏；
- result/target ref 签名、TTL、串改和跨账号拒绝；
- DPAPI、目录边界、日志脱敏、MCP stdout 零污染；
- manifest/schema digest；
- `clients/shared/mcp-conformance/` 全套；
- association 旧样本的兼容回归。

### 10.2 真机测试

- 微信版本至少覆盖当前交付版本和一版升级候选；
- 100%/125%/150% DPI，单屏/双屏，深色/浅色，最大化/普通窗口；
- 焦点抢占、锁屏、最小化、微信退出、加载慢、安全提示；
- 只读操作 30 次成功率目标不低于 98%；
- 写动作仅对测试目标，至少 30 次，错误目标发送为 0；
- 写后校验失败必须返回 unknown，确认 Host 不重试；
- Codex + WorkBuddy（或当时选定国内主流 Host）真机 smoke test；
- Local Tool Runtime 直连。

### 10.3 发布

v0.x 期间 tool 只能逐个晋级。未过真机矩阵的能力只存在于 `lab`，不得出现在 manifest。正式发布包需自包含、签名、带 manifest/schema digest；Provider 版本与 tool schema 按 SemVer 管理。

## 11. 明确不做

- 不做微信登录、扫码、账号池和多实例并发；
- 不读聊天历史、不抓群成员、不监控收消息；
- 不做批量群发、营销频控绕过、验证码或风控规避；
- 首期不发图片、文件、语音、红包、小程序或朋友圈；
- 不把 probe 变成 MCP 的任意桌面控制工具；
- 不让云端指定 executable、PowerShell 脚本、cwd、env 或坐标；
- 不因 Host 或部署位置变化重写 Provider；
- 不在本期重写为 C#/.NET 自动化内核。

## 12. 已确定决策

1. 产品名和可执行名统一为 `weixin-cli`，tool 前缀统一 `weixin_`；
2. 它是独立 MCP Provider，不是协会客户端的内部模块；
3. 复用 BOSS CLI 的 TypeScript Provider 骨架和共享 conformance suite；
4. 首期从协会现有实现一次性复制已验证能力，再在 `weixin-cli` 内独立产品化；
5. v0.1 只发布已验证的 probe/搜一搜只读能力；
6. 公众号文章、好友/群、消息、关注按 probe 证据逐项晋级；
7. 搜索结果、联系人和公众号统一使用短期不透明 ref，写动作不接受模糊裸名称；
8. 写动作单目标、单次、硬上限，结果 unknown 永不自动重放；
9. 微信 CLI 不依赖协会业务、模型供应商或租户体系；
10. 协会客户端是冻结的历史实现，不纳入迁移；未来新项目统一使用 `weixin-cli`。
