# 微信搜一搜 RPA CLI

无界面 PowerShell CLI。默认只输出计划；只有显式传入 `-Execute` 才操作普通微信。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\wechat-souyisou.ps1 -Command search `
  -AssociationName 中国游艺设备游乐园协会 -PersonName 王承展
```

命令：`probe`、`open`、`search`、`collect`。也可用 `-ReadStdin` 接收 UTF-8 JSON：

```json
{"command":"search","association_name":"中国游艺设备游乐园协会","person_name":"王承展"}
```

stdout 始终为单行 JSON，手机号不会写入 stdout。原始结果使用当前 Windows 用户的
DPAPI 加密后保存，返回值只包含 `artifact_ref`。日志和错误消息会脱敏。
`inconclusive` 也会在 DPAPI artifact 中保留已复制正文和 OCR 文本，供人工复盘；
这些敏感证据不会写入 stdout 或普通日志。

`search` 和 `collect` 每次结束都会严格关闭本次搜一搜插件 HWND，并恢复普通微信主窗口，
无论结果是 `found/not_found/inconclusive/blocked` 还是发生异常。成功结果包含
`session_closed=true`。清理失败返回 `SESSION_CLEANUP_FAILED`，调用方必须停止批处理，
不能继续查询下一人；此前已生成的 DPAPI artifact 不会被覆盖。`open` 仅负责打开搜一搜，
按命令语义不会自动关闭。
插件 HWND 已销毁，或插件已不可见且可信微信主窗口恢复前台，均算关闭成功。仍可见插件、
错误身份或主窗口未恢复继续返回清理失败。每个会话只尝试一次 cleanup，失败后不会在
finally 重复发送关闭快捷键。清理开始时若插件 HWND 已经隐藏，只校验其可信身份，
不会重新激活该隐藏窗口；可见插件的关闭状态最多轮询约5秒后再判失败。
若异常发生时详情仍可能打开，`finally` 只先关闭一次详情，并复制验证已经恢复原结果
列表；验证成功后才关闭搜一搜插件。已在结果列表时只关闭插件，已完成主窗口恢复时
不再额外发送 `Ctrl+W`。

`search` 只复制并封存结果页。`collect` 使用项目 LLM 时先判断整页列表文字；列表命中后
直接返回并把来源标记为 `result_page_unbounded`，未命中或不确定才继续打开前 10 条详情。
提交搜索后不会按固定短等待直接复制：脚本每 500ms 复制一次页面，必须连续两次识别到
结果页固定栏目才进入取证。默认最多等待 15 秒，可用
`-SearchReadyTimeoutMilliseconds 10000..60000` 调整；超时返回
`SEARCH_RESULTS_TIMEOUT`，并由 finally 关闭本次搜一搜会话。
结果页整页复制可能包含第 11 条及之后内容，因此 artifact 会明确保留该来源边界，供复核。
图片/PDF 详情会默认调用项目 PaddleOCR adapter，只截取整页详情中央正文区域的最多 3 个滚动
视口作为补充证据，避免把左侧其他搜索结果混入当前详情。
可用 `-OcrCommand` 覆盖 OCR executable，或用 `-DisableOcr` 明确关闭；需要 OCR 而能力
不可用时返回 `inconclusive`。

`collect` 默认用 `System.Drawing` 截取可信插件窗口，在左侧结果内容区检测暗色主题卡片
band，并生成窗口相对标题点击点。浅色主题当前会安全返回 `CARD_LOCATE_FAILED`。

`-LocatorPath` 保留为测试或人工校准覆盖，格式为：

```json
{"items":[{"title":"结果标题","source":"公众号","date":"2026-01-01","x_ratio":0.30,"y_ratio":0.30,"scroll_after":false}]}
```

CLI 会校验覆盖点击点处于结果内容区，不会回退到未经验证的绝对坐标。详情闭环会验证
点击前台、页面截图变化、详情文本特征、`Ctrl+W` 后结果页恢复；连续两次失败会熔断。
详情页当前已有截图变化和详情文本特征校验，但加载等待仍主要受
`-WaitMilliseconds` 控制；慢网详情的稳定就绪轮询仍属于后续真机强化项。
当前内置 judge 是确定性预筛选；正式接 LLM Gateway 时可通过
`-UseProjectLlm` 调用 `scripts/llm_judge.py` 和项目现有 `LLMGateway`，也可用
`-JudgeCommand <executable>` 接外部单行 JSON judge。模型给出的姓名和手机号仍由
PowerShell 在原文中二次校验；provider 全失败返回 `inconclusive`。
LLM 负责多人共现时的语义归属：旧信息允许采用；目标人明确位于联系人组且随后给出
联系电话时，该号码可作为组内目标人的可用联系方式，即使多人共用。只有号码明确排他
绑定给另一人或目标仅出现在无关上下文时才拒绝。存在多个候选时，优先选择在更多独立
结果中重复与目标联系人组关联的完整手机号。PowerShell 不使用固定词距规则替代该语义
判断，只验证返回的目标姓名、
完整号码及逐字 `evidence_quote` 确实存在于原证据。列表未命中或 Judge 异常时，
加密 artifact 记录 `list_judge_status` 和不含原文的 reason code，普通日志不记录号码。
PowerShell 调用 Python Judge 时，stdin JSON 通过重定向流写入显式 UTF-8 无 BOM 字节；
不依赖本机中文代码页或控制台编码。现代 .NET 同时设置
`StandardInputEncoding`，旧 Windows PowerShell 则在创建重定向 writer 时抑制 BOM，
随后直接写入 UTF-8 bytes，并恢复调用方原控制台编码。
Judge 首次响应若不是合法 JSON，或严格字段/schema/type 校验失败，会在不携带首次原始
响应的情况下重试一次，并再次要求 exact schema JSON。Gateway 网络、鉴权或 chat 调用
异常不重试；第二次仍不合规则返回 `inconclusive`。模型命中后仍须经过 PowerShell 的
姓名、完整号码和逐字证据引用合同校验。
Judge 首次和格式重试统一使用 `max_tokens=2500`。该预算包含 Provider 的 reasoning
消耗，避免800 token 全部用于推理、`finish_reason=length` 且最终 content 为空。

失败 artifact 会记录当前 `stage`（如 `locate/detail_copy/recover/cleanup`）以及前台窗口的
HWND、进程 basename、窗口类名和标题，不保存完整进程路径，也不会把这些诊断或手机号
写入 stdout。

当前交付边界：`search` 已具备正式 CLI 实现，可进入搜一搜、提交查询并加密保存第一页文本。
`collect` 已内置暗色主题实验定位器和最多 10 条的详情闭环，但只适合受控真机验收；浅色主题、
动态布局与 DPI 矩阵尚未完成。项目 LLM Gateway 适配已有第一版，但尚未完成真实 Provider
矩阵和准确率验收；不启用 `-UseProjectLlm` 时的确定性 judge 也可能产生漏判。因此不能宣称
具备生产级联系人查找能力。

离线测试（不会操作微信）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\test-static.ps1
```
