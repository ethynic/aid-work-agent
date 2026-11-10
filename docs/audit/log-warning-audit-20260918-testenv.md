# 测试环境主日志 WARNING 审计（2026-09-16 ~ 09-18）

> 数据来源：旧机 254（124.222.3.254）测试容器 `aid-agent-api2`，`/app/log/agent/aid-work-agent_202609{16,17,18}.log`。
> 统计口径：`grep WARNING` 共 **4840 条**（09-16：2204 / 09-17：1521 / 09-18：1115），按日志位置聚合为 **15 类**。
> 与生产审计 [log-warning-audit-20260918.md](log-warning-audit-20260918.md) 相互独立（生产 119 条），逐项处理时在「处理状态」列更新。
> **处理结论（2026-09-18）：全部完成**——修复 1 类（#1）、确认解决 1 类（#9）、升级 1 类（#12）、随部署消失 1 类（#4），其余经评估不重要，收尾不处理（#2/3/5/6/7/10/11/15）或属正常业务日志（#8/13/14）。

---

## 分类汇总

| # | 日志位置 | 条数 | 日期分布 | 优先级 | 处理状态 |
|---|---------|------|---------|--------|---------|
| 1 | `src.subagents.registry:upsert_db_config:159` | 4627 | 2142/1459/1026 | **P0 刷屏** | ✅ 已完成开发（用户确认显示名重复属正常设计，告警整段删除，待部署） |
| 2 | `src.local_tools.repository:expire_stale_claims:382` | 134 | 24/47/63 递增 | P1 | ⏸ 暂不处理（2026-09-18 收尾评估不重要） |
| 3 | `src.services.recruiting_resume_service:_resolve_job_link:731` | 12 | 全部 09-18 | P2 | ⏸ 暂不处理（2026-09-18 收尾评估不重要） |
| 4 | `src.core.agent:_reorder_messages_for_llm:1648` | 11 | 6/2/3 | 低 | 生产侧已完成开发（丢弃预览 500 字符），测试环境代码旧，随部署消失 |
| 5 | `src.wechat_mp.service:_mark_item_no_credit`（3 处行号 2201/2933/3584） | 14 | 21/3/1 | 低 | ⏸ 暂不处理（2026-09-18 收尾评估不重要） |
| 6 | `src.local_tools.proxy_tool:_heal_overlay`（334/480） | 8 | 1/0/7 | 低 | ⏸ 暂不处理（2026-09-18 收尾评估不重要） |
| 7 | `src.saas.api.channel_routes:_transcribe_voice_with_asr:454` | 5 | 全部 09-16 | P1 | ⏸ 暂不处理（2026-09-18 收尾评估不重要，复发再查） |
| 8 | `src.knowledge.service:upload_document:621` + `word_parser:61/68` | 5 | 0/1/4 | 低 | 正常业务拒绝，不改 |
| 9 | `src.services.billing:calculate_credit_cost_with_breakdown:223` | 2 | 09-16 | **P1 计费缺口** | ✅ 已解决（价目行 09-16 14:07 补入，告警发生在补配置前的时间窗，之后零复发；兜底计费逻辑随下版部署） |
| 10 | `src.tools.skill.skill_execute_tool:execute:311` | 2 | 09-17 | P2 | ⏸ 暂不处理（2026-09-18 收尾评估不重要，复发再查） |
| 11 | `src.core.agent:_build_base_system_prompt:667` | 2 | 09-17 | P2 | ⏸ 暂不处理（2026-09-18 收尾评估不重要） |
| 12 | `src.core.agent:_process_message_impl:3216`（max iterations 20） | 2 | 09-17 | 低 | ✅ 已完成开发（升级为 ERROR，进入错误日志库，待部署） |
| 13 | `src.wechat_mp.service:claim_and_run:734/788` | 2 | 09-16 | 低 | 正常竞争让位，不改 |
| 14 | `src.saas.api.channel_routes:_process_tenant_wecom_kf_messages:2447` | 2 | 09-17 | 低 | 正常状态跳过，不改 |
| 15 | `src.local_tools.proxy_tool:_dispatch_and_wait:180`（invocation 超时） | 1 | 09-16 | 低 | ⏸ 暂不处理（2026-09-18 收尾评估不重要，与 #2 一并观察） |

---

## 逐类明细与处理建议

### 1. 显示名重复告警刷屏（4627 条，占 95.6%）【P0】

```
显示名重复: agent_id=aidefine-sales-assistant 与 agent_id=pre-sales 均为 「爱定义AI数字员工顾问」，两条并存，展示层需以 agent_id 区分
显示名重复: agent_id=pre-sales 与 agent_id=aidefine-sales-assistant 均为 「爱定义AI数字员工顾问」，两条并存，展示层需以 agent_id 区分
```

- **现象**：`pre-sales` 与 `aidefine-sales-assistant` 两个 agent_id 显示名相同（「爱定义AI数字员工顾问」）。每次 `upsert_db_config` 都全量扫描 `_configs` 并对重名打 WARNING；两个配置各 upsert 一次，每次调用产出 2 条（正反两个方向）。三天 4627 条 ≈ 每天约 770 次 upsert 调用（~2 分钟一次），说明 upsert 调用频率本身极高（疑为每次消息处理/会话都触发 DB 定义加载）。
- **影响**：把真正有价值的 WARNING 淹没；同时暗示 upsert 高频执行本身有性能开销（每次全量扫描重名）。
- **处理结论（2026-09-18 用户确认）**：**智能体显示名重复是正常设计**（两条并存、展示层以 agent_id 区分，docstring 本就注明「显示名允许重名」），告警与设计矛盾，整段重名检查已从 `upsert_db_config` 删除（不再扫描、不再告警），单测 16 用例通过，待部署。

### 2. 本地工具 invocation 租约过期置 unknown（134 条，日递增 24→47→63）【P1】

```
后端日志：N 条本地工具 invocation 租约过期，置为 unknown
```

- **现象**：`expire_stale_claims` 周期性扫描（约 20 秒一次），每次发现 1~4 条过期租约。三天逐日递增（24→47→63），说明测试机 RPA 端（boss 直聘相关本地工具）有积压的 invocation 长期无人认领/回执。
- **影响**：invocation 被置为 unknown，用户侧行为表现为本地工具无响应；递增趋势提示 RPA 客户端可能离线或处理变慢。
- **处理建议**：查测试环境本地工具客户端在线状态与积压 invocation 的 tool 分布（哪类工具、哪台 RPA 机）；确认是测试机常态（无人值守）还是真异常。若是测试环境常态噪音，可考虑降级或对 unknown 置位做汇总日志。

### 3. recruiting 简历未关联职位（12 条，全部 09-18）【P2】

```
简历未关联职位（无精确匹配）: tenant=tenant_1dc997a1806b, job_name=php工程师
```

- **现象**：测试租户 `tenant_1dc997a1806b` 的简历解析未找到精确匹配职位（php工程师）。12 条集中在 09-18，与批量投递测试相关。
- **影响**：业务告警本身合理（提示匹配失败），但当前是逐条 WARNING，批量投递时会刷屏。
- **处理建议**：确认为测试行为后可保持（低频）；若生产批量投递场景出现，考虑汇总为一条（N 条简历未匹配职位，job_name 列表）。

### 4. _reorder_messages_for_llm 丢弃连续 user 消息（11 条）【低，已有方案】

```
后端日志：_reorder_messages_for_llm 检测到连续 user，已丢弃较早的 N 条（保留最新）。被丢弃 user 内容: ["'[语音消息]'", "'好的'"] ...
```

- **现象**：与生产审计 #3 相同。样本中 `[语音消息]`（内容为占位符，真实 ASR 文本在别处）与短确认语（'好的'/'了解了'）被丢弃。测试环境代码旧，尚无 500 字符预览增强。
- **处理状态**：生产侧已完成开发（丢弃预览加长到 500 字符，含完整 ASR 文本），测试环境随下个版本部署自然解决，无需单独处理。

### 5. wechat_mp 余额不足跳过（14 条）【低】

```
wechat_mp 余额不足跳过 item_id=16803 tenant_id=wmp_test_7904e21f320c
```

- **现象**：全部为 `wmp_test_*` 测试租户，余额不足时逐条打 WARNING。3 处调用点（行号 2201/2933/3584）。
- **影响**：测试环境常态噪音；生产上该告警有意义（提示租户欠费影响推送）。
- **处理建议**：保留但可按租户聚合（同租户连续跳过多条时汇总一条）；优先级低。

### 6. 本地工具弹层遮挡自愈（8 条）【低】

```
后端日志：本地工具失败且疑似弹层遮挡，触发弹层自愈 tool=boss_select_job code=UI_CHANGED
```

- **现象**：boss 系列工具（select_job / interview_demo / filter）执行时遇 UI_CHANGED，触发弹层自愈。自愈是设计内防御动作。
- **处理建议**：属正常防御日志，可降级 INFO 或保留；低频（8 条/3 天），暂不改。

### 7. wecom_kf 语音转文字 ASR HTTP 400（5 条，全部 09-16）【P1】

```
[wecom_kf] 语音转文字失败: 阿里云 ASR 服务异常: HTTP 400
```

- **现象**：09-16 15:10~15:41 集中 5 条（两波：15:10 两连发、15:36 两连发 + 15:41 一条），成对出现像同一用户重发语音。HTTP 400 说明请求参数/音频格式有问题（非限流）。
- **关注点**：与生产审计 #7（09-16 单条）同日出现，且记忆中 [aliyun-asr-accesskey-renewal.md](aliyun-asr-accesskey-renewal.md) 提示 ASR 凭据曾续期——需确认测试环境 ASR 凭据/参数是否与生产不同步。
- **处理建议**：核对 09-16 当天 wecom_kf 语音消息的格式（amr/silk 转码链路）与 ASR accesskey 状态；确认是否用户语音过短/空音频导致 400。若属音频格式问题，补转码兜底并细化报错。

### 8. 知识库 Word 文档内容不合法拒绝（5 条）【低，不改】

```
文档内容不合法被拒绝: storage/tenants/9b9fb62aff5e/knowledge/kb_*.docx, 原因: 文件不是标准 Word 文档（内容格式与 .docx 不符/可能已设置打开密码...）
```

- **现象**：租户 `9b9fb62aff5e` 上传 3 个损坏/加密 docx 被拒（upload_document 与 word_parser 各记一条，共 5 条）。
- **处理建议**：正常业务拒绝 + 用户可读报错，不改。日志双写（service + parser 各一条）可考虑去重，优先级低。

### 9. 计费：模型 deepseek-flash 未配置单价（2 条）【P1 计费缺口】

```
计费：模型 deepseek-flash 未配置单价，credit_cost=0
```

- **现象**：09-16 10:47 / 11:10 两条（wechat_mp 总结场景，`gateway.chat has_tools=False`）。`deepseek-flash` 调用落账时单价表缺失，credit_cost=0，等于漏计费。
- **关联**：生产审计 #1 显示 deepseek-flash 于 09-16 起接入（deepseek-v4-flash 改名），本地 810b3169 已同步模型映射，但**计费单价配置**未跟上。
- **处理结论（2026-09-18 复核）**：**无需改代码**。测试库 `token_cost_prices` 的 `deepseek-flash` 价目行（3.0/9.0）于 **09-16 14:07:47** 补入，两条告警（10:47/11:10）发生在补配置**之前**的过渡窗，之后 09-17/09-18 零复发。另外本地 09-15 负责人定版的兜底计费逻辑（`BILLING_FALLBACK_MODEL="deepseek-flash"`，`billing.py:30`，注释即源于实测 agent2 此场景）尚未部署到测试环境，随下版部署后价目表滞后也不再落 0。

### 10. skill_execute stderr: spec-to-quotation-list 脚本报错（2 条）【P2】

```
[skill_execute] stderr: skill=spec-to-quotation-list, Traceback ...
  File "<stdin>", line 6, in <module>
NameError: name 'null' is not defined
```

- **现象**：09-17 15:37~15:39 两次。错误形态是 LLM 生成的内联 Python 代码里写了裸 `null`（JSON 字面量混入 Python）+ json.load 解析失败。
- **影响**：skill 执行失败，对应会话的报价清单功能未产出。
- **处理建议**：查看该 skill 的 prompt 是否在示例中混用 JSON `null`；在 skill 指令中明确「Python 代码用 None 不用 null」，或对生成代码做 null→None 预处理。低频，可先观察是否复发。

### 11. Reply style '严谨清晰' not found（2 条）【P2】

```
Reply style '严谨清晰' not found, skipping
```

- **现象**：某会话/数字员工配置了回复风格 `严谨清晰`，但样式库中不存在，跳过。
- **处理建议**：定位 style 库定义处，补 `严谨清晰` 枚举或修正该配置值；顺带排查是否还有其他失效 style 名。

### 12. Reached max iterations (20)（2 条）【低，升级 ERROR】

- **现象**：09-17 两个会话达到智能体循环上限 20 轮被截断。
- **处理结论（2026-09-18 按用户要求升级）**：`src/core/agent.py:3236` 日志级别 WARNING -> ERROR，触发时进入错误日志库，便于汇总观察哪些场景反复触顶；若后续确认某子智能体/场景频繁触顶，再评估任务拆分或上限调整（子智能体可经 `context.max_iterations` 配置，见 20260917-1200）。

### 13~15. 正常竞争/状态类（不改）

- **13 claim_and_run 让位**（2 条）：wechat_mp 多实例领取撞 running 唯一约束后主动让位，设计内竞争行为。
- **14 wecom_kf remote_state=3 跳过**（2 条）：远程会话状态不允许机器人发消息，正常跳过。
- **15 invocation 超时 boss_resume_batch**（1 条）：单次，已请求取消；与 #2 一起观察 RPA 端状态。

---

## 处理结果（2026-09-18 收尾）

1. **#1 显示名重复刷屏**：✅ 用户确认显示名重复属正常设计，告警整段删除（`src/subagents/registry.py`）
2. **#9 deepseek-flash 单价**：✅ 复核为配置时差（价目行 09-16 14:07 补入，告警在此之前），零复发，无需改代码
3. **#12 max iterations**：✅ 按用户要求升级为 ERROR（`src/core/agent.py:3236`）
4. 其余（#2/3/5/6/7/10/11/15）：⏸ 经评估不重要，收尾不处理；#8/13/14 属正常业务日志不改；#4 随下版部署消失
