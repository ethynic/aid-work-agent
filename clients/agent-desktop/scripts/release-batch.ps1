# 按 docs/ops/runtime-boss-release-guide.md 所述独立组件打包入口（配套入口，文档为语义权威）。
# stage 9 内置文件占用自愈：workspace 内编辑器/索引器对刚复制文件的短暂监视锁
# （2026-10-21 实跑两次复现，RM API 实证占用者为 VS Code 文件 watcher）会导致
# Compress-Archive PermissionDenied——失败时用 Restart Manager 诊断占用者并退避重试。
param(
    [ValidateSet('All', 'Both', 'Runtime', 'Boss', 'Wecom', 'RuntimeWecom')][string]$Component = 'Both',
    [Alias('Profile')][ValidateSet('acceptance', 'production')][string]$ReleaseProfile = 'acceptance',
    [string]$Signer,
    [string]$TrustRoots,
    [string]$ReleaseId,
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')][string]$RuntimeVersion,
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')][string]$BossVersion,
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')][string]$WecomVersion,
    [string]$LocaldbPythonDirectory,
    [string]$TargetRuntimeResources,
    [string]$SourceRef = 'HEAD',
    [string]$OutputDirectory,
    [switch]$WorkspacePrepared,
    [string]$SourceCommit,
    [switch]$Unsigned,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$includeRuntime = $Component -in @('All', 'Both', 'Runtime', 'RuntimeWecom')
$includeBoss = $Component -in @('All', 'Both', 'Boss')
$includeWecom = $Component -in @('All', 'Wecom', 'RuntimeWecom')
$includePlugin = $includeBoss -or $includeWecom
$localBuildRoot = Join-Path $env:LOCALAPPDATA 'AidWorkBuild'
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $downloads = [Microsoft.Win32.Registry]::GetValue('HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders', '{374DE290-123F-4565-9164-39C4925E467B}', $null)
    if (!$downloads) { $downloads = Join-Path $env:USERPROFILE 'Downloads' }
    $OutputDirectory = Join-Path ([Environment]::ExpandEnvironmentVariables($downloads)) 'AID-Work-Releases'
}
$OutputDirectory = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
if ($includePlugin -and [string]::IsNullOrWhiteSpace($Signer)) {
    if ($ReleaseProfile -eq 'production') { throw '生产插件发布需要 -Signer 指定正式签名配置' }
    $Signer = Join-Path $localBuildRoot 'signers/acceptance-cli-v1/signer.json'
}
if ([string]::IsNullOrWhiteSpace($TrustRoots)) {
    if ($ReleaseProfile -eq 'production') { throw '生产构建需要 -TrustRoots 指定正式公开信任根' }
    $TrustRoots = Join-Path $repo 'clients/runtime-plugin-packaging/trust/acceptance-roots.json'
}
$TrustRoots = [IO.Path]::GetFullPath($TrustRoots)
if ($includePlugin) { $Signer = [IO.Path]::GetFullPath($Signer) }
function Read-Version {
    param([string]$Directory)
    (Get-Content -LiteralPath (Join-Path $repo "$Directory/package.json") -Raw -Encoding UTF8 | ConvertFrom-Json).version
}
function Expand-SourceArchive {
    param([string]$Archive, [string]$Workspace)
    # Windows bsdtar can reject this repository's UTF-8 Chinese paths; Python is already a build dependency.
    & python.exe -X utf8 -c 'import sys, tarfile; tarfile.open(sys.argv[1]).extractall(sys.argv[2])' $Archive $Workspace
    if ($LASTEXITCODE -ne 0) { throw '源码归档展开失败' }
}
$uiVersion = if ($includeRuntime -and $RuntimeVersion) { $RuntimeVersion } else { Read-Version 'clients/agent-desktop' }
$selectedBossVersion = if ($includeBoss -and $BossVersion) { $BossVersion } else { Read-Version 'clients/boss-resume-assistant' }
$selectedWecomVersion = if ($includeWecom -and $WecomVersion) { $WecomVersion } else { Read-Version 'clients/wecom-cli' }
$pluginVersions = [ordered]@{}
if ($includeBoss) { $pluginVersions['boss'] = $selectedBossVersion }
if ($includeWecom) { $pluginVersions['wecom'] = $selectedWecomVersion }
$releaseIds = [ordered]@{}
foreach ($provider in $pluginVersions.Keys) {
    $releaseIds[$provider] = if (![string]::IsNullOrWhiteSpace($ReleaseId)) { $ReleaseId } else { "$provider-$($pluginVersions[$provider])" }
}
if ($Plan) {
    if ($includeBoss -and !$BossVersion -and !$ReleaseId) { $releaseIds['boss'] = $null }
    if ($includeWecom -and !$WecomVersion -and !$ReleaseId) { $releaseIds['wecom'] = $null }
    [ordered]@{
        component = $Component; profile = $ReleaseProfile; runtime = $includeRuntime; boss = $includeBoss
        versions = [ordered]@{ runtime = if ($includeRuntime -and !$RuntimeVersion) { $null } else { $uiVersion }; boss = if ($includeBoss -and !$BossVersion) { $null } else { $selectedBossVersion } }
        wecom = $includeWecom; wecomVersion = if ($includeWecom -and $WecomVersion) { $selectedWecomVersion } else { $null }
        runtimeSigning = if (!$includeRuntime) { 'not_selected' } elseif ($Unsigned -or $ReleaseProfile -eq 'acceptance') { 'unsigned' } else { 'signed' }
        versionDefaults = 'Unspecified selected versions come from SourceRef and are resolved during isolation; Plan does not execute git.'
        sourceRef = $SourceRef; isolated = $true; outputDirectory = $OutputDirectory
        signing = if ($includePlugin) { 'external-stable' } else { 'not_selected' }
        releaseId = if ($releaseIds.Count -eq 1) { @($releaseIds.Values)[0] } else { $ReleaseId }; releaseIds = $releaseIds; trustRoots = $TrustRoots
        steps = @('isolate-source', 'shared-build', 'fixed-node') + $(if ($includeBoss) { @('boss-build') } else { @() }) + $(if ($includeWecom) { @('wecom-build') } else { @() }) + $(if ($includePlugin) { @('sign-plugin', 'verify-plugin') } else { @() }) + $(if ($includeRuntime) { @('runtime-build', 'package-installer', 'verify-runtime') } else { @() }) + $(if ($includeWecom -or ($includeBoss -and ($includeRuntime -or $TargetRuntimeResources))) { @('verify-target-runtime') } else { @() }) + @('delivery-zip')
    } | ConvertTo-Json -Depth 5
    return
}
# Plan returns before external tools, version writes, output creation or private-key access.
if ($includePlugin -and !(Test-Path -LiteralPath $Signer -PathType Leaf)) {
    throw "Signer 配置不存在：$Signer。请由构建机管理员配置已登记的签名者，或使用 -Signer 指定已有配置。"
}
if (!$WorkspacePrepared) {
    if ($includeWecom) {
        $defaultLocaldbPython = Join-Path $localBuildRoot 'assets/localdb-python-final'
        if (!$LocaldbPythonDirectory) {
            $LocaldbPythonDirectory = Join-Path $repo 'clients/runtime-plugin-packaging/assets/localdb-python-final'
            if (!(Test-Path -LiteralPath (Join-Path $LocaldbPythonDirectory 'python.exe') -PathType Leaf)) {
                $LocaldbPythonDirectory = $defaultLocaldbPython
            }
        }
        $LocaldbPythonDirectory = [IO.Path]::GetFullPath($LocaldbPythonDirectory)
        if (!(Test-Path -LiteralPath (Join-Path $LocaldbPythonDirectory 'python.exe') -PathType Leaf)) {
            throw "缺少 WeCom 便携 Python：$LocaldbPythonDirectory。请运行 python.exe clients/runtime-plugin-packaging/prepare-localdb.py --output `"$defaultLocaldbPython`"，或用 -LocaldbPythonDirectory 指定已准备的目录。"
        }
        # WeCom date/IANA capability must be present in the independent local database Python.
        if ($includeWecom) {
            $dayProbe = & (Join-Path $LocaldbPythonDirectory 'python.exe') -I -B -c "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai'); print('TZDATA_OK')"
            if ($LASTEXITCODE -ne 0 -or $dayProbe -notmatch 'TZDATA_OK') { throw '嵌入式 Python 缺少日期/IANA 能力（tzdata）；请先运行 clients/runtime-plugin-packaging/prepare-localdb.py 刷新 localdb-python-final 资产后重试' }
        }
    }
    $commit = & git -C $repo rev-parse --verify "$SourceRef^{commit}"
    if ($LASTEXITCODE -ne 0) { throw 'SourceRef 无法解析为源码提交' }
    $commit = $commit.Trim()
    # Keep dependency junctions in the tested Temp build area; deliveries remain persistent outside it.
    $isolatedRoot = Join-Path $env:TEMP ("aid-release-" + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    $workspace = Join-Path $isolatedRoot 'workspace'
    New-Item -ItemType Directory -Path $workspace -Force | Out-Null
    $archive = Join-Path $isolatedRoot 'source.tar'
    & git -C $repo archive --format=tar --output=$archive $commit
    if ($LASTEXITCODE -ne 0) { throw '源码归档失败' }
    Expand-SourceArchive $archive $workspace
    # Only the packaging implementation under review is overlaid; business WIP is never copied.
    foreach ($relative in @('clients/agent-desktop/scripts/release-batch.ps1', 'clients/agent-desktop/scripts/verify-plugin-runtime.mjs', 'clients/agent-desktop/scripts/tests/release-batch.test.mjs', 'clients/agent-desktop/scripts/tests/plugin-runtime.test.mjs', 'clients/runtime-plugin-packaging/package.mjs', 'clients/runtime-plugin-packaging/tests/package.test.mjs', 'clients/agent-desktop/scripts/package-runtime-win.mjs', 'clients/agent-desktop/scripts/verify-runtime-package.mjs', 'clients/agent-desktop/electron/releasePolicy.ts', 'clients/agent-desktop/tests/releasePolicy.test.ts', 'clients/agent-desktop/assets/runtime.ico')) {
        New-Item -ItemType Directory -Path (Split-Path -Parent (Join-Path $workspace $relative)) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $repo $relative) -Destination (Join-Path $workspace $relative) -Force
    }
    $forward = @('-NoProfile', '-NonInteractive', '-File', (Join-Path $workspace 'clients/agent-desktop/scripts/release-batch.ps1'), '-WorkspacePrepared', '-SourceCommit', $commit, '-OutputDirectory', $OutputDirectory)
    foreach ($key in $PSBoundParameters.Keys) {
        if ($key -in @('Plan', 'WorkspacePrepared', 'SourceCommit', 'Signer', 'LocaldbPythonDirectory', 'OutputDirectory')) { continue }
        if ($key -eq 'Unsigned') { if ($Unsigned) { $forward += '-Unsigned' }; continue }
        $value = $PSBoundParameters[$key]
        if ($key -in @('TrustRoots', 'TargetRuntimeResources')) { $value = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($value) }
        $forward += @("-$key", [string]$value)
    }
    if ($includePlugin) { $forward += @('-Signer', $Signer) }
    if ($includeWecom) { $forward += @('-LocaldbPythonDirectory', $LocaldbPythonDirectory) }
    & powershell.exe @forward
    if ($LASTEXITCODE -ne 0) { throw '隔离打包失败；保留隔离目录供诊断' }
    return
}
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
if ($includeWecom -and $WecomVersion -and (Read-Version 'clients/wecom-cli') -ne $WecomVersion) {
    Invoke-NpmAt 'clients/wecom-cli' @('version', $WecomVersion, '--no-git-tag-version', '--ignore-scripts')
}
$hostVersion = Read-Version 'clients/agent-tool-runtime'
$coreVersion = Read-Version 'clients/shared/local-tool-host-core'
$nativeVersion = Read-Version 'clients/shared/win32-native-input'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$parts = @('AID-Work')
if ($includeRuntime) { $parts += "Runtime-$uiVersion" }
if ($includeBoss) { $parts += "BOSS-$selectedBossVersion" }
if ($includeWecom) { $parts += "WeCom-$selectedWecomVersion" }
$batchName = ($parts + $stamp) -join '-'
$buildRoot = Join-Path $repo "clients/agent-desktop/build/$batchName"
$releaseRoot = [IO.Path]::GetFullPath($OutputDirectory)
$deliveryRoot = Join-Path $releaseRoot $batchName
$zipPath = Join-Path $releaseRoot "$batchName.zip"
$pluginDir = Join-Path $buildRoot 'plugins'
$hostDir = Join-Path $buildRoot 'host'
if ((Test-Path -LiteralPath $buildRoot) -or (Test-Path -LiteralPath $deliveryRoot) -or (Test-Path -LiteralPath $zipPath)) {
    throw '本批次输出已存在，请使用新批次，不覆盖交付文件'
}
New-Item -ItemType Directory -Path $buildRoot, $releaseRoot -Force | Out-Null
$sourceBaseCommit = $SourceCommit
if (!$sourceBaseCommit) { throw '隔离源码缺少 SourceCommit' }
Write-Host "batch=$batchName base=$sourceBaseCommit component=$Component profile=$ReleaseProfile"

Write-Host '[stage 3] 所选组件依赖与构建'
# shared is the workspace root; do not repeatedly replace its hoisted dependencies.
Invoke-NpmAt 'clients/shared' @('ci', '--ignore-scripts')
Invoke-NpmAt 'clients/shared/win32-native-input' @('run', 'build')
Invoke-NpmAt 'clients/shared/local-tool-host-core' @('run', 'build')
if ($includeBoss) {
    Invoke-NpmAt 'clients/boss-resume-assistant' @('ci', '--ignore-scripts')
    Invoke-NpmAt 'clients/boss-resume-assistant' @('run', 'build:main')
}
if ($includePlugin) { Invoke-NpmAt 'clients/runtime-plugin-packaging' @('ci', '--ignore-scripts') }
if ($includeWecom) {
    Invoke-NpmAt 'clients/wecom-cli' @('ci', '--ignore-scripts')
    Invoke-NpmAt 'clients/wecom-cli' @('run', 'build')
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
$pluginPaths = @{}

foreach ($provider in $pluginVersions.Keys) {
    Write-Host "[stage 4] 使用稳定发布者签署 $provider 插件"
    $packageArgs = @(
        'clients/runtime-plugin-packaging/package.mjs', '--provider', $provider,
        '--signer', $Signer, '--release-id', $releaseIds[$provider], '--node', $fixedNode,
        '--npm-cli', $npmCli, '--output', $pluginDir
    )
    if ($provider -eq 'wecom') { $packageArgs += @('--localdb-python', $LocaldbPythonDirectory) }
    Invoke-Checked $nodeExe $packageArgs
    $pluginPath = Join-Path $pluginDir "$provider-$($pluginVersions[$provider]).aidplugin.zip"
    $pluginPaths[$provider] = $pluginPath
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
    if ($Unsigned -and $ReleaseProfile -eq 'production') {
        Invoke-NpmAt 'clients/agent-desktop' @('run', 'build')
        Invoke-Checked $nodeExe @('clients/agent-desktop/scripts/package-runtime-win.mjs', 'production', '--unsigned')
    } else { Invoke-NpmAt 'clients/agent-desktop' @('run', "runtime:package:$ReleaseProfile") }
    # package-runtime-win already verifies this profile's exact output directory.
    $runtimeOutput = Join-Path $repo "clients/agent-desktop/build/runtime-release/$ReleaseProfile"
    $runtimeManifestPath = Join-Path $runtimeOutput 'runtime-release-manifest.json'
    $runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $installerPath = Join-Path $runtimeOutput $runtimeManifest.fileName
    if ($runtimeManifest.version -ne $uiVersion -or $runtimeManifest.hostPackageSha256 -ne $hostSha) { throw '安装器版本或 Host 输入与本批不符' }
    $deliveryFilesToCopy += @($installerPath, $runtimeManifestPath)
}

if ($includePlugin) {
    Write-Host '[stage 6] 插件验包与原生模块加载验证'
    $pluginReportPath = Join-Path $buildRoot 'plugin-verification.json'
    Invoke-Checked $pythonExe @('clients/runtime-plugin-packaging/verify-offline.py', '--directory', $pluginDir,
        '--node', $fixedNode, '--trust-roots', $TrustRoots) | Tee-Object -FilePath $pluginReportPath
    if ($includeBoss) {
        $outerCheck = Join-Path $buildRoot 'plugin-check/outer'
        $payloadCheck = Join-Path $buildRoot 'plugin-check/payload'
        Expand-Archive -LiteralPath $pluginPaths['boss'] -DestinationPath $outerCheck
        Expand-Archive -LiteralPath (Join-Path $outerCheck 'payload.zip') -DestinationPath $payloadCheck
        $nativeModuleUri = ([Uri](Join-Path $payloadCheck 'node_modules/@aid/win32-native-input/dist/index.js')).AbsoluteUri
        Invoke-Checked $fixedNode @('--input-type=module', '-e',
            "const m = await import(process.argv[1]); m.createKoffiApi(); console.log('NATIVE_INPUT_LOAD_OK');", $nativeModuleUri)
    }
    $deliveryFilesToCopy += $pluginReportPath
}

$targetVerification = @{}
if ($includePlugin) {
    if ($includeRuntime) { $TargetRuntimeResources = Join-Path $runtimeOutput 'win-unpacked/resources/runtime' }
    elseif ($includeWecom -and !$TargetRuntimeResources) {
        $installedResources = Join-Path $env:LOCALAPPDATA 'Programs/aid-agent-desktop/resources/runtime'
        if (Test-Path -LiteralPath $installedResources) { $TargetRuntimeResources = $installedResources }
    }
    foreach ($provider in $pluginVersions.Keys) {
        if ($TargetRuntimeResources) {
            $targetReport = Join-Path $buildRoot "$provider-runtime-verification.json"
            Invoke-Checked $fixedNode @('clients/agent-desktop/scripts/verify-plugin-runtime.mjs', '--provider', $provider, '--resources', $TargetRuntimeResources, '--package', $pluginPaths[$provider], '--output', $targetReport)
            $deliveryFilesToCopy += $targetReport
            $targetVerification[$provider] = 'passed'
        } else {
            $targetVerification[$provider] = 'not_run_no_target'
            Write-Warning "未提供 $provider 的目标 Runtime，只完成插件验包；可用 -TargetRuntimeResources 核验指定 Runtime。"
        }
    }
}

Write-Host '[stage 7] 所选组件打包定向测试'
if ($includePlugin) { Invoke-NpmAt 'clients/runtime-plugin-packaging' @('test') }
Invoke-Checked $nodeExe @('--test', 'clients/agent-desktop/scripts/tests/release-batch.test.mjs')
if ($TargetRuntimeResources -and $includePlugin) { Invoke-Checked $nodeExe @('--test', 'clients/agent-desktop/scripts/tests/plugin-runtime.test.mjs') }
if ($includeRuntime) { Invoke-NpmAt 'clients/agent-desktop' @('run', 'runtime:test:packaging') }

Write-Host '[stage 8] 交付文件与公开说明'
New-Item -ItemType Directory -Path $deliveryRoot | Out-Null
foreach ($file in $deliveryFilesToCopy) { Copy-Item -LiteralPath $file -Destination $deliveryRoot }
Copy-Item -LiteralPath $TrustRoots -Destination (Join-Path $deliveryRoot "$ReleaseProfile-roots.json")
$sourceState = @('isolated git archive; packaging-only working tree overlay; explicit selected component versions')
$buildInfo = [ordered]@{
    schemaVersion = 1; batch = $batchName; profile = $ReleaseProfile; component = $Component
    builtAt = (Get-Date).ToString('o'); baseCommit = $sourceBaseCommit; workingTree = $sourceState
    versions = [ordered]@{ ui = $uiVersion; host = $hostVersion; core = $coreVersion; nativeInput = $nativeVersion; boss = $selectedBossVersion; wecom = $selectedWecomVersion; node = $nodeSpec.version }
    hostPackageSha256 = $hostSha; buildInputSha256 = if ($runtimeManifest) { $runtimeManifest.buildInputSha256 } else { $null }
    verification = [ordered]@{
        runtimePackage = if ($includeRuntime) { 'passed' } else { 'not_selected' }
        runtimeVerify = if ($includeRuntime) { 'passed' } else { 'not_selected' }
        pluginOffline = if ($includePlugin) { 'passed' } else { 'not_selected' }
        nativeInputLoad = if ($includeBoss) { 'passed' } else { 'not_selected' }
        bossTargetRuntime = if ($includeBoss) { $targetVerification['boss'] } else { 'not_selected' }
        wecomTargetRuntime = if ($includeWecom) { $targetVerification['wecom'] } else { 'not_selected' }
        nsisInstallation = 'not_run'; oldInventoryUpgrade = 'not_run'; runtimeUiSmoke = 'not_run'; bossBusiness = 'not_run'; visualRecovery = 'not_run'
    }
}
Write-Utf8 (Join-Path $deliveryRoot 'build-info.json') (($buildInfo | ConvertTo-Json -Depth 12) + "`n")
$instructions = @()
if ($includeRuntime) {
    $instructions += '1. 正常退出自己正在使用的 Runtime 后，运行本包 EXE；兼容配置与配对沿用，安装器不自动更新 BOSS 插件。'
}
foreach ($provider in $pluginVersions.Keys) {
    $packageName = [IO.Path]::GetFileName($pluginPaths[$provider])
    $instructions += "通过 Runtime 离线包入口导入 $packageName，保留插件 ZIP 原样。"
    $instructions += if ($targetVerification[$provider] -eq 'passed') { '已通过目标 Runtime 的离线包验证，实际导入和业务调用待安装联调。' } else { '本插件未核验特定客户机，使用 -TargetRuntimeResources 核对目标 Runtime。' }
}
$readme = @"
# Runtime / CLI 离线交付说明

本批：$batchName；组件：$Component；发行配置：$ReleaseProfile。
$(if ($includeRuntime -and ($Unsigned -or $ReleaseProfile -eq 'acceptance')) { 'Windows 安装器未做代码签名；插件仍沿用独立的 Ed25519 发布者签名。' } elseif ($includeRuntime) { 'Windows 安装器按正式配置签名。' })
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
Write-Host "交付目录：$deliveryRoot"
Write-Host "交付 ZIP：$zipPath"
Get-Item -LiteralPath $zipPath, "$zipPath.sha256" | Select-Object FullName, Length
