# 独立双点标定 CLI 驱动；UTF-8 BOM。
param(
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
    $point = Invoke-WeComCalibration -SelfName $SelfName -ArtifactDir $ArtifactDir
    return @{ title_point = @{ x = $point.x; y = $point.y };
        input_point = @{ x = $point.input_x; y = $point.input_y };
        width = $point.w; height = $point.h; dpi = $point.dpi;
        cache_path = $point.cache_path }
}