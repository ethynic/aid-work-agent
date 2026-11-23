import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const driver = fileURLToPath(new URL('../../drivers/ps1/latest-history.ps1', import.meta.url))
const history = fileURLToPath(new URL('../../drivers/ps1/history-read.ps1', import.meta.url))
const loadHelpers = `
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile('${driver.replaceAll("'", "''")}',[ref]$null,[ref]$null)
foreach ($definition in $ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
    . ([ScriptBlock]::Create($definition.Extent.Text))
}
function Throw-DriverError($Code,$Message) { throw $Code }
function Row($id,$text=('message-'+$id),$kind='text',[bool]$clipped=$false) {
    return @{runtime_id=$id;text=$text;kind=$kind;sender='peer';sender_name=$null;clipped=$clipped}
}
`
function run(script: string) {
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')],
    { encoding: 'utf8', timeout: 20_000, windowsHide: true })
  assert.equal(result.status, 0, result.stderr)
  return JSON.parse(result.stdout)
}

test('group-only history refuses a private chat before activation or message collection', { skip: process.platform !== 'win32' }, () => {
  const script = `
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile('${history.replaceAll("'", "''")}',[ref]$null,[ref]$null)
$driver=$ast.Find({param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq 'Invoke-DriverMain'},$false)
function Invoke-DriverMain($MutexName,[scriptblock]$Body) {
    $result=try { & $Body } catch { @{code=$_.Exception.Message} }
    ConvertTo-Json -InputObject $result -Compress
}
function Throw-DriverError($Code,$Message) { throw $Code }
function Get-WeixinCurrentNativeSession($TargetName,$ExpectedSessionIdentity) { return @{Handle=1;Title=$TargetName} }
function Get-WeixinNativeTitleInfo($Session) { return @{title=$Session.Title;is_group=$false} }
function Invoke-WeixinActivation($Hwnd) { throw 'ACTIVATION_REACHED' }
function Get-WeixinNativeRows($Session) { throw 'READ_REACHED' }
. ([scriptblock]::Create($driver.Extent.Text))
`
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')],
    { input: JSON.stringify({ target_name: 'fixture', group_only: true, max_pages: 1 }), encoding: 'utf8', timeout: 20_000, windowsHide: true })
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(JSON.parse(result.stdout), { code: 'TARGET_NOT_FOUND' })
})

test('actual native paginator stops at an overlap, distinguishes identical bodies, and fails on discontinuity', { skip: process.platform !== 'win32' }, () => {
  const cases = run(loadHelpers + `
function Get-WeixinLatestProcessStart($Session) { if ($script:scenario -eq 'processChanged' -and $script:bottom) { return '456' }; return '123' }
function Assert-WeixinNativeSelectedSession($Session) { if ($script:drift) { throw 'UI_CHANGED' } }
function Invoke-WeixinActivation($Hwnd) { return $true }
function Move-WeixinLatestBottom($Session) { Assert-WeixinNativeSelectedSession $Session; $script:bottom++; return @{rows=$script:frames[0];viewport=@{top=0;bottom=680}} }
function Send-WeixinNativeMessageWheel($Session,$Viewport,$Delta,$Count) { $script:up++; $script:frame++ }
function Get-WeixinNativeMessageSnapshot($Session) {
    if ($script:scenario -eq 'identityChanged') { $script:drift=$true }
    return @{rows=$script:frames[[Math]::Min($script:frame,$script:frames.Count-1)];viewport=@{top=0;bottom=680}}
}
function Case($scenario,$frames,$anchor,[bool]$HasCache=$true,[bool]$PreviousEmpty=$false,[bool]$CompleteHistory=$true) {
    $script:scenario=$scenario;$script:frames=$frames;$script:frame=0;$script:up=0;$script:bottom=0;$script:drift=$false
    $result=try {
        $capture=Invoke-WeixinLatestCapture @{Handle=1;ProcessId=2;Title='fixture';Identity='1:2:3'} @{
            process_started_at='123';anchor=$anchor;has_cache=$HasCache;previous_empty=$PreviousEmpty
            complete_history=$CompleteHistory;cached_count=$anchor.Count
        }
        @{code='OK';pages=$capture.pages_scanned;new=@($capture.rows | Select-Object -Skip ($capture.anchor_end+1) | Where-Object {$_.kind -ne 'time'} | ForEach-Object {$_.runtime_id})}
    } catch { @{code=$_.Exception.Message} }
    $result.up=$script:up;$result.bottom=$script:bottom
    return $result
}
$anchor=@((Row 'a'),(Row 'b'))
$longFrames=@(for ($n=10;$n -ge 0;$n--) { ,@((Row ([string]$n)),(Row ([string]($n+1)))) })
$emptyBacklogFrames=@(for ($n=13;$n -ge 0;$n--) { ,@((Row ([string]$n)),(Row ([string]($n+1)))) })
ConvertTo-Json -Depth 8 -Compress @{
    eleventhPage=Case 'eleventhPage' $longFrames @((Row '0')) -CompleteHistory $true
    initialTenPages=Case 'initialTenPages' $longFrames @() -HasCache $false
    firstAfterEmpty=Case 'firstAfterEmpty' @(,@((Row 'a'),(Row 'b'))) @() -PreviousEmpty $true
    emptyBacklog=Case 'emptyBacklog' $emptyBacklogFrames @() -PreviousEmpty $true -CompleteHistory $true
    crossPage=Case 'crossPage' @(@((Row 'c' 'message-c' 'text' $true),(Row 'd')),@((Row 'a'),(Row 'b'),(Row 'c'))) $anchor
    unchanged=Case 'unchanged' @(,@((Row 'a'),(Row 'b'))) $anchor
    sameBodies=Case 'sameBodies' @(,@((Row 'b' 'same'),(Row 'c' 'same'))) @((Row 'b' 'same'))
    timeChanged=Case 'timeChanged' @(,@((Row 'time' '昨天 10:00' 'time'),(Row 'b'))) @((Row 'b')) -CompleteHistory $true
    clippedOldPrefix=Case 'clippedOldPrefix' @(,@((Row 'a' 'message-a' 'text' $true),(Row 'b'),(Row 'c'))) @((Row 'b'),(Row 'c'))
    discontinuity=Case 'discontinuity' @(@((Row 'c'),(Row 'd')),@((Row 'a'),(Row 'b'))) $anchor
    missingAnchor=Case 'missingAnchor' @(,@((Row 'c'),(Row 'd'))) $anchor
    clippedLatest=Case 'clippedLatest' @(,@((Row 'b'),(Row 'c' 'same' 'text' $true))) @((Row 'b'))
    identityChanged=Case 'identityChanged' @(,@((Row 'a'),(Row 'b'))) $anchor
    processChanged=Case 'processChanged' @(,@((Row 'a'),(Row 'b'))) $anchor
    duplicateNativeIds=Case 'duplicateNativeIds' @(,@((Row 'b'),(Row 'b'))) @((Row 'b'))
    reusedAcrossPages=Case 'reusedAcrossPages' @(@((Row 'v1' 'message-C'),(Row 'v2' 'message-D')),@((Row 'v2' 'message-A'),(Row 'v3' 'message-B'),(Row 'v1' 'message-C'))) @((Row 'v2' 'message-A'),(Row 'v3' 'message-B'))
    allIdsRebuilt=Case 'allIdsRebuilt' @(,@((Row 'fresh-a' 'message-a'),(Row 'fresh-b' 'message-b'),(Row 'fresh-c' 'message-c'))) $anchor
    repeatedContentSequence=Case 'repeatedContentSequence' @(,@((Row 'new-a' 'message-a'),(Row 'new-b' 'message-b'),(Row 'another-a' 'message-a'),(Row 'another-b' 'message-b'))) $anchor
    shortChinese=Case 'shortChinese' @(,@((Row 'new-a' '收到'),(Row 'new-b' '好的'),(Row 'new-c' '稍后'))) @((Row 'old-a' '收到'),(Row 'old-b' '好的'))
    completePrefix=Case 'completePrefix' @(,@((Row 'new-a' 'message-a'),(Row 'new-b' 'message-b'),(Row 'new-c' 'message-c'))) $anchor -CompleteHistory $true
    movedPrefix=Case 'movedPrefix' @(,@((Row 'old-z' 'another message'),(Row 'new-a' 'message-a'),(Row 'new-b' 'message-b'),(Row 'new-c' 'message-c'))) $anchor -CompleteHistory $true
    repeatedAnchor=try {
        $newer=Merge-WeixinLatestRows @((Row 'y' 'B'),(Row 'x' 'A')) @((Row 'x' 'A'),(Row 'y' 'B'))
        $all=Merge-WeixinLatestRows @((Row 'x' 'A'),(Row 'y' 'B')) $newer
        Find-WeixinLatestAnchor $all @((Row 'x' 'A'),(Row 'y' 'B'))
    } catch { $_.Exception.Message }
}
`)
  assert.deepEqual(cases.crossPage, { code: 'OK', pages: 2, new: ['c', 'd'], up: 2, bottom: 2 })
  assert.deepEqual(cases.eleventhPage, { code: 'OK', pages: 11, new: Array.from({ length: 11 }, (_, i) => String(i + 1)), up: 11, bottom: 2 })
  assert.equal(cases.initialTenPages.pages, 10)
  assert.equal(cases.initialTenPages.up, 9)
  assert.deepEqual(cases.firstAfterEmpty, { code: 'OK', pages: 1, new: ['a', 'b'], up: 1, bottom: 2 })
  assert.deepEqual(cases.emptyBacklog, { code: 'OK', pages: 14, new: Array.from({ length: 15 }, (_, i) => String(i)), up: 14, bottom: 2 })
  assert.deepEqual(cases.unchanged, { code: 'OK', pages: 1, new: [], up: 1, bottom: 2 })
  assert.equal(cases.sameBodies.code, 'CONTENT_UNAVAILABLE')
  assert.deepEqual(cases.timeChanged, { code: 'OK', pages: 1, new: [], up: 1, bottom: 2 })
  assert.equal(cases.clippedOldPrefix.code, 'CONTENT_UNAVAILABLE')
  for (const name of ['discontinuity', 'missingAnchor', 'clippedLatest', 'duplicateNativeIds']) assert.equal(cases[name].code, 'CONTENT_UNAVAILABLE', name)
  assert.equal(cases.identityChanged.code, 'UI_CHANGED')
  assert.equal(cases.processChanged.code, 'UI_CHANGED')
  assert.deepEqual(cases.reusedAcrossPages, { code: 'OK', pages: 2, new: ['v1', 'v2'], up: 2, bottom: 2 })
  assert.deepEqual(cases.allIdsRebuilt, { code: 'OK', pages: 1, new: ['fresh-c'], up: 1, bottom: 2 })
  assert.equal(cases.repeatedContentSequence.code, 'CONTENT_UNAVAILABLE')
  assert.deepEqual(cases.shortChinese, { code: 'OK', pages: 1, new: ['new-c'], up: 1, bottom: 2 })
  assert.deepEqual(cases.completePrefix, { code: 'OK', pages: 1, new: ['new-c'], up: 1, bottom: 2 })
  assert.equal(cases.movedPrefix.code, 'CONTENT_UNAVAILABLE')
  assert.equal(cases.repeatedAnchor, 'CONTENT_UNAVAILABLE')
})

test('native latest reaches the proven bottom even when starting beyond the old six-batch limit', { skip: process.platform !== 'win32' }, () => {
  const result = run(loadHelpers + `
$script:position=0;$script:wheels=0
function Assert-WeixinNativeSelectedSession($Session) {}
function Get-WeixinNativeRows($Session) { return @{rows=@((Row ([string]$script:position)));viewport=@{top=0;bottom=680}} }
function Send-WeixinNativeMessageWheel($Session,$Viewport,$Delta,$Count) {
    if ($Delta -ne -120) { throw 'unexpected direction' }
    $script:wheels++;$script:position=[Math]::Min(9,$script:position+1)
}
$view=Move-WeixinLatestBottom @{Identity='fixture'}
ConvertTo-Json -Compress @{wheels=$script:wheels;last=$view.rows[-1].runtime_id}
`)
  assert.deepEqual(result, { wheels: 10, last: '9' })
})

test('persistent anchors need a verified minute and position before a repeated template can stop pagination', { skip: process.platform !== 'win32' }, () => {
  const cases = run(loadHelpers + `
function Get-Date { return [DateTime]::new(2026,11,18,12,0,0) }
function Get-WeixinLatestProcessStart($Session) { return '123' }
function Assert-WeixinNativeSelectedSession($Session) {}
function Invoke-WeixinActivation($Hwnd) { return $true }
function Move-WeixinLatestBottom($Session) { return @{rows=$script:frames[0]} }
function Send-WeixinNativeMessageWheel($Session,$Viewport,$Delta,$Count) { $script:up++;$script:index++ }
function Get-WeixinNativeMessageSnapshot($Session) {
    return @{rows=$script:frames[[Math]::Min($script:index,$script:frames.Count-1)];viewport=@{top=0;bottom=680}}
}
function Template($prefix) { return @(1..6 | ForEach-Object { Row ($prefix+$_) ('fixed template '+$_) }) }
$old=Template 'old';$new=Template 'new'
$anchor=Template 'cached'
function Case($frames,$minute='2026-11-18T10:00') {
    $script:frames=$frames;$script:index=0;$script:up=0
    try {
        $result=Invoke-WeixinLatestCapture @{Handle=1;ProcessId=2;Title='fixture';Identity='1:2:3'} @{
            process_started_at='123';has_cache=$true;previous_empty=$false;complete_history=$false;cached_count=12
            anchor=$anchor;anchor_context=@{minute=$minute;offset=6}
        }
        return @{code='OK';pages=$result.pages_scanned;up=$script:up;context=$result.anchor_context;new=@($result.rows | Select-Object -Skip ($result.anchor_end+1) | Where-Object {$_.kind -ne 'time'}).Count}
    } catch { return @{code=$_.Exception.Message;up=$script:up} }
}
$oldTime=Row 'time-old' '10:00' 'time'
$newTime=Row 'time-new' '11:00' 'time'
$differentTime=@( ,(@($newTime)+$new); ,(@($oldTime)+$old+@($newTime,$new[0])) )
$sameTime=@( ,$new; ,(@($oldTime)+$old+@($new[0])) )
$duplicateMinute=@( ,(@($oldTime)+$old+@((Row 'time-new' '10:00' 'time'))+$new) )
$yesterday=Row 'time-yesterday' '昨天 10:00' 'time'
ConvertTo-Json -Depth 8 -Compress @{
    unchanged=Case @(,(@($oldTime)+$new))
    differentTime=Case $differentTime
    sameTimeBlock=Case $sameTime
    duplicateMinute=Case $duplicateMinute
    yesterday=Case @(,(@($yesterday)+$new)) '2026-11-17T10:00'
    missingTime=Case @(,$new)
    unknownWeek=Case @(,(@((Row 'time-week' '星期三 10:00' 'time'))+$new))
    futureTime=Case @(,(@((Row 'time-future' '23:00' 'time'))+$new))
    trailingMarker=Case @(,(@($oldTime)+$new+@($newTime)))
    twoDaysAgo=(ConvertTo-WeixinLatestMinute '前天 08:05' (Get-Date)).ToString('yyyy-MM-ddTHH:mm')
    calendarDate=(ConvertTo-WeixinLatestMinute '2026年11月17日 10:00' (Get-Date)).ToString('yyyy-MM-ddTHH:mm')
}
`)
  const context = (minute: string, offset = 6) => ({ minute, offset })
  assert.deepEqual(cases.unchanged, { code: 'OK', pages: 1, up: 0, new: 0, context: context('2026-11-18T10:00') })
  assert.deepEqual(cases.differentTime, { code: 'OK', pages: 2, up: 1, new: 6, context: context('2026-11-18T11:00') })
  assert.deepEqual(cases.sameTimeBlock, { code: 'OK', pages: 2, up: 1, new: 6, context: context('2026-11-18T10:00', 12) })
  assert.deepEqual(cases.yesterday, { code: 'OK', pages: 1, up: 0, new: 0, context: context('2026-11-17T10:00') })
  assert.deepEqual(cases.trailingMarker, { code: 'OK', pages: 1, up: 0, new: 0, context: context('2026-11-18T10:00') })
  assert.equal(cases.twoDaysAgo, '2026-11-16T08:05')
  assert.equal(cases.calendarDate, '2026-11-17T10:00')
  for (const name of ['duplicateMinute', 'missingTime', 'unknownWeek', 'futureTime']) {
    assert.equal(cases[name].code, 'CONTENT_UNAVAILABLE', name)
  }
})

test('the history entry keeps its legacy mode and binds latest and identity-only modes to the current session', { skip: process.platform !== 'win32' }, () => {
  const result = run(`
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile('${history.replaceAll("'", "''")}',[ref]$null,[ref]$null)
$entry=[ScriptBlock]::Create($ast.EndBlock.Statements[-1].Extent.Text)
function Invoke-DriverMain($MutexName,$Body) { & $Body }
function Get-WeixinCurrentNativeSession($TargetName,$ExpectedSessionIdentity) { return @{Title=$TargetName;Identity='rid';Handle=1;ProcessId=2} }
function Get-WeixinLatestProcessStart($Session) { return '123' }
function Invoke-WeixinLatestCapture($Session,$Request) { if ($Request.session_identity -cne 'rid') { throw 'unbound latest' }; return @{latest=$true} }
function Get-WeixinNativeTitleInfo($Session) { return @{title=$Session.Title;is_group=$false} }
function Invoke-WeixinActivation($Hwnd) { return $true }
function Get-WeixinNativeRows($Session) { return @{viewport=@{top=0;bottom=680}} }
function Send-WeixinNativeMessageWheel($Session,$Viewport,$Delta,$Count) {}
function Get-WeixinNativeMessageSnapshot($Session) { return @{rows=@();viewport=@{top=0;bottom=680}} }
$output=@{}
foreach ($mode in @('identity','latest','legacy')) {
    $request=@{target_name='fixture';mode=$mode;session_identity='rid';max_pages=1}
    [Console]::SetIn([IO.StringReader]::new(($request | ConvertTo-Json -Compress)))
    $output[$mode]=& $entry
}
ConvertTo-Json -Depth 8 -Compress $output
`)
  assert.deepEqual(result.identity, { title: 'fixture', session_identity: 'rid', process_started_at: '123' })
  assert.deepEqual(result.latest, { latest: true })
  assert.deepEqual(result.legacy, { title: 'fixture', is_group: false, pages: [[]] })
})
