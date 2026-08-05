# 微信 RPA 连续查询会话交接记录

## 目的与范围

本文记录协会资料补全中，正式 `collect` 微信 RPA 连续查询的窗口/焦点现象、真机结论和最终交接方案。
方案包含两处最小改动：批次条目之间的 Alt+Tab 交接，以及单条 `collect` 点击后对独立可信详情 HWND
的受限交接；输入回读、列表证据、手机号判断等业务契约保持不变。

## 已确认的真机事实

1. 连续查询第 1～8 条完整通过。
2. 第 9 条为“中国黄金协会 / 周洲”。该条在 `flow_probe` 中打开了独立详情窗口，暴露出搜索主页、
   结果列表和详情层可能对应不同顶层 HWND 的情况；此前第 9→10 条成功结论来自探针脚本，不是正式
   `collect`。
3. 第 9 条完成后，第 10 条虽然把微信主窗口置前，但微信内部键盘焦点已经失效；仅依赖视觉前台和
   再次 `Ctrl+F → Down → Enter` 不能稳定恢复。
4. `flow_probe` 第 9 条完成后，在第 10 条探针启动前发送一次 Alt+Tab、等待 1 秒，第 9→10 条成功；
   正式 `collect` 曾在第 9 条点击后因仍绑定结果列表 HWND 而报 `FOREGROUND_LOST:click`，没有进入
   条目间交接。
5. 对普通路径第 1→2 条执行同样交接也成功。
6. 必须只发送一次 Alt+Tab；不是两次，也不在首条查询前发送。

## flow_probe 暴露的窗口层级与焦点陷阱

隔离 `flow_probe` 先后确认：

- 搜索主页、结果列表、详情可能切换或复用不同的可信 `WeChatAppEx.exe / Chrome_WidgetWin_0` HWND；
- 关闭详情和结果列表后保留搜索主页是正确状态，不应再关闭第三层；
- Qt 微信主窗口成为视觉前台，不等于微信内部快捷键焦点已经可用；
- 通过增加第三次关闭、主窗口 settle 或重复三键来修补，会改变真实页面栈，不能替代批次条目间交接；
- 正式方案同时处理两个边界：单条内部只在受保护点击后接纳本次产生的可信详情 HWND；两次正式查询
  之间执行一次外部焦点切换，再由现有启动逻辑重新置前微信。

## 最终实现位置与状态机

正式 `collect` 现增加单条内部的受限阶段性交接：只有在原 HWND guard 保护的点击完成后，才允许把
当前前台重新按完整 `WeChatAppEx.exe / Chrome_WidgetWin_0` 身份核验并接纳为详情 HWND；外部窗口、
身份不完整窗口仍立即拒绝。详情返回结果页时只接受本次会话已见过的 HWND，并在结果页证据复验成功
后重新绑定后续截图和终态清理目标。query/列表证据、手机号解析及统一顶层清理契约均不放宽。

CLI 每批次创建一个 `ProjectAssociationProviders`；UI 每次运行也由 factory 新建一个 provider。
`AssociationBatchEnricher` 在该实例内串行调用绑定的 `wechat_mobile`，所以 provider 实例能够准确表示
同一批次的微信查询序号，包括同一协会的会长/秘书长和后续协会。

状态机如下：

- 初始查询：无交接资格，不发送 Alt+Tab；
- RPA 子进程实际启动，但异常、超时、返回非零或清理状态不确定：不授予下一条交接资格；
- RPA 返回 `ok=true` 且 `session_closed=true`：授予下一条一次性交接资格；
- 下一次 `wechat_mobile` 启动前：先消费资格，发送一次 Alt+Tab，等待 1000ms，再进入原有 PowerShell
  `collect` 启动逻辑，由原逻辑激活微信主窗口；
- Alt+Tab 或等待失败：资格保持已消费，返回会话级致命 `WECHAT_HANDOFF_FAILED`，禁止启动下一条，
  也禁止后续补发。

该设计保证首条不发送、连续查询之间只发送一次，并在上一条未实际启动或清理不确定时 fail safe。

## 隐私与审计

普通审计只记录安全的 `query_index` 和 `handoff_performed`，不记录 query、手机号或页面正文。交接使用
固定 Alt/Tab 虚拟键事件，按键释放由 `finally` 保证；等待固定为 1 秒。

## 历史结果有效性

此前 20 家协会结果没有建立搜索输入回读证据，所有成功率、`not_found` 和卡片定位结论继续作废，
不得用于对外验收或销售演示。必须使用当前输入回读、清理和条目间交接契约重新跑正式样本。

## 新会话测试顺序

正式改动已通过 PowerShell 静态测试 279 项、PowerShell 语法/公共库加载检查，以及相关 Python 回归
43 项；尚未运行修复后的正式 `collect` 真机 9→10。后续应按以下顺序执行：

1. 单元测试：首条零 Alt+Tab；确认关闭后的第二条恰好一次 Alt+Tab + 1 秒；第三条仍每个间隔一次；
   失败或不确定清理后不得发送；交接失败不得启动下一 RPA。
2. 最小真机：普通第 1→2 条，确认一次交接且两条均成功。
3. 独立详情路径：用正式 `collect` 复现“中国黄金协会 / 周洲”第 9 条，再验证第 9→10 条；既有
   `flow_probe` 成功记录不能替代该验收。
4. 连续 20 条：无人干扰，记录每条启动、完成、`session_closed` 和交接次数，不保存 query 正文。
5. 最后再运行正式协会补全，生成新的 Excel；旧 Excel 不复用。

## 验收标准

- N 条实际启动且明确完成的连续微信查询，Alt+Tab 总数严格为 N-1；
- 首条之前为 0 次，每个合格条目间隔恰好 1 次，等待 1000ms；
- 第 1→2 和第 9→10 两条已知路径均成功；
- 上一条未启动、超时、非零退出或 `session_closed` 非 true 时，下一条不发送 Alt+Tab；
- 交接失败时批次安全停止，不启动下一条、不补发；
- 单条内部输入、复制、详情、证据与清理行为无回归；
- 正式 20 条连续运行完成后，才重新评估手机号召回率和产品稳定性。

## 2026-08-05 重构接手决策

停止继续在正式 `collect` 的固定 `$pluginGuard` 上叠加特殊分支。以已稳定运行的 `flow_probe` 窗口顺序为基线，建立正式与诊断同源的窗口会话状态机：会话启动时快照旧插件；打开后接纳本轮列表 HWND；仅在受保护点击后接纳非旧插件的可信详情 HWND；同 HWND 详情后退，独立详情关闭后回到已登记列表；终态只关闭本会话窗口。任何外部前台、旧插件抢前台、非预期 HWND 或部分关闭失败都保持稳定失败码并禁止授予下一条交接资格。

本轮只进行自动化重构和行为测试，不运行微信真机。完成开发、独立测试和 Code Review 后，再按单条周洲、正式 9→10、正式 1→2、连续 20 条的顺序验收。

## 2026-08-05 换机开发交接（最新状态，以本节为准）

### 用户确认的业务规则

- 官网采集部分保留，当前阶段不要重写。
- 模型解析部分保留；只要模型依据用户提供/程序采集的文本成功解析，就接受该结果，不增加“真实性”、
  “是否可信”或最小文本长度等主观门禁。源文本本身有误不属于解析器拒绝理由。
- 微信 RPA 中，内容不可读和窗口安全必须分开：详情复制为空、打不开、OCR/内容判断不可用时跳过当前
  候选；全部候选不可读则当前 query 返回 `inconclusive`，正常清理并继续下一个 query。只有外部窗口、
  本会话窗口无法确认/关闭、cleanup 部分失败等窗口安全问题才中断批次。
- 用户要求后续流程简化为“开发自测 -> 独立测试 -> 用户真机观察”，不再单独安排 Code Review。

### 当前代码已经实现的内容

1. 正式 Python provider 外层 RPA 超时由 120 秒改为 600 秒：
   `src/services/association_enrichment_providers.py` 中
   `_WECHAT_RPA_TIMEOUT_SECONDS = 600`。PowerShell 正式 `search/collect` 使用 9 分钟工作截止，并为
   最长阻塞调用和 cleanup 保留预算；稳定内部码为 `WECHAT_WORK_TIMEOUT`。
2. 正式 `collect` 已接入 HWND 窗口会话状态机：登记 Main/List/Detail HWND，独立详情用 Ctrl+W，
   同 HWND 详情用 Alt+Left，终态只清理本会话 owned 窗口。
3. 正式详情确认打开后固定等待 5 秒，再复制正文；等待前后均复验前台，且计入工作预算。
4. 详情复制为空、全空白或仍是结果页时，不调用详情 OCR/内容模型，安全返回后继续候选；非空文本不再
   使用最小长度或“真实性”门禁。
5. 搜索列表判断已经删除截图视觉模型链路：不存在 `stage=visual_verify`、列表截图 OCR 或图片 payload。
   列表 Judge payload 只有 `association_name`、`person_name`、`text`；保留的截图只用于本地页面哈希、
   页面变化验证和详情 OCR，不参与元素定位。
6. 已完整撤回按像素或 UIA 点击“文章”分类的实验；当前正式流程保留“全部”结果列表，不发送
   `Ctrl+Tab`，也没有分类固定坐标或下划线像素定位。
7. 正式详情入口只使用可信搜索 HWND 内的 UIA 双语义候选和物理 `ClickablePoint`；旧卡片 band、
   比例 locator、固定相对点和 `flow_probe` 命令均已删除。

### 已确认的真机结果

- 正式单条“中国黄金协会 / 周洲”曾完整执行和清理成功。
- 正式连续“周洲 -> 陈新华”多次肉眼确认：详情和搜一搜窗口能够关闭，最后只剩微信聊天主窗口；
  条目间第二条启动审计为 `handoff_performed=true`。
- 旧版本中曾出现肉眼清理成功但程序返回 `RECOVERY_FAILED`；随后已完成内容/窗口证据解耦和自然关闭
  路径修改，但尚未得到连续两条都返回 `ok=true` 的最终真机验收。
- 删除结果页截图视觉模型后，不再出现 `visual_verify` 路径；一次正式运行结果为周洲成功，陈新华在
  点击阶段报 `INPUT_FOCUS_LOST`。用户观察到陈新华曾打开另一小程序，关闭后连结果列表一起关闭，
  说明“全部”分类混入百科/小程序是重要干扰源。
- Phase 1E8 第一次快捷键真机运行（2026-08-05 17:36 左右）在周洲阶段约 40 秒后返回：
  `FOREGROUND_LOST`, `stage=recover`，第二条没有启动。**本会话结束前用户尚未反馈这次是否肉眼成功
  切到“文章”以及打开/关闭了哪条文章，因此 Ctrl+Tab 真机效果尚未验收。**

### 当前唯一优先问题

先确认 Phase 1E8 的一次 `Ctrl+Tab` 在新电脑微信版本上的真实行为，不要继续叠加恢复分支：

1. 用单条“周洲”运行正式 `collect`，用户观察搜索结果是否从“全部”切到“文章”，确认只切一次。
2. 若未切换，先检查微信版本/快捷键行为，不做坐标回退。
3. 若已切换，记录它打开的文章、关闭详情后的前台和结果窗口是否仍存在；结合最新 failure artifact
   定位 `FOREGROUND_LOST/recover` 的确切窗口状态。
4. 单条返回 `ok=true, session_closed=true` 后，再跑正式“周洲 -> 陈新华”。
5. 两条均成功后再验证普通 1->2，最后才做连续 20 条。

### 正式与测试脚本说明

- `clients/wechat-souyisou-rpa/scripts/wechat-souyisou.ps1`：正式 PowerShell 入口，仅支持
  `probe/open/search/collect`；当前主要开发目标是正式 `collect`。
- `clients/wechat-souyisou-rpa/scripts/wechat-souyisou-lib.ps1`：窗口身份、会话状态机、键盘、剪贴板、
  页面证据和预算公共函数。
- `clients/wechat-souyisou-rpa/scripts/llm_judge.py`：列表/详情的文本 Judge 适配器；结果列表不得传图片。
- `clients/wechat-souyisou-rpa/scripts/extract-mobile.ps1`：详情 OCR/手机号补充路径，不用于列表截图判断。
- `clients/wechat-souyisou-rpa/scripts/read-artifact.ps1`：读取当前 Windows 用户 DPAPI 加密 artifact；只在
  本机同一用户下可解密。换电脑后旧 artifact 通常不能解密，应重新真机生成。
- `src/services/association_enrichment_providers.py`：Python 正式 provider、600 秒外层超时、query 间
  Alt+Tab handoff 和 PowerShell 结果契约。
- `tmp_spike/run_formal_wechat_09_10.py`：当前真机临时 runner，依次运行“中国黄金协会/周洲”和
  “中国饭店协会/陈新华”，输出每条 ok/mobile_found 以及安全的 handoff 审计。它是验收工具，未纳入
  正式产品入口。
- `clients/wechat-souyisou-rpa/tests/test-window-session.ps1`：可执行窗口会话/预算/恢复行为测试。
- `clients/wechat-souyisou-rpa/tests/test-static.ps1`：PowerShell 静态契约回归；测试数按脚本中的
  顶层 `Assert` 动态统计。
- `clients/wechat-souyisou-rpa/tests/test_llm_judge.py`：文本 Judge 协议和 payload 测试。
- `clients/wechat-souyisou-rpa/tests/test_ocr_adapter.py`：详情 OCR adapter 测试；列表视觉 OCR 已删除。
- `tests/unit/services/test_association_batch_enrichment.py`：provider/handoff/超时/批次行为测试。
- `tests/unit/services/test_association_enrichment_cli.py`：CLI 相邻回归。

### 换机后建议先运行的自动测试

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File clients/wechat-souyisou-rpa/tests/test-window-session.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File clients/wechat-souyisou-rpa/tests/test-static.ps1
python -m pytest clients/wechat-souyisou-rpa/tests/test_llm_judge.py clients/wechat-souyisou-rpa/tests/test_ocr_adapter.py tests/unit/services/test_association_batch_enrichment.py tests/unit/services/test_association_enrichment_cli.py -p no:cacheprovider -q
```

历史主控结果曾为窗口行为测试通过、静态测试硬编码输出 `279/279`、Python `67 passed + 8 subtests`；
旧静态计数与旧比例点击探针均不再作为当前验收依据。

### Git/工作区注意事项

- 当前工作区有大量此前阶段的已修改和未跟踪文件，**尚未提交**。不要使用 `git reset --hard`、
  `git checkout --` 或整仓覆盖来“清理”，否则会丢失用户和前序会话改动。
- 换电脑前必须通过用户认可的方式把整个工作区改动带过去；仅拉远端仓库无法获得当前进度。
- 不要把 `demo-output`、DPAPI artifact 或临时 runner 当正式产品代码提交；提交范围需另行审查。

## 2026-08-05 Ctrl+Tab 真机结论

新电脑微信版本中，用户手工测试和两次正式单条 `collect` 均确认 `Ctrl+Tab` 不再切换到“文章”分类。
两次查询都成功获得完整搜索结果文本，但深色卡片像素定位返回零候选并以 `CARD_LOCATE_FAILED / points`
安全结束，`session_closed=true`。正式流程已移除 Phase 1E8 的 `Ctrl+Tab`、2 秒等待和切换后二次复制，
直接使用首次稳定的搜索结果文本及页面进入 Judge。后续深色主题重跑仍确认像素 band 返回零候选，
因此该定位器不能继续作为当前微信版本的生产详情入口。

## 2026-08-05 UIA 详情定位结论与实现

用户在双屏、微信所在显示器 150% 缩放下手工打开“中国黄金协会 / 周洲 联系人”的“全部”结果页。
只读 UIA 探测确认可信 `WeChatAppEx.exe / Chrome_WidgetWin_0` 内存在真实结果 `Button/ListItem`；线程
设为 Per-Monitor DPI Aware V2 后，筛选同时包含协会名和人员名的节点，得到最上方 Button 的
`BoundingRectangle=(2347,756,988,190)`、`ClickablePoint=(2841,851)`。点击前完整复验同一可信前台，
按物理坐标单击一次，成功打开视觉上的第二条链接详情；第一条百科因不具备双语义而被排除。文章标签
的 UIA Invoke 虽返回成功但页面未切换，因此文章分类暂不进入正式实现。

正式 `collect` 已以同 HWND 内 UIA 候选替代暗色卡片像素 band：只接纳可见、支持 Invoke 且具有
ClickablePoint 的 Button/ListItem，以协会名和人员名在内存筛选，排除百科、小程序、顶部分类和右侧栏，
按 Button 优先、从上到下排序并对嵌套标题去重。UIA 和 Per-Monitor V2 `SetCursorPos` 都使用物理屏幕
坐标，150% 缩放不做除法或窗口原点换算，负屏幕坐标保持有效。详情点击后的 HWND 接纳、5 秒等待、复制/Judge/OCR/返回和 cleanup 契约
均保持不变。下一步必须先独立自动测试，再由用户观察单条“周洲”真机，不在开发阶段自动运行微信。

单条真机随后确认：UIA 打开了正确详情，固定停留 5 秒，完成复制/Judge，并正确关闭详情返回列表；
该详情没有手机号。返回列表后 UIA 不再产生新候选属于枚举自然结束。正式实现现经一次有界恢复后记录不含正文的
`uia_candidates_unavailable/no_new_uia_candidate`，保留 `checked` 计数并交给既有
`Get-WeixinCollectStatus`，随后正常 cleanup；任何窗口安全失败仍保持致命。

## 2026-08-05 旧像素定位完整删除

正式 UIA 真机链路确认后，旧像素元素定位不再保留：已删除暗色卡片 band、视觉搜索框定位、显式比例
locator、比例坐标详情分支，以及通过固定相对点点击结果的 `flow_probe` 命令和对应 fixture/合成位图
测试。仅服务旧定位器的错误语义同时删除。

保留的图像用途不参与 UI 元素识别：结果/详情截图哈希只验证点击前后页面变化，详情中央截图只供 OCR，
滚轮坐标只用于把滚动焦点保持在已验证窗口内容区。正式输入继续使用可信键盘导航与精确回读，正式详情
只使用已验证 HWND 内的 UIA 双语义候选和物理 `ClickablePoint`；窗口状态机、DPI、Judge、OCR 与 cleanup
门禁均未放宽。历史章节中的 `flow_probe` 仅记录当时发现窗口层级的过程，不表示该命令仍可调用。
