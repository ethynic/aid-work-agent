# 普通工具特殊分支删除与统一执行链优化计划

> 状态：✅ 已完成开发
> 日期：2026-08-20
> 关联：`docs/ideas.md` #66、工具注册与 Agent 解耦后续清理

## 一、重新评估结论

上一版计划引入 `ToolExecutionOutcome`、`ToolResultPresenter` 和
`ToolRuntimePolicy`，抽象层次过多，暂不采用。

当前工具调用链已经天然知道：

- 调用了哪个工具：`tool_name` / `tool_call_id`；
- 工具是否成功：结果中的 `success`，异常由 `ToolExecutor` 统一转成失败结果；
- 工具返回了什么：原始 `result` 会进入 `tool_result` 事件和 LLM 的
  `role=tool` 消息；
- 工具如何描述结果：多数工具已经返回 `message`、`error` 及领域字段。

因此，Agent 没有必要根据工具名称重新解释结果，更不应该把工具内容再次拼成
进度文案。工具结果的主要消费者是下一轮 LLM；前端执行详情只需要展示“开始、
成功或失败”，无需预览搜索结果、文件正文或浏览器内容。

本计划改为：**能删除的特殊分支直接删除；只有存在真实结构化消费者时，才依据
结果字段处理，不依据工具名称处理。**

## 二、现状审计

### 2.1 可以直接删除的结果预览

`agent.py` 对以下工具拼接了特殊进度文案：

- `content_generate`：截取 `content`；
- `web_search`：计算 `results` 数量；
- `email_process`：输出“成功”；
- `read`：截取文件正文；
- `browser_automation`：截取 `result` 或 `message`。

这些代码没有参与工具执行，也没有影响写入 LLM 的工具结果。它们只是额外发送
`progress` 事件。

Web 前端在收到 `tool_result` 后已经自行增加一条完成记录；随后
`MessageItem.vue` 对 `tool_result` 又统一渲染成“工具执行完成”，不会使用上述预览
正文。因此后端特殊预览存在重复事件，部分内容实际上不可见。

处理方式：全部删除，只保留一次标准 `tool_result` 事件。失败时也由
`tool_result.success` 和结果中的 `error` 表达，不再额外发送工具专属进度文案。

### 2.2 `content_generate` 的即时 `response`

主 Agent 会把 `content_generate.content` 包装在 `<!--process-->` 中再次发送为
`response`。Web 展示层会主动删除该区块；渠道侧则可能把它与最终 LLM 回复一起
发送，产生重复内容。

工具结果本身已经作为 `role=tool` 消息交给下一轮 LLM，因此应删除该即时输出，
以最终 LLM 回复作为唯一用户可见答案。

子智能体中还存在 `generated_content_list` 收集逻辑。它不是执行所必需，并且主
Agent 读取委派结果顶层 `generated_contents` 的路径与当前 Delegate 返回结构不一致，
该即时展示分支实际上不可达。开发时先用行为测试确认子智能体最终结果已包含所需
内容，再删除这套按 `content_generate` 名称收集和透传的旁路。

如果测试证明某个真实场景必须原样返回工具产物，应先修正该工具或子智能体的最终
结果契约；不为此建立通用 Presenter 框架。

### 2.3 定时任务的独立执行分支

`create_scheduled_task` 和 `manage_scheduled_task` 已经是注册到 Registry 的普通工具，
但 Agent 在通用执行链之前再次按名称拦截：

- 手工调用同一个 `ToolExecutor.execute()`；
- 手工发送同样的 `tool_result`；
- 手工加入 `tool_results`；
- `continue` 跳过通用计划状态跟踪。

两类工具自身已经返回完整结果，其中创建工具包含 `name`、
`schedule_description` 和 `message`，管理工具也包含 `message` 或 `error`。这两个
分支没有必要存在，应删除并统一进入普通工具执行链。

需要增加回归测试，确认：

- 每次工具调用只执行一次；
- 创建、管理、失败结果都正常进入 LLM；
- 如果当前存在执行计划，对应计划任务正常完成或失败；
- 工具的用户归属与执行上下文校验不受影响。

不增加 `SKIP_PLAN_TRACKING` 之类的新策略。没有证据表明定时工具应当跳过计划状态；
旧旁路更像历史遗留，而不是领域契约。

### 2.4 浏览器参数日志

浏览器工具名称判断不是结果展示，而是参数脱敏。安全要求必须保留，但没有必要靠
工具名实现。

轻量处理方式：

- 删除 Agent 执行前重复打印工具参数的日志；
- `ToolExecutor` 默认只记录工具名称，不记录完整参数；
- Remote Gateway 现有的异常脱敏边界继续保留；
- 本期不新增 per-tool 日志策略类。

默认不记录工具参数比为单个工具维护名称白名单更简单，也能避免新工具携带敏感
参数时漏配。

## 三、前端与其他真实消费者

### 3.1 执行详情不展示工具结果预览

前端 `useAgent.ts` 目前也按 `web_search`、`content_generate`、`read` 等名称拼接
预览。统一改为：

- `tool_start`：显示工具友好名称；
- `tool_result(success=true)`：显示“执行完成”；
- `tool_result(success=false)`：显示结果中的 `error`；
- 原始 `result` 仍保存在事件中，供调试和持久化，不直接打印给用户。

工具友好名称已由后端 `BaseTool.get_display_name()` 提供。可在 `tool_start` 和
`tool_result` 事件中增加可选 `displayName`，前端优先使用该字段并保留 `toolName`
回退。这样前端也不再维护工具名称 switch。

建议同时增加 `toolCallId`，避免同一轮并行调用两个同名工具时，前端用
`toolName` 缓存参数发生覆盖。它是现有调用标识的透传，不是新的结果抽象。

### 3.2 下载文件按结果形状识别

`write`、`cp` 在 Web、API 持久化和渠道侧有真实消费者：生成下载卡片。这里确实
不能直接删除行为，但也无需判断工具名称。

统一依据结果字段识别：

```text
success == true
and result.file_id 非空
and result.visible != false
```

文件名、大小、URL 和 MIME 类型继续从结果字段读取。任何未来工具只要返回同一文件
结果结构，就能自动展示下载卡片，无需修改消费者。

### 3.3 快捷选项按结果形状识别

前端已有 `extractQuickOptions(result)`，却仍用 `boss_jobs_list` 名称限制调用。删除
名称判断，对所有成功工具结果调用提取函数；没有 `data.options` 时自然返回空列表。

## 四、目标执行流程

普通工具只保留一条路径：

```text
LLM tool call
  → Registry 根据 tool_name 取得工具
  → ToolExecutor 校验并执行
  → Agent 发送标准 tool_result(toolName, toolCallId, displayName, result, success)
  → result 按既有序列化与截断规则写入 role=tool 消息
  → 下一轮 LLM 基于结果生成最终用户回复
```

其中：

- Executor 不推断搜索、邮件、文件、浏览器等领域语义；
- Agent 不预览、改写或复制普通工具结果；
- 前端不按普通工具名称解释结果；
- 结构化 UI 功能只依据结果字段识别；
- 工具返回结构仍保持兼容，不引入新的 outcome 包装层。

## 五、范围边界

### 本期处理

- 删除五个普通工具的后端结果预览分支；
- 删除 `content_generate` 即时 response 和无效透传旁路；
- 删除两个 scheduled 工具的独立执行旁路；
- 普通工具统一发送标准 `tool_result`；
- 删除前端普通工具结果预览 switch；
- 后端提供工具友好名称，前端通用展示；
- 下载文件和快捷选项改为按结果字段识别；
- 删除按 `browser_automation` 名称进行日志脱敏的判断，默认不记录参数。

### 本期不处理

以下属于 Agent 控制流，不是普通工具结果展示，不能直接删除：

- `create_plan`；
- `clarify`；
- `use_skill`；
- `skill_execute`；
- `delegate_to_subagent`；
- 浏览器 `ToolSuspension`；
- `ExecutionTarget.LOCAL_REQUIRED` 本机执行路由。

这些分支是否继续下沉，应另行设计控制工具调度器，不能和本次删除型优化混在一起。

## 六、实施步骤

### Phase 0：锁定当前必要契约

先补最小行为测试，不快照冗余文案：

- 普通工具执行一次并产生一个 `tool_result`；
- 原始结果完整进入下一轮 LLM tool message；
- 成功、失败和非 dict 返回值均可处理；
- scheduled 工具进入通用路径后计划状态正确；
- 子智能体调用 `content_generate` 后最终结果仍包含有效内容；
- Web 和渠道下载文件仍可识别。

### Phase 1：删除 Agent 普通工具特殊代码

- 删除五个工具的结果预览 `if/elif`；
- 删除主 Agent 的 `content_generate` 即时 response；
- 删除子智能体的 `content_generate` 名称判断和不可达展示链；
- 删除 scheduled 两个提前执行分支；
- 合并普通工具成功、失败事件生成代码；
- 删除 Agent 工具参数日志。

本阶段不新增业务抽象类。

### Phase 2：简化 Executor 日志

- Executor 默认只记录 `tool_name`；
- 不记录普通工具参数；
- 保留受信边界的异常脱敏行为；
- 删除 `browser_automation` 名称判断。

### Phase 3：删除前端名称判断

- 事件增加可选 `displayName` 和 `toolCallId`；
- 前端统一显示开始、完成、失败；
- 删除普通工具预览和显示名 switch；
- 下载文件、快捷选项按结果字段处理。

### Phase 4：静态防回归与全量测试

- AST 测试禁止 `agent.py`、`executor.py`、`useAgent.ts` 对本期普通工具名称直接比较；
- 允许工具类自身声明 `name`，允许说明文档与测试 fixture 使用名称；
- 跑相关 Agent loop、scheduled、delegation、Web 单测；
- 跑全量 unit，并与未修改 master 的失败基线比较。

## 七、验收标准

- `agent.py` 不再比较：
  `create_scheduled_task`、`manage_scheduled_task`、`content_generate`、
  `web_search`、`email_process`、`read`、`browser_automation`；
- `executor.py` 不按任何普通工具名称决定日志行为；
- Web 执行详情不按普通工具名称解释结果；
- 普通工具仍只执行一次，结果按既有序列化与截断规则交给 LLM；
- 用户最终回答由 LLM 统一生成，不重复插入工具正文；
- scheduled 工具执行、权限和计划状态回归通过；
- 下载卡片和快捷选项只依赖自描述结果字段；
- 不新增 outcome、presenter、policy 等框架；
- 全量 unit 相对 master 零新增失败。

## 八、预期改动文件

```text
src/core/agent.py
src/tools/executor.py
src/core/agent_events.py（若需要补充事件字段说明）
src/main.py
src/channels/session.py
frontend/web/composables/useAgent.ts
frontend/web/types/index.ts
相关 tests/unit/
docs/ideas.md
docs/ideas_finished.md（开发完成后迁移状态）
```

## 九、设计决策摘要

1. 不建立新的工具结果展示框架。
2. Executor 负责执行，不负责解释领域结果。
3. Agent 只传递工具名、调用 ID、成功状态和原始结果。
4. 工具结果交给下一轮 LLM 生成最终回答，不在执行进度中重复展示。
5. 无真实消费者的特殊代码直接删除。
6. 有真实消费者的下载文件、快捷选项按结果字段识别。
7. 控制工具分支不属于本期，避免误删状态机。
