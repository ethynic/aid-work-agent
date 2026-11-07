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
function Clear-WeComFocusedInput{param($Hwnd)return $true}
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
 Assert-Intent ([WeComWin32]::Writes-eq0-and$owners-eq0-and$keys-eq0-and$clicks-eq0) 'rejected clipboard must not cause a write or input'
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
 Assert-Intent ([PolicyCore]::Allocated-eq1-and[PolicyCore]::FreeAttempts-eq1-and[PolicyCore]::Freed-eq1) 'each actual policy invocation must release its returned pointer exactly once'
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
 Assert-Intent ([PolicyCore]::FreeAttempts-eq2-and[PolicyCore]::Freed-eq0) 'each failed release must be attempted once; harness cleans its injected failed frees afterward'
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
if($failures.Count){throw($failures-join"`n")}
Write-Output('PS_CLIPBOARD_POLICY_OK cases='+$passes)
