# 租户级 Skills 设计文档

> 创建日期: 2026-05-07 | 状态: 已实现
> 关联: [ideas.md - 租户级 Skills](ideas.md)

## Context

当前系统已有租户 skill 的存储层（`SkillResolver`）和 API（`/api/saas/skills`），也已有 `SkillRegistry.load_from_directories()` 多目录加载方法。但 Agent 初始化时只从 `src/skills/` 加载基础 skills，**没有将租户目录的 skills 加载进来**。这导致租户通过 API 上传的自定义 skills 虽然保存到了磁盘，但 Agent 运行时完全感知不到。

本设计的目标是将这条断开的链路接通：让 Agent 在处理租户请求时自动加载该租户的自定义 skills。

---

## 1. 方案概述

**按需加载（Lazy Load）**：Agent 在首次处理某租户的请求时，从 `tenants/{tenant_id}/skills/` 加载租户 skills 并合并到 SkillRegistry 中，之后缓存避免重复扫描。

选择此方案的原因：
- Agent 单例在服务启动时创建，此时无租户上下文
- 租户数量和 skills 数量不确定，启动时预加载不可行
- 按需加载只在需要时付出开销

---

## 2. 数据流

```
用户请求 → TenantContextMiddleware 设置 tenant_id ContextVar
  → Agent.process_message()
    → _ensure_tenant_skills_loaded()
      → TenantSkillCache.get_or_load(tenant_id)
        ├─ 缓存命中 → 直接返回已合并的 skills
        └─ 缓存未命中 → 加载基础 skills + 租户 skills（tenants/{tenant_id}/skills/），合并，缓存
      → 将合并后的 skills 应用到当前 agent 的 SkillRegistry
    → _build_system_prompt()（此时 SkillRegistry 已包含租户 skills）
    → 正常处理消息
```

---

## 3. 文件变更

### 3.1 新增：`src/saas/services/tenant_skill_cache.py`

TenantSkillCache 类，管理按租户合并的 skills 内存缓存。

```python
class TenantSkillCache:
    def __init__(self):
        self._cache: Dict[str, Tuple[Dict[str, Skill], float]] = {}
        self._ttl_seconds: int = 300  # 5 分钟缓存

    def get_or_load(self, tenant_id, base_skills_dir, allowed=None) -> Dict[str, Skill]:
        """返回缓存的租户 skills，或从磁盘加载并合并"""

    def invalidate(self, tenant_id: str) -> None:
        """清除某租户的全部缓存"""

    def invalidate_skill(self, tenant_id: str, skill_name: str) -> None:
        """清除某租户的单个 skill 缓存"""
```

**加载逻辑**：
1. 从 `src/skills/` 加载基础 skills
2. 从 `tenants/{tenant_id}/skills/` 加载租户 skills
3. 租户同名 skill 覆盖基础 skill（高优先级）
4. 应用 allowed 过滤
5. 存入缓存（带时间戳）
6. 加载失败时 fallback 到仅基础 skills

**全局单例**：`tenant_skill_cache = TenantSkillCache()`

### 3.2 修改：`src/core/skill_registry.py`

**问题**：当前 `_loader` 只保存最后一个 loader，导致多目录加载后 `get_content()` 和 `match_by_file()` 无法正确解析来自早期目录的 skill 内容。

**改动**：

1. 添加 `_loaders: Dict[str, SkillLoader]` 字段，记录每个 skill 对应的源 loader
2. 修复 `from typing` 导入，添加 `Set`
3. `load_from_directories()` 中填充 `_loaders`
4. `get_content()` 优先从 `_loaders` 查找 loader，fallback 到 `_loader`
5. `match_by_file()` 遍历所有 `_loaders` 中的 loader 查找匹配

### 3.3 修改：`src/core/agent.py`

**改动**：

1. Agent.__init__ 末尾添加跟踪字段：
   ```python
   self._loaded_tenant_id: Optional[str] = None
   self._skills_loaded_at: float = 0.0
   ```

2. 新增 `_ensure_tenant_skills_loaded()` 方法：
   - 检查 `settings.saas.enabled`，非 SaaS 模式直接返回
   - 从 ContextVar 获取 tenant_id
   - 检查是否已为该租户加载过且缓存仍新鲜
   - 调用 `TenantSkillCache.get_or_load()` 获取合并后的 skills
   - 重建 `_loaders` 映射（基础 loader + 租户 loader）
   - 应用到 `self.skill_registry._skills` 和 `_loaders`

3. 在 `process_message()` 方法开头插入调用（logger.info 之后、history 加载之前）

4. 在 `execute_as_subagent()` 方法开头插入同样的调用

### 3.4 修改：`src/saas/api/tenant_skills.py`

在 CRUD 操作成功后调用缓存失效：

| 操作 | 失效方式 |
|------|---------|
| upload_skill (POST) | `invalidate_skill(tenant_id, skill_name)` |
| update_skill (PUT) | `invalidate_skill(tenant_id, skill_name)` |
| delete_skill (DELETE) | `invalidate(tenant_id)` — 全量失效 |

---

## 4. 涉及文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/saas/services/tenant_skill_cache.py` | 新增 | 租户 skills 缓存管理 |
| `src/core/skill_registry.py` | 修改 | 修复多 loader 支持，添加 `_loaders` 字段 |
| `src/core/agent.py` | 修改 | 添加按需加载方法和调用点 |
| `src/main.py` | 修改 | 注入 tenant_id 到 agent |
| `src/saas/api/tenant_skills.py` | 修改 | CRUD 后缓存失效 |
| `configs/config.yaml` | 修改 | `tenant_skills_dir` 路径更新 |
| `src/config/settings.py` | 修改 | `tenant_skills_dir` 默认值更新 |

**不变更的文件**：
- `src/saas/services/skill_resolver.py` — 已提供 `get_tenant_skills_dir()`，无需修改
- `src/core/skill_loader.py` — 已支持多目录加载，无需修改

---

## 5. 边界情况处理

| 场景 | 处理 |
|------|------|
| 非 SaaS 模式 | `_ensure_tenant_skills_loaded()` 首行检查 `settings.saas.enabled`，立即返回 |
| tenant_id 为 None（CLI/后台任务） | 跳过加载，仅使用基础 skills |
| 租户 skills 目录为空或不存在 | 正常加载基础 skills，租户目录不存在时跳过 |
| 租户 skill 与基础 skill 同名 | 租户版本覆盖基础版本（通过 dict.update 顺序实现） |
| 磁盘 I/O 失败 | try/except 包裹加载逻辑，fallback 到基础 skills + 记录错误日志 |
| 多 Worker 进程 | 各 Worker 维护独立的内存缓存，TTL 5 分钟后自动过期刷新 |
| Agent 切换租户（理论上不应发生） | 通过 `_loaded_tenant_id` 检测，切换时重新加载 |

---

## 6. 验证方式

1. **非 SaaS 模式回归**：启动服务（SAAS_ENABLED=false），确认 Agent 正常工作，只加载 `src/skills/` 中的 skills
2. **SaaS 模式基本功能**：
   - 启用 SaaS，通过 API 上传一个租户 skill
   - 用该租户身份发送消息，验证 Agent 能识别并使用该 skill
3. **同名覆盖**：上传一个与基础 skill 同名的租户 skill，验证租户版本被使用
4. **CRUD 缓存刷新**：上传 skill 后立即更新内容，发送消息验证获取到的是新内容
5. **日志确认**：观察首次请求时输出 "Loading tenant skills for tenant_xxx" 日志，后续请求不再输出（缓存命中）
