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
2. MASTER及inherit角色按受信目录注册业务proxy，显式配置仍按allowed注册兼容实现；
3. 应用 inherit/allowed/excluded；
4. 构造 `ToolControlSet`；
5. 返回 Registry、Controls 及发现/最终名称快照。

2026-10-29通用装配修订（[W14计划](../plans/plan-wecom-cli-group-chat-experiment.md#w14-通用runtime业务工具自动装配2026-10-29)）：取代旧“MASTER不得加载proxy”和W13企微专门注册。现有`TRUSTED_PROVIDERS`的`agent_tools`可收敛业务面，`internal_tools`排除恢复原语，`device_independent_tools`保留混合provider内的纯云端实现；缺省业务面为受信工具，不生成不存在的工具实现。兼容读取别名同处登记，allowed/excluded按映射后使用。

运行时在每次执行/恢复激活前，按可信actor的已选active设备上报provider投影已注册schema与usage guide，不新增实时探测或online/锁屏/繁忙前置门禁。未配对/未安装不会凭空创造工具，临时能力查询失败保留已知定义并明确状态未知；实际派发仍由原设备/插件错误说明恢复原因。安装变化在下一激活更新。旧调用恢复不依赖当前可见schema，不因换设备或短暂离线丢掉冻结执行器；配置指纹不纳入动态能力。已有域设备绑定由执行适配器传入provider→device覆盖，只作用于原域，不扩义为全局绑定。无actor的metadata实例不查询DB、不复用其他用户能力。

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
- MASTER按目录装配业务候选，模型仅见当前actor设备能力范围；内部恢复不进模型，旧调用执行器仍可恢复。
- A/B 并发身份隔离、异常 reset、嵌套和 batch 传播通过。
- 空订阅不暴露 delegate，直接越权调用在 executor 前被拒绝。
- 新增工具必须同步更新黄金清单并说明暴露面变化。


### 服务端独立升级的既有配置兼容（2026-10-29）

设备发现只决定自动新增业务工具，不删除已有显式allowed工具的模型可见性；两者按现有registry（已应用excluded）取并集，再排除内部原语。旧根provider_id与既有providers/manifests解析继续复用，不依赖新客户端。真实调用仍按原执行权限与设备实际状态处理，工具可见性不是新执行门禁。含纯云端分支的旧BOSS工具不应因缺少设备元数据而失去原可见性。测试/修复边界见[W14.5](../plans/plan-wecom-cli-group-chat-experiment.md#w145-服务端单独升级的旧端兼容复核2026-10-29)。
