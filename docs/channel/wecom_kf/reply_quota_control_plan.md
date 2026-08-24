关联想法: wecom_kf 单轮回复配额管控（提示词注入 + 渲染层兜底）
关联设计: docs/channel/wecom_kf/wecom_kf_design.md
状态: 🔄 方案调整（Layer 1 已废除，配额风险改由发送侧长图化承接）
创建日期: 2026-07-01
---

# 微信客服单轮回复配额管控 — 开发计划

> 关联背景：[微信客服发送消息接口官网说明.txt](./微信客服发送消息接口官网说明.txt)

## 0. 2026-08 变更：Layer 1 提示词废除 + 长图化承接

**Layer 1（WECOM_KF_CHANNEL_PROMPT 提示词注入）已于 2026-08-19 废除**，`src/channels/wecom_kf/prompts.py` 中常量已删除（该文件保留 `MSG_EXPIRED` / `MSG_CREDIT_EXHAUSTED` 固定话术）。

废除原因（生产环境实测确认）：

1. **干扰工具失败后的恢复路径**：提示词的「产出节俭」约束（单轮 ≤4 单元、只产出第 1 份、长内容拆分）会把 agent 工具调用失败后的恢复策略压向「一步到位」——实测微信客服渠道 `excel_process fill_template` 报错后，模型直接弃用模板、以 markdown 一次性从零生成，而 Web 端（无此提示词）同样报错后会修正参数重试模板。同一租户同一模板，Web 端稳定套用模板、微信端稳定弃用模板，废除后行为一致。
2. **软约束不可靠**：LLM 是否遵守本就偶发失效（见 §2.1 自述），却持续给主任务带来负面影响，收益/成本倒挂。

配额风险的替代承接（`src/channels/wecom_kf/adapter.py` `send_message`）：

| 场景 | 发送行为 | 单元数 |
|------|----------|--------|
| 回复含 md 表格或图片 | 整段渲染为 1 张长图 image 消息（`contains_table_or_image` → `_send_full_text_as_image`，既有能力） | 1 |
| 纯文本超过单条 2048 字节、需要切分（2026-08-19 新增，按用户决策简化为「需切分即长图」，不再按切分条数判定） | 同样整段渲染为 1 张长图，失败降级分段 | 1 |
| 单条装得下的普通文本 | 维持 segment_markdown 分段 + 2048 字节切分 | ≤4 |

长图渲染/上传/发送失败时的降级日志已升级为 `logger.error`（带 open_kfid 与字节数），避免 Playwright/Chromium 异常导致的静默纯文本降级在线上无感知。

Layer 2（Redis 暂存 +「继续」flush）维持未实现；附件超配额打 zip 打包发送的方案已设计、暂缓开发。

以下原文保留，仅作历史方案记录。


## 1. 问题背景

### 1.1 现网事故

旅游咨询顾问智能体（wecom_kf 渠道）实际使用中：

1. 前几轮已确定目的地、人数、天数、出发时间，智能体给出一稿中文行程
2. 客户：「这个行程安排，你发我一份英文版，一份日语版的给我。」
3. 智能体单轮回复产出 7 个发送单元：
   - 英文文字 + 英文 md 表格图
   - 日文文字 + 日文 md 表格图
   - 中文总结
   - 英文 Word + 日文 Word
4. 微信客服 `send_msg` 接口报错：超出 5 条/48 小时上限，最后 2 个 Word 文档丢失

### 1.2 渠道硬约束

| 约束 | 值 | 来源 |
|------|-----|------|
| 用户发一条消息后，企业可下发消息上限 | **5 条** | 官网说明 §概述 |
| 下发时限 | 48 小时 | 官网说明 §概述 |
| 单条 text.content 字节上限 | **2048 字节**（超出截断） | 官网说明 §文本消息 |
| 单条 msgmenu.head_content | 1024 字节 | 官网说明 §菜单消息 |

> 「5 条」是**本轮回复所有发送单元（text/image/file/link/voice/video）的总和**，不是 5 段文字。一次 md 表格图 = 1 条 image，一个 Word = 1 条 file（本项目用 link 卡片下发，仍计 1 条）。

### 1.3 根因

1. **LLM 无渠道感知**：`agent.process_message_sync` 当前不接受任何渠道能力提示词，LLM 不知道有 5 条上限，会按用户字面要求一次性产出 N 份内容。
2. **渲染层无条数兜底**：`WeComKfAdapter.send_message`（`src/channels/wecom_kf/adapter.py:206`）遍历 markdown 分段 + downloadable_files 逐条 `send_msg`，没有「本轮已发 N 条」的计数与截断。
3. **md 表格转图片**本身受好评，需保留——不能通过关闭渲染来规避问题。

### 1.4 风险放大场景

客户一句「请给我翻译 10 国语言版本」即可让 LLM、send_msg 接口同时爆炸。必须从机制上限制单轮产出量。

## 2. 方案总览

采用**双层防护**：提示词软约束（主）+ 渲染层硬兜底（保底）。

```
┌──────────────────────────────────────────────────────────┐
│  Layer 1: 提示词注入（软约束，让 LLM 主动分批）          │
│  - wecom_kf 调用 agent 时注入「渠道能力说明」            │
│  - 多份文档/多语言 → 单轮只做 1 份，问客户是否继续       │
│  - 单轮产出：≤1 张表格图 + ≤1 份文件 + 简短文字          │
└──────────────────────────────────────────────────────────┘
                          ↓ 偶发失效
┌──────────────────────────────────────────────────────────┐
│  Layer 2: 渲染层条数兜底（硬约束，保接口不报错）         │
│  - adapter.send_message 统计本轮待发条数                 │
│  - 阈值 4 条（留 1 条余量给「继续」提示）                │
│  - 超出部分暂存 Redis，末尾发「回复『继续』获取剩余」    │
│  - 用户下一条匹配「继续」时 flush 暂存内容               │
└──────────────────────────────────────────────────────────┘
```

### 2.1 为什么不只取方向 1 或方向 2

- **只做提示词（方向 1）**：LLM 偶发不遵守，仍会触发 5 条上限报错，客户体验差。
- **只做单轮 1 张表格（方向 2）**：无法覆盖「英+日双 Word+双文字+双表格=7 条」这种非表格单元超量场景。表格只是其中一种发送单元。
- **必须合并**：方向 1 是方向 2 的子集 + 多语言/多文档拆分引导；方向 2 是渲染层硬兜底。两者互补。

## 3. Layer 1：提示词注入

### 3.1 注入点

新增 `agent.process_message_sync` / `process_message` 的可选参数 `extra_system_prompt: Optional[str]`，在 `_build_system_prompt` 返回值末尾追加。

调用链：

```
_process_tenant_wecom_kf_messages (channel_routes.py:1390)
  └─ channel_session_manager.process_and_persist (session.py:609)
       └─ _processor → agent.process_message_sync (agent.py:2908)
            └─ process_message → _process_message_impl → _build_system_prompt
```

改动：

| 文件 | 改动 |
|------|------|
| `src/core/agent.py:2908` `process_message_sync` | 新增 `extra_system_prompt` 参数，透传给 `process_message` |
| `src/core/agent.py:1743` `process_message` | 新增 `extra_system_prompt` 参数，透传给 `_process_message_impl` |
| `src/core/agent.py:738` `_build_system_prompt` | 新增 `extra_system_prompt` 参数，末尾追加该段 |
| `src/channels/session.py:609` `process_and_persist` | 新增 `agent_extra_system_prompt` 参数，透传给 `agent.process_message_sync` |
| `src/saas/api/channel_routes.py:1992` wecom_kf 调用点 | 传入 `WECOM_KF_CHANNEL_PROMPT` 常量 |

### 3.2 提示词内容草稿

存放位置：`src/channels/wecom_kf/prompts.py`（新建）

```python
WECOM_KF_CHANNEL_PROMPT = """---

## 当前渠道约束（微信客服）

你正在通过「微信客服」渠道与用户对话，该渠道有以下硬性限制，违反会导致消息发送失败：

1. **条数限制**：用户发一条消息后，你最多只能下发 **5 条**消息（包括文字、图片、文件、链接卡片等所有类型）。超过 5 条的部分会被微信拦截，用户收不到。
2. **单条文字限制**：单条文字消息不超过 2048 字节（约 600 汉字）。
3. **单轮产出预算**：为留安全余量，**单轮回复总产出 ≤ 4 个发送单元**（文字段 + 表格图 + 文件卡片各算 1 个单元）。

### 必须遵守的输出规则

**规则 1：多份文档/多语言分批输出**
当用户一次性要求多份内容（多语言翻译、多版本方案、多份报告）时：
- **只产出第 1 份**，其余暂不产出
- 输出后明确询问：「已为您提供 {第1份语言/版本}，是否继续生成 {剩余清单}？回复『继续』即可。」
- 禁止一次性输出 2 份及以上完整文档/翻译

**规则 2：单轮产出上限**
单轮回复最多包含：
- 1 张 md 表格（会自动转成图片）
- 1 份可下载文件（Word/Excel/PDF）
- 必要的简短文字说明（合计 ≤ 3 段，每段 ≤ 600 汉字）
- 若有剩余内容，按规则 1 询问是否继续

**规则 3：长内容主动拆分**
当单份内容（如完整行程方案）文字量超过 600 汉字时：
- 先发简短摘要 + 表格图
- 再问「是否需要查看完整版？回复『继续』获取」
- 不要在一条文字里塞满所有内容

**规则 4：拒绝过量请求**
若用户要求「翻译成 10 国语言」「生成 20 份方案」等明显超量请求：
- 礼貌说明「微信单次最多发送 5 条消息，我会分批为您提供」
- 询问优先级：「请告诉我最优先的 1 种语言/1 份方案，我先做这份」
- 禁止尝试一次性完成
"""
```

### 3.3 配置化

将提示词内容放到 `configs/config.yaml` 或租户 channel_config，允许运营按租户/客服账号微调（初期硬编码即可）：

```yaml
wecom_kf:
  channel_prompt:
    enabled: true
    content: ""  # 空则用默认 WECOM_KF_CHANNEL_PROMPT
```

## 4. Layer 2：渲染层条数兜底

### 4.1 阈值定义

| 参数 | 值 | 说明 |
|------|-----|------|
| `MAX_SEND_PER_TURN` | 4 | 单轮最多发送条数（留 1 条余量给「继续」提示） |
| `SAFE_TEXT_BYTES` | 1800 | 单条文字安全字节阈值（2048 的 88%） |

### 4.2 改动点：`WeComKfAdapter.send_message`

`src/channels/wecom_kf/adapter.py:206`，重构为：

```python
async def send_message(self, message: UnifiedResponse) -> bool:
    # 1. 收集本轮所有待发送单元
    units = self._collect_send_units(message)
    # units: List[SendUnit]，每项为 (type, payload)：
    #   text  -> {"content": str}
    #   image -> {"media_id": str}
    #   link  -> {title, url, thumb_media_id, ...}

    # 2. 超出 MAX_SEND_PER_TURN 的部分暂存 Redis
    session_key = self._pending_key(message.reply_to)
    if len(units) > MAX_SEND_PER_TURN:
        pending = units[MAX_SEND_PER_TURN - 1:]  # 留 1 条发"继续"提示
        sent = units[:MAX_SEND_PER_TURN - 1]
        self._save_pending(session_key, pending)
        sent.append(self._make_continue_hint_unit(len(pending)))
    else:
        sent = units

    # 3. 顺序发送（已合并 pending 的不重发）
    all_success = True
    for unit in sent:
        if not await self._send_unit(unit, message.reply_to):
            all_success = False
    return all_success
```

### 4.3 Redis 暂存结构

| 字段 | 说明 |
|------|------|
| Key | `wecom_kf:pending_continue:{external_userid}` |
| Type | String（JSON） |
| Value | `{"units": [...], "saved_at": 1700000000, "session_id": "..."}` |
| TTL | 24 小时（覆盖微信 48 小时窗口的半个周期，避免脏数据长期堆积） |
| 失效条件 | 用户回复「继续」flush 后删除；TTL 过期自动删除 |

> **注意**：暂存按 `external_userid` 而非 `session_id`，因为微信客服的 5 条额度绑定在「用户最近一次发消息」上，与本地 session_id 解耦。

### 4.4 「继续」flush 入口

在 `_process_tenant_wecom_kf_messages`（`channel_routes.py:1390`）处理用户消息**之前**，先检查是否为「继续」指令：

```python
# 伪代码，位置：channel_routes.py _process_tenant_wecom_kf_messages 内，
# 取到 user_input 之后、调 process_and_persist 之前
if _is_continue_intent(user_input):  # 匹配「继续」「继续生成」「下一份」等
    pending = adapter.load_pending(external_userid)
    if pending:
        await adapter.flush_pending(external_userid)
        # flush 后本轮 5 条额度已被暂存内容消耗，本次用户消息延后到下一轮处理
        # 给用户一条占位回复，告知「以上是上次剩余内容，请重新发送您的需求」
        await adapter.send_text("以上是上次未发送完的内容。您的新消息我已收到，请稍等。", external_userid)
        continue  # 跳过本轮 process_and_persist
# 非继续指令但存在 pending：丢弃 pending（用户已开启新话题）
adapter.clear_pending(external_userid)
```

### 4.5 「继续」意图识别

轻量匹配，避免引入 LLM：

```python
CONTINUE_PATTERNS = {"继续", "继续生成", "继续输出", "下一份", "下一段", "继续发", "继续发给我"}

def _is_continue_intent(text: str) -> bool:
    return text.strip() in CONTINUE_PATTERNS
```

> 不做模糊匹配（如包含「继续」就算），避免误判「我不想继续了」。

### 4.6 暂存内容反向序列化

`SendUnit` 中 `image`/`link` 类型依赖 `media_id` 和 `thumb_media_id`，这些 media_id 在 3 天后过期（微信临时素材有效期）。暂存策略：

- **text 单元**：直接存原文
- **image 单元**：存 `markdown_table` 原文（而非 media_id），flush 时重新渲染+上传
- **link/file 单元**：存 `file_id`（业务侧永久有效），flush 时重新走 `build_public_url` + 上传缩略图

这样避免 media_id 过期问题。

## 5. 改动清单

| # | 文件 | 改动 | 预计 | 阶段 |
|---|------|------|------|------|
| 1.1 | `src/channels/wecom_kf/prompts.py` | 新建，定义 `WECOM_KF_CHANNEL_PROMPT` | 0.1d | 阶段1 |
| 1.2 | `src/core/agent.py:2908` `process_message_sync` | 新增 `extra_system_prompt` 参数 | 0.1d | 阶段1 |
| 1.3 | `src/core/agent.py:1743` `process_message` | 新增 `extra_system_prompt` 参数透传 | 0.1d | 阶段1 |
| 1.4 | `src/core/agent.py:738` `_build_system_prompt` | 末尾追加 `extra_system_prompt` | 0.05d | 阶段1 |
| 1.5 | `src/channels/session.py:609` `process_and_persist` | 新增 `agent_extra_system_prompt` 参数 | 0.1d | 阶段1 |
| 1.6 | `src/saas/api/channel_routes.py:1992` wecom_kf 调用点 | 传入渠道提示词 | 0.05d | 阶段1 |
| 2.1 | `src/channels/wecom_kf/adapter.py:206` `send_message` | 重构为「收集 → 截断 → 暂存 → 发送」 | 0.5d | 阶段2 |
| 2.2 | `src/channels/wecom_kf/adapter.py` | 新增 `_collect_send_units` / `_save_pending` / `load_pending` / `flush_pending` / `clear_pending` | 0.4d | 阶段2 |
| 2.3 | `src/saas/api/channel_routes.py:1390` `_process_tenant_wecom_kf_messages` | 新增「继续」意图检查 + flush 入口 | 0.3d | 阶段2 |
| 2.4 | `src/channels/wecom_kf/prompts.py` | 新增 `CONTINUE_PATTERNS` 与 `_is_continue_intent` | 0.05d | 阶段2 |
| 3.1 | `tests/unit/channels/test_wecom_kf_quota.py` | 单测：条数截断、暂存序列化、继续意图识别 | 0.4d | 阶段3 |
| 3.2 | `tests/integration/test_wecom_kf_continue_flow.py` | 集成：超量 → 暂存 → 继续 → flush 全链路 | 0.3d | 阶段3 |
| 4.1 | `docs/channel/wecom_kf/wecom_kf_design.md` | 补充「单轮回复配额管控」章节 | 0.1d | 阶段4 |
| 4.2 | `docs/ideas.md` | 登记本方案条目 | 0.05d | 阶段4 |

总计：**约 2.6 人日**

## 6. 兼容性与风险

### 6.1 兼容性

- **`extra_system_prompt` 参数为可选**：其他渠道（wecom/dingtalk/feishu/web）不传，行为完全不变。
- **`process_and_persist` 新增参数可选**：现有调用方无需改动。
- **渲染层兜底仅在 wecom_kf adapter 内**：不影响其他渠道。

### 6.2 风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| LLM 仍偶尔超量（提示词失效） | 中 | Layer 2 渲染层硬截断兜底，保证接口不报错 |
| 暂存内容用户不再回复「继续」，数据残留 | 低 | TTL 24h 自动清理；用户发新消息时主动 clear |
| 「继续」意图误判 | 中 | 严格全等匹配，不做模糊匹配；误判时用户重发即可 |
| media_id 过期导致 flush 失败 | 中 | 暂存原文/file_id 而非 media_id，flush 时重新上传 |
| 多 worker 下 pending 状态不一致 | 中 | 全部走 Redis，不使用进程内存 |
| 用户在 48 小时窗口外回复「继续」 | 低 | flush 时若 send_msg 返回 errcode≠0，提示用户「会话已超时，请重新提出需求」并 clear pending |

### 6.3 不做的事

- ❌ 不引入 LLM 做意图识别「继续」（成本高、延迟大，全等匹配足够）
- ❌ 不做跨会话的暂存内容持久化（TTL 24h 够用，长期持久化反而易脏）
- ❌ 不关闭 md 表格转图片功能（受好评，保留）
- ❌ 不在前端/其他渠道注入此提示词（仅 wecom_kf 有 5 条硬限）

## 7. 验收标准

| 场景 | 预期 |
|------|------|
| 客户「发我英文版和日语版」 | 智能体只发英文版（文字+表格图+Word 共 ≤3 条），末尾问「是否继续生成日语版」 |
| 客户「翻译成 10 国语言」 | 智能体拒绝一次性完成，询问优先级 |
| 客户「继续」 | 智能体发送上次暂存的日语版 |
| LLM 失控一次性产出 7 条 | 渲染层截断为 4 条，末尾发「回复『继续』获取剩余 3 条」 |
| 客户 48 小时后回复「继续」 | 提示「会话已超时」，暂存清理 |
| 单条文字 3000 汉字 | 自动拆分为多条，每条 ≤ 600 汉字 |
| 其他渠道（web/钉钉/飞书） | 行为完全不变，无提示词注入 |

## 8. 上线节奏

1. **阶段 1（提示词注入）**：1 人日，先上软约束，观察 LLM 遵守率
2. **阶段 2（渲染层兜底）**：1.5 人日，硬兜底，保证接口永不报错
3. **阶段 3（测试）**：0.7 人日，单测+集成测试
4. **阶段 4（文档）**：0.15 人日

> 建议阶段 1 上线后跑 1-2 天观察 LLM 行为，再决定阶段 2 的阈值是否需要调整。
