# BOSS CLI 筛选面板 VIP 适配 + 操作性能埋点

## 背景与目标

两个问题驱动本任务（2026-09-28 用户提出）：

1. **VIP 筛选面板适配**：BOSS VIP 账号的筛选弹窗含大量 VIP 独享筛选行，我方可设置的行
   （经验要求/学历要求/薪资待遇）与「确定」按钮被排到弹窗可视区下方，弹窗自身可滚动。
   现有 `FilterSetter` 假定目标行可见（DOMSnapshot 能取到 bounds 即点击），目标行在弹窗
   滚动区外时 Win32 点击落到错误位置 → 徽章校验失败或误点。需要：点击前把目标滚进
   弹窗可视区。
2. **性能埋点**：要优化 boss 操作执行速度，需先有分步耗时数据。现状只有 MCP 层整工具
   `elapsed_ms`（`mcp/server.ts`）与 operation 失败日志（`bossContext.logOpFailure`），
   没有步骤级耗时（connect/面板开/清除/逐选项点击/确定/校验、Win32 点击、snapshot、
   固定 sleep）。需要：每次运行输出一条可解析的步骤耗时汇总日志，供后续数据驱动优化。

## 改动范围（clients/boss-resume-assistant）

### Phase 1：性能埋点

- 新增 `src/main/perf.ts`：`PerfCollector`（按名称聚合 count/total_ms/max_ms，
  `time(name, fn)`、`summaryLine()`）。只记元数据与毫秒数，不记页面内容/坐标/文本
  （与 `logOpFailure` 脱敏口径一致）。
- `runBossOperation`（`bossContext.ts`）：
  - 首参从 `kind` 改为 `{ kind, name }`（TS 编译期暴露全部调用点，机械同步 19 个 operation）。
  - 每次 run 创建 collector；计时 `connect`（sessionFactory）、`body`、`close` 三段。
  - body 返回后对 session 原语（snapshot/click/clickBrowse/clickAndType/mouseWheel/
    pressEscape/captureFullpage/getUrl/clearInput/pageNavigate）逐个包装计时
    （包装发生在 runBossOperation 内，测试注入的 fake session 同样被覆盖）。
  - body 回调签名追加第三参 `perf`（可选，旧测试 2 参 body 不受影响）。
  - 结束时（成功/失败都）向 stderr 输出单行：
    `[boss-perf] tool=<name> run_id=<id> ok=1 total_ms=… connect_ms=… body_ms=… close_ms=… snapshot{n=9,total_ms=1520,max_ms=300} win32:click{n=4,…} …`
    （stderr 与 `[boss-mcp]`/`[boss-op]` 同通道，经 providerManager 落 runtime.log）
- `bossFilter.ts` / `bossFilterOptions.ts`：给 `FilterSetter` 注入
  - `sleep: (ms) => perf.time(\`sleep:${ms}\`, …)`（固定等待是首要优化对象，必须可见）
  - `step?: (name, fn) => perf.time(\`filter:${name}\`, fn)`（panel:open / panel:clear /
    panel:option / panel:confirm / panel:verify / probe:describe）
- `FilterSetterDeps` 增加可选 `step`，缺省恒等透传。

### Phase 2：VIP 面板滚动适配（FilterSetter）

- `FilterSetterDeps` 增加可选 `mouseWheel(x, y, deltaY)`（CDP 浏览类滚动，同
  bossAcceptResume 会话列表滚动通道，不占真实鼠标）。
- 新增 `ensureHitVisible(snap, hit)`：
  - 可视下界 = min(根文档视口高, 面板容器 bounds 底)（容器沿用 `panelContainerByDoc`
    的 LCA；取不到容器时退化为仅视口判定）；目标中心 y + 边距超出即需滚动。
  - 滚动点取面板容器可视区内部（x 取行标签列，y 取可视区中下部），deltaY =
    目标超出量 + 200 缓冲；滚后 sleep ~400ms 再 fresh snapshot 重定位，最多 3 次，
  仍不可见 fail-loud `FilterSetError`。
- 接入点：apply() 每个选项点击前、clear()/apply() 的「清除」「确定」按钮点击前。
  未注入 `mouseWheel` 且目标越界 → 直接 fail-loud 报「目标在弹窗可视区外且无滚动能力」。
- 既有非 VIP 路径行为不变（目标全部在可视区内时零滚动、零额外 snapshot）。

### 不做（本任务边界）

- 不基于埋点数据做实际提速改动（sleep 缩短/坐标缓存等）——等真实数据收集后另立任务。
- 不改设计文档 §10.3 结构，仅在完成真机验证后补记 VIP 滚动行为。

## 测试计划

- `tests/perf.test.ts`：聚合正确性（count/total/max）、异常路径仍记时、summary 格式。
- `tests/boss-context`（或新增）：runBossOperation 输出 `[boss-perf]` 行（含 tool 名、
  各段耗时键）、body 收到 perf、原语包装计数正确、失败也输出。
- `tests/filter-setter.test.ts` 新增：
  - VIP 场景：行标签/选项 bounds 在容器 clip 外 → 触发 mouseWheel（deltaY>0）→
    滚后 snapshot 中目标进入可视区 → 点击坐标正确。
  - 滚 3 次仍不可见 → FilterSetError，绝不盲点。
  - 无 mouseWheel 注入 + 目标越界 → FilterSetError（保护旧调用方）。
  - 既有全部用例不回归（无可视区问题时零滚动）。

## 验证

- `npm run typecheck` + `npm test` 全绿（dev-workflow：开发自测 → 独立测试 → CR）。
- 真机验收（用户配合）：VIP 账号跑 `filter --experience … --salary …`，观察自动滚动 +
  `[boss-perf]` 日志行落 runtime.log；非 VIP 账号回归确认无行为变化。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | perf 模块 + runBossOperation 埋点 | ✅ 完成（2026-09-28） | typecheck 0 错；新增 perf/boss-context-perf 共 10 用例 |
| Phase 2 | FilterSetter VIP 滚动适配 | ✅ 完成（2026-09-28） | 新增 PanelScrollError（滚动失败不进保底映射链）；CR 后主控补修：目标上方越界负 deltaY 上滚；清除/确定改纯视口判定（footer 不随内容区滚动，防非 VIP 误判越界） |
| 验证 | 三智能体流程 + typecheck/test 全绿 | ✅ 完成（2026-09-28） | 开发 382/382 → 测试智能体独立复跑 382/382 + 启动冒烟通过 → CR 无 P0/P1 → 补修后主控复验 typecheck 0 错、npm test 384/384 |
| 真机 | VIP 账号真机验收 + 数据收集 | 📋 待开发 | 重点核对：LCA 容器几何是否≈可视 clip（误判则退化为仅视口）；滚点/deltaY 缓冲/sleep(400) 校准；非 VIP 账号回归零滚动；[boss-perf] 行落 runtime.log 后按数据定优化项 |
