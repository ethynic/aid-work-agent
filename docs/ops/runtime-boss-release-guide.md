# Runtime、BOSS 与 WeCom 统一打包手册

从仓库根目录使用 [release-batch.ps1](../../clients/agent-desktop/scripts/release-batch.ps1)。三件一起出包用 `-Component All`，只更新一个组件用 `Runtime`、`Boss` 或 `Wecom`。先带 `-Plan` 核对组件、源码引用和交付位置，确认后去掉 `-Plan` 执行。

## 1. 选组件与交付格式

| `-Component` | 本批交付 |
|---|---|
| `All` | 1 个 Runtime 安装器、BOSS 和 WeCom 各 1 个可导入插件 |
| `Runtime` | 仅 Runtime Windows x64 安装器 `.exe` |
| `Boss` | 仅 `boss-<版本>.aidplugin.zip` |
| `Wecom` | 仅 `wecom-<版本>.aidplugin.zip` |
| `Both`（旧默认） | Runtime＋BOSS |
| `RuntimeWecom` | Runtime＋WeCom |

交付给收件人的是外层 `AID-Work-<组件和版本>-<时间>.zip`，旁边有同名 `.zip.sha256`。收件人解开外层 ZIP，按“先读我.md”运行选配的 Runtime 安装器，并在 Runtime 的离线包入口逐个选择原样的 `.aidplugin.zip`。已经安装兼容 Runtime 的用户可只导入插件。独立 npm CLI 的 `.tgz` 使用第 6 节的流程。

## 2. 构建机条件

Windows 10/11 x64，PowerShell 5.1、Git、Node/npm、Python 3 和系统 `tar.exe`。Runtime 编译监管程序使用 Windows .NET Framework 的 `csc.exe`。脚本准备并验用包内固定 Node 22.23.3 / win-x64 / ABI127。客户机直接安装成品。

需要 WeCom 时，便携 Python 按显式 `-LocaldbPythonDirectory`、仓库内 `clients/runtime-plugin-packaging/assets/localdb-python-final`、`%LOCALAPPDATA%\AidWorkBuild\assets\localdb-python-final` 定位。首次准备到外部固定目录，可跨 checkout 复用：

```powershell
$buildRoot = Join-Path $env:LOCALAPPDATA 'AidWorkBuild'
python.exe clients/runtime-plugin-packaging/prepare-localdb.py --output (Join-Path $buildRoot 'assets/localdb-python-final') --cache (Join-Path $buildRoot 'assets/downloads')
```

准备脚本要求输出目录尚未创建，按锁文件核对下载并检查 SQLite、AES、上海时区。打包保留标准库 `python312.zip`，过滤 Python 字节码缓存。BOSS-only 和 Runtime-only 无需准备这项资产。

## 3. 出包前核对源码与版本

- 一个批次使用同一个 Git `-SourceRef`（默认 `HEAD`）。先确认该提交已包含所有所选组件的改动。若各组件还在不同分支，使用已包含所需改动的集成提交，或分别指定来源出包。
- 脚本在 `%TEMP%\aid-release-…\workspace` 归档已提交源码，仅叠加入口列明的打包相关文件。未提交的业务源码不会进入包。仓库工作区中的版本和锁文件由各自开发任务维护。
- `-RuntimeVersion`、`-BossVersion`、`-WecomVersion` 可分别设置，省略时取源码提交中的版本。显式版本只写隔离副本，不会把旧源码变成新版本的实现。新内容应使用新版本与发行 ID，已经交付的版本及摘要保留用于追溯。
- `-Plan` 只查看计划，不运行 Git 或 npm，不读签名配置，不生成文件。未显式指定的版本和相应发行 ID 在计划里留空，实际出包时从隔离源码解析。

## 4. 稳定签名者与公开信任

验收 BOSS 和 WeCom 共用已登记的 `aidwork-acceptance-cli-v1`。本机构建配置在 `%LOCALAPPDATA%\AidWorkBuild\signers\acceptance-cli-v1\signer.json`，可用 `-Signer` 显式覆盖。换机构建由构建机管理员配置已登记的签名者。该配置指向的私钥只供签名进程使用。交付包保存公开根和签名报告。

### Windows 换会话找不到验收签名者

Windows 安装版 Codex 可能将 `%LOCALAPPDATA%\AidWorkBuild` 重定向到自己的 `LocalCache\Local`。资源管理器或另一个 agent 看不到默认目录时，先检查本机已验证的 Codex 实际配置路径。在执行打包的同一个 PowerShell 会话运行：

```powershell
$local = [Environment]::GetFolderPath('LocalApplicationData')
$relative = 'AidWorkBuild\signers\acceptance-cli-v1\signer.json'
$signer = Join-Path $local $relative
if (-not (Test-Path -LiteralPath $signer -PathType Leaf)) {
    $signer = Join-Path $local "Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\$relative"
}
if (-not (Test-Path -LiteralPath $signer -PathType Leaf)) {
    throw "验收签名配置在当前会话不可访问：$signer。请切回原构建会话或由构建机管理员恢复。"
}
$meta = Get-Content -LiteralPath $signer -Raw -Encoding UTF8 | ConvertFrom-Json
$keyFile = $meta.private_key_path
if ($meta.key_id -ne 'aidwork-acceptance-cli-v1' -or -not $keyFile) { throw '验收签名配置与预期发布者不符' }
if (-not [IO.Path]::IsPathRooted($keyFile)) { $keyFile = Join-Path (Split-Path -Parent $signer) $keyFile }
if (-not (Test-Path -LiteralPath $keyFile -PathType Leaf)) { throw '配置引用的密钥文件在当前会话不可访问' }
Write-Output "可用签名配置：$signer"
```

从仓库根目录用 `-Signer $signer` 显式出包。例如 `powershell.exe -NoProfile -File clients/agent-desktop/scripts/release-batch.ps1 -Component Boss -SourceRef HEAD -Signer $signer`。`HEAD` 应替换为已核对的源码提交，WeCom 同理使用 `-Component Wecom`。`-Plan` 不读取签名配置，能运行 `-Plan` 只代表参数和构建计划可用。检查时仅输出配置文件路径和必要的通过状态，密钥内容、配置正文和密钥路径留在构建机。

两个位置都不可访问时，先找原构建会话或由管理员恢复既有签名者。确认确实无法恢复时再安排发布者轮换与 Runtime 配套验证。第 6 节的随机测试根只用于隔离试验，现有客户 Runtime 需要信任发布者才能导入。

验收公开根默认用仓库的 `clients/runtime-plugin-packaging/trust/acceptance-roots.json`。普通补丁复用稳定发布者和现有公开根。新增或轮换发布者按[公开根登记工具](../../clients/runtime-plugin-packaging/register-publisher-trust.mjs)单独处理。历史公钥、范围和已交付兼容性需要保留。

`-ReleaseId` 缺省前缀分别为 `boss-<版本>`、`wecom-<版本>`，封装会追加对应 Provider 后缀。显式提供时作为本批所选插件的公共前缀，后缀仍分别绑定各自 Provider。

正式出包使用 `-Profile production` 和 `-TrustRoots` 指定正式公开根文件（其中 `profile` 为 `production`，各根 `test_only` 为 `false`）。涉及插件时另传 `-Signer` 指定正式签名配置，涉及 Runtime 时按正式 Windows 代码签名配置打包。只有明确选择未签名 Windows 安装器时才加 `-Unsigned`，插件仍需签名和验签。默认验收安装器不做 Windows 代码签名。

## 5. 常用出包命令

```powershell
$release = 'clients/agent-desktop/scripts/release-batch.ps1'

# 三件一起出，先核对计划
powershell.exe -NoProfile -File $release -Component All -SourceRef HEAD -Plan
powershell.exe -NoProfile -File $release -Component All -SourceRef HEAD

# 只出指定组件
powershell.exe -NoProfile -File $release -Component Runtime -SourceRef HEAD
powershell.exe -NoProfile -File $release -Component Boss -SourceRef HEAD
powershell.exe -NoProfile -File $release -Component Wecom -SourceRef HEAD

# 单插件核对指定 Runtime 的实际 resources 或 resources/runtime 目录
powershell.exe -NoProfile -File $release -Component Boss -SourceRef HEAD -TargetRuntimeResources 'C:\target-runtime\resources'
powershell.exe -NoProfile -File $release -Component Wecom -SourceRef HEAD -TargetRuntimeResources 'C:\target-runtime\resources'
```

用前请将 `HEAD` 换成需要的已提交源码（若当前 HEAD 正确可原样使用）。可用 `git show HEAD:clients/wecom-cli/package.json` 核对源码版本，BOSS 和 Runtime 分别查看 `clients/boss-resume-assistant/package.json` 与 `clients/agent-desktop/package.json`。

默认交付到当前 Windows 用户的「下载」→ `AID-Work-Releases`，可用 `explorer.exe shell:Downloads` 打开下载目录，或用 `-OutputDirectory` 覆盖位置。成功时脚本输出批次目录和 ZIP 的完整路径。构建临时文件在 Temp，签名配置及便携 Python 仍在构建目录。

## 6. 其他包形态

- **BOSS 独立 npm CLI**供 `npm install -g` 和外部 MCP Host 使用。先在 Windows x64 构建机执行 `npm --prefix clients/shared ci --ignore-scripts` 和 `npm --prefix clients/boss-resume-assistant ci --ignore-scripts`，再从仓库根用 Git Bash 执行 `bash clients/boss-resume-assistant/scripts/pack-release.sh`。该入口输出 `clients/boss-resume-assistant/boss-resume-assistant-<版本>.tgz`。它会随包带上共享 Win32 模块及 koffi 原生依赖。直接运行 `npm pack` 会留下客户机无法解析的仓库相对依赖。独立包交付前在空目录安装，验证 `createKoffiApi()` 可实例化，不能只跑 `version --json`。客户机需 Node ≥ 22 且可访问 npm registry。
- **WeCom**走表中的签名离线插件入口。当前未维护独立 npm 交付流程。
- **隔离试验**可在独立输出目录显式运行 `package.mjs --test --test-key-id aid-isolated-<唯一值>`，传入所需 Provider 和固定 Node 等参数。随机测试根随该次实验产物单独保存，仅供隔离测试环境。容器格式与低层参数见[插件打包说明](../../clients/runtime-plugin-packaging/README.md)。

## 7. 验包与证据

- 每个所选插件独立签名、验签、核对清单及搬迁后的只读 version/doctor。BOSS 另验证原生输入模块可加载，WeCom 验证其便携 Python。组合包直接使用同批安装器的实际 Runtime Core、公开根、Node/ABI 逐个核验；WeCom 还核对四项必需工具。
- 单插件用 `-TargetRuntimeResources` 指定客户机对应版本的 Runtime 资源。WeCom 省略参数时会尝试本机已安装的 Runtime。没有目标时，报告明确写 `not_run_no_target`。目标不兼容时先核对该版本的信任根、Runtime 版本和工具能力，必要时用配套模式出包。
- `build-info.json` 记录实际源码 SHA、组件版本和验证范围，逐包结果见 `plugin-verification.json` 及 `*-runtime-verification.json`。脚本验证 `SHA256SUMS.txt`、外层 ZIP 内各文件和 `.zip.sha256`，不会覆盖已有批次。
- 出包是构建和离线检查。真实安装、导入升级、配对、业务调用和后台部署分别验收。只升级服务端时仍须兼容已交付的旧客户端。

## 8. 安装和升级说明

以每批 ZIP 内的“先读我.md”为准。组合包先安装或升级 Runtime，再导入本批选择的插件。只更新插件时，直接在已验证兼容的 Runtime 中导入对应 `.aidplugin.zip`。Runtime 安装器与插件分别升级，客户已有配对和配置按安装器的既有迁移规则处理。

服务端启用准备与确认配对后，新设备在新版独立 Windows Runtime 界面填写网页生成的配对码。确认成功后自动选定。旧版 Runtime、旧 CLI 的单步配对会提示升级，已经配对的设备可沿用原凭据继续运行。

## 9. 排查入口

| 表现 | 处理 |
|---|---|
| 找不到 Signer 或看不到 AidWorkBuild | 按第 4 节检查默认位置和 Codex 重定向位置，在当前会话核对后显式传 `-Signer` |
| 找不到 WeCom 便携 Python | 按第 2 节准备资产或指定现有资产目录 |
| 版本对了但功能没进包 | 对照 `build-info.json` 的 `baseCommit`，检查该功能是否在 `-SourceRef` 中 |
| 插件验签通过，目标机仍拒绝 | 传该目标的 `-TargetRuntimeResources` 做兼容预检，必要时重新配套出包 |
| 找不到交付 ZIP | 在资源管理器侧栏打开「下载」→ `AID-Work-Releases`，或核对脚本打印的最终路径 |
