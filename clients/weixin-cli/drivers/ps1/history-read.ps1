param()
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot '_common.ps1')
. (Join-Path $PSScriptRoot 'native-messages.ps1')

function Test-WeixinHistoryCutoff($Rows, [int]$Days) {
    if ($Days -le 0) { return $false }
    $today = (Get-Date).Date
    foreach ($row in $Rows) {
        if ($row.kind -ne 'time' -or $row.text -notmatch '(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日') { continue }
        $year = if ($Matches[1]) { [int]$Matches[1] } else { $today.Year }
        $month = [int]$Matches[2]
        $day = [int]$Matches[3]
        if ($month -lt 1 -or $month -gt 12 -or $day -lt 1 -or $day -gt [DateTime]::DaysInMonth($year, $month)) { continue }
        if (-not $Matches[1] -and ($month -gt $today.Month -or ($month -eq $today.Month -and $day -gt $today.Day))) { $year-- }
        if ($day -gt [DateTime]::DaysInMonth($year, $month)) { continue }
        return ($today - [DateTime]::new($year, $month, $day)).Days -gt $Days
    }
    return $false
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WeixinCli.NativeObserve' -Body {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $session = Get-WeixinCurrentNativeSession -TargetName $request.target_name
    $chat = Get-WeixinNativeTitleInfo $session
    if (-not (Invoke-WeixinActivation -Hwnd $session.Handle)) {
        Throw-DriverError 'UI_CHANGED' '无法将微信主窗口切换到前台'
    }

    $viewport = (Get-WeixinNativeRows $session).viewport
    Send-WeixinNativeMessageWheel $session $viewport -120 40
    $pages = New-Object 'Collections.Generic.List[object]'
    $last = $null
    $notches = [int][Math]::Max(1, [Math]::Min(4, [Math]::Floor(($viewport.bottom - $viewport.top) / 170)))
    $scrolledUp = $false
    try {
        for ($page = 1; $page -le $request.max_pages; $page++) {
            $capture = Get-WeixinNativeMessageSnapshot -Session $session
            $rows = @($capture.rows)
            # Only stop on an identical viewport, including which visible rows are clipped.
            $signature = ConvertTo-Json -InputObject $rows -Depth 6 -Compress
            if ($last -ceq $signature) { break }
            if ($pages.Count -gt 0 -and $rows.Count -eq 0) {
                Throw-DriverError 'CONTENT_UNAVAILABLE' '微信历史分页返回空视口，请重试'
            }
            $last = $signature
            $pages.Add($rows)
            if ($rows.Count -eq 0 -or (Test-WeixinHistoryCutoff $rows $request.since_days) -or $page -eq $request.max_pages) { break }
            $scrolledUp = $true
            Send-WeixinNativeMessageWheel $session $capture.viewport 120 $notches
        }
    } finally {
        # Do not scroll a different conversation when the user switches during capture.
        if ($scrolledUp) {
            $canRestore = $false
            try { Assert-WeixinNativeSelectedSession $session; $canRestore = $true } catch {}
            if ($canRestore -and [WeixinProbeWin32]::GetForegroundWindow().ToInt64() -eq $session.Handle) {
                Send-WeixinNativeMessageWheel $session $viewport -120 40
            }
        }
    }
    return @{title=$chat.title; is_group=[bool]$chat.is_group; pages=$pages.ToArray()}
}
