# 母体 Agent（agent.py）收敛原则

> 日期：2026-08-19
>
> 性质：架构治理原则与执行顺序决策，不是功能设计。对后续所有涉及 `src/core/agent.py` 的开发工作有约束力。
>
> 关联：[真诚建议 §10 把大型母体 Agent 收敛为稳定 Kernel](../research/aid-work-agent-honest-advice.md) / [企业 Agent 平台总体架构（Task Plane）](enterprise-agent-platform/enterprise-agent-platform-integration-design.md)

## 1. 背景与核实数据

2026-08-19 核实：

- `src/core/agent.py` 共 **4284 行**、44 个类/函数定义；`tenant/channel/video/wecom/billing` 等领域关键词约 **208 处**——Prompt 组装、上下文、记忆、LLM 循环、工具解析与执行、子智能体委派、文件结果、SSE、计费、渠道差异仍汇聚在母体内。
- 2026-08-12 战略审计记录为 4113 行，**一周增长约 170 行**，债务在加速（近期 #64 中间反馈、视频参数、skill_ws 清理等都在往里加代码）。
- `src/core/` 下已有 30+ 独立模块（executor、skill_executor、trace_collector、dialog_manager、plan_manager 等），拆分事实上一直以绞杀者模式进行，只是 agent.py 仍是所有主线的汇聚点。

## 2. 核心决策

**不设立「agent.py 专项重构」项目，不把它作为其他工作的阻塞前置。采用「冻结增长 + 伴生绞杀」路线。**

理由：

1. agent.py 是生产核心循环（多租户计费、渠道、子智能体都从它走），目前没有一套 Kernel 等价性回归保障，大爆炸重写是全仓库风险最高的动作；
2. Task Plane Phase 1（旁路双写 + shadow 决策）的接入点在 `process_message` 入口与工具执行边界，D1 已打出真实 Agent 分步 tool-call seam，并不在 agent.py 深处；协作体系 Phase 1 替换的是 `src/subagents/executor.py`；
3. 无驱动力的重构容易产出投机抽象；Task Plane 与协作体系恰好提供真实的拆分驱动力。

同时确认的未来规划启动顺序（2026-08-19 与用户对齐）：**Task Plane Phase 0+1 最小切片先行 → 多智能体协作体系 Phase 0-2 跟进**。本文原则中的首批拆分项并入 Task Plane 落地设计的交付物。

## 3. 原则

| # | 原则 | 内容 |
|---|------|------|
| P1 | **冻结增长** | 新逻辑不得写入 agent.py，必须落在独立模块后由 agent.py 调用。允许的改动仅限：既有函数的 bug 修复、调用新模块的接线行 |
| P2 | **结构守卫** | 测试中加行数守卫（基线 4284 行，只降不升）与依赖方向约束，防止回涨；照抄仓库已有的前端结构测试模式 |
| P3 | **伴生绞杀** | 只拆有驱动力的 seam：为接入 Task Plane / 协作体系 / 后续平面而拆，不为「好看」而拆 |
| P4 | **首批拆分目标** | ActionDispatcher（工具执行分发）、ContinuationManager（等待/恢复/澄清）——与 Task Plane Phase 0/1 双写接入点天然重合；TurnEngine 随协作体系 Phase 1 一并评估 |
| P5 | **拆分即等价** | 每次拆分前先固化行为回归（现有 agent 单测 + 相邻回归），拆分前后全绿才算完成；禁止「顺手改行为」——行为变更与结构拆分不得混在同一个提交 |
| P6 | **Kernel 不认识领域** | 拆出的模块不得包含 video/travel/BOSS/wecom 等领域特例；数值上限、权限规则、状态迁移、必填字段、格式验证、幂等/重试条件等确定性规则优先从 Prompt 移入代码（战略文档 §10.3） |
| P7 | **完整 Kernel 化不设专项** | ContextAssembler / IntentCompiler / TurnEngine / CapabilityResolver / ResultInterpreter / ResponseComposer 等完整边界（战略文档 §10.2）不排专门项目，随灯塔工作流与各平面落地按需拆 |

## 4. 执行顺序

1. **立即**（1-2 天，可单独排期）：P1 立规 + P2 结构守卫落地。
2. **Task Plane Phase 0/1 伴生**：P4 首批 seam 拆分，作为 Task Plane 落地设计的交付物之一。
3. **后续按需**：P7，以真实接入需求为触发条件。

## 5. 追踪

- 本原则登记于 `docs/ideas.md` 基础设施区（#65）。
- Task Plane 落地设计已产出（2026-08-19）：[task-plane-phase0-1-landing-design.md](enterprise-agent-platform/task-plane-phase0-1-landing-design.md)，其 §15 已将 P1（agent.py 接线 ≤20 行）、P2（结构守卫测试，基线 4284）、P4（Phase K 工具分发 seam 抽取）纳入交付物。
- 每完成一次 seam 拆分，在 `docs/ideas.md` #65 条目更新进度与最新行数基线。
