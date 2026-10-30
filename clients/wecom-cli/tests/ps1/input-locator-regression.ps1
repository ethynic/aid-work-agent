param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $DriverDir '_common.ps1')

# Load the actual production functions and main body through the PS parser.
# Only OS/OCR/network boundaries are replaced; never touch real UI/clipboard.
$parseErrors = $null; $parseTokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $DriverDir 'message-send.ps1'), [ref]$parseTokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
foreach ($fn in $ast.FindAll({ param($a) $a -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    . ([scriptblock]::Create($fn.Extent.Text))
}
$command = $ast.FindAll({ param($a) $a -is [System.Management.Automation.Language.CommandAst] -and
    $a.GetCommandName() -eq 'Invoke-DriverMain' }, $true)[0]
$body = ($command.CommandElements | Where-Object {
    $_ -is [System.Management.Automation.Language.ScriptBlockExpressionAst]
} | Select-Object -First 1).ScriptBlock.GetScriptBlock()
Add-Type 'public class WeComWin32 { public static bool IsWindow(System.IntPtr h) { return true; } }'

function Assert-True($Value, $Why) { if (-not $Value) { throw $Why } }
function Start-Sleep { param($Milliseconds) }
function Get-WeComWindowInfo { param($Hwnd) return @{ Hwnd=101;Pid=42;Class='WeWorkWindow';Visible=$true;X=1277;Y=0;W=1280;H=1392 } }
function Resolve-WeComMainWindow { return 101 }
function Get-WeComSearchBoxState { param($MainHwnd,$Tag) return $script:searchState }
function Send-WeComClick { param($Hwnd,$ScreenX,$ScreenY)
    [void]$script:actions.Add(@{kind='click';x=$ScreenX;y=$ScreenY});return 101 }
function Send-WeComAttachChordKey { param($Hwnd,$Vk)
    [void]$script:actions.Add(@{kind='chord';vk=$Vk});return $script:chordOk }
function Write-DriverLog { param($Msg) }
function Save-StepShot { param($Hwnd,$Name) return @{path=$Name;left=1277;top=0;w=1280;h=1392} }
function Set-Clipboard { param($Value) [void]$script:actions.Add(@{kind='clipboard';text=$Value}) }
function Send-WeComText { param($Hwnd,$Text) [void]$script:actions.Add(@{kind='text';text=$Text}) }
function Clear-WeComFocusedInput { param($Hwnd) [void]$script:actions.Add(@{kind='clear-own-draft'}) }
function Send-WeComEnter { param($Hwnd) [void]$script:actions.Add(@{kind='enter'});throw 'TEST_ENTER_BOUNDARY' }
function Invoke-WeComJev { param($StateText,$Questions)
    if ($Questions.ContainsKey('click_which')) {
        Assert-True (-not $Questions.click_which.criteria.ContainsKey('T2')) 'search + button must not be selectable'
        return @{used=$true;answers=@{click_which=@{choice=$script:searchChoice}}}
    }
    $script:sendQuestions=$Questions
    return @{used=$true;answers=@{right_conversation=@{choice=$script:right};has_draft=@{choice=$script:draft};
        input_point=@{choice=$script:inputChoice}}}
}
function Invoke-SendChatOcr { param($ImagePath,$Mode)
    if ($Mode -eq 'input') { return @{input_empty=$true;texts=@()} }
    $title = $script:title
    if ($ImagePath -eq 'step2-typed.png' -and $script:changedTitle) { $title='other-group' }
    $boxes=@(@{text=$title;x0=426;y0=28;x1=580;y1=48},
        @{text='快速会议';x0=1023;y0=1153;x1=1093;y1=1173},
        @{text='发送(S)';x0=1049;y0=1345;x1=1096;y1=1365})
    if ($script:withPlaceholder) { $boxes+=@{text='输入消息';x0=640;y0=1210;x1=720;y1=1230} }
    if ($ImagePath -eq 'step2-typed.png' -and $script:readback) {
        $boxes+=@{text='fixture-message';x0=500;y0=1235;x1=720;y1=1255}
    }
    return @{boxes=$boxes}
}
function Reset-Scenario {
    $script:actions=New-Object System.Collections.ArrayList
    $script:chordOk=$true;$script:searchChoice='T1';$script:right='yes';$script:draft='no'
    $script:inputChoice='B0';$script:withPlaceholder=$false;$script:readback=$false;$script:changedTitle=$false
    $script:title='fixture-group'
    $script:searchState=@{State='empty';TokensRaw=@(@{text='Q';x0=186;y0=28;x1=213;y1=44},
        @{text='搜索';x0=208;y0=28;x1=241;y1=44},@{text='+';x0=368;y0=28;x1=385;y1=44});
        PlaceholderPos=@(224,36);ContentPos=$null}
}
function Run-Send($Multiline=$true,$External=$false) {
    $TargetName='fixture-group';$Subtitle='';$Section='group';$ExpectedPrefix=''
    if ($External) { $TargetName='fixture-peer@微信';$Subtitle='微信联系人';$Section='contact' }
    $Text='fixture-message';if ($Multiline) { $Text+="`nsecond line" }
    $SkipVerify=$false;$VerifyOnly=$false
    try { & $body } catch { return $_.Exception.Message }
}

Reset-Scenario
$method=Focus-WeComSearchBox -MainHwnd 101
Assert-True ($method -eq 'jev_click') 'Ctrl+F dispatch alone must not complete focus'
Assert-True (@($actions | Where-Object {$_.kind -eq 'click'}).Count -eq 1) 'must explicitly click the search anchor'
Reset-Scenario;$script:searchChoice='T2'
Assert-True ((Focus-WeComSearchBox -MainHwnd 101) -eq 'placeholder_click') 'invalid Jev + choice must fall back to actual search anchor'
Reset-Scenario;$script:chordOk=$false
Assert-True ((Focus-WeComSearchBox -MainHwnd 101) -eq 'jev_click') 'failed shortcut keeps OCR/Jev fallback'
Reset-Scenario
$script:searchState=@{State='has_content';TokensRaw=@(@{text='fixture';x0=208;y0=28;x1=268;y1=44});
    PlaceholderPos=$null;ContentPos=@(238,36)}
$script:searchChoice='T0'
Assert-True ((Focus-WeComSearchBox -MainHwnd 101) -eq 'jev_click') 'verified content anchor remains focusable without destructive clearing'
Reset-Scenario;$script:searchState=@{TokensRaw=@();PlaceholderPos=$null;ContentPos=$null}
$failure='';try { Focus-WeComSearchBox -MainHwnd 101 } catch {$failure=$_.Exception.Message}
Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'no search anchor must fail closed'
Assert-True ($actions.Count -eq 1) 'no anchor must not click or clear input'

# Execute the actual Clear helper, rather than replacing its dangerous boundary.
foreach ($searchStatus in @('empty','has_content','unknown')) {
    Reset-Scenario;$script:searchState.State=$searchStatus
    $failure='';$result=$false
    try { $result=Clear-WeComSearchBoxV2 -MainHwnd 101 } catch {$failure=$_.Exception.Message}
    if ($searchStatus -eq 'empty') { Assert-True $result 'empty search is a no-op success' }
    else { Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'unproved search clear must fail closed' }
    Assert-True ($actions.Count -eq 0) 'clear must never send chords, mouse or Delete into an unproved focus'
}

Reset-Scenario
$script:searchState=@{State='empty';TokensRaw=@(@{text='unread-badge';x0=120;x1=150;y0=0;y1=20});
    PlaceholderPos=$null;ContentPos=$null}
$script:searchChoice='T0'
$failure='';try { Focus-WeComSearchBox -MainHwnd 101 } catch {$failure=$_.Exception.Message}
Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'arbitrary crop token cannot become a search anchor'
Assert-True (@($actions | Where-Object {$_.kind -eq 'click'}).Count -eq 0) 'untrusted crop token must never be clicked'

Reset-Scenario
$script:searchState=@{State='empty';TokensRaw=@(@{text='unread-badge';x0=120;x1=150;y0=0;y1=20});
    PlaceholderPos=$null;ContentPos=$null}
$script:searchChoice='T0';$script:draft='yes'
$failure=Run-Send
Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'draft blocks sending even with misleading OCR elsewhere'
Assert-True ($actions.Count -eq 0) 'empty search must neither move focus nor clear before user draft check'

Reset-Scenario;$script:searchState.State='has_content'
$failure=Run-Send
Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'nonempty search must block until a safe clear control is calibrated'
Assert-True ($actions.Count -eq 0) 'nonempty search refusal must not touch any focused draft'

foreach ($scale in @(1,1.5)) {
    $toolbar=@{text='快速会议';x0=1023*$scale;x1=1093*$scale;y0=1153*$scale;y1=1173*$scale}
    Assert-True (-not (Test-WeComTextInputToken $toolbar (1280*$scale) (1392*$scale))) 'toolbar is not an editor candidate at any DPI'
    $placeholder=@{text='输入消息';x0=640*$scale;x1=720*$scale;y0=1210*$scale;y1=1230*$scale}
    Assert-True (Test-WeComTextInputToken $placeholder (1280*$scale) (1392*$scale)) 'actual editor placeholder remains eligible'
}
Assert-True (-not (Test-WeComTextInputToken @{text='立即总结';x0=1130;x1=1210;y0=1230;y1=1250} 1280 1392)) 'sidebar is not an editor candidate'
Assert-True (-not (Test-WeComTextInputToken @{text='发送(S)';x0=640;x1=710;y0=1310;y1=1330} 1280 1392)) 'send button is not an editor candidate'

Reset-Scenario
$failure=Run-Send
Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'missing pasted text must retain readback failure'
Assert-True (-not $sendQuestions.ContainsKey('input_point')) 'toolbar-only OCR must not become a Jev input choice'
$chatClick=@($actions | Where-Object {$_.kind -eq 'click'})[-1]
Assert-True ($chatClick.x -eq 1917 -and $chatClick.y -eq 1253) 'unsafe B0 answer must use existing safe ratio, with screen offset'
Assert-True (@($actions | Where-Object {$_.kind -eq 'enter'}).Count -eq 0) 'readback failure must never Enter'
Assert-True (@($actions | Where-Object {$_.kind -eq 'chord' -and $_.vk -in @(0x41,0x46)}).Count -eq 0) 'empty search must skip Ctrl+F and Ctrl+A'

Reset-Scenario;$script:draft='yes'
$failure=Run-Send
Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'user draft must still block send'
Assert-True (@($actions | Where-Object {$_.kind -in @('clipboard','text','enter','clear-own-draft')}).Count -eq 0) 'user draft must remain untouched'

Reset-Scenario;$script:withPlaceholder=$true;$script:inputChoice='B1';$script:readback=$true
$failure=Run-Send
Assert-True ($failure -eq 'TEST_ENTER_BOUNDARY') 'valid editor plus matched readback/title may reach send boundary'
$chatClick=@($actions | Where-Object {$_.kind -eq 'click'})[-1]
Assert-True ($chatClick.x -eq 1957 -and $chatClick.y -eq 1220) 'valid Jev editor token remains usable'
Assert-True (@($actions | Where-Object {$_.kind -eq 'enter'}).Count -eq 1) 'valid send must not duplicate Enter'

Reset-Scenario;$script:changedTitle=$true
$failure=Run-Send $false
Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'changed conversation must still block send'
Assert-True (@($actions | Where-Object {$_.kind -eq 'enter'}).Count -eq 0) 'title mismatch must never Enter'
Assert-True (@($actions | Where-Object {$_.kind -eq 'clear-own-draft'}).Count -eq 1) 'only our newly typed draft is cleared on title mismatch'
# Execute the production body: a Jev yes must not override an internal/other external title.
foreach ($wrongTitle in @('fixture-peer','fixture-peer@其他企业','fixture-peer（2）','fixture-peer-more@微信')) {
    Reset-Scenario;$script:title=$wrongTitle
    $failure=Run-Send $false $true
    Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'explicit WeChat scope must reject a non-WeChat title before input'
    Assert-True (@($actions | Where-Object {$_.kind -in @('clipboard','text','enter','click')}).Count -eq 0) 'Jev yes cannot cause input or send to the wrong identity'
}
Reset-Scenario;$script:title='fixture-peer @微信'
$failure=Run-Send $false $true
Assert-True ($failure -eq 'TEST_ENTER_BOUNDARY') 'exact WeChat contact can reach the one-send boundary'
Assert-True (@($actions | Where-Object {$_.kind -eq 'enter'}).Count -eq 1) 'valid external send reaches Enter exactly once'
Reset-Scenario;$script:title='fixture-peer @微信';$script:changedTitle=$true
$failure=Run-Send $false $true
Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'changing title after input still rejects Enter'
Assert-True (@($actions | Where-Object {$_.kind -eq 'enter'}).Count -eq 0) 'external scope is rechecked immediately before Enter'
Assert-True (@($actions | Where-Object {$_.kind -eq 'clear-own-draft'}).Count -eq 1) 'only this attempt own draft is cleared'
Write-Output 'PS_INPUT_LOCATOR_OK external_cases=6'
