param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$parseTokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $DriverDir 'message-send.ps1'), [ref]$parseTokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
foreach ($fn in $ast.FindAll({ param($a) $a -is [Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    . ([scriptblock]::Create($fn.Extent.Text))
}
Add-Type -AssemblyName System.Drawing
Add-Type 'public class WeComWin32 { public static uint GetDpiForWindow(System.IntPtr h) { return 96; } }'
function Get-WeComWindowInfo { param($Hwnd) return @{ W=1117; H=1354 } }
function Throw-DriverError { param($Code,$Message) throw "WECOMDRIVE|$Code|$Message" }
function Invoke-RestMethod { param($Uri,$Method,$Headers,$ContentType,$Body,$TimeoutSec)
    $script:calls++
    $script:lastRequest = [Text.Encoding]::UTF8.GetString($Body) | ConvertFrom-Json
    return @{ content = $script:modelContent }
}
function Assert-True($Value,$Why) { if (-not $Value) { throw $Why } }
Assert-True (Test-WeComTitleMatch -Title '群 A' -Expected '群 A' -Group) 'copied group title must match the complete target name'
Assert-True (-not (Test-WeComTitleMatch -Title '群 A' -Expected '群A' -Group)) 'spaces distinguish two group names when exact text is available'

$oldLocal = $env:LOCALAPPDATA; $oldUrl = $env:AID_WECOM_SERVER_URL; $oldToken = $env:AID_WECOM_SERVER_TOKEN
$testDir = Join-Path $env:TEMP ('wecom-title-point-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDir | Out-Null
$env:LOCALAPPDATA = $testDir
$env:AID_WECOM_SERVER_URL = 'https://example.invalid'
$env:AID_WECOM_SERVER_TOKEN = 'synthetic-token'
$shot = Join-Path $testDir 'window.png'
$bmp = [Drawing.Bitmap]::new(1117,1354)
try { $bmp.Save($shot,[Drawing.Imaging.ImageFormat]::Png) } finally { $bmp.Dispose() }
try {
    $script:calls=0
    $script:modelContent='{"divider_x":409,"x":435,"y":38}'
    $point=Get-WeComTitlePoint -Hwnd 1 -ImagePath $shot
    Assert-True ($point.source -eq 'glm' -and $point.x -eq 435 -and $point.y -eq 38) 'GLM must locate the title first character'
    Assert-True (-not (Test-Path -LiteralPath $point.cache_path)) 'unverified model coordinates must not enter cache'
    Assert-True ($lastRequest.model -eq 'GLM-5.3-Flash') 'title calibration must pin the vision model'
    $dataUrl=$lastRequest.messages[1].content[1].image_url.url
    Assert-True ($dataUrl.StartsWith('data:image/png;base64,')) 'model must receive a PNG header image'
    $bytes=[Convert]::FromBase64String($dataUrl.Substring('data:image/png;base64,'.Length))
    $memory=[IO.MemoryStream]::new($bytes)
    try { $image=[Drawing.Image]::FromStream($memory); try {
        Assert-True ($image.Width -eq 1117 -and $image.Height -eq 64) 'only the header is sent to the model'
    } finally { $image.Dispose() } } finally { $memory.Dispose() }
    Save-WeComTitlePoint $point
    $cached=Get-WeComTitlePoint -Hwnd 1 -ImagePath $shot
    Assert-True ($cached.source -eq 'cache' -and $script:calls -eq 1) 'same window reuses only a verified point'

    Remove-Item -LiteralPath $point.cache_path
    $script:modelContent='{"divider_x":409,"x":300,"y":38}'
    $failure=''; try { Get-WeComTitlePoint -Hwnd 1 -ImagePath $shot | Out-Null } catch { $failure=$_.Exception.Message }
    Assert-True ($failure -like 'WECOMDRIVE|CONTENT_UNAVAILABLE|*') 'coordinates outside the right title area must fail before any click'
    Assert-True (-not (Test-Path -LiteralPath $point.cache_path)) 'rejected point must not poison cache'
} finally {
    $env:LOCALAPPDATA=$oldLocal; $env:AID_WECOM_SERVER_URL=$oldUrl; $env:AID_WECOM_SERVER_TOKEN=$oldToken
    Remove-Item -LiteralPath $testDir -Recurse -Force
}

$cacheFile=Join-Path $env:TEMP ('wecom-title-cache-' + [guid]::NewGuid().ToString('N') + '.json')
try {
    Set-Content -LiteralPath $cacheFile -Value 'synthetic'
    Clear-WeComCachedTitlePointOnCopyFailure -Point @{source='cache';cache_path=$cacheFile} -Failure 'clipboard_changed'
    Assert-True (Test-Path -LiteralPath $cacheFile) 'clipboard contention must not invalidate a valid title coordinate'
    Clear-WeComCachedTitlePointOnCopyFailure -Point @{source='cache';cache_path=$cacheFile} -Failure 'title_copy_unconfirmed'
    Assert-True (-not (Test-Path -LiteralPath $cacheFile)) 'unconfirmed title copy must force recalibration on the next call'
    Set-Content -LiteralPath $cacheFile -Value 'synthetic'
    Clear-WeComCachedTitlePointOnCopyFailure -Point @{source='glm';cache_path=$cacheFile} -Failure 'title_edit_not_focused'
    Assert-True (Test-Path -LiteralPath $cacheFile) 'a newly calibrated point must not delete another cache record'
    Clear-WeComCachedTitlePointOnCopyFailure -Point @{source='cache';cache_path=$cacheFile} -Failure 'title_edit_not_focused'
    Assert-True (-not (Test-Path -LiteralPath $cacheFile)) 'missing title edit must invalidate a stale cached point'
} finally {
    if (Test-Path -LiteralPath $cacheFile) { Remove-Item -LiteralPath $cacheFile -Force }
}

# A second click on an already selected group title can cancel the selection.
# The send path must reuse an existing title edit, click only once when absent,
# and stop before copying if that one click does not create the edit.
function Get-WeComTitleEditWindow { param($Hwnd,$ScreenX,$ScreenY)
    $script:probes++
    if ($script:probes -le $script:missingProbes) { return [IntPtr]::Zero }
    return [IntPtr]77
}
function Send-WeComClick { param($Hwnd,$ScreenX,$ScreenY) $script:clicks++; return 1 }
function Start-Sleep { param($Milliseconds) }

$script:probes=0; $script:clicks=0; $script:missingProbes=0
$edit=Get-WeComGroupTitleEditWindow -Hwnd 1 -ScreenX 455 -ScreenY 85
Assert-True ($edit -eq [IntPtr]77 -and $script:clicks -eq 0) 'an existing title selection must not be clicked away'

$script:probes=0; $script:clicks=0; $script:missingProbes=1
$edit=Get-WeComGroupTitleEditWindow -Hwnd 1 -ScreenX 455 -ScreenY 85
Assert-True ($edit -eq [IntPtr]77 -and $script:clicks -eq 1 -and $script:probes -eq 2) 'an unselected group title gets exactly one click before copy'

$script:probes=0; $script:clicks=0; $script:missingProbes=2
$failure=''; try { Get-WeComGroupTitleEditWindow -Hwnd 1 -ScreenX 455 -ScreenY 85 | Out-Null }
catch { $failure=$_.Exception.Message }
Assert-True ($failure -eq 'title_edit_not_focused' -and $script:clicks -eq 1) 'a failed title click must stop rather than retry blindly'
Write-Output 'PS_TITLE_POINT_AND_CLICK_OK'
