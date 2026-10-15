# 对话失败提示与重试修复计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 修订既有 Runner 展示设计、明确重试语义 | ✅ 完成（2026-10-15） | 接单失败与执行失败分开处理，失败提示附在用户消息旁 |
| Phase 2 | 消息投影、重试交互及回归测试 | ✅ 完成（2026-10-15） | 原输入失败提示/重试、历史恢复与草稿隔离完成，保留部分回复及产物 |
| Phase 3 | 独立测试、CodeReview、build 与视觉检查 | ✅ 完成（2026-10-15） | 独立测试 97 passed、CR 两项 P2 关闭、build 与桌面/窄屏/键盘检查通过；未部署 |

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

## 验证记录

- 开发自测初轮：`cd frontend && npm test -- web/__tests__/utils/runner web/__tests__/components/MessageItem web/__tests__/composables/useAgent` → **12 文件、94 passed，0 failed / skipped**；最终竞态修复后定向 `npm test -- web/__tests__/composables/useAgentRunner.test.ts --reporter=dot` → **36 passed，0 failed / skipped**（退出码均 0）。
- 开发环境：Node v24.13.0、npm 11.6.2；隔离分支 `codex/chat-runner-failure-ui`，依赖复用原仓库 frontend/node_modules。未操作任何服务器。
- 独立测试最终状态：`npm test -- web/__tests__/utils/runner web/__tests__/components/MessageItem web/__tests__/composables/useAgent --reporter=dot` → **12 文件、97 passed，0 failed / skipped**（退出码 0）。独立临时用例覆盖迟到身份/会话错误、草稿、清缓存旧闭包及切会话竞态，已清理；不将不同版本或重复执行结果相加。
- 独立 CodeReview：发现两个 P2（重试等待能力/余额后仍向旧会话发送；迟到重试清除另一发送持有的占位），均已修复并通过最终复核，无遗留可操作 P0/P1/P2。活动 SID 检查贯穿首次 POST 前各等待点，局部 ownsSelection 只释放本调用占位；普通发送后台语义及已发出请求的同键确认保持不变。
- 最终构建：`cd frontend && npm run build` → vue-tsc、依赖边界、Vite 与 Web 产物校验全部通过（退出码 0）；保留既有 Browserslist 数据较旧及大分包提示。
- 视觉/键盘：本地 Chromium 用真实 MessageList/MessageItem 与模拟 Runner 失败事实渲染两条「上海的」，1200×650 / 375×812 截图已人工核对；两条失败均位于用户消息旁、没有失败助手框，正常助手回复保留，无横向溢出。Enter 触发重试恰好一次并禁用重试中按钮，pageerror=0。此检查是组件浏览器 fixture，不冒充已部署页面或真实模型验收；fixture 和开发服务器已清理。
- 前一 failover 修复已通过 0b6c930e 合入 Codeup/GitHub master；2026-10-15 用户已明确授权提交本次 UI 修复并合入 master，未部署。
