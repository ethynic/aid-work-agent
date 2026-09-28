# 会话任务自然语言建任务（NL 创建向导）+ 任务列表页修复

> 关联条目：`docs/ideas.md` 20260912-2313 端侧会话任务执行器（P5 后续）
> 关联设计：`docs/design/desktop-automation/edge-session-task-design.md`

## 背景与问题

1. **建任务表单太复杂**：用户反馈微信自动聊天任务 UI「不会用、有点复杂」。现状新建草稿需手填
   对象绑定、任务目标、完成方式（三选一 + 各自参数）、回复风格、事实/禁止承诺、四项硬上限、
   截止时间、工作时段（UTC）等十余项，专业术语多（轮数/决策数/积分/水位）。
2. **任务列表页打开即报错**：agent2 实测根因——租户 19 条任务中 12 条（09-15～09-17 创建）的
   spec 受控文本用旧主密钥加密，`list_tasks` 对每条任务逐个调 `get_task` 解密投影，第一条坏数据
   即让整个列表接口 503（`受控文本解密失败`），前端 `Promise.all` 整页报错。

## 目标

- 用户用自然语言描述任务（目标用户、要实现的目标、轮次等），系统自动解析成结构化草稿并预填表单。
- 描述中缺失必要要素时逐项提醒；所有必要要素齐备前「保存草稿」禁用。
- 任务列表接口对单条坏数据降级容错：列表永远能打开，坏行标记 degraded 展示基础字段。

## 必要要素定义（缺一不可保存）

| 要素 | 来源 | 缺失时提醒 |
|------|------|-----------|
| 目标用户（会话绑定） | NL 名称匹配已有绑定 / 下拉手选 | 请选择要发消息的对象 |
| 任务目标 | NL 解析 / 表单填写 | 请说明这个任务要实现什么 |
| 完成方式及参数 | NL 解析（轮数/标准/确认字段）/ 表单填写 | 请选择聊到什么程度算完成 |
| 截止时间 | NL 解析 / 表单填写 | 请选择任务截止时间 |

其余字段（回复风格、事实/禁止承诺、硬上限、工作时段）保留默认值或空，可在「高级设置」表单调整，不阻塞保存。

## 方案

### 后端

1. **新增 `src/session_tasks/nl_parse.py`**：
   - `async parse_natural_language(tenant_id, user_id, text, scenario_key)`：
     - 经场景描述器 `binding_resolver.list_bindings` 取该用户绑定（id/名称/类型）；
     - `llm_gateway.chat_no_thinking` 单次调用，严格 JSON 契约输出（goal/opening/completion/
       style/facts/forbidden/limits/expires_at/binding_ids）；
     - 后端组装部分 spec：必要要素缺则置空（不静默补默认），非必要给默认值；
     - `missing` 由后端按要素权威计算；binding_ids 过滤为真实属主绑定，按 LLM 相关性排序；
     - LLM 输出非法（JSON 解析失败/字段超界）→ 降级为全缺失 + `parse_error` 提示，不抛 500；
       LLM 网关异常 → 503 `PARSE_UNAVAILABLE`（前端回退手动填表）。
2. **`api.py` 新增 `POST /api/session-tasks/parse-spec`**：认证 + 租户中间件，纯读（LLM + 绑定查询，
   不写库不建幂等键）；声明在 `/{task_id}` 动态路由之前。
3. **`service.list_tasks` 降级容错**：单条 `get_task` 抛错时保留基础行并标记
   `projection_degraded: true`（目标摘要显示「历史数据不可读」），列表与统计不再整页失败。

### 前端

1. **`api/sessionTasks.ts`**：新增 `parseSpec(text, signal)` 与返回类型。
2. **`TaskList.vue` 新建弹窗重构**（NL 优先，表单兜底）：
   - 弹窗顶部为自然语言 textarea + 「智能解析」按钮 + 示例占位文案；
   - 解析成功：绑定下拉自动预选首个候选；TaskSpecForm 预填解析结果；展示必要要素清单
     （已解析 ✓ / 待补充，含「描述中未提到：截止时间」提示）；全部就绪前保存禁用；
   - 解析失败/服务不可用：展示原因，现有表单流程完整可用；
   - 保存条件：绑定已选 + `getSpec()` 校验通过 + 必要要素清单为空。
3. **`TaskSpecForm.vue`**：完成方式增加「请选择」空态（`mode=''`），`load()` 容忍
   completion_rule/expires_at 为空的解析结果；`getSpec()` 对空 mode 抛出可读错误。

### 数据与安全

- 解析端点不落库、不写幂等键；绑定列表只送 id/名称/类型给 LLM，不含验证证据等敏感字段；
  LLM 返回的 binding_id 一律以数据库属主过滤为准（防幻觉 id）。
- 目标用户匹配失败不猜测定 向，宁可列入 missing 由用户确认。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 列表页修复：list_tasks 单条降级 + 后端测试 | ✅ 完成（2026-09-28） | 根因=旧主密钥密文（agent2 该租户 19 条中 12 条坏）；单条 SessionTaskError 降级 projection_degraded，测试 test_nl_parse.py::TestListTasksResilience |
| Phase 2 | 后端 parse-spec 端点 + nl_parse 模块 + 单测 | ✅ 完成（2026-09-28） | nl_parse.py（LLM 抽取+后端权威组装/missing/绑定过滤）+ POST /api/session-tasks/parse-spec；16 个新用例全过 |
| Phase 3 | 前端 NL 向导（TaskList 弹窗 + TaskSpecForm 空态） | ✅ 完成（2026-09-28） | 两步式弹窗（NL 描述+必要要素清单，齐备才可存）；npm run build 通过，11 个组件测试通过 |
| Phase 4 | 验证：后端定向测试 + 前端 build + 既有回归 + 独立验证 | ✅ 完成（2026-09-28） | 模块回归 402 passed；与干净 HEAD 基线对照（10 failed），失败集为既有 flaky（同代码三次跑出三种组合），非本次引入；独立验证智能体复查通过（无 P0/P1），其提出的 P2/nit 已修：parse-spec 异常语义（仅 ValueError→400）、accepted 无交集整字段丢弃、注释纠正、DEFAULT_SCENARIO_KEY 复用、解析无候选不清空手选绑定，并补 3 个缺口测试（超长文本/未知场景 fail-closed/无交集丢弃）；25 定向用例 + build 复验全绿。前端 routes 快照 1 失败为既有 wechat-mp 路由未更新快照，与本任务无关 |

## 遗留与说明

- agent2 上 12 条旧密钥任务在列表页将以「历史数据不可读」降级展示；其详情页仍会诚实报 503
  （解密失败），是否清理旧数据由用户决定。
- LLM 解析失败（网关不可用/输出非 JSON）不阻断建任务：前端提示后回退手动填表。
- 每任务单目标对象（沿用现有数据模型）；NL 提到多个对象时取首个候选，可在下拉切换。
