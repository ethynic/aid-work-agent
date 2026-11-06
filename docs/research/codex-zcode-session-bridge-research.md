# Codex 编排 × ZCode 开发会话桥接可行性研究

> 2026-11-04 ~ 11-05 真机实验 + 双侧开源仓库官方文档核对。结论：可行，且逆向验证出的用法与官方文档一致；无头默认模型问题已解决。设计方案见 [design/devtools/codex-zcode-session-bridge-design.md](../design/devtools/codex-zcode-session-bridge-design.md)。

## 目标

验证：Codex 会话（设计/开发调度/评审）能否驱动 ZCode 无头会话（开发实现），双向沟通循环直至全部 Phase 评审通过。对照 `phased-dev-review` skill 在 Codex 内部线程（`create_thread`/`wait_threads`/`read_thread`）的既有模式，寻找跨工具等价机制。

## 双侧能力事实（真机验证）

### Codex CLI（本机 0.146.0）

| 能力 | 验证方式 |
|------|---------|
| `codex exec` 非交互执行 | `codex exec --help` |
| `codex exec resume <session_id>` 按 id 恢复指定会话继续 | `codex exec --help` |
| App Server：JSON-RPC 2.0 over stdio，官方文档化 `thread/start` / `turn/start` / `thread/resume` / `turn/steer` / `item/agentMessage/delta` 等 | [官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)、[OpenAI 博客](https://openai.com/index/unlocking-the-codex-harness)，可 `codex app-server generate-ts` 生成类型 |

### ZCode CLI（本机 0.17.1，入口 `resources/glm/zcode.cjs`）

| 能力 | 真机结果 | 官方文档依据 |
|------|---------|-------------|
| `-p "<任务>"` 无头单轮（默认 yolo） | ✅ exit=0，stdout 输出答复 | [02-cli-reference.md](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/02-cli-reference.md) |
| `--cwd <路径>` 指定工作目录 | ✅ | 同上 |
| `--json` 结构化输出 `sessionId`/`traceId`/`turnId`/`response`/`usage` | ✅ | 同上（文档另列 `eventCount`/`projection`） |
| `--output-format stream-json` NDJSON 流式事件 | 未实测（官方文档确认存在） | 同上 |
| `--resume sess_xxx` 续聊且上下文完整 | ✅ 记住 47→答 94 | [06-sessions.md](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/06-sessions.md)（官方流程："首 prompt→新会话→存 sessionId→resume→加载历史→新 turn"） |
| 无头会话内工具调用（建文件等） | ✅ `hello.txt` 真实创建 | 同上 |
| `--mode plan/build/edit/yolo/guarded` | 默认 yolo（-p） | 02-cli-reference.md |
| `--model` / `--print` / `--allowed-tools` / `--permission-mode` | ❌ 不存在 | 官方明确列为"不支持的参数，不要使用" |
| app-server 私有 NDJSON 协议 | 未采用 | [feedback#286](https://github.com/zai-org/feedback/issues/286)：未文档化未版本化，第三方抱怨；文档称"协议服务入口，不是批量问答命令" |

### 会话持久化

`~/.zcode/cli/db/db.sqlite`（`session` 表含 `id`/`directory`/`title` 等）为桌面端与 CLI 共享库——注意：查询历史用量区分归属时不能只看此库。官方可用 `ZCODE_SESSION_DB_PATH`/`ZCODE_STORAGE_DIR` 重定向实现桥接隔离。

## 无头默认模型问题的根因与解决（2026-11-04 已跑通）

症状：`zcode -p` 报 `Select a model before sending a message`。

根因（三层连锁，经源码 + 官方文档交叉定位）：

1. standalone CLI 必须同时拿到 `ZCODE_BUILTIN_PROVIDER_CONFIG_FILE` + `ZCODE_PERSONAL_PROVIDER_CONFIG_FILE`（`gPr` 规则，二者缺一报错）；桌面终端继承的 env 指向桌面 endpoint 物化文件（内容为空壳），其中没有 `individual-coding-plan` provider 声明 → 账号候选为空。
2. 账号 provider 经凭证存在性过滤（`account-provider:<providerId>:identity` 等键），本机凭证只有 team 变体的 API key，缺 `account:bigmodel-individual-coding-plan` 的 identity/auth-source/api-key 三键 → provider 被过滤且错误被静默吞掉。
3. 注册表为空时，所有默认模型选择（含写入 personal 配置的 `config.defaultModelSelection`）都在校验环节被丢弃。

解决（已固化并 E2E 验证：创建→工具干活→resume 续聊全通）：

- 新增 `~/.zcode/cli/provider/zcode-builtin.json`（取自桌面 3.14.5 物化文件副本，声明 `account:bigmodel-individual-coding-plan`，access `{type: zhipu-account, accountType: bigmodel, mode: individual-coding-plan}`，anthropic-messages API）+ `provider_config.json`（`config.defaultModelSelection = {providerId: "account:bigmodel-individual-coding-plan", modelId: "GLM-5.3-Flash", options: {reasoningLevel: "max"}}`）。
- `~/.zcode/v2/credentials.json` 补 3 个键（值复用既有凭证）：`account-provider:account:bigmodel-individual-coding-plan:identity`、`...:auth-source=manual`、`account-provider:coding-plan:account:bigmodel-individual-coding-plan:account:53641734509380425:api-key`。原文件备份 `credentials.json.bak-bridgeresearch`。
- 官方文档验证：[03-config.md](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/03-config.md) 确认 `config.defaultModelSelection` 即官方模型默认选择键、`ZCODE_PERSONAL_PROVIDER_CONFIG_FILE` 为官方覆盖变量；旧版 `~/.zcode/cli/config.json` 的 model/provider 属一次性导入的遗留格式（解释了早期改该文件无效）。

标准调用配方：

```bash
env -u ZCODE_OFFICIAL_ENDPOINT -u ZCODE_OFFICIAL_ENDPOINT_TOKEN \
  ZCODE_BUILTIN_PROVIDER_CONFIG_FILE="C:\Users\PC\.zcode\cli\provider\zcode-builtin.json" \
  ZCODE_PERSONAL_PROVIDER_CONFIG_FILE="C:\Users\PC\.zcode\cli\provider\provider_config.json" \
  node "C:\Users\PC\AppData\Local\Programs\ZCode\resources\glm\zcode.cjs" \
  -p "<任务书>" --cwd <仓库路径> --json
```

## 消息回传 Codex 的通道评估

| 通道 | 评估 |
|------|------|
| Codex 同步 spawn `zcode -p`（每轮子进程） | ✅ 采用：双侧全官方 flag；进程退出即结果返回，零协议风险 |
| ZCode 侧调 `codex exec resume <编排会话id>` 主动推 | ⚠️ 备选：官方 flag，但同一会话 TUI+exec 并发有冲突风险，TUI 不实时刷新 |
| 双 app-server（桥接守护进程） | ❌ 暂不：Codex 侧协议文档化，但 ZCode 侧私有协议无承诺，升级即断 |

## 遗留注意点

- builtin 配置为静态副本，ZCode 升级后模型清单变化需更新（或走 `zcode login` 正式生成）。
- 无头 `-p` 默认 yolo 无审批，信任边界需在设计里明确。
- 实测单轮无头调用上下文约 44 万 input tokens（13 个共享插件注入），桥接可精简。
- resume 跨 ZCode 版本兼容未验证。
