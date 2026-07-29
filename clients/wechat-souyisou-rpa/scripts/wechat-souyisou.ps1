[CmdletBinding()]
param(
    [ValidateSet('probe','open','search','collect')][string]$Command = 'probe',
    [string]$AssociationName,
    [string]$PersonName,
    [string]$InputJson,
    [switch]$ReadStdin,
    [switch]$Execute,
    [ValidateRange(1,10)][int]$Limit = 10,
    [string]$LocatorPath,
    [string]$JudgeCommand,
    [switch]$UseProjectLlm,
    [string]$OcrCommand,
    [switch]$DisableOcr,
    [string]$ArtifactDirectory = (Join-Path $env:LOCALAPPDATA 'AidWorkAgent\wechat-souyisou-rpa\artifacts'),
    [ValidateRange(500,30000)][int]$WaitMilliseconds = 2500
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot 'wechat-souyisou-lib.ps1')

function Write-Result([hashtable]$Value) {
    [Console]::Out.WriteLine(($Value | ConvertTo-Json -Depth 12 -Compress))
}
function Fail([string]$Code, [string]$Message, [AllowNull()][string]$ArtifactRef) {
    $result = @{
        ok=$false; executed=[bool]$Execute; error_code=$Code
        message=(Get-RedactedSummary $Message)
    }
    if ($ArtifactRef) { $result.artifact_ref = $ArtifactRef }
    Write-Result $result
    exit 1
}

try {
    $stage = 'enum'
    $raw = $InputJson
    if ($ReadStdin) { $raw = [Console]::In.ReadToEnd() }
    if ($raw) {
        $inputObject = $raw | ConvertFrom-Json
        if ($inputObject.command) { $Command = [string]$inputObject.command }
        if ($inputObject.association_name) { $AssociationName = [string]$inputObject.association_name }
        if ($inputObject.person_name) { $PersonName = [string]$inputObject.person_name }
        if ($inputObject.limit) { $Limit = [math]::Min(10, [int]$inputObject.limit) }
    }
    if ($Command -notin @('probe','open','search','collect')) { throw 'INVALID_COMMAND' }
    if ($Limit -lt 1 -or $Limit -gt 10) { throw 'INVALID_LIMIT' }
    if ($Command -in @('search','collect')) { $query = New-SearchQuery $AssociationName $PersonName }
    if (-not $Execute) {
        Write-Result @{
            ok=$true; executed=$false; mode='dry_run'; command=$Command
            query=if ($query) { $query } else { $null }; limit=$Limit
        }
        exit 0
    }

    $createdNew = $false
    $mutex = New-Object Threading.Mutex($false, 'Local\AidWorkAgent.WechatSouyisouRpa', [ref]$createdNew)
    if (-not $mutex.WaitOne(0)) { throw 'RPA_BUSY' }
    try {
        [void](Add-Type -AssemblyName System.Windows.Forms)
        [void](Add-Type -AssemblyName System.Drawing)
        [void](Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class WechatSouyisouWin32 {
 public delegate bool EnumWindowsProc(IntPtr h, IntPtr p);
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
 [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h,System.Text.StringBuilder b,int n);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,System.Text.StringBuilder b,int n);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
 [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr SetActiveWindow(IntPtr h);
 [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
 [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a,uint b,bool v);
 [DllImport("user32.dll")] public static extern void keybd_event(byte k,byte s,uint f,IntPtr e);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,IntPtr e);
 public struct RECT { public int Left,Top,Right,Bottom; }
}
"@)
        $windows = @()
        [WechatSouyisouWin32]::EnumWindows({
            param($h,$unused)
            if ([WechatSouyisouWin32]::IsWindowVisible($h)) {
                $windowProcessId = [uint32]0
                [void][WechatSouyisouWin32]::GetWindowThreadProcessId($h,[ref]$windowProcessId)
                try {
                    $p = Get-Process -Id $windowProcessId -ErrorAction Stop
                    $script:windows += [pscustomobject]@{ Hwnd=$h.ToInt64(); MainWindowHwnd=$p.MainWindowHandle.ToInt64(); Visible=$true; ProcessPath=$p.Path }
                } catch {}
            }; return $true
        },[IntPtr]::Zero) | Out-Null
        $stage = 'enum'
        $main = Select-WeixinMainWindow $windows
        if ($Command -eq 'probe') {
            Write-Result @{ ok=$true; executed=$true; command='probe'; window_found=$true; hwnd=[int64]$main.Hwnd }
            exit 0
        }
        $getThread = {
            param($h)
            $windowProcessId = [uint32]0
            [WechatSouyisouWin32]::GetWindowThreadProcessId(
                [IntPtr]$h, [ref]$windowProcessId
            )
        }
        $activateWindow = {
            param($targetHwnd)
            Invoke-WeixinActivation ([int64]$targetHwnd) `
                { [WechatSouyisouWin32]::GetCurrentThreadId() } $getThread `
                { [WechatSouyisouWin32]::GetForegroundWindow().ToInt64() } `
                { param($a,$b,$v) [WechatSouyisouWin32]::AttachThreadInput($a,$b,$v) } `
                { param($h) [WechatSouyisouWin32]::ShowWindow([IntPtr]$h,9) } `
                { param($h) [WechatSouyisouWin32]::BringWindowToTop([IntPtr]$h) } `
                { param($h) [WechatSouyisouWin32]::SetActiveWindow([IntPtr]$h) } `
                { param($h) [WechatSouyisouWin32]::SetForegroundWindow([IntPtr]$h) } `
                { param($ms) Start-Sleep -Milliseconds $ms }
        }
        $stage = 'activate'
        $activated = & $activateWindow ([int64]$main.Hwnd)
        if (-not $activated) { throw 'WX_ACTIVATION_FAILED' }
        function Get-WindowIdentityByHwnd([int64]$Hwnd) {
            if (-not [WechatSouyisouWin32]::IsWindow([IntPtr]$Hwnd)) { return $null }
            $currentPid = [uint32]0
            [void][WechatSouyisouWin32]::GetWindowThreadProcessId([IntPtr]$Hwnd,[ref]$currentPid)
            try { $currentProcess = Get-Process -Id $currentPid -ErrorAction Stop } catch { return $null }
            $currentClass = New-Object Text.StringBuilder 256
            $currentTitle = New-Object Text.StringBuilder 256
            [void][WechatSouyisouWin32]::GetClassName([IntPtr]$Hwnd,$currentClass,$currentClass.Capacity)
            [void][WechatSouyisouWin32]::GetWindowText([IntPtr]$Hwnd,$currentTitle,$currentTitle.Capacity)
            [pscustomobject]@{
                Hwnd=$Hwnd; ProcessPath=$currentProcess.Path
                ClassName=$currentClass.ToString(); Title=$currentTitle.ToString()
            }
        }
        function Get-TrustedForegroundIdentity([switch]$RequirePlugin) {
            $currentHwnd = [WechatSouyisouWin32]::GetForegroundWindow()
            if ($currentHwnd -eq [IntPtr]::Zero) { return $null }
            $identity = Get-WindowIdentityByHwnd $currentHwnd.ToInt64()
            if (-not $identity) { return $null }
            if (-not (Test-WeixinForegroundIdentity $identity ([int64]$main.Hwnd))) { return $null }
            if ($RequirePlugin -and [int64]$identity.Hwnd -eq [int64]$main.Hwnd) { return $null }
            $identity
        }
        if ($Command -eq 'open') {
            $stage = 'open'
            $virtualKeyMap = @{CTRL=0x11;F=0x46;DOWN=0x28;ENTER=0x0D;A=0x41;C=0x43;V=0x56;W=0x57}
            $mainGuard = { [WechatSouyisouWin32]::GetForegroundWindow().ToInt64() -eq [int64]$main.Hwnd }
            $send = { param($ks) Invoke-SafeKeyChord $ks {param($k,$up) [WechatSouyisouWin32]::keybd_event($virtualKeyMap[$k],0, $(if($up){2}else{0}),[IntPtr]::Zero)} {Start-Sleep -Milliseconds 40} $mainGuard }
            & $send @('CTRL','F'); Start-Sleep -Milliseconds 400
            & $send @('DOWN'); Start-Sleep -Milliseconds 250
            & $send @('ENTER'); Start-Sleep -Milliseconds $WaitMilliseconds
            if (-not (Get-TrustedForegroundIdentity -RequirePlugin)) { throw 'SOUYISOU_WINDOW_UNTRUSTED' }
            Write-Result @{ ok=$true; executed=$true; command='open' }
            exit 0
        }
        # search/collect 从主窗口重新打开搜一搜，避免依赖未知页面状态。
        $virtualKeyMap = @{CTRL=0x11;F=0x46;DOWN=0x28;ENTER=0x0D;A=0x41;C=0x43;V=0x56;W=0x57}
        $mainGuard = { [WechatSouyisouWin32]::GetForegroundWindow().ToInt64() -eq [int64]$main.Hwnd }
        $send = { param($ks,$guard) Invoke-SafeKeyChord $ks {param($k,$up) [WechatSouyisouWin32]::keybd_event($virtualKeyMap[$k],0,$(if($up){2}else{0}),[IntPtr]::Zero)} {Start-Sleep -Milliseconds 40} $guard }
        & $send @('CTRL','F') $mainGuard; Start-Sleep -Milliseconds 400; & $send @('DOWN') $mainGuard; Start-Sleep -Milliseconds 250
        & $send @('ENTER') $mainGuard; Start-Sleep -Milliseconds $WaitMilliseconds
        $pluginIdentity = Get-TrustedForegroundIdentity -RequirePlugin
        if (-not $pluginIdentity) { throw 'SOUYISOU_WINDOW_UNTRUSTED' }
        $pluginHwnd = [IntPtr]$pluginIdentity.Hwnd
        $requiresSessionCleanup = $true
        $sessionCleanupCompleted = $false
        $sessionCleanupAttempted = $false
        $pluginGuard = {
            $identity = Get-TrustedForegroundIdentity -RequirePlugin
            $null -ne $identity -and [int64]$identity.Hwnd -eq $pluginHwnd.ToInt64()
        }
        $cleanupSession = {
            Close-WeixinPluginSession $pluginHwnd.ToInt64() ([int64]$main.Hwnd) `
                { param($h) Get-WindowIdentityByHwnd $h } `
                { param($h) [WechatSouyisouWin32]::IsWindow([IntPtr]$h) } `
                { param($h) [WechatSouyisouWin32]::IsWindowVisible([IntPtr]$h) } `
                { [WechatSouyisouWin32]::GetForegroundWindow().ToInt64() } `
                $activateWindow `
                { param($key,$up)
                    [WechatSouyisouWin32]::keybd_event(
                        $virtualKeyMap[$key],0,$(if($up){2}else{0}),[IntPtr]::Zero)
                } `
                { Start-Sleep -Milliseconds 40 } `
                { param($ms) Start-Sleep -Milliseconds $ms }
        }
        $originalClipboard = $null
        $clipboardCaptured = $false
        try {
            $originalClipboard = [Windows.Forms.Clipboard]::GetDataObject()
            $clipboardCaptured = $true
        } catch { throw 'CLIPBOARD_CAPTURE_FAILED' }
        $stage = 'search'
        [Windows.Forms.Clipboard]::SetText($query)
        & $send @('CTRL','A') $pluginGuard; & $send @('CTRL','V') $pluginGuard; & $send @('ENTER') $pluginGuard; Start-Sleep -Milliseconds $WaitMilliseconds
        [Windows.Forms.Clipboard]::Clear()
        $stage = 'copy'
        & $send @('CTRL','A') $pluginGuard; & $send @('CTRL','C') $pluginGuard; Start-Sleep -Milliseconds 250
        $text = [Windows.Forms.Clipboard]::GetText([Windows.Forms.TextDataFormat]::UnicodeText)
        $html = [Windows.Forms.Clipboard]::GetText([Windows.Forms.TextDataFormat]::Html)
        if ([string]::IsNullOrWhiteSpace($text)) { throw 'RESULT_TEXT_EMPTY' }
        $links = @(Get-CfHtmlLinks $html)
        $judge = $null
        if ($UseProjectLlm) {
            $judge = New-ExternalJudge (Get-Command python.exe -ErrorAction Stop).Source `
                @((Join-Path $PSScriptRoot 'llm_judge.py'))
        } elseif ($JudgeCommand) {
            $judge = New-ExternalJudge $JudgeCommand
        }
        # 保留阶段名用于兼容诊断协议；本阶段仅封存无边界列表，不做命中判断。
        $stage = 'list_judge'
        $artifact = Protect-EvidenceArtifact $ArtifactDirectory @{
            kind='result_page_unbounded'; association_name=$AssociationName; person_name=$PersonName
            text=$text; links=$links; captured_at=[DateTimeOffset]::Now.ToString('o')
        }
        if ($Command -eq 'search') {
            $stage = 'cleanup'
            $sessionCleanupAttempted = $true
            $cleanupResult = Complete-WeixinPluginSession `
                ([ref]$sessionCleanupCompleted) $cleanupSession
            Write-Result @{
                ok=$true; executed=$true; status='captured'
                source='result_page_unbounded'; artifact_ref=$artifact.artifact_ref
                link_count=$links.Count; session_closed=[bool]$cleanupResult.session_closed
            }
            exit 0
        }
        $ocr = $null
        if (-not $DisableOcr) {
            if ($OcrCommand) {
                $ocr = New-ExternalJudge $OcrCommand
            } else {
                $ocr = New-ExternalJudge (Get-Command python.exe -ErrorAction Stop).Source `
                    @((Join-Path $PSScriptRoot 'ocr_adapter.py'))
            }
        }
        $stage = 'locate'
        $stage = 'rect'
        if ([WechatSouyisouWin32]::GetForegroundWindow() -ne $pluginHwnd) { throw 'FOREGROUND_LOST' }
        $rect = New-Object WechatSouyisouWin32+RECT
        if (-not [WechatSouyisouWin32]::GetWindowRect($pluginHwnd,[ref]$rect)) { throw 'WINDOW_RECT_FAILED' }
        $width = $rect.Right - $rect.Left
        $height = $rect.Bottom - $rect.Top
        if (-not (Test-WindowRectDimensions $width $height)) { throw 'WINDOW_RECT_FAILED' }
        $capture = {
            $bitmap = New-Object Drawing.Bitmap $width, $height
            $graphics = [Drawing.Graphics]::FromImage($bitmap)
            try { $graphics.CopyFromScreen($rect.Left,$rect.Top,0,0,$bitmap.Size) }
            finally { $graphics.Dispose() }
            $bitmap
        }
        $explicitItems = $null
        if ($LocatorPath) {
            $stage = 'locator_read'
            $clientRoot = Split-Path -Parent $PSScriptRoot
            $resolvedLocatorPath = Resolve-LocatorFilePath $LocatorPath $clientRoot
            $locator = Read-LocatorJson $resolvedLocatorPath
            $stage = 'points'
            $explicitItems = @(Get-ValidatedLocatorPoints $locator)
        }
        $checked=0; $failures=0; $consecutiveFailures=0; $scrolls=0
        $seen=@{}; $seenDetailText=@{}; $records=@(); $foundJudge=$null
        $detailMayBeOpen=$false
        while ($checked -lt $Limit -and $scrolls -le 6 -and $consecutiveFailures -lt 2) {
            if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
            $viewport = & $capture
            try {
                $viewportHash = Get-BitmapSha256 $viewport
                $points = @(if ($explicitItems) { $explicitItems } else { @(Find-DarkThemeCardBands $viewport) })
            } finally { $viewport.Dispose() }
            if (-not $points.Count) {
                $stage = 'points'
                throw 'CARD_LOCATE_FAILED'
            }
            $newCount = 0
            foreach ($point in $points) {
                if ($checked -ge $Limit -or $consecutiveFailures -ge 2) { break }
                $pointKey = if ($point.fingerprint) {
                    [string]$point.fingerprint
                } elseif ($point.title -or $point.source -or $point.date) {
                    "$($point.title)|$($point.source)|$($point.date)"
                } else {
                    "$viewportHash|$($point.x_ratio)|$($point.y_ratio)"
                }
                if ($seen.ContainsKey($pointKey)) { continue }
                $seen[$pointKey]=$true; $newCount++
                $stage = 'click'
                $x=$rect.Left+[int]($width*[double]$point.x_ratio)
                $y=$rect.Top+[int]($height*[double]$point.y_ratio)
                if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
                [void][WechatSouyisouWin32]::SetCursorPos($x,$y)
                Start-Sleep -Milliseconds 100
                if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
                $before = & $capture
                try { $beforeHash=Get-BitmapSha256 $before } finally { $before.Dispose() }
                Invoke-SafeMouseClick {
                    param($up)
                    [WechatSouyisouWin32]::mouse_event($(if($up){4}else{2}),0,0,0,[IntPtr]::Zero)
                } $pluginGuard
                Start-Sleep -Milliseconds $WaitMilliseconds
                if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
                $after = & $capture
                try { $afterHash=Get-BitmapSha256 $after } finally { $after.Dispose() }
                if ($afterHash -eq $beforeHash) {
                    # 无法证明仍在列表页；停止后续点击，避免在未知页面状态继续注入输入。
                    $records += [pscustomobject]@{
                        stage='click';reason='screenshot_unchanged';text_length=0
                        before_hash=$beforeHash;after_hash=$afterHash
                    }
                    $failures++; $consecutiveFailures=2
                    break
                }
                $detailMayBeOpen=$true
                $stage = 'detail_copy'
                [Windows.Forms.Clipboard]::Clear()
                & $send @('CTRL','A') $pluginGuard; & $send @('CTRL','C') $pluginGuard
                Start-Sleep -Milliseconds 200
                $detail=[Windows.Forms.Clipboard]::GetText([Windows.Forms.TextDataFormat]::UnicodeText)
                $checked++
                $ocrHashes=@()
                $ocrText=$null
                $needsOcr = Test-DetailNeedsOcr $detail $text $AssociationName $PersonName
                if ($needsOcr) {
                    if (-not $ocr) {
                        $records += [pscustomobject]@{
                            ordinal=$checked;stage='detail_copy';reason='ocr_unavailable'
                            text=$detail;text_length=$detail.Length;detail_hash=$afterHash
                        }
                        $failures++; $consecutiveFailures=2
                    } else {
                        $temporaryImages=@()
                        try {
                            for ($ocrIndex=0; $ocrIndex -lt 3; $ocrIndex++) {
                                if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
                                $ocrBitmap=& $capture
                                $ocrEvidenceBitmap=$null
                                try {
                                    # 当前微信详情为整页打开，只截取中央正文/PDF区域。
                                    # 结果列表已由 Test-ResultPageEvidence 在进入 OCR 前排除。
                                    $ocrRegion=New-Object Drawing.Rectangle(
                                        [int]($width*0.26),[int]($height*0.08),
                                        [int]($width*0.48),[int]($height*0.87))
                                    $ocrEvidenceBitmap=$ocrBitmap.Clone(
                                        $ocrRegion,$ocrBitmap.PixelFormat)
                                    $candidateOcrHash=Get-BitmapSha256 $ocrEvidenceBitmap
                                    if (-not (Test-NewOcrViewportHash $ocrHashes $candidateOcrHash)) { break }
                                    $ocrHashes += $candidateOcrHash
                                    $temporaryPath=Join-Path ([IO.Path]::GetTempPath()) (
                                        'wechat-ocr-' + [guid]::NewGuid().ToString('N') + '.png')
                                    $temporaryImages += $temporaryPath
                                    $ocrEvidenceBitmap.Save(
                                        $temporaryPath,[Drawing.Imaging.ImageFormat]::Png)
                                } finally {
                                    if ($ocrEvidenceBitmap) { $ocrEvidenceBitmap.Dispose() }
                                    $ocrBitmap.Dispose()
                                }
                                if ($ocrIndex -lt 2) {
                                    if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
                                    [void][WechatSouyisouWin32]::SetCursorPos(
                                        $rect.Left+[int]($width*0.50),$rect.Top+[int]($height*0.72))
                                    if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
                                    [WechatSouyisouWin32]::mouse_event(
                                        0x0800,0,0,(ConvertTo-MouseWheelData -480),[IntPtr]::Zero)
                                    Start-Sleep -Milliseconds 500
                                }
                            }
                            $ocrResult=& $ocr ([pscustomobject]@{image_paths=$temporaryImages})
                            $ocrText=Get-ValidatedOcrText $ocrResult $temporaryImages.Count
                            if (-not (Test-OcrEvidenceAttribution `
                                $ocrText $AssociationName $PersonName)) {
                                throw 'OCR_ATTRIBUTION_FAILED'
                            }
                            $detail=($detail + "`n`n" + $ocrText).Trim()
                        } catch {
                            if ($_.Exception.Message -eq 'FOREGROUND_LOST') { throw }
                            $records += [pscustomobject]@{
                                ordinal=$checked;stage='detail_copy';reason='ocr_failed'
                                text=$detail;ocr_text=$ocrText
                                text_length=$detail.Length;detail_hash=$afterHash
                                ocr_hashes=$ocrHashes
                                ocr_region='center_detail_0.26_0.08_0.74_0.95'
                            }
                            $failures++; $consecutiveFailures=2
                        } finally {
                            $temporaryCleanupFailed=$false
                            foreach($temporaryImage in $temporaryImages) {
                                try { Remove-Item -LiteralPath $temporaryImage -Force -ErrorAction Stop }
                                catch { $temporaryCleanupFailed=$true }
                            }
                            if ($temporaryCleanupFailed) { throw 'TEMP_CLEANUP_FAILED' }
                        }
                    }
                }
                if ($consecutiveFailures -ge 2) {
                    $stage='close'
                    & $send @('CTRL','W') $pluginGuard
                    Start-Sleep -Milliseconds ([math]::Min(2000,[math]::Max(800,$WaitMilliseconds)))
                    $stage='recover'
                    [Windows.Forms.Clipboard]::Clear()
                    & $send @('CTRL','A') $pluginGuard; & $send @('CTRL','C') $pluginGuard
                    Start-Sleep -Milliseconds 150
                    $returned=[Windows.Forms.Clipboard]::GetText([Windows.Forms.TextDataFormat]::UnicodeText)
                    if (-not (Test-ResultPageEvidence $returned $text)) { throw 'RECOVERY_FAILED' }
                    $detailMayBeOpen=$false
                    break
                }
                if (-not (Test-DetailEvidence $detail $text $AssociationName $PersonName)) {
                    $records += [pscustomobject]@{
                        ordinal=$checked;stage='detail_copy';reason='detail_evidence_invalid'
                        text=$detail;ocr_text=$ocrText
                        text_length=$detail.Length;detail_hash=$afterHash;ocr_hashes=$ocrHashes
                    }
                    $failures++; $consecutiveFailures++
                    $stage = 'close'
                    & $send @('CTRL','W') $pluginGuard
                    Start-Sleep -Milliseconds ([math]::Min(2000,[math]::Max(800,$WaitMilliseconds)))
                    $stage = 'recover'
                    [Windows.Forms.Clipboard]::Clear()
                    & $send @('CTRL','A') $pluginGuard; & $send @('CTRL','C') $pluginGuard
                    Start-Sleep -Milliseconds 150
                    $returned=[Windows.Forms.Clipboard]::GetText([Windows.Forms.TextDataFormat]::UnicodeText)
                    if (-not (Test-ResultPageEvidence $returned $text)) { throw 'RECOVERY_FAILED' }
                    $detailMayBeOpen=$false
                    continue
                }
                if ($seenDetailText.ContainsKey($detail)) {
                    $records += [pscustomobject]@{
                        ordinal=$checked;stage='detail_copy';reason='duplicate_detail'
                        text=$detail;ocr_text=$ocrText
                        text_length=$detail.Length;detail_hash=$afterHash;ocr_hashes=$ocrHashes
                    }
                    $stage = 'close'
                    & $send @('CTRL','W') $pluginGuard
                    Start-Sleep -Milliseconds ([math]::Min(2000,[math]::Max(800,$WaitMilliseconds)))
                    $stage = 'recover'
                    [Windows.Forms.Clipboard]::Clear()
                    & $send @('CTRL','A') $pluginGuard; & $send @('CTRL','C') $pluginGuard
                    Start-Sleep -Milliseconds 150
                    $returned=[Windows.Forms.Clipboard]::GetText([Windows.Forms.TextDataFormat]::UnicodeText)
                    if (-not (Test-ResultPageEvidence $returned $text)) { throw 'RECOVERY_FAILED' }
                    $detailMayBeOpen=$false
                    continue
                }
                $seenDetailText[$detail]=$true
                $consecutiveFailures=0
                $stage = 'detail_judge'
                $judgeResult=Invoke-EvidenceJudge $detail $AssociationName $PersonName $judge
                $records += [pscustomobject]@{
                    ordinal=$checked;stage='detail_judge';reason='checked'
                    text_length=$detail.Length;detail_hash=$afterHash
                    text=$detail;matched=[bool]$judgeResult.matched
                    ocr_text=$ocrText;ocr_hashes=$ocrHashes
                    ocr_region=if($ocrHashes.Count){'center_detail_0.26_0.08_0.74_0.95'}else{$null}
                }
                $stage = 'close'
                & $send @('CTRL','W') $pluginGuard
                Start-Sleep -Milliseconds ([math]::Min(2000,[math]::Max(800,$WaitMilliseconds)))
                $stage = 'recover'
                [Windows.Forms.Clipboard]::Clear()
                & $send @('CTRL','A') $pluginGuard; & $send @('CTRL','C') $pluginGuard
                Start-Sleep -Milliseconds 150
                $returned=[Windows.Forms.Clipboard]::GetText([Windows.Forms.TextDataFormat]::UnicodeText)
                if (-not (Test-ResultPageEvidence $returned $text)) { throw 'RECOVERY_FAILED' }
                $detailMayBeOpen=$false
                if ($judgeResult.inconclusive) { $consecutiveFailures=2; break }
                if ($judgeResult.matched) { $foundJudge=$judgeResult; break }
            }
            if ($foundJudge -or $checked -ge $Limit -or $consecutiveFailures -ge 2 -or -not $newCount) { break }
            $stage = 'scroll'
            if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
            [void][WechatSouyisouWin32]::SetCursorPos(
                $rect.Left+[int]($width*0.38),$rect.Top+[int]($height*0.72))
            if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
            [WechatSouyisouWin32]::mouse_event(
                0x0800,0,0,(ConvertTo-MouseWheelData -360),[IntPtr]::Zero)
            Start-Sleep -Milliseconds 700
            $scrolls++
        }
        $status=if($foundJudge){'found'}elseif($checked -ge $Limit -and $failures -eq 0){'not_found'}else{'inconclusive'}
        $detailArtifact=Protect-EvidenceArtifact $ArtifactDirectory @{
            kind='collect_result';status=$status;checked=$checked;failures=$failures
            records=$records
            found_result=if($foundJudge){$foundJudge}else{$null}
            captured_at=[DateTimeOffset]::Now.ToString('o')
        }
        $stage = 'cleanup'
        $sessionCleanupAttempted = $true
        $cleanupResult = Complete-WeixinPluginSession `
            ([ref]$sessionCleanupCompleted) $cleanupSession
        Write-Result @{
            ok=$true;executed=$true;status=$status;checked=$checked;failures=$failures
            artifact_ref=$detailArtifact.artifact_ref
            session_closed=[bool]$cleanupResult.session_closed
        }
    } finally {
        # search/collect 的严格终态：关闭详情后精确关闭本次插件 HWND，并恢复主窗口。
        $sessionCleanupFailure = $null
        try {
            if (
                $requiresSessionCleanup -and
                -not $sessionCleanupCompleted -and
                -not $sessionCleanupAttempted
            ) {
                $stageBeforeCleanup = $stage
                $stage = 'cleanup'
                $sessionCleanupAttempted = $true
                if ($detailMayBeOpen) {
                    if (-not $send -or -not $pluginGuard -or -not (& $pluginGuard)) {
                        throw 'SESSION_CLEANUP_FAILED'
                    }
                    & $send @('CTRL','W') $pluginGuard
                    $detailMayBeOpen=$false
                }
                if (-not $cleanupSession) { throw 'SESSION_CLEANUP_FAILED' }
                [void](Complete-WeixinPluginSession `
                    ([ref]$sessionCleanupCompleted) $cleanupSession)
                # 清理成功时保留原业务失败阶段；只有清理自身失败才对外报告 cleanup。
                $stage = $stageBeforeCleanup
            }
        } catch { $sessionCleanupFailure = $_ }
        if ($clipboardCaptured) {
            try {
                if ($null -eq $originalClipboard) { [Windows.Forms.Clipboard]::Clear() }
                else { [Windows.Forms.Clipboard]::SetDataObject($originalClipboard, $true) }
            } catch {}
        }
        if ($mutex) { try { $mutex.ReleaseMutex() } catch {}; $mutex.Dispose() }
        if ($sessionCleanupFailure) {
            $stage = 'cleanup'
            throw 'SESSION_CLEANUP_FAILED'
        }
    }
} catch {
    $code = if ($_.Exception.Message -match '^[A-Z][A-Z0-9_]+$') { $_.Exception.Message } else { 'RPA_FAILED' }
    $failureArtifactRef = $null
    if ($Execute) {
        try {
            $foregroundContext = $null
            if ('WechatSouyisouWin32' -as [type]) {
                $failureHwnd = [WechatSouyisouWin32]::GetForegroundWindow()
                if ($failureHwnd -ne [IntPtr]::Zero) {
                    $failurePid = [uint32]0
                    [void][WechatSouyisouWin32]::GetWindowThreadProcessId($failureHwnd,[ref]$failurePid)
                    $failureClass = New-Object Text.StringBuilder 256
                    [void][WechatSouyisouWin32]::GetClassName($failureHwnd,$failureClass,$failureClass.Capacity)
                    $failureProcessName = $null
                    try {
                        $failureProcessName = [IO.Path]::GetFileName(
                            (Get-Process -Id $failurePid -ErrorAction Stop).Path)
                    } catch {}
                    $foregroundContext = [pscustomobject]@{
                        hwnd=$failureHwnd.ToInt64(); process_basename=$failureProcessName
                        class_name=$failureClass.ToString()
                    }
                }
            }
            $failureArtifact = Protect-EvidenceArtifact $ArtifactDirectory @{
                kind='failure'; error_code=$code; stage=$stage
                foreground=$foregroundContext
                result_artifact_id=if ($detailArtifact) {
                    $detailArtifact.artifact_id
                } elseif ($artifact) {
                    $artifact.artifact_id
                } else { $null }
                captured_at=[DateTimeOffset]::Now.ToString('o')
            }
            $failureArtifactRef = $failureArtifact.artifact_ref
        } catch {}
    }
    Fail $code $_.Exception.Message $failureArtifactRef
}
