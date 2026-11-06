param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference='Stop'
. (Join-Path $DriverDir '_send-points.ps1')
Add-Type -AssemblyName System.Drawing
Add-Type 'public class WeComWin32 { public static uint Dpi = 96; public static uint GetDpiForWindow(System.IntPtr h) { return Dpi; } }'
function Assert-True($Value,[string]$Why) { if (-not $Value) { throw $Why } }
function Get-WeComWindowInfo { param($Hwnd) return @{W=$script:width;H=800} }
function Throw-DriverError { param($Code,$Message) throw ('WECOMDRIVE|'+$Code+'|'+$Message) }
function Invoke-RestMethod { param($Uri,$Method,$Headers,$ContentType,$Body,$TimeoutSec)
    $script:modelCalls++
    $script:lastRequest=[Text.Encoding]::UTF8.GetString($Body) | ConvertFrom-Json
    return @{content=$script:modelContent}
}
function Write-DriverLog { param($Message) }
function Open-WeComNamedChat { param($Hwnd,$Name) $script:searched=$Name }
function Resolve-WeComMainWindow { return 101 }
function Get-WeComWindowSnapshot { param($Hwnd,$Path) Copy-Item -LiteralPath $script:shot -Destination $Path;return @(0,0,1000,800) }
function Get-WeComCopiedTitle { param($Hwnd,$Section,$Point) return $script:copied }
$oldLocal=$env:LOCALAPPDATA;$oldUrl=$env:AID_WECOM_SERVER_URL;$oldToken=$env:AID_WECOM_SERVER_TOKEN
$dir=Join-Path $env:TEMP ('wecom-send-points-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $dir | Out-Null
$env:LOCALAPPDATA=$dir;$env:AID_WECOM_SERVER_URL='https://example.invalid';$env:AID_WECOM_SERVER_TOKEN='synthetic'
$script:width=1000;$script:shot=Join-Path $dir 'shot.png'
$bmp=[Drawing.Bitmap]::new(1000,800)
try { $bmp.Save($script:shot,[Drawing.Imaging.ImageFormat]::Png) } finally { $bmp.Dispose() }
try {
    Assert-True (Test-WeComTitleMatch -Title '群 A' -Expected '群 A' -Group) 'group title must compare exact text'
    Assert-True (-not (Test-WeComTitleMatch -Title '群 A' -Expected '群A' -Group)) 'group title cannot ignore spaces'
    Assert-True (Test-WeComTitleMatch -Title '陆伟' -Expected '陆伟') 'personal target requires an exact title'
    Assert-True (-not (Test-WeComTitleMatch -Title '陆伟民' -Expected '陆伟')) 'similar default search result must not be sent a message'
    Assert-True (-not (Test-WeComTitleMatch -Title '陆伟（技术部）' -Expected '陆伟')) 'unknown identity suffix must not turn a prefix into a match'
    Assert-True (-not (Test-WeComTitleMatch -Title '陆伟@其他' -Expected '陆伟')) 'unknown account suffix must not turn a prefix into a match'
    Assert-True (Test-WeComTitleMatch -Title '陆伟@微信' -Expected '陆伟' -RequireWechat) 'known WeChat identity suffix is an exact match'
    Assert-True (Test-WeComTitleMatch -Title '陆伟 @微信' -Expected '陆伟' -RequireWechat) 'known displayed separator before WeChat suffix remains an exact match'
    Assert-True (-not (Test-WeComTitleMatch -Title '陆伟@其他' -Expected '陆伟' -RequireWechat)) 'WeChat target cannot match another account suffix'
    $script:modelCalls=0
    $script:modelContent='{"divider_x":400,"title_x":440,"title_y":38,"input_x":620,"input_y":710}'
    $point=Get-WeComTitlePoint -Hwnd 101 -ImagePath $script:shot
    Assert-True ($point.x -eq 440 -and $point.input_x -eq 620 -and $point.input_y -eq 710) 'one model response must provide both points'
    Assert-True ($script:modelCalls -eq 1) 'one calibration invokes model exactly once'
    Assert-True (-not (Test-Path -LiteralPath $point.cache_path)) 'unverified model points must not enter cache'
    $url=$lastRequest.messages[1].content[1].image_url.url
    $bytes=[Convert]::FromBase64String($url.Substring('data:image/png;base64,'.Length))
    $memory=[IO.MemoryStream]::new($bytes)
    try { $img=[Drawing.Image]::FromStream($memory); try {
        Assert-True ($img.Width -eq 1000 -and $img.Height -eq 800) 'model must see title and input in one full-window frame'
    } finally {$img.Dispose()} } finally {$memory.Dispose()}
    Save-WeComTitlePoint $point
    $cached=Get-WeComCachedPoints -Hwnd 101
    Assert-True ($cached.source -eq 'cache' -and $cached.x -eq 440 -and $cached.input_x -eq 620) 'same geometry reuses both cached points'
    Assert-True ($script:modelCalls -eq 1) 'cache read must not invoke GLM'
    $script:width=1100
    Assert-True ($null -eq (Get-WeComCachedPoints -Hwnd 101)) 'width change invalidates both points'
    $script:width=1000
    [WeComWin32]::Dpi=120
    Assert-True ($null -eq (Get-WeComCachedPoints -Hwnd 101)) 'DPI change invalidates both points'
    [WeComWin32]::Dpi=96
    Remove-Item -LiteralPath $point.cache_path -Force
    $script:modelContent='{"divider_x":400,"title_x":440,"title_y":38,"input_x":300,"input_y":710}'
    $failure='';try{Get-WeComTitlePoint -Hwnd 101 -ImagePath $script:shot | Out-Null}catch{$failure=$_.Exception.Message}
    Assert-True ($failure -like 'WECOMDRIVE|CONTENT_UNAVAILABLE|*') 'input point outside chat area must fail without caching'
    Assert-True (-not (Test-Path -LiteralPath $point.cache_path)) 'bad point must not poison cache'
    # 自会话标题全等是模型坐标写入缓存的最后条件。
    function Get-WeComTitlePoint { param($Hwnd,$ImagePath)
        return @{x=440;y=38;input_x=620;input_y=710;w=1000;h=800;dpi=96;cache_path=(Get-WeComPointCachePath);source='glm'}
    }
    $script:copied='Wrong'
    $failure='';try{Invoke-WeComCalibration -SelfName 'Self' -ArtifactDir $dir | Out-Null}catch{$failure=$_.Exception.Message}
    Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'wrong self title must reject calibration'
    Assert-True (-not (Test-Path -LiteralPath $point.cache_path)) 'wrong self title must not cache'
    $script:copied='Self'
    $ok=Invoke-WeComCalibration -SelfName 'Self' -ArtifactDir $dir
    Assert-True ($script:searched -eq 'Self' -and $ok.input_y -eq 710) 'self search and exact title verify both points'
    Assert-True (Test-Path -LiteralPath $point.cache_path) 'verified self title caches both points'
} finally {
    $env:LOCALAPPDATA=$oldLocal;$env:AID_WECOM_SERVER_URL=$oldUrl;$env:AID_WECOM_SERVER_TOKEN=$oldToken
    Remove-Item -LiteralPath $dir -Recurse -Force
}
Write-Output 'PS_TITLE_POINT_AND_CLICK_OK'
