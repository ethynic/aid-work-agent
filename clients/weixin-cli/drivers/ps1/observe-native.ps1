param()
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot '_common.ps1')
. (Join-Path $PSScriptRoot 'native-messages.ps1')

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WeixinCli.NativeObserve' -Body {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $options = @{
        TargetName=$request.target_name; ExpectedNativeTitle=$request.native_title
        ExpectedSessionIdentity=$request.session_identity
    }
    $session = Get-WeixinCurrentNativeSession @options
    return Get-WeixinNativeMessageSnapshot -Session $session -IncludeEvidence:($request.include_evidence -ne $false)
}
