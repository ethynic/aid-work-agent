# 多会话后台流式开发计划

> 关联设计文档：[multi-session-background-streaming-design.md](../system/multi-session-background-streaming-design.md)
> 登记：[ideas.md](../ideas.md) 前端分区 #43

## 目标

切换历史会话 / 新建会话 / 切换数字员工不再中断正在进行的 Agent 会话；后台会话继续流式生成，切回可见实时内容；会话列表显示进行中 spinner 与完成小点。

## 任务清单

| # | 任务 | 文件 | 状态 |
|---|------|------|------|
| 1 | useAgent 重构为 per-session 状态池（`shallowReactive` Map + computed 视图代理；sendMessage 按会话 guard；switchSession 不再 abort；删除 onUnmounted 断连；新增 isSessionRunning / hasSessionUnreadCompletion / removeStreamState） | `frontend/src/composables/useAgent.ts` | ✅ 完成 |
| 2 | MenuSidebar：删除 handleSelectSession / handleNewSession 的中断 confirm；会话列表项加 spinner / 完成小点；删除会话时清理流式状态 | `frontend/src/components/MenuSidebar.vue` | ✅ 完成 |
| 3 | ChatContainer：handleSubagentChange 删除中断 confirm；删除 watch(isProcessing) 清缓存逻辑 | `frontend/src/components/ChatContainer.vue` | ✅ 完成 |
| 4 | 新增多会话单元测试（7 用例）+ 前端构建/全量测试回归 | `frontend/src/__tests__/composables/useAgentMultiSession.test.ts` | ✅ 完成 |
| 5 | 设计文档 + 开发计划 + ideas.md 登记 | `docs/system/multi-session-background-streaming-design.md` 等 | ✅ 完成 |

## 验证结果（2026-07-20）

- `npm run build` ✅ 0 错误
- 新增测试 7/7 通过 ✅
- 全量测试回归：除 `RpaBindingPanel.test.ts`（未跟踪新文件，**预先存在**失败，已通过 stash 验证与本次改动无关）外全部通过 ✅

## 待手动验证（E2E，需真实环境）

1. travel-consultant 会话发长任务 → 会话列表该项出现 spinner
2. 直接点击另一历史会话（无 confirm 弹窗）→ 显示历史消息；原会话 spinner 继续转
3. 后台任务完成 → spinner 变为完成小点
4. 点回原会话 → 小点消失，显示完整回复内容
5. 新建会话 / 切换数字员工同样无弹窗，原会话后台继续
