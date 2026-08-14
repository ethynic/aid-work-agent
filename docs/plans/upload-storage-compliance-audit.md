# 上传文件存储合规审计报告

> **审计日期**：2026-08-14
> **依据规范**：`.claude/rules/backend_dev.md`「租户附件存储规范」
> **关联改造**：[plan-tenant-storage-migration.md](plan-tenant-storage-migration.md)（Phase 1~8 已完成）
> **审计方法**：全项目扫描上传/保存文件调用点 + 逐点读函数上下文核对路径构造与 tenant_id 来源

---

## 1. 审计范围

| 扫描对象 | 覆盖范围 |
|---------|---------|
| 上传路由 | `/api/upload`、子智能体模板/配置文件、知识库、Excel 导入、头像/Logo、客户端 OCR |
| 渠道媒体落盘 | wecom / dingtalk / feishu / wecom_kf（含 RPA） |
| 工具侧文件生成 | cp_tool / write_tool / 文档转换类工具 |
| 会话附件 | agent 会话 workspace、图片资产注册 |
| 临时文件 | OCR、Excel 解析、skill 工作目录 |

---

## 2. 结论

上传文件存储规范**主体已合规**：绝大多数持久化上传入口均已统一走 `storage.py` 工具函数并带租户场景（conversation / knowledge / templates / data_sources / temp / images / videos），符合规范。

仅发现 **1 处实际违规**（P1），详见下节。

---

## 3. 唯一违规项（P1）

### `tenant_config_file.py` 硬编码拼路径且跳过场景子目录

**位置**：`src/api/tenant_config_file.py:27-34`

```python
def _get_config_path(tenant_id: str, subagent_name: str) -> Path:
    from src.core.storage import normalize_tenant_id
    return PROJECT_ROOT / "storage" / "tenants" / normalize_tenant_id(tenant_id) / f"{subagent_name}-api.md"
```

**问题**：
1. 手工拼接 `storage/tenants/{tid}/{subagent}-api.md`，未使用 `get_tenant_storage_path` / `ensure_tenant_storage_dir`，违反「必须通过统一工具函数」；
2. 配置文件直接落在 `storage/tenants/{tid}/` **根目录**，无场景子目录，违反「禁止跳过场景子目录直接存放文件」。

**背景**：Phase 8 仅在此处补了 `normalize_tenant_id` 剥离前缀（修复带前缀与无前缀目录读写错位），未修复工具函数与场景子目录问题。

---

## 4. 修复建议

改用 `ensure_tenant_storage_dir(tenant_id, "templates")`（子智能体模板场景已存在）或新建 `config` 场景；同时更新 `storage_migration.py` 迁移映射表，把历史已落盘的无场景目录文件纳入迁移。
