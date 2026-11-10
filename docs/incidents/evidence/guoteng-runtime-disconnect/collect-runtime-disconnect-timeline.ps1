[CmdletBinding()]
param(
    [Guid]$ExpectedDeviceId = '6bfc3e1a-c8f1-41e7-906c-d86a74266eeb'
)

$ErrorActionPreference = 'Stop'
$appData = [IO.Path]::GetFullPath($env:APPDATA)
$userData = Join-Path $appData 'aidwork-runtime-app-acceptance'
$metadata = Join-Path $userData 'installation-v2'
$windows = @(
    [pscustomobject]@{ Name = 'nov05-last-seen'; From = [datetimeoffset]'2026-11-05T15:45:00+08:00'; To = [datetimeoffset]'2026-11-05T18:00:00+08:00' }
    [pscustomobject]@{ Name = 'nov09-before-pair'; From = [datetimeoffset]'2026-11-09T13:45:00+08:00'; To = [datetimeoffset]'2026-11-09T16:40:00+08:00' }
)

function Get-SafeItem([string]$Path) {
    if (-not $Path.StartsWith($appData + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'unexpected-path' }
    $item = Get-Item -LiteralPath $Path -Force
    for ($ancestor = $item; $ancestor.FullName -ne $appData; ) {
        if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'linked-path' }
        $ancestor = Get-Item -LiteralPath (Split-Path -Parent $ancestor.FullName) -Force
    }
    return $item
}

function Get-ChildHomes([string]$Path) {
    try { $folder = Get-SafeItem $Path } catch { return }
    Get-ChildItem -LiteralPath $folder.FullName -Directory -Force |
        Where-Object { $_.Name -match '^[a-f0-9-]{36}$' -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) }
}

function Read-Timeline([string]$Path, [bool]$Desktop = $false) {
    $result = [ordered]@{ source = Split-Path -Leaf $Path; status = 'missing' }
    if (-not (Test-Path -LiteralPath $Path)) { return $result }
    $reader = $null
    try {
        $item = Get-SafeItem $Path
        $result.lastWriteUtc = $item.LastWriteTimeUtc.ToString('o')
        if ($item.PSIsContainer -or $item.Length -gt 12MB) { $result.status = 'unexpected-file-or-too-large'; return $result }
        $stream = [IO.File]::Open($item.FullName, 'Open', 'Read', ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
        $reader = New-Object IO.StreamReader($stream)
        $counts = @{}
        $samples = New-Object 'Collections.Generic.List[object]'
        $first = $last = $null
        while ($null -ne ($line = $reader.ReadLine())) {
            if ($line -notmatch '^(?:\{"at":")?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z)') { continue }
            $at = [datetimeoffset]::Parse($Matches[1], [Globalization.CultureInfo]::InvariantCulture)
            if (-not $first) { $first = $at.ToString('o') }
            $last = $at.ToString('o')
            $window = $windows | Where-Object { $at -ge $_.From -and $at -lt $_.To } | Select-Object -First 1
            if (-not $window) { continue }
            $detail = $null
            $event = switch -Regex ($line) {
                '"event":"(runtime-login-resume-failed|runtime-host-initialization-failed|api-configuration-loaded|startup-failed|renderer-gone)"' { if ($Desktop) { $Matches[1]; break } }
                ' \[runtime\]\[\w+\] 心跳失败（' { 'heartbeat-failed'; break }
                ' \[runtime\]\[\w+\] 领取任务失败（' { 'claim-failed'; break }
                ' \[runtime\]\[\w+\] 设备 token 已失效或被撤销，主循环停止' { 'device-revoked'; break }
                ' \[runtime\]\[\w+\] \[runtime\] providers:' { 'runtime-providers-initialized'; break }
                ' \[runtime\]\[\w+\] 云端连接中断' { 'cloud-progress-disconnected'; break }
                ' \[runtime\]\[\w+\] 云端连接已恢复' { 'cloud-progress-recovered'; break }
            }
            if (-not $event) { continue }
            if ($event -in @('heartbeat-failed', 'claim-failed')) {
                $detail = switch -Regex ($line) {
                    'HTTP ([1-5]\d\d)\b' { 'http-' + $Matches[1]; break }
                    'ECONNRESET|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|ECONNREFUSED|CERT_HAS_EXPIRED|UNABLE_TO_VERIFY_LEAF_SIGNATURE' { $Matches[0].ToUpperInvariant(); break }
                    '超时|timed out|timeout' { 'timeout'; break }
                    'fetch failed' { 'fetch-failed'; break }
                    default { 'unclassified' }
                }
            }
            $key = "$($window.Name)/$event/$detail"
            $counts[$key] = 1 + [int]$counts[$key]
            if ($counts[$key] -le 3) {
                $samples.Add([pscustomobject]@{ atUtc = $last; window = $window.Name; event = $event; detail = $detail })
            }
        }
        $result.status = 'readable'
        $result.firstTimestampUtc = $first
        $result.lastTimestampUtc = $last
        $result.counts = $counts
        $result.samples = @($samples.ToArray())
    } catch { $result.status = 'unreadable' }
    finally { if ($reader) { $reader.Dispose() } }
    return $result
}

function Read-WindowsEvents([string]$Log, [string]$Provider, [int[]]$Ids) {
    try {
        $events = @(Get-WinEvent -FilterHashtable @{
            LogName = $Log; ProviderName = $Provider; Id = $Ids
            StartTime = $windows[0].From.LocalDateTime; EndTime = $windows[-1].To.LocalDateTime
        })
        $results = @($events | ForEach-Object {
            $app = $null
            $bootType = $null
            $failureCode = $null
            if ($Log -eq 'Application' -or $_.Id -in @(27, 41, 1074)) {
                $data = ([xml]$_.ToXml()).Event.EventData.Data
                $values = @($data | ForEach-Object { if ($_ -is [string]) { $_ } else { $_.'#text' } })
                if ($values -match '(?i)(?:^|[\\/])AID Work (?:Runtime|Agent).*?\.exe\b') { $app = 'aid-runtime-or-agent' }
                elseif ($values -match '(?i)[\\/]resources[\\/]runtime[\\/].*?[\\/]node\.exe\b') { $app = 'packaged-runtime-node' }
                elseif ($values -match '(?i)^node\.exe$') { $app = 'node-unattributed' }
                if ($Log -eq 'Application' -and -not $app) { return }
                if ($_.Id -eq 27) {
                    $value = $data | Where-Object { $_.Name -eq 'BootType' } | Select-Object -First 1
                    if ($value.'#text' -match '^(?:0x)?[0-9a-f]{1,8}$') { $bootType = $value.'#text' }
                }
                if ($_.Id -in @(41, 1000)) {
                    $value = $data | Where-Object { $_.Name -in @('BugcheckCode', 'ExceptionCode') } | Select-Object -First 1
                    if ($value.'#text' -match '^(?:0x)?[0-9a-f]{1,10}$') { $failureCode = $value.'#text' }
                }
            }
            [pscustomobject]@{ atUtc = $_.TimeCreated.ToUniversalTime().ToString('o'); id = $_.Id; app = $app; bootType = $bootType; failureCode = $failureCode }
        })
        return @{ provider = $Provider; status = 'readable'; events = $results }
    } catch {
        $status = if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { 'no-matching-events' } else { 'unavailable' }
        return @{ provider = $Provider; status = $status; events = @() }
    }
}

function Read-WindowsLogCoverage([string]$Name) {
    try {
        $oldest = Get-WinEvent -LogName $Name -Oldest -MaxEvents 1
        if (-not $oldest) { return @{ name = $Name; status = 'no-events' } }
        return @{ name = $Name; status = 'readable'; oldestUtc = $oldest.TimeCreated.ToUniversalTime().ToString('o') }
    } catch { return @{ name = $Name; status = 'unavailable' } }
}

$homes = @((Get-ChildHomes (Join-Path $userData 'connections')))
foreach ($generation in Get-ChildHomes (Join-Path $metadata 'generations')) {
    $homes += @(Get-ChildHomes (Join-Path $generation.FullName 'connections'))
}
foreach ($candidate in @((Join-Path $userData 'rt'), (Join-Path $appData 'aidwork-tool-runtime-acceptance'), (Join-Path $appData 'aidwork-tool-runtime'))) {
    if (Test-Path -LiteralPath $candidate -PathType Container) {
        try { $homes += Get-SafeItem $candidate } catch { continue }
    }
}
$homeResults = @($homes | ForEach-Object {
    $connectionHome = $_
    $matches = $null
    try {
        $file = Get-SafeItem (Join-Path $connectionHome.FullName 'config.json')
        if ($file.Length -le 65536) {
            $config = [IO.File]::ReadAllText($file.FullName) | ConvertFrom-Json
            if ($config.device_id) { $matches = $config.device_id -eq $ExpectedDeviceId.ToString() }
        }
    } catch { }
    [pscustomobject]@{
        homeCreatedUtc = $connectionHome.CreationTimeUtc.ToString('o')
        matchesOriginalDevice = $matches
        logs = @('runtime.log', 'runtime.old.log', 'runtime.log.old.log' | ForEach-Object {
            Read-Timeline (Join-Path (Join-Path $connectionHome.FullName 'logs') $_)
        })
    }
})

[ordered]@{
    profile = 'acceptance'
    collectedUtc = [datetime]::UtcNow.ToString('o')
    windows = $windows
    desktop = @('desktop-diagnostics.jsonl', 'desktop-diagnostics.old.jsonl' | ForEach-Object {
        Read-Timeline (Join-Path (Join-Path $userData 'logs') $_) $true
    })
    homes = $homeResults
    windowsLogCoverage = @(Read-WindowsLogCoverage 'System'; Read-WindowsLogCoverage 'Application')
    windowsEvents = @(
        Read-WindowsEvents 'System' 'Microsoft-Windows-Kernel-Power' @(41, 42, 107)
        Read-WindowsEvents 'System' 'Microsoft-Windows-Kernel-Boot' @(27)
        Read-WindowsEvents 'System' 'Microsoft-Windows-Kernel-General' @(12, 13)
        Read-WindowsEvents 'System' 'EventLog' @(6005, 6006, 6008)
        Read-WindowsEvents 'System' 'User32' @(1074)
        Read-WindowsEvents 'System' 'Microsoft-Windows-Power-Troubleshooter' @(1)
        Read-WindowsEvents 'Application' 'Application Error' @(1000)
        Read-WindowsEvents 'Application' 'Application Hang' @(1002)
        Read-WindowsEvents 'Application' 'Windows Error Reporting' @(1001)
    )
} | ConvertTo-Json -Depth 8
