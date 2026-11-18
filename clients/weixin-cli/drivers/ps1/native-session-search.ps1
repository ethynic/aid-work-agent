# Dot-source after _common.ps1 and native-messages.ps1.

function Get-WeixinNativeSearchEdit($Session) {
    $focus = [Windows.Automation.AutomationElement]::FocusedElement
    if ([WeixinProbeWin32]::GetForegroundWindow().ToInt64() -ne $Session.Handle -or $null -eq $focus) { return }
    $item = $focus.Current
    if ($item.ProcessId -eq $Session.ProcessId -and $item.HasKeyboardFocus -and
        $item.ClassName -ceq 'mmui::XValidatorTextEdit' -and $item.ControlType -eq [Windows.Automation.ControlType]::Edit) {
        return $focus
    }
}

function Select-WeixinNativeSearchCandidate($Nodes, $Session, [string]$TargetType) {
    $section = ''
    $complete = $false
    $candidates = @()
    foreach ($node in $Nodes) {
        $row = $node.Current
        if ($row.ProcessId -ne $Session.ProcessId -or $row.ControlType -ne [Windows.Automation.ControlType]::ListItem) {
            Throw-DriverError 'UI_CHANGED' '微信搜索结果结构无法核对'
        }
        $label = [string]$row.Name
        if ($row.ClassName -ceq 'mmui::XTableCell' -and $label -cin @('最常使用','功能','联系人','群聊','聊天记录','搜索网络结果')) {
            $section = $label
            if ($section -cin @('聊天记录','搜索网络结果')) { $complete=$true; break }
            continue
        }
        $eligible = ($section -ceq '最常使用' -and $TargetType -cne 'other') -or
            ($TargetType -ceq 'friend' -and $section -ceq '联系人') -or
            ($TargetType -ceq 'group' -and $section -ceq '群聊') -or
            ($TargetType -ceq 'other' -and $section -ceq '功能')
        if ($row.ClassName -ceq 'mmui::SearchContentCellView' -and $section -and $row.AutomationId) {
            if ($eligible -and [string]::Equals(($label -split '\r\n|\r|\n',2)[0], $Session.Title, [StringComparison]::Ordinal)) {
                if ($row.IsOffscreen -or $label -cne $Session.Title -or
                    -not [string]::Equals($row.AutomationId, ('search_item_' + $Session.Title), [StringComparison]::Ordinal)) {
                    Throw-DriverError 'TARGET_AMBIGUOUS' '微信搜索同名结果无法核对'
                }
                $candidates += $node
            }
            continue
        }
        if (-not $eligible -and $section -and $row.ClassName -ceq 'mmui::XTableCell' -and
            $label -cmatch '^(查看更多|更多|查看全部|展开)') { continue }
        Throw-DriverError 'TARGET_AMBIGUOUS' '微信搜索结果有尚未核对的分组或候选'
    }
    if (-not $complete) { Throw-DriverError 'UI_CHANGED' '无法确认微信搜索结果已完整加载' }
    if ($candidates.Count -eq 0) { Throw-DriverError 'TARGET_NOT_FOUND' '微信搜索没有原生精确目标' }
    if ($candidates.Count -ne 1) { Throw-DriverError 'TARGET_AMBIGUOUS' '微信搜索存在多个同名目标' }
    return $candidates[0]
}

function Get-WeixinNativeSearchResult($Session, [int64]$Overlay, [string]$TargetType) {
    $edit = Get-WeixinNativeSearchEdit $Session
    $value = $null
    if ($null -eq $edit -or -not $edit.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$value) -or
        -not [string]::Equals($value.Current.Value, $Session.Title, [StringComparison]::Ordinal) -or
        (Get-WeixinSearchOverlayHwnd) -ne $Overlay) {
        Throw-DriverError 'UI_CHANGED' '微信搜索关键词或面板已变化'
    }
    $root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Overlay)
    if ($null -eq $root -or $root.Current.ProcessId -ne $Session.ProcessId -or $root.Current.ClassName -cne 'mmui::SearchContentPopover') {
        Throw-DriverError 'UI_CHANGED' '微信原生搜索面板无法核对'
    }
    $list = Get-WeixinNativeList @{Handle=$Overlay;ProcessId=$Session.ProcessId} 'search_list' 'mmui::XTableView'
    $nodes = $list.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)
    $match = Select-WeixinNativeSearchCandidate $nodes $Session $TargetType
    $bounds = $match.Current.BoundingRectangle
    $visible = [Windows.Rect]::Intersect($bounds,$list.Current.BoundingRectangle)
    $runtimeId = $match.GetRuntimeId() -join '.'
    if (-not $runtimeId -or $visible.IsEmpty -or $visible.Width -le 0 -or $visible.Height -le 0) {
        Throw-DriverError 'UI_CHANGED' '微信搜索目标在当前视口中不可核对'
    }
    return @{ Id=$runtimeId; Bounds=$visible }
}

function Open-WeixinNativeSearchTarget([string]$TargetName, [string]$TargetType) {
    if ($TargetType -cnotin @('friend','group','other')) { Throw-DriverError 'TARGET_AMBIGUOUS' '微信目标类型无法核对' }
    $handle = Resolve-WeixinMainWindowHwnd
    $identity = Get-WindowIdentityByHwnd $handle
    if (-not (Test-WeixinMainIdentity $identity $handle)) { Throw-DriverError 'UI_CHANGED' '微信主窗口无法核对' }
    $session = @{Handle=$handle;ProcessId=$identity.ProcessId;Title=$TargetName}
    if ((Get-WeixinSearchOverlayHwnd) -ne 0 -or -not (Invoke-WeixinActivation $handle)) {
        Throw-DriverError 'FOREGROUND_LOST' '微信已有搜索面板或主窗口不在前台'
    }
    $overlay = 0
    try {
        Send-WeixinKeyChord @('CTRL','F') { [WeixinProbeWin32]::GetForegroundWindow().ToInt64() -eq $handle }
        for ($i=0; $i -lt 8 -and -not $overlay; $i++) {
            Start-Sleep -Milliseconds 150
            $overlay = Get-WeixinSearchOverlayHwnd
        }
        if (-not $overlay -or (Get-WindowIdentityByHwnd $overlay).ProcessId -ne $session.ProcessId -or
            $null -eq (Get-WeixinNativeSearchEdit $session)) {
            Throw-DriverError 'UI_CHANGED' '微信原生搜索输入框不可用'
        }
        Send-WeixinPasteText -Text $TargetName -ForegroundGuard {
            $null -ne (Get-WeixinNativeSearchEdit $session) -and (Get-WeixinSearchOverlayHwnd) -eq $overlay
        }
        Start-Sleep -Milliseconds 850
        $initial = Get-WeixinNativeSearchResult $session $overlay $TargetType
        Start-Sleep -Milliseconds 150
        $candidate = Get-WeixinNativeSearchResult $session $overlay $TargetType
        if ($candidate.Id -cne $initial.Id -or -not $candidate.Bounds.Equals($initial.Bounds)) {
            Throw-DriverError 'UI_CHANGED' '微信搜索结果仍在更新'
        }
        $x = [int]($candidate.Bounds.Left + $candidate.Bounds.Width/2)
        $y = [int]($candidate.Bounds.Top + $candidate.Bounds.Height/2)
        $hit = [Windows.Automation.AutomationElement]::FromPoint([Windows.Point]::new($x,$y))
        if ($null -eq $hit -or ($hit.GetRuntimeId() -join '.') -cne $candidate.Id) {
            Throw-DriverError 'UI_CHANGED' '微信搜索目标的点击位置已变化'
        }
        Send-WeixinPostMessageClick -Hwnd $overlay -ScreenX $x -ScreenY $y
        for ($i=0; $i -lt 8; $i++) {
            Start-Sleep -Milliseconds 160
            if ((Get-WeixinSearchOverlayHwnd) -ne 0) { continue }
            $title = Get-WeixinNativeTitleInfo $session
            if (-not [string]::Equals($title.title,$TargetName,[StringComparison]::Ordinal)) { continue }
            if (($TargetType -ceq 'group') -ne $title.is_group) {
                Throw-DriverError 'TARGET_AMBIGUOUS' '打开后的微信会话类型与目标不一致'
            }
            $session.Identity = Get-WeixinNativeSelectedSessionId $session
            if ($session.Identity) { return $session }
        }
        Throw-DriverError 'UI_CHANGED' '选择后的微信会话没有原生身份凭据'
    } finally {
        if ($overlay -and (Get-WeixinSearchOverlayHwnd) -eq $overlay -and $null -ne (Get-WeixinNativeSearchEdit $session)) {
            try { Send-WeixinKeyChord @('ESC') { $null -ne (Get-WeixinNativeSearchEdit $session) } } catch {}
        }
    }
}
