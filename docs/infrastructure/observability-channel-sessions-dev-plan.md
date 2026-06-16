# 渠道会话追踪接入 — 开发计划

> 对应设计文档：[observability-channel-sessions-design.md](./observability-channel-sessions-design.md)（方案 C）
> 关联开发计划：[observability-dev-plan.md](./observability-dev-plan.md)（主计划，本计划是其 Phase 1 的延伸，覆盖原 1.3.2 项）
> 创建日期：2026-06-15
> 状态：开发完成，待端到端验证（D 阶段）

---

## 目标

将 TraceCollector 接入下沉到 `Agent.process_message()` 内部，自动从 `record_service` 读取 trace 上下文（含 source_type），实现**渠道层零改造**自动覆盖所有来源（Web/企微/微信客服/钉钉/飞书），并在追踪查看页面展示来源标签和渠道信息。

---

## 前置条件

- Phase 1 主体已完成（trace_collector、trace_persist、obs_traces 表、monitor API、前端追踪页均已就绪）
- `obs_traces.source_type` 字段已存在，TraceCollector 已支持 `source_type` 参数
- `SessionRecordManager` 各渠道调用方已传 `source_type`（事实 1，无需新增）

---

## 任务清单

### 阶段 A：采集下沉（核心改动）

- [x] **A.1 `process_message` 内部接入 TraceCollector**
  - 文件：`src/core/agent.py`
  - 实现：将原 `process_message` 重命名为 `_process_message_impl`，新增 wrapper `process_message`，从 `_explicit_record_service or SessionRecordManager.get_current_record()` 解析 record，初始化 TraceCollector，async for 中旁路 `on_event`，try/except/finally 处理 `on_error`/`on_complete`
  - `_record` 为空时静默跳过（Gradio/CLI/Scheduler 路径不产生 trace）
  - 所有 try/except 包裹，失败仅 debug 日志

- [x] **A.2 审计 `process_message` 所有 yield 点**
  - 通过 wrapper 包装，所有 yield 点零侵入覆盖

- [x] **A.3 移除 `main.py:event_generator` 旁路收集代码**
  - 删除 TraceCollector 初始化、`on_event`、`on_error`、`on_complete`、`{"type": "cancelled"}` 等约 30 行
  - 保留 `record_service` 的创建和 SSE 转发逻辑

- [x] **A.4 验证 `process_message_sync` 路径无需改动**
  - `agent.py:2452` 已设置 `self._explicit_record_service = record_service`，channel_routes.py 零改动

### 阶段 B：展示侧 API

- [x] **B.1 `list_traced_sessions` 增加 source_type 字段和筛选**
  - `SessionSummary` 增加 `source_type`、`subagent_id`
  - 新增 `source_type` Query 参数 + SQL where + `MIN(source_type)`/`MIN(subagent_id)` 返回

- [x] **B.2 `get_trace_detail` 增加 channel_info 补充**
  - `TraceDetail` 增加 `channel_info` 字段
  - `source_type in (wecom/wecom_kf/dingtalk/feishu)` 时跨库查询业务库 `channel_sessions`，失败仅 warning

### 阶段 C：前端

- [x] **C.1 monitor.ts 类型扩展**
- [x] **C.2 TraceBrowser.vue 新增「来源」列和筛选下拉**
  - 来源标签颜色：chat→gray、wecom/wecom_kf→success、dingtalk→primary、feishu→warning
- [x] **C.3 TraceDetail.vue 条件展示 channel_info**
- [x] **C.4 前端构建验证** — `npm run build` 通过

### 阶段 D：端到端验证

- [ ] **D.1 逐渠道发送测试消息** — 待用户在测试环境执行
- [ ] **D.2 SQL 验证采集** — 待用户执行
- [ ] **D.3 展示侧验证** — 待用户执行
- [ ] **D.4 回归验证** — 待用户执行

### 阶段 E：文档同步

- [x] **E.1 更新 observability-design.md §3 描述** — §3.1、§3.4 已加方案 C 修正说明
- [x] **E.2 更新 observability-dev-plan.md 1.3.2** — 已加交叉引用
- [x] **E.3 在 docs/ideas.md 登记本计划** — 已登记开发计划链接

---

## 完成标准

- [ ] 所有渠道（Web/wecom/wecom_kf/dingtalk/feishu）调用都自动产生 trace，channel_routes.py 零改动
- [ ] `obs_traces.source_type` 字段正确反映来源
- [ ] 追踪页面列表显示「来源」列并能筛选
- [ ] trace 详情页显示渠道用户信息（wecom/wecom_kf/dingtalk/feishu）
- [ ] main.py 的旁路收集代码全部移除
- [ ] Gradio/CLI/Scheduler 路径不影响业务、不产生 trace
- [ ] `cd frontend && npm run build` 通过

---

## 进度追踪

| 阶段 | 内容 | 预估 | 状态 |
|------|------|------|------|
| A | 采集下沉（核心） | 1.1d | ✅ |
| B | 展示侧 API | 0.6d | ✅ |
| C | 前端 | 0.8d | ✅ |
| D | 端到端验证 | 0.6d | ⬜（待用户在测试环境执行） |
| E | 文档同步 | 0.35d | ✅ |

**合计：约 3.5 人天**

---

## 风险与应对

| 风险 | 应对 |
|------|------|
| `process_message` 多个 yield 点和异常路径改造复杂 | 编写单元测试覆盖所有 yield 和 finally 路径 |
| TraceCollector 异常影响主流程 | 全部 try/except 包裹，失败仅 debug 日志 |
| 子智能体委派场景混淆 | 子智能体走 `execute_as_subagent`（不调用 `process_message`），不受影响 |
| 老 channel session 接入前产生，无 trace | 符合预期，从接入时间点开始可见 |
