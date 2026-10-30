# 计费审计规范

> **适用范围**：所有涉及 LLM / Embedding / ASR / 视频生成 / 短信发送 调用的代码改动--新增调用点、重构调用方、修改计费逻辑、定期计费审计。
>
> **背景**：2026-08-14 计费缺失审计发现项目存在 18 处真实缺口（详见 [llm-billing-gap-audit.md](../../docs/plans/llm-billing-gap-audit.md)），2026-10-30 全量复检确认已全部修复关闭；同日复检把短信发送纳入审计范围并发现 1 处缺口（见 §5）。根因是审计方法论盲区 + 服务/工具/管理后台多入口边界盲区。本规范把审计 checklist 固化为开发流程的一部分，**每次计费相关改动必须执行**，避免下次审计再次遗漏。

---

## 1. 何时需要执行计费审计

| 场景 | 执行范围 |
|------|---------|
| 新增 LLM/Embedding/ASR/视频调用点 | 该调用点 + 同模块相邻调用点 |
| 新增短信发送调用点 / 新接短信通道 | 该调用点 + `sms_manager.send` 全部调用方 |
| 重构调用方（如把同步改异步、把直接调用改 wrapper） | 该调用点 + 所有调用方 |
| 修改计费函数（`record_background_llm_usage` / `add_embedding_usage` / `add_asr_usage` / `_record_*_billing`） | 全量回归（执行 §3 checklist） |
| 定期计费审计（每季度或大版本前） | 全量执行 §3 checklist |
| 接入新渠道 / 新工具 / 新 API 路由 | 该入口 + 内部所有 LLM/Embedding/ASR 调用 |

---

## 2. 计费机制速查（先厘清再说审计）

主计费载体是 `chat_records` 表，三条计费通路：

| 通路 | 机制 | 触发条件 |
|------|------|---------|
| ① agent 主循环 | `agent.py` 的 `chat_with_tools` 后显式 `_record.add_llm_usage()` | 仅覆盖主循环**自身**的 LLM 调用 |
| ② 后台/工具服务显式计费 | `record_background_llm_usage()` 或独立 `_record_*_billing()` | 调用方主动调用 |
| ③ `gateway.chat()` 内 `record_response_usage()` | 依赖 ContextVar `_recorder`，仅 `install_usage_recorder()` 后生效 | **装饰性 no-op**，目前仅 `association_enrichment_ui` 一处安装 |

**关键陷阱**：通路③默认是 no-op，**不能作为"已计费"的依据**。真正落账靠通路①②显式调用。

---

## 3. 计费审计 Checklist（核心，8 步）

### 3.1 全量 LLM 调用扫描

```bash
grep -rn "gateway\.chat\b\|llm_gateway\.chat\b\|_gateway\.chat\b" src/ --include="*.py" \
  | grep -v "def chat\b\|async def chat\b\|# \|chat_with_tools"
```

**对每个命中点**，逐点读函数上下文，确认调用方是否在 `async def` 中且 `await`（避免 [followup_manager P1 bug](../../docs/plans/llm-billing-gap-audit.md#518-srcskillsfollowup-tracking-100scriptsfollowup_managerpy364附带-p1-bug-修复) 同步调用 async 函数致 LLM 实际未执行）。

### 3.2 全量 Embedding 调用扫描

```bash
grep -rn "embedding_client\.embed\|TextEmbeddingV3Client\|\.embed_batch\|\.embed_sync\|retriever\._embed\|TextEmbedding\.call" src/ --include="*.py" \
  | grep -v "def embed\|test_\|/tests/"
```

**排除项**（核实后确认无需计费）：
- 使用本地模型（如 `sentence-transformers`）的 embedding 调用，不调用云端 API

### 3.3 全量 ASR 调用扫描

```bash
grep -rn "SpeechToTextTool\|_call_aliyun_asr\|nls-gateway\|aliyun.*asr" src/ --include="*.py" \
  | grep -v "test_\|/tests/"
```

**重点核对**：BaseTool 的 `execute` 方法被多入口调用时（agent 主循环 / channel_routes / API 路由），计费应在工具内部统一补，外层不得重复补。

### 3.4 全量视频生成扫描

```bash
grep -rn "calculate_video_credit_cost\|video_agent\.service\|VideoChatService" src/ --include="*.py" \
  | grep -v "test_\|/tests/"
```

视频提示词 LLM 调用独立计费（`source_type=video_prompt`），不与最终视频生成计费合并（用户可能多次调整提示词后放弃创建视频）。

### 3.5 逐点对照"已计费五条件"

每个调用点必须满足以下**之一**才算"已计费"，否则记为缺口：

| 条件 | 实现方式 |
|------|---------|
| A | 紧邻调用处有 `record_background_llm_usage(response.get("usage"))` |
| B | 紧邻调用处有 `record.add_embedding_usage(tokens, model=...)` 或 `record.add_asr_usage(calls=1)` |
| C | 紧邻调用处有 `ChatRecordDB.create(...)` 独立落库（含 `source_type` 区分） |
| D | 紧邻调用处有专用计费函数：`_record_knowledge_embedding_billing` / `_record_background_llm_billing` / `_record_prompt_llm_usage` / `_write_billing_chat_record` |
| E | 调用处于 `install_usage_recorder` 上下文内（仅 `association_enrichment_ui` 模式） |

**核实方式**：读调用点所在函数的完整上下文（前后 50 行），确认 5 个条件之一成立。不能仅凭 grep 命中关键字判断--`record_background_llm_usage` 可能被注释、被 `if False` 包裹、或参数传 None。

### 3.6 工具入口 vs 渠道入口分离核对

对每个 BaseTool 的 `execute` 方法：

1. 列出所有调用入口：`grep -rn "<ToolName>\|<tool_name>" src/ --include="*.py"`
2. 每个入口分别确认是否计费：
   - agent 主循环工具调用：计费应在工具内部补（主循环不会自动覆盖工具内 LLM/Embedding/ASR）
   - 渠道侧调用（如 `channel_routes.py`）：若工具内部已计费，外层**不得重复计费**
   - API 路由调用：用独立 `ChatRecordDB.create(source_type=admin_*)` 落账
3. 避免双计：工具内部统一计费后，删除外层重复的 `add_*_usage` 调用

**已知案例**：`SpeechToTextTool` 上次计划只在 `channel_routes.py:2174` 外层补 `add_asr_usage`，工具自身无计费，导致 agent 主循环调用 ASR 时 100% 漏计。修复时必须把计费移到工具内部，同时删除 `channel_routes.py:2174` 的外层计费。

### 3.7 同步/异步核对

所有 `gateway.chat` 是 `async def`，调用方必须满足：

| 调用方类型 | 要求 |
|-----------|------|
| `async def` 函数 | `await gateway.chat(...)` |
| 同步 `def` 函数（命令行脚本、同步工具） | `asyncio.run(gateway.chat(...))`，且主线程无事件循环时才能用 `asyncio.run` |
| 已在事件循环内的同步代码 | 重构为 `async def` 或用 `asyncio.run_coroutine_threadsafe` |

**反例**（[followup_manager.py:364](../../src/skills/followup-tracking-1.0.0/scripts/followup_manager.py)）：同步 `def _evaluate_with_llm` 中 `result = llm_gateway.chat(...)`（无 await），返回 coroutine 对象，`result.get("content", "")` 抛 `AttributeError`，被 `except Exception` 吞掉返回默认评分。**LLM 实际从未执行**，既漏计费又坏功能。

### 3.8 全量短信发送扫描

```bash
grep -rn "sms_manager\.send\|sender\.send\|send_sms\|SmsSender" src/ --include="*.py" \
  | grep -v "test_\|/tests/\|src/sms/"
```

短信成本与 token 无关，**按条计费**（固定积分/条，当前 `SMS_CREDIT_COST=1.0`），核对要点与 LLM 不同：

1. **唯一发送出口**：所有真实发信必须经 `SmsManager.send`（`src/sms/manager.py`）。通道实现类（`src/sms/{aliyun,tencent,zhutong}.py`）只负责发送不自行计费；新增通道时同样不在此层计费。扫描对象是 `sms_manager.send` / `sender.send` 的**调用方**。
2. **租户归属判定**：服务于某个租户的短信（Agent 对话触发的验证码、租户业务通知）**必须对该租户计费**，归属从 `AID_*` 环境变量（skill_executor 注入，fail-closed）或 `request.state` 取，**不信任 LLM/请求参数**。确实无租户归属的短信（如平台运营自身通知）可不计费，但调用点必须**显式注释说明归属判定与豁免理由**，不得静默漏计。
3. **发送前余额预检**：积分余额不足时拒绝发送（短信发出后无法撤回，不能先发后补账）。
4. **bypass / 私有云分支**：`settings.sms.qb_sms_code` bypass（未购通道，不真实发信）与 `PRIVATE_CLOUD=true`（客户自购通道）不落账——这两类分流必须在调用方显式出现并注释，防止把"跳过发信"误当"已计费"。
5. **落账时机**：真实发送成功后落账 `chat_records`（独立 `source_type`，如 `sms_verification`），`ChatRecordDB.create` 同事务扣减 `tenants.credit_balance`；落账失败只记错误日志（短信已无法撤回），不向用户报错。

---

## 4. 修复模式参考

### 4.1 主循环工具内 LLM 调用（最常见的缺口模式）

```python
# src/tools/xxx/xxx_router.py
async def route(self, context, file_paths):
    gateway = self._get_gateway()
    response = await gateway.chat(messages=[...], temperature=0, max_tokens=512)

    # 补计费：
    from src.services.session_record import record_background_llm_usage
    record_background_llm_usage(
        response.get("usage") if isinstance(response, dict) else None,
        source="xxx_router",  # 标识来源，便于审计
    )

    content = response.get("content", "")
    ...
```

`record_background_llm_usage` 已实现双路径兜底：
- 有 `SessionRecordService`（主循环内）-> 累加到当前 record
- 无 `SessionRecordService`（background_runner / 命令行脚本）-> 独立落 `chat_records`（source_type=background_llm）

### 4.2 对话内 Embedding 调用

```python
# src/knowledge/retriever/hybrid_retriever.py
async def retrieve(self, query, top_k=10, ...):
    query_embedding = await self.embedding_client.embed(query)

    # 补计费：
    if getattr(self.embedding_client, "last_usage_tokens", 0) > 0:
        try:
            from src.services.session_record import SessionRecordManager
            record = SessionRecordManager.get_current_record()
            if record:
                record.add_embedding_usage(
                    self.embedding_client.last_usage_tokens,
                    model=getattr(self.embedding_client, "model", "text-embedding-v3"),
                )
        except Exception:
            logger.debug("Failed to record embedding usage", exc_info=True)

    raw_vector_results = await self.vector_db.search(...)
    ...
```

### 4.3 管理后台 API 路由 LLM/embedding 调用

```python
# src/api/xxx.py
@router.post("/optimize")
async def optimize(request: Request, ...):
    result = await llm_gateway.chat(...)

    # 补计费（独立落库，从 request.state 取租户/用户）：
    try:
        from src.db.models import ChatRecordDB
        from src.services.billing import calculate_credit_cost
        usage = result.get("usage", {}) if isinstance(result, dict) else {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        credit_cost = calculate_credit_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=settings.llm.model,
        )
        ChatRecordDB.create(
            session_id=f"admin_llm_ops_{request.state.user_id}_{int(time.time())}",
            tenant_id=request.state.tenant_id,
            user_id=request.state.user_id,
            user_message=f"[管理后台] optimize",
            source_type="admin_llm_ops",
            credit_cost=credit_cost,
            ...
        )
    except Exception as billing_err:
        logger.error(f"管理后台 LLM 计费落库失败: {billing_err}", exc_info=True)
```

**建议**：把上述计费逻辑封装为 `record_admin_llm_usage(response, request, source_label)` 工具函数，避免每个路由重复代码。

### 4.4 ASR 工具内部统一计费

```python
# src/tools/asr/speech_to_text_tool.py
async def _call_aliyun_asr(self, ...):
    ...
    if status_code == 20000000:
        text = result_json.get("result", "")
        # 补计费：ASR 调用成功后累加到当前 SessionRecordService
        try:
            from src.services.session_record import SessionRecordManager
            record = SessionRecordManager.get_current_record()
            if record:
                record.add_asr_usage(calls=1)
        except Exception:
            logger.debug("Failed to record ASR usage", exc_info=True)
        return {"success": True, "text": text, ...}
```

**关键**：工具内部计费后，**必须删除** `channel_routes.py:2174` 的外层 `add_asr_usage` 调用，否则渠道侧语音消息会双计。

### 4.5 短信计费（标准模式）

参考实现：`src/skills/sms-verification-1.0.0/scripts/sms_cli.py` 的 `_record_sms_billing` / `_check_credit_balance` / `_resolve_billing_identity`。新增短信发送点直接复用该套函数，不自建口径：

```python
# 1) 身份 fail-closed：仅取 skill_executor 注入的 AID_* 环境变量（或 request.state），
#    取不到租户身份时必须拒绝发送，防止计费落空
identity = _resolve_billing_identity()   # {tenant_id, user_id, session_id}
if not identity["tenant_id"]:
    return {"success": False, "error": "无法确认租户身份，短信发送被拒绝"}

# 2) 余额预检：credit_balance 不足 1 积分时拦截（发送前，非发送后）
blocked = _check_credit_balance(identity["tenant_id"])
if blocked:
    return blocked

# 3) 真实发送（sms_manager.send；bypass/私有云分支在更早处分流，不进入这里）
result = sms_manager.send(mobile, template_params={...})
if result and result.get("code") == 200:
    # 4) 落账：chat_records 审计记录 + 同事务扣减 tenants.credit_balance；
    #    落账失败只记日志（短信已发出，无法撤回）
    _record_sms_billing(mobile, identity)
```

关键语义：
- `ChatRecordDB.create(source_type="sms_verification", credit_cost=SMS_CREDIT_COST)` 内部已原子扣减 `tenants.credit_balance`，不要再手写 UPDATE 扣减（会双扣）
- bypass（`qb_sms_code` 已配置且无通道）不发信也不落账；私有云（`PRIVATE_CLOUD=true`，客户自购通道）发信但不落账，调用方分流
- 手机号进日志/落库描述必须脱敏（`_mask_mobile`：`138****0000`）

---

## 5. 已知计费缺口索引

### 5.1 2026-08-14 审计（18 处）——2026-10-30 复检已全部修复关闭

详见 [llm-billing-gap-audit.md](../../docs/plans/llm-billing-gap-audit.md)：

| 类别 | 数量 | 状态 |
|------|------|------|
| 主循环工具内 LLM 调用 | 9 处 | ✅ 已修复 |
| 管理后台 API LLM 路由 | 3 处 | ✅ 已修复（`record_admin_llm_usage` 收口） |
| 对话内 RAG embedding | 1 处 | ✅ 已修复（hybrid_retriever 条件累加 + 无 record 兜底） |
| 数据 schema 管理 embedding | 3 处 | ✅ 已修复（`record_admin_embedding_usage`） |
| ASR 工具入口 | 1 处 | ✅ 已修复（计费移入工具内部，渠道侧按需外补，不双计） |
| 离线运维脚本 embedding | 1 处 | ✅ 已修复（backfill_hotel_info_name.py 已补独立落库） |
| **合计** | **18 处** | |

### 5.2 2026-10-30 复检新增（短信维度）

| 调用点 | 场景 | 裁决 | 状态 |
|--------|------|------|------|
| `send_sms_code`（`src/db/models.py:2113`） | 登录 `/phone/send-code` 与重置密码 `/reset-password/send-code` 的平台认证验证码真实发信 | 用户裁决：登录/重置密码是平台级功能（参照主流互联网平台惯例），不计入租户费用；已在函数 docstring 显式注释豁免（2026-10-30），并约束其他短信发送点禁止复用该函数 | ✅ 豁免（注释已落地） |

已计费短信点（对照）：`sms-verification` 技能 `sms_cli.py`（AID_* fail-closed 身份 + 余额预检 + `_record_sms_billing` 落账，source_type=sms_verification）。**后续新增短信发送点一律走 §4.5 标准模式，禁止复用 `send_sms_code`。**

潜在风险（未启用/依赖单入口，扩展前需补计费）：
- `gateway.stream_chat()` - 无业务调用点，启用流式前需在 chunk 累积 loop 补计费
- `parse_association_evidence_with_llm`（`association_enrichment_ui.py:73`）- 死代码无调用方；接线时需补计费
- association 富化链路条件 E 依赖 `association_enrichment_ui` 唯一安装 `install_usage_recorder`——新增直接调用 `enrich_one()` 的入口会静默漏计，新入口必须同样包 recorder

---

## 6. 审计报告模板

执行全量审计后，输出报告应包含：

```markdown
# LLM 计费审计报告（YYYY-MM-DD）

## 审计范围
- LLM 调用扫描：N 处命中
- Embedding 调用扫描：N 处命中
- ASR 调用扫描：N 处命中
- 视频生成扫描：N 处命中
- 短信发送扫描：N 处命中（§3.8）

## 已正确计费调用点（N 处）
| 调用点 | 计费方式 | 满足条件 |
|--------|---------|---------|
| ... | record_background_llm_usage | A |

## 计费缺失（N 处）
| 调用点 | 场景 | 修复模式 | 优先级 |
|--------|------|---------|--------|
| ... | ... | §4.1 | P0 |

## 潜在风险（N 处）
| 调用点 | 问题 | 启用前补计费 |
|--------|------|-------------|

## 修复进度
- [ ] P0 缺口修复
- [ ] P1 缺口修复
- [ ] P2 缺口修复
- [ ] 不双计验证
- [ ] 租户归属验证
```

报告存放位置：`docs/plans/llm-billing-audit-YYYYMMDD.md`，并在 `docs/ideas.md` 「调研报告索引」或对应功能分区登记。

---

## 7. 维护责任

- **新增计费调用点时**：开发者按 §3 checklist 自检，PR 描述中附"已按 billing_audit.md §3 核对"
- **CodeReview 阶段**：审查者按 §3.5「已计费五条件」逐点核对，未满足的标 P0
- **定期审计**：每季度或大版本前执行全量审计，输出报告并更新 §5 缺口索引

---

## 关联文档

- [LLM 调用计费缺失审计](../../docs/plans/llm-billing-gap-audit.md) - 2026-08-14 审计报告（18 处缺口 + 7 条遗漏根因分析）
- [LLM 计费接入改造设计](../../docs/system/saas/llm-billing-integration-design.md) - 上次改造设计文档
- [租户信用计费设计](../../docs/system/saas/tenant-credit-billing-design.md) - 整体计费架构
