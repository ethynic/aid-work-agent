# 招聘 CLI 客户端部署封装设计（npm install 一键装机）

> 状态：📋 设计完成，待开发（2026-08-19 与用户对齐需求后成稿）
> 背景：runtime + boss CLI 目前在开发机以仓库目录方式手工运行；部署到客户电脑
> 必须封装为可交付安装包，目标「一条 PowerShell 命令装完，重启后自动可用」。

## 0. 既有基础（2026-08-11 已交付，勿重复建设）

- **tgz 分发机制**：runtime `prepack --install-links` 把 boss 包落实体捆绑进 tar
  （bin：`aid-runtime` / `boss-cli`）；boss 包 files 含 `dist + scripts`（win-click.ps1）。
  两包已能 `npm install -g <tgz>` 独立运行（m07 验收指南 §2.2）。
- **配对与凭证**：`aid-runtime pair --code <8位码> --server <url>`；token 经 DPAPI
  加密存 `%APPDATA%\aidwork-tool-runtime\credentials.bin`；config.json 存 server/device_id。
- **可靠性（进程内已有）**：pollLoop 心跳 5s/长轮询 20s、断线指数退避 1s→30s、
  401 fail-loud、MCP 子进程崩溃自动 respawn、SIGINT 优雅停。
- **doctor 双层自检**：runtime doctor 6 项（config/DPAPI/心跳全链路/boss 入口/子进程
  doctor/桌面可交互）；boss-cli doctor 4 项（脚本/CDP/attach/登录态）。
- **attach-only 铁律**：绝不 spawn/杀死 Chrome；要求用户以独立调试实例运行 Chrome
  （`--remote-debugging-port=9222 --user-data-dir=<独立目录>`，Chrome 136+ 默认 profile
  会静默忽略调试端口参数）。

## 1. 目标与非目标

**目标**：客户 IT 或普通员工在 Windows 电脑上：
1. `install.ps1` 一条命令完成安装（Node 检查引导 → 装 tgz → 写配置 → 建 Chrome 快捷方式 → 注册开机自启 → doctor 自检 → 配对引导）
2. 重启/登录后 runtime 自动常驻，无需手工开控制台窗口
3. `upgrade.ps1` 一条命令升级到新版本
4. 排障自助：`doctor --report` 导出诊断包发支持

**非目标（明确不做）**：
- 不做 SYSTEM 服务（NSSM/Windows Service）——runtime 必须跑在用户会话内操作
  用户桌面的 Chrome 与鼠标，服务化反而不适用
- 不做私有 npm registry（一期 tgz 文件直发；registry 后置）
- 不做 Intune/域静默批量推送（后置）
- 不动 attach-only Chrome 铁律（不代客户启动/登录 Chrome）

## 2. 交付物形态

```
aidwork-recruiting-client/           ← 交付目录（zip 发给客户）
├── agent-tool-runtime-0.2.0.tgz     ← runtime（捆绑 boss CLI，单包即可运行）
├── install.ps1                      ← 一键安装
├── upgrade.ps1                      ← 一键升级
├── uninstall.ps1                    ← 卸载（解除自启 + npm rm -g + 询问保留配置）
├── 部署说明.docx                    ← 图文步骤（面向客户 IT）
└── VERSION.txt                      ← 版本与兼容说明
```

`install.ps1 -Server https://agent2.aidingyi.cn [-InstallDir] [-NoAutoStart] [-SkipChromeShortcut]`

## 3. install.ps1 安装步骤（核心设计）

1. **前置检查**：Windows 10/11 x64；Node ≥22（未装则引导 `winget install OpenJS.NodeJS.LTS`
   或离线 node.msi；版本不符 fail-loud 列当前版本）；npm 可用。
2. **安装包**：`npm install -g --prefix "%APPDATA%\aidwork-npm" <tgz 绝对路径>`
   （用户级安装不需要管理员权限；PATH 注入用户环境变量）。
3. **写配置**：`%APPDATA%\aidwork-tool-runtime\config.json` 预置 `{"server": <参数>}`
   （已有 config 结构不动，只预填 server；已存在则跳过不覆盖——升级场景保配置）。
4. **Chrome 调试实例快捷方式**：桌面创建「BOSS 助手浏览器」快捷方式，目标
   `chrome.exe --remote-debugging-port=9222 --user-data-dir=%LOCALAPPDATA%\aidwork-chrome`
   （探测 chrome.exe 路径：注册表 App Paths → Program Files 标准位置；探测失败输出手工指引）。
   首次使用引导：用该快捷方式打开并登录 BOSS 直聘（附在安装完成输出里）。
5. **开机自启**：`schtasks /Create /TN "AidWorkToolRuntime" /SC ONLOGON /TR <cmd> /F`
   —— 登录触发、用户权限、隐藏窗口（`wscript` 包装或 `conhost --headless` 起
   `aid-runtime start`；日志重定向到文件，不再依赖控制台窗口）。
6. **doctor 自检**：自动跑 `aid-runtime doctor`，逐项输出 ✓/✗ 与修复指引
   （Chrome 未开调试实例时 doctor 的 CDP 项会 ✗——属预期，输出「请先用桌面快捷方式
   打开浏览器并登录 BOSS」）。
7. **配对引导**：输出三步图文（Web「本地工具」页生成配对码 → `aid-runtime pair
   --code XXXX --server <url> --name <本机名>` → `aid-runtime start` 或注销重登）。

## 4. 常驻与自愈

- **看门狗**：`schtasks /Create /SC MINUTE /TN "AidWorkToolRuntimeWatchdog"` 每分钟
  检查进程存在（`tasklist` 过滤 node+cli.js start），不存在且已配对则拉起——
  轻量、零第三方依赖；拉起前检查 credentials 存在（未配对不盲目拉起）。
- **日志**：`%APPDATA%\aidwork-tool-runtime\logs\runtime.log`（启动器重定向 stdout；
  简单轮转：单文件超 5MB 改名 .1，保留 2 代）。
- **优雅停机**：schtasks 删除时 `taskkill` 走 SIGINT 路径（已有 20s 协作退出）。

## 5. 升级与版本门禁

1. **upgrade.ps1**：stop（schtasks /Run 停 + taskkill）→ `npm install -g <新 tgz>` →
   doctor → start；config/credentials 天然保留（不在 npm 目录）。
2. **版本号规范**：两包同步升版（runtime 0.2.0 起，boss 跟随）；`npm pack` 固化到
   `package.ps1`（一键双包构建 + 冒烟 `npm install -g --prefix` 隔离目录验证——
   把 m07 指南的手工冒烟步骤脚本化，防 files 漏文件类事故重演）。
3. **云端版本门禁激活**（把 catalog.py 的死字段 `min_provider_version` 用起来）：
   heartbeat 时比对 runtime_version < min_provider_version → 设备标记
   `deprecated=true`（devices 表加列），Web 设备页黄牌提示「请升级」；invocation
   claim 不拦截（软门禁——避免客户正在用的链路突然断，只提醒不阻断）。

## 6. 诊断与支持

- `aid-runtime doctor --report`：输出 zip（config 脱敏 + doctor 全项结果 + 最近
  500 行日志 + 版本信息 + Node/Chrome 版本）到桌面，用户直接发群里。
- 部署说明.docx 含「常见问题」表（迁移自 m07 §排查表，去内部化表述）。

## 7. Phase 划分（每阶段独立可验收）

> **已先行落地**：`clients/README.md` 部署手册（编译新版本/安装/Chrome 调试实例/
> 绑定服务器配对/doctor 自检/手工开机自启/升级卸载/常见问题）——一键脚本完成前，
> 手工装机照该手册执行即可。

| Phase | 内容 | 验收 |
|---|---|---|
| 1 安装闭环 | package.ps1（版本化+隔离冒烟）；install.ps1 全步骤；客户部署说明；uninstall.ps1 | 全新 Windows 虚拟机：一条命令装完 → Chrome 快捷方式登录 → pair → 注销重登后 runtime 自起 → Web 设备页在线 |
| 2 升级与门禁 | upgrade.ps1；看门狗 + 日志轮转；云端 min_provider_version 软门禁 + 设备页版本展示 | 装旧版 → upgrade.ps1 到新版功能无损；杀进程 1 分钟内自愈；旧版本设备页黄牌 |
| 3 诊断与交付打磨 | doctor --report 诊断包；部署说明终稿；交付目录 zip 组装脚本 | 支持角色模拟：仅凭交付包+文档在无网内网机装通（tgz 离线装） |

## 8. 风险与对策

- **客户 Chrome 路径千差万别**（企业策略/Edge 优先）：探测失败降级为输出手工指引，不阻塞安装。
- **winget 不可用/离线环境**：tgz 与 node.msi 一并放入交付目录，install.ps1 支持离线参数。
- **多用户同机**：npm 用户级安装 + 配对凭证按 Windows 账户隔离（DPAPI CurrentUser 天然隔离），文档注明「每用户各自安装配对」。
- **风控敏感**：交付文档明确「专用调试实例浏览器 + 真人操作期间勿动」话术，降低客户误用主 Chrome 的封号风险。
