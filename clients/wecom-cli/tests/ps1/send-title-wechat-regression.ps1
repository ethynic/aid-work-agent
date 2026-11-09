param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference = 'Stop'
$points = Get-Content -LiteralPath (Join-Path $DriverDir '_send-points.ps1') -Raw -Encoding UTF8
if (-not $points.Contains('Get-WeComCopiedTitle -Hwnd $mainHwnd -Section ''contact'' -Point $point')) {
    throw '自会话标定必须仍复制标题并核对本人显示名'
}
if (-not $points.Contains('[string]::Equals($title, $SelfName, [StringComparison]::Ordinal)')) {
    throw '自会话标定缺少本人标题全等核对'
}
foreach ($name in @('message-send.ps1', 'send-image.ps1', 'send-file.ps1')) {
    $source = Get-Content -LiteralPath (Join-Path $DriverDir $name) -Raw -Encoding UTF8
    $search = $source.IndexOf('Open-WeComNamedChat -Hwnd $mainHwnd -Name $query')
    $input = $source.IndexOf('Clear-WeComFocusedInput -Hwnd $mainHwnd')
    $paste = $source.IndexOf('Send-WeComAttachChordKey -Hwnd $mainHwnd -Vk 0x56')
    $enter = $source.IndexOf('Send-WeComEnter -Hwnd $mainHwnd')
    if ($search -lt 0 -or $input -le $search -or $paste -le $input -or $enter -le $paste) {
        throw ($name + ' 未保持唯一目标搜索后输入、粘贴、发送顺序')
    }
    if ($source.Contains('Get-WeComCopiedTitle') -or $source.Contains('Test-WeComSendTitleMatch') -or $source.Contains('title = $title')) {
        throw ($name + ' 仍依赖发送前标题复制或伪报标题')
    }
    if (-not $source.Contains('if ($Section -ne ''group'') { $query = $TargetName -replace ''@微信$'', '''' }')) {
        throw ($name + ' 群名必须保留完整搜索词')
    }
}
Write-Output 'send-without-title-regression passed: calibration keeps self-title verification, 3 send drivers skip target-title copy'
