# web_c 排队与多端同步 + 会话文件清单按需检出开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 方案设计与文档登记 | ✅ 完成（2026-11-27） | 完成架构核对与计划建档（branch: `feat/webc-queue-and-session-files`） |
| Phase 2 | 主线 1：会话文件清单 + `checkout_session_file` 按需检出 | ✅ 完成（2026-11-27） | 覆盖 Web/web_c/IM（`attachments`+`metadata`）双角色文件清单、按需隔离拷贝检出、`file_assets` DB 兜底与 `read_tool` 租户域/共享知识库校验 |
| Phase 3 | 主线 2：`web_c` 排队不强杀 + 跨端同步 + 观测一致性 | ✅ 完成（2026-11-27） | `queue_on_busy` FIFO 排队、`finalizer` 事务原子落库、修复 `active-runner` 404、前端排队态与切回同步、全路径同步 `superseded`/`superseded_by_other_client` 语义 |
| Phase 4 | 开发者自测 + 独立测试 + 独立 CodeReview | ✅ 完成（2026-11-27） | 独立测试 306 单测 + 27 SQL + 79 前端 Vitest + `web run build` 0 回归；独立 CR 发现 3 Blocking + 2 Major 已全部闭环并复核通过（修复后重跑 200 单测 + 14 SQL + Vitest + build 全绿）；待合并与真机验收 |

---

## 1. 背景与问题定性

基于 2026-11-26 `agent2` 会话 `tenant_e9b2fab93a2f_web_c_ext_lisi_sales-assistant`（`tr_runner_249a2a2b1c534d529ddb22e0444df45d`、`tr_runner_8fa9ada20354478fbd174f0db72ce712`）实证排查，并核对最新 `master`（含 `82a9187b`、`d1580705`）后确认以下核心缺口：

1. **跨轮找不到历史文件（含 AI 产出 PDF/Word）**：
   - `d1580705` 的 `read_user_attachments` 仅查询 `role = 'user'`，完全漏掉本案中 AI 生成交付的 `role = 'assistant'` 附件（如 `file_1fba1c4a843e.pdf`、`file_3947ea252452.docx`）；
   - 每轮在 `context_assembler` 阶段无条件将历史附件全量 `shutil.copyfile` 到 `skill_ws_/history_files/` 造成纯文字轮次 IO 浪费，且仅查 Redis 未优先兜底持久化 `file_assets` 表。
2. **多端同会话并发与观测不一致**：
   - `web_c` 复用 `ChannelRunnerAgent.submit()` 在同会话有活跃 runner 时直接抢占取消，双端同时打开页面时 B 端发消息会静默杀掉 A 端任务；
   - `web_c.py` 的 `active-runner` 端点请求路径缺少 `/runners` 后缀导致 404，前端无法在刷新或切回前台时真实感知在途任务；
   - `worker._persist_trace()` 仅投影 `status = 'superseded'` / `'interrupted'`，未同步修正 `metadata.termination_reason`（残留 `user_cancelled`）与 `tags`，且无法区分同端/其他端抢占；
   - `WebCChat.vue` 在被取代终态未清理 `streamFailed`，与取代提示文案冲突并存。

---

## 2. 技术方案

### 2.1 主线 1：会话文件清单 + 按需检出

1. **统一会话文件元数据读取**（`src/services/agent_runner/runtime/history_repository.py`）：
   - 新增 `read_session_files(session_id, limit=100, roles=('user', 'assistant'))`：
     - `channel_messages`：读取未压缩、未撤回、活跃的 `user` + `assistant` 消息 `attachments` 与 `metadata.downloadableFiles`/`images`/`attachments`（同时覆盖 `web_c` 与企微/企微客服/飞书/钉钉交付物）；
     - `chat_messages`：读取未压缩的 `user` `metadata.attachments` 及 `assistant` `metadata.downloadableFiles`/`metadata.images`/`metadata.attachments`；
     - 按 `file_id` 去重（保留最新）并标注 `origin`（`user_upload` / `assistant_generated`），按时间正序返回最近上限（默认 20 条）；
     - 保留 `read_user_attachments` 作为兼容封装（委托 `roles=('user',)`）。
2. **轻量上下文清单注入**（`src/services/agent_runner/runtime/attachments.py` + `context_assembler.py`）：
   - 当轮新上传附件保持立即物化进执行工作区不变；
   - 历史会话文件不再每轮盲拷磁盘，改为在 `enhanced_input` 注入紧凑文件清单（`file_id`、文件名、来源、大小），并指引按需调用 `checkout_session_file(file_id)` 或直接传 `file_id`。
3. **按需检出服务与平台控制工具**（`src/services/files/session_file_checkout.py`、`src/tools/file/checkout_session_file_tool.py`、`src/tools/control_set.py`、`src/services/agent_runner/runtime/controls.py`）：
   - 新增 `checkout_session_file(file_id)` 控制工具（通过 `ToolControlSet` 暴露给所有 Master/Standalone/Subagent，包括 `sales-assistant`）；
   - 安全边界（fail-closed）：校验 `file_id` 格式、校验归属当前 `session_id` 的会话文件列表、解析物理路径走 **`file_assets` DB → Redis → `resolve_scoped_uploaded_file` 租户域校验**；
   - 检出目录挂在当前执行 `state.resources["workspace"]` 下 `session_files/{file_id}/`（惰性创建，随 `worker._cleanup_completed_resources` 自动清理），采用 `shutil.copyfile` 隔离拷贝（不使用硬链接，防技能原地编辑篡改持久原件），保留原始扩展名；
   - 同步增强 `src/core/storage.py` 的 `resolve_uploaded_file_path` 增加 `file_assets` DB 兜底（且存在工具上下文时走租户域 `resolve_scoped_uploaded_file`）及 `ReadTool._resolve_path` 的租户域 `file_id` 解析（与 `cp_tool` 同口径放行已授权共享知识库）。

### 2.2 主线 2：`web_c` 方案 A 排队 + 跨端同步 + 观测一致性

1. **`web_c` 同会话排队不强杀**（`src/channels/runner_agent.py`、`src/saas/api/web_c.py`、`src/services/agent_runner/finalizer.py`）：
   - `ChannelRunnerAgent.submit()` 新增 `queue_on_busy: bool = False`（`web_c` 入口传 `True`；企微/钉钉/飞书保持原有取代语义不变）：
     - 若存在 `waiting` 澄清等待 → 照常走 reply control；
     - 若存在 `running` / `finalizing` / `queued` → **不取消旧 runner**，直接提交新 runner 进入 `agent_runners` FIFO 队列（由现有 `ExecutionRepository.acquire` + `agent_runner_session_claims` 严格按 `queue_order` 串行消费）；
     - 仅对卡死的 `paused` / `interrupted` / 非澄清 `waiting` 清理取消，防止会话锁死。
   - **严格回合顺序与零竞态持久化**：
     - `finalizer.py` 在 `web_c` 终态事务中（释放 `agent_runner_session_claims` 之前）原子幂等落库 `user` 消息（按 `client_request_id` `ON CONFLICT DO NOTHING`）与 `completed` 时的 `assistant` 回复及 `attachments`（按 `runner_id` `ON CONFLICT DO NOTHING`）；
     - `web_c.py /chat` 在提交前/受理时判断会话是否已有在途 runner：空闲会话仍立即落库 `user` 消息；排队会话延迟到 `finalizer.py`（或 `waiting` 反问落库前）写入，保证 `channel_messages` 严格 `user1 → assistant1 → user2 → assistant2`，不出现连续 user 导致上下文错位；
     - `web_c_runner_persist.py` 保持幂等补写作为最终兜底。
2. **跨端状态感知与排队交互**（`src/saas/api/web_c.py`、`frontend/web/api/webC.ts`、`frontend/web/components/webC/WebCChat.vue`）：
   - 修复 `web_c.py` `GET /sessions/{session_id}/active-runner` 缺少 `/runners` 路径后缀及 `profile_id` 参数的 404 缺陷，同时返回 `queued` 与 `active_runners` 摘要；
   - `WebCChat.vue` 维护每标签页 `client_id`（`sessionStorage`）随 `/chat` 透传；监听 `visibilitychange` / `focus` 自动刷新消息并接管活跃 runner；
   - 排队状态展示清晰提示「当前会话正在处理上一条消息，您的消息已排队等待…」，支持停止当前排队任务，切入 `running` 后平滑流式输出；
   - 修复被取代终态残留 `streamFailed` 导致「本轮对话未完成，请重试」与取代提示并存的问题。
3. **取消与取代观测彻底一致**（`src/services/agent_runner/contracts.py`、`repository.py`、`public_view.py`、`worker.py`、`SessionTraces.vue`、`TraceDetail.vue`）：
   - 新增 `SUPERSEDED_BY_OTHER_CLIENT_CANCEL_REASON = 'superseded_by_other_client'`；当发生取代且新旧 `client_id` 不同时传入该原因，否则沿用 `superseded_by_new_message`；
   - `worker._persist_trace()` 投影 `superseded` / `interrupted` 时同步改写 `collector.trace.metadata['termination_reason']` 与 `tags`，消除 `status=superseded` 却残留 `termination_reason=user_cancelled` 的矛盾；
   - 监控页区分展示「被其他端新消息中断」与「被新消息取代」。

---

## 3. 验证计划

1. 后端单测（WSL2 `docker compose -f docker-compose.dev-tools.yml run --rm python`）：
   - 会话文件清单（user + assistant、Web + channel）、按需检出（硬链接/拷贝、跨租户与跨会话拒绝、DB 兜底、清理）、`read` / `storage` `file_id` 解析；
   - `web_c` 排队受理、顺序落库、`active-runner` 真实路径、取消原因区分与 trace `termination_reason`/`tags` 一致性。
2. 真实 PostgreSQL 集成测试（`docker compose -f docker-compose.test-db.yml run --rm sql`）：
   - `agent_runners` FIFO 排队、finalizer 原子落库 `web_c` 消息与会话 claim 释放顺序。
3. 前端测试与构建（`docker compose -f docker-compose.dev-tools.yml run --rm web`）：
   - `webC` / `monitor` 相关 Vitest 用例 + `npm run build`。
4. 独立验证智能体与独立 CodeReview 智能体审查通过后提交推送。
