# 租户附件存储规范整改 - 存量文件迁移方案

> 关联：[租户附件存储规范](../../.claude/rules/backend_dev.md)（backend_dev.md）、[file_usage.md](../system/file_usage.md)
> 代码整改：2026-09 第二阶段整改（data_analyzer / write_tool / cp_tool / LongTermMemory / wecom_kf_account / 渠道媒体 / lead_manager，共 7 处）
> **执行顺序**：先发布代码（保证新写入合规）-> 再执行本迁移 -> 观察 >= 24h -> 清理旧目录
>
> | 环境 | 时机 | 存储根（容器内 /app/storage） |
> |------|------|------|
> | 测试（aid-agent-api2 / api3） | 代码发布后**立即**执行 | /var/www/qb3_upload/agent2_storage |
> | 生产（aid-agent-api） | 下周择日，低峰期执行 | /var/www/qb3_upload/agent_storage |

## 1. 迁移对象清单

以生产 `agent_storage` 为准（测试 `agent2_storage` 同构，另多一个 `storage/storage` 嵌套目录）：

| # | 源目录 | 体量/状态 | 归属判定 | 目标 | 处理 |
|---|--------|----------|---------|------|------|
| 1 | `memory/tenant_{tid}/`、`memory/{tid}/` | 各 1-2 文件 | 可精确归属 | `tenants/{tid}/memory/` | 代码已自动迁移（LongTermMemory 首次访问 move）；本方案脚本仅做**兜底扫描**，把 24h 内未被访问触发的旧文件也迁走 |
| 2 | `storage/storage/tenants/{tid}/conversation/`（仅测试环境） | 2 租户、3 文件 | 可精确归属（嵌套目录本身就是 tenants 结构） | `tenants/{tid}/conversation/` | cp 合并，重名跳过 |
| 3 | `uploads/tenant_{tid}/knowledge/` | 少量 | 可精确归属 | `tenants/{tid}/knowledge/` | cp + **同步 UPDATE documents.file_path**（见 §4） |
| 4 | `uploads/tenant_{tid}/conversation/`、`templates/`、`user_{uid}/` | 少量 | 可精确归属 | `tenants/{tid}/` 对应场景 | cp 合并，重名跳过 |
| 5 | `analysis_charts/`、`analysis_data/` | 生产 96M，活跃到 9-4 | **无法归属**（文件名不含租户） | 宿主机归档目录 | mv 出 volume，30 天后删除 |
| 6 | `output/` | 生产 76K，7-9 后停写 | 无法归属 | 宿主机归档目录 | mv 出 volume |
| 7 | `uploads/wecom_kf/`、`uploads/wecom/`、`uploads/dingtalk/` 等历史媒体 | 生产 174 文件、11M | 无法归属（内容 hash 命名，可再生） | 宿主机归档目录 | mv 出 volume |
| 8 | `uploads/conversation/`、`uploads/knowledge/`（旧共享目录） | 空或少量 | 无归属 | - | 确认为空后直接删 |
| 9 | `memories/`、`subagents2/` | 生产空目录，代码零引用 | - | - | 直接删 |
| 10 | `analysis_charts` 等目录**本身** | - | - | - | 全部文件迁出后 rmdir |

## 2. 归档目录（宿主机，volume 之外）

```
/var/www/qb3_upload/agent_storage_legacy_YYYYMMDD/     # 生产
/var/www/qb3_upload/agent2_storage_legacy_YYYYMMDD/    # 测试
```

- 归档保留 **30 天**，到期人工确认后删除（`calendar 提醒` 或运维清单）
- 选择 mv 出 volume 而非 volume 内 `_legacy/` 的原因：立即释放容器可见空间，且避免 `strip_legacy_storage_prefix` 等路径逻辑再扫到

## 3. 迁移脚本要点

新建 `scripts/migrate_storage_legacy.py`（幂等、支持 `--dry-run`、宿主机直接跑，无需容器）：

```python
# 核心规则
# 1. 租户 ID 归一化：strip("tenant_") 前缀（复用 normalize_tenant_id 语义）
# 2. 归属类迁移一律"复制合并"：目标存在同名文件则跳过并记日志，绝不覆盖
# 3. 无法归属类迁移一律"移动归档"：mv 到宿主机归档目录（跨设备 mv 自动 copy+unlink）
# 4. 每步打印 计划文件数 / 实际迁移数 / 跳过数，--dry-run 只打印不执行
# 5. 迁移日志写 log/temp/storage_migrate_{env}.log（tlog 规范）
```

执行命令（宿主机）：

```bash
# 测试环境（代码发布后立即）
python scripts/migrate_storage_legacy.py --env test --dry-run
python scripts/migrate_storage_legacy.py --env test

# 生产环境（下周择日，低峰期）
python scripts/migrate_storage_legacy.py --env prod --dry-run
python scripts/migrate_storage_legacy.py --env prod
```

## 4. documents 表路径同步（仅对象 #3）

`uploads/tenant_{tid}/knowledge/` 中的文件部分被 `documents.file_path` 引用（知识库文档）。迁移后需同步更新，**先 SELECT 核对再 UPDATE**：

```sql
-- 1. 核对命中数量与样例
SELECT id, file_path FROM documents
WHERE file_path LIKE 'storage/uploads/tenant_%/%' AND is_deleted = false;

-- 2. 同步前缀（幂等，仅命中未迁移过的行）
UPDATE documents
SET file_path = regexp_replace(file_path, '^storage/uploads/tenant_([^/]+)/', 'storage/tenants/\1/')
WHERE file_path LIKE 'storage/uploads/tenant_%/%'
  AND file_path <> regexp_replace(file_path, '^storage/uploads/tenant_([^/]+)/', 'storage/tenants/\1/');

-- 3. 复核：确认无残留
SELECT count(*) FROM documents WHERE file_path LIKE 'storage/uploads/tenant_%/%';
```

> 注意：`chunks_vec` / 向量库不存磁盘路径，无需处理；Redis `uploaded_file:{file_id}` 元数据 TTL 86400s，**迁移当天不物理删除任何源文件**（对象 #5-#7 是 mv 而非 rm，volume 外归档仍可紧急找回），24h 后链接自然换新。

## 5. 执行 checklist

### 测试环境（代码发布后立即）

- [ ] 代码已发布到 aid-agent-api2 / api3，服务重启无错
- [ ] 冒烟：对话上传文件 -> 落 `tenants/{tid}/conversation/`；数据分析生成图表 -> 落 `tenants/{tid}/report/`
- [ ] `--dry-run` 审查迁移计划
- [ ] 执行迁移（对象 #1-#4 cp 合并；#5-#7 mv 归档；#8-#9 清理）
- [ ] 执行 §4 SQL（先 SELECT 核对）
- [ ] 复查 `agent2_storage/` 顶层只剩 `tenants/ tmp/ uploads/(仅未迁完残渣)`
- [ ] 冒烟：渠道收发图片、知识库检索出图、长期记忆读写

### 生产环境（2026-09-07 已执行）

- [x] 发布 + 确认 5 个容器（api/background/background2/api2/api3）均含新代码，启动无错
- [x] `--dry-run` 审查迁移计划（#5+analysis_charts 归档合计 106M；uploads/ 仅有 wecom_kf 174 文件，无 tenant_* 文件，cp 合并 0）
- [x] 执行迁移（mv 归档 6 项；§4 SQL SELECT 核对命中 0 行，无需 UPDATE）
- [x] 复查顶层仅剩 memory/ tenants/ tmp/；memory 兜底 1 文件（tenant_c148f4efb4dc）已 cp 归位并修正属主 prompter:docker
- [x] 探针验证：LongTermMemory 按新路径读取已迁移记忆成功；迁移后无错误日志、旧目录未复活
- [ ] 冒烟：渠道收发图片、知识库检索出图、对话上传（需真实用户/渠道操作，待人工验证）
- [ ] 30 天后（约 2026-10-07）删除两个 `*_legacy_*` 归档目录（测试环境归档同样处理）
- [ ] 30 天后清理测试环境 `agent2_storage/storage/tenants/` 下的 cp 源文件与生产 `agent_storage/memory/` 兜底源文件

## 6. 回滚预案

- 归属类（#1-#4）：cp 合并、源文件保留在原位 30 天，异常时把 `documents.file_path` UPDATE 反向执行即可（新路径 -> 旧路径）
- 归档类（#5-#7）：mv 回原目录即可（目录名保留在归档路径中，一一对应）
- 代码侧回滚：git revert 本次整改提交；新旧行为互不干扰（旧代码写旧目录，新代码写新目录）

## 7. 明确不做的事

- 不迁移 `tenants/` 内部任何文件（已合规）
- 不为 analysis 历史文件强行归属租户（无依据，宁可归档）
- 不改 `uploads/` 内 `tenant_*` 之外的历史文件归属（媒体文件可再生）
- 不在迁移当天物理删除任何源文件
