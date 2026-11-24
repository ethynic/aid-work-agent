# 浏览器 MCP 第一方插件设计

关联[原包验证与接入调研](../../research/browser-tool-rebuild-research.md)、[第一方 Provider 规范](../../system/first-party-cli-mcp-provider-standard.md)和 [Runner / Desktop / Runtime 共同契约](../../system/runner-desktop-runtime-integration-contract.md)。

## 已确认范围

复用官方发行的 `agent-browser` 原生 MCP 浏览器执行器。取消从上游提取源码、自建浏览器执行器和维护上游源码副本的路线。以此前实测的 `agent-browser v0.43.0` 作为首轮接入验证基线。 [官方发行版本](https://github.com/vercel-labs/agent-browser/releases/tag/v0.43.0)

浏览器在 Windows Runtime 本机以有界面 Chrome 运行，AID 负责模型、工具选择和任务编排。上游内置 Chat 及其模型调用排除在插件调用范围之外。未来桌面 Agent 复用 Runtime 本机插件方式。

## 原版 MCP 行为基线

保留原生 MCP 的工具名、参数含义和浏览器操作行为。官方原包的能力盘点用于核对工具来源和原版实测结果。Runtime 安装的包契约、原版 MCP 的工具分组与 AID 每轮提供给模型的工具清单分别核对。

原包裸二进制提供标准 MCP stdio。现有第一方 Runtime 插件使用签名离线包、机器可读版本、就绪诊断和标准回执。接入时按现行契约核对这两个接口的差异。 [Runtime 签名插件安装手册](../../guides/deployment/runtime-boss-release-guide.md) · [Provider 规范](../../system/first-party-cli-mcp-provider-standard.md)

## 首轮 Runtime 安装与调用边界

AID 第一方离线包随包交付原版 Windows x64 执行器和许可。插件接线使用现有 `mcp --stdio`、`version --json`、`doctor --json` 与 `tool_declaration`，使用原生 MCP 工具名和输入 schema。首轮对照原版 `core`，并额外提供上传和批处理。通用接线负责签名声明、转发和 Runtime 标准回执。 [原包离线接入验证](../../research/browser-tool-rebuild-research.md#原包离线接入验证)

上游进程启动 `core,debug`，插件公开面和实际调用入口使用同一份工具定义。`agent_browser_chat` 在插件中不可调用，包括从公开的批处理入口间接调用。浏览器进程获得执行所需环境，模型凭据留在 AID 调用路径。插件声明采用现有版本 1。Runtime 继续使用已交付的管理接口、Device API 和 stdio 宿主。

隔离验收的上传文件由本机用户放入 `%LOCALAPPDATA%\AID-Work\browser-uploads`。`agent_browser_upload` 和批处理中的 `upload` 都使用原生参数，执行前检查文件位于该目录内。批处理回执保留原生逐项结果和未执行项的位置，供 AID 判断下一步。 [Provider 输入与结果规范](../../system/first-party-cli-mcp-provider-standard.md#52-输入)

截图通过独立的 `agent_browser_screenshot` 保存到插件临时目录。批处理保留页面操作和等待，拒绝指定下载输出路径的等待选项。需要打开新标签页时使用显式传入 HTTP(S) 地址的 `agent_browser_tab_new`，由插件检查地址。

本机先验证隔离的验收 Runtime。由仓库当前 Host 构建验收实例，使用单独的运行目录及仅覆盖浏览器插件的临时 Ed25519 测试根。已安装的 0.0.38 Host 按三种既有插件固定信任范围，本轮保留其安装资源和配对状态。

Runtime 心跳报告签名声明中的原生工具名，模型工具目录由 AID 产品侧管理。本阶段通过本机测试 Device API 验证真实 Runtime 的任务领取、浏览器操作及回执。生产 Agent 工具投影和正式发布信任分别验收。 [工具声明与派发契约](../../system/runner-desktop-runtime-integration-contract.md)

## 开发完成记录

2026-11-24 补记；原开发计划已清理，Git 历史可查。✅ 开发完成（2026-11-19）。Phase 0 验证原包直接复用、体积与 Chat 隔离；Phase 1 接线原生 MCP 与第一方 Provider（原生 35 项，保留原工具名及输入 schema，Chat 调用拒绝）；Phase 2 隔离签名与离线验包通过，真实 Host 导入后已启用、就绪；Phase 3 本地 Device API 派发、可见 Chrome、批处理、上传和 Chat 拒绝通过；Phase 4 配套 Runtime 1.3.0 与浏览器 0.1.0 出包，八项 Host 检查通过。遗留：生产 Agent 工具投影和正式发布信任分别验收。
