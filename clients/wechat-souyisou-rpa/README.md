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
finally 重复发送关闭快捷键。

`search` 只复制并封存结果页。`collect` 使用项目 LLM 时先判断整页列表文字；列表命中后
直接返回并把来源标记为 `result_page_unbounded`，未命中或不确定才继续打开前 10 条详情。
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
当前内置 judge 是确定性预筛选；正式接 LLM Gateway 时可通过
`-UseProjectLlm` 调用 `scripts/llm_judge.py` 和项目现有 `LLMGateway`，也可用
`-JudgeCommand <executable>` 接外部单行 JSON judge。模型给出的姓名和手机号仍由
PowerShell 在原文中二次校验；provider 全失败返回 `inconclusive`。

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
