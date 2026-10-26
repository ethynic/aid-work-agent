param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'

# Execute production PS policy and C# policy helper, replacing only OS boundaries.
# No native clipboard, window, keyboard, mouse or enterprise-policy API is called.
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $DriverDir '_common.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw($errors|Out-String)}
foreach($fn in $ast.FindAll({param($n)$n-is[Management.Automation.Language.FunctionDefinitionAst]},$true)){
 . ([scriptblock]::Create($fn.Extent.Text))
}
$initialize=$ast.FindAll({param($n)$n-is[Management.Automation.Language.FunctionDefinitionAst]-and$n.Name-eq'Initialize-WeComWin32'},$true)[0]
$native=$initialize.FindAll({param($n)$n-is[Management.Automation.Language.StringConstantExpressionAst]-and$n.Value.Contains('public class WeComWin32')},$true)[0].Value
$start=$native.IndexOf('    public static bool IsPersonalClipboard() {')
$end=$native.IndexOf('    static IntPtr AllocateClipboardText(',$start)
if($start-lt0-or$end-le$start){throw 'production policy helper was not found'}
$actualPolicy=$native.Substring($start,$end-$start)
$managed=@'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class PolicyCore {
 public static int Hr,ThrowKind,Calls,Allocated,FreeAttempts,Freed;
 public static bool NonNull,FreeFails;
 public static HashSet<IntPtr> Buffers=new HashSet<IntPtr>();
 static int EdpGetEnterpriseIdForClipboard(out IntPtr id) {
  Calls++;id=IntPtr.Zero;
  if(NonNull){id=Marshal.AllocHGlobal(2);Marshal.WriteInt16(id,0);Buffers.Add(id);Allocated++;}
  if(ThrowKind==1)throw new DllNotFoundException();
  if(ThrowKind==2)throw new EntryPointNotFoundException();
  return Hr;
 }
 static IntPtr GetProcessHeap(){return (IntPtr)999;}
 static bool HeapFree(IntPtr heap,uint flags,IntPtr memory){
  FreeAttempts++;
  if(heap!=(IntPtr)999||flags!=0||!Buffers.Contains(memory))throw new InvalidOperationException("wrong heap/pointer");
  if(FreeFails)return false;
  Buffers.Remove(memory);Marshal.FreeHGlobal(memory);Freed++;return true;
 }
 public static void Cleanup(){foreach(IntPtr p in Buffers)Marshal.FreeHGlobal(p);Buffers.Clear();}
 public static void Reset(){Cleanup();Hr=ThrowKind=Calls=Allocated=FreeAttempts=Freed=0;NonNull=FreeFails=false;}
__ACTUAL_POLICY__
}
public class PolicyData {
 public string[] Snapshot; public string[] GetFormats(bool convert){return Snapshot??PolicyClipboardFacade.Formats;}
}
public class PolicyClipboardFacade {
 public static string[] Formats;
 public static bool HasText=true,Empty,EmptyView; public static int Reads; public static Queue<int> Plan=new Queue<int>();
 public static string Text;
 public static object GetDataObject(){
  Reads++;EmptyView=false;int step=Plan.Count>0?Plan.Dequeue():0; if(step==6){EmptyView=true;return new PolicyData();}
  if(step==1)return null;
  if(step==2)return new PolicyData{Snapshot=new string[0]};
  if(step>=3){Text="external";Empty=false;HasText=true;WeComWin32.Sequence++;WeComWin32.Owner=999;
   Formats=step==4?new[]{"UnicodeText","HTML Format"}:step==5?new[]{"UnicodeText","EnterpriseDataProtectionId"}:new[]{"UnicodeText"};
   if(step==5)PolicyCore.NonNull=true;}
  return new PolicyData();
 }
 public static bool ContainsText(){return HasText;}
 public static string GetText(){return EmptyView?"":Text;}
}
public class WeComWin32 {
 public static int Writes,Exchanges,Owner,FailChord; public static uint Sequence;
 public static bool IsPersonalClipboard(){return PolicyCore.IsPersonalClipboard();}
 public static uint GetClipboardSequenceNumber(){return Sequence;}
 public static IntPtr GetClipboardOwner(){return (IntPtr)Owner;}
 public static uint GetWindowThreadProcessId(IntPtr h,out int pid){pid=(h.ToInt64()==101||h.ToInt64()==202)?42:84;return 1;}
 public static bool ExchangeClipboardText(IntPtr owner,uint sequence,string previous,bool wasEmpty,
   string next,bool nextEmpty,out uint written){Exchanges++;written=Sequence;
    if(sequence!=Sequence||wasEmpty!=PolicyClipboardFacade.Empty||(!wasEmpty&&previous!=PolicyClipboardFacade.Text))return false;
    Writes++;PolicyClipboardFacade.Text=next;PolicyClipboardFacade.Empty=nextEmpty;PolicyClipboardFacade.HasText=!nextEmpty;
    PolicyClipboardFacade.Formats=nextEmpty?new string[0]:new[]{"UnicodeText"};Owner=303;Sequence++;written=Sequence;Sequence+=3;return true;}
}
'@
Add-Type ($managed.Replace('__ACTUAL_POLICY__',$actualPolicy))
$plain=$ast.FindAll({param($n)$n-is[Management.Automation.Language.FunctionDefinitionAst]-and$n.Name-eq'Get-WeComPlainClipboard'},$true)[0].Extent.Text
$plain=$plain.Replace('[Windows.Forms.Clipboard]','[PolicyClipboardFacade]').Replace('Add-Type -AssemblyName System.Windows.Forms','')
. ([scriptblock]::Create($plain))
function Initialize-WeComWin32{}
function Start-Sleep{param($Milliseconds)if($Milliseconds-eq75-and$script:expireCopyWait){$script:expireCopyWait=$false;[Threading.Thread]::Sleep($script:expireDelay)}}
function Get-WeComWindowInfo{param($Hwnd)return @{Hwnd=101;Pid=42;Class='WeWorkWindow';Visible=$true;X=0;Y=0;W=1280;H=1000}}
function Get-WeComSearchBoxState{param($MainHwnd,$Tag)return @{State='unknown';ContentPos=@(268,36);TokensRaw=@(
 @{text='fixture';x0=208;x1=328;y0=26;y1=46},@{text='+';x0=368;x1=384;y0=28;y1=44})}}
function New-WeComClipboardOwner{$script:owners++;$w=[pscustomobject]@{Handle=[IntPtr]303};$w|Add-Member ScriptMethod Dispose {};return $w}
function Send-WeComAttachChordKey{param($Hwnd,$Vk)$script:keys++;if($Vk-eq[WeComWin32]::FailChord){return $false};if($Vk-eq0x43){[PolicyClipboardFacade]::Text='fixture';[PolicyClipboardFacade]::Empty=$false;[PolicyClipboardFacade]::HasText=$true;[PolicyClipboardFacade]::Formats=@('UnicodeText');[WeComWin32]::Owner=202;[WeComWin32]::Sequence++};return $true}
function Send-WeComClick{param($Hwnd,$ScreenX,$ScreenY)$script:clicks++;throw 'FORBIDDEN_CLICK'}
function Assert-Intent($Value,$Why){if(-not$Value){throw $Why}}
function Reset-Case{
 [PolicyCore]::Reset();[WeComWin32]::Writes=0;[WeComWin32]::Exchanges=0;[WeComWin32]::FailChord=0;[WeComWin32]::Sequence=10;[WeComWin32]::Owner=999;[PolicyClipboardFacade]::Empty=$false;[PolicyClipboardFacade]::Reads=0;[PolicyClipboardFacade]::Plan.Clear()
 [PolicyClipboardFacade]::Formats=@('UnicodeText','Text','EnterpriseDataProtectionId')
 [PolicyClipboardFacade]::HasText=$true;[PolicyClipboardFacade]::Text="  fixture`r`noriginal  "
 $script:owners=0;$script:keys=0;$script:clicks=0;$script:expireCopyWait=$false;$script:expireDelay=130;[PolicyClipboardFacade]::EmptyView=$false
}
function Assert-Rejected{
 $failure='';try{Get-WeComPlainClipboard|Out-Null}catch{$failure=$_.Exception.Message}
 Assert-Intent ($failure-ne'') 'actual plain policy must reject this clipboard'
 $r=Test-WeComSearchClipboardEcho -MainHwnd 101 -Query 'fixture' -TimeoutMs 100
 Assert-Intent (-not$r.matched-and[WeComWin32]::Writes-eq0-and$owners-eq0-and$keys-eq0-and$clicks-eq0) 'rejection must occur before owner creation, any write or input'
}
$script:passes=0;$script:failures=New-Object 'Collections.Generic.List[string]'
function Case($Name,[scriptblock]$Test){
 Reset-Case
 try{&$Test;if(-not[PolicyCore]::FreeFails){Assert-Intent ([PolicyCore]::Buffers.Count-eq0) 'all returned policy buffers must be released'};
  $script:passes++;Write-Output('PASS '+$Name)}
 catch{$script:failures.Add($Name+': '+$_.Exception.Message);Write-Output('FAIL '+$Name+': '+$_.Exception.Message)}
 finally{[PolicyCore]::Cleanup()}
}
Case 'ordinary plaintext retains old behavior without consulting EDP API' {
 [PolicyClipboardFacade]::Formats=@('UnicodeText','Text');[PolicyCore]::ThrowKind=1
 $r=Get-WeComPlainClipboard
 Assert-Intent (-not$r.empty-and$r.text-ceq[PolicyClipboardFacade]::Text-and[PolicyCore]::Calls-eq0) 'plain data without marker must not acquire a new platform dependency'
}
Case 'personal marker accepts only successful NULL identity and preserves exact text' {
 $r=Get-WeComPlainClipboard
 Assert-Intent (-not$r.empty-and$r.text-ceq[PolicyClipboardFacade]::Text) 'harmless personal marker must not reject or normalize original plaintext'
 Assert-Intent ([PolicyCore]::Calls-eq1-and[PolicyCore]::FreeAttempts-eq0) 'NULL enterprise ID must never be freed or interpreted'
}
Case 'nonnull identity even an empty identity buffer is protected and released once' {
 [PolicyCore]::NonNull=$true;Assert-Rejected
 Assert-Intent ([PolicyCore]::Allocated-eq2-and[PolicyCore]::FreeAttempts-eq2-and[PolicyCore]::Freed-eq2) 'each actual policy invocation must release its returned pointer exactly once'
}
Case 'failed or nonzero HRESULT never makes NULL identity personal' {
 foreach($hr in @(-2147467259,1)){
  Reset-Case;[PolicyCore]::Hr=$hr;Assert-Rejected
  Assert-Intent ([PolicyCore]::FreeAttempts-eq0) 'no identity pointer means no free on API failure'
 }
}
Case 'failed HRESULT still releases any returned identity buffer' {
 [PolicyCore]::Hr=-2147467259;[PolicyCore]::NonNull=$true;Assert-Rejected
 Assert-Intent ([PolicyCore]::Allocated-eq[PolicyCore]::Freed) 'failure return cannot bypass finally cleanup'
}
Case 'missing DLL or entry point rejects marker before clipboard writes' {
 foreach($kind in @(1,2)){Reset-Case;[PolicyCore]::ThrowKind=$kind;Assert-Rejected}
}
Case 'API exception after assigning pointer still frees the buffer' {
 [PolicyCore]::ThrowKind=2;[PolicyCore]::NonNull=$true;Assert-Rejected
 Assert-Intent ([PolicyCore]::Allocated-eq[PolicyCore]::Freed) 'exception path must release any returned allocation'
}
Case 'marker plus any rich or unknown format stays rejected without policy probing' {
 foreach($format in @('HTML Format','Rich Text Format','Bitmap','FileDrop','unknown-format')){
  Reset-Case;[PolicyClipboardFacade]::Formats=@('UnicodeText','EnterpriseDataProtectionId',$format);Assert-Rejected
  Assert-Intent ([PolicyCore]::Calls-eq0) 'EDP exception must not create a rich-format fallback'
 }
}
Case 'marker without actual text never invokes enterprise policy or writes' {
 [PolicyClipboardFacade]::HasText=$false;Assert-Rejected
 Assert-Intent ([PolicyCore]::Calls-eq0) 'marker alone cannot manufacture plaintext'
}
Case 'HeapFree failure is explicit and cannot admit or write protected clipboard' {
 [PolicyCore]::NonNull=$true;[PolicyCore]::FreeFails=$true
 $failure='';try{[PolicyCore]::IsPersonalClipboard()|Out-Null}catch{$failure=$_.Exception.Message}
 Assert-Intent ($failure-match'CLIPBOARD_PROTECTION_CHECK_FAILED') 'failed cleanup must surface explicitly'
 Assert-Rejected
 Assert-Intent ([PolicyCore]::FreeAttempts-eq3-and[PolicyCore]::Freed-eq0) 'each failed release must be attempted once; harness cleans its injected failed frees afterward'
}
Case 'null or no-formats first object refreshes to personal plaintext once' {
 foreach($step in @(1,2)){
  Reset-Case;[PolicyClipboardFacade]::Plan.Enqueue($step);[PolicyClipboardFacade]::Plan.Enqueue(0)
  $r=Get-WeComPlainClipboard
  Assert-Intent (-not$r.empty-and$r.text-ceq[PolicyClipboardFacade]::Text-and[PolicyClipboardFacade]::Reads-eq2) 'transient OLE object must be refreshed rather than reusing empty snapshot'
 }
}
Case 'permanent empty reads are bounded and never invent text' {
 foreach($step in @(1,2)){
  Reset-Case;[PolicyClipboardFacade]::Plan.Enqueue($step);[PolicyClipboardFacade]::Plan.Enqueue($step);[PolicyClipboardFacade]::Plan.Enqueue(0)
  $r=Get-WeComPlainClipboard
  Assert-Intent ($r.empty-and[PolicyClipboardFacade]::Reads-eq2-and[PolicyClipboardFacade]::Plan.Count-eq1) 'read at most two objects, even if a third might later appear'
 }
}
Case 'actually empty original clipboard is restored empty after nonce and copy' {
 [PolicyClipboardFacade]::Empty=$true;[PolicyClipboardFacade]::Text='';[PolicyClipboardFacade]::HasText=$false;[PolicyClipboardFacade]::Formats=@()
 $r=Test-WeComSearchClipboardEcho -MainHwnd 101 -Query 'fixture' -TimeoutMs 100
 Assert-Intent ($r.matched-and$r.restored-and[PolicyClipboardFacade]::Empty) 'an actually empty original is distinct from missing evidence for an owned nonce'
}
Case 'receipt null or format0 then published nonce verifies and restores' {
 foreach($step in @(1,2)){
  Reset-Case;foreach($s in @(0,$step,0,0,0)){[PolicyClipboardFacade]::Plan.Enqueue($s)}
  $saved=[PolicyClipboardFacade]::Text
  $r=Test-WeComSearchClipboardEcho -MainHwnd 101 -Query 'fixture' -TimeoutMs 100
  Assert-Intent ($r.matched-and$r.restored-and[PolicyClipboardFacade]::Text-ceq$saved) 'same-sequence transient empty publication must not strand nonce or abandon original'
 }
}
Case 'retry cannot discard new rich or enterprise-protected formats' {
 foreach($step in @(4,5)){
  Reset-Case;[PolicyClipboardFacade]::Plan.Enqueue(1);[PolicyClipboardFacade]::Plan.Enqueue($step)
  $r=Test-WeComSearchClipboardEcho -MainHwnd 101 -Query 'fixture' -TimeoutMs 100
  Assert-Intent (-not$r.matched-and[WeComWin32]::Writes-eq0-and$owners-eq0-and$keys-eq0) 'second object must undergo full policy checks before any clipboard write'
 }
}
Case 'foreign sequence change while refreshing original cannot be overwritten by CAS' {
 [PolicyClipboardFacade]::Plan.Enqueue(1);[PolicyClipboardFacade]::Plan.Enqueue(3)
 $r=Test-WeComSearchClipboardEcho -MainHwnd 101 -Query 'fixture' -TimeoutMs 100
 Assert-Intent (-not$r.matched-and[WeComWin32]::Writes-eq0-and[PolicyClipboardFacade]::Text-ceq'external'-and$keys-eq0) 'outer original sequence and native CAS remain mandatory after refresh'
}
Case 'permanently empty own receipt reports possible unrestored original without blind write' {
 foreach($s in @(0,1,1,1,1)){[PolicyClipboardFacade]::Plan.Enqueue($s)}
 $r=Test-WeComSearchClipboardEcho -MainHwnd 101 -Query 'fixture' -TimeoutMs 100
 Assert-Intent (-not$r.matched-and-not$r.restored-and$r.reason-eq'clipboard_restore_failed') 'same hidden owner without readable nonce still cannot prove restoration'
 Assert-Intent ([WeComWin32]::Writes-eq1-and$keys-eq0) 'unreadable owned clipboard must never be blindly overwritten'
}
Case 'permanently empty copy read cannot hide possible unrestored original' {
 $script:expireCopyWait=$true
 foreach($s in @(0,0,1,1,0)){[PolicyClipboardFacade]::Plan.Enqueue($s)}
 $r=Test-WeComSearchClipboardEcho -MainHwnd 101 -Query 'fixture' -TimeoutMs 100
 Assert-Intent (-not$r.matched-and-not$r.restored-and$r.reason-eq'clipboard_restore_failed') 'two empty copy-result reads are missing proof, not an ordinary nonmatching query'
 Assert-Intent ([WeComWin32]::Writes-eq1) 'missing copy text must not authorize blind restoration'
}
Case 'foreign write during receipt refresh cannot establish nonce receipt or be restored over' {
 foreach($s in @(0,1,3,0)){[PolicyClipboardFacade]::Plan.Enqueue($s)}
 $r=Test-WeComSearchClipboardEcho -MainHwnd 101 -Query 'fixture' -TimeoutMs 100
 Assert-Intent (-not$r.matched-and-not$r.restored-and[WeComWin32]::Writes-eq1-and$keys-eq0) 'receipt refresh must retain sequence and exact hidden owner proof'
 Assert-Intent ([PolicyClipboardFacade]::Text-ceq'external') 'external clipboard arriving after nonce must remain authoritative'
}
Case 'finally may refresh a transiently empty own nonce and safely recover original' {
 foreach($step in @(1,2)){
  Reset-Case;$saved=[PolicyClipboardFacade]::Text;[WeComWin32]::FailChord=0x41
  foreach($s in @(0,0,$step,0)){[PolicyClipboardFacade]::Plan.Enqueue($s)}
  $r=Test-WeComSearchClipboardEcho -MainHwnd 101 -Query 'fixture' -TimeoutMs 100
  Assert-Intent (-not$r.matched-and$r.restored-and[PolicyClipboardFacade]::Text-ceq$saved) 'failed query copying must still recover a readable exact owned nonce after one refresh'
 }
}
Case 'false-empty original cannot overwrite underlying nonempty data through native CAS' {
 $saved=[PolicyClipboardFacade]::Text
 foreach($s in @(1,1,0)){[PolicyClipboardFacade]::Plan.Enqueue($s)}
 $r=Test-WeComSearchClipboardEcho -MainHwnd 101 -Query 'fixture' -TimeoutMs 100
 Assert-Intent (-not$r.matched-and[WeComWin32]::Writes-eq0-and$keys-eq0) 'missing OLE evidence cannot bypass native empty-format precondition'
 Assert-Intent ([PolicyClipboardFacade]::Text-ceq$saved) 'unread original contents must remain intact'
}
Case 'late nonempty copy before original deadline may prove and restore after transient empty' {
 foreach($variant in @('null-objects','empty-string')){
  Reset-Case;$saved=[PolicyClipboardFacade]::Text
  $steps=if($variant-eq'null-objects'){@(0,0,1,1,0,0)}else{@(0,0,6,0,0)}
  foreach($s in $steps){[PolicyClipboardFacade]::Plan.Enqueue($s)}
  $r=Test-WeComSearchClipboardEcho -MainHwnd 101 -Query 'fixture' -TimeoutMs 100
  Assert-Intent ($r.matched-and$r.restored-and[PolicyClipboardFacade]::Text-ceq$saved) 'empty publications are not proof, but a later exact stable copy within original deadline is valid'
 }
}
Case 'actual search Body reports permanent copy-empty restoration uncertainty without retyping' {
 $tokens=$null;$errors=$null
 $searchAst=[Management.Automation.Language.Parser]::ParseFile((Join-Path $DriverDir 'chat-search.ps1'),[ref]$tokens,[ref]$errors)
 if($errors.Count){throw($errors|Out-String)}
 $command=$searchAst.FindAll({param($n)$n-is[Management.Automation.Language.CommandAst]-and$n.GetCommandName()-eq'Invoke-DriverMain'},$true)[0]
 $body=($command.CommandElements|Where-Object{$_-is[Management.Automation.Language.ScriptBlockExpressionAst]}|Select-Object -First 1).ScriptBlock.GetScriptBlock()
 function Resolve-WeComMainWindow{return 101}
 function Write-DriverLog{param($Msg)}
 function Invoke-WeComJev{param($StateText,$Questions)return @{used=$false}}
 function Send-WeComClick{param($Hwnd,$ScreenX,$ScreenY)$script:clicks++;return 101}
 function Send-WeComText{param($Hwnd,$Text)$script:typed++}
 function Wait-WeComWindow{param($ClassName,$TimeoutMs)$script:overlay++;throw 'FORBIDDEN_OVERLAY'}
 function Get-WeComSearchBoxState{param($MainHwnd,$Tag)
  if($Tag-in@('pre','focus')){return @{State='empty';Content='';PlaceholderPos=@(224,36);TokensRaw=@(@{text='搜索';x0=208;x1=240;y0=28;y1=44})}}
  return @{State='unknown';Content='clipped';ContentPos=@(268,36);TokensRaw=@(@{text='fixture';x0=208;x1=328;y0=26;y1=46},@{text='+';x0=368;x1=384;y0=28;y1=44})}
 }
 $script:typed=0;$script:overlay=0;$script:expireCopyWait=$true;$script:expireDelay=1600
 foreach($s in @(0,0,1,1,0)){[PolicyClipboardFacade]::Plan.Enqueue($s)}
 $Query='fixture';$ArtifactDir='unused';$failure=''
 try{&$body|Out-Null}catch{$failure=$_.Exception.Message}
 Assert-Intent ($failure-like'WECOMDRIVE|UI_CHANGED|*剪贴板写入/恢复失败*原内容可能未恢复*') 'actual user-visible failure must explain restoration uncertainty'
 Assert-Intent ($typed-eq1-and$overlay-eq0-and[WeComWin32]::Writes-eq1) 'do not retype, proceed to overlay or blindly restore after copy publication deadline'
}
if($failures.Count){throw($failures-join"`n")}
Write-Output('PS_CLIPBOARD_POLICY_OK cases='+$passes)
