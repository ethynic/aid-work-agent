$ErrorActionPreference = 'Stop'
$runtimeHome = Join-Path $env:APPDATA 'aidwork-tool-runtime-acceptance'
$summary = [ordered]@{ schemaVersion = 1; collectedAtUtc = [DateTime]::UtcNow.ToString('o'); homeExists = (Test-Path -LiteralPath $runtimeHome); deviceId = $null; server = $null; bootTimeUtc = $null; runningMarker = $null; stateDirectories = @{}; operationCounts = @{}; relatedProcesses = @(); processInventoryScope = 'Known Runtime paths only; absence does not prove descendants stopped'; errors = @() }
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
Write-Output '摘要只含设备编号、服务域名、文件计数/摘要与进程编号；不包含凭证或任务内容。未停止进程、修改配对或删除运行记录。'
