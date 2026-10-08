# 外部 Skill 插件位（M1 知识层）

本目录是**第一方插件位**（compose 已将 `./skills:/app/skills` 挂载进
api/worker/runner 容器，M1 起开始消费）；运维动态安装位在
`storage/skills/plugins/`。目录链优先级：`src/skills`（内置）→ 本目录 →
`storage/skills/plugins/`（低 → 高，插件间同名时高优先级覆盖）。

设计文档：[docs/plans/plan-external-skill-plugin-m1.md](../docs/plans/plan-external-skill-plugin-m1.md)
（M1 只做知识层：插件 skill 经审批后对 Agent 可见，执行层路由属 M2）。

## 使用步骤

1. 将插件 skill 放入本目录（AgentSkills 标准 `SKILL.md` 格式，一级子目录一个 skill）；
2. 开启 `configs/config.yaml` 的 `skills.plugins.enabled: true`；
3. 审批（内容 hash 锁定 + note 必填来源与用途）：

   ```bash
   python scripts/approve_skill_plugin.py --scan
   python scripts/approve_skill_plugin.py --approve <skill-name> --note "来源与用途"
   ```

4. 将 skill 名加入 `skills.master_agent.allowed` / `skills.subagent.default_allowed`
   白名单（空列表 = 允许所有）后对 Agent 生效；
5. 撤销：`python scripts/approve_skill_plugin.py --revoke <skill-name>`——runner
   进程缓存随签名轮询在下一次执行生效，租户合并视图最迟 TTL 300s 后刷新。

## 安全边界（务必阅读）

- **审批 ≠ 可执行**：M1 对插件来源 skill 的 `skill_execute` 全量拦截
  （`src/core/skill_executor.py` 入口，只读不可执行）；设备执行链路属 M2。
- **审批即锁定内容**：插件目录内容变化后必须重新 `--approve`，否则注册表
  重建时被剔除（fail-closed）；读手册时另校验 SKILL.md hash（两层内容门）。
- **与内置 skill 同名的插件会被拒绝注册**（内置优先）。
- **租户边界（如实声明）**：审批清单是平台级清单，**无租户维度**——一次审批
  + 该 skill 名进入（或本就不受）allowed 白名单 = **所有租户**的
  master/subagent 立即可见可用；租户绑定只管租户目录自定义 skill。
  租户级审批/上架属 M4 规划，M1 期间审批从紧（note 必填来源与用途）。
- 管理后台技能选择器会出现已审批插件 skill（`_all_skills` 扩大，预期行为）；
  插件 skill 的 `init_script` 不会被执行（`run_init=False`，纵深防御）。
