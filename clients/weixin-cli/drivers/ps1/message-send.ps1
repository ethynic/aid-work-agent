param()
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot '_common.ps1')
. (Join-Path $PSScriptRoot 'native-messages.ps1')

function Get-WeixinSendSearchEdit($Session) {
    $focus = [Windows.Automation.AutomationElement]::FocusedElement
    if ([WeixinProbeWin32]::GetForegroundWindow().ToInt64() -ne $Session.Handle -or $null -eq $focus) { return }
    $item = $focus.Current
    if ($item.ProcessId -eq $Session.ProcessId -and $item.HasKeyboardFocus -and
        $item.ClassName -ceq 'mmui::XValidatorTextEdit' -and $item.ControlType -eq [Windows.Automation.ControlType]::Edit) {
        return $focus
    }
}

function Select-WeixinSendSearchCandidate($Nodes, $Session, [string]$TargetType) {
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

function Get-WeixinSendSearchResult($Session, [int64]$Overlay, [string]$TargetType) {
    $edit = Get-WeixinSendSearchEdit $Session
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
    $match = Select-WeixinSendSearchCandidate $nodes $Session $TargetType
    $bounds = $match.Current.BoundingRectangle
    $visible = [Windows.Rect]::Intersect($bounds,$list.Current.BoundingRectangle)
    $runtimeId = $match.GetRuntimeId() -join '.'
    if (-not $runtimeId -or $visible.IsEmpty -or $visible.Width -le 0 -or $visible.Height -le 0) {
        Throw-DriverError 'UI_CHANGED' '微信搜索目标在当前视口中不可核对'
    }
    return @{ Id=$runtimeId; Bounds=$visible }
}

function Open-WeixinSendTarget([string]$TargetName, [string]$TargetType) {
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
            $null -eq (Get-WeixinSendSearchEdit $session)) {
            Throw-DriverError 'UI_CHANGED' '微信原生搜索输入框不可用'
        }
        Send-WeixinPasteText -Text $TargetName -ForegroundGuard {
            $null -ne (Get-WeixinSendSearchEdit $session) -and (Get-WeixinSearchOverlayHwnd) -eq $overlay
        }
        Start-Sleep -Milliseconds 850
        $initial = Get-WeixinSendSearchResult $session $overlay $TargetType
        Start-Sleep -Milliseconds 150
        $candidate = Get-WeixinSendSearchResult $session $overlay $TargetType
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
        if ($overlay -and (Get-WeixinSearchOverlayHwnd) -eq $overlay -and $null -ne (Get-WeixinSendSearchEdit $session)) {
            try { Send-WeixinKeyChord @('ESC') { $null -ne (Get-WeixinSendSearchEdit $session) } } catch {}
        }
    }
}

function Get-WeixinSendComposer($Session) {
    if ([WeixinProbeWin32]::GetForegroundWindow().ToInt64() -ne $Session.Handle) {
        Throw-DriverError 'FOREGROUND_LOST' '微信发送需要保持当前会话在前台'
    }
    Assert-WeixinNativeSelectedSession $Session
    $title = Get-WeixinNativeTitleInfo $Session
    if (-not [string]::Equals($title.title, $Session.Title, [StringComparison]::Ordinal)) {
        Throw-DriverError 'TARGET_AMBIGUOUS' '微信当前会话已变化'
    }
    $composer = @(Get-WeixinNativeElements $Session.Handle ([Windows.Automation.AutomationElement]::AutomationIdProperty) 'chat_input_field' |
        Where-Object { $_.Current.ProcessId -eq $Session.ProcessId -and -not $_.Current.IsOffscreen -and
            $_.Current.ClassName -ceq 'mmui::ChatInputField' })
    $value = $null
    if ($composer.Count -ne 1 -or -not $composer[0].TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$value)) {
        Throw-DriverError 'UI_CHANGED' '微信原生输入框不可核对'
    }
    $current = $composer[0].Current
    $bounds = $current.BoundingRectangle
    $runtimeId = $composer[0].GetRuntimeId() -join '.'
    if (-not $runtimeId -or $bounds.IsEmpty -or $bounds.Width -le 0 -or $bounds.Height -le 0) {
        Throw-DriverError 'UI_CHANGED' '微信原生输入框边界不可核对'
    }
    return @{ Bounds=$bounds; Id=$runtimeId; Text=[string]$value.Current.Value; Focused=$current.HasKeyboardFocus }
}

function Assert-WeixinSameSendComposer($Session, $Expected, [string]$Text, [switch]$RequireFocus, [switch]$PinBounds) {
    $composer = Get-WeixinSendComposer $Session
    if ($composer.Id -cne $Expected.Id -or ($PinBounds -and -not $composer.Bounds.Equals($Expected.Bounds))) {
        Throw-DriverError 'UI_CHANGED' '微信原生输入框已变化'
    }
    if (-not [string]::Equals($composer.Text, $Text, [StringComparison]::Ordinal) -or
        ($RequireFocus -and -not $composer.Focused)) {
        Throw-DriverError 'UI_CHANGED' '微信输入框内容或焦点已变化，请人工核对草稿'
    }
}

function Find-WeixinSendComposerPoint($Session, $Composer, $NativeView) {
    # Export only the UIA-bounded empty edit box, below the message viewport.
    $window = New-Object WeixinProbeWin32+RECT
    $b = $Composer.Bounds
    if (-not [WeixinProbeWin32]::GetWindowRect([IntPtr]$Session.Handle, [ref]$window) -or
        $b.Left -lt $window.Left -or $b.Top -lt $NativeView.viewport.bottom -or
        $b.Right -gt $window.Right -or $b.Bottom -gt $window.Bottom) {
        Throw-DriverError 'UI_CHANGED' '微信输入框与消息区域无法安全分离'
    }
    $x = [int]([Math]::Ceiling($b.Left)) - $window.Left
    $y = [int]([Math]::Ceiling($b.Top)) - $window.Top
    $crop = [Drawing.Rectangle]::new($x, $y, ([int]([Math]::Floor($b.Right)) - $window.Left - $x),
        ([int]([Math]::Floor($b.Bottom)) - $window.Top - $y))
    if ($crop.Width -le 0 -or $crop.Height -le 0) { Throw-DriverError 'UI_CHANGED' '微信输入框裁剪边界不可用' }
    Assert-WeixinSameSendComposer $Session $Composer '' -PinBounds
    try { $frame = Get-WeixinWindowSnapshot -Hwnd $Session.Handle -Crop $crop }
    catch { Throw-DriverError 'UI_CHANGED' '无法截取空微信输入框' }
    if (-not $frame.WindowRect.Equals($window) -or -not $frame.Bounds.Equals($crop)) {
        Throw-DriverError 'UI_CHANGED' '微信输入框截图边界已变化'
    }
    Assert-WeixinSameSendComposer $Session $Composer '' -PinBounds
    $point = Invoke-KimiVision -ImageBytes $frame.Image -ProxyOnly -ArtifactPrefix 'weixin-driver-send-composer' `
        -SystemPrompt 'Locate an editable control in this WeChat screenshot. Do not transcribe text. Return only JSON.' `
        -UserText 'Return { "x": integer, "y": integer } for a safe focus click inside the upper empty text edit area. Pixel offsets are relative to this image. Return null coordinates if uncertain.'
    if (($point.x -isnot [int] -and $point.x -isnot [long]) -or ($point.y -isnot [int] -and $point.y -isnot [long]) -or
        $point.x -lt 0 -or $point.x -ge $crop.Width -or $point.y -lt 0 -or $point.y -ge $crop.Height) {
        Throw-DriverError 'UI_CHANGED' '视觉未定位到微信输入框内部'
    }
    $screenX = $window.Left + $crop.Left + [int]$point.x
    $screenY = $window.Top + $crop.Top + [int]$point.y
    Assert-WeixinSameSendComposer $Session $Composer '' -PinBounds
    $hit = [Windows.Automation.AutomationElement]::FromPoint([Windows.Point]::new($screenX, $screenY))
    if ($null -eq $hit -or ($hit.GetRuntimeId() -join '.') -cne $Composer.Id) {
        Throw-DriverError 'UI_CHANGED' '视觉坐标与微信原生输入框不一致'
    }
    return @{ ScreenX=$screenX; ScreenY=$screenY }
}

function Get-WeixinSendBottom($Session) {
    Assert-WeixinNativeSelectedSession $Session
    $view = Get-WeixinNativeRows $Session
    # This RecyclerListView does not publish ScrollPattern. Move toward latest until
    # consecutive windows agree after downward scrolling, with the same selected chat.
    for ($attempt=0; $attempt -lt 6; $attempt++) {
        Send-WeixinNativeMessageWheel $Session $view.viewport -120 4
        $next = Get-WeixinNativeRows $Session
        if ((ConvertTo-Json -InputObject $view -Depth 5 -Compress) -ceq (ConvertTo-Json -InputObject $next -Depth 5 -Compress)) {
            $view.at_bottom = $true
            $view.empty_proven = $false
            if ($view.rows.Count -eq 0) {
                $list = Get-WeixinNativeList $Session 'chat_message_list' 'mmui::RecyclerListView'
                $view.empty_proven = $list.FindAll(
                    [Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition).Count -eq 0
            }
            Assert-WeixinNativeSelectedSession $Session
            return $view
        }
        $view = $next
    }
    Throw-DriverError 'CONTENT_UNAVAILABLE' '无法确认微信消息列表末尾'
}

function Test-WeixinSendNewMessage($Before, $After, [string]$Text) {
    if (-not $Before.at_bottom -or -not $After.at_bottom) { return $false }
    $earlier = @($Before.rows)
    $later = @($After.rows)
    if ($later.Count -eq 0) { return $false }
    $last = $later[-1]
    $previousIds = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($row in $earlier) {
        if (-not $row.runtime_id -or -not $previousIds.Add($row.runtime_id)) { return $false }
    }
    $nextIds = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($row in $later) {
        if (-not $row.runtime_id -or -not $nextIds.Add($row.runtime_id)) { return $false }
    }
    if ($last.class -cne 'mmui::ChatTextItemView' -or $last.clipped -or -not $last.runtime_id -or
        -not [string]::Equals($last.text, $Text, [StringComparison]::Ordinal) -or
        $previousIds.Contains($last.runtime_id)) { return $false }
    # A newly observed row must follow a continuous suffix of the previous bottom viewport.
    # A genuinely empty native list is also an anchor; an empty visible viewport alone is not.
    for ($count=[Math]::Min($earlier.Count, $later.Count-1); $count -ge 0; $count--) {
        if ($count -eq 0 -and ($earlier.Count -ne 0 -or -not $Before.empty_proven)) { return $false }
        $aligned = $true
        for ($i=0; $i -lt $count; $i++) {
            $old = $earlier[$earlier.Count-$count+$i]
            $next = $later[$i]
            if (-not $old.runtime_id -or $old.runtime_id -cne $next.runtime_id -or $old.class -cne $next.class -or $old.text -cne $next.text) {
                $aligned = $false
                break
            }
        }
        if ($aligned) {
            for ($i=$count; $i -lt $later.Count-1; $i++) {
                if ($later[$i].class -cne 'mmui::ChatItemView' -or $later[$i].text -cnotmatch '\d{1,2}:\d{2}$' -or
                    $previousIds.Contains($later[$i].runtime_id)) { return $false }
            }
            return $true
        }
    }
    return $false
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WeixinCli.MessageSend' -Body {
    try { $request = [Console]::In.ReadToEnd() | ConvertFrom-Json }
    catch { Throw-DriverError 'UI_CHANGED' '微信发送请求格式不可用' }
    $text = [string]$request.text
    if (-not $request.target_name -or -not $text -or $text.Length -gt 500 -or $text -match '[\r\n]') {
        Throw-DriverError 'UI_CHANGED' '微信发送请求格式不可用'
    }
    $session = Open-WeixinSendTarget -TargetName ([string]$request.target_name) -TargetType ([string]$request.target_type)
    $composer = Get-WeixinSendComposer $session
    if ($composer.Text.Length -ne 0) { Throw-DriverError 'UI_CHANGED' '微信输入框已有草稿，请人工核对' }
    $view = Get-WeixinNativeRows $session
    $point = Find-WeixinSendComposerPoint $session $composer $view
    Assert-WeixinSameSendComposer $session $composer '' -PinBounds
    Send-WeixinPostMessageClick -Hwnd $session.Handle @point
    Start-Sleep -Milliseconds 200
    Assert-WeixinSameSendComposer $session $composer '' -RequireFocus
    Send-WeixinPostMessageText -Hwnd $session.Handle -Text $text
    Start-Sleep -Milliseconds 160
    $before = Get-WeixinSendBottom $session
    Assert-WeixinSameSendComposer $session $composer $text -RequireFocus
    try {
        Send-WeixinPostMessageKey -Hwnd $session.Handle -Vk 0x0D -ScanCode 0x1C
        for ($i=0; $i -lt 8; $i++) {
            Start-Sleep -Milliseconds 200
            $draft = Get-WeixinSendComposer $session
            if ($draft.Id -cne $composer.Id -or $draft.Text.Length -ne 0) { continue }
            $after = Get-WeixinSendBottom $session
            Assert-WeixinSameSendComposer $session $composer ''
            if (Test-WeixinSendNewMessage $before $after $text) {
                return @{ target=$session.Title; title=$session.Title; verified=$true }
            }
        }
    } catch {
        Throw-DriverError 'EXECUTION_UNKNOWN' '微信发送结果无法确认，请人工核对，勿重复发送'
    }
    Throw-DriverError 'EXECUTION_UNKNOWN' '微信发送结果无法确认，请人工核对，勿重复发送'
}
