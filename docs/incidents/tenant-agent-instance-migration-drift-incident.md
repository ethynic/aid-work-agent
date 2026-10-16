# 数字员工定制弹框扩展提示词不回显：迁移块原地改写致错键数据残留

日期：2026-10-16。测试环境（agent2）现象，生产库未受影响。

## 事实

租户 `tenant_e9b2fab93a2f` 的「销售助手」扩展提示词此前通过租户前台定制提示词页面填写过，改版后的「我的数字员工」定制弹框打开后该字段为空；数字员工实际对话行为正常。

只读核查测试库：

- `tenant_agent_instances` 中该租户只有一行，`agent_id = base_agent_id = tenant_e9b2fab93a2f`（`created_by = migration_20261014`），`extra_prompt` 有内容。全库 6 行均为「`agent_id` = `tenant_id`、每租户一行」形态。
- `prompt_registry` 中 `scope = 'tenant_extra'` 有 8 条注册记录，`scope_id` 形如 `extra:{subagent}:{tenant_id}`（如 `extra:sales-assistant:tenant_e9b2fab93a2f`），即应有 8 行直定制行。
- 该表结构也是草稿版：只有 `agent_id` 单列全局唯一约束（`tenant_agent_instances_agent_id_key`），缺 `uq_tenant_agent_inst UNIQUE (tenant_id, agent_id)` 与副本部分唯一索引 `uq_tenant_agent_inst_copy`；生产库两者齐全。

定制弹框按卡片 `agent_id`（真实目录名 `sales-assistant`）查实例行，`/api/subagents` 的直定制覆盖项合并（`_apply_direct_customization_overrides`）同样按真实 agent_id 匹配，二者都命不中错键行，故回显为空、卡片也不显示覆盖的显示名/简介。

## 原因

`2026-10-14 16:47:01` 的实例表迁移块曾以草稿形态落库（表带 `agent_id` 全局唯一、直定制行 `agent_id`/`base_agent_id` 误用 `tenant_id`）。其后该块被原地改写为按 `substring(scope_id from '^extra:([^:]+):')` 建键，**但 datetime 未变**（仍为 16:47:01）。测试库水位已推进到 `2026-10-14 22:45:00`，改写后的正确版不再进入 pending，草稿数据与结构永久残留。生产 10-15 才部署，直接执行的是正确版（13 行键与约束均正确）。

运行时 `_load_extra_md` 在实例行未命中时会回退 `prompt_registry` 的 `tenant_extra`，因此提示词仍被加载、智能体行为正常，掩盖了数据错键——故障只从弹框回显这一个口子暴露出来。

## 影响

测试库 6 个租户的直定制行不可达：定制弹框的扩展提示词/显示名/简介回显为空（保存会把空值写回实例行，但运行时仍走 registry 回退，实际未丢失）；`/api/subagents` 覆盖项合并失效。`agent_id` 全局唯一还会让第二个租户对同名基础员工创建直定制行时直接撞唯一约束。生产库不受影响。

## 修复

1. `deploy/db_update.yaml` 追加 `2026-10-16 18:30:00` 幂等补偿块：动态删除 `agent_id` 单列唯一约束（按 `pg_constraint` 查列构成，不依赖约束名）→ 删除 `agent_id = base_agent_id = tenant_id` 的错键行 → 补 `(tenant_id, agent_id)` 唯一约束与副本部分唯一索引 → 按 `prompt_registry` 的 `tenant_extra` 回填正确直定制行（`ON CONFLICT DO NOTHING`）。生产等已跑正确版的库 DROP 无命中、INSERT 冲突跳过，无副作用。
2. 运行时语义收敛（`prompt_sources._load_extra_md`）：实例行存在时一律以其 `extra_prompt` 为准，空即租户已清空定制；仅当无实例行时才回退 `prompt_registry`。修掉「弹框清空扩展提示词后运行时仍生效」。

## 验证

- 测试库事务内试跑后正式执行：`DELETE 6`、`INSERT 0 8`，约束与生产一致。
- 用 1 小时临时 token 调 `/api/saas/tenant/agent-instances`（`X-Tenant-Id: tenant_e9b2fab93a2f`）确认返回 `sales-assistant`（1915 字）与 `data-analysis`（250 字）两行且 `extra_prompt` 非空；token 已删除。
- 单测 `tests/unit/api/test_subagent_extra.py`、`test_tenant_agent_instance.py`、`test_tenant_instance_permissions.py`、`test_subagent_api_tenant_filter.py` 共 59 passed（重写 `_load_extra_md` 契约测试，覆盖实例行权威/空值即清空/查询异常回退三条路径）；运行时验收 `tests/integration/test_agent_runtime_acceptance.py` 26 passed。

## 教训与残余风险

- **迁移块禁止原地改写**：改内容必须换新 datetime，否则水位已过的库永久漏跑（本次与 `runtime-registration-migration-watermark-incident.md` 同类，已是第二次）。
- 存量库补迁移前必须核对**目标库水位**，而非只看新块时间是否更新。
- 读侧口径要一致：弹框只读实例表、运行时读「实例 -> registry」，两者差异会把数据错键隐藏成「只有 UI 不对」。后续新增读侧入口按同一语义收口。
- 生产环境仍需下次部署时由启动迁移自动应用补偿块与运行时改动；部署后应抽查一个租户的定制弹框回显。
