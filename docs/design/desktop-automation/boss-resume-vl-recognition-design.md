# BOSS 简历识别去 OCR 化（GLM-5.3-Flash 多模态）设计

- 状态：v2 设计定稿（2026-09-17 修订：合并评分、取消全文转写、防造假收口），按此修订开发
- 关联：`boss_resume_detail` / `boss_resume_batch` 工具链路；`docs/design/billing/client-billing-integration-design.md`（计费模式）；简历-职位匹配设计 §2/§3（key_info schema 与评分规则，本设计沿用）
- 租户决策记录：
  - 2026-09-17 ①云端切片方案 B；②截图不扣费，VL 解析成功一份扣一份；③OCR 价目全部替换为简历识别费；④`ocr_engine` 回填实际模型名；⑤batch 逐份识别逐份入库
  - 2026-09-17 v2 修订：⑥**识别/评分必须在服务端，客户端只交图**（计费防造假）；⑦**不做全文转写**，直接拿拼接长图让 GLM 打分+总结（转写 ~47s 太慢）；⑧确认原评分在云端服务（非 runtime），合并进本次 VL 调用
  - 2026-09-17 ⑨**姓名核对前置到评分调用**：页面姓名（candidate_name）传入 VL 提示词，模型先读简历姓名；不符直接返回不匹配（不入库不扣费），匹配才继续评分；不匹配打全量日志（防 runtime 点击错位开错人）

## 1. 背景与目标

boss-resume-assistant CLI 发货包捆绑 RapidOCR（ocr-python 便携环境），大量客户机报错，是简历链路最大不稳定源。GLM-5.3-Flash 原生多模态（输入 0.8/输出 2.8 元/M tokens），直接看简历截图即可完成识别与评分。

v2 目标：

1. 客户端整条 OCR 链路下线（已实现），只负责「滚动截图 + 拼接」，**payload 一律不带文本**；
2. **取消全文转写**：拼接长图直接交给 GLM-5.3-Flash，一次调用输出「姓名识别 + 简历总结 + 职位匹配评分 + key_info」结构化 JSON（输出从 ~4400 字降到 ~400 字，预期 47s → 10-20s）；
3. **评分与文本的唯一来源是服务端 VL**，客户端不产生、不传递、不被信任任何文本——计费与数据防造假（§6）；
4. 计费：截图免费；服务端 VL 解析成功一份扣一份简历识别费；原评分 token 计费（recruiting_match）随合并取消。

## 2. 原流程与变化对照

### 2.1 原流程（改造前，两个云端步骤）

```
CLI：滚动截图 → 拼接 → 本地 OCR（RapidOCR/WinRT）→ 文本合并 → 姓名校验
  → payload{candidate_name, ocr_text, images[拼接图]}
  → write_result：按次扣 detail 1.0 / batch 2.0
  → 云端工具层：
      ① create_resume_record：ocr_text + 图片入库
      ② recruiting_match_service.evaluate_and_update（云端服务，非 runtime）：
         读库中 ocr_text（截 3000 字）+ 职位上下文 → 主链路模型（纯文本）
         → JSON{score, match_summary, key_info} → 回写 match_* / key_info 四列
         计费：record_background_llm_usage(source=recruiting_match，按 token)
```

评分触发点（原样保留的两个入口）：工具层入库后自动评 + 前端「重新评分」按钮
（POST /api/recruiting-operator/resumes/{id}/re-evaluate）。此结论回答租户问题③：
**评分一直是云端智能体侧服务做的，runtime/客户端从未参与评分**。

### 2.2 新流程（v2）

```
CLI：滚动截图 → 拼接 → payload{candidate_name, job_name, images[拼接图]}   ← 无任何文本
  → write_result：detail/batch 价目 0，仅落终态（截图免费）
  → 云端工具层（每份，逐份推进）：
      ① Pillow 切拼接长图 → 带重叠横带
      ② 一次 VL 调用（chat_direct 指定 zhipu/GLM-5.3-Flash）：
         输入 = 横带图 + 期望姓名（candidate_name，决策⑨）+ 职位上下文
         输出 = JSON{name_seen: 图中姓名栏原文, resume_summary, score, match_summary, key_info}
         （无职位关联时 score/match_summary 为 null，总结与 key_info 照常输出）
      ③ 防张冠李戴（决策⑨，评分第一道门）：
         服务端 resume_name_matches(candidate_name, name_seen)（≤1 字容差）判定：
         不符 → 直接返回 RESUME_NAME_MISMATCH，不入库不扣费，
         打全量日志（tenant/device/invocation/candidate_name/name_seen，供区分
         「识别误读」与「真点错人」）；相符 → 才继续入库与计费
      ④ 入库：图片 + resume_summary + key_info + match_*（按阈值判 matched/rejected/unmatched）
         （ocr_text 不再生成，列保留：历史数据照旧、手工录入路径照旧）
      ⑤ 扣简历识别费（VL 调用成功即扣，score 缺席不算失败）
      ⑥ push 进度，下一份
```

### 2.3 变化清单

| 项 | 原 | 新 |
|---|---|---|
| 文本来源 | 客户端 OCR → payload.ocr_text（被云端信任） | **无**。文本唯一来源=服务端 VL（§6 防造假） |
| 评分输入 | 库中 ocr_text 截 3000 字 | 长图切片（VL 直接看图） |
| 评分调用 | 主链路模型独立一次（token 计费 recruiting_match） | **合并进 VL 调用**（chat_direct 指定多模态通道） |
| 简历全文 | ocr_text 入库、前端展示 | **不再生成**；ocr_text 列保留（历史/手工录入）；前端 v-if 兼容已确认 |
| 简历总结 | 无（仅 match_summary ≤100 字评分理由） | 新增 `resume_summary`（≤200 字人物总结），新列 |
| 计费 | 按次 1.0/2.0 + 评分 token 两笔 | 截图免费 + 简历识别费 1.0/份（VL 成功即扣）一笔 |
| 单份耗时 | ~30s（截图+本地 OCR） | ~30s 截图 + ~10-20s VL ≈ **40-50s** |
| 重新评分 | 库中文本 → 主链路模型 | 图（库中 file 读回）→ VL；无图回退文本（历史数据） |

## 3. 客户端（已实现，v2 不再改动）

滚动截图 + 拼接管线保留；RapidOCR/WinRT/ocr-python 捆绑包/引擎解析/姓名校验已删（2026-09-17 完成，
TS 363 用例绿）。payload 契约不变：`candidate_name`（必填）+ `job_name` + `images` + 截图元信息。

## 4. 云端设计

### 4.1 VL 服务 `src/services/resume_vl_service.py`（v2 改造）

- `resolve_model_spec(model_param)`：不变（provider/model 语法 + `settings.resume_vl.allowed_models`
  白名单，缺省 `zhipu/GLM-5.3-Flash`）。
- `slice_stitched_image(png_base64)`：不变（带高 1800 / 重叠 400 / ≤2400 整图单发 / ≤10 带）。
- `recognize_resume_text`（全文转写）→ **改为 `evaluate_resume(bands, candidate_name, job_ctx, model_param)`**：
  - 输入：横带图 + **期望姓名 candidate_name（决策⑨，提示词明示「先核对简历姓名」）** +
    职位上下文（沿用 recruiting_match_service 的 `_load_job_context` 产物：
    requirements 或 job_name+备注+初次开场话术隐含要求）；
  - 一次 `llm_gateway.chat_direct(provider, model, ...)` 调用，multimodal content = 提示词 +
    逐带 image_url；提示词要求：**第一步读出简历姓名栏原文输出 name_seen**；逐带阅读、重叠去重；
    输出 JSON：
    `{"name_seen": "简历姓名栏原文", "resume_summary": "≤200字人物总结", "score": 0-100|null, "match_summary": "≤100字评分理由|null", "key_info": {8 字段同匹配设计 §2}}`；
  - **铁律沿袭**：仅依据图中可见内容，不可见填 null/[]，严禁编造；无职位上下文时 score/match_summary 输出 null（总结与 key_info 照常）；
  - 解析清洗：复用 recruiting_match_service 的 `_parse_json`/`_extract_score`/`_clean_key_info`
    （提为共用或 import，不复制两份）；name_seen 必须非空字符串（空 = 解析失败走重试）；
  - 超时/异常/JSON 不合法重试 1 次；仍失败抛 `ResumeVLError`（fail-loud，不扣费）；
  - **服务端判定而非模型判定**：模型只负责读 name_seen，是否「匹配」由服务端
    `resume_name_matches(candidate_name, name_seen)` 决定（确定性比较，不依赖模型判断力）；
  - **不再输出全文文本**：v1 已实现的 `recognize_resume_text` 删除。
- `resume_name_matches`：保留（candidate_name vs name_seen，Levenshtein ≤1 容差）。

### 4.2 工具层（proxy_tool.py，v2 改造）

- `BossResumeDetailTool.execute`（CLI 成功后）：
  1. **撤销 v1 的「payload 带 ocr_text 直接入库」信任路径**：CLI 来源 payload 一律忽略其携带的
     `ocr_text` 字段（旧客户端升级期：旧客户端仍带文本 → 云端照常走 VL 评分入库，旧文本字段
     丢弃并记 warning 观测日志；手工 Web 录入路径 `/resumes` POST 不受影响，那是租户自己的操作）；
  2. 切片 → VL 评分（重试 1 次）；
  3. 姓名核对（决策⑨）：`resume_name_matches(candidate_name, name_seen)` 不符 →
     `RESUME_NAME_MISMATCH` 失败 + 全量日志（candidate_name/name_seen/device/invocation），
     不入库不扣费不打分；
  4. 入库（`create_resume_record_from_tool_result`：images + resume_summary；`ocr_text` 不回填）；
  5. `_update_match_fields` 回写 match_*（按职位阈值判状态，阈值/隐含要求逻辑复用 recruiting_match_service）；
  6. 扣简历识别费（VL 成功即扣，入库失败不退——成本真实发生；与 v1 决策「入库成功才扣」的差异
     在此明确：评分成功 = 服务已交付）；
  7. 摘要回 LLM：resume_id/candidate_name/match_score/match_status/summary 预览，图片与全文不进上下文。
- `BossResumeBatchTool`：逐份串行上述 1-6（识别成功一份入库一份记账一份），单份失败记 failures 继续，
  progress 逐份推送（决策⑤）。
- `re-evaluate` 端点（前端重新评分）：改为走 VL——从库中 images 读回图（file 路径）→ 切片 → VL →
  回写 resume_summary/match_*/key_info；历史记录无图时回退旧文本路径（recruiting_match_service 原逻辑
  保留为 fallback），两者皆无 → skipped。

### 4.3 计费

| 项 | 值 | 说明 |
|---|---|---|
| boss_resume_detail / boss_resume_batch 按次费 | **0** | 截图免费（决策②） |
| 简历识别费 `resume_recognition_price` | **1.0 积分/份** | VL 调用成功即扣（决策②/v2 明确：评分成功=服务交付，入库失败不退）；台账 tool_name=`boss_resume_recognition` |
| 评分 token 计费（recruiting_match） | **取消** | 合并进 VL 调用，同一次调用不产生两笔账 |

成本依据：GLM-5.3-Flash 输入 0.8/输出 2.8 元/M tokens；单份 ~6-8 横带 + ~500 字输出 ≈ **0.01-0.015 元/份**，
1.0 积分/份毛利充足。扣费复用 `ClientUsageLogDB.record_tool_usage`（弹层自愈直记模式）；
余额预检：两简历工具覆写 `_tool_credit_price()` 返回识别费单价（已实现）。

### 4.4 独立解析接口（形态随 v2 调整）

`POST /api/local-tools/runtime/resume/parse` → **`POST /api/local-tools/runtime/resume/evaluate`**：

- 入参：`image`/`images` + `candidate_name`（**必填**，传入提示词做姓名核对，决策⑨）+
  `job_name`/`job_id`（可选，给了才评分）+ `model`（白名单）；
- 流程：鉴权 → 入参/白名单校验 → 切片 → VL 评估 → 姓名比对（不符 422 `RESUME_NAME_MISMATCH` +
  全量日志，不扣费）→ 按份扣简历识别费 → 返回
  `{name_seen, resume_summary, score, match_summary, key_info, model, bands, billing}`；
- 计费与工具层同科目同价（VL 成功即扣）；服务端识别与计费满足决策⑥；
- 接口与工具层共用 `evaluate_resume` 服务，区别只是 HTTP 壳。

## 5. 数据库与前端影响

- `bs_recruiting_operator_resumes` **新增 `resume_summary` TEXT NULL 列**（DB 规范：变更记录见
  database_dev.md；init 建表语句与存量 ALTER 幂等迁移）；
- `ocr_text` 列保留：历史记录照旧展示；新记录不填（NULL）；手工录入路径照旧；
- 前端 ResumeDetail.vue：ocr_text 区块已有 v-if 兼容（空则隐藏）；新增 resume_summary 展示块；
  ResumeLibrary 列表/`recruitingDisplay.ts` 不变（match_score/match_status 照常）；
- 工具摘要 `ocr_char_count` 改为 `summary_len`（前端未消费该字段，仅 LLM 摘要用）。

## 6. 防造假设计（决策⑥）

威胁：客户端若能提交「文本」或影响评分结果，租户可伪造简历/绕过计费。

- **文本不可信**：CLI payload 不携带也不信任任何文本字段（`ocr_text` 直接忽略 + warning 观测）；
  姓名/总结/评分全部来自服务端 VL 看图。
- **计费不可绕**：识别费在服务端工具链路内扣（VL 成功即扣），不经客户端上报；客户端唯一的
  「抵抗」方式是不交图/交坏图 → 结果是自己拿不到评分数据，无利可图。
- **张冠李戴防护（决策⑨，第一道门）**：runtime 点击错位开错人是已知的真实事故形态（0.2.9 实证）。
  candidate_name 传入 VL 提示词，模型读出简历姓名栏原文（name_seen），服务端确定性比对（≤1 字容差）：
  不符 → 直接返回 `RESUME_NAME_MISMATCH`，不入库不扣费不打分；**全量日志**必含
  tenant/device/invocation/candidate_name/name_seen/bands——name_seen 让排障能区分「识别误读」
  （重试可救）与「真点错人」（流程问题），长图本体在 invocation result_json 可人工核对。
- 图片真实性不做密码学校验（截图来自受控 Chrome 环境，超范围）。

## 7. 已实现代码的处置（v1 → v2 差异）

| v1 已实现 | v2 处置 |
|---|---|
| 客户端去 OCR 全部改动 | **保留**，不再动 |
| `resume_vl_service.slice_stitched_image` / `resolve_model_spec` / `resume_name_matches` | **保留** |
| `recognize_resume_text`（全文转写） | **删除**，换 `evaluate_resume`（评分 JSON） |
| `gateway.chat_direct`（指定通道直连） | **保留**（v2 核心依赖） |
| 接口 `/runtime/resume/parse`（返回全文） | 改造为 `/runtime/resume/evaluate`（返回评分 JSON，入参加职位上下文） |
| 工具层「payload 带 ocr_text 兼容直入库」 | **撤销信任**（§6），旧客户端字段丢弃 + warning |
| 工具层 VL 失败 fail-loud / batch 逐份推进 / 预检覆写 | **保留**，识别调用换 `evaluate_resume` |
| 计费「入库成功才扣」 | 改为「VL 成功即扣」（§4.3，差异已明确） |
| recruiting_match_service | 保留为 re-evaluate 的文本 fallback；自动评分路径不再调用 |

## 8. 真机验证结论（v1 阶段已完成，管线部分继续有效）

- CLI 实读「王宣广」：4 段拼接 756×3500，全程无 python/RapidOCR 依赖 ✓
- GLM-5.3-Flash 真图调用链路（chat_direct）通：转写 47s（v2 改评分输出后预期大幅下降，需复测）✓
- 端到端：假 token 401 / 白名单外 422 / 姓名不符 422 不扣费 / 成功扣 1.0 余额 10→9 / 台账落行 ✓
  （接口路径与参数随 v2 变化后需复测）

## 9. 风险与回退

- **VL 评分质量**：图面评分 vs 文本评分的差异需真机比对验证（同一批简历新旧 score 抽样对比）；
  不合格可回退 v1 转写路径（代码保留在 git 历史）。
- **无职位关联简历**：总结/key_info 有、score 为 null——前端列表 match 列显示「未评分」（已有文案）。
- **回退**：`resume_recognition_price=0` 停扣；服务端按 §7 表可整体回退 v1 转写形态。
- **GLM 图片数上限**：带数 ≤10 保护不变。
