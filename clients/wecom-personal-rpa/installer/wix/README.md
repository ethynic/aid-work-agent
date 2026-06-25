# 安装包（WiX Toolset v5 / MSI）

本目录承载企业微信个人账号 RPA 客户端的 **WiX v5 MSI 安装包工程**，把 `publish\` 下的发布产物（Client.App + Client.Supervisor）打成可安装/卸载/升级的 Windows Installer（MSI），含 Supervisor 服务安装与 App 登录自启。

> 本 README 取代旧的占位说明。实现依据见设计文档 §10.2「首版必须包含 — 部署」。

---

## 1. 目录与产物

| 文件 | 作用 |
|------|------|
| `WeComRpa.wxs` | WiX v4/v5 架构源（`<Wix>` + `<Package>`），定义产品、目录、文件、服务、快捷方式、UI、升级 |
| `WeComRpa.wixproj` | MSBuild 工程文件（SDK 风格，可选；`wix` CLI 路径不依赖它） |
| `README.md` | 本文档 |

构建产物：`clients\wecom-personal-rpa\publish\WeComRpa-<version>.msi`

---

## 2. 依赖（构建机）

| 依赖 | 安装方式 | 用途 |
|------|----------|------|
| **WiX v5 CLI** | `dotnet tool install -g wix` | `wix build` 编译 .wxs 为 .msi（推荐路径） |
| 或 WiX v5 VS 扩展 | Visual Studio 扩展市场搜 "WiX Toolset v5" | `msbuild` 编译 .wixproj（CI/VS 路径） |
| .NET SDK | 已有（wix CLI 是 dotnet tool） | wix CLI 运行基础 |
| **Windows SDK / signtool**（可选） | Windows SDK 安装时勾选 | MSI 与 exe 代码签名 |
| 代码签名证书（生产必备） | 内部 CA 或第三方 EV/OV 证书 | 签名 MSI 与 exe |

> **2026-06-24 已完成首次真机实编译验证**：WiX v5.0.2 + UI 扩展 + 7 项兼容性修复 + 64 位 `-arch x64`，MSI 装到 `C:\Program Files\WeComRpa\`，8 项验证全通过。详见 [docs/build-install-guide.md](../../docs/build-install-guide.md)。
>
> **必须用 WiX v5**（不要 v7）：v7 引入 OSMF 许可限制，年收入 > $10,000 的商业组织要付费。装法：`dotnet tool install -g wix --version 5.0.2` + `wix extension add -g WixToolset.UI.wixext/5.0.2`。

---

## 3. 构建步骤

```powershell
# 1) 发布产物（必须先跑，生成 publish\app\ 与 publish\supervisor\）
cd clients\wecom-personal-rpa
powershell scripts\publish.ps1

# 2) 构建 MSI（默认 1.0.0 版本号）
powershell scripts\build-msi.ps1

# 2a) 指定版本号
powershell scripts\build-msi.ps1 -Version 1.2.3

# 2b) 构建 + 代码签名（PFX 方式）
powershell scripts\build-msi.ps1 -Version 1.2.3 -SignPfx "C:\cert\codesign.pfx" -SignPfxPassword <密码>

# 2c) 构建 + 代码签名（证书指纹方式，证书已装入证书库）
powershell scripts\build-msi.ps1 -Version 1.2.3 -SignCertThumbprint "abc123..."

# 2d) 用 msbuild 代替 wix CLI（CI 场景）
powershell scripts\build-msi.ps1 -Version 1.2.3 -UseMsbuild
```

产物：`publish\WeComRpa-1.2.3.msi`

---

## 4. 安装 / 卸载 / 升级

### 安装（交互）

双击 `.msi`，或命令行：

```powershell
msiexec /i "WeComRpa-1.0.0.msi"
```

弹出目录选择 UI（`WixUI_InstallDir`），默认安装到 `C:\Program Files\WeComRpa\`。

### 静默安装（批量部署）

```powershell
msiexec /i "WeComRpa-1.0.0.msi" /qn /norestart
# 自定义安装目录：
msiexec /i "WeComRpa-1.0.0.msi" /qn INSTALLDIR="D:\WeComRpa" /norestart
```

### 安装时自动发生

| 行为 | 实现 |
|------|------|
| 文件落地 | `publish\app\**` → `INSTALLDIR\app\`；`publish\supervisor\**` → `INSTALLDIR\supervisor\`（`.pdb` 调试符号被排除以减小体积） |
| Supervisor 服务 | `ServiceInstall` 创建 Windows 服务 `WeComRpaSupervisor`（`Start=auto`），等价于 `scripts\install-service.ps1` 的 `sc.exe create ... start= auto`；服务立即启动 |
| App 开始菜单快捷方式 | 「开始菜单 → 企业微信RPA → 企业微信RPA客户端」指向 `app\Client.App.exe` |
| App 登录自启 | 写入 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\WeComRpaApp`，用户登录后自动启动托盘程序（如改由 Supervisor 拉起，可在 .wxs 注释掉 `AppAutoRun` 组件） |

### 卸载

```powershell
msiexec /x "WeComRpa-1.0.0.msi"
# 或通过「设置 → 应用」卸载
```

卸载时自动：`ServiceControl` 停止并删除 `WeComRpaSupervisor` 服务 → 删除文件 → 删除快捷方式 → 删除 Run 注册表项。配置文件（如用户编辑过的 `client.yaml`）**不在 MSI 文件清单内**，不会被卸载删除（它们由 App 首次运行时从 `client.example.yaml` 复制生成，属于用户数据）。

### 升级

`<MajorUpgrade>` 支持**大版本升级**（同 `UpgradeCode` 的高版本替换低版本）：

```powershell
# 旧版 1.0.0 已安装，直接装新版 1.1.0：
msiexec /i "WeComRpa-1.1.0.msi"
```

MSI 先卸载旧版（停止旧服务、删旧文件），再装新版（重装服务）。**禁止降级**：若检测到更高版本已安装，弹窗报错拒绝。

> `UpgradeCode` = `7B2F3A4C-9D81-4E6B-B5C3-1A0F2E3D4C5B`，**一经发布不可更改**，否则升级链断裂。

---

## 5. 代码签名

生产部署**必须签名** MSI 与 exe，避免 SmartScreen 拦截和"未知发布者"警告：

```powershell
# 构建 + 签名一步到位（build-msi.ps1 内置）
powershell scripts\build-msi.ps1 -Version 1.0.0 -SignPfx "C:\cert\codesign.pfx" -SignPfxPassword <密码>

# 或单独对 exe 签名（publish.ps1 之后、build-msi.ps1 之前）
signtool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /f codesign.pfx /p <密码> publish\app\Client.App.exe
signtool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /f codesign.pfx /p <密码> publish\supervisor\Client.Supervisor.exe
```

签名算法：SHA-256（`/fd sha256`），时间戳服务器 `http://timestamp.digicert.com`（可换）。

---

## 6. 与方式 A（publish + install-service.ps1）的关系

| | 方式 A：脚本部署 | 方式 B：MSI 部署（本目录） |
|---|---|---|
| 步骤 | `publish.ps1` → 拷贝 `publish\` 到目标机 → 编辑 `client.yaml` → `install-service.ps1 -Action install` | `publish.ps1` → `build-msi.ps1` → 双击 MSI |
| 服务安装 | `install-service.ps1`（`sc.exe create`） | MSI 内 `ServiceInstall`（等价） |
| 升级 | 手动停服务、替换文件、起服务 | `msiexec /i 新版.msi`（自动停服务→替换→起服务） |
| 卸载 | `install-service.ps1 -Action uninstall` + 手动删文件 | `msiexec /x`（自动） |
| 适用 | 开发调试、单台手工部署、快速验证 | 生产批量部署、标准化镜像、CI/CD 出包 |
| 回滚 | 手动恢复旧文件 | 重装旧版 MSI（受 MajorUpgrade 降级限制，需先卸载新版） |

**建议**：开发与准入验证用方式 A；上线与运维用方式 B。两种方式的二进制产物完全一致（都来自 `publish.ps1`），仅封装与部署方式不同。

---

## 7. 版本号与协议协同

- MSI 的 `Version`（`build-msi.ps1 -Version`）对应客户端协议版本号，需与服务端 `min_client_version` 协同（见 `docs/system/wecom-personal-rpa-protocol.md` §A.7）。
- 服务端可阻止低版本客户端继续托管；升级 MSI 时确保新版本号 ≥ 服务端要求。

---

## 8. 验证状态

> 2026-06-24 完成真机打通，所有项已验证。

| 项 | 状态 | 说明 |
|----|------|------|
| `.wxs` XML 良构 | 已验证 | PowerShell `[xml]` 解析通过 |
| `.wixproj` XML 良构 | 已验证 | PowerShell `[xml]` 解析通过 |
| `build-msi.ps1` PS 5.1 语法 | 已验证 | `Parser::ParseFile` 0 错误 |
| **MSI 实编译** | **已验证** | WiX v5.0.2 + UI 扩展，`WeComRpa-1.0.0.msi` 真生成（115 MB） |
| 64 位打包 | 已验证 | `-arch x64` 装到 `C:\Program Files\WeComRpa\`（非 `(x86)`） |
| 子目录文件落地 | 已验证 | `app\configs\client.example.yaml` + `app\assets\wecom_nodes.yaml` 都在 |
| 服务安装实跑 | 已验证 | `WeComRpaSupervisor` 服务注册成功（Status=Stopped, StartType=Automatic） |
| HKCU Run 自启 | 已验证 | 注册表指向 `C:\Program Files\WeComRpa\app\Client.App.exe` |
| 开始菜单快捷方式 | 已验证 | `企业微信RPA客户端.lnk` 正确生成 |
| 卸载 | 已验证 | `msiexec /x` 干净清理（文件/服务/注册表/快捷方式全删） |
| 代码签名实跑 | 未验证 | 依赖证书就绪；脚本已支持 `-SignPfx` / `-SignCertThumbprint` 参数 |

---

## 9. 后续可选增强

- 自定义安装 UI 位图（`WixUIBannerBmp` / `WixUIDialogBmp`）与许可协议（`WixUILicenseRtf`）：准备文件后在 `.wxs` 取消注释对应 `<WixVariable>`。
- 开始菜单/添加删除程序图标：准备 `assets\app.ico` 后在 `.wxs` 取消注释 `<Icon>` 并在 `<Shortcut>` 上加 `Icon=`。
- 自定义动作（CustomAction）：安装后写 Windows Event Log 源、卸载前备份日志等，挂到 `<InstallExecuteSequence>`。
- App 改由 Supervisor 拉起：注释掉 `AppAutoRun` 组件，改为 Supervisor 在 Session 0 隔离方案下启动用户态 App。

---

## 关联文档

- 设计：[docs/system/wecom-personal-rpa-design.md](../../../docs/system/wecom-personal-rpa-design.md) §10.2 部署
- 协议：[docs/system/wecom-personal-rpa-protocol.md](../../../docs/system/wecom-personal-rpa-protocol.md) §A.7 版本兼容
- 发布脚本：[scripts/publish.ps1](../../scripts/publish.ps1)
- 服务脚本：[scripts/install-service.ps1](../../scripts/install-service.ps1)（方式 A）
- 构建脚本：[scripts/build-msi.ps1](../../scripts/build-msi.ps1)
- 操作手册：[docs/操作手册.md](../../../docs/操作手册.md) §5/§6/§7
