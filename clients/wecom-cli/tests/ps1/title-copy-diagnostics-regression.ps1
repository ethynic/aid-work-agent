param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference='Stop'
. (Join-Path $DriverDir '_send-points.ps1')
Add-Type @'
public static class WeComWin32 {
 public static uint Sequence=10; public static long Owner=404; public static string Text="private-secret";
 public static bool RejectRestore=false; public static bool RejectInitial=false; public static int Exchanges=0;
 public static uint GetClipboardSequenceNumber(){return Sequence;}
 public static System.IntPtr GetClipboardOwner(){return (System.IntPtr)Owner;}
 public static uint GetWindowThreadProcessId(System.IntPtr hwnd,out uint pid){pid=hwnd.ToInt64()==202?7u:(hwnd.ToInt64()==404?49232u:99u);return 1;}
 public static bool IsWindow(System.IntPtr hwnd){return true;}
 public static bool ExchangeClipboardText(System.IntPtr owner,uint seq,string previous,bool wasEmpty,string next,bool nextEmpty,out uint written){
  written=Sequence;Exchanges++;if((RejectInitial&&Exchanges==1)||(RejectRestore&&Exchanges==2))return false;
  if(seq!=Sequence||previous!=Text||wasEmpty)return false;
  Text=next;Sequence++;Owner=owner.ToInt64();written=Sequence;return true;
 }
}
public static class TitleClipboard {
 public static int Reads=0;
 public static bool ContainsText(){Reads++;return true;}
 public static string GetText(){Reads++;return WeComWin32.Text;}
}
'@
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $DriverDir '_send-points.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw($errors|Out-String)}
$fn=$ast.Find({param($n)$n-is[Management.Automation.Language.FunctionDefinitionAst]-and$n.Name-eq'Get-WeComCopiedTitle'},$true)
$definition=$fn.Extent.Text.Replace('function Get-WeComCopiedTitle','function global:Get-WeComCopiedTitle').Replace('[Windows.Forms.Clipboard]','[TitleClipboard]')
. ([scriptblock]::Create($definition))
function Get-WeComWindowInfo{param($Hwnd)return @{X=0;Y=0;W=1000;H=800;Pid=7}}
function Get-WeComPlainClipboard{if($script:ReadFails){throw 'synthetic_read_failure'};return @{empty=$false;text=[WeComWin32]::Text}}
function New-WeComClipboardOwner{$w=[pscustomobject]@{Handle=[IntPtr]303};$w|Add-Member ScriptMethod Dispose {};return $w}
function Send-WeComTitleDoubleClick{param($Hwnd,$ScreenX,$ScreenY)$script:TitleClicks++}
function Send-WeComAttachChordKey{param($Hwnd,$Vk)
 $script:Chords++
 if($Vk-eq0x43){[WeComWin32]::Text=if($script:RemoteTakeover){'remote-data'}else{'孙晨'};[WeComWin32]::Owner=if($script:RemoteTakeover){404}else{202};[WeComWin32]::Sequence++};return $true
}
function Start-Sleep{param($Milliseconds)}
function Throw-DriverError{param($Code,$Message)throw ('WECOMDRIVE|'+$Code+'|'+$Message)}
function Write-DriverLog{param($Message)[void]$script:Logs.Add($Message)}
function Assert-True($Value,[string]$Why){if(-not$Value){throw $Why}}
function Run-Case([bool]$ReadFails,[bool]$RemoteTakeover,[bool]$RejectRestore,[bool]$RejectInitial){
 [WeComWin32]::Sequence=10;[WeComWin32]::Owner=404;[WeComWin32]::Text='private-secret';[WeComWin32]::RejectRestore=$RejectRestore;[WeComWin32]::RejectInitial=$RejectInitial;[WeComWin32]::Exchanges=0;[TitleClipboard]::Reads=0
 $script:ReadFails=$ReadFails;$script:RemoteTakeover=$RemoteTakeover;$script:Logs=New-Object Collections.ArrayList;$script:TitleClicks=0;$script:Chords=0
 $title=$null;$failure=''
 try{$title=Get-WeComCopiedTitle -Hwnd 101 -Section contact -Point @{x=440;y=38;source='glm'}}catch{$failure=$_.Exception.Message}
 return @{title=$title;failure=$failure;logs=@($script:Logs);exchanges=[WeComWin32]::Exchanges;text=[WeComWin32]::Text;reads=[TitleClipboard]::Reads;titleClicks=$script:TitleClicks;chords=$script:Chords}
}
$r=Run-Case $true $false $false
Assert-True ($r.failure-like'*synthetic_read_failure*'-and$r.exchanges-eq0) 'initial read failure must not claim a restore failure or touch clipboard'
Assert-True (($r.logs-join';')-match'original_read_begin.*owner_pid=49232') 'begin marker must identify remote owner before blocking read'
$r=Run-Case $false $false $false $true
Assert-True ($r.failure-like'*clipboard_changed*'-and$r.failure-notmatch'clipboard_restore_failed|restore='-and$r.exchanges-eq1-and$r.text-ceq'private-secret') 'initial CAS rejection must preserve the primary error and leave clipboard unchanged'
Assert-True ($r.reads-eq0-and$r.titleClicks-eq0-and$r.chords-eq0-and($r.logs-join';')-notmatch'restore_read|title_ui|copy_chord') 'initial CAS rejection must skip restore reads and all title UI input'
$r=Run-Case $false $true $false
Assert-True ($r.failure-like'*clipboard_changed;restore=external_owner*'-and$r.exchanges-eq1-and$r.reads-eq0) 'remote takeover must skip blocking OLE read and not claim a failed restore CAS'
$r=Run-Case $false $false $false
Assert-True ($r.failure-eq''-and$r.title-ceq'孙晨'-and$r.text-ceq'private-secret'-and$r.exchanges-eq2) 'exact copied title requires successful safe clipboard restoration'
Assert-True (($r.logs-join';')-notmatch'private-secret|孙晨|AidWecomTitle') 'diagnostics must not leak clipboard or title text'
$r=Run-Case $false $false $true
Assert-True ($r.failure-like'*clipboard_restore_failed:cas_rejected*'-and$r.exchanges-eq2) 'actual CAS rejection must be reported separately'
Write-Output 'PS_TITLE_COPY_DIAGNOSTICS_OK cases=5'
