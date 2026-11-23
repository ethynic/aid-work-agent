param()
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot '_common.ps1')
. (Join-Path $PSScriptRoot 'native-messages.ps1')
. (Join-Path $PSScriptRoot 'latest-history.ps1')

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

function Select-WeixinDailyText($Capture, [string]$BusinessDate, [DateTime]$ObservedAt) {
    $zone = [TimeZoneInfo]::Local
    $beijing = [TimeSpan]::FromHours(8)
    $today = ([DateTimeOffset]::new($ObservedAt)).ToOffset($beijing).ToString('yyyy-MM-dd')
    if ($BusinessDate -cne $today) { Throw-DriverError 'UI_CHANGED' '微信总结日期已变化' }

    $messages = New-Object 'Collections.Generic.List[string]'
    $currentDate = $null
    $previousMinute = $null
    $priorDay = $false
    $unknownDate = $false
    foreach ($row in $Capture.rows) {
        if ($row.kind -ceq 'time') {
            $currentDate = $null
            if ($row.clipped) { continue }
            $label = [string]$row.text
            $isWeekdayLabel = $label -cmatch '^(?:星期|周)(?<day>[一二三四五六日天])\s*(?<time>.+)$'
            if ($isWeekdayLabel) {
                $weekday = if ($Matches.day -ceq '天') { '日' } else { $Matches.day }
                $daysAgo = (7 + [int]$ObservedAt.DayOfWeek - '日一二三四五六'.IndexOf($weekday)) % 7
                if ($daysAgo -eq 0) { continue }
                $date = $ObservedAt.Date.AddDays(-$daysAgo)
                $label = $date.ToString('yyyy年M月d日', [Globalization.CultureInfo]::InvariantCulture) + ' ' + $Matches.time
            }
            $minute = ConvertTo-WeixinLatestMinute $label $ObservedAt
            if ($null -eq $minute -or $zone.IsAmbiguousTime($minute) -or $zone.IsInvalidTime($minute)) { continue }
            $localMinute = [DateTime]::SpecifyKind($minute, [DateTimeKind]::Unspecified)
            $knownMinute = [DateTimeOffset]::new($localMinute, $zone.GetUtcOffset($localMinute)).ToOffset($beijing)
            $knownDate = $knownMinute.ToString('yyyy-MM-dd')
            # The week is not explicit, so a weekday label can only exclude prior text.
            if ($isWeekdayLabel -and $knownDate -ceq $today) { continue }
            if ($null -ne $previousMinute -and $knownMinute -lt $previousMinute) {
                Throw-DriverError 'CONTENT_UNAVAILABLE' '微信消息时间顺序无法核实'
            }
            $previousMinute = $knownMinute
            $currentDate = $knownDate
            if ([string]::CompareOrdinal($currentDate, $today) -lt 0) {
                $priorDay = $true
                # Rows before a verified prior-day anchor cannot be from the requested day.
                $unknownDate = $false
            }
        } elseif ($row.kind -ceq 'text' -and -not [string]::IsNullOrWhiteSpace($row.text)) {
            if ($row.clipped -or $null -eq $currentDate) { $unknownDate = $true }
            elseif ($currentDate -ceq $today) { $messages.Add([string]$row.text) }
        }
    }
    # Stay within the model input bound, keeping the most recent complete messages.
    $first = $messages.Count
    $characters = 0
    while ($first -gt 0 -and $messages.Count - $first -lt 500) {
        $length = $messages[$first-1].Length
        if ($characters + $length -gt 20000) { break }
        $characters += $length
        $first--
    }
    $partial = $unknownDate -or (-not ($priorDay -or $Capture.complete_history -or $Capture.empty_proven)) -or $first -gt 0
    return @{messages=$messages.GetRange($first, $messages.Count-$first).ToArray(); partial=$partial}
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WeixinCli.NativeObserve' -Body {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $session = Get-WeixinCurrentNativeSession -TargetName $request.target_name -ExpectedSessionIdentity $request.session_identity
    if ($request.mode -ceq 'identity') {
        return @{title=$session.Title;session_identity=$session.Identity;process_started_at=(Get-WeixinLatestProcessStart $session)}
    }
    if ($request.mode -ceq 'latest') { return Invoke-WeixinLatestCapture $session $request }
    $chat = Get-WeixinNativeTitleInfo $session
    if ($request.group_only) {
        if (-not $chat.is_group) { Throw-DriverError 'TARGET_NOT_FOUND' '当前会话未核实为微信群聊' }
        $session.RequireGroup = $true
    }
    if ($request.mode -ceq 'daily') {
        $observedAt = Get-Date
        $capture = Invoke-WeixinLatestCapture $session @{
            process_started_at=(Get-WeixinLatestProcessStart $session)
        }
        $finishedAt = Get-Date
        if ($finishedAt.Date -ne $observedAt.Date -or
            ([DateTimeOffset]::new($finishedAt)).ToOffset([TimeSpan]::FromHours(8)).ToString('yyyy-MM-dd') -cne $request.business_date) {
            Throw-DriverError 'UI_CHANGED' '微信本地日期已变化'
        }
        return Select-WeixinDailyText $capture $request.business_date $observedAt
    }
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
