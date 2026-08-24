# 协会资料批量补全 CLI：本机演示操作手册

> 适用场景：在 Windows 本机、不打开 Codex 的情况下预跑或演示协会资料采集。
> 当前版本定位：可见浏览器、人工监督的 Demo/MVP，不建议无人值守运行大批量任务。

## 1. 演示会发生什么

输入一个或多个协会名称后，程序会按顺序执行：

1. 去重协会名称；
2. 网络检索并识别协会官网；
3. 打开可见 Chromium，自动发现官网内的简介、领导、组织、联系等相关页面；
4. 使用项目 LLM 提取官网字段；
5. 官网不可访问时，尝试 `https → http`，仍失败则用普通网络检索补充基础信息；
6. 如果找到了秘书长、会员服务负责人或办公室/综合办负责人姓名，自动驱动微信搜一搜查找手机号；
7. 每个联系人结束后关闭搜一搜，恢复微信主窗口；
8. 将所有协会的结果、状态、来源和错误摘要写入一个 `.xlsx` 文件。

手机号不会打印在 PowerShell 窗口中。完整手机号只写入本机输出 Excel；微信原始证据使用
当前 Windows 用户的 DPAPI 加密保存。

## 2. 演示前准备

### 2.1 固定项目目录

打开 Windows PowerShell：

```powershell
Set-Location C:\repos\aid-work-agent
```

后续命令都在这个目录执行。不要使用系统的 `python`，统一使用项目虚拟环境：

```powershell
.\venv\Scripts\python.exe --version
```

预期：输出 Python 版本且退出码为 0。

### 2.2 检查项目密钥

根目录 `.env` 至少需要：

```text
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEYS=实际可用的密钥
TAVILY_API_KEY=实际可用的密钥
```

也可以使用项目已支持的 Qwen 或 ZhipuAI 配置。不要在演示画面、截图或聊天中展示 `.env`。

只检查变量是否存在，不显示真实密钥：

```powershell
.\venv\Scripts\python.exe -c "from src.config.settings import settings; print({'llm_provider': settings.llm.provider, 'search_key_configured': bool(settings.tools.search.tavily_api_key)})"
```

预期：`search_key_configured` 为 `True`，LLM provider 与本机配置一致。

### 2.3 检查 Python 和浏览器依赖

```powershell
.\venv\Scripts\python.exe -c "import openpyxl; from playwright.async_api import async_playwright; print('dependencies_ok')"
```

如果缺 Playwright Chromium：

```powershell
.\venv\Scripts\python.exe -m playwright install chromium
```

### 2.4 准备微信

演示前必须满足：

- 使用 Windows 官方微信并已登录；
- 普通微信主窗口已经打开，不要最小化；
- 关闭之前遗留的搜一搜详情或插件页，保持普通微信主界面；
- 确认当前微信版本向 Windows UI Automation 暴露搜一搜结果节点；主题颜色不参与定位；
- 微信窗口不要被其他窗口完全遮挡；
- 演示过程中不要同时操作键盘、鼠标或剪贴板；
- 不要同时启动第二个协会采集进程。

只检查微信窗口，不执行搜索：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\clients\wechat-souyisou-rpa\scripts\wechat-souyisou.ps1 `
  -Command probe -Execute
```

成功示例：

```json
{"ok":true,"window_found":true,"command":"probe","executed":true}
```

如果返回 `WX_WINDOW_NOT_FOUND`，先确认微信已登录并显示普通主窗口。

## 3. 今天预跑 Demo

### 3.1 第一步：只验证输入，不访问网络或微信

```powershell
.\venv\Scripts\python.exe `
  .\clients\association-enrichment-cli\association_enrichment_cli.py `
  --association "中国日用玻璃协会,中国缝制机械协会,中国日用玻璃协会" `
  --output ".\demo-output\dry-run.xlsx" `
  --dry-run
```

预期：

```json
{"ok":true,"dry_run":true,"association_count":2,"associations":["中国日用玻璃协会","中国缝制机械协会"]}
```

`dry-run` 不会生成 Excel，不会打开浏览器，也不会操作微信。

### 3.2 第二步：先真实运行一个协会

先选一个已经验证过的协会，推荐：

```powershell
.\venv\Scripts\python.exe `
  .\clients\association-enrichment-cli\association_enrichment_cli.py `
  --association "中国日用玻璃协会" `
  --output ".\demo-output\association-demo-one.xlsx"
```

运行时的正常现象：

- PowerShell 会依次显示“正在发现官网”“正在使用可见浏览器采集官网”
  “正在微信检索秘书长/会员部主任/办公室主任”“正在写入 Excel 结果”等阶段进度；
- 出现可见 Chromium 窗口并自动浏览协会官网；
- Chromium 阶段结束后窗口自动关闭；
- 微信被切换到前台，打开搜一搜并执行联系人查询；
- 一个联系人结束后搜一搜关闭并回到微信主窗口；
- 如果有第二个联系人，会重新完整打开一次搜一搜；
- PowerShell 最终只输出汇总 JSON，不显示手机号。

阶段进度写入 PowerShell 的错误输出流，最后一行标准输出仍是唯一的汇总
JSON。这些进度不是报错；它们用于判断程序当前停留在哪个环节，并且其中
的手机号会被脱敏。

成功或部分成功示例：

```json
{"ok":true,"association_count":1,"complete_count":0,"partial_count":1,"failed_count":0,"output":"C:\\repos\\aid-work-agent\\demo-output\\association-demo-one.xlsx"}
```

`partial` 不等于程序失败：只要采集到部分字段，但官网、网络检索或微信某一步留下错误摘要，
该行就会标记为 `partial`。

对当前 Demo，建议把以下结果视为成功：

- 官网基础字段已写入；
- 秘书长、会员服务负责人或办公室/综合办负责人至少一人的手机号命中；
- `source_summary` 包含 `official:` 和至少一个 `wechat:`；
- 未命中的联系人明确记录为 `wechat:秘书长:not_found` 等脱敏错误摘要。

只有 `failed` 且业务字段全部为空，才属于整条流程失败。

### 3.3 第三步：检查输出 Excel

```powershell
Invoke-Item ".\demo-output\association-demo-one.xlsx"
```

重点检查：

- `association_name`：协会名称；
- `secretary_general_name` / `member_director_name` / `office_director_name`：秘书长、会员服务负责人、办公室/综合办负责人姓名；
- `secretary_general_mobile` / `member_director_mobile` / `office_director_mobile`：对应手机号；
- `official_website`、地址、邮箱、会员数等基础信息；
- `processing_status`：`complete`、`partial` 或 `failed`；
- `source_summary`：使用过的官网、网络检索和微信来源；
- `error_summary`：未完成步骤的脱敏错误摘要；
- `processed_at`：处理时间。

关闭 Excel 后再重新运行同名输出文件，避免 Windows 文件占用导致保存失败。

### 3.4 第四步：运行多个协会

单协会成功后再运行2—3家：

```powershell
.\venv\Scripts\python.exe `
  .\clients\association-enrichment-cli\association_enrichment_cli.py `
  --association "中国日用玻璃协会" `
  --association "中国缝制机械协会" `
  --association "中国物资再生协会" `
  --output ".\demo-output\association-demo-batch.xlsx"
```

程序按输入顺序串行处理。某一家失败不会阻断后续协会，最终每家都会在 Excel 中占一行。

## 4. 使用 CSV 或 Excel 作为输入

### 4.1 CSV

新建 `demo-associations.csv`，推荐使用 UTF-8：

```csv
协会名称
中国日用玻璃协会
中国缝制机械协会
中国日用玻璃协会
```

运行：

```powershell
.\venv\Scripts\python.exe `
  .\clients\association-enrichment-cli\association_enrichment_cli.py `
  --input ".\demo-associations.csv" `
  --output ".\demo-output\association-demo-csv.xlsx"
```

### 4.2 Excel

`.xlsx` 可以有多个工作表。至少一个工作表的第一行必须包含以下任一列名：

- `协会名称`
- `association_name`
- `association`
- `单位名称`

运行：

```powershell
.\venv\Scripts\python.exe `
  .\clients\association-enrichment-cli\association_enrichment_cli.py `
  --input ".\demo-associations.xlsx" `
  --output ".\demo-output\association-demo-excel.xlsx"
```

文本参数和文件可以同时使用，程序会合并并去重。

## 5. 演示当天推荐流程

1. 提前启动并登录微信，切换深色主题；
2. 关闭无关窗口和通知，确保网络稳定；
3. 执行微信 `probe`；
4. 对客户给出的协会名单先执行 `--dry-run`，展示解析和去重结果；
5. 先真实运行1家，说明正在执行“官网优先、网络检索降级、微信补手机号”；
6. 浏览器或微信自动操作时不要碰鼠标、键盘；
7. 命令结束后打开 Excel，优先展示姓名、手机、官网、地址、邮箱、来源和处理状态；
8. 再运行剩余协会；
9. 演示结束后妥善保管或删除包含手机号的 Excel。

建议第一次客户演示控制在2—3家，不要直接演示几十家。

## 6. 常见错误与恢复

### `INPUT_ASSOCIATION_LIST_EMPTY`

没有解析到协会名称。检查文字参数，或检查 CSV/XLSX 是否有正确表头和数据。

### `INPUT_ASSOCIATION_COLUMN_NOT_FOUND`

CSV/XLSX 第一行没有受支持的协会名称列。将列名改为 `协会名称`。

### `INPUT_FILE_NOT_FOUND` / `INPUT_FILE_TYPE_UNSUPPORTED`

输入文件路径错误，或文件不是 `.csv` / `.xlsx`。

### `WX_WINDOW_NOT_FOUND`

微信没有登录、主窗口未打开，或当前显示的不是普通微信主窗口。打开微信主界面后重试。

### `WECHAT_RPA_FAILED`

先观察微信当前状态：

1. 如果仍停留在搜一搜或详情页，手工按 `Ctrl+W`，直到回到普通微信主窗口；
2. 确认深色主题和窗口无遮挡；
3. 重新执行 `probe`；
4. 只重跑当前协会，并使用新的输出文件名。

### `WECHAT_RPA_TIMEOUT`

单个联系人的微信流程超过2分钟。手工恢复微信主窗口，确认网络和页面可操作后重试。

### `SESSION_CLEANUP_FAILED` / `WECHAT_SESSION_NOT_CLOSED`

搜一搜没有可靠关闭。不要直接开始下一批。手工按 `Ctrl+W` 恢复普通微信主窗口，
执行 `probe` 成功后再重跑。

### UIA 候选不可用

一次有界恢复后仍没有 UIA 候选时，本条返回 `inconclusive` 并正常清理，不再使用像素或比例坐标
兜底。若返回 `UIA_ROOT_UNAVAILABLE`，记录屏幕分辨率、缩放比例、微信版本和错误时间。

### 浏览器打开后官网失败

这是允许的降级路径。程序会尝试 HTTPS、HTTP，然后使用普通网络检索。查看 Excel 的
`source_summary` 和 `error_summary` 判断是否已成功降级。

### 浏览器在官网多个页面之间来回操作

程序正在自动检查协会简介、领导、组织和联系等候选页面。当前 Demo 已限制为最多4个页面、
12次导航；不应持续超过约2分钟。如果明显循环或单协会长时间无进展，按 `Ctrl+C` 停止，
确认没有残留 CLI Python 进程后记录官网、页面和时间，不要继续启动第二个批次。

### 只生成协会名称，没有出现浏览器或微信

先查看 PowerShell 中最后出现的阶段进度。如果停在“正在发现官网”，通常是
搜索服务或 LLM 官网识别失败；当前程序会使用“协会名称”“协会名称 官网”
“协会名称 官方网站”三种查询合并候选，并在 LLM 返回非 JSON 时自动重试。
如果随后出现“正在使用网络检索补充基础信息”，说明没有确认到可靠官网，
因此不会打开浏览器。只有基础信息中识别出联系人姓名后，程序才会
进入对应的微信检索阶段。

### 输出 Excel 无法保存

关闭已经打开的同名 Excel 文件，或换一个新文件名后重试：

```powershell
--output ".\demo-output\association-demo-retry.xlsx"
```

## 7. 复盘材料

出现问题时请保留：

- 执行的完整命令；
- PowerShell 最后一行 JSON；
- 输出 Excel；
- 出错协会名称；
- 出错时处于官网、搜索结果还是详情页；
- 微信版本、Windows 显示缩放比例、深色/浅色主题；
- 出错时间。

微信加密证据默认位于：

```text
%LOCALAPPDATA%\AidWorkAgent\wechat-souyisou-rpa\artifacts
```

这些 `.dpapi` 文件只能由生成它们的 Windows 用户解密。不要通过聊天或邮件随意发送包含
手机号的 Excel；CLI 普通输出和错误日志已做手机号脱敏。

## 8. 演示前快速检查清单

- [ ] 项目目录正确；
- [ ] `venv` Python 可运行；
- [ ] LLM 与 Tavily 密钥已配置；
- [ ] Playwright Chromium 已安装；
- [ ] 微信已登录、主窗口打开、深色主题、无遮挡；
- [ ] `probe` 返回成功；
- [ ] `--dry-run` 能正确解析和去重；
- [ ] 单协会真实预跑成功或可接受地部分成功；
- [ ] 输出 Excel 可打开；
- [ ] Excel 已关闭，避免正式演示保存失败；
- [ ] 演示名单控制在2—3家；
- [ ] 准备新的输出文件名；
- [ ] 演示期间不操作鼠标、键盘和剪贴板。
