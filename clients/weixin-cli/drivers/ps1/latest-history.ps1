# Local-only incremental history. Dot-source after native-messages.ps1.

function Get-WeixinLatestProcessStart($Session) {
    return (Get-Process -Id $Session.ProcessId -ErrorAction Stop).StartTime.ToUniversalTime().Ticks.ToString()
}

function Test-WeixinLatestRow($Earlier, $Later) {
    return ($Earlier.runtime_id -and $Earlier.runtime_id -ceq $Later.runtime_id -and
        $Earlier.kind -ceq $Later.kind -and $Earlier.text -ceq $Later.text -and
        ($Earlier.sender -ceq $Later.sender -or $Earlier.sender -ceq 'unknown' -or $Later.sender -ceq 'unknown') -and
        (-not $Earlier.sender_name -or -not $Later.sender_name -or $Earlier.sender_name -ceq $Later.sender_name))
}

function Merge-WeixinLatestRows($Older, $Newer) {
    $overlap = [Math]::Min($Older.Count, $Newer.Count)
    for (; $overlap -gt 0; $overlap--) {
        $same = $true
        for ($i=0; $i -lt $overlap; $i++) {
            if (-not (Test-WeixinLatestRow $Older[$Older.Count-$overlap+$i] $Newer[$i])) { $same=$false; break }
        }
        if ($same) { break }
    }
    if (-not $overlap) { Throw-DriverError 'CONTENT_UNAVAILABLE' '无法核对微信分页连续性' }
    $merged = [Collections.Generic.List[object]]::new()
    for ($i=0; $i -lt $Older.Count-$overlap; $i++) { $merged.Add($Older[$i]) }
    for ($i=0; $i -lt $overlap; $i++) {
        $earlier = $Older[$Older.Count-$overlap+$i]
        $later = $Newer[$i]
        # Keep the complete bubble when a page boundary clips the same native row.
        $merged.Add($(if ($later.clipped -and -not $earlier.clipped) { $earlier } else { $later }))
    }
    for ($i=$overlap; $i -lt $Newer.Count; $i++) { $merged.Add($Newer[$i]) }
    return ,$merged.ToArray()
}

function ConvertTo-WeixinLatestMinute([string]$Label, [DateTime]$ObservedAt) {
    if ($Label -cnotmatch '^(?:(?<relative>今天|昨天|前天)\s*|(?:(?<year>\d{4})年)?(?<month>\d{1,2})月(?<day>\d{1,2})日\s*)?(?<period>上午|下午|晚上|凌晨|中午)?\s*(?<hour>[01]?\d|2[0-3]):(?<minute>[0-5]\d)$') { return }
    $parts = $Matches
    $date = $ObservedAt.Date
    if ($parts.relative -ceq '昨天') { $date=$date.AddDays(-1) }
    if ($parts.relative -ceq '前天') { $date=$date.AddDays(-2) }
    if ($parts.month) {
        $year = if ($parts.year) { [int]$parts.year } else { $ObservedAt.Year }
        try { $date=[DateTime]::new($year,[int]$parts.month,[int]$parts.day) } catch { return }
    }
    $hour = [int]$parts.hour
    if ($parts.period -cin @('下午','晚上','中午') -and $hour -lt 12) { $hour+=12 }
    if ($parts.period -cin @('上午','凌晨') -and $hour -eq 12) { $hour=0 }
    $minute = $date.AddHours($hour).AddMinutes([int]$parts.minute)
    if ($minute -gt $ObservedAt) { return }
    return $minute
}

function Get-WeixinLatestAnchorContext($Rows, [int]$End, [DateTime]$ObservedAt) {
    $offset = 0
    for ($i=$End; $i -ge 0; $i--) {
        $row = $Rows[$i]
        if ($row.kind -cne 'time') { $offset++; continue }
        if ($row.clipped) { return }
        $minute = ConvertTo-WeixinLatestMinute $row.text $ObservedAt
        if ($null -eq $minute) { return }
        # Unknown later labels add no date evidence. A contradictory interpretable
        # label means the current-day candidate cannot identify this time block.
        $previous = $minute
        for ($j=$i+1; $j -lt $Rows.Count; $j++) {
            if ($Rows[$j].kind -cne 'time') { continue }
            $next = ConvertTo-WeixinLatestMinute $Rows[$j].text $ObservedAt
            if ($null -eq $next) { continue }
            if ($next -lt $previous) { return }
            $previous = $next
        }
        return @{minute=$minute.ToString('yyyy-MM-ddTHH:mm');offset=$offset}
    }
}

function Find-WeixinLatestAnchor($Rows, $Anchor, $Context, [DateTime]$ObservedAt=(Get-Date), [switch]$RequireContext) {
    if ($Anchor.Count -eq 0) { return -1 }
    $messages = @()
    for ($i=0; $i -lt $Rows.Count; $i++) {
        if ($Rows[$i].kind -cne 'time') { $messages += @{row=$Rows[$i];index=$i} }
    }
    $matches = @()
    for ($i=0; $i -le $messages.Count-$Anchor.Count; $i++) {
        $same = $true
        for ($j=0; $j -lt $Anchor.Count; $j++) {
            $current = $messages[$i+$j].row
            $saved = $Anchor[$j]
            if ($current.clipped -or $saved.kind -cne $current.kind -or $saved.text -cne $current.text -or
                $saved.sender -cne $current.sender -or ([string]$saved.sender_name) -cne ([string]$current.sender_name)) {
                $same=$false; break
            }
        }
        if ($same) {
            $end = $messages[$i+$Anchor.Count-1].index
            if ($RequireContext) {
                $currentContext = Get-WeixinLatestAnchorContext $Rows $end $ObservedAt
                if (-not $Context -or -not $currentContext -or $Context.minute -cne $currentContext.minute -or
                    $Context.offset -ne $currentContext.offset) { continue }
            }
            $matches += $end
        }
    }
    if ($matches.Count -gt 1) { Throw-DriverError 'CONTENT_UNAVAILABLE' '微信末尾锚点不唯一' }
    if ($matches.Count -eq 1) { return [int]$matches[0] }
    return -1
}

function Move-WeixinLatestBottom($Session) {
    Assert-WeixinNativeSelectedSession $Session
    $view = Get-WeixinNativeRows $Session
    # A user may start many pages above latest. Continue until scrolling actually
    # stops, instead of assuming one fixed wheel batch reached the end.
    for ($attempt=0; $attempt -lt 100; $attempt++) {
        Send-WeixinNativeMessageWheel $Session $view.viewport -120 40
        $next = Get-WeixinNativeRows $Session
        if ((ConvertTo-Json -InputObject $view -Depth 6 -Compress) -ceq (ConvertTo-Json -InputObject $next -Depth 6 -Compress)) {
            if ($next.rows.Count -eq 0) {
                $list = Get-WeixinNativeList $Session 'chat_message_list' 'mmui::RecyclerListView'
                if ($list.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition).Count -ne 0) {
                    Throw-DriverError 'CONTENT_UNAVAILABLE' '无法核对空微信消息列表'
                }
            } elseif ($next.rows[-1].clipped) {
                Throw-DriverError 'CONTENT_UNAVAILABLE' '微信末条消息没有完整显示'
            }
            Assert-WeixinNativeSelectedSession $Session
            return $next
        }
        $view = $next
    }
    Throw-DriverError 'CONTENT_UNAVAILABLE' '无法到达微信消息列表末尾'
}

function Invoke-WeixinLatestCapture($Session, $Request) {
    $started = Get-WeixinLatestProcessStart $Session
    if ($started -cne $Request.process_started_at) { Throw-DriverError 'UI_CHANGED' '微信进程已变化' }
    if (-not (Invoke-WeixinActivation -Hwnd $Session.Handle)) { Throw-DriverError 'UI_CHANGED' '微信窗口无法激活' }
    $bottom = Move-WeixinLatestBottom $Session
    $rows = @()
    $last = $null
    $anchorEnd = -1
    $pages = 0
    $reachedTop = $false
    $scrolledUp = $false
    $maxPages = if ($Request.has_cache) { 100 } else { 10 }
    $requireFullPrefix = $Request.complete_history -and $Request.cached_count -eq @($Request.anchor).Count
    $observedAt = Get-Date
    try {
        for ($page=1; $page -le $maxPages; $page++) {
            $capture = Get-WeixinNativeMessageSnapshot -Session $Session
            $current = @($capture.rows)
            # Qt may recycle a message RuntimeId on a later page. Within one
            # captured viewport each node still has to identify exactly one row.
            $ids = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            foreach ($row in $current) {
                if (-not $row.runtime_id -or -not $ids.Add($row.runtime_id)) {
                    Throw-DriverError 'CONTENT_UNAVAILABLE' '微信当前视口消息身份不唯一'
                }
            }
            if ($page -eq 1) {
                if ($current.Count -ne $bottom.rows.Count) { Throw-DriverError 'CONTENT_UNAVAILABLE' '微信末尾视口已变化' }
                for ($i=0; $i -lt $current.Count; $i++) {
                    if ($current[$i].runtime_id -cne $bottom.rows[$i].runtime_id -or
                        $current[$i].text -cne $bottom.rows[$i].text -or $current[$i].clipped -ne $bottom.rows[$i].clipped) {
                        Throw-DriverError 'CONTENT_UNAVAILABLE' '微信末尾视口已变化'
                    }
                }
            }
            $signature = ConvertTo-Json -InputObject $current -Depth 6 -Compress
            if ($signature -ceq $last) { $reachedTop=$true; break }
            if ($pages -gt 0 -and $current.Count -eq 0) { Throw-DriverError 'CONTENT_UNAVAILABLE' '微信分页返回空视口' }
            $rows = if ($pages -eq 0) { ,$current } else { Merge-WeixinLatestRows $current $rows }
            $pages++
            $last = $signature
            $anchorEnd = Find-WeixinLatestAnchor $rows @($Request.anchor) $Request.anchor_context $observedAt -RequireContext:(-not $requireFullPrefix)
            if (($anchorEnd -ge 0 -and -not $requireFullPrefix) -or $current.Count -eq 0) {
                $reachedTop=$current.Count -eq 0; break
            }
            if ($page -eq $maxPages) { break }
            $notches = [int][Math]::Max(1, [Math]::Min(4, [Math]::Floor(($capture.viewport.bottom-$capture.viewport.top)/170)))
            $scrolledUp = $true
            Send-WeixinNativeMessageWheel $Session $capture.viewport 120 $notches
        }
        if ($Request.has_cache -and $anchorEnd -lt 0 -and -not ($Request.previous_empty -and $reachedTop)) {
            Throw-DriverError 'CONTENT_UNAVAILABLE' '无法找到本机副本末尾锚点'
        }
        if ($Request.has_cache -and @($Request.anchor).Count -gt 0 -and $requireFullPrefix) {
            $prefix = @($rows | Select-Object -First ($anchorEnd+1) | Where-Object { $_.kind -cne 'time' })
            if (-not $Request.complete_history -or -not $reachedTop -or
                $Request.cached_count -ne @($Request.anchor).Count -or $prefix.Count -ne @($Request.anchor).Count) {
                Throw-DriverError 'CONTENT_UNAVAILABLE' '无法核对短会话的完整历史前缀'
            }
        }
        $completeHistory = $reachedTop -and ($rows.Count -eq 0 -or -not $rows[0].clipped)
        # Only the outer oldest boundary may be clipped. It is excluded from the
        # copy, and can never become an incremental watermark.
        if ($rows.Count -gt 0 -and $rows[0].clipped -and $anchorEnd -lt 0) {
            $rows = @($rows | Select-Object -Skip 1)
        }
        if (@($rows | Select-Object -Skip ($anchorEnd+1) | Where-Object { $_.clipped }).Count -gt 0) {
            Throw-DriverError 'CONTENT_UNAVAILABLE' '微信消息气泡没有完整证据'
        }
    } finally {
        if ($scrolledUp) {
            # Restoration rechecks the selected session itself, and failures
            # prevent a successful capture from being persisted by the caller.
            $null = Move-WeixinLatestBottom $Session
        }
    }
    Assert-WeixinNativeSelectedSession $Session
    if ((Get-WeixinLatestProcessStart $Session) -cne $started) { Throw-DriverError 'UI_CHANGED' '微信进程已变化' }
    $lastMessage = $rows.Count-1
    while ($lastMessage -ge 0 -and $rows[$lastMessage].kind -ceq 'time') { $lastMessage-- }
    $anchorContext = Get-WeixinLatestAnchorContext $rows $lastMessage $observedAt
    return @{
        title=$Session.Title;session_identity=$Session.Identity;process_started_at=$started
        rows=@($rows);anchor_end=$anchorEnd;pages_scanned=$pages;empty_proven=($bottom.rows.Count -eq 0)
        complete_history=$completeHistory
        anchor_context=$anchorContext
    }
}
