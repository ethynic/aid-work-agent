# 进入唯一匹配会话；坐标来自 W24 双点缓存，按需自会话标定。UTF-8 BOM。
param(
    [Parameter(Mandatory)][string]$TargetName,
    [Parameter(Mandatory)][string]$Section,
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
Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.ChatSelect' -Body {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $mainHwnd = Resolve-WeComMainWindow
    $point = Get-WeComCachedPoints -Hwnd $mainHwnd
    if ($null -eq $point) { $point = Invoke-WeComCalibration -SelfName $SelfName -ArtifactDir $ArtifactDir }
    $mainHwnd = Resolve-WeComMainWindow
    Open-WeComNamedChat -Hwnd $mainHwnd -Name ($TargetName -replace '@微信$', '')
    $mainHwnd = Resolve-WeComMainWindow
    $point = Get-WeComCachedPoints -Hwnd $mainHwnd
    if ($null -eq $point) { Throw-DriverError 'UI_CHANGED' '进入会话后窗口尺寸或 DPI 变化' }
    $title = Get-WeComCopiedTitle -Hwnd $mainHwnd -Section $Section -Point $point
    $titleMatches = Test-WeComTitleMatch -Title $title -Expected $TargetName -Group:($Section -eq 'group')
    if (-not $titleMatches -and $Section -eq 'contact' -and -not $TargetName.EndsWith('@微信')) {
        $titleMatches = Test-WeComTitleMatch -Title $title -Expected $TargetName -RequireWechat
    }
    if (-not $titleMatches) {
        if ($point.source -eq 'cache' -and (Test-Path -LiteralPath $point.cache_path)) {
            Remove-Item -LiteralPath $point.cache_path -Force
        }
        Throw-DriverError 'UI_CHANGED' ('会话标题「' + $title + '」与目标「' + $TargetName + '」不一致')
    }
    $sw.Stop()
    return @{ title = $title; timing_ms = @{ total = [int]$sw.ElapsedMilliseconds }; screenshot_paths = @() }
}
