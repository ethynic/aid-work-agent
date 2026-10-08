# 微信客服原渠道恢复与 AgentRunner 接入计划及复盘

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 恢复原 KF 渠道业务 | ✅ 完成（2026-10-08） | adapter/API/budget 对齐恢复基线；原回调、队列、ASR、历史及发送保留 |
| Phase 2 | 对话接入独立 Runner 服务 | ✅ 完成（2026-10-08） | HTTP 提交渠道身份，通用追问/取消；明确历史及费用唯一责任方 |
| Phase 3 | 清除渠道误重构及专属部署 | ✅ 完成（2026-10-08） | 删除专用管线、接口和测试；移除两个 KF 容器；文档同步 |
| Phase 4 | 本地验证与独立审查 | ✅ 完成（2026-10-08） | 190 定向通过；独立 87 通过；CR 无阻断；入口/语法检查通过 |
| Phase 4a | 历史迁移与发布顺序纠正 | ✅ 完成（2026-10-08） | 历史字节保留，仅追加 gate 退役；73 定向通过；独立测试/CR 通过 |
| Phase 5 | 已部署环境切换与真机验收 | ✅ 完成（2026-10-08） | agent2 发布 `1f7b2ca2`（15:33）：gate 退役迁移实际执行（`_db_update_applied` 推进至 15:21:10）；kf 两容器随 `--remove-orphans` 移除；agent3 残留 `AGENT_RUNNER_WECOM_KF_ENABLED` 已清并重建；真机文本/语音(ASR)/合并缓冲/新消息取代旧任务全部验收通过（语音 13 秒闭环） |

## 方向结论

原“语音修补与会话解锁”方案及其 Phase 2/2b 已被本方案取代，不再作为开发要求。本文保留最终实施边界、验证证据和复盘结论；旧文件 `plan-kf-voice-fix-and-session-unlock.md` 已移除，引用统一更新到本文。

本次授权范围是重构 Agent/AgentRunner 并让渠道使用它提供对话服务。微信客服渠道本身恢复 `33f9dba1^` 的原实现；新 AgentEngine、独立 Runner API/worker 和 Web 接入保留。

原 KF 回调验签/解密、拉取 cursor、消息过滤与去重、合并、撤回、语音 ASR、人工状态、账号拦截、欢迎语、工具业务、历史、回复渲染发送及 recap 继续由原渠道负责。原 `process_and_persist → session_queue` 本来就有 Redis 合并、取消及 pending 行为，按原实现保留。

## 最终执行边界

```text
微信回调 → 主 API 原 KF 渠道 → 原 session_queue
                               ↓ HTTP（source=wecom_kf）
                       共用 runner-api → runner-worker → 新 AgentEngine
                               ↓ 结果/追问/工具消息
                   原渠道历史 → 原渲染发送 → 成功后 recap
```

| 能力 | 责任方 | 最终行为 |
|------|--------|---------|
| 对话执行 | 独立 Runner API/worker | `ChannelRunnerAgent` 转换调用接口，主 API 不运行模型或 AgentEngine |
| 来源与权限 | 主 API 可信 peer、Runner 鉴权 | 提交 source、session、客户、客服账号、profile、配置 ID；校验租户及绑定，保留租户订阅语义 |
| 工具业务 | 原工具、worker 内 KF ContextVar | 从已授权配置重建上下文，退出时重置并关闭 adapter；转人工沿原工具执行 |
| 合并与取消 | 原 session_queue、通用 Runner controls | 新消息取代旧执行时确认取消终态再交接；网络重试复用同请求键；追问回复继续同一执行，包括子智能体嵌套追问 |
| 执行所有权 | 通用 claim/attempt/lease | 终态 finalizer 释放 claim；无 KF 专用接单或投递 gate，发送不延长 claim |
| 历史 | 原 ChannelSessionManager | 保存原 msgid、合并及撤回关系；Runner 返回脱敏工具消息，避免重复写 KF 历史 |
| 费用 | Runner receipts/finalizer、原 ASR 记录 | Runner 结算模型/工具；渠道原 SessionRecord 单独记录入口 ASR，避免重复结算 |
| 渲染、发送及 recap | 原渠道 | 保留原预算与降级行为，发送成功后按原规则触发 recap |

主 API 的共用 peer 授权 `chat` 与 `wecom_kf`，不新增 KF 专用服务令牌。语音降级附件沿原 conversation 文件通过同租户 `file_id` 传递，避免 base64 HTTP 大小限制；不扩展原渠道支持的消息类型。不支持的工具等待及 paused/interrupted 状态明确取消或报错；取消不撤销已生效工具操作，不盲目重演未知副作用。

## 清理与数据保留

已删除 KF 专用 ingress/admission、上下文及语音准备、投递状态机、wire operation、recap handoff、client pool、对应专有测试及 `/v1/source-*` 接口。闲置 InputRepository、source receipt 类型、worker 输入准备、追问恢复和历史/费用投影一并删除，源码不再查询 `agent_runner_inputs`；旧 checkpoint 明确拒绝，不交新版重新执行。

旧实施/交接文档已删除，应用架构、Runner 计划、数据库/文件说明及索引更新为上述边界。`docker-compose.agent-runner.yml` 只保留共用 `runner-api`、`runner-worker`；KF 专属两个容器的 service/profile/资源和启动配置已移除。

**历史数据库记录保留**：`db_update.yaml` 和累积 `init-postgres.sql` 的已提交历史完整保留，新库仍沿历史创建退役 KF 表，运行代码不消费。旧表、消息和审计数据不自动 DROP，不提供可选清理块。

仅在末尾追加 `2026-10-08 15:21:10` 退役操作：`ALTER TABLE IF EXISTS agent_runner_session_claims DROP COLUMN IF EXISTS gate`，初始化 SQL 末尾同步，使新库与升级库最终结构一致。当前更新器按 `_db_update_applied.last_datetime` 执行；历史 `file_hash` 字段不控制是否重跑。本次只准备文件，未执行真实迁移。

## 发布与待验收

具体步骤以 [部署检查清单](../../deploy/agent-runner-deploy-checklist.md) 为准，发布前必须完成：

1. 在旧制品冻结 KF 新流量，核对全部已拉取消息（含未 accepted）及实际送达结论；排空或明确取消旧任务，副作用不确定时人工核对。
2. 全部消息有处理结论后，将最终 cursor 交接给原 `CursorManager` 的环境隔离 Redis 键；不能提前推进跳过未处理消息，也不能留空重放已处理消息。
3. 冻结新 Runner 提交，停止旧 KF 消费者及仍读写 gate 的旧共用 Runner API/worker；旧 Web 任务按既有排空/安全暂停/取消流程保留，不强删 claim。
4. 分环境核对并清理旧 KF 专用 token、peer 和开关，保留共用 Runner 凭据；再运行授权的发布与迁移。`--remove-orphans` 仅清理同 compose project 的旧容器，不能代替迁移前停机确认。
5. 验收真实语音、合并取消、追问、转人工、渲染发送、撤回及 recap，同时核对实库历史和费用没有重复。

移除 gate 后不能直接启动旧 KF native 制品；恢复旧方案须另行准备恢复列/原约束的新增兼容迁移或核对过的备份恢复方案，不改写历史、不重跑已完成任务。

**agent2 已按上述要求完成切换（2026-10-08 15:33，见 Phase 5）**。实际执行说明：native 管线的拉取 cursor 存于 `wecom_kf_account_sync` 表，原渠道 `CursorManager` 的 Redis cursor 全程未被 native 管线触碰，故无需 cursor 交接动作；切换前 native 管线内消息均已到达终态结论（含一条已知 `closed_unknown` 长图投递丢失，用户侧重发后恢复）；KF 专用 token/peer/开关已从 agent2 与 agent3 的 `.env` 清除（备份留存）。生产环境切换时仍须完整执行上述 1-5 步。

## 验证记录（2026-10-08）

| 验证 | 结果 | 证据边界 |
|------|------|---------|
| 主控原 KF、Engine、Runner、后台及持久限制回归 | 190 passed，无失败/跳过 | `scripts/dev_test.sh` 定向测试 |
| 独立接入与相邻回归 | 87 个不同用例通过 | 新增 KF 接入 41 项、Web/core/persistence 46 项；实际 httpx→ASGI→授权/控制，以及 Coordinator→claim_resume |
| 迁移纠正后的组合验证 | 73 passed，无失败/跳过 | 迁移 32 项（新增退役 6 项）及 KF 接入 41 项；与上面用例有重叠，不相加 |
| 独立 CodeReview | 无 P0/P1 阻断 | 修复并复核契约、追问、参数脱敏、配置绑定、重试/取消窗口、附件及旧表恢复残留；追加迁移和停旧服务顺序复审通过 |
| 入口与文件检查 | 通过 | 8 模块与 Runner API 路由阻网导入、AST/YAML、两个脚本分别 bash 语法及差异检查；原 adapter/API/budget 对齐恢复基线 |
| 历史迁移比对 | 原前缀逐字节一致 | YAML/初始化 SQL 分别仅末尾新增 7 行/4 行，无 DROP TABLE/CASCADE |

HTTP/恢复测试替换存储事务、worker 状态和外部平台端口；费用测试验证调用归属，未证明真实 PostgreSQL 幂等扣费。迁移测试使用实际更新器与 fake SQL 连接，未证明真实 PostgreSQL DDL 已执行。本机无 Docker，未启动容器或服务 lifespan。测试强制业务/日志 DB 指向本机不可连接地址并关闭 Redis，避免共享租户清理。

较早扩大回归有 3 个原有失败（下载链接配置假设、ASR 两个旧错误文案断言）及 4 个数据库 fixture 跳过，未修无关代码，不计入通过数量。本次文档重写不改变源码或验证状态，沿用上述可核验记录。

## 复盘结论

- **范围失控**：Agent 执行重构扩展成 KF 渠道重写，引入专用接单、持久输入准备、投递及额外进程。后续渠道接入先明确原业务责任方，只替换对话执行边界。
- **不能靠局部解锁修正错误边界**：原队列已有调度语义；新增渠道管线的锁与投递限制应随管线退出，通用执行所有权仍须保留并核实取消结果。
- **清理要检查实际调用链**：旧输入恢复残留曾让通用追问查询已删除表；验证应走真实装配和恢复链路，不能仅检查表层 HTTP 响应。
- **代码退役不等于删除迁移历史**：本次曾误删历史块，已恢复并改为追加退役操作；存量数据保留与旧服务停机顺序必须明确。
- **本地通过不等于已部署验收**：独立测试与 CR 只能证明各自检查的边界，真实服务切换、微信投递和实库费用须在授权发布后验收。
