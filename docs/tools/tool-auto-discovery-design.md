# 工具发现、装配与执行上下文

> 2026-08-19。#65 二期实现。总体设计见
> [plan-agent-registration-decoupling.md](../plans/plan-agent-registration-decoupling.md)。

## 目标架构

工具系统分为四层，`Agent` 只负责生命周期和控制流：

1. `BaseTool` Catalog：进程级工具类候选目录。
2. `ToolRegistry`：每个 Agent 的普通工具实例集合。
3. `assemble_agent_tools()`：普通工具、角色策略、本地代理和控制工具的唯一装配入口。
4. `ToolExecutionContext`：单次调用的不可变身份和关联上下文。

## Catalog

- `catalog=True` 且有 `name` 的类按 `module + qualname` 登记，不能按工具名覆盖。
- discovery 在进程锁内递归 import，并只接纳完整 import 成功的 `src.tools.*` 模块。
- 完成模块过滤后再按工具名分组；不同生产类同名立即报错并列出类路径。
- 可选第三方依赖缺失允许 warning 后跳过；首方缺失、循环 import、语法和其他
  import 异常直接中止启动。
- 结果按工具名排序，并校验每个类可无参构造。
- 当前 26 个普通工具全部来自 Catalog，包含两个无状态 scheduled 工具。

新增普通服务端工具只需新增 `BaseTool` 子类和测试；若构造需要依赖，设置
`catalog=False`，由 Assembly 或控制工具集合显式构造。

## Registry

`ToolRegistry` 对重复名称默认报错，提供 `remove_many()`、`retain_only()`、
`clear()` 和只读 `snapshot()`。调用方不得修改 `_tools`。

## Assembly

`ToolAssemblyRole` 只有 `MASTER`、`SUBAGENT`、`STANDALONE`。装配入口验证
角色依赖组合，然后按固定顺序执行：

1. 发现并实例化 Catalog 工具；
2. 非 MASTER 仅在 allowed 命中轻量 manifest 时延迟加载 local proxy；
3. 应用 inherit/allowed/excluded；
4. 构造 `ToolControlSet`；
5. 返回 Registry、Controls 及发现/最终名称快照。

MASTER 路径不得 import `src.local_tools.proxy_tool`。本地代理名称放在无 repository、
service 依赖的 `src/local_tools/manifest.py`。

## 控制工具

`ToolControlSet` 统一构造 create_plan、use_skill、skill_execute、clarify，并在 MASTER
创建 `SubagentExecutor` 后晚绑定 delegate。它也是控制工具 definitions、guide 和动态
display name 的唯一来源。

delegate 使用双层安全边界：

- schema：`None` 表示全量，非空序列表示白名单，空序列完全隐藏 delegate；
- execute：`DelegationAuthorizer` 在创建 task record 前按本次 tenant 和订阅再次授权。

## 请求级执行上下文

所有生产执行入口通过 `ExecutionContextFactory` 构造 `ToolExecutionContext`，并作为
`ToolExecutor.execute(..., context=...)` 的 keyword-only 参数传入。Executor 使用
ContextVar token scope，异常和嵌套执行后精确恢复外层上下文；`execute_batch()` 原样
向每项传播同一个 context。

email、browser、knowledge、cp、write、scheduled 不保存 tenant/user/session 请求态，
也没有身份 setter/fallback。Remote Gateway 从已验证 binding 构造 context；
`_trusted_*` 只保留在 LOCAL_REQUIRED 跨进程协议中。

## 验收契约

- 26 项工具名、排序、schema、控制工具顺序和模式过滤保持稳定。
- tests/ 替身和失败模块的半注册类不能进入生产快照。
- MASTER 装配后 local proxy 实现未被加载。
- A/B 并发身份隔离、异常 reset、嵌套和 batch 传播通过。
- 空订阅不暴露 delegate，直接越权调用在 executor 前被拒绝。
- 新增工具必须同步更新黄金清单并说明暴露面变化。
