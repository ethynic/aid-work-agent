param()
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot '_common.ps1')
. (Join-Path $PSScriptRoot 'native-messages.ps1')
. (Join-Path $PSScriptRoot 'native-session-search.ps1')

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WeixinCli.NameResolve' -Body {
    try { $request = [Console]::In.ReadToEnd() | ConvertFrom-Json }
    catch { Throw-DriverError 'UI_CHANGED' '微信定位请求格式不可用' }
    $name = $request.target_name
    if ($name -isnot [string] -or [string]::IsNullOrWhiteSpace($name) -or $name.Length -gt 128 -or $name -match '[\x00-\x1f\x7f]') {
        Throw-DriverError 'UI_CHANGED' '微信联系人名称无效'
    }
    $session = Open-WeixinNativeSearchTarget -TargetName $name -TargetType 'friend' -RequireTypedSection
    Assert-WeixinNativeSelectedSession $session
    $title = Get-WeixinNativeTitleInfo $session
    if (-not [string]::Equals($title.title, $name, [StringComparison]::Ordinal) -or $title.is_group) {
        Throw-DriverError 'UI_CHANGED' '当前微信联系人与目标不一致'
    }
    return @{ title=$title.title; session_identity=$session.Identity }
}
