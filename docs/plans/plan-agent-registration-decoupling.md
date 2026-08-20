# 总体设计与开发计划：工具装配从 Agent 解耦

> 分支：`feat/email-process-and-tool-discovery`（worktree）
> 开发基线：`216dfacf`（提交前已再次合并 `origin/master@20668f81`）；原功能基线 `d0e267a7` 已完成普通工具自动发现注册（#65）和邮件工具三合一（#64）
> 状态：2026-08-19 已完成开发、独立测试、独立 CodeReview 与主控终检
> 核心目标：`src/core/agent.py` 不再 import、构造、注册或按名称注入任何具体普通工具；新增工具和新增工具身份需求不再修改 Agent 启动链。

---

## 一、结论与设计决策

保留分支已经完成的“普通工具自动发现”，但不在原方案上直接叠加。整体改为四层：

1. **Catalog（类目录）**：进程级，只保存工具类，负责发现和静态校验。
2. **Registry（实例注册表）**：每个 Agent 一份，只保存该 Agent 最终可用的普通工具实例。
3. **Assembly（装配器）**：唯一负责 Catalog → 实例化 → 条件工具 → 配置过滤 → 控制工具构造；Agent 只接收装配结果。
4. **Execution Context（执行上下文）**：请求级不可变上下文，负责 user/tenant/session；不得写入共享工具实例。

关键决策：

- 普通无参工具继续使用 `BaseTool.catalog=True` 自动发现。
- 有构造依赖的控制工具由 `ToolControlSet` 统一构造，不混入普通 Registry。
- `boss_*` 条件注册和子智能体 allowed/excluded/inherit 过滤移出 Agent。
- **取消原方案的 `supports_*_identity + registry.apply_*`**：它仍把请求身份写入共享实例，会在主 Agent 并发时串用户/租户。
- 定时任务工具先去除 `set_context` 共享状态，再转 `catalog=True`。
- delegate 采用“schema 可见性过滤 + execute 执行期授权”双层校验；`None` 与空列表语义严格区分。
- 所有工具执行入口通过唯一的 `ExecutionContextFactory` 构造上下文，禁止依赖不明确的 ambient ContextVar。
- 本期解耦发现、装配、元数据和请求上下文；不借机重写整个 Agent 状态机。

---

## 二、目标边界

### 2.1 必须达到

| 场景 | 完成后的改动位置 | 是否改 `agent.py` |
|------|------------------|---------------------|
| 新增普通、无参、服务端工具 | 新工具类 + 测试 | 否 |
| 普通工具需要 user/tenant/session | 工具读取执行上下文 + 测试 | 否 |
| 新增按配置启用的本地代理 | `src/local_tools/` 自身目录/清单 + 测试 | 否 |
| 调整子智能体 allowed/excluded | 配置或 DB | 否 |
| 调整显示名/guide/schema | 工具类或控制工具集合 | 否 |
| 新增全新的 Agent 控制流语义 | 控制工具 + dispatcher | 是，必须显式评审 |

### 2.2 明确不做

- 不改变租户订阅数据模型和角色权限模型；但修复现有 delegate 空订阅回退全量、执行期未授权校验的问题。
- 不改变 `LOCAL_REQUIRED` 跨进程可信参数协议。
- 不激活 excel/pdf/word/x_to_image 未验证的身份 setter。
- 不把 `src/core/executor.py` 的旧 respond/clarify 注册表并入生产 Agent；它不在当前生产注册链中，后续单独做死代码审计。
- 不自动提交或 push；实现仍按三智能体流程执行。

### 2.3 “零改 agent.py”的准确含义

普通工具是“schema + execute”，必须做到零改 Agent。控制工具会创建计划、修改消息、澄清或委派，它们改变 Agent 状态机；新增全新控制语义时修改 dispatcher 是合理的，不能为了零修改口号把状态机藏进 import 副作用。

---

## 三、master 与当前分支基线

### 3.1 已完成部分

`master` 在 `Agent._register_builtin_tools()` 中逐个 import/register。当前分支已改为：

```python
for cls in discover_tool_classes().values():
    self.tool_registry.register(cls())
self._register_special_tools()
```

当前完整 Registry 黄金清单为 26 个：24 个 Catalog 自动发现，2 个 scheduled 手工注册。普通工具静态清单已从 Agent 删除，这部分保留。

### 3.2 剩余耦合

| 类型 | 当前位置 | 问题 |
|------|----------|------|
| 控制工具构造 | `_register_special_tools` | Agent 知道具体类和依赖 |
| scheduled 注册 | `_register_special_tools` | 普通 Registry 工具仍手工注册 |
| local proxy 注册 | `_register_local_proxy_tools` | 条件注册策略仍在 Agent |
| 工具过滤 | `_filter_tools_by_config` | Agent 直接修改 `registry._tools` |
| 控制工具元数据 | `_get_tools/_get_tool_display_name/_collect_tool_usage_guides` | 同一集合重复维护 |
| 身份注入 | 主循环、子循环、SubagentExecutor | 工具名硬编码且共享实例可变 |
| scheduled 上下文 | `set_context(user/session)` | await 前后读取共享字段，有竞态 |

### 3.3 行为不变量

- MASTER、SUBAGENT、STANDALONE 的最终普通工具集合不变。
- inherit/allowed/excluded 的语义和顺序不变。
- `boss_*` 只在非 MASTER 且明确 allowed 时注册；inherit 不自动获得本地代理。
- LLM schema、动态 skill enum、租户过滤后的 subagent enum、guide、显示名不变。
- 五个控制工具仍走原有控制流。
- scheduled 保留现有专用进度和结果处理。
- 首方 import 错误响亮失败；只有可选第三方依赖缺失允许降级。

允许且必须验证的安全行为修正只有三类：

1. `available_subagents=None` 表示非 SaaS/明确不限制；`available_subagents=[]` 表示无可用子智能体，不能生成 delegate schema。
2. delegate 即使被直接构造调用，也必须在执行期拒绝未订阅的子智能体。
3. 无身份请求不再继承共享工具实例中曾经写入的 user/tenant/session。

---

## 四、目标架构

```text
BaseTool subclasses
       │ discover
       ▼
Tool Catalog（进程级类目录，只读快照）
       │ instantiate
       ▼
AgentToolAssembler ── local proxy ── subagent policy
       │
       ├── ToolRegistry（每 Agent 普通工具）
       └── ToolControlSet（每 Agent 控制工具/动态元数据）
                         │ late bind delegate
                         ▼
                    Agent 只消费 bundle

每次执行：Agent/Remote Gateway → ToolExecutor(context=...) → ContextVar scope
                                                        │
                                                        ▼
                                             工具只读执行上下文
```

### 4.1 Catalog：发现类，不负责实例和权限

保留 `BaseTool.catalog` 和 `discover_tool_classes()`，但固定以下实现算法，不能只写成原则：

1. `_CATALOG` 按稳定的 class identity（`module + qualname`）保存候选类，不以 `tool.name` 为键；同一 identity 重复登记幂等。
2. discovery 使用进程内锁串行执行“遍历 import → 构造成功模块集合 → 生成快照”，避免并发发现观察到半完成状态。
3. `_walk_and_import()` 显式返回 `successful_modules`。模块只有完整 import 成功才进入集合；中途失败模块即使已触发类定义，其候选类也不能进入本次快照。
4. 候选类先按 `cls.__module__ in successful_modules` 且属于 `src.tools.*` 过滤，再按 `tool.name` 分组。
5. 同组只有一个 class identity 时接纳；两个不同生产类同名时启动失败，并列出全部类路径，禁止后定义覆盖。
6. 测试模块、local proxy 等非 `src.tools.*` 同名类不会参与生产判重，更不能覆盖生产类。
7. 结果按工具名排序，保证多 worker 和不同文件系统下稳定。
8. 无参构造校验保留；失败提示引导使用 `catalog=False` + Assembly。
9. 26 项黄金清单继续作为暴露面契约；新增工具必须更新清单并说明。

为避免测试之间污染，测试临时类必须按 class identity 清理；不得再用 `_CATALOG.pop(tool_name)` 作为清理协议。

Catalog 不实例化工具，不读取租户配置，不 import Agent，不建立外部连接。

### 4.2 Registry：每 Agent 的最终普通工具视图

`ToolRegistry` 增加公共集合 API，装配器不再访问 `_tools`：

```python
def remove_many(self, names: Iterable[str]) -> None: ...
def retain_only(self, names: Collection[str]) -> None: ...
def clear(self) -> None: ...
def snapshot(self) -> Mapping[str, BaseTool]: ...
```

`register()` 对重复名称默认抛错；未来若确需替换，必须调用语义明确的 `replace()`。

### 4.3 Assembly：唯一装配入口

新增 `src/tools/assembly.py`：

```python
class ToolAssemblyRole(str, Enum):
    MASTER = "master"
    SUBAGENT = "subagent"
    STANDALONE = "standalone"

@dataclass(frozen=True)
class ToolAssemblyRequest:
    role: ToolAssemblyRole
    subagent_config: Optional[SubagentConfig]
    plan_manager: PlanManager
    skill_registry: SkillRegistry
    skill_executor: SkillExecutor
    subagent_registry: Optional[SubagentRegistry]

@dataclass
class AgentToolBundle:
    registry: ToolRegistry
    controls: ToolControlSet
    discovered_names: tuple[str, ...]
    final_names: tuple[str, ...]

def assemble_agent_tools(request: ToolAssemblyRequest) -> AgentToolBundle: ...
```

`ToolAssemblyRole` 定义在工具层中立模块，不能从 Agent 反向 import。Agent 完成现有 `is_master/mode` 兼容归一化后才能构造 request。Assembly 入口做运行时校验：

- MASTER 必须提供 `subagent_registry`，且允许后续 bind delegate。
- SUBAGENT 必须提供 `subagent_config`，禁止 bind delegate。
- STANDALONE 按现有兼容规则接收 config，但禁止 bind delegate。
- 非法 role 或依赖组合立即抛出包含 role/缺失字段的启动错误。

固定装配顺序：

1. 发现并实例化 catalog 普通工具。
2. scheduled 无状态化前，兼容性手工加入两个 scheduled tools。
3. 非 MASTER 按现有规则加入明确 allowed 的 local proxy。
4. 非 MASTER 应用 inherit/allowed/excluded。
5. 构造 `ToolControlSet`。
6. 校验最终集合、重复名和模式不变量，返回 bundle。

`assembly.py` 顶层只能 import dataclass、协议、Registry 等轻量模块，禁止 import `Agent/AgentMode` 及任何具体工具实现。具体导入遵守：

- scheduled 兼容工具只在确需手工注册的分支内延迟 import。
- local proxy 只在非 MASTER 且 allowed 与本地代理名有交集时延迟 import；MASTER 启动不得加载 `src.local_tools.proxy_tool`。
- 控制工具由 `control_set.py` 在构造路径内延迟 import。
- 如果 local proxy 清单本身无法轻量读取，应拆出不依赖 repository/service 的 manifest，Assembly 只先读取 manifest，命中后才加载实现。

这样既避免循环依赖，也保持当前 MASTER 不加载 recruiting/local runtime 服务的启动依赖面。

### 4.4 Agent 初始化形态

最终删除 Agent 中四个方法：

- `_register_builtin_tools`
- `_register_special_tools`
- `_register_local_proxy_tools`
- `_filter_tools_by_config`

Agent 只做生命周期编排：

```python
self._tool_bundle = assemble_agent_tools(ToolAssemblyRequest(...))
self.tool_registry = self._tool_bundle.registry
self.tool_executor = ToolExecutor(self.tool_registry)

# MASTER 创建 SubagentExecutor 后晚绑定
self._tool_bundle.controls.bind_delegate(
    subagent_registry=self.subagent_registry,
    subagent_executor=self.subagent_executor,
)
```

`delegate` 在所有模式初始恒为 None，非 MASTER 不会再出现未定义属性。

---

## 五、控制工具设计

新增 `src/tools/control_set.py`：

```python
class ToolControlSet:
    def __init__(self, dependencies: ControlToolDependencies): ...
    def bind_delegate(self, *, subagent_registry, subagent_executor) -> None: ...
    def get(self, name: str) -> Optional[BaseTool]: ...
    def definitions(
        self, *, available_subagents: Optional[Sequence[str]]
    ) -> list[dict]: ...
    def usage_guides(self, *, subagent_descriptions: str = "") -> str: ...
    def get_display_name(self, name: str, args: dict) -> Optional[str]: ...
```

统一负责：

- create_plan/use_skill/skill_execute/clarify 的构造。
- skill 子进程 LLM 环境计算。
- use_skill 动态 skill schema。
- MASTER 动态 delegate schema，继续接收租户过滤后的 available_subagents。
- Assembly 通过 `ControlToolDependencies` 注入无状态 `DelegationAuthorizer`，bind delegate 时传给工具；Agent 不持有具体授权实现。
- 控制工具 guide 和动态显示名。

Agent 元数据收敛为普通 Registry definitions + controls definitions。主/子 loop 暂时只做实例引用机械替换，不改变控制分支顺序和结果结构。

以后若要完全插件化控制流，应另行设计 `ControlOutcome` 状态机，不混入本次注册重构。

### 5.1 delegate 可见性与执行期授权

schema 是模型提示，不是权限边界。delegate 必须实施双层控制：

1. **定义期可见性**：`definitions(available_subagents=...)` 严格区分：
   - `None`：非 SaaS 或调用方明确允许全量，生成全部可用 delegate enum。
   - 非空序列：只生成指定子智能体 enum。
   - 空序列：完全不生成 `delegate_to_subagent` schema。
2. **执行期授权**：`DelegateToSubagentTool.execute()` 在查到 config 后、创建 task record 前，调用无状态 `DelegationAuthorizer.authorize(context, subagent_name)`；未授权直接返回稳定的 permission denied 结果，不能进入 `SubagentExecutor.delegate()`。

`DelegationAuthorizer` 从本次 `ToolExecutionContext.tenant_id` 和当前订阅数据解析权限；演示/非 SaaS规则显式配置。它不把 tenant、available list 或授权结果写入 `ToolControlSet/DelegateToSubagentTool` 实例。需要缓存时，缓存键必须包含 tenant_id 和订阅版本/失效机制，不能使用单个共享字段。

重新委派、直接内部调用和模型 function call 必须走同一个 authorizer，禁止只在 `_get_tools()` 过滤。现有 `SubagentRegistry.get_delegation_tool_definition()` 同步修复 truthiness 判断，使用 `is None` 区分无限制与空集合。

---

## 六、请求级工具执行上下文

### 6.1 为什么不用 capability flag + setter

主 Agent 是进程内单例，Registry 工具实例被不同 session 并发复用：

- A 注入后 B 覆盖，A 随后的工具调用可能读取 B 身份。
- 无身份请求不调用 setter，可能继承上次身份。

身份是执行态，不是注册能力，因此 Registry 不提供 `apply_user_identity/apply_tenant_identity`。

### 6.2 统一上下文

在现有 `src/tools/_helpers.py` ContextVar 基础上演进，或拆为 `src/tools/context.py` 并兼容导出：

```python
@dataclass(frozen=True)
class ToolExecutionContext:
    tenant_id: Optional[str]
    user_id: Optional[str]
    session_id: Optional[str]
    channel: Optional[str] = None
    subagent_id: Optional[str] = None
    chat_record_id: Optional[int] = None
    agent_execution_id: Optional[str] = None
    tool_call_id: Optional[str] = None

@contextmanager
def tool_execution_scope(context: ToolExecutionContext):
    token = _current_tool_context.set(context)
    try:
        yield
    finally:
        _current_tool_context.reset(token)
```

`ToolExecutor.execute()` 增加 keyword-only `context`，在调用工具前进入 scope，finally 用 token 恢复。`execute_batch()` 同样增加 `context` 并逐项原样转交；嵌套执行必须显式传入当前上下文或由调用方明确派生子上下文。这样 asyncio task 隔离，嵌套执行也不会被粗暴清空。

### 6.3 唯一上下文构造入口

新增无状态 `ExecutionContextFactory`。它只在可信边界解析身份，工具内部不得自行猜测身份来源：

```python
class ExecutionContextFactory:
    @staticmethod
    def for_agent_call(*, tenant_id, user_id, session_id, channel,
                       subagent_id, chat_record_id,
                       agent_execution_id, tool_call_id) -> ToolExecutionContext: ...

    @staticmethod
    def for_remote_gateway(*, authenticated_tenant_id, authenticated_user_id,
                           correlation) -> ToolExecutionContext: ...
```

Agent 边界解析优先级为“构造/调用显式可信值 → SaaS middleware ContextVar → None”，解析完成后生成不可变 context；工具执行期间不再回读 Agent 实例字段。Remote Gateway 必须用鉴权和 ticket binding 已确认的 tenant/user 构造 context，不能仅把它们塞进 kwargs。

| 执行入口 | Context 来源与要求 |
|----------|-------------------|
| MASTER 普通工具 | 本次请求解析出的 tenant/user/session + tool call correlation；每次 `ToolExecutor.execute` 显式传入 |
| SUBAGENT 普通工具 | Agent 构造时保存的可信 tenant/user + parent/session/execution；在子智能体执行入口统一构造 |
| scheduled control | 当前交互请求的 context；后台 runner 进入 master Agent 后重新按任务 owner/session 构造，禁止复用旧 ambient context |
| LOCAL_REQUIRED | 云端仍构造 context；跨进程协议另传签名保护的 `_trusted_*`，本机边界重新验证/构造本机 context |
| Desktop Remote Gateway | 从已校验 ticket/binding 构造 context，同时保留 `_trusted_*` 仅供现有跨边界兼容 |
| `execute_batch()` | 一个批次必须显式传同一 context；若未来允许混合身份，API 改为每项携带 context，禁止隐式复用 |
| 嵌套工具执行 | 调用方显式传当前 context 或基于它 `derive()`；token reset 后恢复外层 |

传播规则写入代码注释和测试：`asyncio.create_task`、`asyncio.to_thread` 会复制当前 Context；普通新线程、手工 `run_in_executor` 和子进程不保证自动传播，必须显式传递并在目标边界重新建立 scope。任何生产入口漏传 context 都视为测试失败；无上下文只允许明确的单元测试/系统调试，工具必须安全拒绝身份相关操作。

普通服务端工具只读 `get_tool_execution_context()`。不得同时从 kwargs、SaaS ContextVar 和实例字段随机 fallback；跨进程 `_trusted_*` 只在边界适配器中转换为 context。

### 6.4 原子迁移矩阵

| 工具 | 改造 |
|------|------|
| email_process | 显式 `user_email` 测试构造参数优先；生产只读 context user_id；同阶段删除身份 setter/fallback |
| browser_automation | Remote/LOCAL 边界先把 trusted args 转为 context；生产只读 context；同阶段删除实例身份字段 |
| 三个 knowledge search | 生产只读 context tenant_id；同阶段删除身份 setter/fallback |
| cp | 下载目录和成果登记读取同一 context 快照 |
| write | 字段当前未消费，直接删除 setter/字段及 Agent 注入，不人为激活 |
| scheduled | 从 context 读 user/session，任何 await 前复制到局部变量 |
| excel/pdf/word/x_to_image | 本期不激活，后续专项审计 |

每个工具按“读取 context → 所有生产入口接线 → 单元/并发测试 → 删除该工具身份 setter、实例字段和 Agent 调用点”在同一个可审查阶段完成。不得把有安全意义的 setter fallback 留到 Phase 5。测试注入使用构造参数或专用 credential/provider fake，不使用请求身份 setter。

---

## 七、scheduled 转 Catalog 的安全顺序

1. 两个 scheduled tool 改为读取 `ToolExecutionContext`。
2. 删除 `set_context` 及 `_user/_session_id/_send_progress` 请求态字段。
3. 并发测试证明两个 session 交错 dry-run 不串 user/session。
4. Agent 拦截分支从 Registry 获取，并通过 `ToolExecutor.execute(context=...)` 执行。
5. 保持现有 tool_start/tool_result/成功进度文案。
6. 最后设置 `catalog=True`，删除 Assembly 的兼容手工注册。
7. 完整黄金清单仍为 26，只把自动发现来源从 24 变为 26。

---

## 八、分阶段实施计划

各阶段串行执行、独立测试和评审；失败只回滚本阶段。

### Phase 0A：冻结行为和安全基线

- 固化 `d0e267a7` 的工具数组顺序、标准化 schema、enum 顺序、完整 guide 文本和模式过滤结果。
- 新增但暂不改变预期的安全回归：空订阅不暴露 delegate、直接调用未订阅子智能体被拒绝、A/B 租户授权隔离。
- 固化 MASTER/SUBAGENT/Remote Gateway/execute_batch 当前入口清单，作为 Context 接线检查表。

本阶段只新增基线/红灯测试，不改生产行为。

### Phase 0B：先建立执行上下文和 delegate 安全边界

- 新增 `ToolExecutionContext`、`ExecutionContextFactory`、token scope。
- MASTER、SUBAGENT、Remote Gateway、execute_batch 和嵌套执行全部接线。
- 按工具原子迁移 email/browser/knowledge/cp/write；每个工具同阶段删除身份 setter/fallback。
- delegate 增加执行期 `DelegationAuthorizer`，修复 `None`/空列表语义。
- local/desktop 跨进程协议保持兼容，但在可信边界转换为 context。

验收：空订阅、直接越权、并发身份、缺身份、嵌套 scope、异常 reset、`to_thread` 和显式跨线程/进程传播全部通过。Phase 0B 完成后才允许开始装配重构。

### Phase 1：补 Catalog 防线

- `_CATALOG` 改为 class identity 候选集合。
- 增加 discovery 锁、successful_modules 事务过滤、过滤后按 name 判重。
- 修复测试替身碰撞和失败模块半注册。
- 增加重复名、并发发现和稳定排序测试。

本阶段不改变最终 Registry 集合和 Agent loop。

### Phase 2：抽出 Assembly，保留属性兼容

- 新增 Assembly 和 `AgentToolBundle`。
- 移出普通注册、scheduled 兼容注册、local proxy 注册和配置过滤。
- Registry 增加公共过滤 API。
- 可暂给现有 `_create_plan_tool` 等属性设置 bundle 别名，使两套 loop 不动。

验收：所有模式的 Registry、schema、定义顺序和 guide 与 Phase 0A 完全一致（仅 Phase 0B 明确批准的安全修正除外）。

### Phase 3：ToolControlSet 收敛元数据

- 控制工具构造、动态 schema、guide、显示名移入 control_set。
- delegate 晚绑定，所有模式初始值明确。
- Agent 三处元数据逻辑改为消费 controls。
- 主/子 loop 只做机械引用替换。
- delegate definitions 严格区分 `None` 与空列表，执行仍统一走 Phase 0B 的 authorizer。

验收：动态 skill/subagent schema、guide 和显示名逐项一致。

### Phase 4：scheduled 无状态化并转 Catalog

严格按第七节执行。完成后 26 个普通 Registry 工具全部来自 Catalog。

### Phase 5：清理兼容层和文档

- 删除旧 `_xxx_tool` 纯引用别名和过时注释；有安全意义的身份 setter/fallback 已在 Phase 0B 按工具删除，本阶段不得遗留。
- 更新自动发现设计、架构扩展指南和 ideas 索引。
- 执行启动安全和全量回归。

---

## 九、测试与验收矩阵

### 9.1 Catalog / Assembly

- 26 个完整工具名黄金清单一致。
- 保留 LLM 工具数组顺序、schema JSON 结构、enum 顺序和 guide 完整文本，不能只比较名称或集合。
- 生产工具同名立即失败并列出类路径。
- tests 同名替身不能覆盖生产工具。
- 可选依赖缺失跳过；首方缺失/循环 import/其他异常响亮失败。
- 失败模块不留下部分定义。
- 连续及并发发现幂等、顺序稳定。
- Registry 重复注册失败，retain/remove/clear 正确。
- Assembly 非法 role/依赖组合启动失败。
- MASTER 装配/import 后 `src.local_tools.proxy_tool` 不在 `sys.modules`；只有命中本地代理 allowed 时才加载实现。

### 9.2 AgentMode

| 模式 | 必测场景 |
|------|----------|
| MASTER | 26 个普通工具；无 boss proxy；delegate 晚绑定 |
| SUBAGENT inherit | 普通工具继承 + excluded；无隐式 boss proxy |
| SUBAGENT allowed | 只保留 allowed；明确 allowed 的 boss proxy 可注册 |
| SUBAGENT empty | 普通工具清空；控制工具保持既有规则 |
| STANDALONE | 入口级规则不变；delegate 为空且安全 |

### 9.3 控制工具

- definitions/get/guides/display_name 覆盖全部控制工具。
- use_skill schema 与 SkillRegistry 一致。
- delegate schema 只含租户可用子智能体。
- `available_subagents=None` 可生成全量 delegate；空列表完全不生成 delegate。
- 直接调用未订阅子智能体在创建 task record 前被拒绝；重新委派走同一授权器。
- A/B 租户并发获取 schema 和执行授权互不污染。
- 非 MASTER 查未知工具显示名不抛异常。
- 主/子 loop 的控制工具事件和结果不变。

### 9.4 上下文与安全

- A/B asyncio task 用 barrier 强制交错，各工具只能读本请求身份。
- 有身份请求后接无身份请求，必须读到 None。
- 工具异常后 token reset。
- 嵌套执行恢复外层上下文。
- 子智能体 cp 进入正确租户下载目录。
- scheduled 两 session 交错 dry-run 后仍写各自 user/session。
- local proxy 和 Desktop Remote Gateway 身份回归。
- Remote Gateway 从已验证 ticket/binding 构造 context，context-only 工具可以获得 tenant/user。
- `execute_batch()` 向每一项传播同一 context；嵌套执行恢复外层 context。
- `asyncio.to_thread` 保持 context；普通线程、`run_in_executor`、子进程只有显式传递后才能获得 context。

### 9.5 分阶段质量门

| 阶段 | 进入下一阶段前必须通过 |
|------|------------------------|
| Phase 0A | 基线快照生成；新增安全用例能稳定复现现状问题 |
| Phase 0B | delegate 双层授权 + 全入口 context + 身份工具原子迁移全部通过 |
| Phase 1 | Catalog 判重/事务/并发发现测试 + 完整黄金清单 |
| Phase 2 | 三种 AgentMode 装配快照 + MASTER lazy import 断言 |
| Phase 3 | 控制工具动态 schema/guide/display + 主子 loop 集成 |
| Phase 4 | scheduled 交错并发 + 24→26 来源变化、完整清单不变 |
| Phase 5 | 启动 import 终检 + 相关回归 + 同环境全量零新增失败 |

### 9.6 命令

```bash
./scripts/dev_test.sh tests/unit/tools/test_tool_discovery.py -p no:cacheprovider -q -v
./scripts/dev_test.sh tests/unit/test_tool_registry.py -p no:cacheprovider -q -v
./scripts/dev_test.sh tests/unit/tools/test_tool_assembly.py -p no:cacheprovider -q -v
./scripts/dev_test.sh tests/unit/tools/test_tool_context.py -p no:cacheprovider -q -v
./scripts/dev_test.sh tests/unit/tools/test_scheduled_task_tool.py -p no:cacheprovider -q -v
./scripts/dev_test.sh tests/unit/test_delegation_tool_tenant_filter.py -p no:cacheprovider -q -v
./scripts/dev_test.sh tests/unit/test_desktop_agent_d1.py -p no:cacheprovider -q -v
./scripts/dev_test.sh tests/integration/test_agent_loop.py -p no:cacheprovider -q -v
./scripts/dev_test.sh -m unit --ignore=tests/integration/test_data_analysis_integration.py -p no:cacheprovider -q
```

全量失败基线不依赖 `/tmp/fail_*.txt` 作为长期凭据。测试阶段在同一环境运行基准提交和当前改动，保存 JUnit XML 或失败 node id，要求零新增。

启动安全终检：

- `from src.tools.registry import discover_tool_classes`
- `from src.tools.assembly import assemble_agent_tools`
- `from src.core.agent import Agent`
- `from src.core.agent import get_master_agent; get_master_agent()`
- `import src.main`

同时记录 import 新增的 `src.*` 模块数，确保 Catalog/Assembly 没有反向拉起 Agent；MASTER 装配后必须显式断言未加载 `src.local_tools.proxy_tool`，不能只比较模块总数。

---

## 十、风险与回滚

| 风险 | 控制 |
|------|------|
| 自动发现漏工具/多暴露 | 名称 + schema 双契约，重复名启动失败 |
| 模式过滤变化 | 三种模式参数化快照 |
| 控制元数据变化 | 动态 skill/subagent schema 契约 |
| 身份串租户 | 请求级 token scope + 强制交错并发测试 |
| delegate 越权/空列表回退 | `None`/`[]` 显式语义 + schema/execute 双层授权 |
| Context 入口漏接 | 唯一 Factory + 入口检查表 + Gateway/batch/线程传播测试 |
| scheduled 串 session | 先无状态化、后 catalog 化 |
| 循环 import/启动面扩大 | Assembly 顶层只用轻量类型，具体工具按 role 延迟 import |
| 改动过大 | Phase 0A~5 串行，每阶段质量门通过后才继续 |

不引入运行时双注册 feature flag；旧/新两套 Registry 同时构造容易产生重复实例和副作用。等价性只在测试中比较。

---

## 十一、预计文件范围

新增：

- `src/tools/assembly.py`
- `src/tools/control_set.py`
- `src/tools/context.py`（若不直接演进 `_helpers.py`）
- `src/tools/delegation_policy.py`（或等价的无状态授权模块）
- `tests/unit/tools/test_tool_assembly.py`
- `tests/unit/tools/test_tool_control_set.py`
- `tests/unit/tools/test_tool_context.py`

重点修改：

- `src/tools/base.py`
- `src/tools/registry.py`
- `src/tools/executor.py`
- `src/core/agent.py`
- `src/subagents/executor.py`
- `src/local_tools/proxy_tool.py`
- email/browser/cp/knowledge/scheduler 对应工具
- `tests/unit/tools/test_tool_discovery.py`
- `tests/unit/test_delegation_tool_tenant_filter.py`
- `docs/tools/tool-auto-discovery-design.md`
- `docs/ideas.md`

---

## 十二、最终验收

1. Agent 不含具体普通工具 import、普通注册、本地代理清单或身份工具名元组。
2. 新增普通工具只新增工具类和测试；黄金清单变化显式评审。
3. 三种 AgentMode 的工具集合、schema、过滤和控制行为与 `d0e267a7` 一致；仅允许本文明确列出的 delegate 授权和身份隔离安全修正。
4. 工具身份来自请求级上下文，共享实例不保存 user/tenant/session。
5. MASTER/SUBAGENT/scheduled/LOCAL_REQUIRED/Remote Gateway/execute_batch/嵌套执行均有明确 Context 构造和传播契约。
6. delegate 对 `None`/空列表语义明确，schema 不暴露无权限项，直接调用也必须通过执行期授权。
7. scheduled 进入 Catalog，且并发不串 user/session。
8. Catalog 按 class identity 收集、成功模块过滤后判重；重名、首方 import 错误、不可构造工具响亮失败。
9. Assembly role 和依赖组合运行时校验；MASTER 不加载 local proxy 实现依赖。
10. 工具数组/schema/enum/guide 顺序契约、相关单测、Agent loop 和启动 import 通过；全量测试相对同环境基线零新增。
11. 按开发 → 独立测试 → CodeReview 串行实施，不自动提交或 push。
