$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\wechat-souyisou-lib.ps1')

function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "ASSERTION_FAILED: $Message" }
}

function New-PluginIdentity([int64]$Hwnd) {
    [pscustomobject]@{
        Hwnd = $Hwnd
        ProcessPath = Join-Path $script:WeixinPluginRoot "1\WeChatAppEx.exe"
        ClassName = 'Chrome_WidgetWin_0'
        Title = $script:WeixinTitle
    }
}

function New-MainIdentity([int64]$Hwnd) {
    [pscustomobject]@{
        Hwnd = $Hwnd
        ProcessPath = "C:\Program Files\Tencent\Weixin\Weixin.exe"
        ClassName = 'Qt51514QWindowIcon'
        Title = $script:WeixinTitle
    }
}

$mainHwnd = [int64]100

# Same-HWND detail uses browser back and returns to the list.
$same = New-WeixinWindowSession $mainHwnd @()
[void](Add-WeixinWindowSessionForeground $same (New-PluginIdentity 200) 'list')
$sameForegrounds = [Collections.Generic.Queue[object]]::new()
$sameForegrounds.Enqueue((New-PluginIdentity 200))
$sameForegrounds.Enqueue((New-PluginIdentity 200))
$sameKeys = @()
$sameReturned = Invoke-WeixinWindowSessionReturnToList $same `
    { $sameForegrounds.Dequeue() } `
    { param($keys) $script:sameKeys += ,@($keys) } `
    { }
Assert ($sameReturned -eq 200) 'same hwnd returns list'
Assert (($sameKeys[0] -join '+') -eq 'ALT+LEFT') 'same hwnd uses back'

# Independent detail uses the Ctrl+W sequence proven by flow_probe.
$independent = New-WeixinWindowSession $mainHwnd @()
[void](Add-WeixinWindowSessionForeground $independent (New-PluginIdentity 200) 'list')
[void](Add-WeixinWindowSessionForeground $independent (New-PluginIdentity 300) 'detail')
$independentEvents = @()
$independentForegrounds = [Collections.Generic.Queue[object]]::new()
$independentForegrounds.Enqueue((New-PluginIdentity 300))
$independentForegrounds.Enqueue((New-PluginIdentity 200))
[void](Invoke-WeixinWindowSessionReturnToList $independent `
    { $script:independentEvents += 'read'; $independentForegrounds.Dequeue() } `
    { param($keys) $script:independentEvents += ('keys:' + ($keys -join '+')) } `
    { $script:independentEvents += 'wait' })
Assert (($independentEvents -join ',') -eq 'read,keys:CTRL+W,wait,read') `
    'independent detail critical sequence'
Assert ($independent.LastReturnUsedIndependentDetail) `
    'independent detail return is recorded for evidence handling'
Assert ((Get-WeixinRecoveryEvidenceOutcome $independent $false) -eq 'cleanup') `
    'independent detail with transient list evidence ends in safe cleanup'
Assert ((Get-WeixinRecoveryEvidenceOutcome $independent $true) -eq 'continue') `
    'verified list evidence can continue collection'
$sameEvidenceRejected = $false
try { [void](Get-WeixinRecoveryEvidenceOutcome $same $false) }
catch { $sameEvidenceRejected = $_.Exception.Message -eq 'RECOVERY_FAILED' }
Assert $sameEvidenceRejected `
    'same-hwnd detail still requires result-page evidence'

# Independent detail may land on the trusted Qt main window. Only the exact
# already-owned list HWND can then be reactivated.
$mainFallback = New-WeixinWindowSession $mainHwnd @()
[void](Add-WeixinWindowSessionForeground $mainFallback (New-PluginIdentity 200) 'list')
[void](Add-WeixinWindowSessionForeground $mainFallback (New-PluginIdentity 300) 'detail')
$mainFallbackForegrounds = [Collections.Generic.Queue[object]]::new()
$mainFallbackForegrounds.Enqueue((New-PluginIdentity 300))
$mainFallbackForegrounds.Enqueue((New-MainIdentity 100))
$mainFallbackForegrounds.Enqueue((New-PluginIdentity 200))
$activatedTargets = @()
$mainFallbackReturned = Invoke-WeixinWindowSessionReturnToList $mainFallback `
    { $mainFallbackForegrounds.Dequeue() } { } { } `
    { param($hwnd) New-PluginIdentity $hwnd } { $true } `
    { param($hwnd) $script:activatedTargets += $hwnd; $true }
Assert ($mainFallbackReturned -eq 200) 'trusted main fallback returns owned list'
Assert (($activatedTargets -join ',') -eq '200') `
    'trusted main fallback activates only registered list'

function Test-MainFallbackFailure(
    [scriptblock]$IdentityLookup,
    [scriptblock]$WindowExists,
    [scriptblock]$Activate
) {
    $session = New-WeixinWindowSession $mainHwnd @()
    [void](Add-WeixinWindowSessionForeground $session (New-PluginIdentity 200) 'list')
    [void](Add-WeixinWindowSessionForeground $session (New-PluginIdentity 300) 'detail')
    $foregrounds = [Collections.Generic.Queue[object]]::new()
    $foregrounds.Enqueue((New-PluginIdentity 300))
    $foregrounds.Enqueue((New-MainIdentity 100))
    try {
        [void](Invoke-WeixinWindowSessionReturnToList $session `
            { $foregrounds.Dequeue() } { } { } `
            $IdentityLookup $WindowExists $Activate)
        return $false
    } catch {
        return $_.Exception.Message -eq 'RECOVERY_FAILED'
    }
}
Assert (Test-MainFallbackFailure `
    { param($h) New-PluginIdentity $h } { param($h) $h -eq 300 } { $true }) `
    'missing list with a residual owned detail cannot be restored'
Assert (Test-MainFallbackFailure `
    { [pscustomobject]@{Hwnd=200;ProcessPath='C:\bad.exe';ClassName='x';Title='x'} } `
    { $true } { $true }) 'changed list identity cannot be restored'
Assert (Test-MainFallbackFailure `
    { param($h) New-PluginIdentity $h } { $true } { $false }) `
    'list activation failure remains recovery failure'

$natural = New-WeixinWindowSession $mainHwnd @()
[void](Add-WeixinWindowSessionForeground $natural (New-PluginIdentity 200) 'list')
[void](Add-WeixinWindowSessionForeground $natural (New-PluginIdentity 300) 'detail')
$naturalForegrounds = [Collections.Generic.Queue[object]]::new()
$naturalForegrounds.Enqueue((New-PluginIdentity 300))
$naturalForegrounds.Enqueue((New-MainIdentity 100))
$naturalReturned = Invoke-WeixinWindowSessionReturnToList $natural `
    { $naturalForegrounds.Dequeue() } { } { } `
    { param($h) $null } { param($h) $false } { param($h) $true }
Assert ($naturalReturned -eq 0 -and $natural.NaturallyClosed) `
    'trusted main with every owned plugin gone is a naturally closed session'

$externalFallback = New-WeixinWindowSession $mainHwnd @()
[void](Add-WeixinWindowSessionForeground $externalFallback (New-PluginIdentity 200) 'list')
[void](Add-WeixinWindowSessionForeground $externalFallback (New-PluginIdentity 300) 'detail')
$externalForegrounds = [Collections.Generic.Queue[object]]::new()
$externalForegrounds.Enqueue((New-PluginIdentity 300))
$externalForegrounds.Enqueue([pscustomobject]@{
    Hwnd=900;ProcessPath='C:\Windows\notepad.exe';ClassName='Notepad';Title='x'
})
$externalActivationCount = 0
$externalFallbackRejected = $false
try {
    [void](Invoke-WeixinWindowSessionReturnToList $externalFallback `
        { $externalForegrounds.Dequeue() } { } { } `
        { param($h) New-PluginIdentity $h } { $true } `
        { $script:externalActivationCount++; $true })
} catch {
    $externalFallbackRejected = $_.Exception.Message -eq 'RECOVERY_FAILED'
}
Assert ($externalFallbackRejected -and $externalActivationCount -eq 0) `
    'external foreground never triggers list activation'

$sameMain = New-WeixinWindowSession $mainHwnd @()
[void](Add-WeixinWindowSessionForeground $sameMain (New-PluginIdentity 200) 'list')
$sameMainForegrounds = [Collections.Generic.Queue[object]]::new()
$sameMainForegrounds.Enqueue((New-PluginIdentity 200))
$sameMainForegrounds.Enqueue((New-MainIdentity 100))
$sameActivationCount = 0
$sameMainRejected = $false
try {
    [void](Invoke-WeixinWindowSessionReturnToList $sameMain `
        { $sameMainForegrounds.Dequeue() } { } { } `
        { param($h) New-PluginIdentity $h } { $true } `
        { $script:sameActivationCount++; $true })
} catch {
    $sameMainRejected = $_.Exception.Message -eq 'RECOVERY_FAILED'
}
Assert ($sameMainRejected -and $sameActivationCount -eq 0) `
    'same-hwnd path never uses trusted-main activation fallback'

# Preexisting plugins and external windows cannot receive input.
$oldSession = New-WeixinWindowSession $mainHwnd @(150)
$oldRejected = $false
try { [void](Add-WeixinWindowSessionForeground $oldSession (New-PluginIdentity 150) 'list') }
catch { $oldRejected = $_.Exception.Message -eq 'PREEXISTING_PLUGIN_REJECTED' }
Assert $oldRejected 'preexisting plugin rejected'
$openedSession = New-WeixinWindowSession $mainHwnd @(150)
[void](Add-WeixinWindowSessionForeground `
    $openedSession (New-PluginIdentity 150) 'list' -AllowPreexisting)
Assert ($openedSession.ListHwnd -eq 150) `
    'expected open transition can claim the verified list plugin once'

$externalSession = New-WeixinWindowSession $mainHwnd @()
[void](Add-WeixinWindowSessionForeground $externalSession (New-PluginIdentity 200) 'list')
$externalKeys = 0
$external = [pscustomobject]@{
    Hwnd=900;ProcessPath='C:\Windows\notepad.exe';ClassName='Notepad';Title='x'
}
$externalRejected = $false
try {
    [void](Invoke-WeixinWindowSessionReturnToList $externalSession `
        { $external } { $script:externalKeys++ } { })
} catch {
    $externalRejected = $_.Exception.Message -eq 'FOREGROUND_LOST'
}
Assert ($externalRejected -and $externalKeys -eq 0) 'external foreground blocks input'

# Return must land on the registered list HWND.
$wrongReturn = New-WeixinWindowSession $mainHwnd @()
[void](Add-WeixinWindowSessionForeground $wrongReturn (New-PluginIdentity 200) 'list')
[void](Add-WeixinWindowSessionForeground $wrongReturn (New-PluginIdentity 300) 'detail')
$wrongForegrounds = [Collections.Generic.Queue[object]]::new()
$wrongForegrounds.Enqueue((New-PluginIdentity 300))
$wrongForegrounds.Enqueue((New-PluginIdentity 400))
$recoveryRejected = $false
try {
    [void](Invoke-WeixinWindowSessionReturnToList $wrongReturn `
        { $wrongForegrounds.Dequeue() } { } { })
} catch { $recoveryRejected = $_.Exception.Message -eq 'RECOVERY_FAILED' }
Assert $recoveryRejected 'wrong trusted plugin cannot replace list'

# Cleanup tries every owned window and never hides a partial failure.
$cleanup = New-WeixinWindowSession $mainHwnd @()
[void](Add-WeixinWindowSessionForeground $cleanup (New-PluginIdentity 200) 'list')
[void](Add-WeixinWindowSessionForeground $cleanup (New-PluginIdentity 300) 'detail')
$closeTargets = @()
$cleanupResult = Close-WeixinWindowSession $cleanup {
    param($hwnd)
    $script:closeTargets += $hwnd
    if ($hwnd -eq 300) { throw 'PLUGIN_CLOSE_TIMEOUT' }
    [pscustomobject]@{ session_closed=$true }
}
Assert (-not $cleanupResult.session_closed) 'partial cleanup is not success'
Assert (($closeTargets -join ',') -eq '300,200') 'cleanup attempts detail before list'
Assert ($cleanupResult.error_codes[0] -eq 'PLUGIN_CLOSE_TIMEOUT') `
    'cleanup preserves stable failure code'

# Work budget reserves enough time before starting a blocking helper.
$budgetNow = [DateTimeOffset]::Parse('2026-08-05T00:00:00Z')
$budgetDeadline = $budgetNow.AddSeconds(60)
$remaining = Assert-WeixinWorkBudget $budgetDeadline 60000 { $budgetNow }
Assert ($remaining -eq 60000) 'exact helper budget is accepted'
$budgetRejected = $false
try {
    [void](Assert-WeixinWorkBudget `
        $budgetDeadline 60000 { $budgetNow.AddMilliseconds(1) })
} catch { $budgetRejected = $_.Exception.Message -eq 'WECHAT_WORK_TIMEOUT' }
Assert $budgetRejected 'blocking helper cannot consume cleanup reserve'

# A confirmed detail gets one fixed settle delay before content work starts.
$settleEvents = @()
$settleGuardCalls = 0
Wait-WeixinDetailSettled `
    { $script:settleGuardCalls++; $true } `
    { param($milliseconds) $script:settleEvents += $milliseconds }
Assert (($settleEvents -join ',') -eq '5000') 'detail settle waits exactly once for 5000 ms'
Assert ($settleGuardCalls -eq 2) 'detail settle guards foreground before and after waiting'
$lostAfterSettle = $false
$lostGuardCalls = 0
try {
    Wait-WeixinDetailSettled `
        { $script:lostGuardCalls++; $script:lostGuardCalls -eq 1 } `
        { param($milliseconds) }
} catch { $lostAfterSettle = $_.Exception.Message -eq 'FOREGROUND_LOST' }
Assert $lostAfterSettle 'detail settle fails loud if foreground is lost during the wait'

# Unreadable copied content is skipped without OCR or judge work.
$ocrCalls = 0
$judgeCalls = 0
$visited = 0
foreach ($candidateText in @('', ('usable detail body ' * 8))) {
    $visited++
    if (-not (Test-WeixinReadableDetailCopy $candidateText 'result list body')) { continue }
    $judgeCalls++
}
Assert ($visited -eq 2 -and $ocrCalls -eq 0 -and $judgeCalls -eq 1) `
    'empty detail copy skips content tools and collection continues to next candidate'
Assert (-not (Test-WeixinReadableDetailCopy 'result list body' 'result list body')) `
    'result page copy is not accepted as readable detail content'
Assert (-not (Test-WeixinDetailOpened 'same-hash' 'same-hash')) `
    'unchanged click is a skipped candidate rather than an opened detail'
Assert (Test-WeixinDetailOpened 'before-hash' 'after-hash') `
    'changed viewport is an opened detail candidate'
Assert ((Get-WeixinCollectStatus $null $false 3 10 3) -eq 'inconclusive') `
    'all unreadable candidates have a successful inconclusive content outcome'

Write-Output 'window session behavior tests passed'
