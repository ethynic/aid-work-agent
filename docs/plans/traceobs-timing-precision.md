# trace 观测 span 计时精确化修复

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | engine 观测字段（A1 llm_call 墙钟起止 / A2 失败事实挂载 / A3 工具计时） | ✅ 完成（2026-10-12） | engine.py 最小增量，控制流/checkpoint 结构/异常类型未变 |
| Phase 2 | trace_collector 计时与失败修复（B1–B6） | ✅ 完成（2026-10-12） | B4 的 `tags 追加 llm_error` 落在 trace 级 tags（SpanRecord/obs_spans 无 tags 列） |
| Phase 3 | 失败路径接入（C agent.py / D worker.py 调 on_llm_failure） | ✅ 完成（2026-10-12） | 均包 try/except，best-effort 不影响业务 |
| Phase 4 | 单测更新/新增与回归验证 | ✅ 完成（2026-10-12） | 新增 tests/unit/test_engine_observation_timing.py、扩展 tests/unit/test_trace_llm_spans.py；回归门禁（脚本统一执行）：bash ./scripts/dev_test.sh 11 个相关测试文件（test_trace_llm_spans / test_engine_observation_timing / test_agent_engine_acceptance / test_agent_engine_resume_input / test_trace_cancel_status / test_trace_merge_semantics / test_trace_total_cost / test_mid_term/test_context_compressed_event / services/agent_runner/test_worker_execution / test_agent_record_isolation / test_verbose_feedback）-p no:cacheprovider -q → 158 passed；评审：git diff 全部修改文件对照本计划逐项核对通过 |

## 背景与根因

traceobs（`obs_traces`/`obs_spans`，写入链路 `src/core/trace_collector.py` -> `src/core/trace_persist.py`）的 LLM span 计时不准确，**生产库已验证**：6201 个 generation span 中 396 个窗口与后续 span 重叠，全部是多轮 LLM 的 trace；多轮 trace 中出现 `llm_call dur=29170ms` 且窗口完全盖住后续 3 次 LLM 与全部工具的情况。

根因（按代码位置）：

1. **span 起点错位**：`_handle_llm_call` 用事件到达时刻 `time.time()` 当 `span.start_time`（`src/core/trace_collector.py:259`）——此刻 LLM 调用已结束，真实起点是"到达时刻 - duration_ms"。
2. **兜底循环覆盖 duration**：`on_complete` 的兜底循环（`src/core/trace_collector.py:180-183`）对一切 `end_time is None` 的 span 执行 `end_time=now; duration_ms=now-start_time`。generation span 的 `end_time` 从未设置（`src/core/trace_collector.py:256-269` 创建时无 end_time），因此非最后一次 LLM span 的 duration 被覆盖为"该次调用结束 -> 整个 trace 结束"（吞掉后续所有工具与 LLM 时间）；最后一次 LLM span 的真实耗时也被覆盖为收尾尾巴。
3. **失效的补 end 特判**：`on_complete` 末尾 `_last_llm_span`"补 end"的特判（`src/core/trace_collector.py:185-187`）因上述循环先覆盖 `duration_ms` 而失效，注释意图与实际行为不符。
4. **context_compressed 不闭合**：`_handle_context_compressed` 只反推 start 不设 `end_time`（`src/core/trace_collector.py:304-307`，span 创建于 :327-343），`end_time` 同样被推到 trace 结尾。
5. **事件缺墙钟、工具 span 依赖消费端时钟**：引擎侧 `_model()` 已用 `time.monotonic()` 精确测量 `model.complete` 耗时（`src/core/agent_engine/engine.py:127`、`:142`，不含工具），但事件里只有 `duration_ms` 没有墙钟起止；工具 span 依赖消费端时钟（`_handle_tool_start` 的 `start_time=time.time()`，`src/core/trace_collector.py:211`），durable runner 下 checkpoint 落库时间被计入 span。
6. **失败与恢复场景丢 span**：失败的 LLM 调用不产生任何 span（`_model()` except 分支 `src/core/agent_engine/engine.py:130-138` 不发事件、collector 无感知）；断点恢复后 `tool_result` 没有配对的 `tool_start`（恢复路径直接从 fact 构造结果，`src/core/agent_engine/engine.py:269-271`），`_handle_tool_result` 找不到配对即静默跳过（`src/core/trace_collector.py:224-233`），span 直接丢失。

**历史数据不可回填**：真实耗时未存于任何列（存量列中只有被上述缺陷污染的 start_time/end_time/duration_ms），只保证新数据正确。

## 目标

- generation span 精确表达单次模型调用（含 failover 重试）的真实墙钟窗口与耗时；
- 工具 span 精确表达工具执行本身；
- 失败的 LLM 调用可见；
- 恢复执行的孤立 tool_result 不丢 span。

## 改动清单

### A. `src/core/agent_engine/engine.py`（执行内核，只加观测字段，不改控制流/checkpoint 结构/异常类型）

- **A1** `_model()`：在 `started = time.monotonic()`（`engine.py:127`）旁记录 `started_wall = time.time()`；成功路径 `fact.update` 时加 `ended_wall = time.time()`，并把 `start_ts=started_wall`、`end_ts=ended_wall`（epoch 秒 float，与 `SpanRecord.start_time/end_time` 同单位）放入 fact；`duration_ms` 保持 monotonic 计量不变。`llm_call` 事件经 `make_event("llm_call", **fact...)`（`engine.py:152`）自动携带。
- **A2** `_model()` except 分支（`engine.py:130-138`）：`raise` 前把失败事实挂到异常对象上（不改变异常类型与控制流，`authoritative_storage_failure` 分支同样先挂再 raise）：

  ```python
  error.model_call_fact = {"call_id":..., "execution_id":..., "iteration":...,
                           "error_code":type(error).__name__,
                           "start_ts":started_wall, "end_ts":time.time(),
                           "duration_ms":int((time.monotonic()-started)*1000)}
  ```

- **A3** 工具循环（`engine.py:258-322`）：`tool_start` 事件增加 `started_ts`（在 `before_tool` checkpoint 落库（`engine.py:277`）之后、`make_event` 之前捕获 `time.time()`）；dispatch 循环拿到 `DispatchResult` 并校验后（`engine.py:290-291`）、`control.save("tool_completed")`（`engine.py:319`）之前捕获 `ended_ts = time.time()`，`tool_result` 事件携带 `ended_ts` 与 `duration_ms=int((ended_ts-started_ts)*1000)`。恢复路径（`fact.phase` 为 completed/waiting，未经本次 dispatch，`engine.py:269-273`）不带计时字段，走缺省。

### B. `src/core/trace_collector.py`

- **B1** `_handle_llm_call`：span 创建时立即落完整窗口——优先用事件的 `start_ts`/`end_ts`；缺省时 `now=time.time()`，`start=now-duration_ms/1000`，`end=now`（`duration_ms` 为 0 时退化为零宽）。`duration_ms` 恒取事件值。`_last_llm_span` 仅保留"新事件到达时对上一个 generation span 做 `_lean_llm_span` 精简"的用途。
- **B2** `on_complete`：删除 `_last_llm_span` 补 end 的特判（`trace_collector.py:185-187`）；兜底关 span 循环保留（修复后 generation/context_compressed 已闭合，循环只会关闭异常中断遗留的工具 span，语义正确），注释同步更新以匹配实际行为。
- **B3** `_handle_context_compressed`：补 `end_time = now`（start 反推逻辑已存在，`trace_collector.py:304-307`）。
- **B4** 新增 `on_llm_failure(fact: dict)`：用失败事实构造失败 generation span（`name=llm_call`、`span_type=generation`、`success=False`、窗口用 `start_ts`/`end_ts` 缺省时 `now-duration` 兜底），`result` 存 `error_code` 等摘要 JSON，`tags` 追加 `"llm_error"`；整体 try/except 吞异常（collector 一切操作 best-effort）。
- **B5** 工具 span 优先用事件时钟：`tool_start` 的 `started_ts` 作为 `span.start_time`；`tool_result` 的 `ended_ts` 作为 `span.end_time`、`duration_ms` 优先取事件值；字段缺省回退现状（消费端 `time.time()`）。
- **B6** `_handle_tool_result` 找不到配对 active span 时（断点恢复场景）：用事件数据直接补一个已完成 span（`end=ended_ts` 或 now；`duration=事件 duration_ms` 或 0；`success` 取事件），避免恢复执行丢 span。

### C. `src/core/agent.py`（process_message 包装层）

`except Exception as e` 分支（`agent.py:255-261`）：`on_error` 之前提取 `fact = getattr(e, "model_call_fact", None)`，非空且 trace_collector 存在时调用 `trace_collector.on_llm_failure(fact)`，同样 try/except 吞异常。`asyncio.CancelledError` 分支（`agent.py:245-254`）不动。

### D. `src/services/agent_runner/worker.py`（runner 失败路径）

worker 的 `except error` 块（`worker.py:535-557`，collector 在作用域内、`finally` 的 `_persist_trace` 之前）：同样提取 `error.model_call_fact` 调 `collector.on_llm_failure`，包 try/except。该路径不调用 `on_error`，span 落库靠 `_persist_trace` -> `on_complete`（`worker.py:558-564`、`:627`、`:653`）。

## 事件契约增量

> 本节供后续维护者对照；全部为**新增可选字段**，旧事件缺省走回退，不影响其他消费者。异常属性 `model_call_fact` 仅进程内传递，**不入 checkpoint/事件流**。

| 事件 | 新增字段 | 含义 |
|------|---------|------|
| `llm_call` | `start_ts`、`end_ts` | 单次模型调用的墙钟起止（epoch 秒 float，与 `SpanRecord.start_time/end_time` 同单位） |
| `tool_start` | `started_ts` | 工具开始执行的墙钟时刻 |
| `tool_result` | `ended_ts`、`duration_ms` | 工具结束墙钟时刻与事件侧计时耗时（ms） |

## 兼容回退

- `llm_call` 事件缺 `start_ts`/`end_ts`（旧事件、其他生产者）：`now=time.time()`，`start=now-duration_ms/1000`，`end=now`；`duration_ms` 为 0 时退化零宽。`duration_ms` 恒取事件值。
- 工具 span：`started_ts`/`ended_ts`/`duration_ms` 缺省回退现状（消费端 `time.time()` 计时）。
- `tool_result` 无配对 `tool_start`：补 span 时 `ended_ts` 缺省用 now、`duration_ms` 缺省 0、`success` 取事件值。
- `on_llm_failure`：`start_ts`/`end_ts` 缺省时 `now-duration` 兜底。
- 恢复路径（fact.phase 为 completed/waiting）的 `tool_result` 不带计时字段，走缺省。

## 历史数据不可回填

真实耗时未存于任何列，存量 `obs_spans` 无法回填真实窗口；本修复只保证新数据正确，已有数据不迁移、不修正，排查历史 trace 时需考虑上述计时失真。

## 已知局限

- **echo 兼容重试的失败不可见**：`engine.run()` 中 is_tool_result_echo 触发的重试若因 `model_io_failure` 抛错会被吞掉（保持原 echo 响应继续），该次失败的 `model_call_fact` 已挂上异常但无消费方，不产生失败 span。此路径属协议兼容兜底（正常响应被存储回声覆盖时才触发），如需闭环需在引擎吞异常前发观测事件，涉及内核 yield 语义，未纳入本次。
- `duration_ms` 语义为"含 failover 重试的单次模型调用总耗时"（`_model` 包住整个 `ModelAdapter.complete`）；failover 各 provider 槽位的分段耗时只在 failover 日志，不进 span。

## 硬性约束

- 禁止 git commit/push；禁止任何部署动作（`*_update.sh`、容器重建/重启等）；未获授权。
- trace_collector 的所有操作保持 best-effort：任何异常不得影响业务流。
- engine 是执行内核：不改状态机控制流、不给 state/ToolFact 加字段（不改 checkpoint 结构）、不改异常类型；A2 只挂属性。
- 不重构无关代码；注释用中文、只写代码本身看不出的约束；遵循各文件既有风格。
- 行为变更必须有能验证正确行为的测试，重点覆盖回归、边界与异常路径。

## 测试要求

更新/新增单测放 `tests/unit/`，风格对齐 `tests/unit/test_trace_llm_spans.py`：

1. 单次与多次 LLM 调用：span 窗口取事件起止，`duration_ms` 恒等于事件值，`on_complete` 后不被覆盖；多轮窗口互不重叠且时间有序。
2. 事件无 `start_ts`/`end_ts` 时的兼容回退路径（`now-duration`/`now`）。
3. `context_compressed` 的 `end_time` 在创建时即闭合，`on_complete` 后不变。
4. `on_llm_failure` 构造失败 span（含 tags、窗口兜底）。
5. 工具 span 采用事件时钟；`tool_result` 无配对 `tool_start` 时补 span。
6. engine：`_model` 成功事件含 `start_ts`/`end_ts` 且 `end-start` 与 `duration_ms` 一致（fake model sleep 或容忍粗粒度断言）；模型抛错时异常携带 `model_call_fact` 且字段齐全。

运行方式：

```bash
bash ./scripts/dev_test.sh <相关测试文件...> -p no:cacheprovider -q
```
