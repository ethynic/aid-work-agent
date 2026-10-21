# 按 docs/ops/runtime-boss-release-guide.md 第 2-9 节组合的一键执行脚本（配套入口，文档为语义权威）。
# stage 9 内置文件占用自愈：workspace 内编辑器/索引器对刚复制文件的短暂监视锁
# （2026-10-21 实跑两次复现，RM API 实证占用者为 VS Code 文件 watcher）会导致
# Compress-Archive PermissionDenied——失败时用 Restart Manager 诊断占用者并退避重试。
$ErrorActionPreference = 'Stop'
# 本脚本预期使用 Windows 自带 bsdtar（System32	ar.exe）；从 Git Bash 会话启动时 GNU tar 在 PATH
# 更前，会把 C:\ 绝对路径当远程主机（"Cannot connect to C"）——把 System32 前置修正解析顺序
$env:PATH = "$env:SystemRoot\System32;" + $env:PATH
$repo = (& git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw '请在 aid-work-agent 仓库内执行' }
Set-Location -LiteralPath $repo
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
function Read-Version {
    param([string]$Directory)
    (Get-Content -LiteralPath (Join-Path $repo "$Directory/package.json") -Raw -Encoding UTF8 | ConvertFrom-Json).version
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

Write-Host '[stage 2] 版本与新批次'
$bumpPackages = @('clients/agent-desktop', 'clients/boss-resume-assistant')
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
    throw '批次或该 UI 版本的发布快照已存在，请核对版本并使用新批次'
}
New-Item -ItemType Directory -Path $buildRoot, $hostDir -Force | Out-Null
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
$sourceBaseCommit = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw '读取源码提交失败' }
Write-Host "batch=$batchName base=$sourceBaseCommit ui=$uiVersion host=$hostVersion boss=$bossVersion"

Write-Host '[stage 3] 依赖安装与构建'
# clients/shared 是 npm workspace 根：依赖提升到 shared 根，必须从根一次性安装
# （逐成员 ci 会把根 node_modules 重装成单个成员的依赖集，互相冲掉——koffi 曾因此丢失）
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

Write-Host '[stage 4] 签署 BOSS 插件并登记公钥'
$testKeyId = "aid-isolated-boss-$($bossVersion.Replace('.', '-'))-$stamp"
Invoke-Checked $nodeExe @(
    'clients/runtime-plugin-packaging/package.mjs', '--test', '--provider', 'boss',
    '--test-key-id', $testKeyId, '--node', $fixedNode, '--npm-cli', $npmCli,
    '--output', $pluginDir
)
$pluginPath = Join-Path $pluginDir "boss-$bossVersion-dev.aidplugin.zip"
$newRootPath = Join-Path $pluginDir 'ISOLATED-TEST-TRUST-ROOT.json'
$newRoot = Get-Content -LiteralPath $newRootPath -Raw -Encoding UTF8 | ConvertFrom-Json
$newRoot.roots[0].providers = @('ai.aidwork.boss-recruiting')
Write-Utf8 $newRootPath (($newRoot | ConvertTo-Json -Depth 10) + "`n")
Invoke-Checked $nodeExe @(
    'clients/runtime-plugin-packaging/register-publisher-trust.mjs', '--input', $newRootPath
)
Copy-Item -LiteralPath $registryPath -Destination $snapshotPath

Write-Host '[stage 5] Host tgz 与 Runtime 安装器'
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

Write-Host '[stage 6] 插件验包与原生模块加载验证'
$pluginReportPath = Join-Path $buildRoot 'plugin-verification.json'
Invoke-Checked $pythonExe @(
    'clients/runtime-plugin-packaging/verify-offline.py', '--directory', $pluginDir, '--node', $fixedNode
) | Tee-Object -FilePath $pluginReportPath
$outerCheck = Join-Path $buildRoot 'plugin-check/outer'
$payloadCheck = Join-Path $buildRoot 'plugin-check/payload'
Expand-Archive -LiteralPath $pluginPath -DestinationPath $outerCheck
Expand-Archive -LiteralPath (Join-Path $outerCheck 'payload.zip') -DestinationPath $payloadCheck
$nativeModule = Join-Path $payloadCheck 'node_modules/@aid/win32-native-input/dist/index.js'
$nativeModuleUri = ([Uri]$nativeModule).AbsoluteUri
Invoke-Checked $fixedNode @('--input-type=module', '-e',
    "const m = await import(process.argv[1]); m.createKoffiApi(); console.log('NATIVE_INPUT_LOAD_OK');", $nativeModuleUri)

Write-Host '[stage 7] 打包定向测试'
Invoke-NpmAt 'clients/runtime-plugin-packaging' @('test')
Invoke-NpmAt 'clients/agent-desktop' @('run', 'runtime:test:packaging')

Write-Host '[stage 8] 复制交付文件与说明'
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
3. 打开 Runtime，用"安装 / 升级离线包"导入本 ZIP 内的 boss-$bossVersion-dev.aidplugin.zip；插件不要解压。
4. 已配对用户沿用原身份；首次使用在 Web 生成配对码，在 Runtime 填服务器根地址和配对码。
5. 准备原有调试 Chrome 并登录 BOSS，确认插件版本/启用状态，启动执行并在 Web 选定同一设备。

Runtime 安装器不自动更新 BOSS 插件，第 3 步不可遗漏。客户机无需安装源码、Node、npm 或 Python。
视觉恢复复用 Runtime 配对的服务器和设备身份，不需要额外 token 或环境变量；后台须有配套设备视觉 API，按后台 20 倍规则结算。服务器发布另行安排，本包不会部署后台。
acceptance-roots.json 是公开验签资料，已包含在安装器中，无需客户手工导入。
本批自动验包与实际安装、业务实测的范围见 build-info.json；not_run 不表示已经通过。
默认本验收 Runtime 日志：%APPDATA%\aidwork-tool-runtime-acceptance\logs\runtime.log。
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
