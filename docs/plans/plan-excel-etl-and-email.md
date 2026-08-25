# 开发计划：#63 多源脏 Excel → 标准模板 LLM 抽取填充 + #64 邮件工具整改

> 2026-08-19。设计/决议/测算见 [调研+决议](../tools/excel/excel-etl-gap-analysis.md)（D1~D24、Q1~Q3 已拍板）、[邮件审查](../tools/email/email-tool-audit.md)、[点数测算](../tools/excel/excel-etl-points-estimation.md)。
> 开工核对结论：设计无歧义；新发现 1 个必须补的缺口——**skill 子进程 LLM 调用零计量**（存量 skill 漏账，本管线为公用云计费必须解决）；`_default_llm` 已覆盖 qwen/zhipu/deepseek 且 deepseek 支持关思考，无需改。

## Phase 0 — 邮件工具整改（#64 第一步，约 1~1.5 天）

改动（`src/tools/email/` 重构 + `src/db/email_credential.py`）：
1. 拆 `email_lib.py`（IMAP/SMTP 连接、HEADER 先行两段式 fetch、正文/附件解析纯函数）+ 工具薄壳。
2. **三合一 `email_process`**（action: send|read，download_attachments 留 Phase 3）：确定性分发、不建 LLM 路由；folders 进 read 返回字段；删除 EmailListFoldersTool。
3. P0 修复：`asyncio.to_thread` 包裹同步 IO、连接 timeout、finally 关闭、两段式过滤（先 `BODY.PEEK[HEADER]` 再拉全信）。
4. P1 合规：`sanitize_error`、删调试残留/死代码、HTML 正文 fallback（strip 标签）、body_preview 参数化（默认 500）。
5. 修 `EmailCredentialDB.upsert` 重绑丢配置 bug（DELETE 分支不再早退）。

验收：email 全量单测绿 + 新增回归（HTML 正文、两段式过滤、upsert 解绑→重绑→读到配置、action 分发、folders 字段）；`tests/test_email_tool.py` 旧引用迁移到新工具名。

## Phase 1 — M1 渲染层 + 黄金夹具（约 1 天）

1. `src/tools/excel/excel_reader.py` 加 `render_llm_view(file_path) -> {sheets:[{name, text, row_count}]}`：值按 number_format/is_date 渲染、空行压缩、单元格 300 字截断、markdown 表输出（D5 契约）。
2. `mask()/unmask()` 脱敏钩子（占位符往返最简实现：身份证/手机号 → `[ID_n]/[TEL_n]`，映射表随任务生命周期）。
3. `tests/fixtures/excel_etl/`：5 个样本文件 + **人工标注黄金 JSON（14 条）**——本 Phase 人工投入重点，逐条对照原文件核字段。
4. 单测逐脏点断言：两行表头/多段重复表头/标题前置行/备注页脚/Excel 序列日期/重复列名/8 险种日期归并的渲染输出。

验收：渲染输出与黄金夹具逐 sheet 一致；脱敏往返幂等（mask→unmask 还原全等）。

## Phase 2 — M3 抽取 + M4 校验修复 + 计量（约 2 天）

1. `src/tools/excel/excel_extract.py`：
   - `extract_to_schema(rendered_text, schema) -> rows`：分块（50 行/24k tokens，表头随块重复）、`_default_llm` 同步调用（关思考、temperature 0）、`_extract_json` 容错、单块重试 1 次再败任务级失败。
   - 校验器（D10 分级）+ 修复回路（error 行回喂 ≤2 轮，仍败进人工清单）。
   - 同人多条分组（D12：不合并、标 `_source`、备注追加标记）。
2. schema 资产：skill `assets/schema.json`（18 字段 + 约定 + template_header 别名）+ 指纹匹配纯函数；不匹配触发一次重抽。
3. **计量接线（新发现缺口，本次必须）**：skill_executor 注入 `AID_TENANT_ID`/`AID_SESSION_ID` 环境变量；新增 `record_skill_llm_usage()`（显式参数版，子进程直写 usage 记录并扣积分，参照 `record_background_llm_usage`）；抽取/修复/schema 重抽全部计量（stage 标注）。

验收：样本全量抽取与黄金 JSON 字段级全等（含 `_source`）；坏数据注入测试（错身份证→人工清单）；计量记录落库且积分扣减正确。

## Phase 3 — M5 skill 编排 + 邮件附件 + 端到端（约 2 天）

1. `src/skills/excel-to-template-1.0.0/`：SKILL.md（触发词/双入口说明）+ `scripts/pipeline.py`——文件入口（会话附件/zip 解包）与邮件入口（email_process read + D23 两级附件判定 + download_attachments 落盘 skill_ws + D24 UID 水位 marker）。
2. `email_process` 补 `download_attachments` action（uid/filenames/download_dir，25MB 上限，RFC2231 中文文件名）。
3. 纯代码填充（D15：克隆模板→指纹校验→列绑定写入→样式复制→终检）+ 校验报告 .md 生成（D16/D17 命名）+ cp 双文件交付。
4. e2e：zip 样本会话入口全链路（模板识别→抽取→报告→交付两文件）；邮件入口用 mock IMAP 夹具覆盖判定与下载。

验收：14 条全部正确写入、人工清单/同人多条提示正确、报告完整、积分账单与测算同量级（25~50 积分/百条折算）。

## 不做（scope 控制）

- 存量 skill（travel-quote 等）计量回填——P2 另立项，本计划只建可复用模式。
- 邮件轮询/定时触发、发送附件、IMAP/SMTP 凭据分离。
- 抽取结果同指纹缓存复用（P2）。

## 流程

非平凡多文件改动，全程走三智能体流程（开发自测 → 独立测试回归 → CodeReview → 主控终检），每个 Phase 单独一轮。
