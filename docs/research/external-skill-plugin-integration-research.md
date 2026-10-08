# 外部 Skill 插件接入 Runtime 机制调研与设计（workbuddy 样本）

> 2026-10-06 · 样本：workbuddy 制作的 `jingpian-house-finder-v2.0.0.zip`（镜片找房小程序桌面 RPA skill）
> 关联：Runtime 插件生态扩展 · 不对应已立项开发任务

> 修订（2026-10-08）：第 1～7 节保留 M1/M2 的历史方案；目标架构调整为“客户机安装代码、云端登记手册和调用契约、UI 管理可选插件”，见第 8 节及[新设计](../system/runtime-plugin-host-architecture-design.md)、[开发计划](../plans/plan-runtime-plugin-host.md)。第 6 节对旧 Desktop 网关路线的否定，不适用于复用同一 Runtime core 的新 UI 方案。

## 1. 结论（TL;DR）

**可行，且不需要发明第三种接入机制**。本 runtime 已有两条能力接入通道，把它们组合即可：

- **知识层复用 Skill 通道**：workbuddy skill 就是 AgentSkills 标准 SKILL.md 格式，与 `src/core/skill_loader.py` 完全兼容（额外 frontmatter 字段被忽略）。放进插件 skill 目录即被 `use_skill` 动态工具发现，Agent 获得操作手册。
- **执行层复用 CLI Provider 的 invocation 队列**：RPA 脚本必须在 Windows 设备上跑（Win32 API + 微信 PC 端），现有 `local_tools` 的「云端入队 → 设备 claim → 子进程执行 → 结果回传」链路正是为此建的。新增一个**通用 `skill-runner` Provider**（catalog 与 Runtime manifest 各加一条，纯加法），`skill_execute` 按 skill 声明路由到该队列，设备侧 agent-tool-runtime 新增 skill 执行 handler。

对内部三个 CLI 插件（boss/weixin/wecom）**零侵入**：它们的受信清单、manifest、校验逻辑均不动；唯一强交互点是**桌面资源锁必须共享**——该 skill 操作微信 PC 端小程序窗口，与 weixin-cli 操作同一微信进程，必须纳入同一互斥域串行执行。

主要工程量（估）在设备侧 skill-runner handler（skill 目录发现 / Python venv 管理 / 命令门禁 / 截图产物回传）与 skill_execute 路由分支，云端侧改动小。

## 2. 样本分析：jingpian-house-finder v2.0.0

```
jingpian-house-finder/
├── SKILL.md               # AgentSkills 标准：name/description/version + 手册正文
├── requirements.txt       # pillow / rapidocr-onnxruntime
├── scripts/               # flow.py（seq 动作执行器）、wx_open.py（唤起小程序）、
│                          # uicache.py（坐标缓存）、wx_act/wx_capture/wx_ocr/win_probe
├── references/            # ui-cache.json（界面坐标缓存 ★核心）、ui-map.md、_scan_data.json
└── shots/tpl/             # 首页校验模板图
```

关键事实：

| 维度 | 事实 | 对接入的含义 |
|---|---|---|
| 包格式 | 标准 AgentSkills SKILL.md（frontmatter + scripts/references 资源目录） | 与 `SkillLoader` 兼容，`agent_created`/`updated` 等额外字段被忽略，直接可加载 |
| 执行环境 | **Windows 专用**：Win32 API（SetForegroundWindow/PrintWindow）、微信 PC 端已登录、Python 3.13 venv | 无法在 runner-worker（Linux 容器）执行；必须路由到 Windows 设备 |
| 交互模式 | 缓存坐标驱动：脚本完成绝大多数点击/校验/重试，模型只做「需求问答 + 读详情截图」两类判断 | 执行入口收敛为少数几条命令（`flow.py seq "..."`），门禁可枚举；**结果必须能回传截图给模型看** |
| 自学习 | 脚本运行时写回 `ui-cache.json`（坐标校准入库） | skill 目录在设备侧必须**可写持久**（非只读安装），且多租户共享设备时缓存互相污染需注意 |
| 安全敏感 | 键鼠注入、剪贴板读写、操作真实微信窗口 | 与 weixin-cli 同进程互斥；误操作风险与 CLI 写操作同级，需同等审计 |

## 3. Runtime 现状：两条能力接入通道

### 3.1 CLI Provider 通道（boss / weixin / wecom）

能力 = **封闭 schema 的工具**。链路（详查 `docs/system/first-party-cli-mcp-provider-standard.md`）：

1. 云端静态受信清单：`src/local_tools/catalog.py:10` `TRUSTED_PROVIDERS`（provider_id + execution_target=local_required + 工具白名单）；`src/local_tools/manifest.py` + `src/local_tools/proxy_tool.py:2154` 代理工具类清单。
2. 装配：`src/tools/assembly.py` 按 SUBAGENT.md `tools.allowed` 与代理工具名交集注册进 ToolRegistry；`runtime/tools.py::ToolDispatcher` 见 `execution_target==LOCAL_REQUIRED` 走 `LocalToolAdapter`。
3. 执行：proxy `execute()` → 设备闸门（selected+active+在线+能力覆盖）→ `LocalInvocationService.enqueue`（DB 队列）→ 轮询终态。
4. 设备侧：`clients/agent-tool-runtime`（Windows 宿主 Node 进程）claim 长轮询 → manifest 门禁 → 桌面资源锁 → v2 写操作 write-authorize 许可 + journal → **MCP stdio 子进程**调用对应 CLI → result 回传。
5. Runtime 侧镜像清单 `clients/agent-tool-runtime/src/providers.ts` `TRUSTED_MANIFESTS` 与云端两处**人工三处同步**——这是每接一个第一方 CLI 的最大摩擦点。

### 3.2 Skill 通道（src/skills + 租户技能）

能力 = **给 LLM 的操作手册 + 脚本**，零代码注册：

1. `src/core/skill_loader.py` 解析 SKILL.md（三层渐进加载：元数据/正文/资源）。
2. `src/services/agent_runner/runtime/resource_cache.py:40` 进程级缓存 registry，**只加载 `src/skills`**；`runtime/skill_session.py` 执行期合并租户目录 `storage/tenants/{id}/skills/`（TTL 缓存 + 版本一致性门）。
3. `use_skill`（`src/tools/skill/use_skill_tool.py`）工具定义由 `skill_registry.get_skill_tool_definition()`（`skill_registry.py:323`）**动态生成**——放目录即出现在 Agent 工具面。
4. `skill_execute`（`src/tools/skill/skill_execute_tool.py` → `src/core/skill_executor.py:408`）以 `asyncio.create_subprocess_shell` 在 **runner-worker 容器内**执行命令。

`skill_registry.load_from_directories()`（`skill_registry.py:97`）已实现多目录优先级合并（低→高覆盖同名），但 runner 尚未接入插件目录。另：`docker-compose.agent-runner.yml` 各服务均挂载 `./skills:/app/skills`，**代码未消费**——天然是插件 skill 的预留分发位。

### 3.3 通道对比与判断

| | CLI Provider 通道 | Skill 通道 | workbuddy skill 需要 |
|---|---|---|---|
| 注册成本 | 6 处同步（catalog/manifest/proxy/SUBAGENT/providers.ts/CLI TOOL_DEFS），三制品发版 | 放目录即生效 | 低成本注册 ✔（生态化前提） |
| 执行位置 | 用户 Windows 设备（invocation 队列） | worker Linux 容器 | **必须 Windows 设备** ✘ |
| 能力形态 | 封闭工具 schema | LLM 生成命令字符串 | 命令面收敛可枚举，可门禁 |
| 权限/审计/计费 | 完备（claim 校验/许可/journal/计费预检） | 无设备语义 | 需要继承 CLI 级管控 |

**核心矛盾只有一个**：Skill 通道的知识层完全够用，执行层到不了设备。因此方案 = Skill 包格式做插件的「壳」，invocation 队列做插件的「腿」。

## 4. 方案设计：外部 Skill 插件（External Skill Plugin）机制

### 4.0 总体

```
┌─ 云端 runner-worker ─────────────────────────────────────────────┐
│ use_skill(手册) → skill_execute(command)                          │
│   └─ SkillExecutor 路由：metadata.execution == "device"？          │
│        ├─ 否 → 容器内子进程（现状，不动）                            │
│        └─ 是 → LocalInvocationService.enqueue(provider="skill-runner")
└──────────────┬────────────────────────────────────────────────────┘
               │ DB invocation 队列（复用，含进度/取消/恢复/计费）
┌──────────────▼────────── Windows 设备 agent-tool-runtime ─────────┐
│ 新 provider handler「skill-runner」：                              │
│  skill 目录发现/版本上报 → 桌面锁（与三 CLI 同一互斥域）             │
│  → 命令门禁（entry 白名单）→ 受管 venv 子进程执行                    │
│  → stdout/JSON 结果 + 截图产物回传                                 │
└────────────────────────────────────────────────────────────────────┘
```

### 4.1 A 层：插件注册（云端知识层）

- **目录链**：`src/skills`（基础，不动）→ `skills/`（仓库根，compose 已挂载、随制品分发的第一方插件位）→ `storage/skills/plugins/`（运维/管理端动态安装位）→ 租户目录（最高优先级）。实现即把 `resource_cache.py` 的 `load_from_directory` 换成 `load_from_directories`，并仿 `cached_subagent_registry` 的签名刷新做插件目录变更失效。
- **frontmatter 声明扩展**（放 `metadata` 下，向后兼容、不改 Skill dataclass）：
  ```yaml
  metadata:
    execution: device            # server（默认，现状）| device
    device_requirements: { os: windows, apps: [wechat_pc] }
    entry: scripts/flow.py       # 设备侧唯一允许的执行入口（可多条）
  ```
- **白名单与审批**：插件 skill 默认不进 allowed；`settings` 增插件总开关 + 逐 skill 审批（审批时记录 SKILL.md 及资源目录的 computedHash——复用仓库 `skills-lock.json` 的供应链锁定思路）。未经审批的插件目录里的 skill 对 Agent 完全不可见。

### 4.2 B 层：执行路由（runner 侧）

- `SkillExecutor.execute()` 入口加一个分支：skill 声明 `execution: device` 时不走 `create_subprocess_shell`，改为构造 invocation（provider_key=`skill-runner`，tool_name=`skill_script_run`，payload 携带 skill 名/entry/args/工作目录语义），复用 proxy 链路的设备闸门、计费预检、进度、取消、超时与恢复语义（`local_invocations.py`/`local_recovery.py` 的存活性证明对 skill 同样成立）。
- **不新增 LLM 可见工具**：`skill_execute` 保持单入口，路由对 LLM 透明——use_skill 手册里的指引（「执行 `flow.py seq ...`」）原样成立，手册无需按执行位置改写。（备选：单开 `device_skill_execute` 工具更显式，但需改全部 SUBAGENT 白名单与提示词，不推荐首期。）
- `catalog.py` 追加一条 `skill-runner`（provider_id `ai.aidwork.skill-runner`，tools=["skill_script_run"]），`providers.ts` 镜像一条。**追加条目不触碰既有三 Provider 的校验路径**。

### 4.3 C 层：设备侧执行器（agent-tool-runtime 新 provider handler）

1. **skill 目录**：设备约定目录（如 `%USERPROFILE%\.aid-runtime\skills\`），支持导入/软链 workbuddy 目录（`~/.workbuddy/skills/`）；目录可写（uicache 自学习写回）。
2. **Python 环境**：设备受管 venv（首期可直接探测复用 workbuddy venv），按 `requirements.txt` 安装/校验；解释器路径记录在设备侧配置，不由云端下发。
3. **能力上报**：capabilities 增 `skills: [{name, version, hash}]`，云端路由时做「设备已安装且版本/哈希一致」校验（对齐 `skill_session` 的版本门思路）——不一致返回可读错误，提示先安装/升级。
4. **命令门禁**（与 CLI 封闭 schema 的安全差距补偿，**必须 fail-closed**）：
   - 只允许 `<受管venv python> <skill_dir>/<声明entry> <args>` 形态；entry 必须在 skill 注册时声明的白名单内；
   - 禁止 shell 元字符拼接执行（不走 shell -c，argv 直传）、禁止绝对/相对路径逃出 skill_dir、args 长度/数量上限；
   - 工作目录圈定 skill_dir，产物只写 skill_dir/shots 与指定产物目录。
5. **桌面锁**：与 boss/weixin/wecom 共享同一桌面资源锁（`shared_lock_capable` 机制既有），skill-runner 声明为不可与三 CLI 并发——该样本操作微信 PC 端，与 weixin-cli **同进程**，不串行必然互踩（键鼠注入互相打断、焦点被抢）。
6. **结果与产物回传**：
   - stdout（JSON 结果）走既有 result 链路；
   - 截图产物：设备上传至共享 storage 产物目录（invocation 关联路径），result.data 返回引用 + 关键图 base64（有大小护栏）；`skill_execute` 把图片转成多模态内容块返回给 Agent。平台 VL 多模态调用已有先例（`src/tenant_custom/hongtao_shop/vision.py`，base64 → 多模态模型），缺口仅在「invocation 结果 → 工具结果」这一段的图片透传协议，属 M3 工程项而非架构阻塞。

### 4.4 D 层：权限、租户与治理

- 设备闸门沿用：租户/用户 selected+active 设备 + claim 时 provider 白名单 + capabilities 覆盖检查。
- 租户级：插件 skill 走租户 allowed（既有 `skill_session` 合并链），可做「租户市场」上架/启停（M4）。
- 计费：invocation 计费预检与落账沿用；skill 执行按「skill-runner 伪模型/操作类」归类计费。
- 审计：journal 落盘沿用；RPA 截图作为 evidence_ref（`OperationResultRequest.evidence_ref` 字段本就为此预留「截图/消息 id 等受控引用」）。

### 4.5 对内部三 CLI 插件的无影响论证

| 改动点 | 性质 | 对三 CLI 的影响 |
|---|---|---|
| `catalog.py` 加 `skill-runner` 条目 | Dict 追加 | 无（三 Provider 条目与 `is_tool_allowed` 逻辑不动） |
| `providers.ts` 加镜像条目 | 追加 | 无 |
| `SkillExecutor` 路由分支 | 新分支，仅 device 声明的 skill 进入 | 无（server 路径代码不动） |
| `resource_cache` 多目录化 | 加载源扩展 | `src/skills` 仍是最低优先级目录，同名覆盖方向为插件覆盖基础（需审批门保证不会意外遮蔽内置 skill——同名冲突时**内置优先并告警**更稳妥，实现时取「拒绝注册同名插件」） |
| 桌面锁共享 | 互斥域扩大 | **行为交互点**：三 CLI 与 skill-runner 互相排队，牺牲并发换正确性；既有 `shared_lock_capable` 语义就是为此设计 |
| invocation 队列 | 新增 consumer 路由 | 无（provider_key 隔离，行级过滤已按 provider 分流） |

## 5. 分期建议

| 阶段 | 内容 | 风险 |
|---|---|---|
| M1 知识层 | 插件目录链 + 审批/hash 门 + `use_skill` 可见（device skill 先返回「需要设备执行，暂不可用」占位）；[开发计划](../plans/plan-external-skill-plugin-m1.md) | 低，纯加法，可独立上线 |
| M2 执行闭环 | catalog/providers.ts 加 skill-runner + `SkillExecutor` 路由 + 设备 handler（目录发现/venv/门禁/锁/stdout 回传）；用 jingpian-house-finder 做首验证样本（`uicache.py status`、`flow.py seq verify` 走通即算闭环）；[开发计划](../plans/plan-external-skill-plugin-m2.md) | 中，设备侧为主 |
| M3 多模态 | 截图产物回传协议 + 工具结果带图（详情页阅读依赖此项；此前模型只能拿 JSON 文本） | 中 |
| M4 生态治理 | 管理端安装/审批/版本管理、租户市场、计费归类、设备侧 skill 自动分发（设备从云端拉包 + hash 校验安装） | 中 |

## 6. 备选方案（为何不选）

1. **按第一方 CLI 规范把 skill MCP 化**（包成第四个 CLI）：管控最强，但每个 skill 六处同步 + 三制品发版，无法生态化；适合把**稳定高频**的外部 skill（如本样本跑稳后）升级为第一方 Provider，与插件机制不冲突、可平滑迁移。
2. **desktop_agent 网关路径**（Electron 桌面客户端）：面向另一生态（远程工具网关），且当前三 CLI 均不走此路，引入会造成双执行面。
3. **容器内跑 Windows RPA**（Windows 容器/云桌面）：与「操作用户自己登录的微信」这一产品前提冲突（微信登录态在用户设备上），排除。

## 7. 开放问题（实现前需拍板）

1. 插件 skill 与内置 skill 同名冲突策略（建议：拒绝注册 + 审批时告警）。
2. 设备侧 skill 分发方式：首期「用户手动安装 + workbuddy 导入」即可用；自动分发（云端拉包）放 M4。
3. 截图回传大小护栏与张数上限（详情页一次 1 张、seq 产物全量引用 + 按需取图，建议按此设计）。
4. `ui-cache.json` 自学习写回在多租户共用一台设备时的隔离策略（首期假设「一台设备单租户使用」，与三 CLI 现状一致）。
5. weixin-cli 与本 skill 的微信会话占用语义：skill 运行期间 weixin-cli 的 unread 轮询是否需要让位（桌面锁之外的业务级协调）。

## 8. 2026-10-08 架构复核：单端安装与 Runtime UI

### 8.1 用户意见及结论

两点意见成立：设备执行 skill 的代码和依赖没有必要同时安装在云端；Runtime 可提供可视化配对/插件管理，BOSS、weixin、wecom 改为可选独立插件。

双端放完整包是旧 M1/M2 的最小接入方式：服务端依赖目录加载器计算审批 hash、读取手册和入口。它是实现复用造成的耦合，不是执行机制必须要求。新架构把注册依据改为受权契约和不可变内容摘要，设备执行包由本地宿主管理。

云端仍需 SKILL.md、调用参数/入口、版本及权限；部分技能还需参考文档，不能把“无需代码”简化成“只要技能名称即可”。官方包分发存储或可选代码审核不等于云端运行时安装。

### 8.2 新核对事实

- M2 代码已存在：`skill_runner_proxy.py` 派发，设备 `skillRunner.ts` 执行；`configs/config.yaml` 两个开关仍默认关闭。部署和 Windows 实测不能由源码存在推断。
- `clients/release/aidwork-recruiting-client-0.2.14.zip` 中 Runtime tgz 不含 skillRunner 编译模块，cli/config/providers 编译产物无对应引用。现有包不能承载源码中的新能力。
- Runtime package 仍捆绑 BOSS，config 将其默认入口视为可用；并非已经实现零业务插件核心。
- Electron Desktop 已有 UI、安全 IPC 和发行基础，但没有运行 Runtime；共用 Host core 当前只定义接口。
- MCP Provider 可以提供 tools/list；现有 Runtime ProviderManager 尚未把工具发现转成云端注册，云端仍依赖静态 catalog/proxy。
- skill-runner manifest 是 protocol v1，不等同于 v2 受控写动作的 write-authorize 与结果持久 ACK 全链已经适用于第三方脚本。
- 原 jingpian ZIP 本次不在工作区；历史依赖、mutable、截图行为需用原包重新核实。

### 8.3 推荐调整

1. 本机 ZIP 导入生成独立执行侧清单，保留原 SKILL.md；环境、入口、状态和输出由宿主管理。
2. 配对设备只登记手册、批准的文档/schema 与 digest；心跳提供短清单。新私有登记按租户/用户/设备授权，不复用 M1 平台级审批为全局可见。
3. 云端 Agent 编排任务，Runtime 执行结构化脚本或 MCP 调用。普通 skill 不变成本地自治 Agent；无完整流程入口时仍需多轮脚本/截图交互。
4. 复用 Electron 工程，提供独立 Runtime 产品形态；main 监管同一 Runtime core，renderer 无业务执行旁路。
5. 三 CLI 保持标准 MCP，对外兼容；本地 lifecycle 和安装共用，工具 schema 与 skill 手册保持各自语义。
6. 原子版本与调用固定、撤销复查、unknown 不重试、截图真正进入模型输入纳入首轮验收。市场和在线分发后置。

与现有规范的冲突处理：第一方 CLI 规范第 8/10.1 节已要求 Runtime core 共用和业务插件按需交付；选择该方向，当前 BOSS 捆绑作为兼容旧版迁移。旧本地 Agent Coordinator 路线已被 AgentRunner 替代，不重新启用。

### 8.4 外部依据

- [AgentSkills 格式](https://agentskills.io/specification)：手册和可选脚本/参考资源、渐进加载。标准 metadata 为字符串映射，现有 entry/mutable 列表是本项目扩展，执行清单独立可减少第三方适配改写。
- [MCP Tools（2025-06-18）](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)：发现、schema、调用及图片等结果；不替代平台授权。
- [Electron 安全](https://www.electronjs.org/docs/latest/tutorial/security)和[进程监管 API](https://www.electronjs.org/docs/latest/api/utility-process)：支持隔离 UI 与执行进程。具体宿主适配依仓库锁定版本实测。

本次为只读核查与设计，没有运行客户机插件、重打发行包或部署。详细边界、数据契约、安装流程与阶段验收见关联设计和计划。
