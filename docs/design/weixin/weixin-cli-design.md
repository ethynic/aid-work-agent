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
- 动词子命令：`probe / search / send / read / mcp / doctor / version`（对象一律作
  `--domain` 参数值，不设按对象命名的子命令）；`--domain` 声明
  `souyisou|article|chat|chat-history|unread`，当前仅 chat 族接线。
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
| `weixin_message_send_v2` | 仅接受 Runtime HMAC 签名许可 | `context`+`signature`（AIDWORK_WEIXIN_BRIDGE_KEY，90s 租约），本人气泡后验 |
| `weixin_history_read` | 读聊天记录 | 翻页 ≤10、内联 ≤200 条、超出落文件 |
| `weixin_unread_list` | 未读会话列表 | 只读不清角标 |
| `weixin_session_observe` | 截图→常驻 OCR→拆行→对齐→水位 | 返回 `session_observer_v1` 冻结契约；无真机截图返回 `coverage=unavailable` |

写动作硬约束：MCP 不接受裸显示名，必须先经只读 tool 获取未过期 target_ref；
v2 路径额外要求 Runtime 签名许可（云端不可凭名字驱动发送）。

## 5. 计费与密钥治理（服务端代理模式）

落地"方案 A：走服务端 LLM 网关代理"（原计费关联设计，已并入本节）：

- **服务端**：`src/llm/providers/moonshot.py` MoonshotProvider（OpenAI 兼容；kimi 系模型
  省略 temperature；reasoning_content 兜底）；`gateway.py` 注册 moonshot +
  `use_failover=False`（模型绑定型调用禁止跨 provider 降级）；`/api/client/v1/llm/chat`
  增加 `model` 字段 + `CLIENT_MODEL_PROVIDER_MAP` 白名单（首期 `kimi-k3 → moonshot`）+
  定价 fail closed（无非零单价 → `MODEL_NOT_PRICED`）；`deploy/db_update.sql` 已写
  kimi-k3 价目行（用户确认单价：输入 20 元/M、输出 100 元/M；**注释态待部署执行**）。
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

- kimi-k3 价目行处于注释态，**待部署执行**后代理视觉链路才实际可用（防免单设计）；
- 服务端 + 真实 Moonshot key 端到端联调与真机回归（搜索→发送走代理）待单价落库后执行；
- git 历史泄漏 Kimi key 待人工轮换（§5）；
- `provider-manifest.json` 静态快照列 6 个 tools，落后于实际 8 个（name_resolve/send_v2
  未入快照），与 `src/mcp/manifest.ts` "digest 不入文件避免漂移"的注释不一致，待对齐；
- `package.json` description 仍写 "M1 骨架"，未随里程碑更新（ cosmetic）。
