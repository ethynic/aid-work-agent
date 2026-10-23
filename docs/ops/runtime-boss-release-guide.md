# Runtime 与 BOSS 插件构建及 ZIP 交付手册

> 维护日期：2026-10-21。以当前仓库的 `package.json`、`package.mjs`、`prepare-runtime.mjs`、`package-runtime-win.mjs` 和 `verify-runtime-package.mjs` 为依据。
>
> 本文是当前独立 Runtime + BOSS 插件的打包入口。完整桌面对话客户端另见[桌面构建手册](../system/desktop-agent-client-build-manual.md)；旧 `clients/pack.sh`、npm 全局捆绑安装和历史交付记录不作为新包的操作步骤。
>
> 2026-10-21 实跑修订：修复 PS 5.1 下 `Get-Content -Raw` 缺 `-Encoding UTF8`（中文 JSON 解析失败）与 shared workspace 逐成员 ci 互毁依赖两处问题，详见第 4 节注释与第 10 节排查表。
> 本文各节的组合一键执行脚本：[clients/agent-desktop/scripts/release-batch.ps1](../../clients/agent-desktop/scripts/release-batch.ps1)（含第 10 节排查表所列的 tar 优先级与压缩占用自愈等实跑修复）。

## 1. 交付形态和目录

当前交付链为：共享依赖 → BOSS 签名插件 → 公开信任根登记 → Host tgz → Runtime 安装器 → 验包 → 组合 ZIP。

- Runtime 安装器包含管理 UI、Host、Core、固定 Node 和进程监管能力；BOSS 是单独的签名插件，**安装 Runtime 不会自动升级已安装的 BOSS 插件**。
- 可以只发一个组合 ZIP。收件人解压后先安装 Runtime，再通过 Runtime 的“安装 / 升级离线包”导入其中的 BOSS `.aidplugin.zip`。
- 本文主流程生成当前使用的 `acceptance` 验收包，Windows 安装器为 `NotSigned`，插件仍有 Ed25519 签名。是否去掉 ZIP 名称的 `trial` 不改变这一性质。正式 Windows 签名发行见第 10 节。
- **最终交付文件统一放在 `clients/release/`**：每批一个子目录保存安装器、插件、说明和清单，组合 ZIP 及其 `.sha256` 放在该目录的上一层，即 `clients/release/` 本身。不要使用仓库根 `release/`。
- 编译输出、固定 Node 缓存及中间 Host tgz 按现有脚本保留在 `clients/agent-desktop/build/` 等构建目录；这些不是发给用户的交付文件。不要修改现有脚本的内部输出路径来凑交付目录。

```text
clients/release/
  AID-Work-Runtime-<UI版本>-BOSS-<插件版本>-<批次>/
    AID-Work-Runtime-<UI版本>-win-x64-acceptance-unsigned.exe
    boss-<插件版本>-dev.aidplugin.zip
    boss-<插件版本>-dev.aidplugin.zip.manifest.json
    acceptance-roots.json
    runtime-<UI版本>.roots.json
    runtime-release-manifest.json
    plugin-verification.json
    build-info.json
    先读我.md
    SHA256SUMS.txt
  AID-Work-Runtime-<UI版本>-BOSS-<插件版本>-<批次>.zip
  AID-Work-Runtime-<UI版本>-BOSS-<插件版本>-<批次>.zip.sha256
```

## 2. 构建机条件及执行约定

Windows 10/11 x64，Node/npm、Git、Windows 自带 `tar.exe`、Windows PowerShell 5.1，以及用于现有离线验包器的 Python 3。Runtime 原生监管编译使用 Windows .NET Framework 的 `csc.exe`；Electron 下载需可访问配置的下载源。客户机不需要安装这些构建工具。

下面的 PowerShell 块按顺序在**同一个会话**执行。只需在仓库内打开 PowerShell，不依赖固定的 `C:\repos` 或 nvm 安装位置。相同 checkout 不要并行打包：prepare 和 package 使用共用资源目录，package 会重建其内部输出目录。

```powershell
$ErrorActionPreference = 'Stop'
$repo = (& git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw '请在 aid-work-agent 仓库内执行' }
Set-Location -LiteralPath $repo
$npmExe = (Get-Command npm.cmd -ErrorAction Stop).Source
$nodeExe = (Get-Command node.exe -ErrorAction Stop).Source
$pythonExe = (Get-Command python.exe -ErrorAction Stop).Source
$npmCli = Join-Path (Split-Path -Parent $npmExe) 'node_modules/npm/bin/npm-cli.js'
if (!(Test-Path -LiteralPath $npmCli)) {
    throw 'npm-cli.js 未找到；请将 npmCli 设置为本机 npm 安装目录内 npm/bin/npm-cli.js 的绝对路径'
}
function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "命令执行失败：$Executable（exit=$LASTEXITCODE）" }
}
function Invoke-NpmAt {
    param([string]$Directory, [string[]]$Arguments)
    Push-Location -LiteralPath (Join-Path $repo $Directory)
    try { Invoke-Checked $npmExe $Arguments } finally { Pop-Location }
}
function Write-Utf8 {
    param([string]$Path, [string]$Text)
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding($false)))
}
function Read-Version {
    param([string]$Directory)
    # PS 5.1 的 Get-Content 默认按 ANSI 解码 UTF-8 无 BOM 文件，中文 package.json 会读成乱码
    # 使 ConvertFrom-Json 失败（2026-10-21 实跑）；所有 -Raw 读取一律带 -Encoding UTF8
    (Get-Content -LiteralPath (Join-Path $repo "$Directory/package.json") -Raw -Encoding UTF8 | ConvertFrom-Json).version
}
Invoke-Checked $nodeExe @('--version')
Invoke-Checked $npmExe @('--version')
Invoke-Checked $pythonExe @('--version')
Invoke-Checked 'tar.exe' @('--version')
git status --short
```

构建机 Node 需满足 frontend/desktop 的 `engines`（当前至少 22.12）；插件签署和包内运行固定使用脚本批准的 **Node 22.23.3 / win32-x64 / ABI 127**，不是当前 PATH 中随便一个 Node。普通 PowerShell 报错会停止，外部命令由 `Invoke-Checked` 检查退出码。

国内网络下载 Electron/NSIS 慢时，可在当前会话额外设置以下变量，再执行后续步骤；不是客户安装配置：

```powershell
$env:ELECTRON_MIRROR = 'https://npmmirror.com/mirrors/electron/'
$env:ELECTRON_BUILDER_BINARIES_MIRROR = 'https://npmmirror.com/mirrors/electron-builder-binaries/'
```

## 3. 版本和新批次

先确定要发布的源码及变更范围，保留其他任务的工作区改动，不自动切分支、拉取合并或提交。新内容不得冒充已交付的同版本包。以下主流程按“新 Runtime 安装器 + 新 BOSS 插件”分别升 patch；如果版本已经由开发阶段升好，则跳过升版本循环。Host/Core/原生共享包有源码变更时，将对应目录加入列表；各组件版本独立，**不要求所有版本号相同**。

```powershell
$bumpPackages = @('clients/agent-desktop', 'clients/boss-resume-assistant')
# 按实际源码变化追加；已升好版本时不要再升一次：
# $bumpPackages += 'clients/agent-tool-runtime'
# $bumpPackages += 'clients/shared/local-tool-host-core'
# $bumpPackages += 'clients/shared/win32-native-input'
foreach ($directory in $bumpPackages) {
    Invoke-NpmAt $directory @('version', 'patch', '--no-git-tag-version', '--ignore-scripts')
}
$uiVersion = Read-Version 'clients/agent-desktop'
$hostVersion = Read-Version 'clients/agent-tool-runtime'
$coreVersion = Read-Version 'clients/shared/local-tool-host-core'
$nativeVersion = Read-Version 'clients/shared/win32-native-input'
$bossVersion = Read-Version 'clients/boss-resume-assistant'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$batchName = "AID-Work-Runtime-$uiVersion-BOSS-$bossVersion-$stamp"
$buildRoot = Join-Path $repo "clients/agent-desktop/build/$batchName"
$releaseRoot = Join-Path $repo 'clients/release'
$deliveryRoot = Join-Path $releaseRoot $batchName
$zipPath = Join-Path $releaseRoot "$batchName.zip"
$pluginDir = Join-Path $buildRoot 'plugins'
$hostDir = Join-Path $buildRoot 'host'
$registryPath = Join-Path $repo 'clients/runtime-plugin-packaging/trust/acceptance-roots.json'
$snapshotPath = Join-Path $repo "clients/runtime-plugin-packaging/trust/history/runtime-$uiVersion.roots.json"
if ((Test-Path -LiteralPath $buildRoot) -or (Test-Path -LiteralPath $deliveryRoot) -or
    (Test-Path -LiteralPath $zipPath) -or (Test-Path -LiteralPath $snapshotPath)) {
    throw '批次或该 UI 版本的发布快照已存在，请核对版本并使用新批次，不覆盖已交付文件'
}
New-Item -ItemType Directory -Path $buildRoot, $hostDir -Force | Out-Null
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
$sourceBaseCommit = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw '读取源码提交失败' }
```

升版本会同步相应 `package.json` 和 lockfile，但不创建 tag/commit。若共享 `file:` 依赖的版本变化导致后面的 `npm ci` 报锁文件不一致，应先在**受影响的消费工程**执行 `npm install --package-lock-only --ignore-scripts` 同步并审阅差异，再重跑该工程的 `npm ci`；不要删除整个 lockfile。

## 4. 安装依赖、构建 BOSS、准备固定 Node

```powershell
# clients/shared 是 npm workspace 根（workspaces 含 win32-native-input/local-tool-host-core 等），
# 依赖提升安装到 shared 根 node_modules——必须从根一次性 ci。逐成员 ci 会把根 node_modules
# 重装成单个成员的依赖集互相冲掉（2026-10-21 实跑：先装 native 后装 core，koffi 被 core 的
# ci 清掉，boss build:native 报 Cannot find module 'koffi'）。
Invoke-NpmAt 'clients/shared' @('ci', '--ignore-scripts')
Invoke-NpmAt 'clients/agent-tool-runtime' @('ci', '--ignore-scripts')
Invoke-NpmAt 'clients/boss-resume-assistant' @('ci', '--ignore-scripts')
Invoke-NpmAt 'clients/runtime-plugin-packaging' @('ci', '--ignore-scripts')
Invoke-NpmAt 'frontend' @('ci')
Invoke-NpmAt 'clients/agent-desktop' @('ci')

Invoke-NpmAt 'clients/shared/local-tool-host-core' @('run', 'build')
Invoke-NpmAt 'clients/boss-resume-assistant' @('run', 'build:main')
Invoke-Checked $nodeExe @('clients/agent-desktop/scripts/prepare-runtime.mjs', '--node-only')
$nodeSpec = Get-Content -LiteralPath 'clients/agent-desktop/scripts/runtime-node-resources.json' -Raw -Encoding UTF8 | ConvertFrom-Json
$fixedNode = Join-Path $repo "clients/agent-desktop/build/runtime-cache/node-v$($nodeSpec.version)-win-x64/node.exe"
Invoke-Checked $fixedNode @('--version')
```

BOSS 的 `build:main` 已调用原生共享包构建；Host 的 prepack 也会构建 Core、原生共享包及监管 EXE。不另写复制旧 `dist` 的打包流程。BOSS-only 不构建微信/企微，也不准备它们的 OCR Python 资产。`--node-only` 下载/校验固定官方 Node，不注册配对身份，不生成插件签名。

## 5. 签署 BOSS 插件，登记公钥及完整历史快照

```powershell
$testKeyId = "aid-isolated-boss-$($bossVersion.Replace('.', '-'))-$stamp"
Invoke-Checked $nodeExe @(
    'clients/runtime-plugin-packaging/package.mjs', '--test', '--provider', 'boss',
    '--test-key-id', $testKeyId, '--node', $fixedNode, '--npm-cli', $npmCli,
    '--output', $pluginDir
)
$pluginPath = Join-Path $pluginDir "boss-$bossVersion-dev.aidplugin.zip"
$newRootPath = Join-Path $pluginDir 'ISOLATED-TEST-TRUST-ROOT.json'
# --test 的默认根覆盖三个 Provider；本次只发布 BOSS，登记前收窄新根范围。
$newRoot = Get-Content -LiteralPath $newRootPath -Raw -Encoding UTF8 | ConvertFrom-Json
$newRoot.roots[0].providers = @('ai.aidwork.boss-recruiting')
Write-Utf8 $newRootPath (($newRoot | ConvertTo-Json -Depth 10) + "`n")
Invoke-Checked $nodeExe @(
    'clients/runtime-plugin-packaging/register-publisher-trust.mjs', '--input', $newRootPath
)
Copy-Item -LiteralPath $registryPath -Destination $snapshotPath
```

新私钥仅在签署进程内，不保存、不索取旧私钥。已有 key_id、公钥、Provider 范围和 `test_only` 原样保留；新签名必须使用新 ID。Runtime prepare 使用**仓库完整登记表 `$registryPath`**，不能使用只含本轮一根的 `$newRootPath`。新登记表与快照是需要审阅、随源码同步的公开资产；本手册不自动提交。换构建电脑应同步这些资产，无需客户解绑重配。

中断后的处理：插件输出目录已有文件时不要原地重签。若已经生成并核验了本轮插件，优先继续使用其原始字节和对应公钥；若确需重新签署，使用新的输出目录和 key_id，保留已登记的根，重新生成尚未交付批次的完整快照。没有私钥时也不能为已交付同版本换内容/换签名。

## 6. 生成 Host tgz，准备并打包 Runtime

```powershell
# 明确切换 cwd；不用曾经取错仓库根 package.json 的 npm --prefix ... pack。
Invoke-NpmAt 'clients/agent-tool-runtime' @('pack', '--pack-destination', $hostDir)
$hostPackage = Join-Path $hostDir "agent-tool-runtime-$hostVersion.tgz"
$hostSha = (Get-FileHash -LiteralPath $hostPackage -Algorithm SHA256).Hash.ToLowerInvariant()
Invoke-Checked $nodeExe @(
    'clients/agent-desktop/scripts/prepare-runtime.mjs', '--profile', 'acceptance',
    '--host-package', $hostPackage, '--host-sha256', $hostSha, '--trust-roots', $registryPath
)
Invoke-NpmAt 'clients/agent-desktop' @('run', 'runtime:package:acceptance')
Invoke-NpmAt 'clients/agent-desktop' @('run', 'runtime:verify')
$runtimeOutput = Join-Path $repo 'clients/agent-desktop/build/runtime-release/acceptance'
$runtimeManifestPath = Join-Path $runtimeOutput 'runtime-release-manifest.json'
$runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$installerPath = Join-Path $runtimeOutput $runtimeManifest.fileName
if ($runtimeManifest.version -ne $uiVersion -or $runtimeManifest.hostPackageSha256 -ne $hostSha) {
    throw '安装器版本或 Host 输入与本批不符'
}
```

应出现 `RUNTIME_PACKAGE_PASS` 和 `RUNTIME_VERIFY_PASS`，且命令退出 0。package 本身已调用一次标准验包；单独 `runtime:verify` 方便交付前复核。验证覆盖实际安装器摘要/签名状态、资源清单、ASAR、Node、信任集合和 Host 依赖图。`win-unpacked` 是验包输入，不是替代安装器的客户交付。

## 7. 插件验包和相关验证

复用现有验包器核对签名、完整文件清单、搬迁后的 `version`、`doctor` 及只读检查前后 payload 不变：

```powershell
$pluginReportPath = Join-Path $buildRoot 'plugin-verification.json'
Invoke-Checked $pythonExe @(
    'clients/runtime-plugin-packaging/verify-offline.py', '--directory', $pluginDir, '--node', $fixedNode
) | Tee-Object -FilePath $pluginReportPath
```

它会用隔离配置做 doctor 的本机只读连接检查，不点击、不发消息、不调用视觉模型。没开调试 Chrome/没登录时 `ready=false`、`doctor_exit=1` 可以是环境未就绪，不应为打包强制登录；**这也不证明原生模块正常**。针对当前新增的原生依赖，再用本轮实际插件加载一次 FFI，完全不投递鼠标/键盘输入：

```powershell
$outerCheck = Join-Path $buildRoot 'plugin-check/outer'
$payloadCheck = Join-Path $buildRoot 'plugin-check/payload'
Expand-Archive -LiteralPath $pluginPath -DestinationPath $outerCheck
Expand-Archive -LiteralPath (Join-Path $outerCheck 'payload.zip') -DestinationPath $payloadCheck
$nativeModule = Join-Path $payloadCheck 'node_modules/@aid/win32-native-input/dist/index.js'
$nativeModuleUri = ([Uri]$nativeModule).AbsoluteUri
Invoke-Checked $fixedNode @('--input-type=module', '-e',
    "const m = await import(process.argv[1]); m.createKoffiApi(); console.log('NATIVE_INPUT_LOAD_OK');", $nativeModuleUri)
```

应出现 `NATIVE_INPUT_LOAD_OK`。模块缺失、Koffi 加载失败、签名或文件摘要错误属于包缺陷，不能用“未登录”解释。不要把 `verify-offline.py` 的退出 0 单独写成 BOSS 已可操作。

打包代码变更的定向测试入口如下；同一源码状态已有可核验结果可复用，业务变更另按实际范围测试，不强制每次重复全仓库测试：

```powershell
Invoke-NpmAt 'clients/runtime-plugin-packaging' @('test')
Invoke-NpmAt 'clients/agent-desktop' @('run', 'runtime:test:packaging')
```

旧库存升级、实际 NSIS 安装、Runtime 启动/退出、真实 BOSS 操作与视觉恢复按本次变更范围分别记录“已验证/未验证”。静态验包不能代替这些实测；真实外发仍需明确候选人和动作授权。`package`/`verify` 不会自动获得部署或提交授权。

## 8. 复制交付文件、说明和源码状态

```powershell
New-Item -ItemType Directory -Path $deliveryRoot | Out-Null
foreach ($file in @($installerPath, $pluginPath, "$pluginPath.manifest.json", $runtimeManifestPath, $pluginReportPath)) {
    Copy-Item -LiteralPath $file -Destination $deliveryRoot
}
Copy-Item -LiteralPath $registryPath -Destination (Join-Path $deliveryRoot 'acceptance-roots.json')
Copy-Item -LiteralPath $snapshotPath -Destination $deliveryRoot
$sourceState = @(& git status --porcelain --untracked-files=normal)
if ($LASTEXITCODE -ne 0) { throw '读取源码状态失败' }
$buildInfo = [ordered]@{
    schemaVersion = 1; batch = $batchName; profile = 'acceptance'
    builtAt = (Get-Date).ToString('o'); baseCommit = $sourceBaseCommit; workingTree = $sourceState
    versions = [ordered]@{ ui = $uiVersion; host = $hostVersion; core = $coreVersion; nativeInput = $nativeVersion; boss = $bossVersion; node = $nodeSpec.version }
    hostPackageSha256 = $hostSha; buildInputSha256 = $runtimeManifest.buildInputSha256
    verification = [ordered]@{
        runtimePackage = 'passed'; runtimeVerify = 'passed'; pluginOffline = 'passed'; nativeInputLoad = 'passed'
        nsisInstallation = 'not_run'; oldInventoryUpgrade = 'not_run'; runtimeUiSmoke = 'not_run'; bossBusiness = 'not_run'; visualRecovery = 'not_run'
    }
}
Write-Utf8 (Join-Path $deliveryRoot 'build-info.json') (($buildInfo | ConvertTo-Json -Depth 12) + "`n")
$readme = @"
# Runtime 与 BOSS 安装说明

本批：$batchName。Runtime UI $uiVersion，Host $hostVersion，BOSS $bossVersion。
Windows x64 验收安装器未做 Windows 代码签名；BOSS 插件已做发布者签名。

1. 在原 Windows 用户下，从托盘正常退出旧 Runtime；有执行任务时先正常收尾。
2. 运行本 ZIP 内的 Runtime EXE，沿用原安装位置及用户资料，不删除配对、插件或执行记录。
3. 打开 Runtime，用“安装 / 升级离线包”导入本 ZIP 内的 boss-$bossVersion-dev.aidplugin.zip；插件不要解压。
4. 已配对用户沿用原身份；首次使用在 Web 生成配对码，在 Runtime 填服务器根地址和配对码。
5. 准备原有调试 Chrome 并登录 BOSS，确认插件版本/启用状态，启动执行并在 Web 选定同一设备。

Runtime 安装器不自动更新 BOSS 插件，第 3 步不可遗漏。客户机无需安装源码、Node、npm 或 Python。
视觉恢复复用 Runtime 配对的服务器和设备身份，不需要额外 token 或环境变量；后台须有配套设备视觉 API，按后台 20 倍规则结算。服务器发布另行安排，本包不会部署后台。
acceptance-roots.json 是公开验签资料，已包含在安装器中，无需客户手工导入。
本批自动验包与实际安装、业务实测的范围见 build-info.json；not_run 不表示已经通过。
默认本验收 Runtime 日志：%APPDATA%\aidwork-tool-runtime-acceptance\logs\runtime.log。
"@
Write-Utf8 (Join-Path $deliveryRoot '先读我.md') ($readme + "`n")
```

如本轮确实另做了实际安装/升级/业务验收，把原始报告复制到本批交付目录，并按事实更新 `build-info.json` 的对应项；没有做就保留 `not_run`。源码未提交时，`baseCommit` 只是基线，`workingTree` 明确标示改动，不能称“全部内容等于该 commit”。版本、公开根和快照的提交由用户另行授权，不在打包命令里自动执行。

## 9. SHA-256、生成最终 ZIP、复读 ZIP 内容

在所有说明/验证报告固定后执行；这一步之后再改文件必须重新生成摘要和 ZIP，已交付 ZIP 不原地覆盖。

```powershell
$sumLines = @(Get-ChildItem -LiteralPath $deliveryRoot -File | Where-Object { $_.Name -ne 'SHA256SUMS.txt' } | Sort-Object Name | ForEach-Object {
    '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $_.Name
})
Write-Utf8 (Join-Path $deliveryRoot 'SHA256SUMS.txt') (($sumLines -join "`n") + "`n")
$deliveryFiles = @(Get-ChildItem -LiteralPath $deliveryRoot -File)
Compress-Archive -LiteralPath @($deliveryFiles.FullName) -DestinationPath $zipPath -CompressionLevel Optimal

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($zipPath)
try {
    if ($zip.Entries.Count -ne $deliveryFiles.Count) { throw 'ZIP 文件数量不符' }
    foreach ($file in $deliveryFiles) {
        $entry = $zip.GetEntry($file.Name)
        if (!$entry -or $entry.Length -ne $file.Length) { throw "ZIP 文件缺失或长度不符：$($file.Name)" }
        $stream = $entry.Open()
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $actual = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
        finally { $sha.Dispose(); $stream.Dispose() }
        $expected = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "ZIP 内容摘要不符：$($file.Name)" }
    }
} finally { $zip.Dispose() }
$zipSha = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Utf8 "$zipPath.sha256" ("$zipSha  $([IO.Path]::GetFileName($zipPath))`n")
Get-Item -LiteralPath $zipPath, "$zipPath.sha256" | Select-Object FullName, Length
git status --short
```

`SHA256SUMS.txt` 覆盖目录内其他交付文件，不对自己做自引用；ZIP 旁的 `.sha256` 覆盖最终 ZIP。不要打包整个 `build/`、`win-unpacked/`、`node_modules/`、实验截图、真实简历、日志、配对凭据或私钥。本流程只复制明确的交付文件。

最终交给用户 `clients/release/` 下的本批 ZIP，可同时提供其 `.sha256`。此时还没有部署服务器，也没有自动提交源码。

## 10. 正式签名发行与故障排查

上文是当前 `acceptance` 包的完整可执行链，不自动转换成 production。正式发行使用外部 Ed25519 signer 配置（`key_id`、`private_key_path`）、不可变 `--release-id`，以及 `profile=production`、`test_only=false` 的生产公钥集合；插件命令使用 `--signer` 而不是 `--test`。Runtime prepare 使用 `--profile production`。在仓库根，构建入口为 `npm --prefix clients/agent-desktop run runtime:package:production`，验包为 `node clients/agent-desktop/scripts/verify-runtime-package.mjs clients/agent-desktop/build/runtime-release/production`。第 7 节现有 `verify-offline.py` 只接受验收信任根，不能原样用于正式签名插件。另需可信环境提供 `CSC_LINK` 或 `WIN_CSC_LINK` 及相应证书密码，最终安装器签名必须 `Valid`。验收登记表和测试根不能改字段后当生产根使用，私钥/证书密码不写进本手册、仓库或交付 ZIP。

| 现象 | 处理 |
|---|---|
| `clients/pack.sh` 拒绝独立插件布局 | 使用本手册；不要恢复已停用的旧捆绑路径 |
| `ConvertFrom-Json` 报“传入的对象无效” | PS 5.1 按 ANSI 读 UTF-8 无 BOM 文件所致；所有 `Get-Content -Raw` 带 `-Encoding UTF8`（2026-10-21 实跑） |
| `boss build:native` 报 `Cannot find module 'koffi'` | shared 是 workspace 根，从 `clients/shared` 根一次 ci；逐成员 ci 互毁根依赖（2026-10-21 实跑） |
| prepare-runtime 的 tar 报 `Cannot connect to C` | Git Bash 的 GNU tar 抢占了 `tar.exe`，把盘符路径当远程主机；确保 Windows 自带 `System32	ar.exe` 优先（如把 System32 前置 PATH）（2026-10-21 实跑） |
| `Compress-Archive` 对刚复制的 `boss-*.aidplugin.zip` 报 PermissionDenied | 实证根因（2026-10-21 RM API 复现 5/5）：workspace 内编辑器（VS Code 系）文件 watcher 对刚复制的新文件短暂监视读取，而 126MB 安装器按字母序先被压缩，拖出的时间窗撞上该锁；watcher 秒级释放，失败时诊断占用者并退避重试即过（配套脚本 `clients/agent-desktop/scripts/release-batch.ps1` 已内置） |
| `npm ci` 报 lockfile 不一致 | 只同步受影响工程 lockfile 后重试，保留审阅差异 |
| 找不到 `win32-native-input/dist/index.js` | 检查共享包依赖安装、BOSS `build:main`，不要直接复制旧 tgz |
| Koffi 原生模块缺失 | 查本批签名 payload 中的共享包及 Koffi；包内加载通过后再交付 |
| test key 已登记 / 输出已存在 | 使用新版本、新 key_id/目录；已生成的可用包优先原样续用 |
| prepare 报历史根缺失 / 未登记新根 | 使用仓库完整登记表；检查公钥追加与发布快照，不删历史根 |
| Host pack 找仓库根 `package.json` | 按第 6 节在 Host 工程 cwd 下执行 pack |
| `Host dependency graph` / 模块缺失 | 从本次源码 fresh pack，检查 bundled Core/原生依赖，不能套用旧 Host 包 |
| EXE 名称与预期不同 | 从 `runtime-release-manifest.json.fileName` 获取，不写死历史版本 |
| 安装器成功但 BOSS 仍旧版 | 在 Runtime 显式导入同批新插件，检查插件实际版本 |
| 插件就绪但视觉 API 不可用 | 查配套后台版本及设备身份路由，不让用户再配置独立 token |
| 验包通过但真实业务未测 | 如实保留 `not_run`，另做授权验收，不靠改报告补齐 |

维护关联：[Runtime 计划](../plans/plan-runtime-plugin-host.md)、[组合包历史计划](../plans/plan-runtime-ui-boss-trial-bundle.md)、[共同集成契约](../system/runner-desktop-runtime-integration-contract.md)。本文整理不改变脚本、运行时契约或已有发布策略。
