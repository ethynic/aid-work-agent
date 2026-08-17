# 工具结果截断开发计划（Phase 2）

> 状态：📋 计划定稿待开发（2026-08-17）
> 背景：qwen3.7-flash 上下文缓存优化 Phase 1（显式末尾标记）已落地，但单次 agent 运行 244K prompt 中 239K 是历史 + 工具结果。缓存命中时历史按 10% 计费，但缓存未命中（首次调用 / 跨用户轮次超 5 分钟 TTL）时按 100% 计费，工具结果限长可直接降低输入基数。
> 关联方案：[qwen3.7-flash 上下文缓存优化方案](./plan-qwen3-7-flash-context-cache-optimization.md)（本计划为其 Phase 2）
> 计费审计：本计划不新增/修改 LLM/Embedding/ASR/视频调用点与计费函数，仅降低喂给 LLM 的输入量，按 [billing_audit.md](../../.claude/rules/billing_audit.md) §3 核对无计费影响。

## 一、问题

agent 主循环每次迭代把完整工具结果写入 messages（`src/core/agent.py:3491`），多轮后 prompt 膨胀到 244K，其中 239K 是历史 + 工具结果（billing record 5551）。Phase 1 显式末尾标记落地后，缓存命中时历史按 10% 计费，但：

- 缓存未命中场景（首次调用、跨用户轮次间隔 >5 分钟 TTL）按 100% 计费，239K 输入成本高
- 超大工具结果（read 整本书、http_api 大 JSON、web_search 大量条目）中大量内容 LLM 只用到概要
- 大 prompt 增加 qwen 推理延迟

## 二、方案（用户 2026-08-17 确认）

**通用截断**：所有工具结果超阈值即截断，保留头部 + 尾部 + 省略标记。主智能体与子智能体两个接入点都截。

> **CodeReview 补充（2026-08-17）：use_skill 豁免截断**。use_skill 返回的 content 是技能操作指南（SKILL.md 正文 + guidance_suffix），多个技能指南超 12K 阈值（guizang-ppt 37K / skill-creator 33K / competitor-research 30K / travel-quote 18K / whatsapp-messaging 16K）。若截断：
> 1. `_get_last_use_skill_version`（agent.py）对 use_skill 结果做 `json.loads` 解析 skill_version，截断成非法 JSON 会解析失败 → `_check_skill_version_consistency` 返回 version_block → skill_execute 被拦截要求重新 use_skill → 重新加载又被截断 → **版本校验拦截死循环，大技能无法执行**；
> 2. LLM 拿到的指南残缺，按缺步骤执行质量下降。
> 因此 **use_skill 工具结果豁免截断**（主智能体 `_no_truncate` 标记 + 子智能体 `tool_name == "use_skill"` 判断），仍统一 dumps 成 str 保证消息/内存/持久化一致。截断仍覆盖 read/http_api/web_search/skill_execute 等执行类工具的大输出。

### 2.1 截断函数

```python
def _truncate_tool_content(
    content: str,
    max_chars: int = 8000,
    head_chars: int = 6000,
    threshold: int = 12000,
) -> str:
    """超长工具结果截断：>threshold 字符时保留 head_chars + (max_chars-head_chars) 尾部 + 省略标记。

    保头保尾策略：头部常含 summary/status，尾部常含关键数据（如 JSON 末尾、文件结尾），
    中间省略并明确标注，LLM 能感知结果被截断并主动缩小检索范围或告知用户。
    """
    if len(content) <= threshold:
        return content
    tail_chars = max_chars - head_chars
    return (
        content[:head_chars]
        + f"\n...[已截断：共 {len(content)} 字符，省略 {len(content) - head_chars - tail_chars} 字符]...\n"
        + content[-tail_chars:]
    )
```

阈值参数：`threshold=12000`（超 12K 才截），截断后 `max_chars=8000`（保头 6000 + 保尾 2000）。

### 2.2 接入点（2 处）

| 接入点 | 文件:行 | 改动 |
|--------|--------|------|
| 主智能体工具结果接入 | `src/core/agent.py:3491` | `"content": tool_result["content"]` → 先转字符串（dict→json.dumps / 其他→str）再 `_truncate_tool_content` |
| 子智能体工具结果接入 | `src/core/agent.py:4166` | `"content": json.dumps(tool_result) if isinstance(tool_result, dict) else str(tool_result)` → 外包 `_truncate_tool_content` |

### 2.3 一致性保证（关键）

截断只需发生在**写入 messages 的 tool 消息**这一处，全链路自动一致：

- `memory.add_message`（3493）保存的是同一个已截断的 tool_message → 内存 deque 一致
- `tool_messages_for_persist`（3516-3535）从 `messages[initial_len:]` 收集 → 持久化到 DB 的是截断后内容
- 上下文重建（web 读 `chat_messages`、渠道读 `channel_messages`）读回截断后内容 → 跨会话一致
- 图像提取（3498-3502）读 `tool_results`（原始），不受影响
- SSE `tool_result` 事件（3383）推送原始 result，前端展示不受影响
- 计划管理器（mark_task_completed）用原始 result，不受影响

### 2.4 设计决策

1. **通用截断**：全部工具超阈值即截（read / http_api / web_search 等），省略标记让 LLM 明确感知截断。
2. **保头保尾**：头部（summary/status）+ 尾部（关键数据）都保留，中间省略。比简单限长信息量更高。
3. **截断后是文本而非合法 JSON**：工具结果作为 content 字符串喂给 LLM，LLM 自行理解，不强制 JSON 解析；下游无 JSON.parse 依赖（图像/计划/事件都用原始 result）。
4. **子智能体同样截断**：其工具结果同样进入 LLM 调用且随多轮累积；父智能体看到的是子智能体 final_result/summary 而非原始工具结果，不影响父级上下文。

## 三、改动点

| 文件 | 改动 |
|------|------|
| `src/core/agent.py` | 模块级新增 `_truncate_tool_content`；3491 主智能体工具结果接入截断；4166 子智能体工具结果接入截断 |
| 单测 | 新增 `tests/unit/test_tool_result_truncation.py`（或并入现有 agent 相关测试）：函数边界（≤阈值不截 / >阈值截 / 保头保尾 / 省略标记内容正确 / 短尾边界）+ 主/子智能体接入点 mock 验证 |

**无需改动**：`base.py`（`_format_messages` 已对 str/dict content 兼容）、`billing.py`/`session_record.py`（计费口径不变，仅输入 tokens 数值变小）。

## 四、验证方式

1. **单测**：截断函数边界 + 两个接入点验证，回归既有 agent 测试。
2. **真实对话实测**（部署切换后）：触发长 agent 运行（多轮工具调用 + 超大工具结果），核对 `chat_records.usage_breakdown`：`prompt_tokens` 应显著下降（对比记录 5551 的 244K）。
3. **能力回归抽查**：多轮推理 + 依赖工具结果细节的任务（如读取文件内容后总结），确认截断未导致 LLM 丢失关键结论。

## 五、风险与回退

- **LLM 能力影响**：超长工具结果被截断后，LLM 看不到中间部分。缓解：保头保尾保留两端关键信息 + 省略标记显式提示 + 12K 阈值较高（绝大多数工具结果不受影响）。
- **跨会话一致性**：截断发生在写入 messages 处，DB 持久化与上下文重建均为截断后内容，无"当前会话截断、下次会话完整"的不一致。
- **回退**：单文件改动，`git revert` 即回到无截断基线。

## 六、关联文档

- [qwen3.7-flash 上下文缓存优化方案](./plan-qwen3-7-flash-context-cache-optimization.md)（Phase 1 显式末尾标记 + 本计划 Phase 2）
- [deepseek-v4-flash 平替模型调研](../research/deepseek-v4-flash-replacement-research.md)
