import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { createWeixinGroupRangeSummaryOperation } from '../src/operations/groupRangeSummary.js'
import { CodedOperationError, type OpContext } from '../src/operations/types.js'

const args = {
  target_name: '合成群', start_at: '2026-11-20T16:00:00.000Z', end_at: '2026-11-23T02:00:00.000Z',
  tenant_id: 'tenant-test', grant: 'g'.repeat(43),
}
const context = (): OpContext => ({ signal: new AbortController().signal, progress: () => {} })

test('the range upload has exact UTC bounds and the operation receipt never includes raw content', async () => {
  const { tenant_id, grant, ...read } = args
  const result = await createWeixinGroupRangeSummaryOperation({
    serverUrl: 'https://paired.example.test',
    runDriverFn: async (request) => {
      assert.deepEqual(JSON.parse(request.stdin!), { mode: 'range', ...read })
      assert.equal(request.useVisionProxy, false)
      return { messages: ['private synthetic chat'], partial: true }
    },
    fetchFn: async (url, options) => {
      assert.equal(String(url), 'https://paired.example.test/api/local-tools/runtime/weixin-summary')
      assert.equal(options?.redirect, 'error')
      assert.equal(new Headers(options?.headers).get('Authorization'), `Bearer ${grant}`)
      assert.deepEqual(JSON.parse(String(options?.body)), {
        tenant_id, start_at: args.start_at, end_at: args.end_at, messages: ['private synthetic chat'],
      })
      return Response.json({ summary: '验收安排', forbidden: 'private synthetic chat' })
    },
  }).execute(args, context())
  assert.deepEqual(result.data, { summary: '验收安排', message_count: 1, partial: true })
  assert.doesNotMatch(JSON.stringify(result), /private synthetic chat|gggg/)
})

test('unverified group, partial empty result and reversed or unsupported dates never upload', async () => {
  let requests = 0
  let driverCalls = 0
  const operation = createWeixinGroupRangeSummaryOperation({
    serverUrl: 'https://paired.example.test',
    runDriverFn: async () => {
      driverCalls++
      return { messages: [], partial: true }
    },
    fetchFn: async () => { requests++; throw new Error('unexpected') },
  })
  for (const invalid of [
    { start_at: args.end_at },
    { start_at: '2026-11-15T01:00:00.000Z' },
    { start_at: '2026-11-31T01:00:00.000Z' },
  ]) assert.equal((await operation.execute({ ...args, ...invalid }, context())).success, false)
  assert.equal(driverCalls, 0)
  assert.equal((await operation.execute(args, context())).code, 'SUMMARY_COVERAGE_UNAVAILABLE')
  const ambiguous = await createWeixinGroupRangeSummaryOperation({
    runDriverFn: async () => { throw new CodedOperationError('TARGET_AMBIGUOUS', 'private synthetic chat') },
  }).execute(args, context())
  assert.equal(ambiguous.code, 'TARGET_AMBIGUOUS')
  assert.doesNotMatch(JSON.stringify(ambiguous), /private/)
  assert.equal(requests, 0)
})

test('native range excludes outside, clipped and minute-boundary text, while prior anchors prove empty coverage', { skip: process.platform !== 'win32' }, () => {
  const range = fileURLToPath(new URL('../../drivers/ps1/range-history.ps1', import.meta.url))
  const latest = fileURLToPath(new URL('../../drivers/ps1/latest-history.ps1', import.meta.url))
  const script = `
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
. '${latest.replaceAll("'", "''")}'
. '${range.replaceAll("'", "''")}'
function Throw-DriverError($Code,$Message) { throw $Code }
function Row($Kind,$Text,[bool]$Clipped=$false) { @{kind=$Kind;text=$Text;clipped=$Clipped} }
$zone=[TimeZoneInfo]::CreateCustomTimeZone('Beijing fixture',[TimeSpan]::FromHours(8),'Beijing fixture','Beijing fixture')
$now=[DateTime]::new(2026,11,23,14,30,0)
$start=[DateTimeOffset]'2026-11-23T09:00:00+08:00'
$end=[DateTimeOffset]'2026-11-23T10:30:00+08:00'
$rows=@((Row text 'prefix-not-proven'),
        (Row time '星期六 15:03'),(Row text 'before-window'),
        (Row time '09:00'),(Row text 'included-at-start'),(Row text 'included-bounded'),
        (Row time '10:00'),(Row text 'included-by-anchor'),(Row text 'clipped' $true),
        (Row time '10:29'),(Row text 'included-last-minute'),(Row text 'before-end-unproven'),
        (Row time '10:30'),(Row text 'outside-at-end'))
$old=@((Row text 'unattributed-old'),(Row time '星期六 15:03'),(Row text 'weekend'),(Row time '星期六 15:09'),(Row text 'weekend-two'))
ConvertTo-Json -Compress -Depth 6 @{
    mixed=Select-WeixinRangeText @{rows=$rows} $start $end $now $zone
    priorOnly=Select-WeixinRangeText @{rows=$old} $start $end $now $zone
    unknownTail=Select-WeixinRangeText @{rows=$old+@((Row text 'unknown-unbounded'))} $start $end $now $zone
    noAnchor=Select-WeixinRangeText @{rows=@((Row time '星期一 09:30'),(Row text 'same-weekday-unknown'));complete_history=$true} $start $end $now $zone
    midnight=Select-WeixinRangeText @{rows=@((Row time '周六 15:03'),(Row text 'previous-year'))} ([DateTimeOffset]'2026-12-31T00:00:00+08:00') ([DateTimeOffset]'2027-01-01T00:00:00+08:00') ([DateTime]'2027-01-01T13:00:00') $zone
}
`
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')],
    { encoding: 'utf8', timeout: 20_000, windowsHide: true })
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(JSON.parse(result.stdout), {
    mixed: { messages: ['included-at-start', 'included-bounded', 'included-by-anchor', 'included-last-minute'], partial: true },
    priorOnly: { messages: [], partial: false },
    noAnchor: { messages: [], partial: true },
    unknownTail: { messages: [], partial: true },
    midnight: { messages: [], partial: false },
  })
})



test('automatic range reads must search uniquely even when a same-name group is already open', { skip: process.platform !== 'win32' }, () => {
  const range = fileURLToPath(new URL('../../drivers/ps1/range-history.ps1', import.meta.url))
  const script = `
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
. '${range.replaceAll("'", "''")}'
function Throw-DriverError($Code, $Message) { throw $Code }
function Resolve-WeixinMainWindowHwnd { 1 }
function Get-WindowIdentityByHwnd($Handle) { @{ProcessId=42} }
function Test-WeixinMainIdentity($Identity,$Handle) { $true }
function Get-WeixinNativeTitleInfo($Session) { @{title=$script:currentTitle;is_group=$script:currentIsGroup} }
function Get-WeixinCurrentNativeSession($TargetName) {
    $script:openedChecks++
    @{Title=$TargetName;Identity='verified'}
}
function Get-WeixinNativeList($Session,$Id,$Class) { $null }
function Open-WeixinNativeSearchTarget($Name,$Type,[switch]$VerifyGroupCandidate) {
    $script:searches++
    if ($Name -cne 'same-name' -or $Type -cne 'group' -or -not $VerifyGroupCandidate) {
        throw 'search-without-target-proof'
    }
    if ($script:uniqueSearchResult) { return @{Title=$Name;Identity='verified'} }
    throw 'TARGET_AMBIGUOUS'
}
function Get-WeixinLatestProcessStart($Session) { return 'start' }
function Invoke-WeixinLatestCapture($Session,$Request) {
    if (-not $Session.RequireGroup -or $Session.Identity -ne 'verified') { throw 'unverified-read' }
    $script:captures++
    @{bottom_observed_at=(Get-Date)}
}
function Select-WeixinRangeText($Capture,$Start,$End,$ObservedAt) { 'captured-verified-group' }
$request = @{target_name='same-name';start_at='2026-11-22T00:00:00.000Z';end_at='2026-11-23T00:00:00.000Z'}
$script:openedChecks = 0
$script:searches = 0
$script:captures = 0
$script:currentTitle = 'same-name'
$script:currentIsGroup = $true
$script:uniqueSearchResult = $false
function Run($data) {
    try { Invoke-WeixinRangeHistory $data } catch { $_.Exception.Message }
}
$automatic = Run $request
$notBoolean = Run ($request + @{allow_open_group='true'})
$retry = Run ($request + @{allow_open_group=$true})
$script:currentTitle = 'another-name'
$wrongGroupRetry = Run ($request + @{allow_open_group=$true})
$script:currentTitle = 'same-name'
$script:uniqueSearchResult = $true
$uniqueGroup = Run $request
$script:currentIsGroup = $false
$uniqueFriend = Run $request
ConvertTo-Json -Compress @{
    automatic=$automatic;notBoolean=$notBoolean;retry=$retry;wrongGroupRetry=$wrongGroupRetry
    uniqueGroup=$uniqueGroup;uniqueFriend=$uniqueFriend
    searches=$script:searches;openedChecks=$script:openedChecks;captures=$script:captures
}
`
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')],
    { encoding: 'utf8', timeout: 20_000, windowsHide: true })
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(JSON.parse(result.stdout), {
    automatic: 'TARGET_AMBIGUOUS', notBoolean: 'TARGET_AMBIGUOUS', retry: 'captured-verified-group',
    wrongGroupRetry: 'TARGET_AMBIGUOUS', uniqueGroup: 'captured-verified-group', uniqueFriend: 'TARGET_NOT_FOUND',
    searches: 5, openedChecks: 1, captures: 2,
  })
})
