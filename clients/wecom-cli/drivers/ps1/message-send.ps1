# wecom_message_send：双点缓存、标题复制、本地库终态核验由 TS 承担。UTF-8 BOM。
param(
    [Parameter(Mandatory)][string]$TargetName,
    [string]$Subtitle = '',
    [string]$Section = '',
    [Parameter(Mandatory)][string]$Text,
    [Parameter(Mandatory)][string]$SelfName,
    [Parameter(Mandatory)][string]$ArtifactDir
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')
. (Join-Path $driverDir '_send-points.ps1')
if (-not (Test-Path -LiteralPath $ArtifactDir)) { New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null }
$script:DriverLogPath = Join-Path $ArtifactDir 'driver-log.txt'
function Write-DriverLog([string]$Msg) {
    Add-Content -LiteralPath $script:DriverLogPath -Encoding UTF8 -Value ('[' + [DateTime]::UtcNow.ToString('o') + '] ' + $Msg)
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.MessageSend' -Body {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $mainHwnd = Resolve-WeComMainWindow
    $point = Get-WeComCachedPoints -Hwnd $mainHwnd
    if ($null -eq $point) {
        $point = Invoke-WeComCalibration -SelfName $SelfName -ArtifactDir $ArtifactDir
    }
    $mainHwnd = Resolve-WeComMainWindow
    # 搜索默认结果直接 Enter；本地库已在任何 UI 写入前唯一解析目标。
    $query = $TargetName -replace '@微信$', ''
    Open-WeComNamedChat -Hwnd $mainHwnd -Name $query
    $mainHwnd = Resolve-WeComMainWindow
    $main = Get-WeComWindowInfo ([IntPtr]$mainHwnd)
    $currentPoint = Get-WeComCachedPoints -Hwnd $mainHwnd
    if ($null -eq $currentPoint) {
        Throw-DriverError 'UI_CHANGED' '进入目标后窗口尺寸或 DPI 变化，未输入或发送消息'
    }
    $title = Get-WeComCopiedTitle -Hwnd $mainHwnd -Section $Section -Point $currentPoint
    if (-not (Test-WeComSendTitleMatch -Title $title -Expected $TargetName -Section $Section)) {
        # 无法区分默认搜索结果偏离与缓存标题点偏离；本次拒发，下次重新标定。
        if ($currentPoint.source -eq 'cache' -and (Test-Path -LiteralPath $currentPoint.cache_path)) {
            Remove-Item -LiteralPath $currentPoint.cache_path -Force
        }
        Throw-DriverError 'TARGET_NOT_FOUND' ('搜索默认会话标题「' + $title + '」与目标不一致，未输入或发送消息')
    }
    $screenX = [int]($main.X + $currentPoint.input_x)
    $screenY = [int]($main.Y + $currentPoint.input_y)
    [void](Send-WeComClick -Hwnd $mainHwnd -ScreenX $screenX -ScreenY $screenY)
    Start-Sleep -Milliseconds 300
    if (-not (Clear-WeComFocusedInput -Hwnd $mainHwnd)) {
        Throw-DriverError 'UI_CHANGED' '输入区清理键未成功投递，未输入或发送消息'
    }
    # 文本一律走剪贴板粘贴；不立即恢复，以免企微异步 paste handler 读到旧内容。
    try { Set-Clipboard -Value $Text }
    catch { Throw-DriverError 'CONFIG_MISSING' '发送文本剪贴板写入失败，未按 Enter' }
    if (-not (Send-WeComAttachChordKey -Hwnd $mainHwnd -Vk 0x56)) {
        Throw-DriverError 'UI_CHANGED' 'Ctrl+V 未成功投递，未按 Enter'
    }
    Start-Sleep -Milliseconds 500
    # Enter 后只允许 TS 用本地库对账；任何失败都不自动重发。
    Send-WeComEnter -Hwnd $mainHwnd
    $sw.Stop()
    return @{
        navigate_required = $false
        navigated = $true
        title = $title
        sent = $true
        verified = $false
        sent_verification = @{ method = 'skipped'; result = 'unverified' }
        input_point = @{ x = $currentPoint.input_x; y = $currentPoint.input_y; source = $currentPoint.source }
        timing_ms = @{ total = [int]$sw.ElapsedMilliseconds }
        screenshot_paths = @()
    }
}
