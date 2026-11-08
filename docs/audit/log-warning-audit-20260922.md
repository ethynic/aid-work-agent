# 生产与测试环境主日志 WARNING 审计（2026-09-20 ~ 09-22）

> 数据来源：
> - **生产新机**（129.211.65.243）容器 `aid-agent-api` / `aid-agent-background`（共享同一日志目录），`/app/log/agent/aid-work-agent_202609{20,21,22}.log`
> - **测试机 254**（124.222.3.254）容器 `aid-agent-api2` / `aid-agent-background2`（共享同一日志目录），同上路径；`aid-agent-api3`（0920 仅 2 条）单独核对
>
> 统计口径：`grep WARNING`，生产 **232 条**（09-20：218 / 09-21：2 / 09-22：12），测试 **79 条**（09-20：67 / 09-21：3 / 09-22：9）+ api3 2 条。按「日志位置 + 归一化消息」聚合。
> **处理结论（2026-09-22）：整体完成**——P2 Reply style not found 根因查明并完成恢复 + 观察点上线（见生产 #3 与处理清单）；其余类别经用户评估不重要、⏸ 暂不处理（复发再查）。前次 0918 一揽子开发项仍未部署，部署后大部分剩余噪音将消失。
> 与前次审计 [log-warning-audit-20260918.md](log-warning-audit-20260918.md)（生产）/ [log-warning-audit-20260918-testenv.md](log-warning-audit-20260918-testenv.md)（测试）交叉对照：**前次标记「待部署」的开发项在本次均有复发确认，见各条目说明**。逐项处理时在「处理状态」列更新。

---

## 生产环境分类汇总（232 条 / 33 类）

| # | 日志位置 | 条数 | 分布 | 优先级 | 处理状态 |
|---|---------|------|------|--------|---------|
| 1 | `src.wechat_mp.list_source:parse_list_page:166` | 186 | 09-20 三波（05 时 62 / 11 时 65 / 15 时 68） | **P1** | ⏸ 暂不处理（2026-09-22 用户评估收尾，复发再查） |
| 2 | `src.core.text_sanitizer:sanitize_text:26` | 13 | 09-20 ~ 09-22 零散 | P2 | ⏸ 暂不处理（0918 #4 已开发降级 INFO + source 标签，随部署消失） |
| 3 | `src.core.agent:_build_base_system_prompt:667` Reply style '轻松种草' not found | 2 | 09-22 | P2 | ✅ 已完成（根因查明 + 恢复，见下） |
| 4 | `src.tools.data_analysis.analysis_agent:run:230` MAX_ITERATIONS | 2 | 09-20 | 低 | ⏸ 观察（暂不处理，行号 158 → 230，代码已更新过一轮；单次跑满有产出兜底） |
| 5 | `src.tools.file.read_tool:execute:156` 读取失败（可自愈） | 10 | 09-20 ~ 09-22 | P3 | ⏸ 暂不处理（模型反复幻觉找 `数据分析助手·融合知识库.md`，提示词侧引导） |
| 6 | `src.knowledge.parsers.word_parser:parse:68` + `service:upload_document:621`（双写） | 10 | 09-22 集中，租户 c… | 低 | 不改（正常业务拒绝：Word 设了打开密码/老 .doc 改后缀，提示已给用户） |
| 7 | `src.saas.api.tenant_auth:password_login:434/494` | 4 | 09-22 | 低 | 不改（用户输错密码/手机号不存在，正常安全日志） |
| 8 | `src.tools.file.write_tool:execute:511` 非文本白名单后缀 | 2 | 09-20 | 低 | ⏸ 观察（暂不处理，.xlsx/.xls 以纯文本写入，可能产出损坏文件，建议模型侧改用分析工具导出） |
| 9 | `src.local_tools.proxy_tool:_heal_overlay:480` 弹层自愈 | 1 | 09-20 | 低 | 不改（自愈机制正常工作） |
| 10 | `src.wechat_mp.image_downloader:_normalize_pixels:418` 图片解码失败 | 1 | 09-20 | 低 | 不改（单条，跳过该图） |
| 11 | `src.wechat_mp.list_source:fetch_page:329` 清单接口错误 | 1 | 09-20 | 低 | ⏸ 暂不处理（随 P1 观察，复发再查） |
| 12 | `src.core.agent:_process_message_impl:3221` Reached max iterations | 1 | 09-22 | 低 | ⏸ 观察（暂不处理，0918 测试侧 #12 已开发升级 ERROR，**待部署**） |

### 重点明细

#### #1 wechat_mp 清单 publish_info 解析失败（186 条，占 80%）【P1】

```
wechat_mp 清单 publish_info 解析失败（跳过该条）
```

- **现象**：全部集中在 09-20，三个时间波（05:35 起 / 11 时 / 15 时各约 65 条）。`src/wechat_mp/list_source.py:166`——`appmsgpublish` 响应的 `publish_list` 条目里 `publish_info` 是字符串且 `json.loads` 失败，单条跳过不致命，但**该公众号对应文章全部丢清单**。
- **影响**：公众号文章清单获取批量失败，知识库入清单链路受损（WPS 托底清单源，参考 [wechat-download-api 原理调研](../research/wechat-mp/wechat-download-api-principle-research.md)）。
- **怀疑方向**：① 微信接口返回结构/加密变化；② 扫码会话凭证过期导致返回非 JSON（如登录页 HTML 片段）；③ 频控风控返回错误体。日志无原文打印，**排查第一步：在 parse 失败分支补原文片段（前 200 字符）日志**。
- **处理建议**：加原文采样日志 → 复现一次抓响应 → 按根因修（凭证续期 / 结构兼容 / 限频退避）。

#### #2 text_sanitizer 剔除异常字符（13 条）【P2】

0918 #4 已开发「降级 INFO + 补 source 来源标签」但**未部署**，本次 13 条仍是旧代码 WARNING。等部署后观察 source 标签定位是哪个外部文本入口仍在漏清洗（关联 [孤立代理字符治理](../../docs/incidents/)）。

#### #3 Reply style not found【P2】→ 已核查根因（2026-09-22）

```
Reply style '轻松种草' not found, skipping      （生产 2 条，来自 after-sales 子智能体）
Reply style '严谨清晰' not found, skipping      （测试 10 条，来自 data-analysis 子智能体）
```

**根因已查明，分两层**：

**① 生产 `reply_styles` 表被清空（数据丢失事故）**

- 迁移日备份 `aid_work_agent_20260915.sql.gz` 中该表有完整数据：4 个激活系统风格 `human-like`(v5) / `客服` / `轻松种草`(2026-06-18 建) / `严谨清晰`(2026-07-16 建) + 历史版本。
- 应用日志 `Loaded N reply styles` 曲线：09-14~09-18 恒为 4 → 09-20 00:38 起 0 → **09-20 15:23 重启 seed 回 human-like 1 条 → 09-21 10:32 起又为 0**。即**至少两次清空**（09-18~09-20 凌晨、09-20 晚~09-21 上午）。
- 删除未经审计 API（`user_behavior_logs` 无任何风格相关记录，删除端点均带 `@audit_action`）、非整库还原（chat_records 跨窗口连续）、repo 内外无删除脚本、sim.sh 有 fail-fast 断言只写仿真库 → **只能是直连 SQL 删除**，来源无法从现有日志归因。
- **风险面**：生产 PG 容器端口 10864 对公网开放（本机实测可达，安全组封禁一直未执行，见 [服务器连接信息] 记忆），口令已知且弱；也可能是 09-19（周六）前后团队手工操作数据库时误删。
- **影响**：'轻松种草'（after-sales）与 '严谨清晰' 等风格注入静默跳过；`human-like` 目前靠磁盘 fallback（`src/prompts/styles/human-like.md`）兜底生效，但磁盘版内容**旧于** DB v5（缺「提问方式：一次只问一个问题」等段落）；管理后台系统风格页现为空。

**② `subagent_definitions.reply_style` 存在悬空引用（两环境）**

- 生产：`after-sales='轻松种草'`；测试：`data-analysis='严谨清晰'`（09-22 14:58 还在更新，前端下拉对已有值有容错回显，保存时**不校验风格存在性**，悬空值随表单保存一直延续）、`travel-test='旅游客服'` 同样不存在。
- 建议改造：保存子智能体定义时校验 `reply_style` 在风格库中存在（或为空），悬空引用拒绝保存。

**修复方案（待执行）**：
1. 从 `aid_work_agent_20260915.sql.gz` 恢复 4 个系统风格（含 human-like v5 完整内容）到生产 `reply_styles`，恢复后调 `/api/admin/reply-styles/reload` 或等缓存 30s 过期。
2. **立即封禁 10864 公网访问**（安全组，属既有安全待办，本次事故提高其优先级）。
3. 团队内确认 09-19~09-21 是否有人直连生产库执行过 SQL。
4. （可选）子智能体保存校验 + 考虑 reply_styles 增加 `updated_at` 触发器级审计（应用层实现，见 database_dev.md 禁忌）。

#### #5 read_tool 幻觉路径（10 条）【P3】

```
读取文件失败（可自愈，模型可能传错路径）: 文件不存在: 数据分析助手·融合知识库.md
```

模型在多个目录层级反复尝试读 `数据分析助手·融合知识库.md`（知识库工具产物文件名，不是磁盘文件）。功能已自愈，但说明某子智能体提示词/工具结果里让模型误以为存在这个文件。低频，建议顺带在提示词里明确「融合知识库内容已直接给出，无需再读文件」。

---

## 测试环境分类汇总（79 条 + api3 2 条 / 13 类）

| # | 日志位置 | 条数 | 分布 | 优先级 | 处理状态 |
|---|---------|------|------|--------|---------|
| 1 | `src.wechat_mp.service:_mark_item_no_credit`（行号 3598/3617，代码新旧两版） | 54 | 09-20 全天，17 时一波 52 条 | P3 | ⏸ 暂不处理（wmp_test 测试租户余额用尽属预期，但 17 时 52 条/小时属**重试风暴**，建议退避或降噪） |
| 2 | `src.core.agent:_build_base_system_prompt:667` Reply style '严谨清晰' not found | 10 | 09-20 ~ 09-22 | P2 | ✅ 已完成（根因查明，随生产恢复+部署收口） |
| 3 | `src.subagents.registry:upsert_db_config:159` 显示名重复 | 6（+api3 2） | 09-20 | 低 | 不改代码（0918 已确认属正常设计并删除告警，测试容器**旧代码未部署**，随下版部署消失） |
| 4 | `src.core.agent:_reorder_messages_for_llm:1650/1667` 连续 user 丢弃 | 4 | 09-20 | 低 | 不改代码（0918 #3 已开发丢弃预览加长 500 字符，测试容器**旧代码未部署**；本次样本为真实用户连发消息场景，非 ASR 丢失） |
| 5 | `src.tools.file.read_tool:execute:156` 幻觉路径 | 3 | 09-20 | P3 | ⏸ 暂不处理（同生产 #5） |
| 6 | `src.tenant_custom.hongtao_shop.service:_finish_item:881` 已非 running 跳过终态写入 | 1 | 09-20 | 低 | 不改（僵尸覆写防护正常工作） |
| 7 | `src.wechat_mp.list_source:fetch_page:329` 清单接口错误 | 1 | 09-20 | 低 | ⏸ 暂不处理（随 P1 观察，复发再查；同生产 #11） |

### 重点明细

#### #1 余额不足跳过（54 条）【P3 降噪】

```
wechat_mp 余额不足跳过 item_id=17524 tenant_id=wmp_test_1a95a2edfffe
```

- 测试租户（wmp_test_*）余额用尽属预期；但 09-20 17:19 起一小时内 52 条（行号 3617 新代码路径），同一批 item 被反复扫描重复打标，**疑似任务未正确落「余额不足」终态导致重复跳过**。
- 处理建议：确认 `_mark_item_no_credit` 后 item 是否进入终态不再入队；若已终态，仅调整日志为采样/聚合输出。

---

## 逐项处理清单（按优先级）

- [ ] **P1** wechat_mp publish_info 解析失败：加原文采样日志 → 复现抓响应 → 定位根因（生产 186 条，功能受损）——⏸ 暂不处理（2026-09-22 用户评估收尾，复发再查）
- [x] **P2** Reply style not found：根因已查明（2026-09-22，见生产 #3）——生产 reply_styles 表被两次清空 + subagent_definitions 悬空引用
- [x] **P2-1** 从 09-15 备份恢复生产 reply_styles 4 个系统风格（2026-09-22 19:43 完成：8 行含 4 激活版导入 + 序列校准，应用已加载 `Loaded 4 reply styles`；顺带删除当日重启 seed 的 human-like v1 重复行）
- [x] **P2-3** 观察日志（2026-09-22 完成）：①生产 `reply_styles_audit` 表 + 触发器已上线（捕获 DELETE/UPDATE/TRUNCATE 的 db_user/client_addr/application_name，实测 UPDATE 已记录），任何来源（含直连 SQL）再删风格即可归因，查询 `SELECT * FROM reply_styles_audit;`；②应用侧 `style_manager` 风格数量下降打 WARNING（代码已加，随下版部署）；③增量已登记 deploy/db_update.yaml（2026-09-22 19:45:00）+ deploy/init-postgres.sql
- [ ] **P2-2** 封禁生产 PG 10864 公网访问——**用户决策暂不封**（2026-09-22）；团队确认无人手动执行 SQL，删除来源未明，外部直连嫌疑未排除，观察期内若 reply_styles_audit 再现删除记录即归因
- [ ] **P2** text_sanitizer 复发：核对 0918 一揽子开发项（sanitizer 降级、Redis 重试、max iterations 升 ERROR、显示名告警删除、丢弃预览 500 字）**部署状态**，部署后再观察
- [x] **P3** wechat_mp 余额不足重复跳过——⏸ 暂不处理（2026-09-22 收尾评估不重要）
- [x] **P3** read_tool 幻觉路径——⏸ 暂不处理（2026-09-22 收尾评估不重要）
- [x] **观察** AnalysisAgent MAX_ITERATIONS / write_tool 非文本后缀 / max iterations（生产）——⏸ 暂不处理（2026-09-22 收尾）

---

## 附：统计命令

```bash
# 生产
ssh -p 10167 ubuntu@129.211.65.243 "docker exec aid-agent-api sh -c \
  'grep -h WARNING /app/log/agent/aid-work-agent_202609{20,21,22}.log'"
# 测试
ssh ubuntu@124.222.3.254 "docker exec aid-agent-api2 sh -c \
  'grep -h WARNING /app/log/agent/aid-work-agent_202609{20,21,22}.log'"
```
