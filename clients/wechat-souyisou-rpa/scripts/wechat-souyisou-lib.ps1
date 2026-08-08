$script:WeixinExpectedPathSuffix = 'Tencent\Weixin\Weixin.exe'
$script:WeixinPluginPathSegment = '\Tencent\xwechat\xplugin\plugins\RadiumWMPF\'
$script:WeixinPluginRoot = if ($env:APPDATA) {
    (Join-Path $env:APPDATA 'Tencent\xwechat\xplugin\plugins\RadiumWMPF').Replace('/', '\').TrimEnd('\') + '\'
} else { $null }
$script:WeixinTitle = [string]([char]0x5FAE) + [char]0x4FE1
$script:MobilePattern = '(?<!\d)1[3-9]\d{9}(?!\d)'

function Test-WeixinExecutablePath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return $Path.Replace('/', '\').Trim().EndsWith(
        "\$script:WeixinExpectedPathSuffix", [StringComparison]::OrdinalIgnoreCase)
}

function Select-WeixinMainWindow {
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Candidates)
    $matches = @($Candidates | Where-Object {
        $_.Visible -eq $true -and [int64]$_.Hwnd -ne 0 -and
        [int64]$_.MainWindowHwnd -eq [int64]$_.Hwnd -and
        (Test-WeixinExecutablePath ([string]$_.ProcessPath))
    })
    if ($matches.Count -eq 0) { throw 'WX_WINDOW_NOT_FOUND' }
    if ($matches.Count -ne 1) { throw 'WX_WINDOW_AMBIGUOUS' }
    $matches[0]
}

function Test-WeixinForegroundIdentity {
    param([Parameter(Mandatory)][object]$Identity, [Parameter(Mandatory)][int64]$MainHwnd)
    if ([int64]$Identity.Hwnd -eq $MainHwnd) { return $true }
    $path = ([string]$Identity.ProcessPath).Replace('/', '\').Trim()
    if ([string]::IsNullOrWhiteSpace($path)) { return $false }
    return -not [string]::IsNullOrWhiteSpace($script:WeixinPluginRoot) -and
        $path.StartsWith($script:WeixinPluginRoot, [StringComparison]::OrdinalIgnoreCase) -and
        $path.IndexOf($script:WeixinPluginPathSegment, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        [IO.Path]::GetFileName($path).Equals('WeChatAppEx.exe', [StringComparison]::OrdinalIgnoreCase) -and
        [string]$Identity.ClassName -eq 'Chrome_WidgetWin_0' -and
        [string]$Identity.Title -eq $script:WeixinTitle
}

function Test-WeixinMainIdentity {
    param(
        [AllowNull()][object]$Identity,
        [int64]$MainHwnd
    )
    return $null -ne $Identity -and
        [int64]$Identity.Hwnd -eq $MainHwnd -and
        (Test-WeixinExecutablePath ([string]$Identity.ProcessPath)) -and
        [string]$Identity.ClassName -eq 'Qt51514QWindowIcon' -and
        [string]$Identity.Title -eq $script:WeixinTitle
}

function Test-FreshWeixinPluginIdentity {
    param(
        [AllowNull()][object]$Identity,
        [int64]$ClosedPluginHwnd,
        [int64]$MainHwnd
    )
    if (-not $Identity) { return $false }
    $candidateHwnd = [int64]$Identity.Hwnd
    return $candidateHwnd -ne $ClosedPluginHwnd -and
        $candidateHwnd -ne $MainHwnd -and
        (Test-WeixinForegroundIdentity $Identity $MainHwnd)
}

function New-WeixinWindowSession {
    param(
        [Parameter(Mandatory)][int64]$MainHwnd,
        [AllowEmptyCollection()][int64[]]$PreexistingPluginHwnds = @()
    )
    $preexisting = [Collections.Generic.HashSet[int64]]::new()
    foreach ($hwnd in $PreexistingPluginHwnds) { [void]$preexisting.Add([int64]$hwnd) }
    [pscustomobject]@{
        MainHwnd = $MainHwnd
        PreexistingPluginHwnds = $preexisting
        OwnedPluginHwnds = [Collections.Generic.HashSet[int64]]::new()
        ListHwnd = [int64]0
        CurrentHwnd = [int64]0
        LastReturnUsedIndependentDetail = $false
        NaturallyClosed = $false
    }
}

function Add-WeixinWindowSessionForeground {
    param(
        [Parameter(Mandatory)][object]$Session,
        [AllowNull()][object]$Identity,
        [ValidateSet('list','detail')][string]$Role,
        [switch]$AllowPreexisting
    )
    if (
        $AllowPreexisting -and (
            $Role -ne 'list' -or
            [int64]$Session.ListHwnd -ne 0 -or
            [int64]$Session.CurrentHwnd -ne 0 -or
            $Session.OwnedPluginHwnds.Count -ne 0
        )
    ) { throw 'ALLOW_PREEXISTING_INVALID' }
    if (
        $null -eq $Identity -or
        [int64]$Identity.Hwnd -eq [int64]$Session.MainHwnd -or
        -not (Test-WeixinForegroundIdentity $Identity ([int64]$Session.MainHwnd))
    ) { throw 'INPUT_FOCUS_LOST' }
    $hwnd = [int64]$Identity.Hwnd
    if (
        -not $AllowPreexisting -and
        $Session.PreexistingPluginHwnds.Contains($hwnd) -and
        -not $Session.OwnedPluginHwnds.Contains($hwnd)
    ) { throw 'PREEXISTING_PLUGIN_REJECTED' }
    [void]$Session.OwnedPluginHwnds.Add($hwnd)
    if ($Role -eq 'list') { $Session.ListHwnd = $hwnd }
    $Session.CurrentHwnd = $hwnd
    return $Identity
}

function Invoke-WeixinWindowSessionReturnToList {
    param(
        [Parameter(Mandatory)][object]$Session,
        [Parameter(Mandatory)][scriptblock]$GetForegroundIdentity,
        [Parameter(Mandatory)][scriptblock]$SendChord,
        [Parameter(Mandatory)][scriptblock]$SleepMilliseconds,
        [AllowNull()][scriptblock]$GetWindowIdentityByHwnd,
        [AllowNull()][scriptblock]$IsWindow,
        [AllowNull()][scriptblock]$ActivateWindow
    )
    if ([int64]$Session.ListHwnd -eq 0) { throw 'SESSION_LIST_WINDOW_MISSING' }
    $current = & $GetForegroundIdentity
    if (
        $null -eq $current -or
        -not $Session.OwnedPluginHwnds.Contains([int64]$current.Hwnd) -or
        -not (Test-WeixinForegroundIdentity $current ([int64]$Session.MainHwnd))
    ) { throw 'FOREGROUND_LOST' }
    $usedIndependentDetail = [int64]$current.Hwnd -ne [int64]$Session.ListHwnd
    # 微信搜一搜的详情页无论同窗口还是独立窗口，关闭都用 Ctrl+W。
    # Alt+Left 只是浏览器式后退，在微信搜一搜中不会关闭详情页。
    & $SendChord @('CTRL','W')
    & $SleepMilliseconds
    $returned = & $GetForegroundIdentity
    if (
        $usedIndependentDetail -and
        (Test-WeixinMainIdentity $returned ([int64]$Session.MainHwnd)
    )) {
        try {
            if (
                $null -eq $GetWindowIdentityByHwnd -or
                $null -eq $IsWindow -or
                $null -eq $ActivateWindow
            ) { throw 'RECOVERY_FAILED' }
            $listExists = [bool](& $IsWindow ([int64]$Session.ListHwnd))
            if (-not $listExists) {
                foreach ($ownedHwnd in $Session.OwnedPluginHwnds) {
                    if ([bool](& $IsWindow ([int64]$ownedHwnd))) {
                        throw 'RECOVERY_FAILED'
                    }
                }
                $Session.CurrentHwnd = [int64]0
                $Session.ListHwnd = [int64]0
                $Session.LastReturnUsedIndependentDetail = $true
                $Session.NaturallyClosed = $true
                return [int64]0
            }
            $listIdentity = & $GetWindowIdentityByHwnd ([int64]$Session.ListHwnd)
            if (
                $null -eq $listIdentity -or
                [int64]$listIdentity.Hwnd -ne [int64]$Session.ListHwnd -or
                -not $Session.OwnedPluginHwnds.Contains([int64]$listIdentity.Hwnd) -or
                -not (Test-WeixinForegroundIdentity `
                    $listIdentity ([int64]$Session.MainHwnd)) -or
                -not [bool](& $ActivateWindow ([int64]$Session.ListHwnd))
            ) { throw 'RECOVERY_FAILED' }
        } catch { throw 'RECOVERY_FAILED' }
        & $SleepMilliseconds
        $returned = & $GetForegroundIdentity
    }
    if (
        $null -eq $returned -or
        [int64]$returned.Hwnd -ne [int64]$Session.ListHwnd -or
        -not (Test-WeixinForegroundIdentity $returned ([int64]$Session.MainHwnd))
    ) { throw 'RECOVERY_FAILED' }
    $Session.CurrentHwnd = [int64]$Session.ListHwnd
    $Session.LastReturnUsedIndependentDetail = $usedIndependentDetail
    return [int64]$Session.ListHwnd
}

function Test-WeixinReadableDetailCopy {
    param([AllowEmptyString()][string]$DetailText, [AllowEmptyString()][string]$ResultText)
    if ([string]::IsNullOrWhiteSpace($DetailText)) { return $false }
    if (
        -not [string]::IsNullOrWhiteSpace($ResultText) -and
        [string]::Equals(
            $DetailText.Trim(), $ResultText.Trim(), [StringComparison]::Ordinal)
    ) { return $false }
    if (Test-ResultPageEvidence $DetailText $ResultText) { return $false }
    return $true
}

function Test-WeixinDetailOpened {
    param([string]$BeforeHash, [string]$AfterHash)
    return -not [string]::IsNullOrWhiteSpace($BeforeHash) -and
        -not [string]::IsNullOrWhiteSpace($AfterHash) -and
        -not [string]::Equals($BeforeHash, $AfterHash, [StringComparison]::Ordinal)
}

function Get-WeixinCollectStatus {
    param(
        [AllowNull()][object]$FoundJudge,
        [bool]$RecoveryUnavailable,
        [int]$Checked,
        [int]$Limit,
        [int]$Failures
    )
    if ($FoundJudge) { return 'found' }
    if (-not $RecoveryUnavailable -and $Checked -ge $Limit -and $Failures -eq 0) {
        return 'not_found'
    }
    return 'inconclusive'
}

function Wait-WeixinDetailSettled {
    param(
        [Parameter(Mandatory)][scriptblock]$ForegroundGuard,
        [Parameter(Mandatory)][scriptblock]$SleepMilliseconds,
        [ValidateRange(1,30000)][int]$DelayMilliseconds = 5000
    )
    if (-not (& $ForegroundGuard)) { throw 'FOREGROUND_LOST' }
    & $SleepMilliseconds $DelayMilliseconds
    if (-not (& $ForegroundGuard)) { throw 'FOREGROUND_LOST' }
}

function Close-WeixinWindowSession {
    param(
        [Parameter(Mandatory)][object]$Session,
        [Parameter(Mandatory)][scriptblock]$CloseWindow
    )
    $failures = New-Object Collections.Generic.List[string]
    $targets = @($Session.OwnedPluginHwnds | Sort-Object {
        if ([int64]$_ -eq [int64]$Session.ListHwnd) { 1 } else { 0 }
    })
    foreach ($target in $targets) {
        try {
            $result = & $CloseWindow ([int64]$target)
            if (-not $result -or $result.session_closed -ne $true) {
                $failures.Add('SESSION_CLEANUP_FAILED')
            }
        } catch {
            $code = if ($_.Exception.Message -match '^[A-Z][A-Z0-9_]+$') {
                $_.Exception.Message
            } else { 'SESSION_CLEANUP_FAILED' }
            $failures.Add($code)
        }
    }
    [pscustomobject]@{
        session_closed = $failures.Count -eq 0
        error_codes = @($failures)
        closed_targets = @($targets)
    }
}

function Get-WeixinRecoveryEvidenceOutcome {
    param(
        [Parameter(Mandatory)][object]$Session,
        [Parameter(Mandatory)][bool]$EvidenceVerified
    )
    if ($EvidenceVerified) { return 'continue' }
    if ($Session.LastReturnUsedIndependentDetail) { return 'cleanup' }
    throw 'RECOVERY_FAILED'
}

function Close-WeixinPluginSession {
    param(
        [Parameter(Mandatory)][int64]$PluginHwnd,
        [Parameter(Mandatory)][int64]$MainHwnd,
        [Parameter(Mandatory)][scriptblock]$GetWindowIdentity,
        [Parameter(Mandatory)][scriptblock]$IsWindow,
        [Parameter(Mandatory)][scriptblock]$IsWindowVisible,
        [Parameter(Mandatory)][scriptblock]$GetForegroundHwnd,
        [Parameter(Mandatory)][scriptblock]$ActivateWindow,
        [Parameter(Mandatory)][scriptblock]$RequestCloseWindow,
        [Parameter(Mandatory)][scriptblock]$SleepMilliseconds,
        [int]$PollAttempts = 50
    )
    try {
        $pluginExists = [bool](& $IsWindow $PluginHwnd)
        if ($pluginExists) {
            $pluginIdentity = & $GetWindowIdentity $PluginHwnd
            if (
                -not $pluginIdentity -or
                [int64]$pluginIdentity.Hwnd -ne $PluginHwnd -or
                -not (Test-WeixinForegroundIdentity $pluginIdentity $MainHwnd) -or
                $PluginHwnd -eq $MainHwnd
            ) {
                throw 'PLUGIN_IDENTITY_INVALID'
            }
            $pluginClosed = $false
            $pluginHidden = -not [bool](& $IsWindowVisible $PluginHwnd)
            if (-not $pluginHidden) {
                if (-not [bool](& $RequestCloseWindow $PluginHwnd)) {
                    throw 'PLUGIN_CLOSE_REJECTED'
                }
                for ($attempt = 0; $attempt -lt $PollAttempts; $attempt++) {
                    if (-not [bool](& $IsWindow $PluginHwnd)) {
                        $pluginClosed = $true
                        break
                    }
                    if (-not [bool](& $IsWindowVisible $PluginHwnd)) {
                        $pluginHidden = $true
                        break
                    }
                    & $SleepMilliseconds 100
                }
            }
            if (-not $pluginClosed -and -not $pluginHidden) {
                throw 'PLUGIN_CLOSE_TIMEOUT'
            }
        }

        if (-not [bool](& $IsWindow $MainHwnd) -or -not [bool](& $IsWindowVisible $MainHwnd)) {
            throw 'MAIN_WINDOW_MISSING'
        }
        $mainIdentity = & $GetWindowIdentity $MainHwnd
        if (-not (Test-WeixinMainIdentity $mainIdentity $MainHwnd)) {
            throw 'MAIN_WINDOW_UNTRUSTED'
        }
        if (-not (& $ActivateWindow $MainHwnd)) { throw 'MAIN_ACTIVATION_FAILED' }
        if ([int64](& $GetForegroundHwnd) -ne $MainHwnd) {
            throw 'MAIN_FOREGROUND_NOT_RESTORED'
        }
        return [pscustomobject]@{ session_closed=$true; main_hwnd=$MainHwnd }
    } catch {
        # 保留无敏感信息的稳定底层错误码，调用方才能判断是插件关闭、主窗口
        # 激活还是前台恢复失败；不要把所有故障压平为同一个清理错误。
        if ($_.Exception.Message -match '^[A-Z][A-Z0-9_]+$') { throw }
        throw 'SESSION_CLEANUP_FAILED'
    }
}

function Complete-WeixinPluginSession {
    param(
        [Parameter(Mandatory)][ref]$Completed,
        [Parameter(Mandatory)][scriptblock]$Cleanup
    )
    if ([bool]$Completed.Value) {
        return [pscustomobject]@{ session_closed=$true; already_closed=$true }
    }
    $result = & $Cleanup
    if (-not $result -or $result.session_closed -ne $true) {
        throw 'SESSION_CLEANUP_FAILED'
    }
    $Completed.Value = $true
    return $result
}

function Test-OrRestoreTrustedForeground {
    param(
        [Parameter(Mandatory)][scriptblock]$Guard,
        [Parameter(Mandatory)][scriptblock]$Activate,
        [Parameter(Mandatory)][scriptblock]$Pause
    )
    if (& $Guard) { return $true }
    if (-not (& $Activate)) { return $false }
    & $Pause
    return [bool](& $Guard)
}

function Invoke-LimitedTrustedOpen {
    param(
        [Parameter(Mandatory)][scriptblock]$OpenAttempt,
        [Parameter(Mandatory)][scriptblock]$Verify,
        [int]$MaxAttempts = 2
    )
    if ($MaxAttempts -lt 1 -or $MaxAttempts -gt 2) { throw 'OPEN_ATTEMPTS_INVALID' }
    for ($attempt = 0; $attempt -lt $MaxAttempts; $attempt++) {
        & $OpenAttempt
        $identity = & $Verify
        if ($identity) { return $identity }
    }
    throw 'SOUYISOU_WINDOW_UNTRUSTED'
}

function Invoke-SafeKeyChord {
    param(
        [object[]]$Keys, [scriptblock]$KeyEvent, [scriptblock]$Pause,
        [AllowNull()][scriptblock]$ForegroundGuard
    )
    $failure = $null
    $releaseFailures = @()
    $pressedKeys = @()
    try {
        if ($ForegroundGuard -and -not (& $ForegroundGuard)) { throw 'FOREGROUND_LOST' }
        foreach ($key in $Keys) {
            $pressedKeys += $key
            & $KeyEvent $key $false
        }
        & $Pause
    } catch { $failure = $_ } finally {
        for ($i = $pressedKeys.Count - 1; $i -ge 0; $i--) {
            try { & $KeyEvent $pressedKeys[$i] $true } catch { $releaseFailures += $_ }
        }
    }
    if ($failure) { throw $failure }
    if ($releaseFailures.Count) { throw "KEY_RELEASE_FAILED: $($releaseFailures[0].Exception.Message)" }
}

function Invoke-SafeMouseClick {
    param(
        [Parameter(Mandatory)][scriptblock]$MouseEvent,
        [Parameter(Mandatory)][scriptblock]$ForegroundGuard
    )
    $failure = $null
    $downAttempted = $false
    try {
        if (-not (& $ForegroundGuard)) { throw 'FOREGROUND_LOST' }
        $downAttempted = $true
        & $MouseEvent $false
        if (-not (& $ForegroundGuard)) { throw 'FOREGROUND_LOST' }
    } catch { $failure = $_ } finally {
        if ($downAttempted) {
            try { & $MouseEvent $true }
            catch {
                if (-not $failure) { throw "MOUSE_RELEASE_FAILED: $($_.Exception.Message)" }
            }
        }
    }
    if ($failure) { throw $failure }
}

function Invoke-WeixinActivation {
    param(
        [int64]$Hwnd, [scriptblock]$GetCurrentThreadId, [scriptblock]$GetWindowThreadId,
        [scriptblock]$GetForegroundHwnd, [scriptblock]$AttachThreadInput,
        [scriptblock]$ShowWindow, [scriptblock]$BringWindowToTop,
        [scriptblock]$SetActiveWindow, [scriptblock]$SetForegroundWindow,
        [scriptblock]$SleepMilliseconds
    )
    [void](& $ShowWindow $Hwnd)
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        $attached = @()
        $current = [uint32](& $GetCurrentThreadId)
        $target = [uint32](& $GetWindowThreadId $Hwnd)
        $foregroundHwnd = [int64](& $GetForegroundHwnd)
        $foreground = if ($foregroundHwnd) { [uint32](& $GetWindowThreadId $foregroundHwnd) } else { 0 }
        try {
            foreach ($thread in @($foreground, $target)) {
                if ($thread -and $thread -ne $current -and $attached -notcontains $thread -and
                    (& $AttachThreadInput $current $thread $true)) { $attached += $thread }
            }
            [void](& $BringWindowToTop $Hwnd)
            [void](& $SetActiveWindow $Hwnd)
            [void](& $SetForegroundWindow $Hwnd)
        } finally {
            [array]::Reverse($attached)
            foreach ($thread in $attached) { [void](& $AttachThreadInput $current $thread $false) }
        }
        & $SleepMilliseconds 150
        if ([int64](& $GetForegroundHwnd) -eq $Hwnd) { return $true }
    }
    return $false
}

function New-SearchQuery {
    param([string]$AssociationName, [string]$PersonName, [switch]$ContactSuffix)
    $association = $AssociationName.Trim()
    $person = $PersonName.Trim()
    if (-not $association -or -not $person) { throw 'INVALID_INPUT' }
    if ($ContactSuffix) {
        return "$association $person 联系人"
    }
    return "$association $person"
}

function Get-MobileCandidates {
    param([AllowEmptyString()][string]$Text)
    @([regex]::Matches($Text, $script:MobilePattern) | ForEach-Object Value | Select-Object -Unique)
}

function Test-JudgeResponseSchema {
    param([object]$Result)
    if (-not $Result -or -not ($Result.matched -is [bool])) { return $false }
    foreach ($name in @('person_name','mobile','evidence_quote','reason')) {
        if (-not ($Result.$name -is [string])) { return $false }
    }
    $confidence = $Result.confidence
    $numericConfidence = $confidence -is [byte] -or $confidence -is [sbyte] -or
        $confidence -is [int16] -or $confidence -is [uint16] -or
        $confidence -is [int32] -or $confidence -is [uint32] -or
        $confidence -is [int64] -or $confidence -is [uint64] -or
        $confidence -is [single] -or $confidence -is [double] -or $confidence -is [decimal]
    return $numericConfidence -and -not [double]::IsNaN([double]$confidence) -and
        -not [double]::IsInfinity([double]$confidence) -and
        [double]$confidence -ge 0 -and [double]$confidence -le 1
}

function Test-JudgeResult {
    param([object]$Result, [string]$Evidence, [string]$PersonName)
    if (-not (Test-JudgeResponseSchema $Result) -or $Result.matched -ne $true) { return $false }
    if ([string]$Result.person_name -ne $PersonName) { return $false }
    $mobile = [string]$Result.mobile
    if (-not $mobile -or $Evidence.IndexOf($mobile, [StringComparison]::Ordinal) -lt 0) { return $false }
    if ((Get-MobileCandidates $Evidence) -notcontains $mobile) { return $false }
    return $true
}

function Invoke-DeterministicJudge {
    param([string]$Evidence, [string]$PersonName)
    if (-not @(Get-MobileCandidates $Evidence).Count -or $Evidence.IndexOf($PersonName, [StringComparison]::Ordinal) -lt 0) {
        return [pscustomobject]@{ matched = $false; reason = 'name_or_mobile_absent' }
    }
    foreach ($line in ($Evidence -split '\r?\n')) {
        foreach ($clause in ($line -split '[;；]')) {
            $nameIndex = $clause.IndexOf($PersonName, [StringComparison]::Ordinal)
            if ($nameIndex -lt 0 -or $clause.Length -gt 160) { continue }
            $prefix = $clause.Substring(0, $nameIndex)
            # “刘甲、目标姓名”属于多人共享联系方式，不能确定手机号独属于目标。
            if ($prefix -match '[\p{IsCJKUnifiedIdeographs}]{2,4}[、,，]\s*$') { continue }
            $afterName = $clause.Substring($nameIndex + $PersonName.Length)
            $binding = [regex]::Match(
                $afterName,
                '^(?:\s|[:：,，()（）-]|联系电话|联系手机|手机号码|手机号|手机|电话){0,16}(?<mobile>1[3-9]\d{9})(?:\D|$)'
            )
            if ($binding.Success) {
                return [pscustomobject]@{
                    matched = $true; person_name = $PersonName
                    mobile = $binding.Groups['mobile'].Value
                    evidence_quote = $clause; confidence = 1.0
                    reason = 'deterministic_same_clause'
                }
            }
        }
    }
    [pscustomobject]@{ matched = $false; reason = 'ambiguous_binding' }
}

function Invoke-EvidenceJudge {
    param(
        [string]$Evidence, [string]$AssociationName, [string]$PersonName,
        [AllowNull()][scriptblock]$Judge
    )
    if (-not $Judge -and -not @(Get-MobileCandidates $Evidence).Count) {
        return [pscustomobject]@{ matched = $false; reason = 'no_mobile_candidate' }
    }
    try {
        $result = if ($Judge) { & $Judge ([pscustomobject]@{
            association_name = $AssociationName; person_name = $PersonName; text = $Evidence
        }) } else { Invoke-DeterministicJudge $Evidence $PersonName }
    } catch {
        Add-Content -Path "$env:TEMP\wechat_diag.log" -Value "[$([DateTimeOffset]::Now.ToString('HH:mm:ss'))] Invoke-EvidenceJudge EXCEPTION: $($_.Exception.Message) person=$PersonName"
        return [pscustomobject]@{ matched = $false; inconclusive = $true; reason = 'judge_failed' }
    }
    if ($result.inconclusive -eq $true) {
        Add-Content -Path "$env:TEMP\wechat_diag.log" -Value "[$([DateTimeOffset]::Now.ToString('HH:mm:ss'))] Invoke-EvidenceJudge INCONCLUSIVE: matched=$($result.matched) reason=$($result.reason) person=$PersonName"
        $safe = [pscustomobject]@{ matched = $false; inconclusive = $true; reason = 'judge_failed' }
        if ($result.token_usage) {
            $safe | Add-Member -NotePropertyName token_usage -NotePropertyValue $result.token_usage
        }
        return $safe
    }
    if ($Judge -and -not (Test-JudgeResponseSchema $result)) {
        return [pscustomobject]@{ matched = $false; inconclusive = $true; reason = 'judge_schema_rejected' }
    }
    if ($Judge -and $result.matched -eq $false) { return $result }
    if (Test-JudgeResult $result $Evidence $PersonName) { return $result }
    if ($Judge -and $result.matched -eq $true) {
        return [pscustomobject]@{ matched = $false; inconclusive = $true; reason = 'judge_evidence_rejected' }
    }
    [pscustomobject]@{ matched = $false; reason = 'judge_evidence_rejected' }
}

function ConvertTo-WindowsCommandLineArgument {
    param([AllowEmptyString()][string]$Value)
    $builder = New-Object Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes++
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (($backslashes * 2) + 1)))
            [void]$builder.Append('"')
        } else {
            if ($backslashes) { [void]$builder.Append(('\' * $backslashes)) }
            [void]$builder.Append($character)
        }
        $backslashes = 0
    }
    if ($backslashes) { [void]$builder.Append(('\' * ($backslashes * 2))) }
    [void]$builder.Append('"')
    $builder.ToString()
}

function New-ExternalJudge {
    param(
        [string]$Executable, [string[]]$Arguments = @(),
        [ValidateRange(1,60000)][int]$TimeoutMilliseconds = 60000
    )
    if ([string]::IsNullOrWhiteSpace($Executable)) { return $null }
    $quoteArgument = ${function:ConvertTo-WindowsCommandLineArgument}
    {
        param($payload)
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = $Executable
        $start.UseShellExecute = $false
        $start.RedirectStandardInput = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $start.CreateNoWindow = $true
        $utf8NoBom = New-Object Text.UTF8Encoding($false)
        if ($start.PSObject.Properties.Name -contains 'StandardInputEncoding') {
            $start.StandardInputEncoding = $utf8NoBom
        }
        $start.StandardOutputEncoding = $utf8NoBom
        $start.StandardErrorEncoding = $utf8NoBom
        $start.Arguments = [string]::Join(
            ' ', @($Arguments | ForEach-Object { & $quoteArgument ([string]$_) }))
        $process = New-Object Diagnostics.Process
        $process.StartInfo = $start
        $previousInputEncoding = [Console]::InputEncoding
        try {
            # On legacy .NET this controls only construction of the
            # redirected StreamWriter (not payload encoding); prevent its
            # default BOM before writing explicit UTF-8 bytes below.
            [Console]::InputEncoding = $utf8NoBom
            if (-not $process.Start()) { throw 'JUDGE_START_FAILED' }
        } finally {
            [Console]::InputEncoding = $previousInputEncoding
        }
        $stdin = $process.StandardInput
        try {
            $stdoutTask = $process.StandardOutput.ReadToEndAsync()
            $stderrTask = $process.StandardError.ReadToEndAsync()
            # Windows PowerShell/.NET Framework may not expose
            # ProcessStartInfo.StandardInputEncoding. Write explicit UTF-8
            # bytes to the redirected stream instead of relying on the
            # StreamWriter or console code page.
            $stdinJson = ($payload | ConvertTo-Json -Depth 6 -Compress) + "`n"
            $stdinBytes = $utf8NoBom.GetBytes($stdinJson)
            $stdin.BaseStream.Write($stdinBytes, 0, $stdinBytes.Length)
            $stdin.BaseStream.Flush()
            $stdin.Close()
            if (-not $process.WaitForExit($TimeoutMilliseconds)) {
                try { $process.Kill() } catch {}
                throw 'JUDGE_TIMEOUT'
            }
            $line = $stdoutTask.Result.Trim()
            [void]$stderrTask.Result
            if ($process.ExitCode -ne 0) { throw 'JUDGE_PROCESS_FAILED' }
            if ([string]::IsNullOrWhiteSpace($line)) { throw 'JUDGE_OUTPUT_EMPTY' }
            $line | ConvertFrom-Json
        } finally { $process.Dispose() }
    }.GetNewClosure()
}

function Get-CfHtmlLinks {
    param([AllowEmptyString()][string]$Html)
    if (-not $Html) { return @() }
    @([regex]::Matches($Html, '(?i)href\s*=\s*["''](?<url>https?://[^"'']+)') |
        ForEach-Object { $_.Groups['url'].Value } | Select-Object -Unique)
}

function Protect-EvidenceArtifact {
    param([string]$Directory, [object]$Payload)
    [void](Add-Type -AssemblyName System.Security)
    if (-not (Test-Path -LiteralPath $Directory)) {
        [void](New-Item -ItemType Directory -Path $Directory -Force)
    }
    $json = $Payload | ConvertTo-Json -Depth 12 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    try {
        $protected = [Security.Cryptography.ProtectedData]::Protect(
            $bytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    } catch { throw "ARTIFACT_PROTECTION_FAILED: $($_.Exception.Message)" }
    $id = [guid]::NewGuid().ToString('N')
    $path = Join-Path $Directory "$id.dpapi"
    [IO.File]::WriteAllBytes($path, $protected)
    [pscustomobject]@{ artifact_ref = $path; artifact_id = $id }
}

function Unprotect-EvidenceArtifact {
    param([Parameter(Mandatory)][string]$Path)
    [void](Add-Type -AssemblyName System.Security)
    $protected = [IO.File]::ReadAllBytes($Path)
    $bytes = [Security.Cryptography.ProtectedData]::Unprotect(
        $protected, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    [Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json
}

function Get-RedactedSummary {
    param([AllowEmptyString()][string]$Text)
    $redacted = [regex]::Replace($Text, $script:MobilePattern, '1**********')
    if ($redacted.Length -gt 160) { $redacted = $redacted.Substring(0, 160) }
    $redacted
}

function Get-BitmapSha256 {
    param([Parameter(Mandatory)][System.Drawing.Bitmap]$Bitmap)
    $stream = New-Object IO.MemoryStream
    try {
        $Bitmap.Save($stream, [Drawing.Imaging.ImageFormat]::Png)
        $stream.Position = 0
        $sha = [Security.Cryptography.SHA256]::Create()
        try { ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
        finally { $sha.Dispose() }
    } finally { $stream.Dispose() }
}

function ConvertTo-WeixinPhysicalClickPoint {
    param(
        [Parameter(Mandatory)][double]$X,
        [Parameter(Mandatory)][double]$Y,
        [ValidateRange(96,480)][int]$WindowDpi,
        [Parameter(Mandatory)][string]$DpiAwareness
    )
    if ($DpiAwareness -ne 'PerMonitorV2') { throw 'DPI_AWARENESS_INVALID' }
    if (
        [double]::IsNaN($X) -or [double]::IsInfinity($X) -or
        [double]::IsNaN($Y) -or [double]::IsInfinity($Y)
    ) { throw 'UIA_CLICK_POINT_INVALID' }

    # UIA BoundingRectangle/ClickablePoint 与 Per-Monitor V2 下的 SetCursorPos
    # 都使用物理屏幕坐标。150% 缩放时不能再除以 1.5，也不能叠加窗口原点；
    # 负数坐标是位于主屏左侧/上方的显示器，不应当被拒绝。
    [pscustomobject]@{
        x = [int][math]::Round($X, [MidpointRounding]::AwayFromZero)
        y = [int][math]::Round($Y, [MidpointRounding]::AwayFromZero)
        coordinate_space = 'physical_screen'
        window_dpi = $WindowDpi
    }
}

function Select-WeixinUiaResultTargets {
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Descriptors,
        [Parameter(Mandatory)][string]$AssociationName,
        [Parameter(Mandatory)][string]$PersonName,
        [Parameter(Mandatory)][object]$WindowRect
    )
    $association = [regex]::Replace($AssociationName.Trim(), '\s+', '')
    $person = [regex]::Replace($PersonName.Trim(), '\s+', '')
    if ([string]::IsNullOrWhiteSpace($association) -or
        [string]::IsNullOrWhiteSpace($person)) {
        throw 'UIA_TARGET_TERMS_INVALID'
    }
    $windowLeft = [double]$WindowRect.Left
    $windowTop = [double]$WindowRect.Top
    $windowRight = [double]$WindowRect.Right
    $windowBottom = [double]$WindowRect.Bottom
    $windowWidth = $windowRight - $windowLeft
    $windowHeight = $windowBottom - $windowTop
    if ($windowWidth -le 0 -or $windowHeight -le 0) { throw 'WINDOW_RECT_FAILED' }

    $eligible = New-Object Collections.Generic.List[object]
    foreach ($descriptor in $Descriptors) {
        try {
            $name = [regex]::Replace(([string]$descriptor.Name).Trim(), '\s+', '')
            $controlType = [string]$descriptor.ControlType
            $left = [double]$descriptor.Left
            $top = [double]$descriptor.Top
            $width = [double]$descriptor.Width
            $height = [double]$descriptor.Height
            $right = $left + $width
            $bottom = $top + $height
            $clickX = [double]$descriptor.ClickableX
            $clickY = [double]$descriptor.ClickableY
            if (
                $descriptor.IsOffscreen -eq $true -or
                $descriptor.SupportsInvoke -ne $true -or
                $descriptor.HasClickablePoint -ne $true -or
                $name.IndexOf($association, [StringComparison]::Ordinal) -lt 0 -or
                $name.IndexOf($person, [StringComparison]::Ordinal) -lt 0 -or
                $name -match '(百科|小程序)' -or
                $controlType -notin @('Button','ListItem')
            ) { continue }
            $validSize = if ($controlType -eq 'Button') {
                $width -ge 400 -and $height -ge 80 -and $height -le 300
            } else {
                $width -ge 300 -and $height -ge 25 -and $height -le 130
            }
            if (-not $validSize) { continue }
            # 只接纳结果主列：排除顶部分类、右侧栏以及窗口外的虚拟化节点。
            $centerX = $left + ($width / 2)
            $centerY = $top + ($height / 2)
            if (
                $left -lt $windowLeft -or $top -lt $windowTop -or
                $right -gt $windowRight -or $bottom -gt $windowBottom -or
                $centerX -lt ($windowLeft + $windowWidth * 0.04) -or
                $centerX -gt ($windowLeft + $windowWidth * 0.76) -or
                $centerY -lt ($windowTop + $windowHeight * 0.12) -or
                $centerY -gt ($windowTop + $windowHeight * 0.95) -or
                $clickX -lt $left -or $clickX -gt $right -or
                $clickY -lt $top -or $clickY -gt $bottom
            ) { continue }
            $sha = [Security.Cryptography.SHA256]::Create()
            try {
                $fingerprintBytes = [Text.Encoding]::UTF8.GetBytes(
                    "$controlType|$name")
                $fingerprint = ([BitConverter]::ToString(
                    $sha.ComputeHash($fingerprintBytes))).Replace('-', '').ToLowerInvariant()
            } finally { $sha.Dispose() }
            $eligible.Add([pscustomobject]@{
                control_type=$controlType;left=$left;top=$top;width=$width;height=$height
                x=$clickX;y=$clickY;coordinate_space='physical_screen'
                fingerprint=$fingerprint;name=$name
            })
        } catch {
            # 单个 UIA 节点可能在枚举过程中失效；候选层跳过该节点，不能放宽筛选。
            continue
        }
    }

    $selected = New-Object Collections.Generic.List[object]
    foreach ($candidate in @($eligible | Sort-Object `
        @{Expression={if ($_.control_type -eq 'Button') { 0 } else { 1 }}}, `
        @{Expression={[double]$_.top}}, @{Expression={[double]$_.left}})) {
        $duplicate = $false
        foreach ($accepted in $selected) {
            $intersectionWidth = [math]::Max(0, [math]::Min(
                $candidate.left + $candidate.width,
                $accepted.left + $accepted.width) - [math]::Max($candidate.left,$accepted.left))
            $intersectionHeight = [math]::Max(0, [math]::Min(
                $candidate.top + $candidate.height,
                $accepted.top + $accepted.height) - [math]::Max($candidate.top,$accepted.top))
            $intersection = $intersectionWidth * $intersectionHeight
            $smallerArea = [math]::Min(
                $candidate.width * $candidate.height,
                $accepted.width * $accepted.height)
            if ($smallerArea -gt 0 -and ($intersection / $smallerArea) -ge 0.70) {
                $duplicate = $true
                break
            }
        }
        if (-not $duplicate) { $selected.Add($candidate) }
    }
    $selected.ToArray()
}

function New-WeixinUiaCandidateExhaustionRecord {
    param([ValidateRange(0,10)][int]$Checked)
    [pscustomobject]@{
        stage = 'points'
        reason = if ($Checked -eq 0) {
            'uia_candidates_unavailable'
        } else {
            'no_new_uia_candidate'
        }
        text_length = 0
    }
}

function Assert-WeixinWorkBudget {
    param(
        [Parameter(Mandatory)][DateTimeOffset]$Deadline,
        [ValidateRange(0,120000)][int]$MinimumRemainingMilliseconds = 0,
        [scriptblock]$GetNow = { [DateTimeOffset]::UtcNow }
    )
    $remaining = [int64][math]::Floor(
        ($Deadline - [DateTimeOffset](& $GetNow)).TotalMilliseconds)
    if ($remaining -lt $MinimumRemainingMilliseconds) {
        throw 'WECHAT_WORK_TIMEOUT'
    }
    return $remaining
}

function Normalize-WeixinSearchInputText {
    param([AllowNull()][string]$Text)
    if ($null -eq $Text) { return '' }
    return [regex]::Replace($Text.Trim(), '[\s\u3000]+', ' ')
}

function Invoke-WeixinSouyisouSetValueAndReadback {
    param(
        [Parameter(Mandatory)][IntPtr]$PluginHwnd,
        [Parameter(Mandatory)][string]$Query
    )
    # UIA 原生写搜一搜搜索框：枚举插件窗找到合法矩形 Edit（搜索框，落地页唯一），
    # ValuePattern.SetValue 写入查询并返回读回文本（供上层比对）。不依赖窗口前台/键盘焦点/剪贴板。
    # Windows PowerShell 5.1（.NET Framework 4.x）没有 [double]::IsFinite，用
    # Width/Height>0 + 左上顶<+∞ 判合法矩形（NaN 与任何值比较均为 false，自动排除）。
    $root = [Windows.Automation.AutomationElement]::FromHandle($PluginHwnd)
    if ($null -eq $root) { throw 'UIA_ROOT_UNAVAILABLE' }
    $nodes = $root.FindAll(
        [Windows.Automation.TreeScope]::Descendants,
        [Windows.Automation.Condition]::TrueCondition)
    $target = $null
    foreach ($node in $nodes) {
        try {
            $current = $node.Current
            if ([string]$current.ControlType.ProgrammaticName.Replace('ControlType.','') -ne 'Edit') { continue }
            $b = $current.BoundingRectangle
            if ([double]$b.Width -gt 0 -and [double]$b.Height -gt 0 -and
                [double]$b.Left -lt [double]::PositiveInfinity -and
                [double]$b.Top -lt [double]::PositiveInfinity) {
                $target = $node; break
            }
        } catch { continue }
    }
    if ($null -eq $target) { throw 'SEARCHBOX_NOT_FOUND' }
    $vp = $null
    if (-not $target.TryGetCurrentPattern(
        [Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
        throw 'SEARCHBOX_VALUE_PATTERN_UNAVAILABLE'
    }
    $vp.SetValue($Query)
    Start-Sleep -Milliseconds 200
    return [string]$vp.Current.Value
}

function Invoke-VerifiedWeixinUaSearchSubmission {
    param(
        [Parameter(Mandatory)][string]$Query,
        [Parameter(Mandatory)][scriptblock]$ForegroundGuard,
        [Parameter(Mandatory)][scriptblock]$WriteAndReadback,
        [AllowNull()][scriptblock]$SubmitChord,
        [bool]$Submit = $true,
        [hashtable]$Diagnostics = @{}
    )
    try {
        $Diagnostics.readback_matched = $false
        # SetValue 不依赖窗口前台/键盘焦点（实测：TAB 移走焦点后仍写入成功），故写入前不查前台，
        # 避免搜一搜窗前台漂走时被误判 INPUT_FOCUS_LOST 触发无谓重开——这正是改用 SetValue 的意义。
        $actual = [string](& $WriteAndReadback)
        $Diagnostics.readback_matched = [string]::Equals(
            (Normalize-WeixinSearchInputText $actual),
            (Normalize-WeixinSearchInputText $Query),
            [StringComparison]::Ordinal
        )
        if (-not $Diagnostics.readback_matched) {
            throw 'SEARCH_INPUT_READBACK_MISMATCH'
        }
        if ($Submit -and $SubmitChord) {
            # 提交（回车）需要窗口前台：显式查一次，INPUT_FOCUS_LOST 由上层重开兜底。
            if (-not (& $ForegroundGuard)) { throw 'INPUT_FOCUS_LOST' }
            & $SubmitChord
        }
        return [pscustomobject]@{
            input_verified=$true; submitted=[bool]($Submit -and $SubmitChord)
            input_method='uia_value_pattern'
        }
    } catch {
        if ($_.Exception.Message -in @(
            'SEARCH_INPUT_READBACK_MISMATCH', 'INPUT_FOCUS_LOST'
        )) { throw }
        throw 'SEARCH_INPUT_FOCUS_FAILED'
    }
}

function Test-ListHasActionableCandidates {
    param([string]$Text, [string]$PersonName, [AllowEmptyCollection()][object[]]$Links)
    if ([string]::IsNullOrWhiteSpace($Text) -or [string]::IsNullOrWhiteSpace($PersonName)) {
        return $false
    }
    # CF_HTML 在部分微信版本/页面主题下不提供 href；UIA 语义候选不依赖
    # 链接，因此不能仅因 Links 为空就跳过真实候选。
    return $Text.IndexOf($PersonName, [StringComparison]::Ordinal) -ge 0
}

function Test-WindowRectDimensions {
    param([int]$Width, [int]$Height)
    return $Width -ge 400 -and $Height -ge 300 -and
        $Width -le 10000 -and $Height -le 10000 -and
        ([int64]$Width * [int64]$Height) -le 40000000
}

function Test-DetailEvidence {
    param([string]$DetailText, [string]$ResultText, [string]$AssociationName, [string]$PersonName)
    if ([string]::IsNullOrWhiteSpace($DetailText) -or $DetailText.Length -lt 80) { return $false }
    if ([string]::Equals($DetailText.Trim(), $ResultText.Trim(), [StringComparison]::Ordinal)) { return $false }
    $hasTarget = $DetailText.IndexOf($PersonName, [StringComparison]::Ordinal) -ge 0 -or
        $DetailText.IndexOf($AssociationName, [StringComparison]::Ordinal) -ge 0
    $hasDocumentMarker = $DetailText -match '公众号|联系人|联系电话|会议|协会|来源|发布时间|阅读'
    return $hasTarget -and $hasDocumentMarker
}

function Test-DetailNeedsOcr {
    param([string]$DetailText, [string]$ResultText, [string]$AssociationName, [string]$PersonName)
    if (Test-ResultPageEvidence $DetailText $ResultText) { return $false }
    if ([string]::IsNullOrWhiteSpace($DetailText)) { return $true }
    $detailLines = @($DetailText -split '\r?\n' | ForEach-Object { $_.Trim() })
    foreach ($line in $detailLines) {
        if ($line.IndexOf($PersonName, [StringComparison]::Ordinal) -ge 0 -and
            @(Get-MobileCandidates $line).Count) {
            return $false
        }
    }
    $listMarkerCount = @(@('全部','文章','账号','相关搜索') | Where-Object {
        $detailLines -contains $_
    }).Count
    if ($listMarkerCount -ge 3) { return $false }
    # 已确认离开列表但没有可验证的“姓名+手机”文本时，允许 OCR。纯图片/PDF
    # 可能复制不到目标主体、PDF 字样或任何正文。
    return $true
}

function Get-ValidatedOcrText {
    param([object]$Result, [int]$ExpectedImageCount)
    if (-not $Result -or -not ($Result.ok -is [bool]) -or $Result.ok -ne $true -or
        -not ($Result.text -is [string]) -or
        -not ($Result.image_count -is [int]) -or
        [int]$Result.image_count -ne $ExpectedImageCount -or
        [string]::IsNullOrWhiteSpace([string]$Result.text) -or
        ([string]$Result.text).Length -gt 2000000) {
        throw 'OCR_FAILED'
    }
    [string]$Result.text
}

function Test-OcrEvidenceAttribution {
    param([string]$OcrText, [string]$AssociationName, [string]$PersonName)
    if ([string]::IsNullOrWhiteSpace($OcrText)) { return $false }
    $compactOcr = [regex]::Replace($OcrText, '\s+', '')
    $compactPerson = [regex]::Replace($PersonName, '\s+', '')
    return $compactOcr.IndexOf($compactPerson, [StringComparison]::Ordinal) -ge 0
}

function Test-NewOcrViewportHash {
    param([AllowEmptyCollection()][string[]]$ExistingHashes, [string]$CandidateHash)
    return -not [string]::IsNullOrWhiteSpace($CandidateHash) -and
        $ExistingHashes -notcontains $CandidateHash
}

function ConvertTo-MouseWheelData {
    param([int]$Delta)
    [BitConverter]::ToUInt32([BitConverter]::GetBytes([int32]$Delta), 0)
}

function ConvertTo-SafeDiagnosticRecords {
    param([AllowEmptyCollection()][object[]]$Records)
    @($Records | ForEach-Object {
        [pscustomobject]@{
            ordinal = if ($null -ne $_.ordinal) { [int]$_.ordinal } else { $null }
            stage = if ($_.stage) { [string]$_.stage } else { 'detail_judge' }
            reason = if ($_.reason) { [string]$_.reason } else { 'checked_before_failure' }
            text_length = if ($null -ne $_.text_length) {
                [int]$_.text_length
            } elseif ($_.text -is [string]) {
                ([string]$_.text).Length
            } else { 0 }
            detail_hash = if ($_.detail_hash) { [string]$_.detail_hash } else { $null }
            ocr_hashes = @($_.ocr_hashes | ForEach-Object { [string]$_ })
            ocr_region = if ($_.ocr_region) { [string]$_.ocr_region } else { $null }
        }
    })
}

function Test-ResultPageEvidence {
    param([string]$Text, [string]$OriginalResultText)
    if ([string]::IsNullOrWhiteSpace($Text) -or $Text.Length -lt 80) { return $false }
    if ([string]::IsNullOrWhiteSpace($OriginalResultText)) { return $false }
    if ([string]::Equals(
        $Text.Trim(), $OriginalResultText.Trim(), [StringComparison]::Ordinal)) {
        return $true
    }

    # 搜一搜会动态更新“最近读过”、时间和图片加载状态，不能要求全文逐字一致。
    # 同时验证列表固定栏目和多条原始长文本，避免把单篇详情误判为结果页。
    $navigationMarkers = @('全部', '文章', '账号', '相关搜索')
    $textLines = @($Text -split '\r?\n' | ForEach-Object { $_.Trim() })
    $markerMatches = @($navigationMarkers | Where-Object { $textLines -contains $_ }).Count
    if ($markerMatches -lt 3) { return $false }

    $originalLines = @($OriginalResultText -split '\r?\n' | ForEach-Object {
        $_.Trim()
    } | Where-Object {
        $_.Length -ge 12 -and $navigationMarkers -notcontains $_
    } | Select-Object -Unique)
    if ($originalLines.Count -lt 3) { return $false }
    $overlap = @($originalLines | Where-Object {
        $Text.IndexOf($_, [StringComparison]::Ordinal) -ge 0
    }).Count
    return $overlap -ge 3
}

function Test-SearchResultReady {
    param(
        [string]$Text,
        [string]$Query,
        [AllowNull()][string]$AssociationName,
        [AllowNull()][string]$PersonName,
        [bool]$InputVerified = $true
    )
    if (-not $InputVerified) { return $false }
    if ([string]::IsNullOrWhiteSpace($Text) -or $Text.Length -lt 80) {
        return $false
    }
    if (
        -not [string]::IsNullOrWhiteSpace($Query) -and
        [string]::Equals(
            $Text.Trim(), $Query.Trim(), [StringComparison]::Ordinal
        )
    ) {
        return $false
    }
    if (-not [string]::IsNullOrWhiteSpace($Query)) {
        # 微信可能把中文查询中的普通空格、全角空格或换行去掉；仅忽略排版
        # 空白，仍要求全部查询字符按原顺序出现在当前复制文本中。
        $normalizedText = [regex]::Replace($Text.Trim(), '\s+', '')
        $normalizedQuery = [regex]::Replace($Query.Trim(), '\s+', '')
        if (
            $normalizedText.IndexOf(
                $normalizedQuery, [StringComparison]::Ordinal
            ) -lt 0
        ) {
            return $false
        }
    }
    $normalizedReadyText = [regex]::Replace($Text.Trim(), '\s+', '')
    foreach ($requiredName in @($AssociationName, $PersonName)) {
        if (
            -not [string]::IsNullOrWhiteSpace($requiredName) -and
            $normalizedReadyText.IndexOf(
                ([regex]::Replace($requiredName.Trim(), '\s+', '')),
                [StringComparison]::Ordinal
            ) -lt 0
        ) { return $false }
    }
    # 搜一搜结果页的稳定栏目是比固定 sleep 更可靠的就绪信号；要求三个栏目，
    # 避免把搜索输入框、加载提示或单篇详情误判为结果列表。
    $navigationMarkers = @('全部', '文章', '账号', '相关搜索')
    $textLines = @($Text -split '\r?\n' | ForEach-Object { $_.Trim() })
    $markerMatches = @(
        $navigationMarkers | Where-Object { $textLines -contains $_ }
    ).Count
    return $markerMatches -ge 3
}

function Invoke-CollectFramework {
    param(
        [object[]]$Items, [int]$Limit, [string]$AssociationName, [string]$PersonName,
        [scriptblock]$OpenItem, [scriptblock]$ReadDetail, [scriptblock]$CloseDetail,
        [scriptblock]$Scroll, [AllowNull()][scriptblock]$Judge
    )
    $seen = @{}
    $checked = 0
    $evidenceItems = @()
    foreach ($item in $Items) {
        if ($checked -ge [math]::Min(10, $Limit)) { break }
        $key = "$($item.title)|$($item.source)|$($item.date)"
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        & $OpenItem $item
        try {
            $detail = [string](& $ReadDetail $item)
            $checked++
            $evidenceItems += [pscustomobject]@{
                title = [string]$item.title; source = [string]$item.source
                date = [string]$item.date; text = $detail
            }
            $result = Invoke-EvidenceJudge $detail $AssociationName $PersonName $Judge
            if ($result.inconclusive) {
                return [pscustomobject]@{
                    status = 'inconclusive'; checked = $checked
                    evidence_items = $evidenceItems; judge = $result
                }
            }
            if ($result.matched) {
                return [pscustomobject]@{
                    status = 'found'; checked = $checked; evidence_items = $evidenceItems; judge = $result
                }
            }
        } finally { & $CloseDetail }
        if ($item.scroll_after) { & $Scroll }
    }
    [pscustomobject]@{
        status = 'not_found'; checked = $checked; evidence_items = $evidenceItems; judge = $null
    }
}
