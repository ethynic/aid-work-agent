# Native Weixin message acquisition. Dot-source after _common.ps1.

function Get-WeixinNativeElements([int64]$Handle, $Property, [string]$Value) {
    $root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Handle)
    $condition = [Windows.Automation.PropertyCondition]::new($Property, $Value)
    foreach ($node in $root.FindAll([Windows.Automation.TreeScope]::Descendants, $condition)) { $node }
}

function Get-WeixinNativeTitleInfo($Session) {
    $elements = @(Get-WeixinNativeElements $Session.Handle ([Windows.Automation.AutomationElement]::ClassNameProperty) 'mmui::XTextView' |
        ForEach-Object { $_.Current } | Where-Object { $_.ProcessId -eq $Session.ProcessId -and -not $_.IsOffscreen })
    $title = @($elements | Where-Object {
        ([string]$_.AutomationId).EndsWith('.current_chat_name_label', [StringComparison]::Ordinal)
    })
    if ($title.Count -gt 1) { Throw-DriverError 'TARGET_AMBIGUOUS' '当前微信会话标题不唯一' }
    if ($title.Count -eq 0 -or [string]::IsNullOrWhiteSpace($title[0].Name)) { return }
    $group = @($elements | Where-Object {
        ([string]$_.AutomationId).EndsWith('.current_chat_count_label', [StringComparison]::Ordinal)
    })
    return @{title = [string]$title[0].Name; is_group = $group.Count -eq 1}
}

function Get-WeixinNativeList($Session, [string]$Id, [string]$Class) {
    $matches = @(Get-WeixinNativeElements $Session.Handle ([Windows.Automation.AutomationElement]::AutomationIdProperty) $Id |
        Where-Object { $_.Current.ProcessId -eq $Session.ProcessId -and -not $_.Current.IsOffscreen -and
            $_.Current.ClassName -ceq $Class -and $_.Current.ControlType -eq [Windows.Automation.ControlType]::List })
    if ($matches.Count -ne 1) { Throw-DriverError 'UI_CHANGED' '当前微信原生列表不可用' }
    return $matches[0]
}

function Get-WeixinNativeSessionRowId($Session, $Row) {
    $item = $Row.Current
    if ($item.ProcessId -ne $Session.ProcessId -or $item.ClassName -cne 'mmui::ChatSessionCell' -or
        -not [string]::Equals($item.AutomationId, ('session_item_' + $Session.Title), [StringComparison]::Ordinal) -or
        -not [string]::Equals(($item.Name -split '\r\n|\r|\n', 2)[0], $Session.Title, [StringComparison]::Ordinal)) { return }
    $runtimeId = @($Row.GetRuntimeId())
    if ($runtimeId.Count -eq 0) { return }
    return ('{0}:{1}:{2}' -f $Session.Handle, $Session.ProcessId, ($runtimeId -join '.'))
}

function Get-WeixinNativeSelectedSessionId($Session) {
    $list = Get-WeixinNativeList $Session 'session_list' 'mmui::XTableView'
    $selection = $null
    if (-not $list.TryGetCurrentPattern([Windows.Automation.SelectionPattern]::Pattern, [ref]$selection)) { return }
    $selected = @($selection.Current.GetSelection())
    if ($selected.Count -ne 1) { return }
    $item = $null
    if (-not $selected[0].TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern, [ref]$item) -or
        -not $item.Current.IsSelected) { return }
    return Get-WeixinNativeSessionRowId $Session $selected[0]
}

function Assert-WeixinNativeSelectedSession($Session) {
    $selectedId = Get-WeixinNativeSelectedSessionId $Session
    if (-not $Session.Identity -or $selectedId -cne $Session.Identity) {
        Throw-DriverError 'UI_CHANGED' '当前微信选中会话已变化'
    }
}

function Get-WeixinCurrentNativeSession {
    param([string]$TargetName, [string]$ExpectedNativeTitle, [string]$ExpectedSessionIdentity)
    $handle = Resolve-WeixinMainWindowHwnd
    $identity = Get-WindowIdentityByHwnd $handle
    if (-not (Test-WeixinMainIdentity $identity $handle)) { Throw-DriverError 'UI_CHANGED' '微信主窗口已变化' }
    $title = if ($ExpectedNativeTitle) { $ExpectedNativeTitle } else { $TargetName }
    $session = @{Handle=$handle; ProcessId=$identity.ProcessId; Title=$title}
    $current = Get-WeixinNativeTitleInfo $session
    if (-not [string]::Equals($current.title, $title, [StringComparison]::Ordinal)) {
        Throw-DriverError 'UI_CHANGED' '当前微信会话与目标不一致，请先手动打开目标会话'
    }
    $selectedId = Get-WeixinNativeSelectedSessionId $session
    if (-not $selectedId -or ($ExpectedSessionIdentity -and $selectedId -cne $ExpectedSessionIdentity)) {
        Throw-DriverError 'UI_CHANGED' '当前微信选中会话与目标不一致'
    }
    $session.Identity = $selectedId
    return $session
}

function Get-WeixinNativeRows($Session) {
    $titleInfo = Get-WeixinNativeTitleInfo $Session
    if (-not [string]::Equals($titleInfo.title, $Session.Title, [StringComparison]::Ordinal)) {
        Throw-DriverError 'UI_CHANGED' '当前微信会话与目标不一致'
    }
    $list = Get-WeixinNativeList $Session 'chat_message_list' 'mmui::RecyclerListView'
    $listRect = $list.Current.BoundingRectangle
    if ($listRect.IsEmpty -or $listRect.Width -le 0 -or $listRect.Height -le 0) {
        Throw-DriverError 'UI_CHANGED' '当前微信消息视口不可用'
    }
    $rows = @(foreach ($node in $list.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)) {
        $item = $node.Current
        if ($item.ProcessId -ne $Session.ProcessId -or $item.IsOffscreen) { continue }
        $bounds = $item.BoundingRectangle
        if ($bounds.IsEmpty -or $bounds.Width -le 0 -or $bounds.Height -le 0 -or
            $bounds.Bottom -le $listRect.Top -or $bounds.Top -ge $listRect.Bottom) { continue }
        [ordered]@{
            runtime_id = ($node.GetRuntimeId() -join '.')
            class = [string]$item.ClassName
            text = [string]$item.Name
            left = [int][Math]::Max($bounds.Left, $listRect.Left)
            top = [int][Math]::Max($bounds.Top, $listRect.Top)
            right = [int][Math]::Min($bounds.Right, $listRect.Right)
            bottom = [int][Math]::Min($bounds.Bottom, $listRect.Bottom)
            clipped = $bounds.Top -lt $listRect.Top -or $bounds.Bottom -gt $listRect.Bottom
        }
    } )
    return [ordered]@{
        title=$titleInfo.title; is_group=$titleInfo.is_group
        viewport=[ordered]@{left=[int]$listRect.Left;top=[int]$listRect.Top;right=[int]$listRect.Right;bottom=[int]$listRect.Bottom}
        rows=@($rows | Sort-Object {$_.top})
    }
}

function Get-WeixinNativeBubbleTop($Bitmap, [int]$StartX, [int]$EndX, [int]$StartY, [int]$EndY, [int[]]$Colors, [double]$Scale) {
    $needed = [Math]::Ceiling(10 * $Scale)
    $step = [Math]::Max(1, [int]$Scale)
    $prior = -1
    for ($y=$StartY; $y -lt $EndY; $y+=$step) {
        $run=0
        $hit=$false
        for ($x=$StartX; $x -lt $EndX; $x++) {
            if (($Bitmap.GetPixel($x, $y).ToArgb() -band 0xffffff) -in $Colors) {
                $run++
                if ($run -ge $needed) { $hit=$true; break }
            } else { $run=0 }
        }
        if ($hit -and $prior -ge 0) { return $prior }
        $prior = if ($hit) { $y } else { -1 }
    }
    return -1
}

function Get-WeixinNativeSenderName($Session, $Row, $Viewport, [int]$BubbleTop, [double]$Scale) {
    # An accessible child over the nickname region must belong to this exact row.
    # Virtual message cells without a separate native nickname leave this field unconfirmed.
    try {
        $point = [Windows.Point]::new(($Viewport.left + 75 * $Scale), ($BubbleTop - 7 * $Scale))
        if ($point.Y -lt $Row.top) { return }
        $node = [Windows.Automation.AutomationElement]::FromPoint($point)
        $current = $node.Current
        if ($current.ProcessId -ne $Session.ProcessId -or $current.IsOffscreen -or
            $current.ControlType -ne [Windows.Automation.ControlType]::Text -or
            [string]::IsNullOrWhiteSpace($current.Name) -or $current.Name -ceq $Row.text -or
            $current.BoundingRectangle.Top -lt $Row.top -or $current.BoundingRectangle.Bottom -gt $BubbleTop) { return }
        $parent = [Windows.Automation.TreeWalker]::RawViewWalker.GetParent($node)
        if (($parent.GetRuntimeId() -join '.') -ceq $Row.runtime_id) { return [string]$current.Name }
    } catch { return }
}

function Get-WeixinNativeMessageSnapshot {
    param($Session, [switch]$IncludeEvidence)
    $identity = Get-WindowIdentityByHwnd $Session.Handle
    if (-not (Test-WeixinMainIdentity $identity $Session.Handle) -or $identity.ProcessId -ne $Session.ProcessId) {
        Throw-DriverError 'UI_CHANGED' '微信主窗口已变化'
    }
    Assert-WeixinNativeSelectedSession $Session
    $dpi = [WeixinProbeWin32]::GetDpiForWindow([IntPtr]$Session.Handle)
    if (-not $dpi) { Throw-DriverError 'UI_CHANGED' '微信消息像素比例不可用' }
    $scale = $dpi / 96.0
    $first = Get-WeixinNativeRows $Session
    $snapshot = Get-WeixinWindowSnapshot -Hwnd $Session.Handle
    $last = Get-WeixinNativeRows $Session
    if (-not [string]::Equals((ConvertTo-Json -InputObject $first -Compress -Depth 5),
        (ConvertTo-Json -InputObject $last -Compress -Depth 5), [StringComparison]::Ordinal)) {
        Throw-DriverError 'UI_CHANGED' '采集期间微信消息视口已变化'
    }
    $currentRect = New-Object WeixinProbeWin32+RECT
    if (-not [WeixinProbeWin32]::GetWindowRect([IntPtr]$Session.Handle, [ref]$currentRect) -or
        -not $currentRect.Equals($snapshot.WindowRect)) {
        Throw-DriverError 'UI_CHANGED' '采集期间微信窗口已移动'
    }
    $view = $first.viewport
    if ($view.left -lt $currentRect.Left -or $view.top -lt $currentRect.Top -or $view.right -gt $currentRect.Right -or
        $view.bottom -gt $currentRect.Bottom -or $view.right -le $view.left -or $view.bottom -le $view.top) {
        Throw-DriverError 'UI_CHANGED' '微信消息视口超出窗口'
    }
    $stream = [IO.MemoryStream]::new($snapshot.Image)
    $bitmap = $null
    try {
        $bitmap = [Drawing.Bitmap]::new($stream)
        $leftAnchor = $view.left - $currentRect.Left
        $rightAnchor = $view.right - $currentRect.Left
        $inner = [int](70 * $scale)
        $outer = [int](185 * $scale)
        $center = [int](($rightAnchor + $leftAnchor) / 2)
        $rows = @(foreach ($row in $first.rows) {
            if ($row.runtime_id -eq '' -or $row.right -le $row.left -or $row.bottom -le $row.top) {
                Throw-DriverError 'UI_CHANGED' '微信消息行证据不完整'
            }
            $kind = 'unknown'
            $sender = 'unknown'
            $senderName = $null
            $top = $row.top - $currentRect.Top
            $bottom = $row.bottom - $currentRect.Top
            if ($row.class -ceq 'mmui::ChatItemView' -and
                $row.text -cmatch '^(?:(?:(?:\d{4}年)?\d{1,2}月\d{1,2}日|星期[一二三四五六日天]|周[一二三四五六日天]|昨天|今天|前天)\s*)?(?:上午|下午|晚上|凌晨|中午)?\s*(?:[01]?\d|2[0-3]):[0-5]\d$') {
                $kind='time'; $sender='system'
            } elseif ($row.class -ceq 'mmui::ChatTextItemView' -and -not [string]::IsNullOrWhiteSpace($row.text)) {
                $kind='text'
            } elseif ($row.class -match '^mmui::Chat(?:Bubble|Refer|Image|Video|File|Voice|App|Card|Emoji).*ItemView$' -and $row.text) {
                $kind='media'
            }
            if ($kind -in @('text','media') -and -not $row.clipped) {
                # Dark 4.1.15.13 samples, and the existing light-theme bubble palette.
                $leftTop = Get-WeixinNativeBubbleTop $bitmap ($leftAnchor+$inner) ([Math]::Min($leftAnchor+$outer,$center)) $top $bottom @(0x2f2f30,0xeeeef0) $scale
                $rightTop = Get-WeixinNativeBubbleTop $bitmap ([Math]::Max($rightAnchor-$outer,$center)) ($rightAnchor-$inner) $top $bottom @(0x35d28d,0x9df29f) $scale
                if ($leftTop -ge 0 -and $rightTop -lt 0) {
                    $sender='peer'
                    if ($first.is_group) { $senderName=Get-WeixinNativeSenderName $Session $row $view ($leftTop+$currentRect.Top) $scale }
                    if ($senderName -isnot [string]) { $senderName=$null }
                } elseif ($rightTop -ge 0 -and $leftTop -lt 0) { $sender='self' }
            }
            @{
                kind=$kind;text=$row.text;sender=$sender;sender_name=$senderName;runtime_id=$row.runtime_id;clipped=$row.clipped
                bounds=@{x0=$row.left-$currentRect.Left;y0=$top;x1=$row.right-$currentRect.Left;y1=$bottom}
            }
        })
        $inputEmpty=$false
        if ($IncludeEvidence) {
            $composer = @(Get-WeixinNativeElements $Session.Handle ([Windows.Automation.AutomationElement]::AutomationIdProperty) 'chat_input_field' |
                Where-Object { $_.Current.ProcessId -eq $Session.ProcessId -and -not $_.Current.IsOffscreen -and $_.Current.ClassName -ceq 'mmui::ChatInputField' })
            $value=$null
            if ($composer.Count -eq 1 -and $composer[0].TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$value)) {
                $inputEmpty = [string]::IsNullOrEmpty($value.Current.Value)
            }
        }
        Assert-WeixinNativeSelectedSession $Session
        $result = @{title=$first.title;session_identity=$Session.Identity;is_group=$first.is_group;viewport=$view;rows=$rows;input_empty=$inputEmpty}
        if ($IncludeEvidence) { $result.png=[Convert]::ToBase64String($snapshot.Image) }
        return $result
    } finally {
        if ($bitmap) { $bitmap.Dispose() }
        $stream.Dispose()
    }
}
