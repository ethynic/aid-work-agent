# 简历-职位匹配体系设计（职位为中心 / 评分 / 关键信息 / 严谨关联）

> 状态：📋 设计完成，待开发（2026-08-17 与用户对齐需求后成稿；同日二次对齐：
> 职位管理为 BOSS CLI 的基础心智模型——要求即筛选项、职位选择交互、职位状态）
> 接力说明：本文档为新会话的实施依据。当前会话已交付的基线见 §0；
> 按 §6 的 Phase 计划开工，每阶段走 dev-workflow 三智能体 + 真机验收。

## 0. 当前基线（2026-08-17 已交付，勿重复建设）

- BOSS CLI 14 个 MCP tool（含 boss_resume_detail / boss_resume_batch 批量读简历入库、
  boss_filter_options 查档位、boss_filter 保底映射）
- 云端：简历库（bs_recruiting_operator_resumes + 前端）、职位管理
  （bs_recruiting_operator_jobs + job_scripts + 前端 + 预置 PHP/Laravel 13 条话术）、
  boss_send_to / boss_send_current 已上云且带 script_title 话术模式
  （SCRIPT_NEEDS_FILL：话术原文 + 简历 OCR 摘录 600 字，占位符证据铁律）
- Runtime 受信清单 14 工具；AttachThreadInput 强制前台；菜单「职位管理」
- 一键链路（SUBAGENT）：筛选简历按钮 → 反问参数 → 切职位 → 查档位 → 筛选 →
  批量读简历入库 → 汇报 → 询问打招呼

## 1. 现状问题（用户 2026-08-17 指出，均已核实）

| # | 问题 | 现状代码位置 |
|---|---|---|
| P1 | 简历与职位无硬关联：resumes.job_name 是自由文本（取自职位框显示名），与 jobs 表无 FK；写法漂移即断联 | recruiting_resume_service.py（job_name TEXT） |
| P2 | 话术解析不严谨：_resolve_job_script 缺省「遍历全部职位」找话术，跨职位混用 | proxy_tool.py:514 |
| P3 | 无匹配度：OCR 入库即结束，≥70 才算匹配、才进入打招呼流程的判断不存在 | — |
| P4 | 无结构化关键信息：只存 OCR 全文，沟通时靠 600 字截断摘录现场找亮点 | SCRIPT_NEEDS_FILL 的 resume_excerpt |
| P5 | 打招呼/发消息与匹配度无联动（greet 独立执行，不问简历匹配与否） | SUBAGENT 链路 |

## 2. 目标模型（职位为中心，严谨关联）

参照 BOSS「职位管理」页面的模型（职位基本信息 + 职位要求；BOSS 官方提示
「将通过以下条件，为您精确推荐合适的牛人」），**职位是我们 BOSS CLI 工具的主数据**：
要求、阈值、话术、简历全挂在职位下；筛选参数默认取自职位要求，评分输入也来自职位要求。

```
jobs（职位管理，主数据）
  ├── status TEXT='active'      active（正常，可被选择/筛选）/ paused（暂停，仅存档）
  ├── job_requirements JSONB    结构化职位要求 = 筛选项（见 §2.1，评分输入）
  ├── match_threshold INT=70    匹配及格线（每职位可调）
  ├── 话术（job_scripts，已有 FK）——只在所属职位下解析，绝不跨职位
  └── resumes（简历库）
        ├── job_id FK → jobs        （P1 修复；job_name 保留为显示冗余）
        ├── match_score INT NULL    0-100（LLM 评分）
        ├── match_summary TEXT      评分理由（≤100 字，人可读）
        ├── match_status TEXT       unmatched / matched / rejected
        │                           （阈值判断；与既有 status 业务流转字段分离）
        └── key_info JSONB          结构化关键信息（评分时一并产出）
```

- **status 语义**：active 才出现在选择交互选项（§5）和一键链路里；paused 保留
  简历关联与统计数据（「简历 N · 匹配 M」仍可见），但不可发起筛选/打招呼。
  存量回填 active。与 BOSS 页面上职位的在线/暂停**不强同步**（我们库是主数据，
  页面切换由 boss_select_job 负责）。
- **job_name 是与 BOSS 页面职位的关联键**：boss_select_job 按精确名切换，
  因此职位库的 job_name 需与 BOSS 上发布的职位名一致（不一致时切换报错，
  提示改名对齐；不做别名映射表）。

### 2.1 job_requirements schema（要求 = 筛选项）

BOSS 筛选面板可设置维度只有 3 个：**经验（单选）/ 学历（多选）/ 薪资（单选）**
（FilterSetter ROW_DEFS；年龄/活跃度等行存在但不可设置）。schema 与之一一对应：

```json
{
  "experience": "3-5年",
  "educations": ["本科", "硕士"],
  "salary": "10-20K",
  "keywords": ["Linux", "MySQL", "PHP", "Laravel"],
  "notes": "接受 AI 工具深度使用者优先"
}
```

- `experience` / `educations` / `salary`：**取值必须是 boss_filter_options 档位文本**
  （写入时校验；前端用档位下拉，杜绝自由文本漂移）→ 可直接作为 boss_filter 入参。
- `keywords` / `notes`：面板无对应筛选行，不参与 CLI 筛选；作为 LLM 评分的
  加分项输入（key_info 命中 keywords 时体现）与汇报展示。
- 字段可缺省（null/[]）；存量为空时一键链路退回「反问用户要筛选要求」的现状行为。

### key_info 固定 schema（评分 LLM 必须按此输出）
```json
{
  "years_of_experience": 6,
  "education": "本科",
  "current_company": "xx科技",
  "core_skills": ["PHP", "Laravel", "MySQL", "Vue"],
  "highlights": ["日活十万级 SaaS 主导", "AI 工具深度使用者（Cursor/Claude Code）"],
  "ai_tool_usage": "熟练：Cursor + Claude Code 日常开发",
  "salary_expectation": "15-25K",
  "concerns": ["行业跨度大", "薪资超预算"]
}
```
字段可缺省（简历没写就 null），数组字段空给 []，**严禁编造**（同话术占位符铁律）。

## 3. 匹配度评分（OCR 入库后自动）

- **时机**：create_resume_record_from_tool_result 落库成功后，紧接着评分并 UPDATE 同一行。
  评分失败（LLM 异常/JSON 不合法重试 1 次仍败）→ score/status 留 NULL（unmatched），
  **绝不因评分失败丢简历或阻塞工具返回**；摘要里标「未评分」，前端提供「重新评分」按钮。
- **评分者**：云端 LLM 结构化评审一次。输入 = 职位要求（job_requirements；缺省用
  job_name + 该职位话术上下文拼一段隐含要求）+ 简历 OCR 正文（截断 3000 字）；
  输出 = 上节 JSON + score + match_summary。规则打分（关键词匹配）太脆，不做。
- **新服务** `src/services/recruiting_match_service.py`：
  `evaluate_and_update(tenant_id, resume_id) -> {score, match_status, key_info}`，
  内部走既有 LLM 调用设施；prompt 固定要求「仅依据简历文本，不可见的字段填 null」。
- **阈值**：match_score ≥ jobs.match_threshold（默认 70）→ matched；
  < 50 → rejected；50~69 → 归 unmatched（保留分数，前端黄色徽标提示「接近」，人工可翻牌）。
- **接入点**：云端 BossResumeDetailTool / BossResumeBatchTool 落库循环里，逐份
  `to_thread(evaluate_and_update)`；返回 LLM 的紧凑摘要增加 `match_score` /
  `match_status` 字段（图片与 OCR 全文仍绝不进上下文）。
- **手动触发**：API `POST /api/recruiting-operator/resumes/{id}/re-evaluate`（前端按钮）。

## 4. 关联解析严密化（P1/P2）

1. **简历关联职位**：工具层落库前解析 job → jobs：
   - payload 带 job_id → 直接用（校验属于本租户）
   - 只带 job_name → 精确匹配 jobs.job_name：唯一命中 → 关联；0 或多个命中 →
     job_id=NULL + job_name 原文保留，摘要带 warning「未关联职位（请在职位管理核对）」
   - **绝不自动创建职位**
2. **存量回填**：一次性迁移（deploy/db_update.sql + 启动幂等执行）：
   按 resumes.job_name 精确匹配回填 job_id；匹配不上的保持 NULL 由前端提示。
3. **话术只在本职位下**：_resolve_job_script 改为**必须先定位唯一职位**
   （job_name 必传；或租户恰好只有一个职位时可省略）；定位不到职位直接报错列出
   现有职位；定位到职位后只在该职位的 scripts 里找（标题精确→包含兜底不变）。
4. jobs 表加 status / match_threshold / job_requirements 三列（幂等 ALTER）；
   job_requirements 写入校验 Phase 1 为结构校验（键白名单 + 类型，见 §2.1）——
   云端无 BOSS 档位枚举数据源不硬编码，档位值精确性由 Phase 3 使用时
   boss_filter_options 校准 + 既有保底映射兜底。

## 5. 匹配驱动的闭环（SUBAGENT 链路改写）

入口从「反问用户要参数」改为「选职位 → 职位要求自动带出筛选」：

```
用户：「帮我筛选简历」
  ↓ ① 选择交互：boss_jobs_list 列出 status=active 的职位，用户选择（§5.1）
  ↓ ② 筛选参数 = 该职位 job_requirements（experience/educations/salary）
       用户可在此时口头覆盖任意维度（「薪资放到 15-25K」），只改本次不改职位
  ↓ ③ boss_select_job(job_name) 切 BOSS 页面职位 → boss_filter_options 校准档位
       → boss_filter 设筛选（档位不一致时走既有保底映射）
  ↓ ④ 批量读简历入库（payload 带 job_id，硬关联）
  ↓ ⑤ 自动评分（score / summary / key_info）→ ≥阈值 标记 matched
  ↓ ⑥ 仅对 matched 候选人推进沟通：
       打招呼（boss_greet）→ 选【该职位】初次开场话术 →
       填占位符（优先 key_info.highlights，其次 OCR 摘录；证据铁律不变）→
       用户过目 → boss_send_to 真发送 → 收简历 / 继续沟通 / 邀约面试
未达阈值：rejected/borderline 留简历库供人工翻牌，不自动打招呼
```

- SUBAGENT 反问规则改写：**筛选要求不再是必备反问参数**——职位要求齐全时直接执行；
  仅当 job_requirements 三维度全空或用户明确要改时才反问。职位本身也通过选择交互
  确定（取代「口述职位名 + boss_list_jobs 自由文本挑选」的现状）。
- SUBAGENT 硬规则：批量打招呼只面向 match_status=matched（用户点名某人的除外）；
  汇报格式带每人分数（「刘草威 82 ✓ / 何先生 61 ✗」）。
- SCRIPT_NEEDS_FILL 返回升级：`key_info`（结构化，优先）+ `resume_excerpt`（补充）+
  `match_score`；占位符填写优先用 key_info.highlights（评分时已提炼，比现场截断稳）。

### 5.1 选择交互（通用机制，首场景：职位选择）

现状缺口：智能体交互没有「列出选项让用户选」的逻辑（全靠自由文本）。
从职位库选择开始建立，机制做成通用约定，后续场景复用。

**数据层**：新云端只读工具 `boss_jobs_list`（查自家职位库，非 BOSS 页面）：
入参无；返回 `active` 职位数组 `[{job_id, job_name, requirements 摘要, threshold,
话术数, 简历数, 匹配数}]` + 结构化 `options: [{key, label, description}]`
（key=job_id；话术数不在返回内，由前端职位卡展示）。CLI 页面侧的 boss_list_jobs / boss_select_job 照旧负责
「列 BOSS 页面职位 / 切换」（待上云，见 §6 Phase 3），两者职责区分明确。

**交互约定（SUBAGENT prompt）**：
1. 需要用户从清单里选时，调 boss_jobs_list，在回复中渲染**编号列表**：
   `1. PHP开发工程师（Laravel）· 要求 3-5年/本科/10-20K · 简历 12 · 匹配 3`
2. 结尾固定话术：「请回复序号选择职位，或直接说要求我来帮你挑」；
3. 用户回复序号/职位名/自然语言描述均可——序号映射 key，描述则由 agent
   结合 options 的 label/description 判断并复述确认；只有 1 个 active 职位时
   复述确认后直接用，不列单。

**前端（Phase 5）**：assistant 消息携带 options 元数据时，在消息下方渲染
可点击选项按钮（复用 quickPrompts chips 的样式与发送逻辑，点击即发送序号）；
用户手动回复数字同样有效（前端纯增强，不改对话协议）。

**无状态原则**：不在后端存「待选状态」，选择完全靠对话上下文解析——
agent 下一轮收到「2」时从上文 options 取 key。避免引入会话状态机。

## 6. 实施拆分（新会话按此开工，每阶段独立可验收）

| Phase | 内容 | 验收 |
|---|---|---|
| 1 关联严密化 | jobs 加列（status/threshold/requirements，requirements 按 §2.1 结构校验）；resumes 加列（job_id/score/summary/status/key_info）；存量回填迁移（status→active + job_name→job_id 精确匹配）；话术不跨职位（含测试改写：send_script 测试补「多职位时不传 job_name 报错」） | 单测绿 + 回填脚本对库执行成功 | ✅ 2026-08-17 完成（三智能体流程全过，88 测试绿） |
| 2 评分服务 | recruiting_match_service + LLM prompt/JSON 校验/失败不阻塞；detail/batch 工具接入（摘要带分）；re-evaluate API | 集成测试（stub LLM 返回合法/非法 JSON 两路）+ 真机读 1 份简历看到分数 | ✅ 2026-08-17 代码完成（三智能体全过，123 测试绿；真机验收待做） |
| 3 要求驱动闭环 | SCRIPT_NEEDS_FILL 返回 key_info+score；SUBAGENT matched-only 打招呼规则与汇报格式；话术解析必须唯一职位；**筛选参数默认取 job_requirements（用户可口头覆盖，仅改本次）**；boss_list_jobs/boss_select_job 上云（受信白名单 + proxy_tool 注册，补齐一键链路第 1、2 步） | 集成测试 + Web 演示：批量读 → 报分数 → 仅对 matched 走话术确认；演示「改薪资档位只影响本次」 | ✅ 2026-08-17 代码完成（三智能体全过 219 绿；Web 真机演示待做；boss_jobs_list 轻量版由 Phase 4 提前至本阶段实现） |
| 4 选择交互 | 云端工具 boss_jobs_list（active 职位 + options 元数据 + 简历/匹配统计）；SUBAGENT 编号选择约定（§5.1：列表格式/序号解析/单职位直用）；一键链路入口改为选择交互 | Web 演示：说「筛选简历」→ 回复编号列表 → 回复序号 → 无需再口述职位名/要求直接进入筛选 | ✅ 2026-08-17 代码完成（三智能体全过 182 绿；Web 真机演示待做） |
| 5 前端 | 简历库：分数徽标（绿≥阈值/黄50-69/灰未评）+ 关联职位链接 + 关键信息卡 + 重新评分；职位管理：职位卡显示「简历 N · 匹配 M」+ 状态切换（active/paused）+ 阈值/要求编辑（**档位下拉，取自 boss_filter_options**）；对话：options 选项按钮渲染（§5.1 前端增强） | typecheck + 手动验收 | ✅ 2026-08-17 代码完成（三智能体全过；typecheck/build/vitest 绿；UI 手动验收待做；对话按钮为纯增强不做历史持久化） |

## 7. 明确不做

- 不做规则/关键词打分；不在本机 CLI 做 LLM 评分（评分在云端，CLI 保持零 LLM 依赖）
- 不自动创建职位；不因评分失败拒收简历；不自动给 rejected 候选人发婉拒消息（后续可选）
- key_info 不追求全字段必填（简历没写就是 null，宁缺勿编）
- 不做职位别名映射表（job_name 与 BOSS 发布名不一致就让切换报错提示改名）
- 不与 BOSS 页面职位状态做双向同步（paused 仅是我们库的展示/可选性控制）
- 选择交互不引入后端会话状态机（靠对话上下文解析，前端按钮纯增强）
- 面板上「年龄/活跃度/性别」等行不纳入 job_requirements（CLI 本身不支持设置）
