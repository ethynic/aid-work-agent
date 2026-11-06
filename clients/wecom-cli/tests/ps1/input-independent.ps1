param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# Parse definitions and driver bodies only. The real wrapper, P/Invokes, OCR,
# network, clipboard and UI are never initialized or executed in this process.
$driverAsts = @{}
foreach ($file in @('_common.ps1', 'message-send.ps1', 'chat-search.ps1')) {
    $parseErrors = $null; $parseTokens = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $DriverDir $file), [ref]$parseTokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
    foreach ($fn in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
        . ([scriptblock]::Create($fn.Extent.Text))
    }
    $driverAsts[$file] = $ast
}
function Get-TestDriverBody($File) {
    $cmd = $driverAsts[$File].FindAll({ param($n)
        $n -is [System.Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Invoke-DriverMain'
    }, $true)[0]
    return ($cmd.CommandElements | Where-Object {
        $_ -is [System.Management.Automation.Language.ScriptBlockExpressionAst]
    } | Select-Object -First 1).ScriptBlock.GetScriptBlock()
}
$sendBody = Get-TestDriverBody 'message-send.ps1'
$searchBody = Get-TestDriverBody 'chat-search.ps1'
$script:ChatInputRx = 0.5; $script:ChatInputRy = 0.9

# The actual Send-WeComText/Click/AttachChord/Enter implementations run against
# these in-memory classes. No class below imports an OS library.
Add-Type @'
using System;
using System.Collections.Generic;
public class WeComWin32 {
    public struct POINT { public int X, Y; }
    public class Msg { public long H, W, L; public uint M; }
    public static List<Msg> Messages = new List<Msg>();
    public static List<string> Audit = new List<string>();
    public static bool DeleteOk = true;
    public static long FailVk = -1;
    public static uint FailMessage = 0;
    public static bool OldWindowAlive = true;
    public static bool RebuildOnClick = false;
    public static bool IsWindow(IntPtr h) { return h.ToInt64() != 101 || OldWindowAlive; }
    public static IntPtr WindowFromPoint(POINT p) { return (IntPtr)101; }
    public static bool ScreenToClient(IntPtr h, ref POINT p) { p.X -= 100; p.Y -= 200; return true; }
    public static uint MapVirtualKeyW(uint v, uint mode) { return v; }
    public static short VkKeyScanW(char c) {
        if (c >= 'a' && c <= 'z') return (short)(c - 'a' + 65);
        if (c >= 'A' && c <= 'Z') return (short)(c + 256);
        if (c >= '0' && c <= '9') return (short)c;
        return -1;
    }
    public static bool PostMessageW(IntPtr h, uint m, IntPtr w, IntPtr l) {
        Messages.Add(new Msg { H=h.ToInt64(), M=m, W=w.ToInt64(), L=l.ToInt64() });
        Audit.Add("post:" + m + ":" + w.ToInt64());
        if (RebuildOnClick && m == 0x202 && h.ToInt64() == 101) OldWindowAlive = false;
        return (m != 0x100 || w.ToInt64() != 46 || DeleteOk) &&
            !(w.ToInt64() == FailVk && m == FailMessage);
    }
}
public class WeComAttach32 {
    public static bool AttachOk = true;
    public static byte[] State = new byte[256];
    public static uint GetCurrentThreadId() { return 1; }
    public static uint GetWindowThreadProcessId(IntPtr h, out uint pid) { pid=7; return 2; }
    public static bool AttachThreadInput(uint a, uint b, bool attach) {
        WeComWin32.Audit.Add(attach ? "attach" : "detach"); return AttachOk;
    }
    public static bool GetKeyboardState(byte[] ks) { Array.Copy(State,ks,256); return true; }
    public static bool SetKeyboardState(byte[] ks) { State=(byte[])ks.Clone(); return true; }
}
'@
function Initialize-WeComWin32 {}
function Initialize-WeComAttachInput {}
function Start-Sleep { param($Milliseconds) }
function Get-WeComWindowInfo { param($Hwnd)
    if ([int64]$Hwnd -eq 202) { return @{ Hwnd=202;Pid=42;Class='WeWorkWindow';Visible=$true;X=300;Y=400;W=1280;H=1000 } }
    return @{ Hwnd=101;Pid=42;Class='WeWorkWindow';Visible=$true;X=100;Y=200;W=1280;H=1000 }
}
function Resolve-WeComMainWindow {
    $script:resolveCalls++
    if ($script:rebuildBeforeClick -and $script:resolveCalls -gt 1) { return 202 }
    return 101
}
function Write-DriverLog { param($Msg) }
function Save-StepShot { param($Hwnd,$Name)
    [void]$script:shots.Add(@{name=$Name;hwnd=$Hwnd})
    return @{path=$Name;left=100;top=200;w=1280;h=1000}
}
function Get-WeComTitlePoint { param($Hwnd,$ImagePath)
    return @{x=435;y=38;source='glm';cache_path='synthetic-title-cache.json'}
}
function Get-WeComCopiedTitle { param($Hwnd,$Section,$Point)
    $script:titleCopyCalls++
    [WeComWin32]::Audit.Add('copy-title')
    if ($script:rebuildAfterTitleCopy) { [WeComWin32]::OldWindowAlive=$false }
    if ($script:wrongVerifyTitle) { return 'another-group' }
    if ($script:lateTitle) { return $script:lateTitle }
    return 'fixture-group'
}
function Save-WeComTitlePoint { param($Point) }
function Set-Clipboard { param($Value)
    $script:clipCalls++; [WeComWin32]::Audit.Add('clipboard')
    if ($script:clipFail) { throw 'fixture clipboard unavailable' }
}
function Wait-WeComWindow { param($ClassName,$TimeoutMs)
    $script:overlayCalls++; throw 'TEST_OVERLAY_BOUNDARY'
}
function Get-WeComSearchBoxState { param($MainHwnd,$Tag)
    [WeComWin32]::Audit.Add('search-state:'+$Tag)
    if ($Tag -eq 'safe-clear') { return @{State=$script:clearState;Content='fixture';Tokens=@()} }
    if ($Tag -eq 'typed') { return @{State='has_content';Content=$script:echo;Tokens=@()} }
    if ($Tag -in @('pre','cleared')) { return @{State='empty';Content='';Tokens=@()} }
    return $script:anchor
}
function Test-WeComSearchClipboardEcho { param($MainHwnd,$Query)
    return @{matched=[string]::Equals($script:echo,$Query,[StringComparison]::Ordinal);
        restored=$true;reason='copied';copied_length=$script:echo.Length}
}
function Invoke-WeComJev { param($StateText,$Questions)
    if ($Questions.ContainsKey('click_which')) {
        $script:focusCriteria=$Questions.click_which.criteria
        return @{used=$script:useFocusJev;answers=@{click_which=@{choice=$script:focusChoice}}}
    }
    if ($Questions.ContainsKey('input_point')) {
        $script:sendCriteria=$Questions
        return @{used=$true;answers=@{input_point=@{choice=$script:inputChoice}}}
    }
    return @{used=$false;reason='fixture offline'}
}
function Invoke-SendChatOcr { param($ImagePath,$Mode)
    if ($ImagePath -eq 'step2-typed.png') { $script:typedOcrCalls++ }
    if ($Mode -eq 'input') { return @{input_empty=$true;texts=@()} }
    if ($Mode -eq 'preview') { return @{texts=@();texts_norm=@()} }
    $title='fixture-group'
    if ($script:changedTitle -and $ImagePath -eq 'step2-typed.png') { $title='fixture-group+' }
    if ($script:wrongVerifyTitle) { $title='another-group' }
    $boxes=@(@{text=$title;x0=500;y0=20;x1=680;y1=40},
        @{text='快速会议';x0=1000;y0=820;x1=1100;y1=840},
        @{text='立即总结';x0=1120;y0=900;x1=1210;y1=920})
    if ($script:placeholder) { $boxes+=@{text='输入消息';x0=660;y0=890;x1=740;y1=910} }
    if ($ImagePath -eq 'step2-typed.png' -and $script:readback) {
        $boxes+=@{text='中文消息第一行第二行';x0=650;y0=920;x1=850;y1=940}
    }
    return @{boxes=$boxes}
}
function Assert-Intent($Pass,$Why) { if (-not $Pass) { throw $Why } }
function Count-Key($Vk) { return @([WeComWin32]::Messages | Where-Object {$_.M -eq 0x100 -and $_.W -eq $Vk}).Count }
function Reset-Test {
    [WeComWin32]::Messages.Clear(); [WeComWin32]::Audit.Clear()
    [WeComAttach32]::AttachOk=$true;[WeComAttach32]::State=New-Object byte[] 256
    [WeComWin32]::DeleteOk=$true;[WeComWin32]::FailVk=-1;[WeComWin32]::FailMessage=0
    [WeComWin32]::OldWindowAlive=$true;[WeComWin32]::RebuildOnClick=$false
    $script:clipCalls=0;$script:clipFail=$false;$script:overlayCalls=0
    $script:shots=New-Object System.Collections.ArrayList
    $script:resolveCalls=0;$script:rebuildBeforeClick=$false;$script:rebuildAfterTitleCopy=$false;$script:lateTitle=''
    $script:titleCopyCalls=0
    $script:readback=$true;$script:placeholder=$false;$script:changedTitle=$false;$script:wrongVerifyTitle=$false
    $script:typedOcrCalls=0
    $script:right='yes';$script:draft='no';$script:inputChoice='B0'
    $script:clearState='empty'
    $script:useFocusJev=$false;$script:focusChoice='T0';$script:echo='中文群'
    $script:sendCriteria=$null;$script:focusCriteria=$null
    $script:anchor=@{State='empty';TokensRaw=@(@{text='搜索';x0=200;y0=30;x1=240;y1=50},
        @{text='+';x0=365;y0=30;x1=385;y1=50});PlaceholderPos=@(220,40);ContentPos=$null}
}
function Run-Send([switch]$Single,[switch]$Verify,[switch]$Skip) {
    $TargetName='fixture-group';$Subtitle='';$Section='group';$ExpectedPrefix=''
    $Text="中文消息第一行`n第二行";if ($Single) {$Text='中文A9'}
    $SkipVerify=[bool]$Skip;$VerifyOnly=[bool]$Verify
    try { $data=& $sendBody; return @{data=$data;error=''} } catch { return @{error=$_.Exception.Message} }
}
function Run-Search {
    $Query='中文群';$ArtifactDir='unused'
    try { & $searchBody | Out-Null;return '' } catch { return $_.Exception.Message }
}
function Test-Intent($Name,[scriptblock]$Test) {
    Reset-Test
    $script:caseCount++
    try {
        & $Test
        Write-Output ('PASS '+$Name)
    } catch {
        $script:failedCases.Add($Name+': '+$_.Exception.Message)
        Write-Output ('FAIL '+$Name+': '+$_.Exception.Message)
    }
}
$script:caseCount=0
$script:failedCases=New-Object 'System.Collections.Generic.List[string]'

Test-Intent 'actual Unicode primitive emits each intended character once to original HWND' {
    Send-WeComText -Hwnd 101 -Text '中文A9a'
    $chars=@([WeComWin32]::Messages | Where-Object {$_.M -eq 0x102})
    Assert-Intent (($chars.W -join ',') -eq '20013,25991,65') 'Chinese/shifted chars must be WM_CHAR only, in order'
    Assert-Intent ((Count-Key 57) -eq 1 -and (Count-Key 65) -eq 1) 'unmodified chars must use one keydown/up pair'
    Assert-Intent ([WeComWin32]::Messages.Count -eq 7) 'no duplicate keydown plus WM_CHAR per character'
    Assert-Intent (@([WeComWin32]::Messages | Where-Object {$_.H -ne 101}).Count -eq 0) 'input must retain original target HWND'
}
Test-Intent 'real attach chord restores keyboard state and detaches' {
    [WeComAttach32]::State[65]=1
    Assert-Intent (Send-WeComAttachChordKey -Hwnd 101 -Vk 0x46) 'attached shortcut should dispatch'
    Assert-Intent ([WeComAttach32]::State[17] -eq 0 -and [WeComAttach32]::State[65] -eq 1) 'Ctrl and existing keys must be restored'
    Assert-Intent ([WeComWin32]::Audit[-1] -eq 'detach') 'thread attachment must not leak'
}
Test-Intent 'failed Ctrl+A dispatch never posts Delete' {
    [WeComAttach32]::AttachOk=$false
    Assert-Intent (-not (Clear-WeComFocusedInput -Hwnd 101)) 'clear reports failed dispatch'
    Assert-Intent ((Count-Key 46) -eq 0) 'failed select-all cannot delete unselected content'
}
foreach ($failedMessage in @(0x100, 0x101)) {
    Test-Intent "failed Ctrl+A message $failedMessage cannot delete, paste or send" {
        [WeComWin32]::FailVk=65;[WeComWin32]::FailMessage=$failedMessage
        $r=Run-Send -Skip
        Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'failed select-all dispatch must reject send'
        Assert-Intent ((Count-Key 46) -eq 0 -and $clipCalls -eq 0 -and (Count-Key 86) -eq 0 -and (Count-Key 13) -eq 0) 'unselected editor must not be deleted or sent'
    }
}
foreach ($failedMessage in @(0x100, 0x101)) {
    Test-Intent "failed Ctrl+V message $failedMessage cannot send" {
        [WeComWin32]::FailVk=86;[WeComWin32]::FailMessage=$failedMessage
        $r=Run-Send -Skip
        Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'failed paste dispatch must reject send'
        Assert-Intent ((Count-Key 13) -eq 0) 'failed paste must not press Enter'
    }
}
Test-Intent 'failed Delete dispatch aborts before clipboard paste' {
    [WeComWin32]::DeleteOk=$false
    $r=Run-Send -Skip
    Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'failed Delete must report pre-send UI error'
    Assert-Intent ($clipCalls -eq 0 -and (Count-Key 86) -eq 0 -and (Count-Key 13) -eq 0) 'failed clear cannot paste or send'
}
Test-Intent 'failed Delete keyup aborts before clipboard paste' {
    [WeComWin32]::FailVk=46;[WeComWin32]::FailMessage=0x101
    $r=Run-Send -Skip
    Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'failed Delete keyup must report pre-send UI error'
    Assert-Intent ($clipCalls -eq 0 -and (Count-Key 86) -eq 0 -and (Count-Key 13) -eq 0) 'failed clear cannot paste or send'
}
Test-Intent 'successful CtrlF focuses search without OCR click or destructive clear' {
    Assert-Intent ((Focus-WeComSearchBox -MainHwnd 101) -eq 'shortcut') 'focus uses the accepted Ctrl+F contract'
    Assert-Intent (@([WeComWin32]::Messages | Where-Object {$_.M -eq 0x201}).Count -eq 0) 'search focus does not click an OCR anchor'
    Assert-Intent ((Count-Key 65) -eq 0 -and (Count-Key 46) -eq 0) 'focus operation cannot destructively clear input'
}
Test-Intent 'single-line send clears target editor then uses original text primitive' {
    $r=Run-Send -Single -Skip
    Assert-Intent ($r.error -eq '') ('single send should work: '+$r.error)
    Assert-Intent ((Count-Key 70) -eq 0 -and (Count-Key 65) -eq 1 -and (Count-Key 46) -eq 1) 'only the target editor is selected and cleared'
    Assert-Intent (@([WeComWin32]::Messages | Where-Object {$_.M -eq 0x201}).Count -eq 1) 'only chat editor should be clicked'
    Assert-Intent ($clipCalls -eq 0 -and (Count-Key 86) -eq 0) 'single-line text must not overwrite clipboard or paste'
    Assert-Intent (@([WeComWin32]::Messages | Where-Object {$_.M -eq 0x102}).Count -eq 3) 'Chinese and shifted characters still use original WM_CHAR primitive'
    Assert-Intent ((Count-Key 13) -eq 1) 'send boundary must be reached only once'
}
Test-Intent 'missing search anchor does not block an already open target conversation' {
    $script:anchor=@{TokensRaw=@();PlaceholderPos=$null;ContentPos=$null}
    $script:clearState='unknown'
    $r=Run-Send -Skip
    Assert-Intent ($r.error -eq '') 'search anchor is irrelevant after target title is confirmed'
    Assert-Intent ((Count-Key 65) -eq 1 -and (Count-Key 46) -eq 1 -and $clipCalls -eq 1 -and (Count-Key 13) -eq 1) 'target editor replacement reaches one send'
}
Test-Intent 'unrelated crop OCR token does not change shortcut focus or chat send' {
    $script:anchor=@{TokensRaw=@(@{text='99';x0=300;y0=30;x1=330;y1=50});PlaceholderPos=$null;ContentPos=$null}
    $script:useFocusJev=$true;$script:focusChoice='T0';$script:draft='yes'
    Assert-Intent ((Focus-WeComSearchBox -MainHwnd 101) -eq 'shortcut') 'search focus ignores unrelated OCR tokens'
    Assert-Intent (@([WeComWin32]::Messages | Where-Object {$_.M -eq 0x201}).Count -eq 0) 'unrelated token cannot authorize a search-focus click'
    $script:clearState='unknown'
    $r=Run-Send -Skip
    Assert-Intent ($r.error -eq '') 'search token does not decide an already open target conversation'
    Assert-Intent ((Count-Key 65) -eq 1 -and (Count-Key 46) -eq 1 -and $clipCalls -eq 1 -and (Count-Key 13) -eq 1) 'target editor replacement is independent of search focus'
}
Test-Intent 'nonempty search does not authorize or block target editor replacement' {
    $script:clearState='has_content';$script:draft='yes'
    $r=Run-Send -Skip
    Assert-Intent ($r.error -eq '') 'unrelated search state must not block a confirmed target chat'
    Assert-Intent ((Count-Key 65) -eq 1 -and (Count-Key 46) -eq 1 -and $clipCalls -eq 1) 'only target editor is cleared and repopulated'
}
Test-Intent 'stale placeholder coordinate does not cause a search click' {
    $script:anchor.PlaceholderPos=@(330,40);$script:useFocusJev=$true;$script:focusChoice='T0'
    Assert-Intent ((Focus-WeComSearchBox -MainHwnd 101) -eq 'shortcut') 'shortcut focus ignores stale OCR coordinates'
    Assert-Intent (@([WeComWin32]::Messages | Where-Object {$_.M -eq 0x201}).Count -eq 0) 'stale source evidence cannot cause a guessed click'
}
Test-Intent 'Jev plus-button choice is ignored by shortcut focus' {
    $script:useFocusJev=$true;$script:focusChoice='T1'
    $method=Focus-WeComSearchBox -MainHwnd 101
    Assert-Intent ($method -eq 'shortcut' -and $null -eq $focusCriteria) 'search focus does not ask Jev for OCR tokens'
    Assert-Intent (@([WeComWin32]::Messages | Where-Object {$_.M -eq 0x201}).Count -eq 0) 'plus button is never clicked'
}
Test-Intent 'attach failure aborts search focus without an OCR fallback' {
    [WeComAttach32]::AttachOk=$false
    $failure='';try { Focus-WeComSearchBox -MainHwnd 101 | Out-Null } catch {$failure=$_.Exception.Message}
    Assert-Intent ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'failed shortcut must abort before clear or input'
    Assert-Intent ((Count-Key 70) -eq 0) 'failed attachment must not post unmodified f'
}
Test-Intent 'Chinese search readback must pass before overlay lookup' {
    $failure=Run-Search
    Assert-Intent ($failure -eq 'TEST_OVERLAY_BOUNDARY' -and $overlayCalls -eq 1) 'complete Chinese echo should advance to result lookup'
    Assert-Intent (@([WeComWin32]::Messages | Where-Object {$_.M -eq 0x102}).Count -eq 3) 'query must use actual Unicode primitive'
}
Test-Intent 'empty or truncated Chinese echo cannot be treated as successful search' {
    foreach($echoValue in @('', '中文')) {
        Reset-Test;$script:echo=$echoValue
        Assert-Intent ((Run-Search) -like 'WECOMDRIVE|UI_CHANGED|*') 'empty/lost last character must fail query readback'
        Assert-Intent ($overlayCalls -eq 0 -and (Count-Key 13) -eq 0) 'bad query must not advance or Enter'
    }
}
Test-Intent 'toolbar-only candidates use editor fallback even with stale B0 answer' {
    $r=Run-Send -Skip
    Assert-Intent ($r.error -eq '') ('fixture paste/title should permit one send: '+$r.error)
    Assert-Intent ($null -eq $sendCriteria) 'toolbar and sidebar tokens must not be offered to Jev'
    Assert-Intent ($r.data.input_point.source -ne 'jev' -and $r.data.input_point.x -eq 740 -and $r.data.input_point.y -eq 1100) 'stale unsafe answer must not click quick meeting or sidebar'
}
Test-Intent 'valid editor candidate still usable with exact screen offset' {
    $script:placeholder=$true;$script:inputChoice='B1'
    $r=Run-Send -Skip
    Assert-Intent ($r.error -eq '' -and $r.data.input_point.source -eq 'jev') 'valid candidate should remain usable'
    Assert-Intent ($r.data.input_point.x -eq 800 -and $r.data.input_point.y -eq 1100) 'window offset applies exactly once'
}
Test-Intent 'existing editor text is replaced rather than appended to message' {
    $script:draft='yes';$r=Run-Send -Skip
    Assert-Intent ($r.error -eq '') 'a draft answer must not veto requested replacement'
    $order=[WeComWin32]::Audit -join ','
    Assert-Intent ($order.IndexOf('post:256:46') -lt $order.IndexOf('clipboard')) 'Delete must precede clipboard write'
    Assert-Intent ((Count-Key 65) -eq 1 -and (Count-Key 46) -eq 1 -and $clipCalls -eq 1 -and (Count-Key 13) -eq 1) 'exactly one clear, paste and send'
}
Test-Intent 'multiline paste does not depend on OCR echo before database verification' {
    $script:readback=$false;$r=Run-Send -Skip
    Assert-Intent ($r.error -eq '') 'OCR echo must not gate a posted paste'
    Assert-Intent ($clipCalls -eq 1 -and (Count-Key 86) -eq 1 -and (Count-Key 13) -eq 1 -and (Count-Key 65) -eq 1) 'one clear, paste and Enter without repeat'
}
Test-Intent 'failed paste dispatch never presses Enter' {
    [WeComAttach32]::AttachOk=$false;$r=Run-Send -Skip
    Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'failed attach is a known pre-send rejection'
    Assert-Intent ($clipCalls -eq 0 -and (Count-Key 86) -eq 0 -and (Count-Key 46) -eq 0 -and (Count-Key 13) -eq 0) 'failed clear cannot post Delete, paste or Enter'
}
Test-Intent 'clipboard failure exhausts finite attempts without paste or Enter' {
    $script:clipFail=$true;$r=Run-Send
    Assert-Intent ($r.error -like 'WECOMDRIVE|CONFIG_MISSING|*') 'clipboard exhaustion must fail loudly'
    Assert-Intent ($clipCalls -eq 5 -and (Count-Key 86) -eq 0 -and (Count-Key 13) -eq 0) 'no partial input/send after clipboard failure'
}
Test-Intent 'conversation switch before the only title copy leaves both editors untouched' {
    $script:lateTitle='another-group';$r=Run-Send -Skip
    Assert-Intent ($r.error -eq '' -and $r.data.navigate_required) 'copied non-target title must request navigation before input'
    Assert-Intent ($script:titleCopyCalls -eq 1 -and @([WeComWin32]::Messages | Where-Object {$_.M -eq 0x201}).Count -eq 0) 'one title copy, zero editor clicks'
    Assert-Intent ((Count-Key 65) -eq 0 -and (Count-Key 46) -eq 0 -and $clipCalls -eq 0 -and (Count-Key 13) -eq 0) 'no clear, paste or Enter on new conversation'
}
Test-Intent 'rebuilt window with a matching copied title still stops before input' {
    $script:rebuildBeforeClick=$true;[WeComWin32]::OldWindowAlive=$false
    $r=Run-Send -Single -Skip
    Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'old title proof cannot authorize a new HWND'
    Assert-Intent ($script:resolveCalls -eq 1 -and $script:titleCopyCalls -eq 1) 'driver does not silently re-resolve and send through a rebuilt window'
    Assert-Intent ((Count-Key 65) -eq 0 -and (Count-Key 46) -eq 0 -and (Count-Key 13) -eq 0) 'no clear or send through a rebuilt HWND'
}
Test-Intent 'window rebuilt after click aborts before any clear' {
    [WeComWin32]::RebuildOnClick=$true
    $r=Run-Send -Skip
    Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'focus on destroyed window cannot be trusted'
    Assert-Intent ((Count-Key 65) -eq 0 -and (Count-Key 46) -eq 0 -and $clipCalls -eq 0 -and (Count-Key 13) -eq 0) 'must not clear or send after click destroys window'
}
Test-Intent 'window rebuilt after title copy aborts before any click or clear' {
    $script:rebuildAfterTitleCopy=$true
    $r=Run-Send -Skip
    Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'stale window after copied title cannot authorize clearing'
    Assert-Intent (@([WeComWin32]::Messages | Where-Object {$_.M -eq 0x201}).Count -eq 0) 'no editor click after title copy destroys the window'
    Assert-Intent ((Count-Key 65) -eq 0 -and (Count-Key 46) -eq 0 -and $clipCalls -eq 0 -and (Count-Key 13) -eq 0) 'no clear or send after window rebuild'
}
Test-Intent 'post-input title OCR plus cannot veto confirmed send' {
    $script:changedTitle=$true;$r=Run-Send -Single -Skip
    Assert-Intent ($r.error -eq '') 'confirmed pre-input title permits send despite noisy later OCR'
    Assert-Intent ($script:typedOcrCalls -eq 0) 'typed screenshot must not be OCR-checked before Enter'
    Assert-Intent ((Count-Key 65) -eq 1 -and (Count-Key 13) -eq 1) 'one pre-input clear and one Enter occur'
}
Test-Intent 'Jev yes cannot clear a different group editor' {
    $script:wrongVerifyTitle=$true
    $r=Run-Send -Skip
    Assert-Intent ($r.error -eq '' -and $r.data.navigate_required) 'wrong title is returned for navigation'
    Assert-Intent ((Count-Key 65) -eq 0 -and (Count-Key 46) -eq 0 -and $clipCalls -eq 0 -and (Count-Key 13) -eq 0) 'no destructive action before title confirmation'
}
Test-Intent 'post-send uncertainty never causes an automatic second send' {
    $r=Run-Send
    Assert-Intent ($r.error -like 'WECOMDRIVE|EXECUTION_UNKNOWN|*') 'insufficient post-send evidence must be unknown'
    Assert-Intent ((Count-Key 13) -eq 1 -and $clipCalls -eq 1) 'unknown cannot resend or repaste'
}
Test-Intent 'verify-only with another conversation stays read-only and unknown' {
    $script:wrongVerifyTitle=$true;$r=Run-Send -Verify
    Assert-Intent ($r.error -like 'WECOMDRIVE|EXECUTION_UNKNOWN|*') 'wrong current title cannot confirm intended target'
    Assert-Intent ([WeComWin32]::Messages.Count -eq 0 -and $clipCalls -eq 0) 'verification must not focus, clear, type, paste or Enter'
}
if ($failedCases.Count -gt 0) { throw ($failedCases -join "`n") }
Write-Output ('PS_INPUT_INDEPENDENT_OK cases='+$script:caseCount)
