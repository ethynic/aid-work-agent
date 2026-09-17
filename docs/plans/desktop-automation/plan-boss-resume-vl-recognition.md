# BOSS 简历识别去 OCR 化 开发计划

- 设计文档：[boss-resume-vl-recognition-design.md](../../design/desktop-automation/boss-resume-vl-recognition-design.md)（**v2 修订版**，2026-09-17：合并评分/取消全文转写/防造假收口）
- ideas 条目：20260917-1422（docs/ideas.md 工具分区）
- 流程：三智能体（开发 → 测试 → CodeReview）+ 主控者真机验证

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | v1 云端 VL 服务 + 工具层接入 + 客户端去 OCR + 计费迁移 | ✅ 完成（2026-09-17） | 云端 628 + 集成 37 + TS 363 全绿；真机 CLI 实读/真图 GLM 调用/端到端扣费台账均验证通过 |
| Phase 2 | v2 改造：evaluate_resume（评分 JSON 合并）替换全文转写 + 工具层接入 + ocr_text 信任撤销 | ✅ 完成（2026-09-17） | 姓名门（name_seen vs candidate_name，≤1 字容差）+ 全量日志；识别费姓名门通过即扣；job_ctx 最小上下文兜底（带职位名即评） |
| Phase 3 | v2 接口改造 /resume/evaluate + re-evaluate VL 化 + resume_summary 列 + 前端展示 | ✅ 完成（2026-09-17） | 接口 candidate_name 必填 + 职位上下文；re_evaluate 图优先/无图回退文本；resume_summary 列幂等迁移；详情页 AI 总结块 |
| Phase 4 | v2 测试（单测改写 + 回归 + 启动安全） | ✅ 完成（2026-09-17） | 服务 26 + 工具 14 + 接口 9 + 回归 713 + 集成 37 全绿 |
| Phase 5 | v2 真机验证（评分质量对比 v1/延迟复测/计费台账） | ✅ 完成（2026-09-17） | 真图评估 11.1s（v1 转写 47s → 提速 4 倍）；score=68 理由准确（实习/方向偏差）；错误姓名 10.1s 被姓名门拦截；端到端 401/422/扣 1.0/余额 10→9/台账落行 |
| Phase 6 | CodeReview + 主控者提交前验证 | ✅ 完成（2026-09-17） | CR 修复 P0（客户端可携 text/ocr 别名绕过文本丢弃伪造全文 → 丢弃覆盖全部别名）+ P1 两项（工具路径接住 ResumeVLModelError 防整批中断；缺省模型免白名单防配置不自洽全挂）+ 前端死键；回归 713 + 37 全绿；待用户明确提交 |

## v2 需求背景（租户 2026-09-17 决策）

1. **识别/评分必须在服务端**，客户端只交图——计费防造假：客户端不产生/不传递/不被信任任何文本；
2. **取消全文转写**（单次 ~47s 太慢，输出 4400 字是大头）：拼接长图直接给 GLM 打分+总结，输出 ~400 字
   JSON，预期 10-20s；
3. 确认原评分在云端服务（recruiting_match_service，工具层入库后自动评 + 前端重评按钮，runtime 从未
   参与评分）→ 合并进本次 VL 调用，原评分 token 计费（recruiting_match）取消；
4. **姓名核对前置（决策⑨）**：candidate_name 传入 VL 提示词，模型输出 name_seen（简历姓名栏原文），
   服务端确定性比对（≤1 字容差）；不符直接返回不匹配（不入库不扣费不打分）+ 全量日志
   （tenant/device/invocation/candidate_name/name_seen），防 runtime 点击错位开错人。

## Phase 2 云端服务与工具层（v2 核心）

- `resume_vl_service`：
  - 删 `recognize_resume_text`（全文转写），新增 `evaluate_resume(bands, candidate_name, job_ctx, model_param)`：
    提示词含期望姓名（先读姓名栏）+ 职位上下文，一次 chat_direct 输出
    `{name_seen, resume_summary(≤200字), score(无职位 null), match_summary, key_info}`；
    解析清洗复用 recruiting_match_service 的 `_parse_json`/`_extract_score`/`_clean_key_info`（提共用）；
    name_seen 空 = 解析失败走重试；
  - `slice_stitched_image`/`resolve_model_spec`/`resume_name_matches` 保留不动。
- 职位上下文：复用 `_load_job_context`（requirements 优先 / job_name+备注+初次开场话术隐含要求）；
  无职位 → score/match_summary=null，总结照常。
- `proxy_tool`：
  - 撤销「payload 带 ocr_text 直接入库」信任路径（CLI 来源一律忽略该字段 + warning 观测日志；
    手工 Web 录入不受影响）；
  - detail/batch 改调 `evaluate_resume`；姓名门：`resume_name_matches(candidate_name, name_seen)`
    不符 → `RESUME_NAME_MISMATCH` + 全量日志（candidate_name/name_seen/device/invocation），
    不入库不扣费不打分；
  - 入库：images + resume_summary（新列），ocr_text 不回填；`_update_match_fields` 回写 match_*
    （阈值/状态判定复用现有函数）；
  - 计费时机改「VL 成功即扣」（姓名门通过后入库前扣；入库失败不退，评分成功=服务交付）。

## Phase 3 接口 / 重评 / 数据库 / 前端

- DB：`bs_recruiting_operator_resumes` 加 `resume_summary TEXT NULL`（init 建表 + 幂等 ALTER + 变更记录）。
- 接口：`POST /runtime/resume/parse` → `/runtime/resume/evaluate`（入参加 job_name/job_id 可选；
  出参评分 JSON；计费同工具层）。
- re-evaluate 端点：优先库中图（file 读回 → 切片 → VL）；无图历史记录回退旧文本路径
  （recruiting_match_service 保留为 fallback）；自动评分路径不再调用它。
- 前端：ResumeDetail.vue 加 resume_summary 展示块（ocr_text 区块已有 v-if 兼容不动）；其余不动。

## Phase 4 测试

- 服务层单测：evaluate_resume JSON 解析/重试/name_seen 为空按失败/无职位 score=null/提示词含期望姓名；
  切片保留原用例。
- 工具层单测：ocr_text 信任撤销（旧客户端字段丢弃 + warning）、姓名门（不符不入库不扣费 + 日志字段）、
  VL 成功即扣（入库失败不退）、batch 逐份、旧客户端无图路径。
- 接口单测：evaluate 形态入参出参/candidate_name 必填/白名单/失败不扣费。
- 回归：local_tools + services + 集成 + TS 全量；启动安全 import 检查。

## Phase 5 真机验证

1. BOSS 实读一份简历 → evaluate 全链路：评分 JSON 合理性、延迟复测（预期 ≤20s/份）；
2. 评分质量：同批简历抽样对比旧文本评分 vs 新图评分（score 偏差与理由质量，租户验收）；
3. 计费台账：成功扣 1.0/失败不扣/余额变动核对；
4. 前端：详情页 resume_summary/评分/图片展示正常，旧记录（有 ocr_text）照常。

## Phase 6 CodeReview + 提交前验证

- CR 重点：ocr_text 信任撤销的遗漏点（grep 全部 ocr_text 消费方）、计费时机变更的对账口径、
  VL JSON 解析健壮性、re-evaluate 双路径。
- 主控者终检后按 Git 规范提交（等用户明确说提交才提交）。

---

## 历史归档：v1 阶段（已完成，代码在库）

- Phase 1 v1：`resume_vl_service`（切片 1800/400/≤10 带 + recognize_resume_text 全文转写 +
  resume_name_matches 移植）+ `gateway.chat_direct`（指定通道直连）+ 工具层接入 + 计费迁移
  （截图免费/识别费 1.0/份/预检覆写）+ 客户端去 OCR（RapidOCR/WinRT/ocr-python 全下线）。
  测试：云端 628 + 集成 37 + TS 363 全绿。
- 真机验证 v1：CLI 实读「王宣广」756×3500 无 OCR 依赖；真图 GLM 转写 47s/4561 字；端到端
  401/422/姓名不符不扣费/成功扣 1.0 余额 10→9/台账落行。
- v2 差异与处置见设计文档 §7（保留：客户端改动/切片/白名单/chat_direct/fail-loud/逐份推进；
  删除：全文转写；撤销：ocr_text 信任；改造：接口与 re-evaluate；变更：计费时机）。
