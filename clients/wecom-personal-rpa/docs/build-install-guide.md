# 编译打包安装实战指南（MSI 版）

> 适用：`clients/wecom-personal-rpa/`（.NET 8 客户端）
> 配套：[操作手册.md](./操作手册.md)（全景视图）、[STATUS.md](../STATUS.md）、[installer/wix/README.md](../installer/wix/README.md)
> 目标读者：负责把客户端打成 MSI 装到客户机的工程师/运维

---

## ⚠️ 联调会话遗留问题（2026-06-25，新会话必读）

本指南原本只覆盖「编译→打包→安装」，但 2026-06-25 联调会话发现并修复了一系列**前后端协同 bug**。这些修复都已落地（前后端代码 + 文档），但**联调最后一步未完成**——客户端发的 status callback 仍被服务端拒（`event_type Input should be 'status' [input_value='Status']`）。

新会话接手时，**先读本节**，再继续往下看基础流程。

### 已完成（不要重做）

| # | 改动 | 文件 |
|---|------|------|
| 1 | MSI 打包链路打通（WiX v5 + 7 个兼容坑 + 64 位 + 子目录文件 + 中文 codepage） | `installer/wix/WeComRpa.wxs`、`scripts/build-msi.ps1`、`scripts/build-and-install.ps1` |
| 2 | 平台后台 RPA 绑定管理（列表 + 新增绑定 + 轮换密钥 + 改 agent_base_url） | `frontend/src/components/saas/RpaBindingPanel.vue` 等 |
| 3 | 后端 `/all_bindings` 改为以 client 为基准（之前从 conversation_bindings 查导致新建 client 看不到） | `src/channels/wecom_personal_rpa/db.py` `list_all_bindings_rich` |
| 4 | 后端 `/config` 响应加 `client_id` / `tenant_id` / `config_id` 字段 | `src/channels/wecom_personal_rpa/schemas.py`、`src/saas/api/wecom_personal_rpa_routes.py:728` |
| 5 | 后端 `/config` 鉴权用新函数 `_make_get_secret_by_client_id()`，**不强制 tenant_id 匹配**（client_id 全局唯一，HMAC 签名已证明身份） | `src/saas/api/wecom_personal_rpa_routes.py:177` |
| 6 | 后端 `/config` 取 config_id 改为从 `tenant_channel_configs.id` 字段（之前误用 `configs[0].get("config_id")` 字段名） | `src/saas/api/wecom_personal_rpa_routes.py:744-751` |
| 7 | 客户端 callback/ws 路径改为动态拼接 `t/{tenant_id}/wecom_personal_rpa/callback/{config_id}`（之前是固定常量 `api/v1/channels/...`） | `clients/wecom-personal-rpa/src/Client.Core/AgentApi/AgentApiClient.cs` |
| 8 | 客户端首启顺序：`PostCallbackAsync` 之前先 `EnsureCallbackRoutingAsync` 同步拉 `/config` 拿 tenant_id/config_id | 同上 |
| 9 | 写 ConfigTool CLI（DPAPI 加密写入 client_config.enc） | `clients/wecom-personal-rpa/src/Client.ConfigTool/`（新工程） |
| 10 | 写 SnakeCaseEnumJsonConverter（PascalCase 枚举 ⇄ snake_case JSON，对齐服务端 Pydantic Literal） | `clients/wecom-personal-rpa/src/Client.Core/Serialization/SnakeCaseEnumJsonConverter.cs`（新文件） |
| 11 | 5 个枚举改用 SnakeCaseEnumJsonConverter：EventType / ConversationType / InboundMessageType / PausedScope / AccountStatus / ErrorCode | `clients/wecom-personal-rpa/src/Client.Core/Protocol/*.cs` |

### 联调最后一步未完成（新会话首要任务）

**症状**：客户端 status callback 仍被服务端 400 拒：
```
RPA callback 信封校验失败: event_type
  Input should be 'message', 'status' or 'action_result'
  [input_value='Status', input_type=str]
```

**已做的修复**（但似乎在生产 exe 里没生效）：写了 `SnakeCaseEnumJsonConverter`，5 个枚举都加了 `[JsonConverter(typeof(SnakeCaseEnumJsonConverter<>))]`。

**诊断方向**（按可能性排序）：

1. **客户端 exe 没真正重打**：联调会话最后一次 `build-and-install.ps1` 可能 publish 出错或 MSI 没重装。新会话先确认 `C:\Program Files\WeComRpa\app\Client.App.exe` 的修改时间是不是最新的。
2. **`ProtocolJsonOptions.Instance` 没注册 SnakeCaseEnumJsonConverter**：枚举上的 `[JsonConverter]` 特性应该自动生效，但 `ProtocolJsonOptions.cs:22` 里有个 `new JsonStringEnumConverter()` 在 Converters 列表里——可能它优先级高于枚举上的特性，把所有枚举都按 PascalCase 序列化了。**这是最可疑的点**。检查 `clients/wecom-personal-rpa/src/Client.Core/Protocol/ProtocolJsonOptions.cs:22`，把 `new JsonStringEnumConverter()` 删掉，让枚举用自己的 `[JsonConverter]`。
3. **客户端构造 InboundEvent 时绕过了 JsonSerializer**：检查 `AgentApiClient.PostCallbackAsync` 实际用什么序列化 InboundEvent。如果用的是 `_jsonOptions`（来自 `ProtocolJsonOptions.Instance`），就回到怀疑点 2。
4. **TypeModel 缓存了旧版本**：极少见，但 .NET 偶尔有 JIT 缓存问题。清理 `bin/`、`obj/`、`publish/` 后重打。

**验证手段**：直接看客户端实际发出的 JSON 字节。在 `AgentApiClient.PostCallbackAsync` 里加一行临时日志，把序列化后的 `json` 字符串打出来（**记得事后删，不能泄露 secret 但 event_type 不是 secret**）。看到底是 `"event_type":"Status"` 还是 `"event_type":"status"`。

### 其他已知问题（不阻塞主流程，但应修）

| # | 问题 | 影响 |
|---|------|------|
| A | `DesktopState.IsLocked` P/Invoke 入口点找不到：`SystemParametersInfoGetScreensaver` 在 user32.dll 不存在 | HealthSupervisor 每次报 `EntryPointNotFoundException`，被 try/catch 吞了但日志刷屏。修法：改成 P/Invoke `SystemParametersInfo`（user32.dll 真实存在），uiAction 用 `SPI_GETSCREENSAVERRUNNING = 0x0072` |
| B | `TrayApp.OnRelogin` DI 注入报错 `No service for type 'RpaHost'` | 用户点「重新登录」会失败。修法：在 `App.xaml.cs.ConfigureServices` 把 `RpaHost` 注册成 `AddSingleton<RpaHost>` 或修复 `OnRelogin` 的服务定位方式 |
| C | ConfigTool 默认输出 `%LOCALAPPDATA%\WeComRpa\client_config.enc`，但客户端读 `%LOCALAPPDATA%\WeComPersonalRpa\Client.App\data\client_config.enc` | 用户必须手动 cp 配置文件，否则客户端读不到。修法：ConfigTool 默认输出路径改成跟客户端读取路径一致 |
| D | `.env` 必须设 `RPA_SECRET_KEY`，否则 `register_client` / `rotate_client_secret` 都会 RuntimeError | 文档没说明。修法：在 `.env.example` 加注释 + 在 `docs/system/wecom-personal-rpa-design.md` 补部署前置条件 |
| E | `build-and-install.ps1` 之前的 msiexec 异步 bug 已修（改用 `Start-Process -Wait`） | 已修，无需再动 |

### 关键路径速查（新会话定位代码用）

- 客户端启动入口：`clients/wecom-personal-rpa/src/Client.App/App.xaml.cs`
- 客户端配置加载：`clients/wecom-personal-rpa/src/Client.App/Services/ClientOptionsLoader.cs`（读 `%LOCALAPPDATA%\WeComPersonalRpa\Client.App\data\client_config.enc`）
- 客户端 HTTP 请求：`clients/wecom-personal-rpa/src/Client.Core/AgentApi/AgentApiClient.cs`
- 客户端 JSON 序列化选项：`clients/wecom-personal-rpa/src/Client.Core/Protocol/ProtocolJsonOptions.cs`
- 客户端日志：`%LOCALAPPDATA%\WeComPersonalRpa\Client.App\logs\client-YYYYMMDD.log`
- 服务端 callback 路由：`src/saas/api/wecom_personal_rpa_routes.py:191`
- 服务端 /config 路由：`src/saas/api/wecom_personal_rpa_routes.py:663`
- 服务端 Pydantic 信封校验：`src/channels/wecom_personal_rpa/schemas.py` 的 `RpaCallbackEnvelope`
- 服务端鉴权：`src/channels/wecom_personal_rpa/auth.py` `verify_request`
- 服务端 secret 加解密：`src/channels/wecom_personal_rpa/secret_crypto.py`（主密钥来自 `RPA_SECRET_KEY` 环境变量）
- 数据库连接：`aid_work_agent2` 库，`wecom_rpa_clients` / `wecom_rpa_accounts` / `wecom_rpa_conversation_bindings` / `tenant_channel_configs` 四张关键表

---


> 写作日期：2026-06-24（首次 MSI 真机打通）

---

## 0. 这份文档解决什么

旧 [操作手册.md](./操作手册.md) §0 把 WiX/MSIX 安装包标为「不可，仅占位 README」。本文档记录的是**首次把 MSI 真机打通的全过程**，包括：

- WiX 版本选择（v5 vs v7，含 OSMF 许可坑）
- 7 个 WiX v5 兼容性修复
- MSI 64 位打包的正确写法（避免装错到 `Program Files (x86)`）
- 子目录文件没装上的修复
- 安装后的完整验证清单

读完这份文档，有代码的内部人员在 `clients/wecom-personal-rpa/` 目录下就能从零打 MSI 并装到客户机。**整个流程封装在 `scripts/build-and-install.ps1` 里，可一键自动化**。

---

## 1. 环境前置（构建机）

| 项 | 要求 | 验证命令 |
|----|------|---------|
| .NET SDK | 8.0.x（含 `windows` 内置 TFM） | `dotnet --version` |
| PowerShell | ≥ 5.1（Windows 自带） | `$PSVersionTable.PSVersion` |
| WiX v5 CLI | **5.0.2**（不要装 v7） | `wix --version` |
| WiX UI 扩展 | `WixToolset.UI.wixext/5.0.2` | `wix extension list`（v5.0.2 有 bug 不显示，看下面验证法） |

### 1.1 为什么不用 WiX v7

WiX v7 引入 **OSMF（Open Source Maintenance Fee）许可**：

- 个人/年收入 < $10,000 的小组织：免费，但要 `wix eula accept wix7` 接受 EULA
- 年收入 > $10,000 的商业组织：需要付费赞助 `wixtoolset` GitHub 组织

v5 完全免费、无 EULA 坑。**内网项目统一用 v5**。

### 1.2 安装 WiX v5 + UI 扩展

```powershell
# 装 WiX v5.0.2（注意：默认装的是 v7，要显式指定版本）
dotnet tool install -g wix --version 5.0.2

# 验证版本
wix --version
# 预期：5.0.2+aa65968c

# 装 UI 扩展（wix extension add 在 v5.0.2 是静默的，没输出是正常的）
wix extension add -g WixToolset.UI.wixext/5.0.2

# 验证扩展已装（看目录而不是 list 命令，因为 list 在 5.0.2 有 bug）
Test-Path "$env:USERPROFILE\.wix\extensions\WixToolset.UI.wixext"
# 预期：True
```

---

## 2. 编译与发布

### 2.1 Debug 编译验证 baseline

```powershell
cd clients\wecom-personal-rpa
dotnet build WeComPersonalRpaClient.sln -c Debug
```

**期望**：`0 个警告 0 个错误`。如果挂了，**不要继续**，先修代码（很可能是新加的源文件未纳入工程）。

### 2.2 发布自包含单文件 exe

```powershell
powershell -ExecutionPolicy Bypass -File scripts\publish.ps1
```

**期望产物**：

| 文件 | 路径 | 大小 |
|------|------|------|
| Client.App.exe | `publish\app\Client.App.exe` | ~269 MB（self-contained + WPF + .NET runtime） |
| Client.Supervisor.exe | `publish\supervisor\Client.Supervisor.exe` | ~68 MB（self-contained，无 WPF） |
| Client.Core.xml | `publish\{app,supervisor}\Client.Core.xml` | XML 文档 |
| e_sqlite3.dll | `publish\supervisor\e_sqlite3.dll` | SQLite native 库（必须随包） |
| client.example.yaml | `publish\app\configs\client.example.yaml` | 配置模板 |
| wecom_nodes.yaml | `publish\app\assets\wecom_nodes.yaml` | 节点资产 |

**体积异常小（< 50 MB）** = 没启用 self-contained，检查 `publish.ps1` 的 `--self-contained` 参数。

---

## 3. 构建 MSI

### 3.1 一行命令

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-msi.ps1
```

产物：`publish\WeComRpa-1.0.0.msi`，体积约 115 MB。

### 3.2 已修复的 7 个 WiX v5 兼容坑（仅供排错参考）

历史踩坑记录，**正常情况下你不需要看**。如果改了 `.wxs` 后 build 报错，对照这张表排查：

| # | 错误码 | 现象 | 根因 | 修复 |
|---|--------|------|------|------|
| 1 | WIX7015 | OSMF EULA required | WiX v7 强制接受 EULA | 降级到 v5 |
| 2 | WIX0144 | Extension 'WixUIExtension' not found | WiX v5 `-ext` 要用完整包名 + 需先 `wix extension add` | 脚本用 `-ext WixToolset.UI.wixext`；预先 `wix extension add -g WixToolset.UI.wixext/5.0.2` |
| 3 | WIX0004 | MajorUpgrade/@DowngradeMessage unexpected | v5 改名 | 改为 `@DowngradeErrorMessage` |
| 4 | WIX0005 | Component contains unexpected child 'Files' | v5 移除 `<Files>` 通配 | 改用逐个 `<File>` 显式列举 |
| 5 | WIX0094 | WixUI:WixUI_InstallDir inaccessible | v4/v5 内置 UI dialog 不能用 `<UIRef>` | 改用 `<UI><ui:WixUI Id="WixUI_InstallDir" /></UI>` + 加 `xmlns:ui="http://wixtoolset.org/schemas/v4/wxs/ui"` 命名空间 |
| 6 | WIX0103 | Cannot find File '..\..\publish\...' | v5 `Source` 相对路径基准是 cwd 不是 .wxs 目录 | 用 `$(var.PublishRoot)` 绝对路径变量，`build-msi.ps1` 通过 `-d "PublishRoot=<abs>"` 注入 |
| 7 | WIX0311 | String not available in code page '1252' | 默认西欧编码不支持中文 | `<Package>` 加 `Codepage="65001"`（UTF-8） |

### 3.3 装到 `(x86)` 而不是 `Program Files`（64 位声明）

**症状**：MSI 装到 `C:\Program Files (x86)\WeComRpa\` 而不是 `C:\Program Files\WeComRpa\`。

**根因**：WiX v5 移除了 `Package/@Platform` 属性，64 位 MSI 必须用 `wix build -arch x64` 命令行参数声明。`build-msi.ps1` 已加此参数。

### 3.4 子目录文件没装上

**症状**：MSI 安装后 `app\configs\` 和 `app\assets\` 子目录不存在，但 `app\Client.App.exe` 在。

**根因**：WiX v5 的 `<File>` 元素只装文件本身，**不会自动创建所在的子目录**。子目录必须在 `<Directory>` 树中显式声明，且 `<File>` 要挂到对应 `<DirectoryRef>` 下。

正确写法见 `installer/wix/WeComRpa.wxs`：

```xml
<!-- 目录树必须显式声明子目录 -->
<StandardDirectory Id="ProgramFiles64Folder">
  <Directory Id="INSTALLDIR" Name="WeComRpa">
    <Directory Id="APPDIR" Name="app">
      <Directory Id="APPASSETSDIR" Name="assets" />
      <Directory Id="APPCONFIGSDIR" Name="configs" />
    </Directory>
    <Directory Id="SUPDIR" Name="supervisor" />
  </Directory>
</StandardDirectory>

<!-- 文件挂到对应 DirectoryRef 下 -->
<DirectoryRef Id="APPASSETSDIR">
  <Component Id="AppAssets" Guid="...">
    <File Id="..." Source="$(var.PublishRoot)\app\assets\wecom_nodes.yaml" KeyPath="yes" />
  </Component>
</DirectoryRef>

<!-- Feature 树必须引用新组件，否则不会被装 -->
<Feature Id="AppFeature">
  <ComponentRef Id="AppAssets" />
  <ComponentRef Id="AppConfigs" />
  ...
</Feature>
```

---

## 4. 安装 MSI

### 4.1 安装前必做

1. **关闭企业微信**（不只是关窗口，要从托盘退出）：
   ```powershell
   Get-Process | Where-Object { $_.ProcessName -like "*WXWork*" -or $_.ProcessName -like "*WeCom*" } | Stop-Process -Force
   ```
2. **管理员权限**运行 PowerShell（msiexec 写 `Program Files` 和注册服务都要管理员）

### 4.2 安装命令

**命令行带 UI（推荐首次）**：

```powershell
msiexec /i "C:\path\to\WeComRpa-1.0.0.msi"
```

**静默安装（自动化场景）**：

```powershell
msiexec /i "C:\path\to\WeComRpa-1.0.0.msi" /qn /norestart
```

**自定义安装目录**：

```powershell
msiexec /i "WeComRpa-1.0.0.msi" /qn INSTALLDIR="D:\WeComRpa" /norestart
```

### 4.3 安装时发生什么

| 行为 | 实现位置 |
|------|---------|
| 文件落地 `C:\Program Files\WeComRpa\{app,supervisor}\` | `<File>` + `<DirectoryRef>` |
| 注册 Windows 服务 `WeComRpaSupervisor`（Start=auto） | `<ServiceInstall>` + `<ServiceControl>` |
| 开始菜单快捷方式 | `<Shortcut>` 在 `ProgramMenuFolder` 下 |
| HKCU Run 登录自启 | `<RegistryValue Root="HKCU" Key="...\Run">` |
| 安装标记 | HKCU `Software\WeComRpa\Install` 几个 marker |

### 4.4 SmartScreen 拦截

MSI 未签名时，Windows SmartScreen 会弹「Windows 已保护你的电脑」。

- 点「更多信息」→「仍要运行」即可绕过
- 生产环境**必须签名**：`build-msi.ps1 -SignPfx "C:\cert\codesign.pfx" -SignPfxPassword <密码>`

---

## 5. 安装后验证清单

**5 项全部 True / 正确才算装好**：

```powershell
# 1. 安装位置正确（Program Files，不带 x86）
Test-Path "C:\Program Files\WeComRpa\app\Client.App.exe"
# 期望：True

# 2. 子目录和文件齐全
Test-Path "C:\Program Files\WeComRpa\app\assets\wecom_nodes.yaml"
Test-Path "C:\Program Files\WeComRpa\app\configs\client.example.yaml"
# 期望：True

# 3. HKCU Run 自启路径正确
(Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "WeComRpaApp").WeComRpaApp
# 期望：C:\Program Files\WeComRpa\app\Client.App.exe（不带 x86）

# 4. 服务已注册
Get-Service -Name "WeComRpaSupervisor" | Select-Object Name, Status, StartType
# 期望：WeComRpaSupervisor Stopped Automatic
# Status=Stopped 是正常的，Client.Supervisor 服务虽然注册了但当前没起；下次开机或手动 Start-Service 才 Running

# 5. 开始菜单快捷方式
Test-Path "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\企业微信RPA\企业微信RPA客户端.lnk"
# 期望：True

# 6. 已安装程序列表
Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" | Where-Object { $_.DisplayName -like "*企业微信*RPA*" } | Select-Object DisplayName, DisplayVersion, InstallLocation
# 期望：企业微信个人账号 RPA 客户端 / 1.0.0 / C:\Program Files\WeComRpa\
```

---

## 6. 卸载

```powershell
# 命令行卸载
msiexec /x "C:\path\to\WeComRpa-1.0.0.msi"

# 或用 ProductCode 静默卸载（自动化场景）
$productCode = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" | Where-Object { $_.DisplayName -like "*企业微信*RPA*" }).PSChildName
msiexec /x "$productCode" /qn /norestart
```

卸载自动发生：停服务 → 删服务 → 删文件 → 删快捷方式 → 删 Run 注册表项 → 删安装标记。

**配置文件（`client.yaml`）不会被删**——它由 App 首次运行时从 `client.example.yaml` 复制生成，属于用户数据。

---

## 7. 升级

`<MajorUpgrade>` 支持**大版本升级**（同 UpgradeCode 高版本替换低版本）：

```powershell
# 旧版 1.0.0 已装，直接装新版 1.1.0：
powershell scripts\build-msi.ps1 -Version 1.1.0
msiexec /i "WeComRpa-1.1.0.msi"
# MSI 自动：停旧服务 → 卸旧版 → 装新版 → 起新服务
```

**禁止降级**：检测到更高版本已装时弹窗报错拒绝。

> `UpgradeCode = 7B2F3A4C-9D81-4E6B-B5C3-1A0F2E3D4C5B`，**一经发布不可改**，否则升级链断裂。

---

## 8. 一键自动化：`scripts/build-and-install.ps1`

把上面 publish + build-msi + 卸载旧版 + 安装新版 + 验证 全流程封装到一个脚本：

```powershell
# 完整流程（构建 + 卸载旧版 + 装新版 + 验证）
powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1

# 只构建 + 打 MSI（不安装）
powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1 -SkipInstall

# 指定版本号
powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1 -Version 1.2.3
```

脚本会：
1. 跑 `publish.ps1` 生成二进制
2. 跑 `build-msi.ps1` 打 MSI
3. 检测旧版（同 UpgradeCode），自动卸载
4. 调 `msiexec /i` 装新版（带 UI）
5. 跑第 5 节的 6 项验证清单，打印结果

---

## 9. 常见问题排查

### Q1: build-msi.ps1 报 "WIX0144: extension not found"

→ WiX UI 扩展没装，跑：

```powershell
wix extension add -g WixToolset.UI.wixext/5.0.2
```

### Q2: 装完后 `Program Files` 没有但 `Program Files (x86)` 有

→ MSI 没声明 64 位。确认 `build-msi.ps1` 第 61 行有 `-arch x64`：

```powershell
& wix build $wxs -arch x64 -d "WixMsiVersion=$Version" -d "PublishRoot=$publishDir" -ext WixToolset.UI.wixext -o $outMsi
```

### Q3: 装完后 `app\configs\` 不存在

→ `.wxs` 里 configs 子目录声明或 `<File>` 挂载有问题。看 `installer/wix/WeComRpa.wxs` 的 `APPASSETSDIR` / `APPCONFIGSDIR` 写法。

### Q4: 服务装上了但 Status=Stopped

→ 正常现象。MSI 只注册服务不立即启动。下次开机自启，或手动：

```powershell
Start-Service -Name "WeComRpaSupervisor"
```

### Q5: 升级装新版时被识别为"已安装"跳过

→ 同 UpgradeCode 同版本号，MSI 认为已装。要么先卸载旧版，要么改 `-Version` 号让它识别为新版。

### Q6: Client.App 启动报"找不到 client.yaml"

→ `client.yaml` 必须手动从 `client.example.yaml` 复制并填值。App 启动时不会自动生成。详见 [操作手册.md](./操作手册.md) §5。

---

## 10. 关联文档

- [操作手册.md](./操作手册.md) — 全景视图，包含启动 App、配置 client.yaml、服务端联调等
- [installer/wix/README.md](../installer/wix/README.md) — WiX 工程本身的设计说明
- [STATUS.md](../STATUS.md) — 客户端开发状态盘点
- 设计：`docs/system/wecom-personal-rpa-design.md` §10.2 部署
- 协议：`docs/system/wecom-personal-rpa-protocol.md` §A.7 版本兼容
