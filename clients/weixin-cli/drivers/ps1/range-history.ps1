# Dot-source after native session, search and latest-history helpers.

function ConvertTo-WeixinRangeMinute([string]$Label, [DateTime]$ObservedAt, [TimeZoneInfo]$Zone) {
    if ($Label -cmatch '^(?:星期|周)(?<day>[一二三四五六日天])\s*(?<time>.+)$') {
        $weekday = if ($Matches.day -ceq '天') { '日' } else { $Matches.day }
        $daysAgo = (7 + [int]$ObservedAt.DayOfWeek - '日一二三四五六'.IndexOf($weekday)) % 7
        if ($daysAgo -eq 0) { return }
        $date = $ObservedAt.Date.AddDays(-$daysAgo)
        $Label = $date.ToString('yyyy年M月d日', [Globalization.CultureInfo]::InvariantCulture) + ' ' + $Matches.time
    } elseif ($Label -cmatch '^(?<month>\d{1,2})月(?<day>\d{1,2})日') {
        # New year: an unlabeled previous December is the only possible day in the window.
        if (([int]$Matches.month * 100 + [int]$Matches.day) -gt ($ObservedAt.Month * 100 + $ObservedAt.Day)) {
            $Label = [string]($ObservedAt.Year - 1) + '年' + $Label
        }
    }
    $minute = ConvertTo-WeixinLatestMinute $Label $ObservedAt
    if ($null -eq $minute -or $Zone.IsAmbiguousTime($minute) -or $Zone.IsInvalidTime($minute)) { return }
    $local = [DateTime]::SpecifyKind($minute, [DateTimeKind]::Unspecified)
    return [DateTimeOffset]::new($local, $Zone.GetUtcOffset($local))
}

function Select-WeixinRangeText($Capture, [DateTimeOffset]$Start, [DateTimeOffset]$End,
                                [DateTime]$ObservedAt, [TimeZoneInfo]$Zone=[TimeZoneInfo]::Local) {
    $messages = [Collections.Generic.List[string]]::new()
    $pending = [Collections.Generic.List[object]]::new()
    $startCovered = $Capture.complete_history -or $Capture.empty_proven
    $partial = $false
    $lower = $null
    $firstInBlock = $false
    $observation = [DateTimeOffset]::new(
        [DateTime]::SpecifyKind($ObservedAt, [DateTimeKind]::Unspecified), $Zone.GetUtcOffset($ObservedAt)
    )
    $latestBound = $observation.AddTicks(1)
    # A separator groups rows, with minute precision for the immediately following
    # message. Other messages in the block are bounded by the next known separator.
    foreach ($row in @($Capture.rows) + @(@{kind='end'})) {
        if ($row.kind -cin @('time', 'end')) {
            $anchor = if ($row.kind -ceq 'time' -and -not $row.clipped) {
                ConvertTo-WeixinRangeMinute ([string]$row.text) $ObservedAt $Zone
            }
            if ($null -ne $anchor -or $row.kind -ceq 'end') {
                if ($null -ne $anchor -and $null -ne $lower -and $anchor -lt $lower) {
                    Throw-DriverError 'CONTENT_UNAVAILABLE' '微信消息时间顺序无法核实'
                }
                $upper = if ($null -ne $anchor) { $anchor.AddMinutes(1) } else { $latestBound }
                if ($upper -le $Start -and $null -ne $anchor) { $startCovered = $true }
                foreach ($item in $pending) {
                    $limit = if ($item.first) { $item.lower.AddMinutes(1) } else { $upper }
                    if ($limit -le $Start -or ($null -ne $item.lower -and $item.lower -ge $End)) { continue }
                    if ($null -ne $item.lower -and $item.lower -ge $Start -and $limit -le $End -and -not $item.clipped) {
                        $messages.Add($item.text)
                    } else { $partial = $true }
                }
                $pending.Clear()
                if ($null -ne $anchor) { $lower = $anchor }
            }
            # An unparseable separator can carry rows; the preceding known anchor
            # still provides an ordering bound, but cannot identify an adjacent minute.
            $firstInBlock = $null -ne $anchor
            continue
        }
        if ($row.kind -ceq 'text' -and -not [string]::IsNullOrWhiteSpace($row.text)) {
            $pending.Add(@{lower=$lower;first=$firstInBlock;clipped=$row.clipped;text=[string]$row.text})
        }
        $firstInBlock = $false
    }
    $first = $messages.Count
    $characters = 0
    while ($first -gt 0 -and $messages.Count - $first -lt 500) {
        $length = $messages[$first-1].Length
        if ($characters + $length -gt 20000) { break }
        $characters += $length
        $first--
    }
    # A local clock behind the endpoint cannot prove coverage of the requested ending.
    return @{
        messages=$messages.GetRange($first, $messages.Count-$first).ToArray()
        partial=($partial -or -not $startCovered -or $observation -lt $End -or $first -gt 0)
    }
}

function Invoke-WeixinRangeHistory($Request) {
    $format = "yyyy-MM-dd'T'HH:mm:ss.fff'Z'"
    $culture = [Globalization.CultureInfo]::InvariantCulture
    $style = [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
    $start = [DateTimeOffset]::ParseExact([string]$Request.start_at, $format, $culture, $style)
    $end = [DateTimeOffset]::ParseExact([string]$Request.end_at, $format, $culture, $style)
    if ($start -ge $end -or ($end - $start).TotalHours -gt 168) {
        Throw-DriverError 'CONTENT_UNAVAILABLE' '微信总结时段无效'
    }
    $handle = Resolve-WeixinMainWindowHwnd
    $identity = Get-WindowIdentityByHwnd $handle
    if (-not (Test-WeixinMainIdentity $identity $handle)) { Throw-DriverError 'UI_CHANGED' '微信主窗口已变化' }
    $candidate = @{Handle=$handle;ProcessId=$identity.ProcessId;Title=[string]$Request.target_name}
    $session = $null
    # Only a user-requested retry may rely on the caller's deliberate open-group selection.
    if ($Request.allow_open_group -is [bool] -and $Request.allow_open_group) {
        $title = Get-WeixinNativeTitleInfo $candidate
        if ($title -and [string]::Equals($title.title, $candidate.Title, [StringComparison]::Ordinal) -and $title.is_group) {
            $session = Get-WeixinCurrentNativeSession -TargetName $candidate.Title
        }
    }
    if (-not $session) {
        $null = Get-WeixinNativeList $candidate 'session_list' 'mmui::XTableView'
        $session = Open-WeixinNativeSearchTarget $candidate.Title 'group' -VerifyGroupCandidate
    }
    $verified = Get-WeixinNativeTitleInfo $session
    if (-not $verified.is_group -or -not [string]::Equals($verified.title, $candidate.Title, [StringComparison]::Ordinal)) {
        Throw-DriverError 'TARGET_NOT_FOUND' '当前会话未核实为目标微信群聊'
    }
    $session.RequireGroup = $true
    $clockBefore = Get-Date
    $capture = Invoke-WeixinLatestCapture $session @{process_started_at=(Get-WeixinLatestProcessStart $session)}
    $observedAt = $capture.bottom_observed_at
    if ($clockBefore.Date -ne $observedAt.Date -or (Get-Date).Date -ne $observedAt.Date) {
        Throw-DriverError 'UI_CHANGED' '微信本地日期已变化'
    }
    return Select-WeixinRangeText $capture $start $end $observedAt
}
