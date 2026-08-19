# 设计：工具自动发现注册（消除 agent.py 写死的注册清单）

> 2026-08-19。#65。现状：`Agent._register_builtin_tools()` 约 40 行手工 `register(XxxTool())`，每加一个工具都要改 agent.py（启动链路文件，风险最高频次最多的改动点）。
> 用户诉求：新工具零注册；按智能体配置决定可用工具。

## 现状事实（已核实）

1. 写死清单：agent.py `_register_builtin_tools()`（约 423-542 行）逐个 import + register。
2. **按配置过滤已存在**：`_filter_tools_by_config()` 按子智能体 `tools.allowed/excluded/inherit` 过滤；`_register_local_proxy_tools()` 已按 allowed 列表注册 boss 代理工具。本设计不改这两处。
3. 三类特殊工具**不该进自动注册**：
   - 构造需参数/自引用：CreateScheduledTaskTool/ManageScheduledTaskTool（存 self 供后台 runner 用）
   - 虚拟工具（设计如此，不进 registry）：CreatePlanTool/UseSkillTool/SkillExecuteTool/ClarifyTool/DelegateToSubagentTool
   - 有意不注册：SpeechToTextTool（渠道层处理）、LOCAL_PROXY_TOOL_CLASSES（仅子智能体按需）
4. role 权限（user.py has_permission）不做工具级过滤，本次不改。

## 设计

### 1. BaseTool 自动目录化
`src/tools/base.py`：`BaseTool` 增加 `catalog: bool = True` 类属性 + `__init_subclass__`，子类定义即登记进模块级 `_CATALOG: Dict[str, Type[BaseTool]]`（按 name 去重，子类覆盖）。`catalog = False` 显式退出（用于上述三类特殊工具与测试替身）。**不实例化**——只收集类，避免 import 副作用。

### 2. 包遍历发现器
`src/tools/registry.py` 新增 `discover_tool_classes() -> Dict[str, Type[BaseTool]]`：
- `pkgutil.iter_modules(src.tools.__path__)` 逐包 import（目的是触发 `__init_subclass__`）
- 单包 import 失败分两级（CR 修订，2026-08-19）：
  - 第三方可选依赖缺失（`ModuleNotFoundError` 且缺失模块非首方 src 包，如 playwright 未装）→ warning 一次并跳过，行为等价于"没注册"
  - 其余任何失败（首方模块导入错误、循环导入、语法错误、import 期异常）→ **原样抛出**。理由：改造前 agent.py 显式 import 下这类失败会让 Agent 启动响亮崩溃；若降级为 warning，重构引入的循环导入会静默蒸发核心工具（用户表现为"AI 突然不会某功能"，排查困难）。区分标准可执行：`e.name`（缺失模块名）非 `src`/`src.*` 即第三方可选依赖
- 返回 `_CATALOG` 快照（按 name 排序，注册顺序确定）
- 校验：cataloged 类必须可无参构造（`inspect.signature` 检查），不可则 discovery 期报错给开发者

### 3. agent.py 收敛
`_register_builtin_tools()` 改为：
```python
for cls in discover_tool_classes().values():
    self.tool_registry.register(cls())
self._register_special_tools()   # 计划/技能/委派/定时任务等，集中到一个清晰方法
```
删除 40 行 import+register。特殊工具加 `catalog = False`。

### 4. 兼容与验收
- **黄金清单测试**：以 HEAD 注册结果为基准生成工具名快照，改造后 `discover+register` 的工具名集合与快照**完全一致**（特殊工具另行断言仍在 self._* 上）。
- 启动链安全：`from src.core.agent import Agent`、`import src.main` 通过；tests/integration/test_agent_loop.py 通过。
- 新增工具的流程从此 = 写类 + 放进 src/tools/ 包（包 `__init__.py` 导出），零 agent.py 改动。

## 不做

- 不改 `_filter_tools_by_config` / 角色权限 / DB 子智能体工具选择逻辑（已满足"按配置注册"语义）。
- 不迁移 local proxy / 虚拟工具的既有模式。
