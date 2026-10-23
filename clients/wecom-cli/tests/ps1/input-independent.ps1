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
    public static bool IsWindow(IntPtr h) { return true; }
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
        Audit.Add("post:" + m + ":" + w.ToInt64()); return true;
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
function Get-WeComWindowInfo { param($Hwnd) return @{ X=100;Y=200;W=1280;H=1000 } }
function Resolve-WeComMainWindow { return 101 }
function Write-DriverLog { param($Msg) }
function Save-StepShot { param($Hwnd,$Name) return @{path=$Name;left=100;top=200;w=1280;h=1000} }
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
function Invoke-WeComJev { param($StateText,$Questions)
    if ($Questions.ContainsKey('click_which')) {
        $script:focusCriteria=$Questions.click_which.criteria
        return @{used=$script:useFocusJev;answers=@{click_which=@{choice=$script:focusChoice}}}
    }
    if ($Questions.ContainsKey('right_conversation')) {
        $script:sendCriteria=$Questions
        return @{used=$true;answers=@{right_conversation=@{choice=$script:right};
            has_draft=@{choice=$script:draft};input_point=@{choice=$script:inputChoice}}}
    }
    return @{used=$false;reason='fixture offline'}
}
function Invoke-SendChatOcr { param($ImagePath,$Mode)
    if ($Mode -eq 'input') { return @{input_empty=$true;texts=@()} }
    if ($Mode -eq 'preview') { return @{texts=@();texts_norm=@()} }
    $title='fixture-group'
    if (($script:changedTitle -and $ImagePath -eq 'step2-typed.png') -or $script:wrongVerifyTitle) { $title='another-group' }
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
    $script:clipCalls=0;$script:clipFail=$false;$script:overlayCalls=0
    $script:readback=$true;$script:placeholder=$false;$script:changedTitle=$false;$script:wrongVerifyTitle=$false
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
Test-Intent 'successful CtrlF still clicks OCR anchor rather than assuming focus' {
    Assert-Intent ((Focus-WeComSearchBox -MainHwnd 101) -eq 'placeholder_click') 'shortcut dispatch alone cannot prove focus'
    Assert-Intent (@([WeComWin32]::Messages | Where-Object {$_.M -eq 0x201}).Count -eq 1) 'explicit search-anchor click required after CtrlF'
    Assert-Intent ((Count-Key 65) -eq 0 -and (Count-Key 46) -eq 0) 'focus operation cannot destructively clear input'
}
Test-Intent 'empty search cleanup does not focus or clear before draft inspection' {
    $r=Run-Send -Single -Skip
    Assert-Intent ($r.error -eq '') ('guarded single send should work: '+$r.error)
    Assert-Intent ((Count-Key 70) -eq 0 -and (Count-Key 65) -eq 0 -and (Count-Key 46) -eq 0) 'empty search requires no shortcut or destructive cleanup'
    Assert-Intent (@([WeComWin32]::Messages | Where-Object {$_.M -eq 0x201}).Count -eq 1) 'only chat editor should be clicked'
    Assert-Intent ((Count-Key 13) -eq 1) 'send boundary must be reached only once'
}
Test-Intent 'missing search anchor cannot clear existing chat draft' {
    $script:anchor=@{TokensRaw=@();PlaceholderPos=$null;ContentPos=$null}
    $script:clearState='unknown'
    $r=Run-Send
    Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'missing anchor must fail closed'
    Assert-Intent ((Count-Key 65) -eq 0 -and (Count-Key 46) -eq 0 -and $clipCalls -eq 0 -and (Count-Key 13) -eq 0) 'do not clear, paste or Enter when focus was not anchored'
}
Test-Intent 'unrelated crop OCR token plus Jev cannot authorize clearing chat draft' {
    $script:anchor=@{TokensRaw=@(@{text='99';x0=300;y0=30;x1=330;y1=50});PlaceholderPos=$null;ContentPos=$null}
    $script:useFocusJev=$true;$script:focusChoice='T0';$script:draft='yes'
    $focusError='';try { Focus-WeComSearchBox -MainHwnd 101 | Out-Null } catch {$focusError=$_.Exception.Message}
    Assert-Intent ($focusError -like 'WECOMDRIVE|UI_CHANGED|*') 'unrelated token cannot authorize even a search-focus click'
    $script:clearState='unknown'
    $r=Run-Send
    Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'unrelated token cannot establish safe search focus'
    Assert-Intent ((Count-Key 65) -eq 0 -and (Count-Key 46) -eq 0 -and $clipCalls -eq 0 -and (Count-Key 13) -eq 0) 'Jev choice and posted click cannot authorize deleting an existing draft'
}
Test-Intent 'nonempty search cannot authorize blind cleanup even with apparent focus anchor' {
    $script:clearState='has_content';$script:draft='yes'
    $r=Run-Send
    Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'unverified search clear control must block nonempty cleanup'
    Assert-Intent ([WeComWin32]::Messages.Count -eq 0 -and $clipCalls -eq 0) 'residual search must not lead to any keyboard/mouse cleanup or chat mutation'
}
Test-Intent 'stale placeholder coordinate cannot authorize a different token click' {
    $script:anchor.PlaceholderPos=@(330,40);$script:useFocusJev=$true;$script:focusChoice='T0'
    $failure='';try { Focus-WeComSearchBox -MainHwnd 101 | Out-Null } catch {$failure=$_.Exception.Message}
    Assert-Intent ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'placeholder position must correspond to actual trusted token'
    Assert-Intent (@([WeComWin32]::Messages | Where-Object {$_.M -eq 0x201}).Count -eq 0) 'stale source evidence cannot be repaired by guessing click target'
}
Test-Intent 'Jev cannot smuggle excluded plus button back as focus candidate' {
    $script:useFocusJev=$true;$script:focusChoice='T1'
    $method=Focus-WeComSearchBox -MainHwnd 101
    Assert-Intent (-not $focusCriteria.ContainsKey('T1')) 'plus button must not be offered'
    $click=@([WeComWin32]::Messages | Where-Object {$_.M -eq 0x201})[0]
    Assert-Intent ($method -eq 'placeholder_click' -and ($click.L -band 65535) -eq 220) 'unoffered answer must resolve to real search anchor'
}
Test-Intent 'attach failure retains safe explicit search anchor fallback' {
    [WeComAttach32]::AttachOk=$false
    Assert-Intent ((Focus-WeComSearchBox -MainHwnd 101) -eq 'placeholder_click') 'failed shortcut must not skip safe fallback'
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
    Assert-Intent (-not $sendCriteria.ContainsKey('input_point')) 'toolbar and sidebar tokens must not be offered'
    Assert-Intent ($r.data.input_point.source -ne 'jev' -and $r.data.input_point.x -eq 740 -and $r.data.input_point.y -eq 1100) 'stale unsafe answer must not click quick meeting or sidebar'
}
Test-Intent 'valid editor candidate still usable with exact screen offset' {
    $script:placeholder=$true;$script:inputChoice='B1'
    $r=Run-Send -Skip
    Assert-Intent ($r.error -eq '' -and $r.data.input_point.source -eq 'jev') 'valid candidate should remain usable'
    Assert-Intent ($r.data.input_point.x -eq 800 -and $r.data.input_point.y -eq 1100) 'window offset applies exactly once'
}
Test-Intent 'existing user draft is never typed over or cleared' {
    $script:draft='yes';$r=Run-Send
    Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'draft must block sending'
    Assert-Intent ($clipCalls -eq 0 -and (Count-Key 13) -eq 0 -and (Count-Key 65) -eq 0) 'existing draft must survive without any clear shortcut'
}
Test-Intent 'missing multiline echo cannot Enter or clear possibly existing input' {
    $script:readback=$false;$r=Run-Send
    Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'failed paste readback must abort'
    Assert-Intent ($clipCalls -eq 1 -and (Count-Key 86) -eq 1 -and (Count-Key 13) -eq 0 -and (Count-Key 65) -eq 0) 'one paste, no send or destructive recovery after uncertain paste'
}
Test-Intent 'clipboard failure exhausts finite attempts without paste or Enter' {
    $script:clipFail=$true;$r=Run-Send
    Assert-Intent ($r.error -like 'WECOMDRIVE|CONFIG_MISSING|*') 'clipboard exhaustion must fail loudly'
    Assert-Intent ($clipCalls -eq 5 -and (Count-Key 86) -eq 0 -and (Count-Key 13) -eq 0) 'no partial input/send after clipboard failure'
}
Test-Intent 'title changed after our input clears only our draft and never sends' {
    $script:changedTitle=$true;$r=Run-Send -Single
    Assert-Intent ($r.error -like 'WECOMDRIVE|UI_CHANGED|*') 'strict final title must reject other conversation'
    Assert-Intent ((Count-Key 65) -eq 1 -and (Count-Key 13) -eq 0) 'only our newly typed draft may be cleared, no Enter'
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
