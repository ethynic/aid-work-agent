# 数字员工实例入口普通用户消息 403 与数据分析「没有数据」误报复盘

> 2026-10-23 ~ 10-26，agent2 测试环境，租户 `tenant_53cfbdef870c`（羲和）。
> 修复开发任务：`docs/ideas.md` 数字员工分区 20261026-1340 条目。

## 1. 现象（两轮）

| 时间 | 现象 | 表面矛盾 |
|---|---|---|
| 10-23 16:29~16:33 | 测试普通用户（139****3000）在 web 问"6月份美国/加拿大的销售情况"（trace `tr_runner_6e1f74ef1bee4ca68f469ffb15bff538` 等），智能体答"全库只有日本数据、没有美国/加拿大"；管理员 16:35 同租户问"加拿大6月的销售数据"正常出数 | 数据明明已上传 |
| 10-26 11:44 | 同一普通用户从「数据分析-加拿大」实例入口（`/t/{tenant}/chat/data-analysis-tenant-53cfbdef870c-yreb`）发消息，前端显示"消息未发送成功" | 入口、授权都对，发不出去 |

## 2. 结论：两层独立原因

### 2.1 第一轮「没有数据」：数据表智能体名单 + 会话未路由（设计内行为，非 bug）

- 该租户 4 张狗粮销售表配置了 `documents.metadata.allowed_agent_ids`（数据表按智能体可用范围，设计见 `docs/plans/plan-data-analysis-agent-scope.md`）：日本表未配置（全租户共享）；加拿大表限 `[数据分析-加拿大实例, data-analysis]`；美国/韩国表各限对应实例。
- 该会话落库 `subagent_id=None`，由**主智能体**执行（LLM span 系统提示词为主智能体）；`analyze_data` 按 `agent_id = subagent_id or 'main'` 做名单过滤（`src/tools/data_analysis/smart_analysis_tool.py`），fail-closed 下 `main` 不在任何名单 → 三张受限表不可见，`list_data_tables` 只返回共享的日本表。智能体对可见范围的回复是真实的，不是幻觉。
- 管理员 10-23 的会话带 `subagent_id=数据分析-加拿大实例`，在名单内，所以能看到。

### 2.2 第二轮 403：执行授权与授权写入口径不一致（真 bug，已修）

- runner 执行授权 `src/services/agent_runner/authorization.py::_profile` 把实例 agent_id 归并为基础员工后查 `user_agent_permissions`；而授权界面写入口径（`src/saas/db/permission_db.py::set_permissions`）存的是**实例 agent_id**。两种口径永不相交 → 普通用户 + 实例入口必然 `PROFILE_FORBIDDEN` 403。
- 证据链：会话创建/能力探测 200（nginx），`POST /api/chat/runners` 403（100 字节定长错误体，与 PROFILE_FORBIDDEN 17 字符吻合）；runner 事件表零记录（拒绝发生在受理前）；DB 模拟判定：订阅检查过、用户权限按 base 查空。管理员（platform/tenant_admin 跳过用户级权限检查）与主智能体（profile=main 早退）不受影响，与两轮现象完全吻合。

## 3. 修复与验证

- 改动：`_profile` 用户级权限查询改为 `agent_id IN (请求的原始 profile_id, 归并后 base)`，订阅检查与全部 fail-closed 分支不动；兼容授权数据的两种存量形态（实例 ID / base ID）。
- 测试：`tests/integration/agent_runner_service/test_runner_profile_instance_auth.py` 新增「实例 ID 授权放行 + 删行后拒绝」「仅授权实例不放大到兄弟实例/base」两个用例；目标文件 14 用例全绿；`test_api.py` 回归除 2 个 HEAD 基线即存在的存量失败（`wecom_kf` actor proof、pagination cursor，与本改动无关，已用 HEAD 还原比对实证）外全绿。
- 流程：开发 → 独立测试 → CR 三智能体串行通过；未提交（等用户指令）。

## 4. 遗留与建议

1. 该租户数据表名单配置口径不一致：加拿大表额外放行了基础 `data-analysis`，美国/韩国表没放——管理员用基础数据分析助手能看加拿大但看不到美国/韩国，建议管理员复核是否有意。
2. 体验优化（未排期）：主智能体对租户内"存在但无权查看的受限数据表"完全无感知，只会答"没有数据、请上传"；可考虑 `analyze_data` 在仅共享表可见且租户存在受限表时附加提示。
3. 体验优化（未排期）：主智能体路径下用户感知不到"应去对应数字员工入口"，产品可引导。
4. `test_api.py` 两个存量测试失败（`wecom_kf` actor proof 400、pagination cursor error）与本次无关，另立任务排查。

## 5. 经验教训

1. **授权数据的写入侧与校验侧必须有单一权威口径**：实例化（租户副本）功能引入后，写侧按实例 ID、读侧按归并 base，两边各自"正确"但组合即故障；`checker.py::check_agent_access` 早已是"实例或父定义任一命中"，runner 侧未同步。
2. **「没有数据」与「无权限」必须区分**：fail-closed 过滤让智能体对不可见表完全无感知，容易输出误导性结论；修复 403 后经正确入口进入即自然解决，但体验层缺口仍登记待办。
3. 排查路径回顾：前端"消息未发送成功"（而非"自动重试中"）→ 4xx 定性；runner 事件表零记录 → 受理前拒绝；nginx 403 + 错误体长度 → 定位具体错误码；DB 模拟授权判定 → 根因实锤。
