param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference = 'Stop'
function Read-Ast([string]$Name) {
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $DriverDir $Name), [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw "parse errors in $Name" }
    return $ast
}
function Import-Function($Ast, [string]$Name) {
    $fn = $Ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $Name }, $true)
    if ($null -eq $fn) { throw "missing $Name" }
    $definition = [scriptblock]::Create($fn.Extent.Text.Replace(('function ' + $Name), ('function global:' + $Name)))
    . $definition
}
$common = Read-Ast '_common.ps1'
Import-Function $common 'Close-WeComSearchOverlay'
Import-Function $common 'Resolve-WeComMainWindow'
$probe = Read-Ast 'probe.ps1'
$command = $probe.Find({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Invoke-DriverMain' }, $true)
$body = @($command.CommandElements | Where-Object { $_ -is [System.Management.Automation.Language.ScriptBlockExpressionAst] })[0].ScriptBlock.Extent.Text
$runProbe = [scriptblock]::Create($body.Substring(1, $body.Length - 2))
Add-Type 'public static class WeComWin32 { public static int Restores=0; public static bool ShowWindowAsync(System.IntPtr h,int n){Restores++;return true;} }'
$title = -join @(0x4F01,0x4E1A,0x5FAE,0x4FE1 | ForEach-Object { [char]$_ })
$script:Window = [pscustomobject]@{ Class='WeWorkWindow'; Title=$title; Visible=$false; W=1100; H=750; X=0; Y=0; Hwnd=42; Pid=77 }
$script:Login = $null
function Get-WeComTopLevelWindows { if ($null -eq $script:Window) { return @() }; $copy=$script:Window.PSObject.Copy(); if ([WeComWin32]::Restores -gt 0) { $copy.Visible=$true }; return @($copy) }
function Find-WeComLoginWindow { return $script:Login }
function Get-WeComLoginWindowBmp { return $null }
function Get-WeComContentChildWindow { return @() }
function Throw-DriverError([string]$Code,[string]$Message) { throw ($Code + ': ' + $Message) }
$tray = & $runProbe
if ($tray.login_state -ne 'online' -or -not $tray.window_hidden) { throw 'tray must remain online' }
if ([WeComWin32]::Restores -ne 0) { throw 'readonly probe must not activate the tray window' }
$script:Login = @{Hwnd=99; X=0; Y=0; W=400; H=500}
$logout = & $runProbe
if ($logout.login_state -ne 'need_login') { throw 'real login window takes precedence over hidden main' }
$script:Login = $null
$restored = Resolve-WeComMainWindow
if ($restored -ne 42 -or [WeComWin32]::Restores -ne 1) { throw 'restore only original hidden main once' }
$script:Clears=0
function Clear-WeComSearchBox { param($MainHwnd,[switch]$BestEffort) $script:Clears++; return $true }
function Send-WeComEscape { throw 'normal cleanup must not send Escape' }
Close-WeComSearchOverlay -OverlayHwnd 42 -MainHwnd 42
if ($script:Clears -ne 1) { throw 'cleanup must use the located search control' }
$script:Window = $null
$offline = & $runProbe
if ($offline.login_state -ne 'offline') { throw 'no recognised window is offline' }
$local = Read-Ast 'localdb-login.ps1'
$hidden = $local.Find({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$hidden' }, $true)
$offlineCommands = @($local.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Write-DriverJson' -and $n.Extent.Text -match "login_state = 'offline'" }, $true))
$lastOffline = $offlineCommands[$offlineCommands.Count - 1]
$source = [IO.File]::ReadAllText((Join-Path $DriverDir 'localdb-login.ps1'))
$fragment = $source.Substring($hidden.Extent.StartOffset, $lastOffline.Extent.EndOffset - $hidden.Extent.StartOffset).Replace('exit 0','return')
$readLogin = [scriptblock]::Create($fragment)
function Write-DriverJson($Value) { return $Value }
$wins = @([pscustomobject]@{Class='WeWorkWindow'; Title=$title; Visible=$false; W=1100; H=750; Pid=77})
$nativeTray = & $readLogin
if ($nativeTray.data.login_state -ne 'online' -or $nativeTray.data.window_pid -ne 77) { throw 'DB login probe must identify original hidden PID' }
$wins = @()
$nativeOffline = & $readLogin
if ($nativeOffline.data.login_state -ne 'offline') { throw 'DB login probe cannot invent a window' }
Write-Output 'PS_TRAY_ESCAPE_OK cases=7'
