param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference='Stop'
. (Join-Path $DriverDir '_common.ps1')
$searchStateProducer=(Get-Item Function:\Get-WeComSearchBoxState).ScriptBlock
# Recorded OS boundary: production PS functions execute unchanged, with no real clipboard/window access.
Add-Type @'
using System;
public class WeComWin32 {
 public static string Text, CopyPayload;
 public static bool Empty, NonText, WindowDisposed, WriteFail;
 public static uint Sequence;
 public static int Owner, Exchanges, RaceOnExchange, FailOnExchange, PublishAdvance, FailAfterWriteOnExchange;
 public static bool ForeignOwnerAfterWrite;
 public static string FailureCode;
 public static uint GetClipboardSequenceNumber() { return Sequence; }
 public static IntPtr GetClipboardOwner() { return (IntPtr)Owner; }
 public static uint GetWindowThreadProcessId(IntPtr hwnd,out int pid) {
  pid = (hwnd.ToInt32()==101 || hwnd.ToInt32()==201) ? 42 : 77;return 1;
 }
 public static bool ExchangeClipboardText(IntPtr hwnd,uint expected,string previous,bool wasEmpty,
  string next,bool nextEmpty,out uint written) {
  Exchanges++;written=Sequence;
  if(Exchanges==RaceOnExchange) {Text="external-write";Empty=false;Owner=777;Sequence++;}
  if(Sequence!=expected || Empty!=wasEmpty || (!Empty && Text!=previous))return false;
  if(WriteFail || Exchanges==FailOnExchange)throw new InvalidOperationException(FailureCode);
  Text=next;Empty=nextEmpty;NonText=false;Owner=99;Sequence++;written=Sequence;
  Sequence+=(uint)PublishAdvance; // CloseClipboard publishes synthesized formats after the out stamp.
  if(Exchanges==1 && ForeignOwnerAfterWrite) {Owner=777;Sequence++;}
  if(Exchanges==FailAfterWriteOnExchange)throw new InvalidOperationException("CLIPBOARD_CLOSE_FAILED");
  return true;
 }
}
'@
function Assert-True($Value,$Why) { if(-not $Value){throw $Why} }
function Start-Sleep {param($Milliseconds)}
function Get-WeComWindowInfo {param($Hwnd)
 $script:windowReads++
 return @{Hwnd=101;Pid=42;Class='WeWorkWindow';Visible=$true;X=1277;Y=0;
  W=$(if($script:moveOnRead-eq$script:windowReads){1281}else{1280});H=1392}
}
function Get-WeComSearchBoxState {param($MainHwnd,$Tag)
 if($Tag-eq'focus'-and$script:useProducerFocus){return $script:state}
 if($Tag-eq'safe-cleared'){return @{State=$(if($script:clearEmpty){'empty'}else{'unknown'})}}
 if($Tag-in@('pre','focus')){return @{State='empty';Content='';ContentPos=$null;PlaceholderPos=@(224,36);
  TokensRaw=@(@{text='搜索';x0=208;x1=240;y0=28;y1=44},@{text='+';x0=369;x1=384;y0=31;y1=45})}}
 return $script:state
}
function Send-WeComClick {param($Hwnd,$ScreenX,$ScreenY)
 [void]$script:actions.Add(@{kind='click';x=$ScreenX;y=$ScreenY});return 101
}
function Get-WeComPlainClipboard {
 $script:clipboardReads++
 if([WeComWin32]::NonText-or$script:clipboardReadFails){throw 'CLIPBOARD_UNAVAILABLE'}
 if($script:copyReadFails-and$script:clipboardReads-eq3){throw 'COPY_READ_FAILED'}
 if($script:restoreReadFails-and$script:clipboardReads-ge4){throw 'RESTORE_READ_FAILED'}
 $value=@{empty=[WeComWin32]::Empty;text=[WeComWin32]::Text}
 if($script:advanceOnReceiptRead-and$script:clipboardReads-eq2){[WeComWin32]::Sequence++}
 if($script:raceOnReceiptRead-and$script:clipboardReads-eq2){
  [WeComWin32]::Text='external-write';[WeComWin32]::Owner=777;[WeComWin32]::Sequence++
 }
 if($script:raceOnCopyRead-and$script:clipboardReads-eq3){
  [WeComWin32]::Text='external-write';[WeComWin32]::Owner=777;[WeComWin32]::Sequence++
 }
 return $value
}
function New-WeComClipboardOwner {
 $window=[pscustomobject]@{Handle=[IntPtr]99}
 $window|Add-Member ScriptMethod Dispose {
  [WeComWin32]::WindowDisposed=$true
  if($script:disposeFails){throw 'OWNER_DISPOSE_FAILED'}
 }
 return $window
}
function Send-WeComAttachChordKey {param($Hwnd,$Vk)
 [void]$script:actions.Add(@{kind='chord';vk=$Vk})
 if($Vk-eq$script:failedChord){return $false}
 if($Vk-eq0x43-and-not$script:noCopy){
  [WeComWin32]::Text=[WeComWin32]::CopyPayload;[WeComWin32]::Empty=$false
  [WeComWin32]::Owner=$(if($script:foreignCopy){777}else{201});[WeComWin32]::Sequence++
 }
 return $true
}
function Reset-Case {
 [WeComWin32]::Text='original-text';[WeComWin32]::Empty=$false;[WeComWin32]::NonText=$false
 [WeComWin32]::WindowDisposed=$false;[WeComWin32]::Sequence=10;[WeComWin32]::Owner=77
 [WeComWin32]::Exchanges=0;[WeComWin32]::RaceOnExchange=0;[WeComWin32]::WriteFail=$false
 [WeComWin32]::FailOnExchange=0;[WeComWin32]::FailureCode='CLIPBOARD_WRITE_FAILED'
 [WeComWin32]::PublishAdvance=0;[WeComWin32]::FailAfterWriteOnExchange=0;[WeComWin32]::ForeignOwnerAfterWrite=$false
 [WeComWin32]::CopyPayload='full-query'
 $script:actions=New-Object Collections.ArrayList
 $script:windowReads=0;$script:moveOnRead=0;$script:clipboardReads=0;$script:failedChord=0
 $script:clearEmpty=$true;$script:noCopy=$false;$script:foreignCopy=$false
 $script:clipboardReadFails=$false;$script:raceOnCopyRead=$false
 $script:advanceOnReceiptRead=$false;$script:raceOnReceiptRead=$false
 $script:restoreReadFails=$false;$script:disposeFails=$false;$script:copyReadFails=$false
 $script:useProducerFocus=$false
 $script:state=@{State='unknown';Content='tail-query';ContentPos=@(268,36);PlaceholderPos=$null;
  TokensRaw=@(@{text='tail-query';x0=207;x1=329;y0=25;y1=48},
   @{text='Q!';x0=187;x1=216;y0=29;y1=46},@{text='+';x0=369;x1=384;y0=31;y1=45})}
}
function Invoke-EchoProbe {return Test-WeComSearchClipboardEcho -MainHwnd 101 -Query 'full-query' -TimeoutMs 100}

Reset-Case
$result=Invoke-EchoProbe
Assert-True ($result.matched-and$result.restored) 'fresh exact full copy must verify and restore original text'
Assert-True ([WeComWin32]::Text-ceq'original-text') 'original text must return unchanged'
Assert-True ([WeComWin32]::WindowDisposed) 'hidden clipboard owner must be disposed'
Assert-True (@($actions|Where-Object {$_.kind-eq'chord'-and$_.vk-notin@(0x41,0x43)}).Count-eq0) 'copy verification must never Delete/Enter'

Reset-Case;[WeComWin32]::PublishAdvance=3
$result=Invoke-EchoProbe
Assert-True ($result.matched-and$result.restored-and[WeComWin32]::Text-ceq'original-text') 'Close publication sequence advances must not invalidate fresh copy or abandon the saved clipboard'
Reset-Case;$script:advanceOnReceiptRead=$true
$result=Invoke-EchoProbe
Assert-True (-not$result.matched-and$result.restored-and$actions.Count-eq0-and[WeComWin32]::Text-ceq'original-text') 'unstable receipt must block copying but its still-owned unique nonce must be retired'
Reset-Case;$script:raceOnReceiptRead=$true
$result=Invoke-EchoProbe
Assert-True (-not$result.matched-and-not$result.restored-and$actions.Count-eq0-and[WeComWin32]::Text-ceq'external-write') 'foreign write during receipt must prevent copying and must never be restored over'
Reset-Case;[WeComWin32]::ForeignOwnerAfterWrite=$true
$result=Invoke-EchoProbe
Assert-True (-not$result.matched-and-not$result.restored-and$actions.Count-eq0-and[WeComWin32]::Owner-eq777) 'identical nonce under another owner must not establish a receipt or permit restoration'
Reset-Case;[WeComWin32]::FailAfterWriteOnExchange=1;[WeComWin32]::PublishAdvance=3
$result=Invoke-EchoProbe
Assert-True (-not$result.matched-and$result.restored-and$result.reason-eq'clipboard_close_failed'-and[WeComWin32]::Text-ceq'original-text') 'a close fault after writing must stay explicit while finally recovers our owned unique nonce'

Reset-Case;[WeComWin32]::CopyPayload='tail-query'
$result=Invoke-EchoProbe
Assert-True (-not$result.matched-and$result.restored) 'missing first character must never pass exact matching'
Reset-Case;$script:copyReadFails=$true
$result=Invoke-EchoProbe
Assert-True (-not$result.matched-and-not$result.restored-and$result.reason-eq'clipboard_restore_failed') 'copy may replace the original before its read fails, so unproved restoration must be explicit'
Assert-True ([WeComWin32]::Exchanges-eq1-and[WeComWin32]::Text-ceq'full-query') 'missing copy receipt must never cause a blind restore over an unproved current value'
Reset-Case;[WeComWin32]::CopyPayload=' full-query'
Assert-True (-not(Invoke-EchoProbe).matched) 'clipboard equality must not trim or normalize the copied query'
Reset-Case;[WeComWin32]::CopyPayload=' full query '
[WeComWin32]::Text="original  value`r`n with spaces "
$result=Test-WeComSearchClipboardEcho -MainHwnd 101 -Query ' full query ' -TimeoutMs 100
Assert-True ($result.matched-and$result.restored-and[WeComWin32]::Text-ceq"original  value`r`n with spaces ") 'full query and restored clipboard must retain meaningful spaces and line endings'
Reset-Case;[WeComWin32]::Text='full-query';$script:noCopy=$true
$result=Invoke-EchoProbe
Assert-True (-not$result.matched-and$result.restored-and[WeComWin32]::Text-ceq'full-query') 'old expected clipboard text cannot fake a new copy'
Reset-Case;$script:foreignCopy=$true
Assert-True (-not(Invoke-EchoProbe).matched) 'fresh clipboard data from another owner must not pass'
Assert-True ([WeComWin32]::Text-ceq'full-query') 'external-owned clipboard must not be overwritten on failure'
Reset-Case;[WeComWin32]::NonText=$true
Assert-True (-not(Invoke-EchoProbe).matched-and[WeComWin32]::Exchanges-eq0-and$actions.Count-eq0) 'rich formats must be refused before writing sentinel'
Reset-Case;$script:clipboardReadFails=$true
Assert-True (-not(Invoke-EchoProbe).matched-and[WeComWin32]::Exchanges-eq0) 'unreadable original clipboard must never be overwritten'
Reset-Case;[WeComWin32]::Empty=$true;[WeComWin32]::Text=''
$result=Invoke-EchoProbe
Assert-True ($result.matched-and$result.restored-and[WeComWin32]::Empty) 'originally empty clipboard must be restored empty'
foreach($vk in @(0x41,0x43)){
 Reset-Case;$script:failedChord=$vk
 $result=Invoke-EchoProbe
 Assert-True (-not$result.matched-and$result.restored) 'failed copy chord must not certify the query and must restore our sentinel'
}
foreach($race in @(1,2)){
 Reset-Case;[WeComWin32]::RaceOnExchange=$race
 $result=Invoke-EchoProbe
 Assert-True (-not$result.matched-and[WeComWin32]::Text-ceq'external-write') 'atomic compare-and-swap must preserve an intervening external write'
}
Reset-Case;$script:raceOnCopyRead=$true
Assert-True (-not(Invoke-EchoProbe).matched-and[WeComWin32]::Text-ceq'external-write') 'copy read must have a stable sequence across text access'
Reset-Case;[WeComWin32]::WriteFail=$true
Assert-True ((Invoke-EchoProbe).reason-eq'clipboard_write_failed') 'native Empty/Set failure must remain explicit'
foreach($fault in @('CLIPBOARD_WRITE_FAILED','CLIPBOARD_CLOSE_FAILED')){
 Reset-Case;[WeComWin32]::FailOnExchange=2;[WeComWin32]::FailureCode=$fault
 $result=Invoke-EchoProbe
 Assert-True (-not$result.matched-and-not$result.restored) 'restore write/close error must invalidate previously matched clipboard text'
 Assert-True ($result.reason-eq$(if($fault-eq'CLIPBOARD_WRITE_FAILED'){'clipboard_write_failed'}else{'clipboard_close_failed'})) 'system write/close failure must not be reduced to a generic mismatch'
}
Reset-Case;$script:restoreReadFails=$true
$result=Invoke-EchoProbe
Assert-True (-not$result.matched-and$result.reason-eq'clipboard_restore_failed') 'unreadable clipboard during restore must invalidate a successful copy'
Reset-Case;$script:disposeFails=$true
$result=Invoke-EchoProbe
Assert-True (-not$result.matched-and$result.reason-eq'clipboard_close_failed') 'owner close failure must be explicit and cannot leave matched true'
Reset-Case;$script:moveOnRead=3
$result=Invoke-EchoProbe
Assert-True (-not$result.matched-and$result.restored-and$actions.Count-eq0) 'window geometry change before copying must block all copy chords'

Reset-Case
Assert-True (Clear-WeComSearchBoxV2 -MainHwnd 101) 'unknown state with actual content/plus anchors must safely clear despite missing OCR cross'
Assert-True ($actions.Count-eq1-and$actions[0].x-eq1614-and$actions[0].y-eq38) 'clear must only click observed search cross plus actual window origin'
foreach($invalid in @('missing-content','missing-plus','moved-plus','changed-window','uncleared')){
 Reset-Case
 switch($invalid){
  'missing-content' {$script:state.ContentPos=$null}
  'missing-plus' {$script:state.TokensRaw=$script:state.TokensRaw[0..1]}
  'moved-plus' {$script:state.TokensRaw[2].x0=400;$script:state.TokensRaw[2].x1=415}
  'changed-window' {$script:moveOnRead=2}
  'uncleared' {$script:clearEmpty=$false}
 }
 $failure='';try{Clear-WeComSearchBoxV2 -MainHwnd 101}catch{$failure=$_.Exception.Message}
 Assert-True ($failure-like'WECOMDRIVE|UI_CHANGED|*') 'unproved clear must fail closed'
 Assert-True (@($actions|Where-Object {$_.kind-eq'chord'}).Count-eq0) 'clear must never use keyboard fallback'
 if($invalid-ne'uncleared'){Assert-True ($actions.Count-eq0) 'invalid layout must not be clicked'}
}

# Execute the actual chat-search body up to its external overlay wait boundary.
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $DriverDir 'chat-search.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
$command=$ast.FindAll({param($a)$a-is[Management.Automation.Language.CommandAst]-and$a.GetCommandName()-eq'Invoke-DriverMain'},$true)[0]
$body=($command.CommandElements|Where-Object {$_-is[Management.Automation.Language.ScriptBlockExpressionAst]}|Select-Object -First 1).ScriptBlock.GetScriptBlock()
function Resolve-WeComMainWindow {return 101}
function Write-DriverLog {param($Msg)}
function Send-WeComText {param($Hwnd,$Text)}
function Invoke-WeComJev {param($StateText,$Questions)return @{used=$false}}
function Wait-WeComWindow {param($ClassName,$TimeoutMs)$script:overlayReached=$true;throw 'TEST_OVERLAY_BOUNDARY'}
foreach($case in @('ocr-exact','clipped-copy-exact','copy-mismatch','copy-read-failure','restore-read-failure','restore-write-failure','restore-close-failure')){
 Reset-Case;$script:overlayReached=$false;$Query='full-query'
 if($case-eq'ocr-exact'){$script:state.Content=$Query}
 if($case-eq'copy-mismatch'){[WeComWin32]::CopyPayload='tail-query'}
 if($case-eq'copy-read-failure'){$script:copyReadFails=$true}
 if($case-eq'restore-read-failure'){$script:restoreReadFails=$true}
 if($case-eq'restore-write-failure'){[WeComWin32]::FailOnExchange=2}
 if($case-eq'restore-close-failure'){[WeComWin32]::FailOnExchange=2;[WeComWin32]::FailureCode='CLIPBOARD_CLOSE_FAILED'}
 $failure='';try{& $body}catch{$failure=$_.Exception.Message}
 Assert-True ($overlayReached-eq($case-in@('ocr-exact','clipped-copy-exact'))) 'only a proved complete query with safe restoration may reach the search overlay'
 if($case-eq'ocr-exact'){Assert-True ([WeComWin32]::Exchanges-eq0) 'successful original OCR must not use clipboard at all'}
 if($case-like'restore-*-failure'-or$case-eq'copy-read-failure'){
  Assert-True ($failure-like'*剪贴板写入/恢复失败*原内容可能未恢复*已中止且未重试*') 'clipboard system failure must be visible to the user, not merely logged'
 }
}
# Replay the actual state producer with de-identified OCR boxes, including icon-first ordering.
# Only the screenshot/OCR boundary is synthetic; no identity screenshot enters the project fixture.
function Get-WeComWindowSnapshot {param($Hwnd,$Path)
 Add-Type -AssemblyName System.Drawing
 $bitmap=New-Object Drawing.Bitmap 1086,1392
 try{$bitmap.Save($Path,[Drawing.Imaging.ImageFormat]::Png)}finally{$bitmap.Dispose()}
 [void]$script:producerFiles.Add($Path)
 [void]$script:producerFiles.Add(($Path-replace'\.png$','-crop.png'))
 return @(0,0,1086,1392)
}
function Invoke-WeComChatOcr {param($ImagePath,$Mode)
 return @{boxes=@($script:producerBoxes|ForEach-Object {
  @{text=$_.text;x0=($_.x0-120)*4;x1=($_.x1-120)*4;y0=$_.y0*4;y1=$_.y1*4}
 })}
}
$script:producerFiles=New-Object Collections.ArrayList
try{
 Reset-Case
 $script:producerBoxes=@(@{text='Q !';x0=187;x1=216;y0=29;y1=46},
  @{text='tail-query ×';x0=208;x1=344;y0=29;y1=45},@{text='+';x0=369;x1=384;y0=31;y1=45})
 $produced=& $searchStateProducer -MainHwnd 1 -Tag ('synthetic-'+[guid]::NewGuid().ToString('N'))
 Assert-True ($produced.ContentPos[0]-eq276-and$produced.ContentPos[1]-eq37) 'real body must win when OCR puts the icon first'
 Assert-True (Test-WeComSearchBoxLayout $produced) 'an explicit merged cross inside its observed ROI must remain verifiable'
 $script:state=$produced
 $result=Invoke-EchoProbe
 Assert-True ($result.matched-and$result.restored) 'merged-cross layout still requires fresh exact clipboard evidence'
 Reset-Case;$script:state=$produced
 Assert-True (Clear-WeComSearchBoxV2 -MainHwnd 101) 'merged cross may use the observed clear point with empty readback'

 foreach($variant in @('wide-no-cross','ascii-x','outside-cross-roi','icon-only')){
  Reset-Case
  $token=$script:state.TokensRaw[0];$token.x0=208;$token.x1=344;$token.y0=29;$token.y1=45
  $token.text='tail-query ×'
  switch($variant){
   'wide-no-cross' {$token.text='tail-query'}
   'ascii-x' {$token.text='tail-query x'}
   'outside-cross-roi' {$token.x1=351}
   'icon-only' {$token.text='Q !';$token.x0=187;$token.x1=216}
  }
  $script:state.ContentPos=@([int](($token.x0+$token.x1)/2),[int](($token.y0+$token.y1)/2))
  Assert-True (-not(Test-WeComSearchBoxLayout $script:state)) 'wide text without the exact cross/ROI proof must remain rejected'
 }

 Reset-Case
 $script:producerBoxes=@(@{text='Q short';x0=186;x1=300;y0=29;y1=46},
  @{text='x';x0=331;x1=344;y0=29;y1=45},@{text='+';x0=369;x1=384;y0=31;y1=45})
 $combined=& $searchStateProducer -MainHwnd 1 -Tag ('synthetic-'+[guid]::NewGuid().ToString('N'))
 Assert-True ($combined.ContentPos[0]-eq243-and$combined.State-eq'has_content') 'combined icon/text fallback must retain its previous position'
 $script:state=$combined;$script:useProducerFocus=$true
 Assert-True ((Focus-WeComSearchBox -MainHwnd 101)-eq'placeholder_click') 'existing combined icon/text Focus fallback must remain available'
 Assert-True (-not(Test-WeComSearchBoxLayout $combined)) 'the looser legacy Focus fallback must never authorize clear/copy layout'
}finally{
 foreach($path in $script:producerFiles){Remove-Item -LiteralPath $path -ErrorAction SilentlyContinue}
}
Write-Output 'PS_SEARCH_INPUT_OK'
