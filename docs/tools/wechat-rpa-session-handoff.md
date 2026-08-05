# 微信 RPA 会话交接

> 更新时间：2026-08-05
> 当前基线：`7f387b9 重构微信搜一搜RPA为UIA定位`

## 当前结论

微信搜一搜正式 `collect` 已完成从像素定位到 Windows UI Automation（UIA）的切换。

- 搜索结果保持在默认“全部”，不再尝试切换“文章”。
- 搜索输入继续使用可信窗口内的键盘导航、剪贴板粘贴和精确回读。
- 详情入口只使用可信 `WeChatAppEx.exe / Chrome_WidgetWin_0` HWND 内的 UIA 候选。
- UIA 候选必须同时包含协会名和人员名，并具备可见、可点击的物理 `ClickablePoint`；百科、小程序、
  顶部分类、右侧栏和屏外节点均应排除。
- 进程使用 Per-Monitor DPI Aware V2。UIA 的 `BoundingRectangle`、`ClickablePoint` 和
  `SetCursorPos` 均按物理屏幕坐标使用；双屏、150% 缩放或负坐标显示器都不得再手工除以缩放率，
  也不得重复叠加窗口原点。
- 打开详情后固定等待 5 秒，再执行复制、文本 Judge 和必要的详情 OCR；详情没有手机号是正常业务结果。
- 内容不可读只影响当前候选或当前 query，最终可返回 `inconclusive`；只有窗口身份、前台、返回、关闭
  或 cleanup 无法确认时，才按窗口安全故障中断批次。
- 结果页截图哈希只用于确认页面是否变化，详情中央截图只用于 OCR；二者都不参与元素定位。

## 已完成的真机验证

单条“中国黄金协会 / 周洲”已按正式链路验证通过：

1. 在双屏、微信所在显示器 150% 缩放、深色主题下找到正确的双语义 UIA 候选。
2. 点击正确详情，而不是上方百科或其他结果。
3. 详情停留 5 秒，完成复制和判断。
4. 正确关闭详情并返回结果页，最后完成会话清理。
5. 该详情确实没有手机号，因此结果为 `inconclusive`；`checked=1`、`session_closed=true`。
6. 返回列表后没有新的合格 UIA 候选是正常枚举结束，不是卡片定位失败。

这证明“UIA 双语义候选 + 物理 ClickablePoint + HWND 会话状态机”能够完成单条详情闭环，但尚未证明
连续查询和 20 条批次稳定性。

## 尚未实施：协会基础信息解析优化

这项工作此前为了先完成微信 RPA 重构而明确延期，当前代码**尚未按用户要求优化**。官网页面采集、
网络回退和 14 字段模型解析已经存在，但解析器仍包含严格的逐字 `evidence_quote`、同域 `source_url`、
字段格式和逐字段内容拒绝规则；不合格字段会被清空，所有业务字段都被拒绝时结果会变为 `inconclusive`。

后续优化必须遵守以下已确认的业务规则：

- 官网采集流程保留，当前需求不是重写浏览器采集器。
- 模型只依据用户提供或程序采集到的源文本解析 14 个字段。
- 模型成功解析出的字段应被接受，不增加“是否真实”“是否可信”或最小正文长度等主观门禁。
- 源文本本身存在错误不属于解析器的拒绝理由，程序不能替用户纠正或否决源材料。
- 仍需保留确定性的接口安全边界，例如严格 JSON/字段集合、类型、敏感信息处理和来源审计；这些边界
  不得演变成对业务内容真实性的二次判断。

实施前先把当前 `_validate_profile`、字段拒绝原因、全字段失败和 provider 重试路径逐项
分类为“结构安全校验”或“主观内容门禁”，形成最小改动清单和测试预期；然后修改解析器及对应单元测试，
最后用已采集的真实官网文本回归。不要把这项优化夹在微信真机测试过程中临时修改。

## 已证明无效，禁止恢复

以下方案已经由当前微信版本或真机结果证明无效，不再继续调参，也不得作为回退路径重新加入：

- **`Ctrl+Tab` 切换“文章”**：用户手工测试和正式运行均不能切换分类，代码已删除。
- **UIA `Invoke` 点击“文章”标签**：调用可返回成功，但页面实际不切换；“文章”分类不是当前依赖项。
- **暗色/浅色卡片像素 band**：主题、渲染、DPI 和结果内容变化会导致零候选或误点，
  `Find-DarkThemeCardBands` 已删除。
- **截图识别搜索框或详情卡片**：不能提供稳定的元素边界，视觉搜索框 locator 与对应模型链路已删除。
- **比例坐标、固定相对点和 locator fixture**：双屏与 150% DPI 下不可靠，相关 `x_ratio/y_ratio`、
  fixture 和固定点击分支已删除。
- **`flow_probe` 固定坐标探针**：只曾用于发现窗口层级，不代表正式行为，命令及参数已删除。
- **把物理坐标除以 1.5 再点击**：这是浏览器页面坐标转换思路，不适用于已由 DPI-aware UIA 返回的
  Windows 物理坐标，会造成二次缩放。
- **`CARD_LOCATE_FAILED`**：属于旧像素卡片定位语义，已删除；UIA 枚举结束使用安全原因
  `no_new_uia_candidate`，再由现有状态汇总决定 `not_found` 或 `inconclusive`。
- **列表截图视觉模型**：结果列表 Judge 只接收协会名、人员名和复制文本，不发送图片。
- **通过多关一层窗口、重复快捷键或增加 settle 修补焦点**：会破坏真实页面栈，不能替代 HWND 身份核验
  和正式 cleanup。

保留旧方案的历史说明没有执行价值；后续排障应基于 UIA 枚举、可信 HWND、前台状态和 cleanup artifact，
不要重新比较像素阈值或固定坐标。

## 接下来要做什么

当前有两条未完成工作线：

1. **微信稳定性验收线**：代码已实现，下一步是继续真机验证。
2. **协会基础信息解析优化线**：尚未开发，应单独修改解析器和测试，不依赖微信 20 条结果。

微信稳定性验收按以下顺序执行，每一级通过后才能进入下一级：

### 1. 连续两条：周洲 -> 陈新华（最高优先级）

使用正式 provider/`collect`，不要使用已经删除的 `flow_probe`。需要确认：

- 周洲详情正确打开、等待、关闭，返回 `session_closed=true`；
- 第二条查询只在第一条明确清理完成后启动；
- 陈新华命中协会名和人员名同时匹配的 UIA 结果，不能打开百科或小程序；
- 两条都能完成 cleanup，批次不因内容为空而中断；
- 保存安全审计字段和失败码，不保存 query 正文、页面正文或手机号。

若失败，优先检查 UIA 候选清单、候选所属 HWND、前台 HWND、点击前后页面哈希和 cleanup 记录。不要增加
坐标回退。只有出现窗口安全错误才停止；`no_new_uia_candidate` 或没有手机号本身不是窗口故障。

### 2. 普通连续两条：1 -> 2

选择两个不会进入独立小程序的普通样本，验证连续查询不是只对“周洲”路径有效。重点检查第二条输入焦点、
结果页重新绑定以及详情关闭后是否只剩预期微信窗口。

### 3. 无人干扰连续 20 条

前两组通过后，再运行正式 20 条：

- 逐条记录 `ok/status/checked/session_closed`、安全 failure reason 和批次是否继续；
- 记录每条窗口启动、详情返回和终态 cleanup 是否完成；
- 不以手机号命中率代替 RPA 稳定性判断；没有手机号允许 `inconclusive`；
- 任一窗口安全失败立即停止，不自动补点、不切换像素方案。

### 4. 重新跑协会资料补全

连续 20 条通过后，才重新生成正式 Excel，并重新评估手机号召回率。此前没有搜索输入回读证据的 20 家
结果和旧 Excel 继续作废，不得用于验收或演示。

## 验收标准

- 单条详情点击只来源于可信 HWND 内的 UIA 双语义候选和物理 `ClickablePoint`。
- 双屏、150% DPI、深色主题下不发生坐标换算误点。
- 周洲 -> 陈新华和普通 1 -> 2 均连续完成，第二条输入、详情与 cleanup 无回归。
- 内容不可读、无手机号或候选耗尽可以安全结束并继续下一条。
- 外部窗口、未知 HWND、前台丢失、返回失败或部分 cleanup 失败必须稳定中断。
- 连续 20 条无人干扰运行完成后，才认定当前微信 RPA 达到批次验收条件。

## 主要文件与测试

- `clients/wechat-souyisou-rpa/scripts/wechat-souyisou.ps1`：正式 `probe/open/search/collect` 入口。
- `clients/wechat-souyisou-rpa/scripts/wechat-souyisou-lib.ps1`：UIA、DPI、窗口会话、键盘、剪贴板、页面
  证据与预算公共函数。
- `clients/wechat-souyisou-rpa/scripts/llm_judge.py`：仅文本的列表/详情 Judge 适配器。
- `clients/wechat-souyisou-rpa/scripts/extract-mobile.ps1`：详情 OCR 和手机号补充，不用于元素定位。
- `src/services/association_enrichment_providers.py`：正式 provider、600 秒外层超时和连续查询契约。
- `clients/wechat-souyisou-rpa/tests/test-uia-result-targets.ps1`：UIA 候选筛选测试。
- `clients/wechat-souyisou-rpa/tests/test-window-session.ps1`：窗口会话、预算和恢复测试。
- `clients/wechat-souyisou-rpa/tests/test-static.ps1`：已删除旧定位路径的静态契约测试。

换机或继续开发前先运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File clients/wechat-souyisou-rpa/tests/test-uia-result-targets.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File clients/wechat-souyisou-rpa/tests/test-window-session.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File clients/wechat-souyisou-rpa/tests/test-static.ps1
python -m pytest clients/wechat-souyisou-rpa/tests/test_llm_judge.py clients/wechat-souyisou-rpa/tests/test_ocr_adapter.py tests/unit/services/test_association_batch_enrichment.py tests/unit/services/test_association_enrichment_cli.py -p no:cacheprovider -q
```

当前基线已通过：静态测试 204 项、UIA 目标测试、窗口会话测试、Judge/OCR Python 23 项及 8 个子测试，
并完成单条周洲真机闭环。微信线的下一项任务是正式连续“周洲 -> 陈新华”；基础信息解析优化仍是独立的
未开发事项，不能因微信验收计划而遗漏。
