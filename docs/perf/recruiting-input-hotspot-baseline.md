# 招聘链路输入性能热点：替换前基线（Phase 5 固定）

来源计划：[plan-recruiting-error-containment-and-input-performance.md](../plans/plan-recruiting-error-containment-and-input-performance.md) Phase 5。
用途：Phase 1（Win32 输入 Node 原生化）上线后，Phase 6 真机观察以此为**替换前基线**做前后对比；本文件只登记已采集事实，不要求客户重复提供日志。

## 1. 替换前基线数据（客户机 runtime.log，2026-10-20 复盘采集）

故障时段（客户 Windows 未重启）实测，对照健康历史样本：

| 指标 | 故障时段实测 | 健康历史样本 | 倍数 |
|------|------------|------------|------|
| 单次点击均值 | 26.235s | 2.2～2.4s | ~10–12× |
| 拖动（年龄滑块双端） | 27.495s | 2.2～2.4s 量级 | ~10–12× |
| write 工具前置间隔（desktopCheck 相关） | 11～15s | — | — |
| boss 操作 body 层未覆盖耗时 | 81.381s（未细分） | — | — |
| 调用层未细分耗时 | 13.784s | — | — |
| 列职位（boss_list_jobs）3 次 snapshot 累计 | 14.030s | — | — |

数据来源说明：

- 点击/拖动/body/call 层耗时来自客户机 `[boss-perf]` 单行（`win32:click` / `win32:drag` 聚合、`body` / 调用层差值），由 providerManager 转发落 runtime.log。
- 「body 未覆盖 81.381s」= body 总时长减去既有已计时段（`connect/body/close`、`snapshot`、`win32:*`、`cdp:*`、`filter:panel:*`、`sleep:*`）后的剩余部分，当时无分段可归因。
- 「调用层 13.784s」= Runtime 从领取 invocation 到 Provider 调用之间的未细分窗口（含桌面锁等待、Provider 就绪/调度、desktopCheck），当时无分段。
- 客户重启 Windows 后恢复正常——属临时恢复，根因待 Phase 6 复现取证（系统可用/已提交内存、分页与磁盘延迟、CPU；Runtime/Provider/Chrome/PowerShell 进程私有内存、句柄、线程、CPU/IO），不据此关闭内存/进程状态排查。

## 2. Phase 5 新增分段计时（本文件登记时已实现）

### 2.1 插件 TS 侧（BOSS CLI，`[boss-perf]` 行新增分段）

替换（Phase 1 原生化）也解决不了的热点——与输入通道无关，原生化后依然存在：

| 分段名 | 覆盖内容 | 位置 |
|--------|---------|------|
| `filter:parse:structure` | 筛选面板结构解析（structuralRows：行标签锚定 + LCA 容器 + 行内选项收集） | `clients/boss-resume-assistant/src/main/boss/FilterSetter.ts` |
| `filter:parse:panelGeometry` | 无 styles 旧快照的行带几何解析（describePanel fallback） | 同上 |
| `filter:precheck:rows` | 逐行预校验（清除前的行选项定位校验 + 档位保底映射） | 同上（applyOnce） |
| `filter:visible:clip` | 可视区几何计算（目标定位 + 全局坐标 + clip 上下界，ensureHitVisible 每次迭代） | 同上 |
| `filter:age:read` | 年龄值读取/验证（ageSlider 轨道/句柄/tooltip 解析，setAge 循环内反复执行） | 同上 |
| `filter:verify:fallback` | 失败后的只读核验（此前未包装调用） | `clients/boss-resume-assistant/src/main/operations/bossFilter.ts`（verifiedFilter catch） |
| `snapshot:precheck` | 前置校验快照（WRONG_PAGE 门禁用） | bossFilterOptions / bossListJobs / bossSelectJob |
| `snapshot:listJobs` | 列职位 JobSwitcher 业务快照（开下拉/解析/收起/复核） | `clients/boss-resume-assistant/src/main/operations/bossListJobs.ts` |
| `snapshot:selectJob` | 选职位 JobSwitcher 业务快照 | `clients/boss-resume-assistant/src/main/operations/bossSelectJob.ts` |

**消费口径（嵌套归因）**：分段允许嵌套（与既有 `snapshot` 嵌套在 `filter:panel:*` 内同口径）——`filter:parse:structure` 会嵌套出现在 `probe:describe` / `filter:precheck:rows` / `filter:visible:clip` 内，`snapshot:listJobs` 嵌套在 `snapshot` 总量内。做互斥求和时**按最内层归因**（外层段减去其内部已计子段）。

### 2.2 调用层（Runtime，`[runtime-perf]` 单行，经 onEvent/logInfo 落 runtime.log）

`clients/shared/local-tool-host-core/src/legacy/invocationRunner.ts`：

```
[runtime-perf] invocation=<id> tool=<tool> provider=<provider_key> started_ms=<n> lock_wait_ms=<n> lock_prep_ms=<n> provider_call_ms=<n>
```

| 字段 | 覆盖内容 |
|------|---------|
| `started_ms` | started 回传（含网络重试） |
| `lock_wait_ms` | 桌面锁等待（进入仲裁到获得锁） |
| `lock_prep_ms` | Provider 就绪/调度（锁内：取消复验、会话复核、写许可申请、journal fsync、防御复验） |
| `provider_call_ms` | Provider 调用本体（MCP callTool；CLI 侧另有 `[boss-perf]` 细分） |

- `-1` 表示该段未发生；`lock_prep_ms=-1` 另涵盖「锁内前置失败提前返回（lockPreabort，未执行 Provider 调用）」的情况。
- **desktopCheck 本体不计时**（有意为之）：由 Phase 1 进程内化消除，其耗时经 Phase 6 真机观察核对是否随之收敛；写工具前置 11～15s 间隔与 desktopCheck 的相关性证据保留在上表基线中。
- 该行只在执行过锁/Provider 的主路径输出；路由失败（PROVIDER_NOT_AVAILABLE 等提前返回）不输出。

## 3. Phase 6 对比方法

1. 前置条件：Phase 1 native 输入链路上线（capability 验收 + 默认切换）。
2. 观察点：
   - 单次点击/拖动是否复现 26s 量级（对照上表基线）；native 内建分段计时是否回到健康机器 ~1s 量级。
   - body 未覆盖剩余量：`filter:parse:*` / `filter:precheck:rows` / `filter:visible:clip` / `filter:age:read` 分段合计是否解释原 81.381s 的主要成分。
   - 调用层：`lock_wait_ms` / `lock_prep_ms` / `provider_call_ms` 是否解释原 13.784s 的主要成分；`provider_call_ms` 内 CLI 侧 `snapshot` 聚合与 `snapshot:listJobs` 等用途分段是否收敛（对照 14.030s 基线）。
3. 若复现：按最小证据采集流程取证（系统内存/分页/磁盘延迟、进程私有内存/句柄/线程/CPU/IO）后再重启/关闭进程，重新评估归因。

## 4. 边界与遗留

- PowerShell/C# 旧输入路径不做新增子阶段取证（将被 Phase 1 删除，丢弃型工作）；守卫失败证据（预期/实际坐标、widget、命中 HWND、前台/几何变化）由 Phase 1 native 实现内建交付，不在旧路径补做。
- 以下 body 部分仍未分段（Phase 5 范围外，量级预期小）：membership 检查（`inspectMembership` / `inspectFilterPaywall` / `membershipAccountKey`）、`setFirstDegree` 的第一学历控件解析、终态回传（`final_report`）耗时。
- 本文件不进 ideas 索引（性能基线登记，随计划文档链接引用；收尾阶段将其链接进计划文档）。
