# qwen3.7-flash 上下文缓存优化方案（显式末尾标记）

> 状态：✅ Phase 1 已完成开发（2026-08-17，提交 e48d744），Phase 2 计划定稿
> 背景：qwen3.7-flash 实测对话计费记录显示缓存命中极低（`cache_creation_input_tokens=5103` vs `non_cached_input_tokens=223931`，244K prompt 仅缓存 system 部分），成本明显高于 deepseek 对比记录。
> 关联调研：[deepseek-v4-flash 平替模型调研](../research/deepseek-v4-flash-replacement-research.md)
> 关联计划：[qwen3.7-flash 平替实施计划](./plan-qwen3-7-flash-replacement.md)（当前显式缓存标记放在首条 system）
> 计费审计：本方案改动 LLM 调用参数（缓存标记位置）与缓存计费口径，按 [billing_audit.md](../../.claude/rules/billing_audit.md) §3 核对。

## 一、问题

生产实测（billing record 5551，qwen3.7-flash，单次 Agent 运行累计）：

```
prompt_tokens=244343
cache_creation_input_tokens=5103      # 仅首条 system（~5103 token）创建显式缓存
non_cached_input_tokens=223931        # 历史 + 工具结果 ~239K 全部按 100% 原价计费
```

当前实现（[plan-qwen3-7-flash-replacement.md](./plan-qwen3-7-flash-replacement.md) Phase 1）把 `cache_control` 标记放在**首条 system 消息**上，导致：
- 每条 agent 循环请求只缓存固定的 ~5103 token system
- 随循环增长的历史与工具结果（占 prompt 主体的 91.7%）永远按 100% 原价计费
- 深层原因：官方文档「显式/隐式互斥」——请求带任意 `cache_control` 即整体进入显式模式，排除了隐式缓存对尾部的覆盖

## 二、实验结论（2026-08-17 实测）

### 2.1 隐式缓存：对生产确认不可用（两条端点 + 思考模式影响）

25K token 稳定前缀重复请求 N 次，观察 `prompt_tokens_details.cached_tokens`：

| 端点 | enable_thinking | 结果 |
|------|----------------|------|
| legacy `dashscope.aliyuncs.com` | false | 完全无命中 |
| MaaS `ws-hjp2aklyr0ramcne.cn-beijing.maas.aliyuncs.com` | **未传（思考模式）** | 偶发命中 2/6（~30%，符合官方"命中率不确定"） |
| MaaS | **false** | 完全无命中（14+ 次全空） |

**结论**：生产强制 `enable_thinking=false`（思考模式延迟 12s+、单次输出 1K+ tokens，Agent 场景不可用）→ **隐式缓存在生产配置下完全不会命中，legacy / MaaS 端点皆然**。官方文档将 qwen3.7-flash 列入隐式缓存支持列表，但实测仅默认思考模式偶发命中，与生产配置无关。**放弃隐式缓存路径。**

### 2.2 显式末尾标记：唯一可行的全量缓存路径（已验证）

模拟 agent 循环（system 5K + N 轮 assistant tool_calls + tool 结果），`cache_control` 标记放在**消息数组最后一条**：

| 用例 | content 块数 | R1 创建 | R2 命中 | 结论 |
|------|------------|---------|---------|------|
| C6 | 13 | 5921（全数组） | **5921 命中** + 新增 143 创建 | 追加 1 轮后完整命中前序数组 |
| C13 | 27 | 命中 6064 + 创建 861 | **6925 命中** + 新增 144 创建 | 27 块数组完整命中，未被 20 块窗口截断 |

**关键机制**：
- 末尾标记创建的缓存块**覆盖整个消息数组**（system + 全部历史 + 工具结果）
- 下一轮追加新消息后，标记位置相邻（间隔仅新增 1 轮），前序块**完整命中（10%）**，仅新增尾部按**创建（125%）**计费
- 官方文档「20 content 块回溯」限制指**相邻标记之间的间隔**，只要每轮都在末尾放标记即不触发（C13 的 27 块完整命中实证）
- 与 `enable_thinking=false` 完全兼容

**成本影响**（244K prompt 场景）：输入侧从 ~239K×100% + 5103×10% 变为 ~239K×**10%** + 每轮新增尾部×125%，**输入成本约降一个数量级**。

## 三、方案设计

### 3.1 核心改动

`src/llm/providers/base.py` `_format_messages`：显式缓存标记从「首条 system」改为「**最后一条消息**」。

```python
def _format_messages(self, messages, use_cache=False):
    formatted = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "tool":
            formatted.append({
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id", ""),
                "content": json.dumps(content, ensure_ascii=False) if isinstance(content, dict)
                           else (content if isinstance(content, str) else str(content)),
            })
        elif role == "assistant" and "tool_calls" in msg:
            formatted.append({
                "role": "assistant",
                "content": str(content) if not isinstance(content, str) else content,
                "tool_calls": msg.get("tool_calls", []),
            })
        elif isinstance(content, list):
            formatted.append({"role": role, "content": content})
        else:
            formatted.append({"role": role, "content": str(content)})

    # 显式缓存：标记从「首条 system」改为「最后一条消息」。
    # 末尾标记创建的缓存块覆盖整个消息数组，下一轮前序块完整命中（10%），
    # 仅新增尾部按创建（125%）计费。仅 qwen 系文本模型生效（_is_qwen_model）。
    if use_cache and self._is_qwen_model() and formatted:
        last = formatted[-1]
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"],
                                "cache_control": {"type": "ephemeral"}}]
        elif isinstance(last["content"], list) and last["content"]:
            last["content"][-1] = {**last["content"][-1],
                                   "cache_control": {"type": "ephemeral"}}
    return formatted
```

### 3.2 设计决策

1. **标记放最后一条消息**：agent 循环每次 `chat_with_tools` 的消息数组末尾通常是本轮最新内容（首轮为 user 消息、工具轮为 tool 结果），末尾标记使缓存块随循环增长而"滚动覆盖全量上下文"。
2. **保持 `_is_qwen_model()` 守卫**：仍按 model 名前缀判定，仅 qwen 系文本模型生效；qwen provider 承载的百炼第三方模型（deepseek/kimi/glm）与 zhipu provider 不受影响。
3. **不叠加首条 system 标记**：末尾标记已覆盖全数组，再叠 system 标记只多一次 125% 创建、无命中收益（标记数上限 4，留余量给后续精细控制）。
4. **端点保持 legacy 不变**：显式缓存 legacy / MaaS 均完美命中，无切换必要（切换 MaaS 需改 base_url + 依赖 workspace，零收益）。
5. **`enable_thinking=false` 维持**：已实测与显式缓存无冲突。
6. **空 content 边界**：若最后一条是空 content（如空 assistant tool_calls 消息），空文本 + 标记不产生有效缓存块，但该场景在 agent 循环中不出现（tool_calls 后必跟 tool 结果）。

### 3.3 改动点

| 文件 | 改动 |
|------|------|
| `src/llm/providers/base.py` | `_format_messages` 缓存分支：首条 system → 最后一条消息（§3.1） |
| 单测 | `tests/unit/tools/test_llm_providers.py`（或现有 qwen/base provider 测试）更新缓存分支断言：标记在最后一条而非首条 |

**无需改动**：`qwen.py`（仍传 `use_cache=settings.llm.context_cache`）、`gateway.py`（system 前置逻辑不变）、`billing.py` / `session_record.py`（缓存计费口径不变：命中 10% / 创建 125% 已在 2026-08-16 落地）。

## 四、Phase 划分

| Phase | 内容 | 预计 |
|-------|------|------|
| Phase 1 | `base.py` 标记位置改动 + 单测更新 + 三智能体流程（开发→测试→CR） | 0.5 天 |
| Phase 2（可选，独立） | 工具结果截断：agent 循环 `messages.append(tool_result)` 处对超长工具结果限长，降低输入基数 | 已立计划：[plan-tool-result-truncation.md](./plan-tool-result-truncation.md) |

## 五、验证方式

1. **单测**：缓存分支断言更新（标记在最后一条、content 数组化、cache_control 字段），回归既有 86+ 单测。
2. **真实对话实测**（部署切换后）：触发长 Agent 运行（含多轮工具调用），核对 `chat_records.usage_breakdown`：`cached_input` 应覆盖主体、`cache_creation_input` 仅新增尾部、`non_cached_input` 显著缩小。
3. **对比基线**：同场景下对比改前（记录 5551：non_cached 223931）与改后数据，验证输入成本降一个数量级。

## 六、风险与回退

- **5 分钟 TTL**：显式缓存有效期 5 分钟（命中后重置）。Agent 循环内迭代间隔秒级，命中无虞；跨用户轮次若间隔 >5 分钟则整块重建（125%），但仍在命中窗口内会立即恢复。可接受。
- **工具定义一致性**：官方要求 tools 序列化一致才命中。agent 循环内 `AGENT_TOOLS` 恒定，不受影响。
- **回退**：单文件改动，`git revert` 或切 `context_cache: false` 即回到无缓存基线。

## 七、关联文档

- [qwen3.7-flash 平替实施计划](./plan-qwen3-7-flash-replacement.md)（当前显式缓存实现）
- [deepseek-v4-flash 平替模型调研](../research/deepseek-v4-flash-replacement-research.md)
- [官方缓存文档快照](../../ext/qwen-llm-context-cache.md)（含本业务空间端点与实测结论备注）
- [LLM 计费接入改造设计](../system/saas/llm-billing-integration-design.md)
