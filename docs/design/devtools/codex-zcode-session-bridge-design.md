# Codex 编排 × ZCode 开发会话桥接设计

> 状态：📋 设计完成，待实施。可行性研究与真机实验见 [research/codex-zcode-session-bridge-research.md](../../research/codex-zcode-session-bridge-research.md)。本文为 2026-11-05 按双侧官方文档核对后修订的版本。

## 1. 背景与目标

现有 [.agents/skills/phased-dev-review/SKILL.md](../../../.agents/skills/phased-dev-review/SKILL.md) 依赖 Codex 内部线程工具（`create_thread`/`wait_threads`/`read_thread`）实现"调度会话 ↔ 开发会话"逐 Phase 评审。本设计将开发会话换成 ZCode 无头会话：

- **Codex 编排会话**（用户交互的 TUI 会话）：设计、逐 Phase 任务书下发、独立代码评审、放行判定。
- **ZCode 开发会话**（无头子进程）：按任务书实现，内部仍执行项目三智能体开发流程（dev-workflow），完成后交接等待评审。

循环至全部 Phase 评审通过后停止。**不修改现有 phased-dev-review skill**；落地时新建 skill（建议名 `phased-dev-review-zcode`），调度与评审语义复用原文档，仅替换通信层。

## 2. 机制选型（官方文档核对结论）

| 需求 | 选型 | 官方依据 |
|------|------|---------|
| 外部程序创建项目内会话 | `zcode -p "<任务>" --cwd <项目> --json` → 解析 `sessionId` | ZCode 02-cli-reference.md / 06-sessions.md |
| 持续对话 | `zcode --resume <sessionId> -p "<下一条>" --json` | 同上（官方描述的正是该循环） |
| 长任务进度 | `--output-format stream-json`（NDJSON 流式事件） | 同上 |
| 消息返回 Codex | 每轮同步子进程：进程退出即返回，读 `--json` + 交接文件 | — |
| Codex 侧被动收消息（备选） | `codex exec resume <编排会话id>`；同会话 TUI 并发有冲突风险，不作为默认 | Codex CLI 官方 flag |
| 程序化双向桥（不采用） | Codex app-server（JSON-RPC 官方文档化）+ ZCode app-server（**私有 NDJSON 协议未文档化**，feedback#286） | 仅当编排器程序化/需桌面 UI 可见会话时，版本锁定下再评估 |

排除项：ZCode 的 `--model`/`--print`/`--allowed-tools`/`--permission-mode` 为官方明示不支持的参数；ZCode app-server 为私有协议。

## 3. 通信协议（文件握手 + 同步进程）

### 3.1 握手目录

`<repo>/.codex-bridge/`，加入 `.gitignore`（或按任务放 `docs/plans/` 同级的临时目录；不入库）。

### 3.2 Codex → ZCode：任务书

`.codex-bridge/task-phase-N.md`，内容固定包含：

1. 只执行 Phase N 的明确边界（做什么、不做什么）
2. 任务书与计划文档路径（设计、开发计划、`dev_workflow.md`、代码评审准则）
3. 三智能体要求（开发自测 + 独立测试 + 独立 CR，引用项目 dev-workflow skill）
4. 完成证据要求与交接文件路径
5. 硬约束："收到评审者放行前不进入下一 Phase；完成当前 Phase 后写交接文件并结束本次运行"

### 3.3 ZCode → Codex：交接文件

`.codex-bridge/handoff-phase-N.json`，字段：

```json
{
  "phase": "N",
  "status": "completed | blocked",
  "changedFiles": ["..."],
  "diffSummary": "范围与规模一句话",
  "designMapping": "设计目标如何落地",
  "tests": [{"command": "...", "result": "pass/fail", "evidence": "关键输出摘要"}],
  "internalReview": {"test": "结论", "codeReview": "结论及修复"},
  "unverified": ["未验证场景"],
  "planProgress": "计划文档进度登记区已更新至 ...",
  "blockers": ["缺少的环境/授权，及影响"],
  "nextPhaseExpectation": "对下一阶段的输入需求（可选）"
}
```

### 3.4 单轮调用

```
Codex spawn:
node <zcode.cjs> [--resume <sessionId>] -p "<指令：读 task-phase-N.md，按约束执行，完成写 handoff-phase-N.json>" \
  --cwd <repo> --json [--output-format stream-json（长任务可选）]
阻塞等待退出 → 解析 JSON（sessionId/response/usage）→ 读 handoff → 评审
```

首轮无 `--resume`（创建会话），后续轮次全部 `--resume` 同一 `sessionId`，保证开发上下文连续。

## 4. Phase 循环状态机

```
┌─> 调度：写 task-phase-N → spawn（首轮创建 / 后续 resume）
│     ↓ 进程退出
│   读取 handoff + git diff
│     ↓
│   独立评审（代码评审准则；不转述开发会话内部 CR）
│     ├─ 通过 → 更新计划进度登记区 → N=全部? ─ 是 → 结束（交代各 Phase 结论/证据/风险）
│     │                                    └ 否 → 写放行 + task-phase-(N+1) ─┐
│     ├─ 需修复 → 评审意见写入 task-phase-N（阻断项须含触发条件/位置/影响/最小修正）──┤
│     └─ 受阻 → 记录缺失条件，先做不依赖阻塞的工作，不虚报通过 ──────────────────┘
└──────────────────────── resume 同一 sessionId 下一轮 <──────────┘
```

- 同一代码状态复用有效测试结果，不机械重复全套测试。
- 提交/推送/部署不因评审通过而自动授权，仍按 AGENTS.md 等用户明确指示。

## 5. 环境配置（已落地，2026-11-04 真机验证）

- `~/.zcode/cli/provider/zcode-builtin.json` + `provider_config.json`（`config.defaultModelSelection` = GLM-5.3-Flash/max）——CLI 专用稳定副本，桌面不写此目录。
- `~/.zcode/v2/credentials.json` 已补 individual provider 的 identity/auth-source/api-key 三键（备份 `credentials.json.bak-bridgeresearch`）。
- spawn 必带 env：两个 `ZCODE_*_PROVIDER_CONFIG_FILE` 指向上述副本，并 `-u` 移除 `ZCODE_OFFICIAL_ENDPOINT(_TOKEN)`（standalone 自管账号）。
- 可选隔离：`ZCODE_SESSION_DB_PATH` 指向桥接专用库，避免与桌面会话混写。

## 6. 权限与信任边界

- `-p` 默认 `--mode yolo`：ZCode 无头进程改文件/跑命令不经人工审批，等于 Codex 的评审策略覆盖整条链。须用户知情接受。
- 收紧选项：`--mode build`（审批会卡死无头进程，不推荐）→ 改用项目 permission ruleset 预授权常用命令；`--disallowed-tools` 移除高危工具（注意粒度为整工具，不支持命令级匹配）。
- 密钥、令牌不落入任务书、交接文件与日志（AGENTS.md 安全原则）。
- 桥接目录不入库；跨机器迁移时环境配置（第 5 节）需按本文重建。

## 7. 成本与性能

- 实测单轮无头调用上下文约 44 万 input tokens，主要来自共享插件注入；桥接期可通过独立数据目录（`ZCODE_DATA_BASE_DIR`）或精简插件降低。
- `stream-json` 用于长 Phase 的可观测性；结束以 `type: "result"` 行为准，不数固定事件数。

## 8. 风险与遗留

| 风险 | 缓解 |
|------|------|
| builtin 配置静态副本随 ZCode 升级过期（模型清单变化） | 升级 ZCode 后重跑冒烟（`-p "只回复OK" --json`）；必要时更新副本或 `zcode login` |
| resume 跨版本兼容未验证 | 桥接运行期锁定 ZCode 版本；升级后先冒烟再继续任务 |
| 44 万 token/轮的固定开销 | 第 7 节手段精简；Phase 任务书保持精炼 |
| 桌面端与桥接并发写共享库 | 可选 `ZCODE_SESSION_DB_PATH` 隔离；至少避免同仓库并发双会话 |
| 交接文件与 stdout 不一致 | 以 git diff + 测试命令复跑为最终证据，交接文件仅作索引 |

## 9. 实施清单（待授权后执行）

1. 新建 skill `.agents/skills/phased-dev-review-zcode/SKILL.md`（调度/评审语义复用原 skill，通信层按本文第 3~4 节）。
2. 按 AGENTS.md 登记规范补 `docs/ideas.md` 条目的开发计划链接（实施时补计划文档）。
3. 首个真实任务试点 1~2 个 Phase，验证循环与交接质量后复盘迭代 skill 文案。
