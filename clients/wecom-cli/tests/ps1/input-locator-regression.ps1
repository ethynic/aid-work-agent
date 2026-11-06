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
Add-Type 'public class WeComWin32 { public static bool Live = true; public static bool IsWindow(System.IntPtr h) { return Live; } }'

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
function Get-WeComTitlePoint { param($Hwnd,$ImagePath)
    if ($script:titlePointError) { throw 'TEST_TITLE_POINT_ERROR' }
    if ($script:cachedPoint) { return @{x=435;y=38;cache_path='synthetic-title-cache.json';source='cache'} }
    $script:modelCalls++
    return @{x=435;y=38;cache_path='synthetic-title-cache.json';source='glm'} }
function Get-WeComCopiedTitle { param($Hwnd,$Section,$Point)
    $script:titleCopies++
    [void]$script:actions.Add(@{kind='title-copy';number=$script:titleCopies})
    if ($script:windowDiesAfterTitleCopy) { [WeComWin32]::Live=$false }
    if ($script:titleCopyError) { throw 'TEST_TITLE_COPY_ERROR' }
    return $script:title }
function Save-WeComTitlePoint { param($Point)
    if ($Point.source -eq 'glm') { $script:cachedPoint=$true } }
function Set-Clipboard { param($Value) [void]$script:actions.Add(@{kind='clipboard';text=$Value}) }
function Send-WeComText { param($Hwnd,$Text) [void]$script:actions.Add(@{kind='text';text=$Text}) }
function Clear-WeComFocusedInput { param($Hwnd) [void]$script:actions.Add(@{kind='clear-input'}); return $script:clearOk }
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
    if ($ImagePath -eq 'step2-typed.png' -and $script:changedTitle) { $title+='+' }
    $boxes=@(@{text=$title;x0=426;y0=28;x1=580;y1=48},
        @{text='快速会议';x0=1023;y0=1153;x1=1093;y1=1173},
        @{text='发送(S)';x0=1049;y0=1345;x1=1096;y1=1365})
    if ($script:withPlaceholder) { $boxes+=@{text='输入消息';x0=640;y0=1210;x1=720;y1=1230} }
    if ($ImagePath -eq 'step1-precheck.png' -and $script:preDraft) {
        # Synthetic overlap with the old OCR guard; not a measured customer screenshot coordinate.
        $boxes+=@{text=$script:preDraft;x0=450;y0=1235;x1=700;y1=1255}
    }
    if ($ImagePath -eq 'step2-typed.png' -and $script:readback) {
        $boxes+=@{text=$script:readbackText;x0=450;y0=1235;x1=720;y1=1255}
    }
    return @{boxes=$boxes}
}
function Reset-Scenario {
    [WeComWin32]::Live=$true
    $script:actions=New-Object System.Collections.ArrayList
    $script:chordOk=$true;$script:searchChoice='T1';$script:right='yes';$script:draft='no'
    $script:inputChoice='B0';$script:withPlaceholder=$false;$script:readback=$false;$script:changedTitle=$false
    $script:sendQuestions=$null
    $script:titlePointError=$false;$script:titleCopyError=$false
    $script:windowDiesAfterTitleCopy=$false
    $script:titleCopies=0
    $script:cachedPoint=$false;$script:modelCalls=0
    $script:preDraft='';$script:readbackText='fixture-message';$script:clearOk=$true
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
Assert-True ($method -eq 'shortcut') 'Ctrl+F dispatch establishes search focus'
Assert-True (@($actions | Where-Object {$_.kind -eq 'click'}).Count -eq 0) 'search focus must not use OCR anchor clicks'
Reset-Scenario;$script:chordOk=$false
 $failure='';try{Focus-WeComSearchBox -MainHwnd 101}catch{$failure=$_.Exception.Message}
Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'failed shortcut must abort search focus'
Reset-Scenario
$script:searchState=@{State='has_content';TokensRaw=@(@{text='fixture';x0=208;y0=28;x1=268;y1=44});
    PlaceholderPos=$null;ContentPos=@(238,36)}
Assert-True ((Focus-WeComSearchBox -MainHwnd 101) -eq 'shortcut') 'search focus is independent of OCR content state'
Reset-Scenario;$script:searchState=@{TokensRaw=@();PlaceholderPos=$null;ContentPos=$null}
Assert-True ((Focus-WeComSearchBox -MainHwnd 101) -eq 'shortcut') 'missing OCR anchor must not block Ctrl+F focus'
Assert-True ($actions.Count -eq 1) 'focus must only dispatch Ctrl+F'

# Execute the actual Clear helper, rather than replacing its dangerous boundary.
foreach ($searchStatus in @('empty','has_content','unknown')) {
    Reset-Scenario;$script:searchState.State=$searchStatus
    $failure='';$result=$false
    try { $result=Clear-WeComSearchBoxV2 -MainHwnd 101 } catch {$failure=$_.Exception.Message}
    Assert-True $result 'all OCR search states receive the same fixed clear'
    Assert-True ($actions[0].kind-eq'chord'-and$actions[0].vk-eq0x46) 'clear must first dispatch Ctrl+F'
}

Reset-Scenario
$script:searchState=@{State='empty';TokensRaw=@(@{text='unread-badge';x0=120;x1=150;y0=0;y1=20});
    PlaceholderPos=$null;ContentPos=$null}
$script:searchChoice='T0'
Assert-True ((Focus-WeComSearchBox -MainHwnd 101) -eq 'shortcut') 'arbitrary crop token cannot block Ctrl+F'
Assert-True (@($actions | Where-Object {$_.kind -eq 'click'}).Count -eq 0) 'untrusted crop token must never be clicked'

Reset-Scenario
$script:searchState=@{State='empty';TokensRaw=@(@{text='unread-badge';x0=120;x1=150;y0=0;y1=20});
    PlaceholderPos=$null;ContentPos=$null}
$script:searchChoice='T0';$script:draft='yes'
$failure=Run-Send
Assert-True ($failure -eq 'TEST_ENTER_BOUNDARY') 'unrelated search OCR must not block an already open target conversation'
Assert-True (@($actions | Where-Object {$_.kind -eq 'clear-input'}).Count -eq 1) 'target editor is cleared before one send'

Reset-Scenario;$script:searchState.State='has_content'
$failure=Run-Send
Assert-True ($failure -eq 'TEST_ENTER_BOUNDARY') 'nonempty search must not block an already open target conversation'
Assert-True (@($actions | Where-Object {$_.kind -eq 'clear-input'}).Count -eq 1) 'only the confirmed target editor is cleared'

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
Assert-True ($failure -eq 'TEST_ENTER_BOUNDARY') 'OCR echo must not gate a posted paste'
Assert-True ((@($actions | ForEach-Object {$_.kind}) -join ',') -like 'title-copy,click,clear-input,*') 'confirmed title is copied once, then the editor is focused and cleared'
Assert-True ($script:titleCopies -eq 1 -and $script:modelCalls -eq 1) 'send path must not repeat title copy or model lookup before clearing'
Assert-True ($null -eq $sendQuestions) 'toolbar-only OCR must not call Jev for a missing editor candidate'
$chatClick=@($actions | Where-Object {$_.kind -eq 'click'})[-1]
Assert-True ($chatClick.x -eq 1917 -and $chatClick.y -eq 1253) 'unsafe B0 answer must use existing safe ratio, with screen offset'
Assert-True (@($actions | Where-Object {$_.kind -eq 'enter'}).Count -eq 1) 'one posted paste reaches the send boundary'
Assert-True (@($actions | Where-Object {$_.kind -eq 'chord' -and $_.vk -in @(0x41,0x46)}).Count -eq 0) 'send must not touch the search field'
Assert-True (($actions | Where-Object {$_.kind -in @('clear-input','clipboard','chord','enter')} | ForEach-Object {$_.kind}) -join ',' -eq 'clear-input,clipboard,chord,enter') 'clear precedes clipboard paste and Enter'

Reset-Scenario;$script:clearOk=$false
$failure=Run-Send
Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'failed editor clear aborts before paste'
Assert-True (@($actions | Where-Object {$_.kind -in @('clipboard','enter')}).Count -eq 0) 'failed clear cannot paste or Enter'

foreach ($failureKind in @('titlePointError','titleCopyError')) {
    Reset-Scenario
    Set-Variable -Scope Script -Name $failureKind -Value $true
    $failure=Run-Send
    Assert-True ($failure -like 'TEST_TITLE_*_ERROR') 'title coordinate/copy failure must surface before any send action'
    Assert-True (@($actions | Where-Object {$_.kind -in @('clear-input','clipboard','text','enter')}).Count -eq 0) 'unconfirmed title must never clear or send'
}

Reset-Scenario;$script:windowDiesAfterTitleCopy=$true
$failure=Run-Send
Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'a rebuilt window invalidates the only copied title before input focus'
Assert-True ((@($actions | ForEach-Object {$_.kind}) -join ',') -eq 'title-copy') 'window rebuild after title copy must not click, clear, type or send'

Reset-Scenario;$script:chordOk=$false
$failure=Run-Send
Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*') 'failed Ctrl+V dispatch aborts before Enter'
Assert-True (@($actions | Where-Object {$_.kind -eq 'enter'}).Count -eq 0) 'failed dispatch never sends'

Reset-Scenario;$script:preDraft='older draft';$script:readback=$true
$failure=Run-Send
Assert-True ($failure -eq 'TEST_ENTER_BOUNDARY') 'OCR text in editor does not veto deliberate replacement'
Assert-True (@($actions | Where-Object {$_.kind -eq 'clear-input'}).Count -eq 1) 'old editor content is cleared before new paste'

Reset-Scenario;$script:preDraft='[草稿]'
$failure=Run-Send
Assert-True ($failure -eq 'TEST_ENTER_BOUNDARY') 'a draft marker from another chat row cannot veto a confirmed target send'
Assert-True (@($actions | Where-Object {$_.kind -eq 'clear-input'}).Count -eq 1) 'only target editor receives the intentional clear'

Reset-Scenario;$script:readback=$true;$script:readbackText='请发送消息，这是长文本末尾仍可见的一行'
$failure=Run-Send
Assert-True ($failure -eq 'TEST_ENTER_BOUNDARY') 'long message may scroll past its prefix after paste'
Assert-True (@($actions | Where-Object {$_.kind -eq 'enter'}).Count -eq 1) 'visible input text permits exactly one Enter'

Reset-Scenario;$script:draft='yes'
$failure=Run-Send
Assert-True ($failure -eq 'TEST_ENTER_BOUNDARY') 'Jev draft answer does not veto deliberate replacement'
Assert-True (@($actions | Where-Object {$_.kind -eq 'clear-input'}).Count -eq 1) 'existing input is cleared once before send'

Reset-Scenario;$script:withPlaceholder=$true;$script:inputChoice='B1';$script:readback=$true
$failure=Run-Send
Assert-True ($failure -eq 'TEST_ENTER_BOUNDARY') 'valid editor plus matched readback/title may reach send boundary'
$chatClick=@($actions | Where-Object {$_.kind -eq 'click'})[-1]
Assert-True ($chatClick.x -eq 1957 -and $chatClick.y -eq 1220) 'valid Jev editor token remains usable'
Assert-True (@($actions | Where-Object {$_.kind -eq 'enter'}).Count -eq 1) 'valid send must not duplicate Enter'

Reset-Scenario;$script:changedTitle=$true
$failure=Run-Send $false
Assert-True ($failure -eq 'TEST_ENTER_BOUNDARY') 'post-input OCR plus must not veto confirmed conversation'
Assert-True (@($actions | Where-Object {$_.kind -eq 'enter'}).Count -eq 1) 'one confirmed send reaches Enter despite OCR drift'
Assert-True (@($actions | Where-Object {$_.kind -eq 'clear-input'}).Count -eq 1) 'input is cleared only before typing'
Assert-True (@($actions | Where-Object {$_.kind -eq 'text'}).Count -eq 1) 'single-line send keeps original text primitive'
Assert-True (@($actions | Where-Object {$_.kind -eq 'clipboard'}).Count -eq 0) 'single-line send does not touch clipboard'

Reset-Scenario;$script:title='other-group'
$failure=Run-Send
Assert-True ($failure.navigate_required -eq $true -and $script:cachedPoint -and $script:modelCalls -eq 1) 'a copied non-target title must retain its calibrated point while requesting navigation'
Assert-True (@($actions | Where-Object {$_.kind -in @('clear-input','clipboard','enter')}).Count -eq 0) 'Jev yes cannot authorize clearing a different group'
$script:title='fixture-group'
$failure=Run-Send
Assert-True ($failure -eq 'TEST_ENTER_BOUNDARY' -and $script:modelCalls -eq 1) 'navigating to the target must reuse the one calibrated point without another model call'
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
Assert-True ($failure -eq 'TEST_ENTER_BOUNDARY') 'external send also uses confirmed pre-input title'
Assert-True (@($actions | Where-Object {$_.kind -eq 'enter'}).Count -eq 1) 'external send has one Enter despite post-input OCR drift'
Assert-True (@($actions | Where-Object {$_.kind -eq 'clear-input'}).Count -eq 1) 'external editor is cleared only before typing'
Write-Output 'PS_INPUT_LOCATOR_OK external_cases=6'
