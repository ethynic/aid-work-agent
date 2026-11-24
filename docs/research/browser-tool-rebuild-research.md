# 通用浏览器工具重构选型调研

2026-11-19 · 已完成原包双端受控验证、Linux 沙盒接入冒烟与 Windows 实站验证。本期复用 `agent-browser` 官方发行二进制，首期面向 Windows Runtime。以下源码盘点用于理解和比对官方 MCP 的行为。关联[重构设计](../tools/browser/browser_tool_rebuild_design.md)（含开发完成记录）。

Runtime 的签名离线包和 stdio MCP 宿主可用于接入本机浏览器。此前 ChatGPT「Browser Use 集成研究」与「CDP网页信息格式」中的页面结构、元素引用和按需截图仍可参考。 [Runtime 插件与 MCP 源码实录](runtime-master-plugin-communication-20261111.md)

| 候选 | 借用方式 | 要验证的缺口 |
| --- | --- | --- |
| Chrome DevTools MCP | 对照基于无障碍树的文本快照和元素 UID，以及 Linux headless 与 Windows 可见 Chrome 的工具接口。 | 多标签与 iframe 的引用正确性、动态内容、限长正文、固定版本打包、进程回收、遥测与工具控制。 [官方配置](https://developer.chrome.com/docs/devtools/agents/get-started/configuration/) · [工具参考](https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/main/docs/tool-reference.md) |
| Microsoft Playwright MCP 或 Playwright 库 | 用 MCP 在相同任务上做对照。若上游 MCP 难以收敛权限或补齐正文等能力，比较直接使用库实现有限动作的成本。 | 动作等待、快照表达、打包及自行维护适配器的成本。生产隔离由项目宿主提供。 [Playwright MCP](https://github.com/microsoft/playwright-mcp) · [Playwright 可操作性检查](https://playwright.dev/docs/actionability) |
| Browser Use | 参考页面观测、按需图片与结构化结果。 | 完整 Agent 包含其自身决策回路，使用时需要重新对接项目的主任务状态和用量。若评估代码复用，先核对候选版本公开接口、默认服务和许可证。 [Browser Use](https://github.com/browser-use/browser-use) · [参数参考](https://docs.browser-use.com/open-source/all-parameters) |

## 开源项目如何划分工具入口

| 项目 | 公开入口 | 与 AID 的对应关系 |
| --- | --- | --- |
| Browser Use 1.x 开源 MCP | 官方 1.0.6 配置列出 `browser_exec`、`browser_screenshot`、`browser_login`、`browser_feedback`。其中网页动作主要通过模型生成的 Python 进入 `browser_exec`。0.x 迁移清单列出 15 个已移除的旧 MCP 工具名，其中包含会话管理和 Agent 接管。 | 入口数量取决于版本与能力封装方式。可参考持续浏览会话及模型根据观察继续操作的模式。 [MCP 文档](https://docs.browser-use.com/open-source/mcp) · [迁移说明](https://github.com/browser-use/browser-use/blob/main/MIGRATING.md) |
| Vercel agent-browser | CLI 用 `snapshot` 提供简洁的无障碍语义与短引用，动作引用该元素，`read` 独立提供当前浏览器页面的可读内容。 | 适合对照 AID 的语义操作与按需正文。验证其实际读取范围、返回体积及本地打包后，再考虑复用 CLI。 [仓库](https://github.com/vercel-labs/agent-browser) · [命令文档](https://agent-browser.dev/commands) |
| Playwright MCP | 导航、语义快照、点击和批量填表分别提供结构化入口。 | 用于对照同一表单的一次多项填写及动作回执。另需核对失败时后续项的实际行为。 [官方工具表](https://github.com/microsoft/playwright.dev/blob/main/mcp/introduction.mdx#available-tools) |
| Chrome DevTools MCP | 完整模式提供快照、点击、批量填表等独立工具。`--slim` 提供导航、脚本执行和截图三个入口。 | 完整模式可作为受控动作的候选执行器。Slim 模式的工具数量适合说明封装差异。 [完整工具表](https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/main/docs/tool-reference.md) · [配置](https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/main/docs/configuration.md) |

## 2026-11-19 agent-browser 原包验证

同一 Node 探针在两端调用 npm 官方 `0.43.0` 平台二进制。Windows 使用 `@agent-browser/cli-win32-x64-msvc` 和本机 Chrome `157.0.8103.79`，从进程查到标题为 `AID browser probe - Google Chrome` 的可见窗口。Linux 使用 `@agent-browser/cli-linux-x64-gnu`，在本机 WSL2 Docker 的 `aid-agent-api:latest` 镜像内以 UID 1000 运行，Chrome for Testing 为 `153.0.8010.12`。容器网络为 `none`，测试 HTTP 只使用容器内环回地址。进程参数有 `--headless`，环境无 `DISPLAY`，并设 `AGENT_BROWSER_NO_XVFB=1`。两端使用独立命名会话、测试 Profile、同一套合成 HTML，正常关闭后各自被测 Chrome 主进程由 1 个降到 0 个。每端 10 个探针退出码均为 0，部分探针专门记录与产品要求不同的上游行为。 [官方版本](https://github.com/vercel-labs/agent-browser/releases/tag/v0.43.0)

| 已确认能力或待定工具 | 两端实测 | 推荐复用方式 |
| --- | --- | --- |
| 打开网址、读取语义标签与短引用 | `open` 成功。`snapshot -i --json` 给同名 Details 按钮不同引用，并提供 `refs`、角色、名称和可读提示。按 `@e10` 点击确实执行了 Beta 行的按钮。 | 直接复用打开、语义识别与定位。适配输出，模型视图使用已确认的角色、名称和短引用。 |
| 单击并返回最新元素 | 单独 `click` 返回 `clicked` 与 `lifecycle`。后续 `snapshot -i` 读到新增按钮 `Continue Beta`。 | 适配层将单次动作结果与其后的快照合成同一个 AID 回执。 |
| 批量填表，首错停止，逐字段回执 | `batch --bail --json --omit-command` 成功写入全部 3 个字段。失败用例中第 1 项触发页面移除第 2 项，退出码 1，同时返回第 1 项成功与第 2 项找不到 Email 的原因。第 3 项原值为空，结果数组没有第 3 项。 | 复用批处理。AID 根据原请求和结果索引标明「已填写」「失败」「未尝试」，随后读取新快照。退出码 1 仍须保留已完成项的证据。 |
| Markdown 按需读取及已加载文字范围 | 原版 `read` 读取点击后出现的文本和屏幕外的已加载文字，不改变滚动位置。真实滚动后新加载的文字才出现。原版输出约 19,906 字符，包含 `display:none` 的合成文字，表格 `City/Qty` 被展平成 `CityQty`。探针另用固定页面读取逻辑取到可见文字和独立表格列。 | 复用会话与渲染。AID 需提供按可见内容提取并转 Markdown 的逻辑。固定提取探针证明可获取所需数据，通用转换尚待实现。 |
| 每段 5,000 字符及同内容续读 | 原生读取一次返回上面约 19,906 字符。修改页面之后再读，返回修改后的内容。上游返回没有供 AID 按原内容续读的入口。 | AID 在首次读取时保留本次 Markdown，续读按该次内容切段。 |
| 滚动及标签页，待确认是否开放 | `scroll down` 触发懒加载。点击 `target=_blank` 后，`tab list` 返回 `t1`、`t2`，可按标签 ID 切回原页或新页。 | 上游功能可以直接复用，模型是否获得入口仍待产品确认。 |
| stdio MCP 接入 | 两端 `initialize` 协商 `2025-03-26`，`tools/list`、`tools/call` 成功。`core,debug` 中的快照、读取及批处理都可返回结构化结果，同一命名会话沿用 CLI 创建的页面。 | 可直接复用 stdio。AID 发布自身工具名及有限参数，适配器在内部消费上游结果。 |

另测了短引用的边界。原节点被同名同角色的节点替换后，原引用能被 `agent-browser` 恢复到新节点并完成填写。首版若要求原节点失效后报错，这个上游行为需要专门评审，当前验证只确认了同一语义列表中的同名元素可区分。 [上游引用规则](https://agent-browser.dev/snapshots#ref-lifecycle)

### Linux 启动归属与沙盒

原包在 Docker 中直接启动 Chrome 时，实际参数包含 `--no-sandbox`。源码显示只要检测到 `/.dockerenv`，即使非 root 也会加入该参数。补充验证使用同一 Linux 镜像、UID 和断网容器，采用 Playwright 官方 seccomp 配置，由探针先启动 Chrome headless，将调试端点限制在容器内环回地址，然后让 `agent-browser connect` 接入。实测可导航并返回 `button "Works" [ref=e2]`，进程无 `--no-sandbox`，`chrome://sandbox` 显示 `Seccomp-BPF sandbox: Yes`。上游 `close` 断开后 Chrome 仍存活，启动它的探针负责退出回收。该路径只跑了接入冒烟，双端完整矩阵使用的是上游启动路径。 [固定版本 Chrome 启动源码](https://github.com/vercel-labs/agent-browser/blob/v0.43.0/cli/src/native/cdp/chrome.rs) · [容器 seccomp 说明](https://playwright.dev/docs/docker#crawling-and-scraping)

## Windows 有界面实站验证：百度搜索与跨页阅读

2026-11-19 使用同一 `agent-browser 0.43.0` 官方二进制，以 `--headed` 启动本机 Chrome 的独立测试会话。通过语义引用填写百度搜索框「企业级AI智能体头部企业」并点击搜索。从实际结果页快照读到前四条带「广告」，随后点击第一条未标广告的百度百科词条《2025中国AI智能体百强榜》。浏览器在新标签页打开词条，原包 `read` 返回发布机构、上榜企业示例和参考资料。 [百度百科词条](https://baike.baidu.com/item/2025%E4%B8%AD%E5%9B%BDAI%E6%99%BA%E8%83%BD%E4%BD%93%E7%99%BE%E5%BC%BA%E6%A6%9C/67237090)

从词条实际点击三条参考资料，经百科中间页的「继续」或「继续访问」打开原文。上观新闻转引上海静安报道，确认榜单由《互联网周刊》、德本咨询和 eNet 研究院联合发布，并报告迈富时位列第四。汉得发布稿说明灵猿智能体开发平台入选，通信世界网报道百融云创的结果云平台入选。 [迈富时报道](https://www.jfdaily.com/sgh/detail?id=1695095) · [汉得发布稿](https://baijiahao.baidu.com/s?id=1854426240088197300&wfr=spider&for=pc) · [百融云创报道](https://www.cww.net.cn/article?id=607572)

搜索提交后网址先变化，首次观察没有结果卡片，随后重新观察才读到可选结果。两种百科参考页的提示文字不同，按其中一种固定文案等待曾超时，重新观察页面后定位到实际的继续链接。原包 `read` 同时收录正文、导航和推荐广告。实站流程覆盖原生启动、填表、点击、观察、标签页切换和正文读取，结束后关闭本测试浏览器。

## AID 模型自主操作实站验证：打开出差申请并询问字段

2026-11-19 使用现有 AID `AgentEngine`、`ModelAdapter` 和服务器模型网关。临时工具适配器将模型选择的动作转到 Windows 原版 `agent-browser 0.43.0`，使用 stdio MCP 执行主要动作，对无引用菜单使用同原包的精确文本点击。用户任务从全新浏览器会话开始。模型自主选择打开登录页、按引用输入凭据、点击登录、打开办公管理和出差申请、点击新增。密码通过本机工具输入，未放入模型上下文。页面最终保持空白主表单，状态为「未发起」。

第一轮已打开空白表单，但模型继续探查多个明细页签，达到测试轮次上限。同一浏览器会话在补充提示后由模型切回主表单。一次输出截短遗漏了中段字段，随后通过完整主表单快照补齐，模型返回实际标有必填的字段、默认值和待用户补充项。

三段测试共 21 次模型调用，均收到 DeepSeek 网关的实际用量。输入 146,712 token，其中缓存命中 123,776，输出 5,266。独立探针验证了模型调用与用量采集，未写租户积分账本。高输入量来自历史中重复携带页面快照和继续查看明细页签，本轮数据可作为后续精简模型观察的对照。产品代码未改动。

同一浏览器会话中，AID 模型选择填写两个日期和一项文本，并调用原包 `upload` 上传用户指定的本机空文本文件。三个字段均回读成功，上传请求返回 HTTP 200，随后从附件列表的语义快照和链接文本确认文件名。上传动作的即时结果缺少稳定的附件列表反馈，模型在总结里误读了界面，独立回读确认附件实际已列出。表单保持「未发起」，未保存或提交，本轮未验证保存后的附件持久化或非空文件内容完整性。

## Runtime 本机接入边界验证

Windows 原版 `agent-browser 0.43.0` 的 stdio MCP 实测，`core` 列出 33 个工具，含导航、语义快照、交互、读取及 JavaScript 执行。`core,debug` 跨页共列出 73 个工具，新增上传、批处理、上游 Chat 和插件执行；上传入参包含 `selector` 和 `files`。将 `agent_browser_upload` 直接传给 `--tools` 会被拒绝，该选项接受预设分组。固定版本源码中 MCP 通过 `current_exe()` 调用同一二进制里的命令，交付时需保留对应命令和浏览器实现。原版二进制内含 Chat 模块；如果发行物要移除该模块，需要从固定源码构建裁剪版。裁剪版尚未构建。 [固定版本 MCP 源码](https://github.com/vercel-labs/agent-browser/blob/v0.43.0/cli/src/mcp.rs) · [固定版本程序入口](https://github.com/vercel-labs/agent-browser/blob/v0.43.0/cli/src/main.rs)

Runtime 共享核心已有 stdio 握手和工具调用。签名插件安装时探测入口的 `version --json`、`doctor --json`，并比对签名清单中的入口、工具和 schema 摘要。上游 MCP 本身具备协议握手，现有 Runtime 插件的包和回执契约仍需要接线。设备工具声明参与现有云端派发放行，模型可见的工具定义另由产品入口提供。Runtime 安装与本机调用的验收范围见[重构设计](../tools/browser/browser_tool_rebuild_design.md)的「首轮 Runtime 安装与调用边界」及其开发完成记录。 [第一方 MCP Provider 标准](../system/first-party-cli-mcp-provider-standard.md) · [Runtime 插件派发设计](../system/runtime-plugin-decoupling-dispatch-design.md)

## 上游 MCP 源码与能力盘点

已核对 `v0.43.0` 的 Git tag 指向 `1cc19526f3641c8543d55aa6700e7c55b37ac356`。Windows 原版通过 `mcp --tools all` 分 3 页返回 160 个不同工具，含具体浏览器操作、Chat 及上游产品管理工具。这是逐项追踪的总清单规模。下表是已核对的源码入口示例。 [固定源码树](https://github.com/vercel-labs/agent-browser/tree/1cc19526f3641c8543d55aa6700e7c55b37ac356)

| 已核对的上游方法示例 | 已定位源码与需要对照的行为 |
| --- | --- |
| `agent_browser_open`、`agent_browser_close` | `mcp.rs` 的 `call_open`、`call_close`。往下追踪 `main.rs`、`connection.rs`、`native/daemon.rs`、`native/actions.rs` 和 Chrome 启停、会话状态。 |
| `agent_browser_snapshot` | `mcp.rs` 的 `call_snapshot`。往下追踪 `native/actions.rs`、`native/snapshot.rs` 及元素引用解析。对照可操作元素角色、名称和同名元素区分。 |
| `agent_browser_click`、`agent_browser_fill`、`agent_browser_type`、`agent_browser_press` | `mcp.rs` 的对应 `call_*`，经命令解析进入原生操作。提取时连同所依赖的元素定位、输入和等待逻辑保留。 |
| `agent_browser_read` | `mcp.rs` 的 `call_read`、`commands.rs` 的读取解析及 `read.rs`。需核对活动页面读取和直接 URL 读取的差别，以及原包输出与产品 Markdown 约定的差别。 |
| `agent_browser_upload`、`agent_browser_batch` | `mcp.rs` 的 `call_upload`、`call_batch`。批量路径需核对允许执行的子动作及部分成功回执。ERP 空文件测试走原包 CLI 上传，此前只在 MCP 枚举中验证了上传方法和入参。 |

### 完整 MCP 清单与操作边界

对 Windows 原版 3 页 `tools/list` 逐项匹配固定提交 `mcp.rs` 的方法常量及 `call_tool` 分派，160 项全部匹配，各出现一次。一般执行链为 MCP 参数处理 → 同一程序的 CLI 命令 → 命令解析 → 原生浏览器处理。会话管理或本地状态等命令可由程序入口直接处理。以下工具名统一省略 `agent_browser_` 前缀。 [MCP 分派](https://github.com/vercel-labs/agent-browser/blob/1cc19526f3641c8543d55aa6700e7c55b37ac356/cli/src/mcp.rs#L2417) · [程序入口](https://github.com/vercel-labs/agent-browser/blob/1cc19526f3641c8543d55aa6700e7c55b37ac356/cli/src/main.rs#L1674) · [原生动作分派](https://github.com/vercel-labs/agent-browser/blob/1cc19526f3641c8543d55aa6700e7c55b37ac356/cli/src/native/actions.rs#L3521)

**137 个直接浏览器操作和相关执行控制。** 这组是能力分类统计。插件公开分组和 AID 每轮提供给模型的工具范围单独确定。

| 分组 | MCP 方法 | 上游实现入口 |
| --- | --- | --- |
| 导航、窗口、框架与会话（23） | `open`、`close`、`back`、`forward`、`reload`、`get_url`、`get_title`、`tab_new`、`tab_list`、`tab_switch`、`tab_close`、`window_new`、`frame_switch`、`frame_main`、`dialog_status`、`dialog_accept`、`dialog_dismiss`、`session`、`session_list`、`session_id`、`session_info`、`profiles`、`pushstate` | `commands.rs` 与 `native/actions.rs` 的页面、标签页、框架及对话框动作。会话枚举和全局关闭还检查 `main.rs`、`connection.rs`。 |
| 页面观察与正文（16） | `snapshot`、`read`、`get_text`、`get_html`、`get_value`、`get_attr`、`get_count`、`get_box`、`get_styles`、`is_visible`、`is_enabled`、`is_checked`、`find`、`diff_snapshot`、`diff_url`、`a11y` | 元素观察走 `native/actions.rs`、`native/snapshot.rs` 与元素引用解析。`read` 从 `handle_read` 进入，活动页分支使用 `read.rs` 的正文提取逻辑。 |
| 输入、滚动与等待（29） | `click`、`dblclick`、`fill`、`type`、`press`、`keydown`、`keyup`、`keyboard_type`、`keyboard_insert_text`、`hover`、`focus`、`check`、`uncheck`、`select`、`drag`、`scroll`、`scroll_into_view`、`wait_ms`、`wait_for_selector`、`wait_for_text`、`wait_for_url`、`wait_for_load`、`wait_for_function`、`mouse_move`、`mouse_down`、`mouse_up`、`mouse_wheel`、`tap`、`swipe` | `commands.rs` 解析点击、键鼠、等待及触摸命令，`native/actions.rs` 与交互、元素模块执行。 |
| 文件、截图与调试（18） | `upload`、`download`、`wait_for_download`、`screenshot`、`pdf`、`diff_screenshot`、`trace_start`、`trace_stop`、`profiler_start`、`profiler_stop`、`record_start`、`record_stop`、`record_restart`、`console`、`errors`、`highlight`、`inspect`、`vitals` | `native/actions.rs` 下的文件传输、截图、PDF、控制台与录制等分支，连同各自的原生处理模块。 |
| 网络与浏览器模拟（13） | `set_viewport`、`set_device`、`set_geo`、`set_offline`、`set_headers`、`set_credentials`、`set_media`、`network_route`、`network_unroute`、`network_requests`、`network_request`、`network_har_start`、`network_har_stop` | `commands.rs` 的 `set` / `network` 家族及 `native/actions.rs` 的浏览器模拟和网络分支。 |
| 浏览器状态与剪贴板（23） | `storage_get`、`storage_set`、`storage_clear`、`cookies_get`、`cookies_set`、`cookies_set_curl`、`cookies_clear`、`auth_save`、`auth_login`、`auth_list`、`auth_show`、`auth_delete`、`state_save`、`state_load`、`state_list`、`state_clear`、`state_show`、`state_clean`、`state_rename`、`clipboard_read`、`clipboard_write`、`clipboard_copy`、`clipboard_paste` | `native/actions.rs` 的存储、Cookie、登录和剪贴板分支。`main.rs` 还直接处理本地 `state` 命令和保存文件。 |
| 浏览器连接与画面流（5） | `get_cdp_url`、`connect`、`stream_enable`、`stream_disable`、`stream_status` | `main.rs`、`connection.rs` 与 Chrome 连接生命周期，画面流接 `native/actions.rs` 的 `stream` 分支。 |
| 动作组合及控制（5） | `eval`、`remove_init_script`、`batch`、`confirm`、`deny` | `mcp.rs` 的显式脚本、`call_batch` 和批准/拒绝入口，继续提取 `main.rs` 批处理和原有 `native/actions.rs` 执行控制逻辑。 |
| React 观察（5） | `react_tree`、`react_inspect`、`react_renders_start`、`react_renders_stop`、`react_suspense` | `mcp.rs` 将调用转给 `react` 命令，再由 `native/actions.rs` 和 `native/react/` 实际读取。 |

**其余 23 个入口。** 9 个涉及工作流、网页自带工具或其它平台，需按实际行为单列确认。14 个为上游 AI 或产品管理入口。

| 分组 | 入口 | 盘点结论 |
| --- | --- | --- |
| 流程录制与重放（4） | `flow_list`、`flow_run`、`flow_steps`、`flow_save` | 可将浏览器动作捕获为流程，再直接重放 HTTP 请求，某些路径回退到页面或原操作重放。它与执行调用者明确传入的 `batch` 不同，需单独确认是否属于本插件。 [流程执行源码](https://github.com/vercel-labs/agent-browser/blob/1cc19526f3641c8543d55aa6700e7c55b37ac356/cli/src/native/actions.rs#L12611) |
| 网页提供的 WebMCP（4） | `webmcp_list`、`webmcp_invoke`、`webmcp_result`、`webmcp_cancel` | 这些入口发现并调用网页自己提供的方法，工具与行为由网页定义。是否接入单独确认。 [WebMCP 源码](https://github.com/vercel-labs/agent-browser/blob/1cc19526f3641c8543d55aa6700e7c55b37ac356/cli/src/native/webmcp.rs) |
| iOS 模拟器（1） | `device` | 实现要求 macOS 与 Xcode。单列为平台能力，避免与 Chrome 模拟视口的 `set_device` 混淆。 [平台分支](https://github.com/vercel-labs/agent-browser/blob/1cc19526f3641c8543d55aa6700e7c55b37ac356/cli/src/native/actions.rs#L14972) |
| 上游产品管理（13） | `tools_profiles`、`skills_list`、`skills_get`、`skills_path`、`plugin_add`、`plugin_list`、`plugin_show`、`plugin_run`、`doctor`、`dashboard_start`、`dashboard_stop`、`install`、`upgrade` | 属于上游工具分组、安装、诊断、扩展和仪表盘管理。AID 提供自己的 Runtime 插件安装、版本和就绪诊断。 [MCP 分派源码](https://github.com/vercel-labs/agent-browser/blob/1cc19526f3641c8543d55aa6700e7c55b37ac356/cli/src/mcp.rs#L2611) |
| 上游 AI（1） | `chat` | 从 MCP 进入 `chat::run_chat`。该入口应处于插件调用范围之外，动作决策由 AID 完成。 [AI 入口源码](https://github.com/vercel-labs/agent-browser/blob/1cc19526f3641c8543d55aa6700e7c55b37ac356/cli/src/main.rs#L1670) |

同一方法也可能有不同的执行分支。例如 `read` 默认读取当前标签页，传 `url` 时走独立 HTTP 获取。上游 `open` 也支持启用流程录制或 WebMCP，这些参数属于公开工具的边界核对范围。 [读取分支](https://github.com/vercel-labs/agent-browser/blob/1cc19526f3641c8543d55aa6700e7c55b37ac356/cli/src/native/actions.rs#L6438)

### 原包 MCP 补验

在独立 Windows 有界面 Chrome 会话与本机环回测试页上，通过原版 MCP `tools/call` 上传 31 字节测试文件。页面显示上传确认，测试站收到的字节与文件完全相同。三项填写在同一次 `batch` 里执行，启用遇错停止。第 1 项成功且值可回读，第 2 项元素缺失并报告错误，第 3 项仍为空。批处理整体标记 `isError`，已执行项的结果数组仍在 `structuredContent.response`，AID 回执适配需覆盖这一形状。测试最后通过 MCP 关闭会话。 [上传源码](https://github.com/vercel-labs/agent-browser/blob/1cc19526f3641c8543d55aa6700e7c55b37ac356/cli/src/mcp.rs#L3152) · [批量调用源码](https://github.com/vercel-labs/agent-browser/blob/1cc19526f3641c8543d55aa6700e7c55b37ac356/cli/src/mcp.rs#L3861)

### 原版 MCP 直连复验

在 Windows 上用标准 MCP SDK 经 stdio 直连原版 `agent-browser 0.43.0`。本机环回测试页包含登录按钮、出差表单和上传接口。以下操作均通过原生 `tools/call`，工具名称与参数来自原包。登录只使用测试页面配套的虚构密码。

| 原生 MCP 方法 | 实际调用用途 |
| --- | --- |
| `agent_browser_open` | 启动有界面 Chrome 并打开本机测试页。 |
| `agent_browser_snapshot` | 读取密码框、按钮、链接和表单字段的语义及短引用。 |
| `agent_browser_type` | 对密码框逐键输入测试字符串。 |
| `agent_browser_click` | 按短引用点击登录按钮和出差表单链接。 |
| `agent_browser_wait_for_text` | 等待登录、导航或附件上传的页面反馈。 |
| `agent_browser_get_url` | 核对已进入本机测试表单网址。 |
| `agent_browser_batch` | 执行调用方给出的三条填写命令。验证第二条报错后第三条未执行。 |
| `agent_browser_get_text` | 回读页面显示的数值和文本，确认部分失败及最终填写结果。 |
| `agent_browser_fill` | 按短引用填写表单。 |
| `agent_browser_press` | 发送 Tab 键。 |
| `agent_browser_read` | 读取当前标签页的正文并找到填报说明。 |
| `agent_browser_upload` | 将测试文件交给文件输入框，核对测试站收到原样 31 字节。 |
| `agent_browser_close` | 关闭测试浏览器会话。 |

默认分组通过 `tools/list` 返回 33 项，下表记录全部默认工具，省略统一前缀 `agent_browser_`。

| 分组 | 原生方法 |
| --- | --- |
| 导航与会话 | `open`、`back`、`forward`、`reload`、`close`、`tab_new`、`tab_list`、`tab_switch`、`tab_close` |
| 页面观察 | `snapshot`、`read`、`screenshot`、`get_text`、`get_url`、`get_title` |
| 页面交互 | `click`、`fill`、`type`、`press`、`check`、`uncheck`、`select`、`scroll`、`eval` |
| 等待 | `wait_ms`、`wait_for_selector`、`wait_for_text`、`wait_for_load` |
| 流程录制与目录 | `flow_list`、`flow_run`、`flow_steps`、`flow_save`、`tools_profiles` |

`core,debug` 分组返回 73 项，这次只用该分组补充调用 `upload` 和 `batch`，其他 11 项从默认分组调用。

### 历史测试适配器的模型上下文复核

复核原 ERP 测试脚本，启动原版 `agent-browser 0.43.0 mcp` 时只传配置和会话，使用默认工具分组。临时测试脚本里的 `BrowserPort.definitions()` 分别向登录和打开表单、只读字段、填写上传三个阶段提供 5、1、4 项测试工具。定义位于实验脚本，AID 负责推理和工具选择，适配器调用原包。精确文字点击、部分回读和原 ERP 附件上传走同一原包的本机 CLI。

用原脚本的工具定义、AID `AgentEngine`、`ModelAdapter` 与真实 DeepSeek 网关重跑。在 `chat_with_tools` 入参处记录工具名称和紧凑 UTF-8 JSON 大小。DeepSeek 对已是函数调用格式的定义原样纳入请求。ERP 原会话只读，填写和上传使用独立的本机测试页。

| 测量位置 | 数量 | 工具定义数组字节数 | 验证结果 |
| --- | ---: | ---: | --- |
| 原版 MCP 默认 `tools/list` | 33 | 24,628 | 1 页，没有上传或 Chat |
| 原版 MCP `--tools all` | 160 | 85,771 | 3 页，包含上传和 Chat，只做目录观测 |
| AID 查看 ERP 原表单的模型请求 | 5 | 1,751 | 每轮相同，模型选择观察并确认表单未发起 |
| AID 填表与上传的模型请求 | 4 | 1,346 | 每轮相同，日期和事由回读一致，本机测试页收到原样 32 字节文件 |

4 项工具实测为 `browser_observe`、`browser_fill_date`、`browser_fill_reason`、`browser_upload_attachment`。工具枚举和 AID 每轮模型请求各自计数。137 项是上游能力盘点，插件默认 MCP 公开面与 AID 每次传给模型的工具清单仍需分别确定。字节数只统计工具定义数组，模型 token 账单还包括提示词、历史和网页观察。

早期讨论覆盖 11 项浏览器交互需求，用于后续核对 AID 输出体验。包括打开指定网址、仅给模型看可操作元素的语义标签、同名元素使用不同短引用、普通动作逐条执行并附最新元素列表、一次填写同一表单的多项，以及首错停止并区分成功、失败和未尝试项。正文独立按需读 Markdown，读取已加载的可见内容，长文分段并提示续读，每段默认最多 5,000 字符，续读沿用首次读取的内容与顺序。浏览器动作完成与网站业务结果分别核对。

## 原包离线接入验证

- Windows x64 官方 `agent-browser.exe` 实测 20,043,776 字节，约 19.1 MiB。将同一文件以 gzip 试压为 8,716,259 字节，约 8.3 MiB。这是单个可执行文件的试压结果，实际 `.aidplugin.zip` 体积以签名出包为准。测试时标准 MCP 客户端直接启动原生执行器并使用已有 Chrome。Chrome 和浏览器运行数据另计。 [官方发行](https://github.com/vercel-labs/agent-browser/releases/tag/v0.43.0)
- `mcp --tools core` 实测既不枚举 `agent_browser_chat`，`tools/call` 直接请求该方法也返回 `-32602`，说明服务端拒绝执行。`core,debug` 同时枚举 `agent_browser_upload`、`agent_browser_batch` 和 `agent_browser_chat`。官方 MCP 分组是进程启动选项。禁用 MCP 调用后，Chat 代码仍在原版二进制中。 [原生分组和调用检查](https://github.com/vercel-labs/agent-browser/blob/v0.43.0/cli/src/mcp.rs)
- 原包 `version --json` 实测返回 `Unknown command: version`。当前 Runtime 签名插件的安装探测要求它与签名清单的身份、入口、工具 schema 摘要等字段一致，并读取 `doctor --json` 的就绪字段。执行回执还须满足 Runtime 的 `OperationResult`。原生 stdio 握手通过只证明协议传输可用。 [Provider 规范](../system/first-party-cli-mcp-provider-standard.md)

已确认以第一方 `.aidplugin.zip` 随包交付原样的官方 Windows x64 exe 与许可文本。Runtime 通过现有 stdio 入口启动插件，插件转发获准的原生 MCP 名称及参数，并生成 Runtime 标准回执。插件自声明供 Runtime 验签、登记和派发，生产 Agent 的模型工具目录由 AID 负责。隔离验收复用本机 Chrome、Runtime 的固定 Node 及仅授权浏览器插件的测试签名根。 [签名包交付手册](../guides/deployment/runtime-boss-release-guide.md)

默认 `core` 可作为直接验证路径。如果需要上传和批处理，内部可启用 `core,debug`，插件端只公开允许的原生方法，并在执行入口也拒绝 `chat`。浏览器执行器环境只传运行所需配置，AID 模型凭据留在模型调用路径。还需验证实际签名安装包、Runtime 授权行为和公开工具名单。
