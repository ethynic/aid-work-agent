param(
    [string]$TempRoot = [IO.Path]::GetTempPath(),
    [string]$OutputPath
)
$ErrorActionPreference = 'Stop'
$uuid = '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
$hexId = '^[a-fA-F0-9]{32,64}$'
function Id($value, $pattern) { if ($value -is [string] -and $value -cmatch $pattern) { return $value }; return $null }
function Enum($value, $allowed) { if ($value -is [string] -and $allowed -ccontains $value) { return $value }; return $null }
function Number($value) { $parsed = 0L; if ($null -ne $value -and [long]::TryParse([string]$value, [ref]$parsed)) { return $parsed }; return $null }
function Time($value) { try { return [DateTimeOffset]::Parse([string]$value, [Globalization.CultureInfo]::InvariantCulture).UtcDateTime.ToString('o') } catch { return $null } }
function PagePath($value) { try { $uri = [uri]$value; if ($uri.Scheme -eq 'https' -and $uri.Host -in @('zhipin.com','www.zhipin.com') -and $uri.AbsolutePath -in @('/web/chat/index','/web/chat/recommend')) { return $uri.AbsolutePath } } catch {}; return $null }
function IsLink($item) { return (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) }
function SourceState($path) {
    if (-not (Test-Path -LiteralPath $path)) { return 'missing' }
    $item = Get-Item -LiteralPath $path -Force
    if (IsLink $item) { return 'reparse_point_skipped' }
    if (-not $item.PSIsContainer) { return 'not_directory' }
    return 'present'
}
if (-not $OutputPath) { $OutputPath = Join-Path $PSScriptRoot ('boss-widget-evidence-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,6) + '.json') }
$start = [DateTimeOffset]::Parse('2026-10-30T02:40:00Z')
$end = [DateTimeOffset]::Parse('2026-10-30T03:20:00Z')
$bindings = @(); $observations = @(); $issues = @()
$sources = [ordered]@{}
foreach ($kind in @('targets','visual')) {
    $leaf = if ($kind -eq 'targets') { 'aid-boss-targets' } else { 'aid-boss-visual' }
    $root = Join-Path $TempRoot $leaf
    try { $state = SourceState $root } catch { $state = 'unreadable' }
    $counts = [ordered]@{ state=$state; files_seen=0; parsed=0; outside_incident_window=0; invalid_capture_time=0; errors=0; reparse_points_skipped=0 }
    $sources[$kind] = $counts
    if ($state -ne 'present') { continue }
    try { $items = @(Get-ChildItem -LiteralPath $root -Force) } catch { $counts.state='unreadable'; continue }
    foreach ($item in $items) {
        if ($kind -eq 'targets' -and ($item.PSIsContainer -or $item.Extension -ne '.json' -or $item.BaseName -notmatch $uuid)) { continue }
        if ($kind -eq 'visual' -and (-not $item.PSIsContainer -or $item.Name -notmatch $uuid)) { continue }
        if (IsLink $item) { $counts.reparse_points_skipped++; continue }
        $file = $item
        if ($kind -eq 'visual') {
            try {
                $recordPath = Join-Path $item.FullName 'record.json'
                if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) { continue }
                $file = Get-Item -LiteralPath $recordPath -Force
                if (IsLink $file) { $counts.reparse_points_skipped++; continue }
            } catch { $counts.errors++; continue }
        }
        $counts.files_seen++
        try {
            $record = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8) | ConvertFrom-Json
            if ($null -eq $record -or $record -is [array] -or $record -isnot [pscustomobject]) { throw 'Invalid record shape' }
            $counts.parsed++
            if ($kind -eq 'targets') {
                $bindings += [ordered]@{
                    binding_ref=(Id $item.BaseName $uuid); modified_at_utc=$file.LastWriteTimeUtc.ToString('o')
                    target_id=(Id $record.target_id $hexId); cdp_port=(Number $record.cdp_port)
                    restore_pending=($record.restore_pending -eq $true); page_path=(PagePath $record.page_context.entry_url)
                    browser_instance=(Id $record.page_context.browser_instance '^[a-fA-F0-9]{64}$')
                }
                continue
            }
            $captured = Time $record.captured_at
            if (-not $captured) { $counts.invalid_capture_time++; continue }
            $capturedTime = [DateTimeOffset]::Parse($captured)
            if ($capturedTime -lt $start -or $capturedTime -gt $end) { $counts.outside_incident_window++; continue }
            $window = $record.window_observation.window
            $diagnosis = $record.response.body.diagnosis
            $observations += [ordered]@{
                request_id=(Id $record.request_id $uuid); captured_at_utc=$captured; modified_at_utc=$file.LastWriteTimeUtc.ToString('o')
                browser_target_ref=(Id $record.browser_target_ref $uuid); target_id=(Id $record.target_id $hexId)
                page_path=(PagePath $record.url); observation_mode=(Enum $record.observation_mode @('page','window'))
                geometry_epoch=(Id $record.geometry_epoch $uuid); operation=(Id $record.context.operation.name '^boss_[a-z][a-z0-9_]{0,99}$')
                purpose=(Enum $record.context.purpose @('diagnose','verify')); input_status=(Enum $record.context.execution.input_status @('not_started','attempted','unknown'))
                stage=(Enum $record.context.execution.stage @('prepared','input','postcheck'))
                http_status=(Number $record.response.status); visual_state=(Enum $record.response.body.state @('pending','completed','unknown'))
                obstruction=(Enum $diagnosis.obstruction @('present','absent','unknown'))
                goal_state=(Enum $diagnosis.goal_state @('satisfied','not_satisfied','inconclusive'))
                action_kind=(Enum $diagnosis.proposed_action.kind @('dismiss','press_escape'))
                window=[ordered]@{ hwnd=(Id $window.hwnd '^\d{1,20}$'); pid=(Number $window.pid); process_started_at_utc=(Time $window.process_started_at)
                    left=(Number $window.left); top=(Number $window.top); width=(Number $window.width); height=(Number $window.height) }
            }
        } catch { $counts.errors++; $issues += [ordered]@{source=$kind; record_id=$item.BaseName; reason='read_or_parse_failed'} }
    }
}
$report = [ordered]@{
    schema_version=1; collected_at_utc=[DateTime]::UtcNow.ToString('o'); incident_window_utc=[ordered]@{start=$start.UtcDateTime.ToString('o');end=$end.UtcDateTime.ToString('o')}
    scope='Only BOSS target bindings and incident-window visual metadata from this Windows user temporary folder'
    sources=$sources; bindings=$bindings; observations=$observations; issues=$issues
    limits=@('Historical cache may be missing or changed; current bindings are not a historical event log.','Missing records do not prove that an event did not happen.','No screenshots, messages, candidate names, cookies, device credentials, free-text diagnoses, account refs or local source paths are included.')
}
$json = $report | ConvertTo-Json -Depth 12
$output = [IO.Path]::GetFullPath($OutputPath)
$stream = [IO.File]::Open($output, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try { $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($json); $stream.Write($bytes,0,$bytes.Length) } finally { $stream.Dispose() }
Write-Host ('Created report: ' + $output)
Write-Host ('Bindings: ' + $bindings.Count + '; incident observations: ' + $observations.Count)
Write-Host 'Sources were not changed. Please share only the generated boss-widget-evidence JSON.'
