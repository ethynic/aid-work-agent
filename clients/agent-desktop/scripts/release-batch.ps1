# 按 docs/ops/runtime-boss-release-guide.md 所述独立组件打包入口（配套入口，文档为语义权威）。
# stage 9 内置文件占用自愈：workspace 内编辑器/索引器对刚复制文件的短暂监视锁
# （2026-10-21 实跑两次复现，RM API 实证占用者为 VS Code 文件 watcher）会导致
# Compress-Archive PermissionDenied——失败时用 Restart Manager 诊断占用者并退避重试。
param(
    [ValidateSet('Both', 'Runtime', 'Boss')][string]$Component = 'Both',
    [Alias('Profile')][ValidateSet('acceptance', 'production')][string]$ReleaseProfile = 'acceptance',
    [string]$Signer,
    [string]$TrustRoots,
    [string]$ReleaseId,
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')][string]$RuntimeVersion,
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')][string]$BossVersion,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$includeRuntime = $Component -ne 'Boss'
$includeBoss = $Component -ne 'Runtime'
if ($includeBoss -and [string]::IsNullOrWhiteSpace($Signer)) { throw 'BOSS 发布需要 -Signer 指定稳定外部签名者；隔离随机测试包请使用 package.mjs --test' }
if ([string]::IsNullOrWhiteSpace($TrustRoots)) {
    if ($ReleaseProfile -eq 'production') { throw '生产构建需要 -TrustRoots 指定正式公开信任根' }
    $TrustRoots = Join-Path $repo 'clients/runtime-plugin-packaging/trust/acceptance-roots.json'
}
$TrustRoots = [IO.Path]::GetFullPath($TrustRoots)
if ($includeBoss) { $Signer = [IO.Path]::GetFullPath($Signer) }
function Read-Version {
    param([string]$Directory)
    (Get-Content -LiteralPath (Join-Path $repo "$Directory/package.json") -Raw -Encoding UTF8 | ConvertFrom-Json).version
}
$uiVersion = if ($includeRuntime -and $RuntimeVersion) { $RuntimeVersion } else { Read-Version 'clients/agent-desktop' }
$bossVersion = if ($includeBoss -and $BossVersion) { $BossVersion } else { Read-Version 'clients/boss-resume-assistant' }
if ($includeBoss -and [string]::IsNullOrWhiteSpace($ReleaseId)) { $ReleaseId = "boss-$bossVersion" }
if ($Plan) {
    [ordered]@{
        component = $Component; profile = $ReleaseProfile; runtime = $includeRuntime; boss = $includeBoss
        versions = [ordered]@{ runtime = $uiVersion; boss = $bossVersion }
        signing = if ($includeBoss) { 'external-stable' } else { 'not_selected' }
        releaseId = $ReleaseId; trustRoots = $TrustRoots
        steps = @('shared-build', 'fixed-node') + $(if ($includeBoss) { @('boss-build', 'sign-plugin', 'verify-plugin') } else { @() }) + $(if ($includeRuntime) { @('runtime-build', 'package-installer', 'verify-runtime') } else { @() }) + @('delivery-zip')
    } | ConvertTo-Json -Depth 5
    return
}
# Plan returns before external tools, version writes, output creation or private-key access.
Set-Location -LiteralPath $repo
$env:PATH = "$env:SystemRoot\System32;" + $env:PATH
$npmExe = (Get-Command npm.cmd -ErrorAction Stop).Source
$nodeExe = (Get-Command node.exe -ErrorAction Stop).Source
$pythonExe = (Get-Command python.exe -ErrorAction Stop).Source
$npmCli = Join-Path (Split-Path -Parent $npmExe) 'node_modules/npm/bin/npm-cli.js'
if (!(Test-Path -LiteralPath $npmCli)) { throw 'npm-cli.js 未找到' }
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
# 文件占用者诊断（Restart Manager API，与资源管理器「文件正在使用」同源）
$LockFinderSource = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

// Restart Manager API 查询文件占用者（Windows 官方机制，资源管理器"文件正在使用"同源）
public static class LockFinder
{
    [StructLayout(LayoutKind.Sequential)]
    struct RM_UNIQUE_PROCESS
    {
        public int dwProcessId;
        public System.Runtime.InteropServices.ComTypes.FILETIME ProcessStartTime;
    }

    const int RmRebootReasonNone = 0;
    const int CCH_RM_MAX_APP_NAME = 255;
    const int CCH_RM_MAX_SVC_NAME = 63;

    enum RM_APP_TYPE
    {
        RmUnknownApp = 0, RmMainWindow = 1, RmOtherWindow = 2,
        RmService = 3, RmExplorer = 4, RmConsole = 5, RmCritical = 1000
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct RM_PROCESS_INFO
    {
        public RM_UNIQUE_PROCESS Process;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = CCH_RM_MAX_APP_NAME + 1)]
        public string strAppName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = CCH_RM_MAX_SVC_NAME + 1)]
        public string strServiceShortName;
        public RM_APP_TYPE ApplicationType;
        public uint AppStatus;
        public uint TSSessionId;
        [MarshalAs(UnmanagedType.Bool)]
        public bool bRestartable;
    }

    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    static extern int RmStartSession(out uint pSessionHandle, int dwSessionFlags, string strSessionKey);

    [DllImport("rstrtmgr.dll")]
    static extern int RmEndSession(uint pSessionHandle);

    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    static extern int RmRegisterResources(uint pSessionHandle, uint nFiles, string[] rgsFilenames,
        uint nApplications, [In] RM_UNIQUE_PROCESS[] rgApplications, uint nServices, string[] rgsServiceNames);

    [DllImport("rstrtmgr.dll")]
    static extern int RmGetList(uint dwSessionHandle, out uint pnProcInfoNeeded, ref uint pnProcInfo,
        [In, Out] RM_PROCESS_INFO[] rgAffectedApps, ref uint lpdwRebootReasons);

    public static string[] FindLockers(string path)
    {
        var result = new List<string>();
        uint handle;
        var key = Guid.NewGuid().ToString();
        int err = RmStartSession(out handle, 0, key);
        if (err != 0) { result.Add("RmStartSession error " + err); return result.ToArray(); }
        try
        {
            if (RmRegisterResources(handle, 1, new string[] { path }, 0, null, 0, null) != 0)
            {
                result.Add("RmRegisterResources error");
                return result.ToArray();
            }
            uint needed = 0, count = 0, reasons = 0;
            const int ERROR_MORE_DATA = 234;
            int res = RmGetList(handle, out needed, ref count, null, ref reasons);
            if (res == ERROR_MORE_DATA)
            {
                var info = new RM_PROCESS_INFO[needed];
                count = needed;
                if (RmGetList(handle, out needed, ref count, info, ref reasons) == 0)
                {
                    for (int i = 0; i < count; i++)
                    {
                        result.Add(string.Format("pid={0} app={1} type={2} restartable={3}",
                            info[i].Process.dwProcessId, info[i].strAppName,
                            info[i].ApplicationType, info[i].bRestartable));
                    }
                }
            }
            else if (res == 0)
            {
                // 无占用者（RM 认为没有进程锁住该文件）
            }
            else
            {
                result.Add("RmGetList error " + res);
            }
        }
        finally { RmEndSession(handle); }
        return result.ToArray();
    }
}
'@
Add-Type -TypeDefinition $LockFinderSource
function Show-Lockers {
    param([string]$Path)
    $lockers = [LockFinder]::FindLockers($Path)
    if ($lockers.Count -eq 0) { Write-Host "  [lock] $Path : (none)" }
    else { $lockers | ForEach-Object { Write-Host "  [lock] $Path : $_" } }
}

Write-Host '[stage 1] 环境检查'
Invoke-Checked $nodeExe @('--version')
Invoke-Checked $npmExe @('--version')
Invoke-Checked $pythonExe @('--version')
Invoke-Checked 'tar.exe' @('--version')

# 国内镜像（仅构建下载加速）
$env:ELECTRON_MIRROR = 'https://npmmirror.com/mirrors/electron/'
$env:ELECTRON_BUILDER_BINARIES_MIRROR = 'https://npmmirror.com/mirrors/electron-builder-binaries/'

Write-Host '[stage 2] 独立组件版本与新批次'
# No implicit patch bump: versions change only for the selected component and explicit input.
if ($includeRuntime -and $RuntimeVersion -and (Read-Version 'clients/agent-desktop') -ne $RuntimeVersion) {
    Invoke-NpmAt 'clients/agent-desktop' @('version', $RuntimeVersion, '--no-git-tag-version', '--ignore-scripts')
}
if ($includeBoss -and $BossVersion -and (Read-Version 'clients/boss-resume-assistant') -ne $BossVersion) {
    Invoke-NpmAt 'clients/boss-resume-assistant' @('version', $BossVersion, '--no-git-tag-version', '--ignore-scripts')
}
$hostVersion = Read-Version 'clients/agent-tool-runtime'
$coreVersion = Read-Version 'clients/shared/local-tool-host-core'
$nativeVersion = Read-Version 'clients/shared/win32-native-input'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$parts = @('AID-Work')
if ($includeRuntime) { $parts += "Runtime-$uiVersion" }
if ($includeBoss) { $parts += "BOSS-$bossVersion" }
$batchName = ($parts + $stamp) -join '-'
$buildRoot = Join-Path $repo "clients/agent-desktop/build/$batchName"
$releaseRoot = Join-Path $repo 'clients/release'
$deliveryRoot = Join-Path $releaseRoot $batchName
$zipPath = Join-Path $releaseRoot "$batchName.zip"
$pluginDir = Join-Path $buildRoot 'plugins'
$hostDir = Join-Path $buildRoot 'host'
if ((Test-Path -LiteralPath $buildRoot) -or (Test-Path -LiteralPath $deliveryRoot) -or (Test-Path -LiteralPath $zipPath)) {
    throw '本批次输出已存在，请使用新批次，不覆盖交付文件'
}
New-Item -ItemType Directory -Path $buildRoot, $releaseRoot -Force | Out-Null
$sourceBaseCommit = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw '读取源码提交失败' }
Write-Host "batch=$batchName base=$sourceBaseCommit component=$Component profile=$ReleaseProfile"

Write-Host '[stage 3] 所选组件依赖与构建'
# shared is the workspace root; do not repeatedly replace its hoisted dependencies.
Invoke-NpmAt 'clients/shared' @('ci', '--ignore-scripts')
Invoke-NpmAt 'clients/shared/win32-native-input' @('run', 'build')
Invoke-NpmAt 'clients/shared/local-tool-host-core' @('run', 'build')
if ($includeBoss) {
    Invoke-NpmAt 'clients/boss-resume-assistant' @('ci', '--ignore-scripts')
    Invoke-NpmAt 'clients/runtime-plugin-packaging' @('ci', '--ignore-scripts')
    Invoke-NpmAt 'clients/boss-resume-assistant' @('run', 'build:main')
}
if ($includeRuntime) {
    Invoke-NpmAt 'clients/agent-tool-runtime' @('ci', '--ignore-scripts')
    Invoke-NpmAt 'frontend' @('ci')
    Invoke-NpmAt 'clients/agent-desktop' @('ci')
}
Invoke-Checked $nodeExe @('clients/agent-desktop/scripts/prepare-runtime.mjs', '--node-only')
$nodeSpec = Get-Content -LiteralPath (Join-Path $repo 'clients/agent-desktop/scripts/runtime-node-resources.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$fixedNode = Join-Path $repo "clients/agent-desktop/build/runtime-cache/node-v$($nodeSpec.version)-win-x64/node.exe"
Invoke-Checked $fixedNode @('--version')
$deliveryFilesToCopy = @()

if ($includeBoss) {
    Write-Host '[stage 4] 使用稳定发布者签署 BOSS 插件'
    Invoke-Checked $nodeExe @(
        'clients/runtime-plugin-packaging/package.mjs', '--provider', 'boss',
        '--signer', $Signer, '--release-id', $ReleaseId, '--node', $fixedNode,
        '--npm-cli', $npmCli, '--output', $pluginDir
    )
    $pluginPath = Join-Path $pluginDir "boss-$bossVersion.aidplugin.zip"
    $deliveryFilesToCopy += @($pluginPath, "$pluginPath.manifest.json")
}

$hostSha = $null
$runtimeManifest = $null
if ($includeRuntime) {
    Write-Host '[stage 5] Host 与 Runtime 安装器'
    New-Item -ItemType Directory -Path $hostDir -Force | Out-Null
    Invoke-NpmAt 'clients/agent-tool-runtime' @('pack', '--pack-destination', $hostDir)
    $hostPackage = Join-Path $hostDir "agent-tool-runtime-$hostVersion.tgz"
    $hostSha = (Get-FileHash -LiteralPath $hostPackage -Algorithm SHA256).Hash.ToLowerInvariant()
    Invoke-Checked $nodeExe @(
        'clients/agent-desktop/scripts/prepare-runtime.mjs', '--profile', $ReleaseProfile,
        '--host-package', $hostPackage, '--host-sha256', $hostSha, '--trust-roots', $TrustRoots
    )
    Invoke-NpmAt 'clients/agent-desktop' @('run', "runtime:package:$ReleaseProfile")
    # package-runtime-win already verifies this profile's exact output directory.
    $runtimeOutput = Join-Path $repo "clients/agent-desktop/build/runtime-release/$ReleaseProfile"
    $runtimeManifestPath = Join-Path $runtimeOutput 'runtime-release-manifest.json'
    $runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $installerPath = Join-Path $runtimeOutput $runtimeManifest.fileName
    if ($runtimeManifest.version -ne $uiVersion -or $runtimeManifest.hostPackageSha256 -ne $hostSha) { throw '安装器版本或 Host 输入与本批不符' }
    $deliveryFilesToCopy += @($installerPath, $runtimeManifestPath)
}

if ($includeBoss) {
    Write-Host '[stage 6] 插件验包与原生模块加载验证'
    $pluginReportPath = Join-Path $buildRoot 'plugin-verification.json'
    Invoke-Checked $pythonExe @('clients/runtime-plugin-packaging/verify-offline.py', '--directory', $pluginDir,
        '--node', $fixedNode, '--trust-roots', $TrustRoots) | Tee-Object -FilePath $pluginReportPath
    $outerCheck = Join-Path $buildRoot 'plugin-check/outer'
    $payloadCheck = Join-Path $buildRoot 'plugin-check/payload'
    Expand-Archive -LiteralPath $pluginPath -DestinationPath $outerCheck
    Expand-Archive -LiteralPath (Join-Path $outerCheck 'payload.zip') -DestinationPath $payloadCheck
    $nativeModuleUri = ([Uri](Join-Path $payloadCheck 'node_modules/@aid/win32-native-input/dist/index.js')).AbsoluteUri
    Invoke-Checked $fixedNode @('--input-type=module', '-e',
        "const m = await import(process.argv[1]); m.createKoffiApi(); console.log('NATIVE_INPUT_LOAD_OK');", $nativeModuleUri)
    $deliveryFilesToCopy += $pluginReportPath
}

Write-Host '[stage 7] 所选组件打包定向测试'
if ($includeBoss) { Invoke-NpmAt 'clients/runtime-plugin-packaging' @('test') }
if ($includeRuntime) { Invoke-NpmAt 'clients/agent-desktop' @('run', 'runtime:test:packaging') }

Write-Host '[stage 8] 交付文件与公开说明'
New-Item -ItemType Directory -Path $deliveryRoot | Out-Null
foreach ($file in $deliveryFilesToCopy) { Copy-Item -LiteralPath $file -Destination $deliveryRoot }
Copy-Item -LiteralPath $TrustRoots -Destination (Join-Path $deliveryRoot "$ReleaseProfile-roots.json")
$sourceState = @(& git status --porcelain --untracked-files=normal)
if ($LASTEXITCODE -ne 0) { throw '读取源码状态失败' }
$buildInfo = [ordered]@{
    schemaVersion = 1; batch = $batchName; profile = $ReleaseProfile; component = $Component
    builtAt = (Get-Date).ToString('o'); baseCommit = $sourceBaseCommit; workingTree = $sourceState
    versions = [ordered]@{ ui = $uiVersion; host = $hostVersion; core = $coreVersion; nativeInput = $nativeVersion; boss = $bossVersion; node = $nodeSpec.version }
    hostPackageSha256 = $hostSha; buildInputSha256 = if ($runtimeManifest) { $runtimeManifest.buildInputSha256 } else { $null }
    verification = [ordered]@{
        runtimePackage = if ($includeRuntime) { 'passed' } else { 'not_selected' }
        runtimeVerify = if ($includeRuntime) { 'passed' } else { 'not_selected' }
        pluginOffline = if ($includeBoss) { 'passed' } else { 'not_selected' }
        nativeInputLoad = if ($includeBoss) { 'passed' } else { 'not_selected' }
        nsisInstallation = 'not_run'; oldInventoryUpgrade = 'not_run'; runtimeUiSmoke = 'not_run'; bossBusiness = 'not_run'; visualRecovery = 'not_run'
    }
}
Write-Utf8 (Join-Path $deliveryRoot 'build-info.json') (($buildInfo | ConvertTo-Json -Depth 12) + "`n")
$instructions = @()
if ($includeRuntime) {
    $instructions += '1. 正常退出自己正在使用的 Runtime 后，运行本包 EXE；兼容配置与配对沿用，安装器不自动更新 BOSS 插件。'
}
if ($includeBoss) {
    $instructions += "通过 Runtime 的离线包安装入口导入 boss-$bossVersion.aidplugin.zip，不要解压插件。只更新插件时无需重装已满足要求且信任该发布者的 Runtime。"
}
$readme = @"
# Runtime / BOSS 离线交付说明

本批：$batchName；组件：$Component；发行配置：$ReleaseProfile。
$(if ($ReleaseProfile -eq 'acceptance') { 'Windows 验收安装器未做正式代码签名；插件仍有 Ed25519 发布者签名。' } else { 'Windows 安装器与插件按正式配置签名。' })
$($instructions -join "`n")

已有客户的旧 Runtime 和 BOSS 满足原需求即可继续使用，无需随服务端升级。本包不会安装软件、部署后台或执行聊天/招聘业务。
首次使用在 Web 获取配对码；已配对用户保留原服务器和身份。业务工具仍需在使用时登录对应软件。
$ReleaseProfile-roots.json 是公开验签资料，不含私钥。新发布者必须被目标 Runtime 信任；本包不承诺旧客户端认识未曾交付的新根。
本批实际验证范围见 build-info.json，not_selected / not_run 均不表示已通过。
"@
Write-Utf8 (Join-Path $deliveryRoot '先读我.md') ($readme + "`n")

Write-Host '[stage 9] SHA-256 与最终 ZIP'
$sumLines = @(Get-ChildItem -LiteralPath $deliveryRoot -File | Where-Object { $_.Name -ne 'SHA256SUMS.txt' } | Sort-Object Name | ForEach-Object {
    '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $_.Name
})
Write-Utf8 (Join-Path $deliveryRoot 'SHA256SUMS.txt') (($sumLines -join "`n") + "`n")
$deliveryFiles = @(Get-ChildItem -LiteralPath $deliveryRoot -File)
# 占用自愈：监视锁为秒级（编辑器 watcher 读完即释放），失败时先诊断占用者再退避重试；
# 中途失败的 ZIP 是残件，重试前必须删除
$compressError = $null
for ($attempt = 1; $attempt -le 4; $attempt++) {
    if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
    try {
        Compress-Archive -LiteralPath @($deliveryFiles.FullName) -DestinationPath $zipPath -CompressionLevel Optimal -ErrorAction Stop
        $compressError = $null
        break
    } catch {
        $compressError = $_
        Write-Host "[stage 9] 压缩第 $attempt 次失败（$($_.Exception.Message)），占用者诊断："
        foreach ($file in $deliveryFiles) { Show-Lockers $file.FullName }
        Start-Sleep -Seconds (3 * $attempt)
    }
}
if ($compressError) { throw $compressError }
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
Write-Host '=== ALL DONE ==='
Get-Item -LiteralPath $zipPath, "$zipPath.sha256" | Select-Object FullName, Length
