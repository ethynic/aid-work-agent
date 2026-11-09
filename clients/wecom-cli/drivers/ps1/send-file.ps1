# wecom_send_file：双点缓存、标题复制、剪贴板粘贴；终态只由 TS 本地库核验。UTF-8 BOM。
param(
    [Parameter(Mandatory)][string]$TargetName,
    [string]$Subtitle = '',
    [string]$Section = '',
    [Parameter(Mandatory)][string]$FilePath,
    [Parameter(Mandatory)][string]$SelfName,
    [Parameter(Mandatory)][string]$ArtifactDir
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')
. (Join-Path $driverDir '_send-points.ps1')
Add-Type -AssemblyName System.Windows.Forms

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
    $query = $TargetName
    if ($Section -ne 'group') { $query = $TargetName -replace '@微信$', '' }
    Open-WeComNamedChat -Hwnd $mainHwnd -Name $query
    $mainHwnd = Resolve-WeComMainWindow
    $main = Get-WeComWindowInfo ([IntPtr]$mainHwnd)
    $currentPoint = Get-WeComCachedPoints -Hwnd $mainHwnd
    if ($null -eq $currentPoint) {
        Throw-DriverError 'UI_CHANGED' '进入目标后窗口尺寸或 DPI 变化，未输入或发送消息'
    }
    $screenX = [int]($main.X + $currentPoint.input_x)
    $screenY = [int]($main.Y + $currentPoint.input_y)
    [void](Send-WeComClick -Hwnd $mainHwnd -ScreenX $screenX -ScreenY $screenY)
    Start-Sleep -Milliseconds 300
    if (-not (Clear-WeComFocusedInput -Hwnd $mainHwnd)) {
        Throw-DriverError 'UI_CHANGED' '输入区清理键未成功投递，未输入或发送消息'
    }
    # 剪贴板文件列表由企微异步读取；覆盖后不立即恢复。
    $clipOk = $false
    for ($i = 1; $i -le 5; $i++) {
        try {
            $dropList = New-Object 'System.Collections.Specialized.StringCollection'
            [void]$dropList.Add((Get-Item -LiteralPath $FilePath).FullName)
            [Windows.Forms.Clipboard]::SetFileDropList($dropList)
            $clipOk = $true
            break
        } catch { if ($i -lt 5) { Start-Sleep -Milliseconds 150 } }
    }
    if (-not $clipOk) { Throw-DriverError 'CONFIG_MISSING' '剪贴板文件列表写入失败，未按 Enter' }
    if (-not (Send-WeComAttachChordKey -Hwnd $mainHwnd -Vk 0x56)) {
        Throw-DriverError 'UI_CHANGED' 'Ctrl+V 未成功投递，未按 Enter'
    }
    Start-Sleep -Milliseconds 1500
    # Enter 后只允许 TS 用本地库对账；任何失败都不自动重发。
    Send-WeComEnter -Hwnd $mainHwnd
    $sw.Stop()
    return @{
        navigate_required = $false
        navigated = $true
        sent = $true
        verified = $false
        sent_verification = @{ method = 'skipped'; result = 'unverified' }
        input_point = @{ x = $currentPoint.input_x; y = $currentPoint.input_y; source = $currentPoint.source }
        timing_ms = @{ total = [int]$sw.ElapsedMilliseconds }
        screenshot_paths = @()
    }
}
