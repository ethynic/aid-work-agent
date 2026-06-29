---
关联想法: wecom_kf 转人工工具优化（schema + 渠道隔离）
关联设计: docs/channel/wecom_kf/wecom_kf_design.md
状态: 📋 待开发
创建日期: 2026-06-23
---

# 微信客服转人工工具优化设计

> 反向关联：本文件是对 [`wecom_kf_design.md`](./wecom_kf_design.md) 第 7.1 节「转人工工具」的优化迭代，原设计文档第 7.1 节内容待本次开发完成后同步更新。

## 一、背景

现有实现 [`src/tools/transfer_to_human.py`](../../src/tools/transfer_to_human.py) 存在以下问题：

1. **Schema 不明确**：`description` 只是「将会话转接给人工客服…」，没有告诉 LLM 这是**仅限微信客服渠道**的专用工具，导致 Agent 可能在钉钉/飞书/Web 渠道错误调用。
2. **`reason` 为可选字段**：默认值「用户要求人工服务」让 LLM 倾向直接复用默认值，丢失了真实的转人工原因（投诉/超出能力范围/明确要求等）。
3. **渠道隔离缺失**：当非微信客服渠道的 Agent 错误调用此工具时，现有代码会因为 `get_kf_context()` 返回 `None` 而报「不在微信客服会话上下文中」错误，但这属于**内部技术错误**，对用户不友好——Agent 可能还会去把这条错误翻译给用户听。
4. **关键词未配置的拒绝语义混乱**：现有逻辑以「关键词未配置」为由拒绝转人工，这个限制实际上是给**回调路径关键词触发**用的，不应该强加给 LLM 主动判断的转人工路径。

## 二、目标

- 让 LLM 明确知道：此工具是微信客服渠道的**专用工具**，其他渠道不要调用。
- LLM 主动判断转人工时，必须提供 `reason`，便于审计与会话元信息记录。
- 非微信客服渠道调用时，工具返回**业务友好**的「当前渠道未提供人工服务」提示，而非内部错误。
- 微信客服渠道未配置 servicer 列表时，返回明确的失败提示，由 Agent 自然回复用户。

## 三、Schema 优化

### 3.1 InputModel

```python
class TransferToHumanInput(BaseModel):
    reason: str = Field(
        ...,
        description=(
            "转人工的原因，必填。可选值参考："
            "「user_request」（用户明确要求人工）、"
            "「complaint」（用户投诉或情绪强烈不满）、"
            "「out_of_scope」（问题超出 AI 能力范围）、"
            "「repeated_failure」（连续多次无法解决用户问题）、"
            "「other」。"
            "也可直接填写简短中文描述。"
        ),
    )
```

**变更点**：`reason` 由可选 → 必填。

### 3.2 description（给 LLM 看的工具说明）

> **设计原则**：LLM 在推理时并不知道当前消息来自哪个渠道（系统提示词中不含渠道信息），因此 description **不应该**告诉 LLM "这是某某渠道专用工具"——这种约束 LLM 无法自行判断，等同于无效信息。渠道隔离完全由工具执行段处理。

description 只描述"什么时候应该转人工"和"reason 必填"两件事：

```
将会话转接给人工客服。

适用场景：
- 用户明确要求人工服务（"转人工"、"找客服"、"人工"等）
- 用户表达强烈不满、投诉情绪
- 用户的问题明确超出你的能力范围（如：涉及资金、法律判断、复杂业务办理）
- 连续多次尝试仍无法解决用户问题

不适用场景：
- 用户的问题你能解决（即使解决起来稍慢）
- 用户只是表达轻微的不耐烦

调用此工具后，无需再向用户发送任何文字回复（转接动作本身就是对用户的反馈）。
若工具返回失败，再根据失败原因回复用户。
```

### 3.3 usage_guide

**留空**（`usage_guide = ""`）。

理由：
- 渠道隔离由工具执行段（`get_kf_context()`）兜底，不需要也无法让 LLM 自行判断渠道。
- `reason` 必填已通过 Pydantic schema 强制约束，不需要在 usage_guide 里重复说明。
- "调用前先回复用户" 这类规则反而有害：在非微信渠道会让 LLM 先发"正在为您转接人工客服"再调工具，最后工具失败，用户被误导。所以 description 明确改为"调用后无需回复，失败再按失败原因回复"。

## 四、渠道隔离设计

### 4.1 判定方式

通过 `get_kf_context()` 是否为 `None` 判断当前是否处于微信客服渠道。

- **微信客服渠道**：`channel_routes.py::_process_tenant_wecom_kf_messages` 在调用 Agent 前会调用 `set_kf_context({...})`，所以 `kf_context` 不为 `None`。
- **其他渠道**：钉钉/飞书/Web/Gradio 等不会调用 `set_kf_context`，`get_kf_context()` 返回 `None`。

### 4.2 非微信渠道的返回

```python
ctx = get_kf_context()
if not ctx:
    logger.info("转人工工具被非微信客服渠道调用，已拒绝")
    return {
        "success": False,
        "error": "当前渠道未提供人工客服，请直接回复用户",
        "hint": "此工具仅在微信客服渠道下有效，请勿在此渠道继续尝试转人工，改为直接用文字回复用户处理其问题",
    }
```

**关键**：返回的 `error` 和 `hint` 文案要让 LLM 明白：
1. 当前渠道没有人工服务；
2. 不要再尝试调用此工具；
3. 应该直接用文字回复用户处理其问题。

> 注：因 description 不再要求 LLM "调用前先回复"，所以即使非微信渠道调用失败，LLM 也不会已经先发出"正在为您转接…"的误导性消息，可以直接根据 error 文案向用户解释。

## 五、微信客服渠道内的处理

### 5.1 关键词校验

**移除** LLM 工具路径上的「关键词未配置就拒绝」逻辑。

**理由**：
- 关键词触发（`should_transfer_to_human`）是**回调路径的关键字硬匹配**，用于在进入 Agent 之前直接拦截，不需要 LLM 判断。
- LLM 工具路径是 **Agent 主动判断**（基于上下文语义），不应该被关键词配置绑死。
- 如果租户不想让 Agent 主动转人工，应该通过 `kf_config.allow_agent_transfer`（新增）配置项控制，而不是复用关键词配置。

### 5.2 servicer 列表校验（保留）

```python
servicer_list = kf_config.get("servicer_userid_list", [])
if not servicer_list:
    return {
        "success": False,
        "error": "当前客服账号未配置人工客服人员，无法转接",
        "hint": "请联系管理员在客服账号配置中添加 servicer_userid_list",
    }
```

### 5.3 允许 Agent 主动转人工的开关（可选增强）

在 `kf_config` 中新增可选字段：

```yaml
kf_account:
  - open_kfid: "xxx"
    servicer_userid_list: ["zhangsan", "lisi"]
    human_transfer_keywords: ["人工", "转人工"]   # 回调路径拦截关键词
    exit_human_keywords: ["退出人工"]
    allow_agent_transfer: true                    # 是否允许 Agent 主动转人工，默认 true
```

- `allow_agent_transfer: false` 时，Agent 调用工具直接返回失败，提示「管理员已禁用 Agent 主动转人工」。
- 默认 `true`，保持现有行为兼容。

**此增强本次开发是否落地**：✅ 落地。理由是给租户管理员一个明确的开关，避免完全放开后某些租户的 Agent 过度转人工。

## 六、执行成功后的会话元信息更新

保留现有逻辑：

```python
channel_session_manager.update_session(
    session_id=session_id,
    metadata={
        "service_state": 3,
        "transferred_to": servicer_userid,
        "transfer_reason": reason,   # 现在必填，元信息记录更有价值
        "transfer_source": "agent",  # 新增：标记是 Agent 主动转，区别于关键词触发
    },
)
```

## 七、错误处理与日志

| 场景 | 日志级别 | 返回给 LLM |
|------|---------|-----------|
| 非微信客服渠道调用 | `info` | `success=False, error="当前渠道未提供人工客服"` |
| 微信渠道但 servicer 列表为空 | `warning` | `success=False, error="未配置人工客服人员"` |
| `allow_agent_transfer=false` | `info` | `success=False, error="管理员已禁用 Agent 主动转人工"` |
| 转接 API 调用失败 | `error` | `success=False, error="转接失败，请稍后重试"` |
| 转接成功 | `info` | `success=True, message="已转接人工客服（xxx）"` |

## 八、影响范围

| 文件 | 改动 |
|------|------|
| `src/tools/transfer_to_human.py` | 重写 description（去掉渠道误导、去掉"先回复"）、清空 usage_guide、`reason` 改必填；渠道隔离完全由 execute 段 `get_kf_context()` 判断；新增 allow_agent_transfer 开关；调整拒绝语义 |
| `src/saas/api/channel_routes.py` | （可选）在 set_kf_context 时一并传入 `allow_agent_transfer`，或在工具内自行从 kf_config 读取（已在 ctx 中） |
| 文档 | 更新 `wecom_kf_design.md` 第 7.1 节；在 `docs/ideas.md` 登记 |

**不改动**：
- `adapter.transfer_to_human`（API 调用层）保持不变
- 关键词触发路径 `adapter.should_transfer_to_human`（回调路径）保持不变

## 九、验收标准

1. ✅ 微信客服渠道下，Agent 调用工具能成功转接，会话 metadata 记录 `transfer_source=agent` 和 reason 值。
2. ✅ 微信客服渠道下，未配置 servicer 列表时返回明确失败提示。
3. ✅ 微信客服渠道下，`allow_agent_transfer=false` 时返回失败提示。
4. ✅ 非微信客服渠道（钉钉/飞书/Web/Gradio）调用时，工具在执行段返回「当前渠道未提供人工客服」失败提示，LLM 收到后直接用文字回复用户（不会先发"正在为您转接…"）。
5. ✅ LLM 的 `reason` 字段为必填（Pydantic schema 层校验）。
6. ✅ `usage_guide` 为空字符串，不污染系统提示词。
7. ✅ 单元测试覆盖以上所有场景。
