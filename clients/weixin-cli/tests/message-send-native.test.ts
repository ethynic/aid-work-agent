import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'

const driver = fileURLToPath(new URL('../../drivers/ps1/message-send.ps1', import.meta.url))
const search = fileURLToPath(new URL('../../drivers/ps1/native-session-search.ps1', import.meta.url))

test('native search checks complete results without confusing history and known group matches', { skip: process.platform !== 'win32' }, () => {
  const script = `
$ErrorActionPreference = 'Stop'
[void](Add-Type -AssemblyName UIAutomationTypes)
$ast = [Management.Automation.Language.Parser]::ParseFile(${JSON.stringify(search).replaceAll('\\\\', '\\')}, [ref]$null, [ref]$null)
$helper = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Select-WeixinNativeSearchCandidate' }, $true)
. ([ScriptBlock]::Create($helper.Extent.Text))
function Throw-DriverError($Code, $Message) { throw $Code }
function Item($label, $class = 'mmui::XTableCell', $id = '') {
    return @{Current=@{ClassName=$class; AutomationId=$id; ProcessId=42;
        ControlType=[Windows.Automation.ControlType]::ListItem; Name=$label; IsOffscreen=$false}}
}
function Pick($nodes, [string]$TargetType = 'friend') {
    try {
        $node = Select-WeixinNativeSearchCandidate $nodes @{ProcessId=42;Title='contact'} $TargetType
        return $node.Current.AutomationId
    } catch { return $_.Exception.Message }
}
$person = Item 'contact' 'mmui::SearchContentCellView' 'search_item_contact'
$recent = Item '最常使用'
$contacts = Item '联系人'
$groups = Item '群聊'
$more = Item '查看全部(32)'
$history = Item '聊天记录'
$web = Item '搜索网络结果'
ConvertTo-Json @{
    unambiguous=Pick @($recent,$person,$groups,$more,$history,(Item 'contact' 'mmui::SearchMessageCellView' 'history-match'),$web,(Item 'contact'))
    duplicate=Pick @($recent,$person,$contacts,$person,$web)
    moreContacts=Pick @($recent,$person,$contacts,$more,$web)
    noCompletionBoundary=Pick @($recent,$person)
    sendKnownGroup=Pick @($recent,$person,$web) -TargetType 'group'
} -Compress
`
  const result = spawnSync('powershell.exe', ['-NoProfile', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')], { encoding: 'utf8' })
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(JSON.parse(result.stdout.trim()), {
    unambiguous: 'search_item_contact',
    duplicate: 'TARGET_AMBIGUOUS',
    moreContacts: 'TARGET_AMBIGUOUS',
    noCompletionBoundary: 'UI_CHANGED',
    sendKnownGroup: 'search_item_contact',
  })
})

test('a local send acknowledgement requires exact new UIA text anchored at the bottom', { skip: process.platform !== 'win32' }, () => {
  const script = `
$ErrorActionPreference = 'Stop'
$ast = [Management.Automation.Language.Parser]::ParseFile(${JSON.stringify(driver).replaceAll('\\\\', '\\')}, [ref]$null, [ref]$null)
$helper = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-WeixinSendNewMessage' }, $true)
. ([ScriptBlock]::Create($helper.Extent.Text))
function Row($id, $text, $kind = 'mmui::ChatTextItemView') {
    return @{ runtime_id=$id; text=$text; class=$kind; clipped=$false }
}
function Bottom($rows, $empty = $false) {
    return @{rows=@($rows);at_bottom=$true;empty_proven=$empty}
}
$anchor = Row 'a' 'previous'
$baseline = Bottom @($anchor)
$sent = Row 'b' 'exact'
$time = Row 'c' '12:34' 'mmui::ChatItemView'
$cases = @{
    sent = Test-WeixinSendNewMessage $baseline (Bottom @($anchor,$time,$sent)) 'exact'
    firstMessage = Test-WeixinSendNewMessage (Bottom @() $true) (Bottom @($time,$sent)) 'exact'
    emptyViewportNotProof = Test-WeixinSendNewMessage (Bottom @()) (Bottom @($sent)) 'exact'
    scrolledOldDuplicate = Test-WeixinSendNewMessage @{ rows=@($anchor);at_bottom=$false } (Bottom @($anchor,$sent)) 'exact'
    sameTextAlreadyPresent = Test-WeixinSendNewMessage (Bottom @($anchor,$sent)) (Bottom @($anchor,$sent)) 'exact'
    viewportChanged = Test-WeixinSendNewMessage $baseline (Bottom @($sent)) 'exact'
    moreThanOneNewMessage = Test-WeixinSendNewMessage $baseline (Bottom @($anchor,(Row 'd' 'another'),$sent)) 'exact'
    normalizationCannotApprove = Test-WeixinSendNewMessage $baseline (Bottom @($anchor,(Row 'b' 'Exact'))) 'exact'
}
ConvertTo-Json $cases -Compress
`
  const result = spawnSync('powershell.exe', ['-NoProfile', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')], { encoding: 'utf8' })
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(JSON.parse(result.stdout.trim()), {
    sent: true,
    firstMessage: true,
    emptyViewportNotProof: false,
    scrolledOldDuplicate: false,
    sameTextAlreadyPresent: false,
    viewportChanged: false,
    moreThanOneNewMessage: false,
    normalizationCannotApprove: false,
  })
})

test('a draft or composer change prevents Enter and a missing native acknowledgement is never retried', { skip: process.platform !== 'win32' }, () => {
  const script = `
$ErrorActionPreference = 'Stop'
$ast = [Management.Automation.Language.Parser]::ParseFile(${JSON.stringify(driver).replaceAll('\\\\', '\\')}, [ref]$null, [ref]$null)
foreach ($definition in $ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
    . ([ScriptBlock]::Create($definition.Extent.Text))
}
$entry = [ScriptBlock]::Create($ast.EndBlock.Statements[-1].Extent.Text)
function Throw-DriverError($Code, $Message) { throw $Code }
function Invoke-DriverMain($MutexName, $Body) { & $Body }
function Open-WeixinNativeSearchTarget($TargetName, $TargetType) { return @{Handle=1; Title=$TargetName} }
function Find-WeixinSendComposerPoint($Session, $Composer, $NativeView) { return @{ScreenX=5;ScreenY=5} }
function Send-WeixinPostMessageClick($Hwnd, $ScreenX, $ScreenY) {}
function Start-Sleep($Milliseconds) {}
function Get-WeixinNativeRows($Session) { return @{viewport=@{bottom=20}} }
function Get-WeixinSendComposer($Session) {
    $identity = if ($script:mode -eq 'composerChanged' -and $script:typed) { 'second' } else { 'first' }
    $text = if ($script:mode -eq 'userDraft') { 'existing draft' } else { $script:typed }
    return @{Id=$identity;Text=$text;Focused=$true;Bounds='stable'}
}
function Send-WeixinPostMessageText($Hwnd, $Text) { $script:typed=$Text }
function Send-WeixinPostMessageKey($Hwnd, $Vk, $ScanCode) {
    if ($Vk -ne 0x0D) { throw 'unexpected key' }
    $script:enter++
    if ($script:mode -ne 'draftUnchanged') { $script:typed='' }
}
function Get-WeixinSendBottom($Session) {
    $rows=@(@{runtime_id='old';class='mmui::ChatTextItemView';text='anchor';clipped=$false})
    if ($script:enter -and $script:mode -eq 'sent') {
        $rows+=@{runtime_id='new';class='mmui::ChatTextItemView';text='fixture text';clipped=$false}
    }
    return @{rows=$rows;at_bottom=$true}
}
$result=@{}
foreach ($case in @('sent','userDraft','composerChanged','draftUnchanged','noNewRow')) {
    $script:mode=$case
    $script:typed=''
    $script:enter=0
    [Console]::SetIn([IO.StringReader]::new('{"target_name":"fixture","target_type":"friend","text":"fixture text"}'))
    $code='OK'
    try {
        $receipt = & $entry
        if (-not $receipt.verified -or $receipt.title -ne 'fixture') { $code='BAD_RECEIPT' }
    } catch { $code=$_.Exception.Message }
    $result[$case]=@{code=$code;enter=$script:enter}
}
ConvertTo-Json $result -Compress
`
  const result = spawnSync('powershell.exe', ['-NoProfile', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')], { encoding: 'utf8' })
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(JSON.parse(result.stdout.trim()), {
    sent: { code: 'OK', enter: 1 },
    userDraft: { code: 'UI_CHANGED', enter: 0 },
    composerChanged: { code: 'UI_CHANGED', enter: 0 },
    draftUnchanged: { code: 'EXECUTION_UNKNOWN', enter: 1 },
    noNewRow: { code: 'EXECUTION_UNKNOWN', enter: 1 },
  })
})
