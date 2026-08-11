# M0.0 删除清单：BOSS CLI 旧流程逐文件清单

> 关联计划：[plan-recruiting-cli-agent-integration.md](plan-recruiting-cli-agent-integration.md) M0.0 / M0.1
>
> 方法：从 `src/cli/index.ts` + 7 个保留命令（filter / greet / goto / accept / reject / interview）出发递归构建静态 import 图（含 type import 与 dynamic import），不在保留集中的文件进入删除候选并逐一核实引用方。

## 1. 保留集（禁止删除）

**CLI 层**
- `src/cli/index.ts`（需编辑：删 6 个旧 case + 第 23 行 `import type { Conclusion }`）
- `src/cli/args.ts`
- `src/cli/commands/{filter,greet,goto,accept,reject,interview}.ts`（filter.ts 需去掉 `--probe` 分支）

**共享底层（7 个成功操作依赖，禁删）**
- `src/main/cdp/`：CdpGateway.ts、CdpSocket.ts、audit.ts（仅 AuditWriter 类型；DbAuditWriter 实现剥离）、methodPolicy.ts
- `src/main/boss/`：domSnapshot.ts、FilterSetter.ts、GreetExecutor.ts、PageNavigator.ts、ResumeConsentExecutor.ts、ChatRejectExecutor.ts、InterviewDemoExecutor.ts
- `src/main/actions/reasonMapping.ts`（ChatRejectExecutor 引用 REJECT_REASON_OPTIONS；死导出 mapRejectReason 顺手清理）
- `src/main/input/WinMouseClicker.ts`（运行时调用 scripts/win-click.ps1）
- `src/main/chrome/ChromeAttacher.ts`（DEFAULT_CDP_PORT 等；run 专属死导出顺手清理）

**scripts**
- `scripts/run-tests.mjs`、`scripts/win-click.ps1`（禁删）
- 手工工具保留：`live-cdp-check.mjs`、`locate-text.mjs`、`snap-shot.mjs`（引用保留模块的调试工具）

**测试（11 个）**
`cdp-audit`、`cdp-gateway`、`cdp-method-policy`、`cdp-socket`、`filter-setter`、`greet-executor`、`chat-reject-executor`、`interview-demo-executor`、`page-navigator`、`resume-consent`、`win-mouse-clicker`

## 2. 删除集

### 2.1 CLI 旧命令与专属配套
| 文件 | 理由 |
|---|---|
| `src/cli/commands/run.ts` | 旧命令入口 |
| `src/cli/commands/ask.ts` | 旧命令入口 |
| `src/cli/commands/chat.ts` | 旧命令入口 |
| `src/cli/commands/review.ts` | 旧命令入口 |
| `src/cli/commands/export.ts` | 旧命令入口 |
| `src/cli/commands/audit.ts` | 旧命令入口 |
| `src/cli/jobConfig.ts` | run 专属 |
| `src/cli/progress.ts` | run 专属 |
| `src/cli/stdinCommands.ts` | run 专属 |
| `src/cli/reviewReport.ts` | review 专属 |
| `src/cli/envLoader.ts` | 仅 ask/chat 用于加载 DeepSeek key |
| `src/cli/cliRuntime.ts` | 装配旧筛选流水线；`defaultDataDir`（5 行）内联进 index.ts 后整体删除 |

### 2.2 src/main 旧筛选流水线
`workflow/`（ScreeningSession、candidateEnumerator、hardRules）、`screening/`（ScreeningEngine、DeepSeekLlmProvider）、`storage/`（sessionStore、jobStore、reviewStore、auditStore）、`ocr/`（OcrProvider、TesseractJsProvider）、`actions/`（ActionPlanner、ActionStore、PageActionExecutor）、`export/exporter.ts`、`image/LongScreenshotStitcher.ts`、`normalize/ResumeNormalizer.ts`、`boss/`（ButtonLocator、ListSnapshotParser、DetailCapture、DetailCloser、ChatOrchestrator、NlFilterTranslator）

### 2.3 db/ 源码目录
`db/client.ts`、`db/migrations.ts`、`db/schema.ts`、`db/seed.ts` — 仅旧链路使用；`tsconfig.main.json` include 同步清理。

### 2.4 测试（24 个）
`action-planner`、`action-store`、`candidate-enumerator`、`chat-orchestrator`、`chrome-attach`（测 cliRuntime 门禁，属 run 链路）、`cli-job-config`、`cli-rendering`（parseArgs 用例重写进新测试）、`cli-review-override`、`cli-review-report`、`db-crud`、`db-schema`、`deepseek-provider`、`detail-closer`、`exporter`、`hard-rules`、`list-snapshot-parser`、`long-screenshot-stitcher`、`nl-filter-translator`、`ocr-provider`、`page-action-executor`、`resume-normalizer`、`screening-engine`、`screening-session`、`storage-stores`、`tesseract-provider`，以及手工文档 `manual-e2e-phase7.md`、`manual-e2e-phase8.md`。

### 2.5 scripts 探针
`probe-filter-edu.mjs`、`probe-interview-{date,form,pick,typing}.mjs`、`probe-reject-button.mjs`、`win-screenshot.ps1`（无任何代码引用）。

### 2.6 examples / 数据文件
- `examples/example-job.yaml`（run --job 示例，无代码引用）
- `chi_sim.traineddata` / `eng.traineddata`（OCR 语言缓存，Tesseract 删除后无用；git 未跟踪，从工作区移除但不提交）
- `data/` 目录为 gitignore 的运行时产物，不在 git 删除范围，提示用户本地自行备份后清理。

### 2.7 npm 依赖
删除：`better-sqlite3` + `@types/better-sqlite3`（cdp/audit.ts 的 DbAuditWriter 一并剥离后无引用）、`js-yaml`、`pngjs` + `@types/pngjs`、`tesseract.js`。
保留：`ws` + `@types/ws`（CdpSocket 使用）。

## 3. 已决策的处理点

1. **cliRuntime.ts**：`defaultDataDir` 内联进 index.ts，其余随旧流水线删除；index.ts 中 `--data-dir`/`--exports-dir` 死参数一并清理。
2. **cdp/audit.ts**：剥离 `DbAuditWriter` 类（只被 cliRuntime 用），保留 `AuditWriter` 接口与内存/文件实现。
3. **chrome-attach / cli-rendering 测试中覆盖保留模块的用例**（ChromeAttacher、parseArgs）：重写到新测试文件，不直接丢弃覆盖。
4. **死导出顺手清理**：reasonMapping.mapRejectReason、ChromeAttacher 的 diagnoseAttachError/chromeLaunchCommandHint/probeChromeDebugEndpoint。

## 4. 基线记录

- 删除前 `npm run build`：✅ 通过（tsc -p tsconfig.main.json，0 错误）
- 删除前 `npm test`：✅ 287/287 通过，0 失败（duration ~8.2s）
- 后端（aid-work-agent Python）基线：M0.3 开发前在对应阶段记录。
