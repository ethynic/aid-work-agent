$ErrorActionPreference = 'Stop'
# Phase 7（2026-10-20 复盘修复）：诊断导出跟随 installation-v2/current.json 指针定位实际 home，
# 保留 legacy 固定路径兼容。此前固定读 legacy 目录，新版安装代次运行正常的机器会被误报
# homeExists=false（config/device 全部读不到）。只读取证：不修改 current.json、不切换连接、
# 不调用安装初始化；指针校验口径与 @aid/local-tool-host-core/legacy/diagnostics.ts 一致。
# Phase 7 CR（问题4）：采集 logs/log-health.json 日志健康快照（文件写失败计数/原因），
# 读取口径与 diagnostics.ts readLogFileHealth 一致。
$profiles = @(
    @{ profile = 'acceptance'; userData = 'aidwork-runtime-app-acceptance'; legacy = 'aidwork-tool-runtime-acceptance' },
    @{ profile = 'production'; userData = 'aidwork-runtime-app'; legacy = 'aidwork-tool-runtime' }
)
$homeCandidates = @()
$summaryErrors = @()
foreach ($p in $profiles) {
    $userDataPath = Join-Path $env:APPDATA $p.userData
    $legacyHome = Join-Path $env:APPDATA $p.legacy
    $pointerHome = $null; $pointerError = $null
    $pointerFile = Join-Path $userDataPath 'installation-v2\current.json'
    if (Test-Path -LiteralPath $pointerFile) {
        try {
            $pointer = Get-Content -LiteralPath $pointerFile -Raw | ConvertFrom-Json
            $candidateHome = [string]$pointer.home
            $pointerShapeOk = ($pointer.version -eq 2) -and $candidateHome -and [System.IO.Path]::IsPathRooted($candidateHome) -and $candidateHome.StartsWith($userDataPath, [System.StringComparison]::OrdinalIgnoreCase)
            if ($pointerShapeOk) { $pointerHome = $candidateHome } else { $pointerError = 'pointer-invalid-shape' }
        } catch { $pointerError = 'pointer-corrupt-json' }
    }
    if ($null -ne $pointerError) { $summaryErrors += "pointer-error:$($p.profile):$pointerError" }
    $usePointer = ($null -ne $pointerHome) -and (Test-Path -LiteralPath $pointerHome)
    if ($usePointer) { $selected = $pointerHome; $selectedSource = 'installation-v2-pointer' } else { $selected = $legacyHome; $selectedSource = 'legacy-path' }
    $homeCandidates += [ordered]@{
        profile = $p.profile; source = $selectedSource; home = $selected
        homeExists = (Test-Path -LiteralPath $selected)
        pointerHome = $pointerHome; legacyHome = $legacyHome
        legacyHomeExists = (Test-Path -LiteralPath $legacyHome); pointerError = $pointerError
    }
}
$primary = @($homeCandidates | Where-Object { $_.homeExists })[0]
if ($null -eq $primary) { $summaryErrors += 'runtime-home-not-found' }
$runtimeHome = if ($null -ne $primary) { [string]$primary.home } else { [string]$homeCandidates[0].home }
$summary = [ordered]@{
    schemaVersion = 2; collectedAtUtc = [DateTime]::UtcNow.ToString('o')
    homeExists = ($null -ne $primary)
    homeSource = $(if ($null -ne $primary) { [string]$primary.source } else { $null })
    homeCandidates = $homeCandidates
    deviceId = $null; server = $null; bootTimeUtc = $null; runningMarker = $null
    stateDirectories = @{}; operationCounts = @{}; logFiles = @(); logHealth = $null
    relatedProcesses = @(); processInventoryScope = 'Known Runtime paths only; absence does not prove descendants stopped'
    errors = $summaryErrors
}
try {
    $cfg = Get-Content -LiteralPath (Join-Path $runtimeHome 'config.json') -Raw | ConvertFrom-Json
    $summary.deviceId = [string]$cfg.device_id
    $serverUri = [Uri]$cfg.server
    $summary.server = '{0}://{1}:{2}' -f $serverUri.Scheme, $serverUri.Host, $serverUri.Port
} catch { $summary.errors += 'config-unreadable' }
try { $summary.bootTimeUtc = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o') } catch { $summary.errors += 'boot-time-unavailable' }
$marker = Join-Path $runtimeHome 'host-running.json'
if (Test-Path -LiteralPath $marker) {
    try {
        $item = Get-Item -LiteralPath $marker
        $summary.runningMarker = @{ bytes = $item.Length; lastWriteUtc = $item.LastWriteTimeUtc.ToString('o'); sha256 = (Get-FileHash -LiteralPath $marker -Algorithm SHA256).Hash.ToLowerInvariant() }
    } catch { $summary.errors += 'marker-unreadable' }
}
foreach ($name in @('journal', 'result-outbox', 'session-tasks', 'completion-proofs')) {
    $directory = Join-Path $runtimeHome $name
    try {
        if (Test-Path -LiteralPath $directory) {
            $item = Get-Item -LiteralPath $directory
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'linked-directory' }
            $summary.stateDirectories[$name] = @(Get-ChildItem -LiteralPath $directory -Force).Count
        } else { $summary.stateDirectories[$name] = 0 }
    } catch { $summary.stateDirectories[$name] = 'unreadable'; $summary.errors += 'state-directory-unreadable' }
}
foreach ($name in @('runtime.log', 'runtime.log.old.log')) {
    $logFile = Join-Path (Join-Path $runtimeHome 'logs') $name
    try {
        if (Test-Path -LiteralPath $logFile) {
            $item = Get-Item -LiteralPath $logFile
            $summary.logFiles += @{ name = $name; bytes = $item.Length; lastWriteUtc = $item.LastWriteTimeUtc.ToString('o') }
        }
    } catch { $summary.errors += 'log-file-unreadable' }
}
# Phase 7 CR 修复（问题4，二轮补强 + 三轮新鲜度选择）：日志健康快照采集。
# 写入方 legacy/log.ts：主位置 logs/log-health.json；logs 目录不可写（如被同名文件占位）时
# 降级写 home 根 log-health.json（记录内带 degraded=true 与原因）。两位置都解析有效记录，
# 按新鲜度规则（Test-RecordFresher）选择当前快照——不能仅因主位置可读就认定当前记录
#（写端降级后主位置残留旧快照）。口径与 @aid/local-tool-host-core/legacy/diagnostics.ts
# readLogFileHealth 同源同规则。只读取证：ps1 不做可写性探测（log-dir-unwritable 由 TS
# 读取端 accessSync 报告），但目录缺失/被同名文件占位/链接必须显式上报——不得把日志失败呈现为无记录。
# 新鲜度规则（与 TS 读取端 isFresher 一致）：优先记录内 updatedAt（ISO 字典序即时间序）；
# 只有一方有 updatedAt 时有的一方更新（旧格式无该字段视为更旧）；双方 updatedAt 持平或
# 双方均无 updatedAt 用文件 mtime（UTC）兜底；仍持平稳定取先序位置（logs 主位置）。
$logDir = Join-Path $runtimeHome 'logs'
$logDirError = $null
try {
    if (-not (Test-Path -LiteralPath $logDir)) { $logDirError = 'log-dir-missing' }
    else {
        $logDirItem = Get-Item -LiteralPath $logDir -Force
        if ($logDirItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { $logDirError = 'log-dir-linked' }
        elseif (-not $logDirItem.PSIsContainer) { $logDirError = 'log-dir-not-directory' }
    }
} catch { $logDirError = 'log-dir-probe-failed' }
function Test-RecordFresher($a, $b) {
    if ($a.hasUpdatedAt -ne $b.hasUpdatedAt) { return [bool]$a.hasUpdatedAt }
    if ($a.hasUpdatedAt) {
        $cmp = [string]::CompareOrdinal($a.updatedAt, $b.updatedAt)
        if ($cmp -ne 0) { return $cmp -gt 0 }
    }
    return $a.mtimeUtc -gt $b.mtimeUtc
}
$logHealthRecord = $null; $logHealthSource = $null; $logHealthError = $null
$logHealthCandidates = @()
foreach ($candidate in @(
    @{ source = 'logs'; file = (Join-Path $logDir 'log-health.json') },
    @{ source = 'home-root'; file = (Join-Path $runtimeHome 'log-health.json') }
)) {
    if (-not (Test-Path -LiteralPath $candidate.file)) { continue }
    try {
        $lh = Get-Content -LiteralPath $candidate.file -Raw | ConvertFrom-Json
        if ($null -eq $lh.fileWriteFailures) { throw 'invalid-log-health' }
        $lhFailures = [int]$lh.fileWriteFailures
        if ($lhFailures -lt 0) { throw 'invalid-log-health' }
        $lhMtime = (Get-Item -LiteralPath $candidate.file).LastWriteTimeUtc
        $lhHasUpdatedAt = ($null -ne $lh.updatedAt -and [string]$lh.updatedAt -ne '')
        $logHealthCandidates += @{
            record = $lh; source = $candidate.source
            hasUpdatedAt = $lhHasUpdatedAt
            updatedAt = $(if ($lhHasUpdatedAt) { [string]$lh.updatedAt } else { '' })
            mtimeUtc = $lhMtime
        }
    } catch { $logHealthError = 'log-health-unreadable' }
}
if ($logHealthCandidates.Count -gt 0) {
    $chosen = $logHealthCandidates[0]
    foreach ($c in $logHealthCandidates) {
        if ($c -eq $chosen) { continue }
        if (Test-RecordFresher $c $chosen) { $chosen = $c }
    }
    $logHealthRecord = $chosen.record; $logHealthSource = $chosen.source
}
if ($null -eq $logHealthRecord -and $null -eq $logHealthError) { $logHealthError = $logDirError }
$summary.logHealth = @{
    source = $logHealthSource
    fileWriteFailures = $(if ($null -ne $logHealthRecord) { [int]$logHealthRecord.fileWriteFailures } else { $null })
    lastFileWriteErrorAt = $(if ($null -ne $logHealthRecord -and $null -ne $logHealthRecord.lastFileWriteErrorAt) { [string]$logHealthRecord.lastFileWriteErrorAt } else { $null })
    lastFileWriteError = $(if ($null -ne $logHealthRecord -and $null -ne $logHealthRecord.lastFileWriteError) { [string]$logHealthRecord.lastFileWriteError } else { $null })
    degraded = $(if ($null -ne $logHealthRecord -and $null -ne $logHealthRecord.degraded) { [bool]$logHealthRecord.degraded } else { $null })
    degradedReason = $(if ($null -ne $logHealthRecord -and $null -ne $logHealthRecord.degradedReason) { [string]$logHealthRecord.degradedReason } else { $null })
    updatedAt = $(if ($null -ne $logHealthRecord -and $null -ne $logHealthRecord.updatedAt) { [string]$logHealthRecord.updatedAt } else { $null })
    logDirError = $logDirError
    error = $logHealthError
}
if ($null -ne $logHealthError) { $summary.errors += $logHealthError }
try {
    $operations = Get-Content -LiteralPath (Join-Path $runtimeHome 'management-operations.json') -Raw | ConvertFrom-Json
    foreach ($entry in $operations.PSObject.Properties) {
        $op = $entry.Value.operation
        if ($op.operation -notin @('start', 'stop', 'pair', 'import', 'enable', 'disable', 'uninstall') -or $op.status -notin @('running', 'succeeded', 'failed', 'reconciling')) { throw 'invalid-operation' }
        $key = '{0}:{1}' -f $op.operation, $op.status
        $summary.operationCounts[$key] = 1 + [int]$summary.operationCounts[$key]
    }
} catch { $summary.errors += 'operation-store-unreadable' }
try {
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    foreach ($proc in (Get-CimInstance Win32_Process)) {
        if ($proc.ProcessId -eq $PID) { continue }
        $related = $proc.Name -like 'AID Work Runtime*' -or ($proc.Name -in @('node.exe', 'powershell.exe') -and $proc.CommandLine -match 'aidwork-tool-runtime|agent-tool-runtime|managed-entry\.js|boss-resume-assistant|BossPhysicalInput|win-click\.ps1|cv-wheel\.ps1')
        if (-not $related) { continue }
        $owner = Invoke-CimMethod -InputObject $proc -MethodName GetOwnerSid
        if ($owner.ReturnValue -ne 0) { throw 'process-owner-unavailable' }
        if ($owner.Sid -ne $currentSid) { continue }
        $summary.relatedProcesses += @{ name = $proc.Name; pid = $proc.ProcessId; parentPid = $proc.ParentProcessId }
    }
} catch { $summary.errors += 'process-inventory-unavailable' }
$output = Join-Path ([Environment]::GetFolderPath('Desktop')) ('Runtime诊断摘要-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
$summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $output -Encoding UTF8
Write-Output ('诊断摘要已生成：' + $output)
Write-Output ('实际采集 home：' + $runtimeHome + '（来源 ' + $summary.homeSource + '）')
if ($null -ne $summary.logHealth -and $null -ne $summary.logHealth.fileWriteFailures -and $summary.logHealth.fileWriteFailures -gt 0) {
    $degradedNote = ''
    if ($summary.logHealth.degraded) {
        $degradedNote = '（健康记录已降级存于 home 根——logs 目录异常: ' + $summary.logHealth.degradedReason + '）'
    }
    Write-Output ('注意：日志文件写失败累计 ' + $summary.logHealth.fileWriteFailures + ' 次，最近原因: ' + $summary.logHealth.lastFileWriteError + '——文件日志可能不完整' + $degradedNote)
}
if ($null -ne $summary.logHealth -and $null -ne $summary.logHealth.error) {
    Write-Output ('注意：日志健康记录不可用（' + $summary.logHealth.error + '）——日志目录探测: ' + $(if ($null -ne $summary.logHealth.logDirError) { $summary.logHealth.logDirError } else { '正常' }) + '；已发生的日志写失败可能未被记录，请检查磁盘空间与目录权限')
}
Write-Output '摘要只含设备编号、服务域名、文件计数/摘要、日志健康与进程编号；不包含凭证或任务内容。未停止进程、修改配对或删除运行记录。'
