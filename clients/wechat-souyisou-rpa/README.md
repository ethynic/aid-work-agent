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
若异常发生时详情仍可能打开，该状态只进入脱敏诊断；`finally` 不发送详情层 `Ctrl+W`，
也不复制复验结果页，而是直接按同一身份门禁关闭完整搜一搜插件 HWND。失败 JSON 和
failure artifact 保留具体关闭或主窗口恢复错误码。

`search` 只复制并封存结果页。`collect` 使用项目 LLM 时先判断整页列表文字；列表命中后
直接返回并把来源标记为 `result_page_unbounded`，未命中或不确定才继续打开前 10 条详情。
提交搜索后不会按固定短等待直接复制：脚本每 500ms 复制一次页面，必须连续两次识别到
结果页固定栏目才进入取证。默认最多等待 15 秒，可用
`-SearchReadyTimeoutMilliseconds 10000..60000` 调整；超时返回
`SEARCH_RESULTS_TIMEOUT`，并由 finally 关闭本次搜一搜会话。
可信主窗口执行 `Ctrl+F → Down → Enter` 打开新搜一搜会话后直接粘贴并从当前控件回读 query；
期间不截图、不点击鼠标、不激活窗口。只有规范空白后精确一致且前台仍可信时才提交；首次不一致
最多再三键重聚焦一次。失败返回稳定输入子码和 `input_verify`，不会把聊天框内容计为有效检索。
真机诊断可在 `search` 或 `collect` 且带 `-Execute` 时增加 `-VerifyInputOnly`：复用相同键盘聚焦、
聚焦和回读验证，但绝不按 Enter；成功后关闭插件并恢复主窗口，只报告已验证且未提交。
失败会区分输入回读和前台丢失稳定码；加密 failure artifact 仅记录回读是否匹配，
不记录 query、剪贴板文本或 OCR。
结果页整页复制可能包含第 11 条及之后内容，因此 artifact 会明确保留该来源边界，供复核。
图片/PDF 详情会默认调用项目 PaddleOCR adapter，只截取整页详情中央正文区域的最多 3 个滚动
视口作为补充证据，避免把左侧其他搜索结果混入当前详情。
搜索列表相关性只使用剪贴板复制文字，Judge payload 不包含截图、base64 或图片路径；列表截图
只在本地用于页面变化哈希。详情 OCR 非零退出仅返回稳定
`JUDGE_PROCESS_FAILED`，不会输出 stderr、临时路径或页面内容。列表证据在 UIA 详情枚举前始终经过
文本 Judge；没有外部 Judge 时使用内置的同一行姓名—手机号严格绑定。
新电脑微信版本已确认 `Ctrl+Tab` 不能切换“文章”，正式 `search/collect` 不再发送该快捷键，
直接使用首次稳定的“全部”结果列表。若整页已经形成有效绑定则直接返回；否则只在已验证的
搜一搜 HWND 内枚举 UI Automation，要求候选同时包含协会名和人员名，并排除百科、小程序、
分类栏、右侧栏和不可见节点。

可用 `-OcrCommand` 覆盖 OCR executable，或用 `-DisableOcr` 明确关闭；需要 OCR 而能力
不可用时返回 `inconclusive`。

`collect` 默认使用 UIA `ClickablePoint` 打开详情。正式线程在读取窗口矩形和 UIA 坐标前声明
Per-Monitor DPI Aware V2；两者均按物理屏幕坐标处理，所以 150% 缩放不再除以 1.5，也不叠加
窗口原点，位于副屏的负坐标保持有效。点击前后仍严格复验同一可信前台，并且每个候选只单击一次。
有界恢复后没有 UIA 候选，或已检查候选后没有新候选，表示详情枚举自然结束：记录不含正文的
`uia_candidates_unavailable/no_new_uia_candidate`，由现有状态规则返回 `inconclusive` 并正常清理，
前台或窗口身份失败仍是致命安全错误。

正式批处理的查询间交接由 Python provider 管理，不修改本脚本的单条 `collect`：上一条明确返回
`ok=true/session_closed=true` 后，下一条启动前只发送一次 Alt+Tab、等待 1000ms，再由现有脚本
激活微信主窗口。首条、上一条未启动或清理不确定时不发送；交接失败返回
`WECHAT_HANDOFF_FAILED` 并停止批次。详见
[连续查询会话交接记录](../../docs/tools/wechat-rpa-session-handoff.md)。

详情闭环会验证
点击前台、页面截图变化、详情文本特征、`Alt+Left` 后结果页恢复；非终止路径不发送 `Ctrl+W`。
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
`collect` 已接入 UIA 语义候选和最多 10 条的详情闭环，但仍只适合受控真机验收；150% DPI 与
双屏负坐标已有离线合同测试，更多动态布局和 DPI 矩阵尚未完成。项目 LLM Gateway 适配已有第一版，但尚未完成真实 Provider
矩阵和准确率验收；不启用 `-UseProjectLlm` 时的确定性 judge 也可能产生漏判。因此不能宣称
具备生产级联系人查找能力。

离线测试（不会操作微信）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\test-static.ps1
```
