# 记忆系统 Phase 1 开发任务书

> 版本: v1.0 | 创建日期: 2026-04-24 | 状态: 已完成

## 前置调研结论

在开始开发前，通过代码探索确认了以下关键事实，这些发现影响实施策略：

### 1. DialogManager 是死代码

- 全局实例 `dialog_manager` 没有被任何业务代码 import 或调用
- 它提供的槽位管理、意图管理、会话状态等功能均未投入使用
- 它的 Session 模型（`src/models/session.py`）也只被它自身引用
- 因此 **不存在"双实例"问题**，Design Doc 中的 P4 问题实际不成立

### 2. 子智能体的独立 memory 是有意设计

- `SubagentExecutor.delegate()` 创建子智能体时不注入主智能体 memory，这是刻意的隔离
- 子智能体是终端执行者，不支持二级委派，不需要主智能体的完整上下文
- 子智能体通过 `execute_as_subagent(task_description=...)` 以任务描述作为初始输入，用自己的空 memory 走完流程

### 3. 代码中存在脆弱的内部属性访问

- `Agent._compress_skill_context` 直接访问 `self.memory._cache`
- `SkillCompleteTool.set_context` 接收 `memory_cache` 字典引用并直接操作
- `SubagentExecutor._create_task_record` 用 `_cache.keys().__iter__().__next__()` 获取任意 session_id
- 接入 MemoryManager 时必须处理这些依赖

---

## 任务总览

```
Task 1.1 统一配置来源 ─────────┐
                                ├──> Task 1.3 接入 MemoryManager ──> Task 1.4 主动清理
Task 1.2 标注 DialogManager ────┘
```

---

## Task 1.1: 统一配置来源

**问题**: Agent 直接调用 `ShortTermMemory()` 使用硬编码默认值（100条），配置文件设为 10 条但不生效。

**改动点**:

| 文件 | 改动 | 说明 |
|------|------|------|
| `src/config/settings.py` | `ShortTermMemoryConfig.max_messages` 默认值改为 `100` | 与实际运行行为一致，100 条更适合生产环境 |
| `configs/config.yaml` | `max_messages: 10` → `max_messages: 100` | 配置与实际行为对齐 |
| `src/core/agent.py:117` | `ShortTermMemory()` → `ShortTermMemory(max_messages=settings.memory.short_term.max_messages, ttl=settings.memory.short_term.ttl)` | Agent 从配置创建 memory |

**验收标准**:
- [x] Agent 的 ShortTermMemory max_messages 与 config.yaml 一致
- [x] 修改 config.yaml 中的 max_messages 后，重启服务 Agent 行为随之变化

**风险**: 低。改动小且明确。

---

## Task 1.2: 标注 DialogManager 状态

**背景**: 调研确认 DialogManager 是未投入使用的死代码，不存在设计文档中描述的"双实例"问题。

**改动点**:

| 文件 | 改动 | 说明 |
|------|------|------|
| `src/core/dialog_manager.py` | 在模块 docstring 和类 docstring 中标注 `⚠️ 未投入使用` | 避免后续开发者误以为它在生产中起作用 |
| `src/core/__init__.py` | 检查是否需要调整导出 | 确保不误导 |

**验收标准**:
- [x] DialogManager 的文档清晰标注其未投入使用的状态

**风险**: 无。仅修改注释。

---

## Task 1.3: 将 MemoryManager 接入生产流程

**问题**: MemoryManager 已实现但从未被使用，Agent 直接操作 ShortTermMemory。

**这是工作量最大的任务，分为 4 个子步骤：**

### Step 1.3.1: 增强 MemoryManager 接口

MemoryManager 需要暴露与 ShortTermMemory 兼容的完整接口，同时处理内部属性访问的依赖。

**改动点**:

| 文件 | 改动 |
|------|------|
| `src/memory/manager.py` | 1. 暴露 `_cache` 属性（通过 property 或公开方法），供 SkillCompleteTool 使用 |
| | 2. 添加 `add_message(session_id, message: dict)` 方法（接受完整 dict 参数） |
| | 3. 添加 `get_message_count(session_id)` 方法 |
| | 4. 添加 `get_active_sessions()` 方法 |
| | 5. 确保现有 `to_llm_messages` 保持兼容 |

**具体接口设计**:

```python
class MemoryManager:
    @property
    def _cache(self):
        """临时兼容：供 SkillCompleteTool 等需要直接操作缓存的场景"""
        return self._short_term._cache

    def add_message(self, session_id: str, message: Dict[str, Any]) -> None:
        """代理到 ShortTermMemory.add_message"""
        self._short_term.add_message(session_id, message)

    def get_message_count(self, session_id: str) -> int:
        return self._short_term.get_message_count(session_id)

    def get_active_sessions(self) -> List[str]:
        return self._short_term.get_active_sessions()
```

**验收标准**:
- [x] MemoryManager 暴露所有 ShortTermMemory 已有的公开方法
- [x] `_cache` 属性可访问（过渡方案）

---

### Step 1.3.2: Agent 接入 MemoryManager

**改动点**:

| 文件 | 改动 |
|------|------|
| `src/core/agent.py:117` | `self.memory = ShortTermMemory()` → `self.memory = MemoryManager(max_short_term_messages=..., short_term_ttl=...)` |
| `src/core/agent.py:1672` | `len(self.memory._cache.get(session_id, []))` → `self.memory.get_message_count(session_id)` |
| `src/core/agent.py:973-986` | `_compress_skill_context` 适配 MemoryManager |

**注意事项**:
- Agent 中所有 `self.memory.add()`, `self.memory.get_context()`, `self.memory.to_llm_messages()` 调用需要验证兼容性
- `self.memory._cache` 直接访问需替换为 MemoryManager 提供的方法

**验收标准**:
- [x] Agent 通过 MemoryManager 操作短期记忆
- [x] 不再有直接访问 `_cache` 的代码

---

### Step 1.3.3: SkillCompleteTool 适配

**改动点**:

| 文件 | 改动 |
|------|------|
| `src/tools/skill/skill_complete_tool.py` | `set_context` 和 `_compress_skill_context` 适配 MemoryManager |

**当前问题**: `set_context(active_sessions, memory_cache)` 直接接收 `_cache` 字典引用，`_compress_skill_context` 直接替换整个 deque。

**方案**: 保持 `set_context` 接受 `memory_cache` 参数（类型不变），通过 MemoryManager 的 `_cache` property 获取。未来 Phase 可进一步重构为通过 MemoryManager 的公开方法操作。

**验收标准**:
- [x] 技能完成后上下文压缩正常工作
- [x] 技能摘要消息正确生成

---

### Step 1.3.4: 子智能体工厂适配

**改动点**:

| 文件 | 改动 |
|------|------|
| `src/subagents/factory.py:110` | `agent.memory = session_memory` 赋值兼容 MemoryManager 类型 |
| `src/subagents/factory.py:160` | 同上 |
| `src/subagents/executor.py:114-118` | 修复 `_cache.keys().__iter__().__next__()` 脆弱代码 |

**关于 executor 的 task_record 存储**:
- 当前 `_create_task_record` 向 memory 中写入 type=subagent_task_record 的消息，但 `_get_task_record` 从本地 `_task_records` dict 读取，两边不一致
- Phase 1 暂不重构此机制，但需将 `_cache` 访问改为通过 MemoryManager

**验收标准**:
- [x] 子智能体工厂创建的 agent 能正常使用注入的 memory
- [x] 不再有 `_cache.keys().__iter__().__next__()` 这类脆弱代码

---

## Task 1.4: 增加主动清理机制

**问题**: TTL 过期仅依赖惰性清理（get_context 时检查），无活跃请求的会话永远不会被清理。

**改动点**:

| 文件 | 改动 |
|------|------|
| `src/memory/manager.py` | 确认 `cleanup_expired()` 方法已实现且逻辑正确 |
| `src/main.py` | 在 FastAPI startup 事件中启动后台 asyncio 定时清理任务 |
| `src/config/settings.py` | 在 `MemoryConfig` 中添加 `cleanup_interval: int = 300` |
| `configs/config.yaml` | 添加 `cleanup_interval: 300` |

**后台清理任务设计**:

```python
async def _memory_cleanup_task():
    """后台定时清理过期会话"""
    while True:
        await asyncio.sleep(cleanup_interval)
        try:
            stats = memory_manager.cleanup_expired()
            if stats.get("cleaned_sessions", 0) > 0:
                logger.info(f"清理过期会话: {stats}")
        except Exception as e:
            logger.error(f"清理任务异常: {e}")
```

**验收标准**:
- [x] 过期会话在配置的间隔时间内被主动清理
- [x] MemoryManager.get_stats() 准确反映当前活跃会话数
- [x] 清理异常不影响主服务运行

---

## 文件变更清单

### 新增文件

无

### 修改文件

| 文件 | Task | 改动范围 |
|------|------|----------|
| `src/config/settings.py` | 1.1, 1.4 | 默认值调整 + 新增 cleanup_interval |
| `configs/config.yaml` | 1.1, 1.4 | max_messages 调整 + 新增配置项 |
| `src/core/agent.py` | 1.1, 1.3.2 | memory 创建方式 + 接口调用适配 |
| `src/core/dialog_manager.py` | 1.2 | 文档标注 |
| `src/memory/manager.py` | 1.3.1, 1.4 | 接口增强 + 清理确认 |
| `src/tools/skill/skill_complete_tool.py` | 1.3.3 | 适配 MemoryManager |
| `src/subagents/factory.py` | 1.3.4 | 类型适配 |
| `src/subagents/executor.py` | 1.3.4 | 修复脆弱代码 |
| `src/main.py` | 1.4 | 注册定时清理任务 |

---

## 测试计划

每个 Task 完成后需验证：

| Task | 测试方式 |
|------|----------|
| 1.1 | 修改 config.yaml 中的 max_messages，重启服务，确认 Agent memory 容量随之变化 |
| 1.2 | 代码审查（仅注释变更） |
| 1.3 | 运行现有测试确保无回归；手动测试技能执行和子智能体委派 |
| 1.4 | 观察日志确认定时清理执行；创建会话等待过期后确认被清理 |

---

## 风险与注意事项

1. **SkillCompleteTool 的 _cache 直接操作**: 这是最大的风险点。它直接替换 deque 内容（`self._memory_cache[session_id] = new_deque`），接入 MemoryManager 后需确保通过 `_cache` property 获取的引用仍然有效。Phase 1 用 property 兼容，后续 Phase 重构为公开方法。

2. **全局 Agent 单例**: `agent.py` 末尾创建了全局 `master_agent` 实例，memory 在模块导入时就创建。配置变更需要重启才能生效。

3. **子智能体的 task_record 不一致存储**: executor 的 `_create_task_record` 写 memory，`_get_task_record` 读本地 dict。这是已知的设计问题，Phase 1 只修脆弱的 `_cache` 访问，不重构存储机制。
