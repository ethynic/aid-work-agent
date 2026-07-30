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

function Close-WeixinPluginSession {
    param(
        [Parameter(Mandatory)][int64]$PluginHwnd,
        [Parameter(Mandatory)][int64]$MainHwnd,
        [Parameter(Mandatory)][scriptblock]$GetWindowIdentity,
        [Parameter(Mandatory)][scriptblock]$IsWindow,
        [Parameter(Mandatory)][scriptblock]$IsWindowVisible,
        [Parameter(Mandatory)][scriptblock]$GetForegroundHwnd,
        [Parameter(Mandatory)][scriptblock]$ActivateWindow,
        [Parameter(Mandatory)][scriptblock]$KeyEvent,
        [Parameter(Mandatory)][scriptblock]$Pause,
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
                if (-not (& $ActivateWindow $PluginHwnd)) { throw 'PLUGIN_ACTIVATION_FAILED' }
                $pluginGuard = {
                    if ([int64](& $GetForegroundHwnd) -ne $PluginHwnd) { return $false }
                    $identity = & $GetWindowIdentity $PluginHwnd
                    return $null -ne $identity -and
                        [int64]$identity.Hwnd -eq $PluginHwnd -and
                        (Test-WeixinForegroundIdentity $identity $MainHwnd)
                }
                Invoke-SafeKeyChord @('CTRL','W') $KeyEvent $Pause $pluginGuard
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
        if (
            -not $mainIdentity -or
            [int64]$mainIdentity.Hwnd -ne $MainHwnd -or
            -not (Test-WeixinExecutablePath ([string]$mainIdentity.ProcessPath))
        ) {
            throw 'MAIN_WINDOW_UNTRUSTED'
        }
        if (-not (& $ActivateWindow $MainHwnd)) { throw 'MAIN_ACTIVATION_FAILED' }
        if ([int64](& $GetForegroundHwnd) -ne $MainHwnd) {
            throw 'MAIN_FOREGROUND_NOT_RESTORED'
        }
        return [pscustomobject]@{ session_closed=$true; main_hwnd=$MainHwnd }
    } catch {
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

function Complete-WeixinLayeredSession {
    param(
        [Parameter(Mandatory)][ref]$DetailMayBeOpen,
        [Parameter(Mandatory)][ref]$Completed,
        [Parameter(Mandatory)][scriptblock]$CloseDetail,
        [Parameter(Mandatory)][scriptblock]$VerifyResultPage,
        [Parameter(Mandatory)][scriptblock]$CleanupPlugin
    )
    if ([bool]$Completed.Value) {
        return [pscustomobject]@{ session_closed=$true; already_closed=$true }
    }
    if ([bool]$DetailMayBeOpen.Value) {
        & $CloseDetail
        if (-not [bool](& $VerifyResultPage)) {
            throw 'SESSION_CLEANUP_FAILED'
        }
        $DetailMayBeOpen.Value = $false
    }
    $result = Complete-WeixinPluginSession $Completed $CleanupPlugin
    return $result
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
    param([string]$AssociationName, [string]$PersonName)
    $association = $AssociationName.Trim()
    $person = $PersonName.Trim()
    if (-not $association -or -not $person) { throw 'INVALID_INPUT' }
    return "$association $person 联系人"
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
    if ($Evidence.IndexOf($PersonName, [StringComparison]::Ordinal) -lt 0) { return $false }
    if ((Get-MobileCandidates $Evidence) -notcontains $mobile) { return $false }
    $quote = [string]$Result.evidence_quote
    return -not [string]::IsNullOrWhiteSpace($quote) -and
        $quote.IndexOf($PersonName, [StringComparison]::Ordinal) -ge 0 -and
        $quote.IndexOf($mobile, [StringComparison]::Ordinal) -ge 0 -and
        $Evidence.IndexOf($quote, [StringComparison]::Ordinal) -ge 0
}

function Invoke-DeterministicJudge {
    param([string]$Evidence, [string]$PersonName)
    if (-not @(Get-MobileCandidates $Evidence).Count -or $Evidence.IndexOf($PersonName, [StringComparison]::Ordinal) -lt 0) {
        return [pscustomobject]@{ matched = $false; reason = 'name_or_mobile_absent' }
    }
    foreach ($line in ($Evidence -split '\r?\n')) {
        if ($line.IndexOf($PersonName, [StringComparison]::Ordinal) -lt 0) { continue }
        foreach ($number in @(Get-MobileCandidates $line)) {
            if ($line.Length -le 160) {
                return [pscustomobject]@{
                    matched = $true; person_name = $PersonName; mobile = $number
                    evidence_quote = $line; confidence = 1.0; reason = 'deterministic_same_line'
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
    if (-not @(Get-MobileCandidates $Evidence).Count) {
        return [pscustomobject]@{ matched = $false; reason = 'no_mobile_candidate' }
    }
    try {
        $result = if ($Judge) { & $Judge ([pscustomobject]@{
            association_name = $AssociationName; person_name = $PersonName; text = $Evidence
        }) } else { Invoke-DeterministicJudge $Evidence $PersonName }
    } catch {
        return [pscustomobject]@{ matched = $false; inconclusive = $true; reason = 'judge_failed' }
    }
    if ($result.inconclusive -eq $true) {
        return [pscustomobject]@{ matched = $false; inconclusive = $true; reason = 'judge_failed' }
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
            if ($process.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($line)) { throw 'JUDGE_FAILED' }
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

function Get-BitmapRegionSha256 {
    param(
        [Parameter(Mandatory)][System.Drawing.Bitmap]$Bitmap,
        [Parameter(Mandatory)][Drawing.Rectangle]$Region
    )
    if ($Region.Width -le 0 -or $Region.Height -le 0 -or
        $Region.Left -lt 0 -or $Region.Top -lt 0 -or
        $Region.Right -gt $Bitmap.Width -or $Region.Bottom -gt $Bitmap.Height) {
        throw 'INVALID_BITMAP_REGION'
    }
    $crop = $Bitmap.Clone($Region, $Bitmap.PixelFormat)
    try { Get-BitmapSha256 $crop } finally { $crop.Dispose() }
}

function Find-DarkThemeCardBands {
    param(
        [Parameter(Mandatory)][System.Drawing.Bitmap]$Bitmap,
        [double]$LeftRatio = 0.04, [double]$RightRatio = 0.72,
        [double]$TopRatio = 0.10, [double]$BottomRatio = 0.94
    )
    if ($Bitmap.Width -lt 400 -or $Bitmap.Height -lt 300) { return @() }
    $left = [int]($Bitmap.Width * $LeftRatio)
    $right = [int]($Bitmap.Width * $RightRatio)
    $top = [int]($Bitmap.Height * $TopRatio)
    $bottom = [int]($Bitmap.Height * $BottomRatio)
    if ($right -le $left -or $bottom -le $top) { return @() }

    # 暗色主题限定：右上角与左侧边缘的中位亮度必须低，浅色主题直接 fail closed。
    $samples = @()
    foreach ($point in @(
        @(($left + 2), ($top + 2)), @(($right - 2), ($top + 2)),
        @(($left + 2), ($bottom - 2)), @(($right - 2), ($bottom - 2))
    )) {
        $color = $Bitmap.GetPixel($point[0], $point[1])
        $samples += [int](($color.R + $color.G + $color.B) / 3)
    }
    $sorted = @($samples | Sort-Object)
    if ($sorted[2] -gt 125) { return @() }

    $activeRows = New-Object Collections.Generic.List[int]
    $stepX = [math]::Max(2, [int](($right - $left) / 180))
    for ($y = $top; $y -lt $bottom; $y += 2) {
        $different = 0
        $total = 0
        $background = $Bitmap.GetPixel($right - 2, $y)
        $backgroundBrightness = [int](($background.R + $background.G + $background.B) / 3)
        for ($x = $left; $x -lt ($right - 8); $x += $stepX) {
            $color = $Bitmap.GetPixel($x, $y)
            $brightness = [int](($color.R + $color.G + $color.B) / 3)
            if ([math]::Abs($brightness - $backgroundBrightness) -ge 10) { $different++ }
            $total++
        }
        if ($total -and ($different / $total) -ge 0.42) { $activeRows.Add($y) }
    }
    if (-not $activeRows.Count) { return @() }

    $bands = @()
    $start = $activeRows[0]
    $previous = $start
    foreach ($row in @($activeRows | Select-Object -Skip 1)) {
        if (($row - $previous) -gt 8) {
            $height = $previous - $start
            if ($height -ge 32 -and $height -le ($Bitmap.Height * 0.35) -and $start -gt ($top + 8)) {
                $bands += ,@($start, $previous)
            }
            $start = $row
        }
        $previous = $row
    }
    $height = $previous - $start
    if ($height -ge 32 -and $height -le ($Bitmap.Height * 0.35) -and $start -gt ($top + 8)) {
        $bands += ,@($start, $previous)
    }

    @($bands | ForEach-Object {
        $bandTop = [int]$_[0]
        $bandBottom = [int]$_[1]
        [pscustomobject]@{
            top = $bandTop
            bottom = $bandBottom
            # 标题文字位于卡片内容列，不在卡片左侧缩略图/留白区。结果区为
            # 0.04..0.72，取其 38% 得到窗口 x≈0.30；随窗口宽度缩放。
            x_ratio = [math]::Round(($left + (($right - $left) * 0.38)) / $Bitmap.Width, 5)
            y_ratio = [math]::Round(($bandTop + [math]::Min(28, ($bandBottom - $bandTop) * 0.25)) / $Bitmap.Height, 5)
            confidence = 0.8
            fingerprint = Get-BitmapRegionSha256 $Bitmap (New-Object Drawing.Rectangle(
                $left, $bandTop, ($right - $left), ($bandBottom - $bandTop + 1)))
        }
    })
}

function Resolve-LocatorFilePath {
    param([string]$Path, [string]$ClientRoot, [string]$CurrentDirectory = (Get-Location).Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'LOCATOR_READ_FAILED' }
    if ($Path.Trim().StartsWith('\\', [StringComparison]::Ordinal)) {
        # 禁止 UNC/设备路径，避免 locator 解析触发网络访问或绕过本地根目录判断。
        throw 'LOCATOR_READ_FAILED'
    }
    $candidates = if ([IO.Path]::IsPathRooted($Path)) {
        @([pscustomobject]@{ path=$Path; root=$null })
    } else {
        @(
            [pscustomobject]@{ path=(Join-Path $CurrentDirectory $Path); root=$CurrentDirectory },
            [pscustomobject]@{ path=(Join-Path $ClientRoot $Path); root=$ClientRoot }
        )
    }
    foreach ($candidate in $candidates) {
        $fullPath = [IO.Path]::GetFullPath([string]$candidate.path)
        if ($candidate.root) {
            $rootPath = [IO.Path]::GetFullPath([string]$candidate.root).TrimEnd('\') + '\'
            if (-not $fullPath.StartsWith($rootPath, [StringComparison]::OrdinalIgnoreCase)) {
                continue
            }
        }
        if ([IO.Path]::GetExtension($fullPath) -ne '.json' -or
            -not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
            continue
        }
        $resolved = (Resolve-Path -LiteralPath $fullPath -ErrorAction Stop).Path
        if ($candidate.root -and
            -not $resolved.StartsWith($rootPath, [StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        return $resolved
    }
    throw 'LOCATOR_READ_FAILED'
}

function Get-ValidatedLocatorPoints {
    param([object]$Locator)
    if (-not $Locator -or @($Locator.PSObject.Properties.Name).Count -ne 1 -or
        @($Locator.PSObject.Properties.Name) -notcontains 'items') {
        throw 'LOCATOR_POINTS_INVALID'
    }
    $items = @($Locator.items)
    if ($items.Count -lt 1 -or $items.Count -gt 10) { throw 'LOCATOR_POINTS_INVALID' }
    foreach ($item in $items) {
        if (-not $item) { throw 'LOCATOR_POINTS_INVALID' }
        $propertyNames = @($item.PSObject.Properties.Name)
        $allowedProperties = @('title','source','date','x_ratio','y_ratio','scroll_after')
        if ($propertyNames -notcontains 'title' -or
            $propertyNames -notcontains 'x_ratio' -or
            $propertyNames -notcontains 'y_ratio' -or
            @($propertyNames | Where-Object { $allowedProperties -notcontains $_ }).Count) {
            throw 'LOCATOR_POINTS_INVALID'
        }
        if (-not ($item.title -is [string]) -or
            [string]::IsNullOrWhiteSpace([string]$item.title) -or
            ([string]$item.title).Length -gt 512) {
            throw 'LOCATOR_POINTS_INVALID'
        }
        foreach ($optionalText in @('source','date')) {
            if ($propertyNames -contains $optionalText -and
                (-not ($item.$optionalText -is [string]) -or
                 ([string]$item.$optionalText).Length -gt 256)) {
                throw 'LOCATOR_POINTS_INVALID'
            }
        }
        if ($propertyNames -contains 'scroll_after' -and
            -not ($item.scroll_after -is [bool])) {
            throw 'LOCATOR_POINTS_INVALID'
        }
        $xNumeric = $item.x_ratio -is [byte] -or $item.x_ratio -is [sbyte] -or
            $item.x_ratio -is [int16] -or $item.x_ratio -is [uint16] -or
            $item.x_ratio -is [int32] -or $item.x_ratio -is [uint32] -or
            $item.x_ratio -is [int64] -or $item.x_ratio -is [uint64] -or
            $item.x_ratio -is [single] -or $item.x_ratio -is [double] -or
            $item.x_ratio -is [decimal]
        $yNumeric = $item.y_ratio -is [byte] -or $item.y_ratio -is [sbyte] -or
            $item.y_ratio -is [int16] -or $item.y_ratio -is [uint16] -or
            $item.y_ratio -is [int32] -or $item.y_ratio -is [uint32] -or
            $item.y_ratio -is [int64] -or $item.y_ratio -is [uint64] -or
            $item.y_ratio -is [single] -or $item.y_ratio -is [double] -or
            $item.y_ratio -is [decimal]
        if (-not $xNumeric -or -not $yNumeric) {
            throw 'LOCATOR_POINTS_INVALID'
        }
        $x = [double]$item.x_ratio
        $y = [double]$item.y_ratio
        if ([double]::IsNaN($x) -or [double]::IsInfinity($x) -or
            [double]::IsNaN($y) -or [double]::IsInfinity($y) -or
            $x -lt 0.28 -or $x -gt 0.68 -or $y -lt 0.08 -or $y -gt 0.95) {
            throw 'LOCATOR_POINTS_INVALID'
        }
    }
    $items
}

function Read-LocatorJson {
    param([Parameter(Mandatory)][string]$Path)
    try {
        $file = Get-Item -LiteralPath $Path -ErrorAction Stop
        if ($file.Length -le 0 -or $file.Length -gt 65536) { throw 'LOCATOR_READ_FAILED' }
        [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8) | ConvertFrom-Json
    }
    catch { throw 'LOCATOR_READ_FAILED' }
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
    return -not [string]::IsNullOrWhiteSpace($OcrText) -and
        $OcrText.IndexOf($AssociationName, [StringComparison]::Ordinal) -ge 0 -and
        $OcrText.IndexOf($PersonName, [StringComparison]::Ordinal) -ge 0
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
    param([string]$Text, [string]$Query)
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
