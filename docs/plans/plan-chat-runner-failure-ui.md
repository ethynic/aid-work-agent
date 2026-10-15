# 对话失败提示与重试修复计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 修订既有 Runner 展示设计、明确重试语义 | ✅ 完成（2026-10-15） | 接单失败与执行失败分开处理，失败提示附在用户消息旁 |
| Phase 2 | 消息投影、重试交互及回归测试 | ✅ 完成（2026-10-15） | 原输入失败提示/重试、历史恢复与草稿隔离完成，保留部分回复及产物 |
| Phase 3 | 独立测试、CodeReview、build 与视觉检查 | ✅ 完成（2026-10-15） | 独立测试 97 passed、CR 两项 P2 关闭、build 与桌面/窄屏/键盘检查通过；未部署 |
| Phase 4 | 成功重试清理、手动删除及紧凑消息 UI | ✅ 完成（2026-10-15） | 直接在 master 修改；持久展示偏好、成功关联链清理、灰色图标及紧凑气泡完成 |
| Phase 5 | 清理语义独立测试、审查与视觉回归 | ✅ 完成（2026-10-15） | 独立后端 11 passed、前端 109 passed，最终 build/视觉通过；CR P2 已关闭，用户授权提交，待手动部署 |

## 设计与范围

更新 [AgentRunner 架构设计 §6.1](../system/agent-application-architecture-design.md#61-一份执行事实多种呈现)，遵循 [Runner / Desktop / Runtime 共同契约](../system/runner-desktop-runtime-integration-contract.md)。截图中的执行失败没有被投影成普通用户可见的消息失败态，空白助手框仍被渲染。

- 使用现有消息气泡、语义颜色和 BaseButton，在失败用户消息下显示「消息未发送成功」和「重试」。网络受理未确认仍使用既有自动重试提示，不虚报服务端未接单。
- 已接单但执行失败时，由 Runner 状态重建提示；没有文本、图片、文件或人工协助产物的空白最终助手消息不渲染。已有部分内容及澄清记录保留。
- 提交失败沿用原请求体、原 client_request_id 幂等重放；已明确执行失败的任务仅在用户点击后作为新任务发送，生成新请求键。原失败任务及历史保留，不自动恢复或重放未知外部效果。
- 执行失败重试使用原消息正文和附件，读取经过原鉴权的会话信息取得所属数字员工/实例；复用已公开的 profile_id 字段，不新增服务端 wire 字段。不能消费或覆盖用户当前输入框附件草稿。
- 重试检查当前用户、会话、状态池 generation、任务终态与并发占位；切换账号/会话、清理状态或另一任务在途时不能发起旧动作。不回退 legacy SSE。
- 沿用 frontend-design 和高风险 dev-workflow；主控开发自测，独立测试与独立 CR 验证身份、幂等和交互边界。

## 验收标准

1. 即时失败、刷新历史、两条连续失败均显示用户消息失败提示与重试入口，没有空白助手框。
2. 重试发送原文本及附件，保留数字员工/实例，不覆盖草稿；执行失败使用新键，网络未知受理继续复用同一新键。
3. 连点、已有任务在途、旧账号/会话动作不产生重复或错上下文请求；既有暂停、澄清、取消、部分回复和产物展示不回归。
4. 定向 Vitest、前端 build、独立验证通过，桌面/窄屏视觉与键盘入口检查完成。

## 失败消息清理与紧凑展示（2026-10-15 追加）

- 使用现有 `chat_sessions.metadata.chat_failed_message_ui` 保存 `hidden_failed` 和 `retry_sources`（新请求键 → 原失败 Runner）；专用会话接口采用 fresh owner/tenant 校验、事务行锁和逐项更新，保留其他 metadata。两类映射各限制 2000 项，仅返回展示偏好，不修改 Runner wire、执行历史或计费审计。
- 新重试请求发出前持久化明确关联；只依据关联的新 Runner `completed` 隐藏对应旧失败用户消息。重试仍在执行、再次失败、被取消均不清理；连续失败后最终成功沿关联链清理祖先失败输入，不按文本匹配。刷新后从同会话历史及任务事实重建。
- 手动删除仅对明确失败项开放：已接单失败更新持久展示偏好；明确拒绝的本地提交失败移除该本地乐观轮次。未知是否受理且仍自动重试的消息不提供删除，避免掩盖仍执行的任务。失败输入被移除后，原轮次已有的部分回复、澄清和产物保留。
- 失败文案改为灰色感叹号与「消息未发送成功」，去除长解释；受理未知保留准确的自动重试状态。重试和删除使用消息气泡旁的图标按钮，提供标题、无障碍名称、键盘焦点和进行中禁用态。缩小气泡内边距并去除 Markdown 首尾段落外边距，不压缩正文段落间距。
- 本批涉及持久展示偏好与异步重试关联，按高风险流程做独立测试和独立 CR。用户后续已明确授权提交代码，部署由用户手动执行。

### 追加验证记录

- 主控定向回归：`cd frontend && npm test -- web/__tests__/utils/runner web/__tests__/components/MessageItem web/__tests__/composables/useAgent --reporter=dot` → **12 文件、109 passed，0 failed / skipped**（退出码 0）。覆盖对应源清理、成功/失败/取消和失败祖先链、刷新、手动删除成功/失败、草稿、旧闭包及身份边界。
- 独立 CR 发现并关闭一个 P2：删除前方失败消息会移动数组，旧版 SSE 按固定 index 写入可能丢失后续回复。已给该轮助手占位稳定 ID，并按 ID 更新内容/完成时间；新增“第一段 → 删除旧项 → 第二段及完成”交错回归。消息列表 key 同步使用 messageId，避免删除后组件内状态错位。最终复核无遗留可操作 P0/P1/P2。
- 最终 build：`cd frontend && npm run build` → vue-tsc、依赖边界、Vite 与 Web 产物校验通过（退出码 0）。过程曾发现新增测试使用 ES2022 的 `.at()` 与项目目标不符，已改用既有数组索引写法后重跑通过；既有 Browserslist/大分包提示不属于本批。
- 本地真实 MessageList/MessageItem 浏览器 fixture：1200×650 与 375×812 截图已核对；图标在气泡旁，灰色感叹号仅配简短失败文案，助手内边距 10px/14px，首尾 Markdown margin=0，无横向溢出。键盘 Enter 重试一次成功后旧项移除，Enter 删除另一失败项；pageerror=0。此为组件 fixture，不冒充部署页面验收；临时 fixture/开发服务器已清理，截图在本机可视化目录。
- 独立后端集成验证：`bash scripts/dev_test.sh --isolated-db tests/integration/agent_runner_service/test_web_failed_messages.py -p no:cacheprovider -q --tb=short` → **11 passed，0 failed / skipped**（84.39s，退出码 0）。使用本机 `aid-agent-api` 容器 `/tmp/aid-failed-msg-backend-20261015` 独立源码快照，SHA256 与工作区两个实现文件一致；真实隔离 PostgreSQL 与双 gateway 验证跨进程刷新、8 并发写入不丢、原 metadata/消息/Runner/chat_records 不变、幂等不可重绑、容量限制、fresh 失效身份/租户隔离、NULL 管理员范围、非 failed 或异会话/渠道 Runner 拒绝及非法输入。未使用运行中 `/app` 旧源码代替验证，未部署或操作正式服。
- 独立前端回归同上命令 → **12 文件、109 passed，0 failed / skipped**（2.68s，退出码 0），确认关联先持久化、completed 才清理、刷新、手动删除失败保留、未知受理不提供删除、部分产物保留及 legacy 稳定 ID 覆盖；无新增问题。
- 提交前 `git fetch origin` 后以 fast-forward 同步 `master` 到 `0732a09c`，远程改动与本任务文件不重叠；未 stash 或覆盖其他任务改动。同步后再次运行上述前端回归 → **109 passed，0 failed / skipped**（2.80s），`npm run build` 全部通过（退出码 0）。用户已授权提交并推送 `master`，本助手不执行部署。

## 验证记录

- 开发自测初轮：`cd frontend && npm test -- web/__tests__/utils/runner web/__tests__/components/MessageItem web/__tests__/composables/useAgent` → **12 文件、94 passed，0 failed / skipped**；最终竞态修复后定向 `npm test -- web/__tests__/composables/useAgentRunner.test.ts --reporter=dot` → **36 passed，0 failed / skipped**（退出码均 0）。
- 开发环境：Node v24.13.0、npm 11.6.2；隔离分支 `codex/chat-runner-failure-ui`，依赖复用原仓库 frontend/node_modules。未操作任何服务器。
- 独立测试最终状态：`npm test -- web/__tests__/utils/runner web/__tests__/components/MessageItem web/__tests__/composables/useAgent --reporter=dot` → **12 文件、97 passed，0 failed / skipped**（退出码 0）。独立临时用例覆盖迟到身份/会话错误、草稿、清缓存旧闭包及切会话竞态，已清理；不将不同版本或重复执行结果相加。
- 独立 CodeReview：发现两个 P2（重试等待能力/余额后仍向旧会话发送；迟到重试清除另一发送持有的占位），均已修复并通过最终复核，无遗留可操作 P0/P1/P2。活动 SID 检查贯穿首次 POST 前各等待点，局部 ownsSelection 只释放本调用占位；普通发送后台语义及已发出请求的同键确认保持不变。
- 最终构建：`cd frontend && npm run build` → vue-tsc、依赖边界、Vite 与 Web 产物校验全部通过（退出码 0）；保留既有 Browserslist 数据较旧及大分包提示。
- 视觉/键盘：本地 Chromium 用真实 MessageList/MessageItem 与模拟 Runner 失败事实渲染两条「上海的」，1200×650 / 375×812 截图已人工核对；两条失败均位于用户消息旁、没有失败助手框，正常助手回复保留，无横向溢出。Enter 触发重试恰好一次并禁用重试中按钮，pageerror=0。此检查是组件浏览器 fixture，不冒充已部署页面或真实模型验收；fixture 和开发服务器已清理。
- 前一 failover 修复已通过 0b6c930e 合入 Codeup/GitHub master；2026-10-15 用户已明确授权提交本次 UI 修复并合入 master，未部署。
