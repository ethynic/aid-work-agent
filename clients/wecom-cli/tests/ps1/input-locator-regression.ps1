param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference = 'Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $DriverDir 'message-send.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$main=$ast.FindAll({param($a) $a -is [Management.Automation.Language.CommandAst] -and $a.GetCommandName() -eq 'Invoke-DriverMain'},$true)[0]
$body=($main.CommandElements | Where-Object {$_ -is [Management.Automation.Language.ScriptBlockExpressionAst]} | Select-Object -First 1).ScriptBlock.GetScriptBlock()
function Assert-True($Value,[string]$Why) { if (-not $Value) { throw $Why } }
function Resolve-WeComMainWindow { return 101 }
function Get-WeComWindowInfo { param($Hwnd) return @{X=100;Y=200;W=1000;H=800} }
function Get-WeComCachedPoints { param($Hwnd)
    if ($script:cache) { return @{x=440;y=38;input_x=550;input_y=710;cache_path=$script:cachePath;source='cache'} }
    return $null
}
function Invoke-WeComCalibration { param($SelfName,$ArtifactDir)
    [void]$script:actions.Add('calibrate:'+$SelfName)
    $script:cache=$true
    return @{x=440;y=38;input_x=550;input_y=710;cache_path=$script:cachePath;source='glm'}
}
function Open-WeComNamedChat { param($Hwnd,$Name) [void]$script:actions.Add('search:'+$Name) }
function Get-WeComCopiedTitle { param($Hwnd,$Section,$Point) [void]$script:actions.Add('copy-title');return $script:title }
function Test-WeComSendTitleMatch { param($Title,$Expected,[switch]$RequireWechat,[switch]$Group)
    return $Title -eq $Expected
}
function Send-WeComClick { param($Hwnd,$ScreenX,$ScreenY) [void]$script:actions.Add('click-input');return 101 }
function Clear-WeComFocusedInput { param($Hwnd) [void]$script:actions.Add('clear');return $true }
function Set-Clipboard { param($Value) [void]$script:actions.Add('clipboard:'+$Value) }
function Send-WeComAttachChordKey { param($Hwnd,$Vk) [void]$script:actions.Add('paste');return $true }
function Send-WeComEnter { param($Hwnd) [void]$script:actions.Add('enter') }
function Start-Sleep { param($Milliseconds) }
function Throw-DriverError { param($Code,$Message) throw ('WECOMDRIVE|'+$Code+'|'+$Message) }
function Write-DriverLog { param($Message) }
function Run-Scenario([bool]$Cached,[string]$Title,[string]$Text='hello') {
    $script:actions=New-Object Collections.ArrayList
    $script:cache=$Cached;$script:title=$Title
    $TargetName='Peer';$Subtitle='';$Section='contact';$SelfName='Self';$ArtifactDir=$env:TEMP
    $SkipVerify=$true
    $result=$null;$failure=''
    try { $result=& $body } catch { $failure=$_.Exception.Message }
    return @{result=$result;failure=$failure;actions=@($script:actions.ToArray())}
}
$script:cachePath=Join-Path $env:TEMP ('wecom-send-test-'+[guid]::NewGuid().ToString('N')+'.json')
try {
    Set-Content -LiteralPath $script:cachePath -Value '{}' -Encoding UTF8
    $r=Run-Scenario $true 'Peer'
    Assert-True ($r.failure -eq '') 'cached send should complete'
    Assert-True (@($r.actions | Where-Object {$_ -like 'calibrate:*'}).Count -eq 0) 'valid cache must skip model calibration'
    Assert-True (($r.actions -join ',') -eq 'search:Peer,copy-title,click-input,clear,clipboard:hello,paste,enter') 'title must be confirmed before draft clear and paste/Enter'
    $r=Run-Scenario $false 'Peer' ('line1'+[Environment]::NewLine+'line2')
    Assert-True ($r.failure -eq '') 'missing cache should calibrate and then send'
    Assert-True ($r.actions[0] -eq 'calibrate:Self') 'calibration must use verified self display name'
    Assert-True (@($r.actions | Where-Object {$_ -eq 'paste'}).Count -eq 1) 'multiline also pastes once'
    $r=Run-Scenario $true 'Wrong'
    Assert-True ($r.failure -like 'WECOMDRIVE|TARGET_NOT_FOUND|*') 'wrong title must reject before input'
    Assert-True (@($r.actions | Where-Object {$_ -eq 'clear' -or $_ -eq 'enter'}).Count -eq 0) 'wrong title must not clear or send'
    Assert-True (-not (Test-Path -LiteralPath $script:cachePath)) 'unmatched title invalidates cached coordinates for next calibration'
} finally {
    if (Test-Path -LiteralPath $script:cachePath) { Remove-Item -LiteralPath $script:cachePath -Force }
}
Write-Output 'PS_INPUT_LOCATOR_OK'
