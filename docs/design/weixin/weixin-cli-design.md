# weixin-cli：第一方微信操作 CLI / MCP Provider 设计

> 状态：✅ 已实现并持续维护（2026-10-09 按当前代码整合重写）
>
> 上位规范：[第一方 CLI / MCP Provider 架构与开发规范](../../system/first-party-cli-mcp-provider-standard.md)
>
> 第一参考实现：[BOSS CLI 接入设计](../recruiting/recruiting-cli-agent-integration-design.md)
>
> 本文档是 weixin-cli 的唯一现行架构设计文档，按 `clients/weixin-cli/` 实际实现撰写；
> 原始路线图设计（2026-08-11）、计费关联设计（2026-08-27）与 M0 冻结/P1-P3 probe 报告等
> 过程文档已删除，有效内容并入本文（过程明细见 git 历史）。

## 1. 定位

`weixin-cli` 是项目第二个第一方 CLI：独立、标准、可分发的 Windows MCP Provider
（provider_id `ai.aidwork.weixin`，`aid-weixin` 可执行，stdio transport，`local_required`，
平台 `win32-x64`），面向已登录且未锁屏的交互桌面会话，供人工终端、aid-work-agent
Local Tool Runtime / 桌面客户端、Codex 等标准 MCP Host 复用"操作微信 Windows 客户端"能力。

## 2. 工程形态与命令面

- Node.js ≥22 + TypeScript（ESM）；运行时依赖仅 `@modelcontextprotocol/sdk` 与 `zod`。
- 动词子命令：`probe / search / send / add / read / mcp / doctor / version`（对象一律作
  `--domain` 参数值）。聊天操作使用 `chat|chat-history|unread`，本机加好友使用
  `add --domain friend`。
- MCP 仅 stdio（`aid-weixin mcp --stdio`）；`doctor` 严格只读。
- 代码量级：src 40 文件约 3.9k 行、drivers（ps1+py）约 1.5k 行、tests 约 3.9k 行。
- C5 便携打包产出自包含 `aid-weixin.exe`（不要求用户安装 Node/Python）。

## 3. 架构分层

```text
human CLI adapter ─┐
                   ├─> operations（注册表，CLI 与 MCP 共用同一实现）
MCP stdio adapter ─┘        │
                            ▼
              platform（powershell 驱动执行器 / targetRef / liveBridge / ocrResident / serverProxy）
                            │  spawn powershell -File <script>
                            ▼
              drivers/ps1（Win32 P/Invoke + UIA）+ drivers/py（常驻 RapidOCR）
```

- **驱动协议**：TS spawn PowerShell 脚本，stdout 末行 `DRIVER_JSON: {...}` 单行 JSON；
  业务失败 `ok=false` 且退出码 0，非零退出映射 `INTERNAL_ERROR`；默认超时 300s，
  AbortSignal → `CANCELLED`。
- **操作契约**：operation 永不 reject，统一 `OperationResult`（success/code/message/
  effect ∈ none|applied|partial|unknown/data/retryable/run_id）；toolDefs↔registry
  启动期一致性校验 fail-loud。
- **微信交互机制**（drivers/ps1/win32-lib.ps1）：PostMessage 点击 + WM_CHAR 逐字输入 +
  回车（不占光标/剪贴板，RDP 可用）；PrintWindow 遮挡截图；UIA 枚举与标题校验；
  视觉模型坐标定位（Kimi kimi-k3 / GLM-5.3-Flash，429 重试 25s×4）；常驻 RapidOCR
  （drivers/py/ocr_server.py，stdin/stdout JSON 行协议，可重启）；名称会话路径用
  剪贴板粘贴输入（name-ocr.ps1）。
- **稳定性**：进程内单飞 + 跨进程命名管道互斥 `\\.\pipe\AidWorkAgent.AidWeixin.<scope>`
  （占用返回 `BUSY`）；写动作零重试；`ConversationAligner` 会话对齐/水位指纹；
  stderr 日志脱敏；DPAPI CurrentUser 加密 token。

## 4. MCP Tool 全集（8 个）

| tool | 作用 | 要点 |
|------|------|------|
| `weixin_probe` | 只读环境探测 | win32/交互会话/PowerShell/Weixin.exe 进程 |
| `weixin_chat_search` | 主窗口搜索好友/群 | 返回 5 分钟有效 `target_ref`（HMAC 签名，TTL 300s） |
| `weixin_name_resolve` | 按名称 OCR 近似匹配唯一定位会话 | 多匹配即拒绝（名称会话 v2 路径） |
| `weixin_message_send` | 向 target_ref 发 1 条文本（≤500 字） | 写动作，发送后 OCR 核验，unknown 不重试 |
| `weixin_message_send_v2` | 仅接受 Runtime HMAC 签名许可 | `context`+`signature`（AIDWORK_WEIXIN_BRIDGE_KEY，90s 租约），发送前本机核对标题，回执记录已提交的输入动作 |
| `weixin_history_read` | 读聊天记录 | 翻页 ≤10、内联 ≤200 条、超出落文件 |
| `weixin_unread_list` | 未读会话列表 | 只读不清角标 |
| `weixin_session_observe` | 截图→常驻 OCR→拆行→对齐→水位 | 返回 `session_observer_v1` 冻结契约；无真机截图返回 `coverage=unavailable` |

写动作硬约束：MCP 不接受裸显示名，必须先经只读 tool 获取未过期 target_ref；
v2 路径额外要求 Runtime 签名许可（云端不可凭名字驱动发送）。

## 5. 计费与密钥治理（服务端代理模式）

落地"方案 A：走服务端 LLM 网关代理"（原计费关联设计，已并入本节）：

- **服务端**：`/api/client/v1/llm/chat` 按 `CLIENT_MODEL_PROVIDER_MAP` 白名单将
  `GLM-5.3-Flash` 路由到智谱专用网关。沿用模型定价校验与原客户端用量计费。
  历史 `kimi-k3` 路由已于 2026-11-05 停用。
- **客户端**：`AID_WEIXIN_SERVER_URL` + `AID_WEIXIN_ACTIVATION_CODE` 惰性激活
  `POST /api/client/v1/activate` 换 access_token，DPAPI 加密存
  `%APPDATA%\aid-weixin\binding.json`；token 经 env 注入驱动子进程（不进命令行）；
  402 → `INSUFFICIENT_CREDIT`、401 → `CONFIG_MISSING`，不降级不重试。
- **降级**：显式 `AID_WEIXIN_KIMI_API_KEY` / `AID_WEIXIN_ZHIPU_API_KEY` 走本机直调，
  仅开发调试、不计费不入账；生产分发版以代理模式为默认。
- **安全事项（人工跟进）**：git 历史（commit 970b3961 及 experiments/probes 脚本）曾
  泄漏真实 Kimi key，**必须到 Moonshot 控制台轮换/吊销**——轮换是唯一收敛手段。

## 6. 安全不变量

- 只操作普通微信 `Weixin.exe` 与联合验证过的插件窗口；不 Hook、不注入、不碰私有协议；
- 短期不透明 ref（HMAC 签名，绑定账号/目标指纹/进程/TTL），写动作不接受模糊裸名；
- artifact/凭据本地 DPAPI（CurrentUser）加密；ref 限制在受控根目录；
- 消息正文只记长度与 hash；搜索词、联系人名按敏感字段脱敏；MCP stdout 仅 JSON-RPC；
- 进程单飞 + 跨进程命名互斥，用户操作与 Provider 调用不并行控制微信；
- Provider 不启动、退出或登录微信。

## 7. Probe 门禁与能力晋级

新增能力先经 `experiments/probes/<id>/probe.json`（假设/风险/允许动作/目标白名单）
走"假设—最小实验—证据—结论—产品化"；P0 观察 / P1 导航 / P2 沙箱写（测试账号白名单）/
P3 稳定性矩阵。晋级条件（终态可机器验证、清理路径、unknown 不重放、schema 无任意执行
参数、conformance + 跨 Host smoke）见上位规范。未过门禁的能力只存在于实验区，
不进 manifest。

## 8. 与协会客户端的隔离边界

协会客户端（clients/association-client*）是冻结的历史实现：一次性单向复制已验证的
窗口识别/前台守卫/DPAPI/cleanup 代码后双方独立演进；不修改协会侧任何文件、不抽共享包、
不反向调用、不以其回归为发布阻塞。未来新项目的微信需求统一使用 weixin-cli。

## 9. 测试与交付

- `node:test` 零第三方框架；40 个测试文件约 205 用例（MCP conformance、CLI 参数、
  driver 协议、OCR 匹配与取消、对齐器/气泡分段、名称会话族、server-proxy 激活、
  DPAPI、doctor、进度取消）。均为单元/契约级，真机验收随插件 Host 交付执行。
- 交付形态：Runtime 插件 Host 分发（C5 便携包 `aid-weixin.exe`），manifest/schema
  digest 随包。

## 10. 演进记录

| 阶段 | 内容 |
|------|------|
| M0 冻结 + M1 骨架（2026-08-11） | 协议样本冻结、TypeScript Provider 骨架（53 用例 + conformance 全绿） |
| M2 四能力（aee75f8e） | probe / chat_search / message_send / history_read 产品化 |
| 服务端计费（3f28e227，2026-08-27） | Moonshot provider + 模型白名单 + 激活/DPAPI + 402 语义 |
| C0 session_observer 契约（e4f9b899） | 冻结 `session_observer_v1` 观察契约 |
| C2 常驻 OCR / 对齐器（54020c08） | ocr_server 常驻进程 + ConversationAligner |
| C5 便携打包（78f56627） | 自包含 exe 分发 |
| 插件 Host 交付（f1087289） | 随 Runtime 插件宿主 Windows 验收包交付 |

方向变化说明：原路线图的搜一搜/文章/公众号域（souyisou_search/collect、article_read/
get_url、official_account_follow）未产品化——实际方向转向会话观察/名称定位/许可写动作族；
原"不读聊天历史"的边界已由 history_read/session_observe 的实际能力取代。

## 11. 遗留与已知问题

- 服务端代理视觉链路要求部署环境配置非零的 GLM-5.3-Flash 价目；
- GLM 服务端真实计费及微信搜索、发送、添加好友的真机联调待验证；
- git 历史泄漏 Kimi key 待人工轮换（§5）；
- `provider-manifest.json` 静态快照列 6 个 tools，落后于实际 8 个（name_resolve/send_v2
  未入快照），与 `src/mcp/manifest.ts` "digest 不入文件避免漂移"的注释不一致，待对齐；
- `package.json` description 仍写 "M1 骨架"，未随里程碑更新（ cosmetic）。

## 12. 本机添加好友

`aid-weixin add --domain friend --identifier <手机号或微信号> [--verify-message <验证语>] [--json]` 每次处理一个明确的标识。省略验证语时保留微信预填内容。操作复用 CLI renderer、统一 `OperationResult`、桌面互斥及 PowerShell Win32 驱动，要求当前微信已登录且窗口可交互。

操作顺序为主窗口「+」→「添加朋友」→输入修剪后的标识并回车→核定当前搜索结果。弹窗按微信进程与准确标题识别。坐标从当前窗口的截图获取，点击前检查坐标范围及窗口身份。在模型等待期间，用户可能在同一窗口换搜目标，因此模型返回后还需本地重拍并核对搜索窗口内容。初始结果已是好友或明确显示已发送申请时返回 `effect=none`，查无结果返回 `TARGET_NOT_FOUND`。

点击「添加到通讯录」后，如果出现申请窗口则填写验证语并点击一次「确定」。当前标识的搜索状态明确显示申请已发送时返回 `request_sent`，微信确认直接添加为好友时返回 `friend_added`，两者均为 `effect=applied`。写入步骤后未能确认时返回 `effect=unknown`，调用方不得自动重试。

视觉定位强制使用当前微信 CLI 激活绑定及 `POST /api/client/v1/llm/chat` 的现有模型和用量计费路径。新入口不写 Workbuddy 的坐标缓存或联系人日志。微信 CLI 负责产生本机回执，当前阶段可由本机终端调用。MCP 工具公开及 Runtime 调度按第 7 节在沙箱写验收后单独登记。

## 13. 移除本地 OCR

迁移前有五个工具直接依赖 RapidOCR。名称定位用它识别搜索结果和会话标题，许可发送用它核对当前标题，历史读取和未读列表各有一个 Python 驱动，持续观察用它提取消息正文、时间、输入框状态并参与水位对齐。旧发送走多模态读取标题和回执文本，再使用按 OCR 误差设计的本地匹配。环境探测、旧聊天搜索和本机添加好友均不调用 RapidOCR。

未读列表现已读取同进程 UIA 会话行的原生名称、预览和数字角标，候选项在本机用对应位置的红色像素复核。读取不会打开会话。同版真机验证覆盖三个带数字的角标，只有红点而无原生计数的行不推测数字。

许可发送的目标核对可以独立迁移。该步骤读取当前微信窗口同一进程的唯一 UIA 标题和输入框边界。预检与提交前精确核对已确认观察记录的原生标题，保留已有别名绑定。联系人搜索和观察的原有定位、水位与消息来源继续由各自入口负责。详见[开发计划](../../plans/plan-weixin-cli-remove-ocr.md#phase-3a-发送前标题)。

计费定位复用现有客户端 binding 与 `/api/client/v1/llm/chat`，模型使用现有白名单中的 GLM-5.3-Flash。固定控件的坐标在能验证控件与布局时可以缓存；区分客户端版本、DPI、窗口尺寸、布局和控件角色，缓存相对坐标。搜索结果行、当前联系人、发送结果和消息内容随交互变化，每次读取当前证据。

原生读取优先使用 Windows UI Automation。2026-11-17 在本机微信 4.1.15.13 只读核对到会话列表、消息列表、具名文本消息项和可读取的输入框。历史分页、消息发送方、重名联系人匹配和未读数仍需分别核定，不以控件枚举证明这些业务语义。端侧持续观察沿既有约定在本机读取，保留既有消息水位、发送许可与回执外层。无原生证据时报告对应失败或缺口。

本机探查中有唯一的 `current_chat_name_label`、`session_list`、`chat_message_list` 和 `chat_input_field`。消息行的原生边界横跨聊天列，不能从整行边界判断方向。可把原生文本行和现有本地气泡边界配对验证，文字始终来自原生节点。会话行名称中发现 `[n条]` 原生提示，须区分未读元数据行与相同格式的消息预览。其他 UIA 实现的原生读取和方向识别代码可作对照。 [会话列表参考](https://github.com/AngeCoo/wxauto-4.0/blob/main/wxauto4/ui/sessionbox.py) · [消息方向参考](https://github.com/FreeWisdom/wxauto-4.0/blob/main/wxauto4/msgs/msg.py)
