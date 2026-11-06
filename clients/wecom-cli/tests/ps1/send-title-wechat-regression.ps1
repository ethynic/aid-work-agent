param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference = 'Stop'
. (Join-Path $DriverDir '_send-points.ps1')

$cases = @(
    @{ title = '孙晨'; expected = '孙晨'; section = 'contact'; ok = $true },
    @{ title = '孙晨@微信'; expected = '孙晨'; section = 'contact'; ok = $true },
    @{ title = '孙晨 @微信'; expected = '孙晨'; section = 'contact'; ok = $true },
    @{ title = '孙晨@企业微信'; expected = '孙晨'; section = 'contact'; ok = $false },
    @{ title = '孙晨附加'; expected = '孙晨'; section = 'contact'; ok = $false },
    @{ title = '孙晨@微信'; expected = '孙晨'; section = 'group'; ok = $false },
    @{ title = '孙晨 @微信'; expected = '孙晨'; section = 'group'; ok = $false },
    @{ title = '孙晨'; expected = '孙晨'; section = 'group'; ok = $true },
    @{ title = '孙晨@微信'; expected = '孙晨@微信'; section = 'group'; ok = $true }
)
foreach ($case in $cases) {
    $actual = Test-WeComSendTitleMatch -Title $case.title -Expected $case.expected -Section $case.section
    if ($actual -ne $case.ok) {
        throw ('标题匹配失败：' + ($case | ConvertTo-Json -Compress))
    }
}

foreach ($name in @('message-send.ps1', 'send-image.ps1', 'send-file.ps1')) {
    $source = Get-Content -LiteralPath (Join-Path $DriverDir $name) -Raw -Encoding UTF8
    if (-not $source.Contains('Test-WeComSendTitleMatch -Title $title -Expected $TargetName -Section $Section')) {
        throw ($name + ' 未使用已验证的发送标题规则')
    }
}
Write-Output ('send-title-wechat-regression passed: ' + $cases.Count + ' behavior cases, 3 send drivers')
