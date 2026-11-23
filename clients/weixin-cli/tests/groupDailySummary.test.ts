import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { createWeixinGroupDailySummaryOperation } from '../src/operations/groupDailySummary.js'
import { CodedOperationError, type OpContext } from '../src/operations/types.js'

const args = { target_name: '测试项目群', business_date: '2026-11-23', tenant_id: 'tenant-test', grant: 'g'.repeat(43) }
const context = (): OpContext => ({ signal: new AbortController().signal, progress: () => {} })

test('today’s verified text goes to the paired service, and only the summary enters the receipt', async () => {
  const operation = createWeixinGroupDailySummaryOperation({
    serverUrl: 'https://paired.example.test',
    runDriverFn: async (request) => {
      assert.equal(request.useVisionProxy, false)
      assert.deepEqual(JSON.parse(request.stdin!), {
        mode: 'daily', group_only: true, target_name: args.target_name, business_date: args.business_date,
      })
      return { messages: ['synthetic message body'], partial: true, snapshot: 'synthetic local image' }
    },
    fetchFn: async (url, options) => {
      assert.equal(String(url), 'https://paired.example.test/api/local-tools/runtime/weixin-summary')
      assert.equal(options?.redirect, 'error')
      assert.equal(new Headers(options?.headers).get('Authorization'), `Bearer ${args.grant}`)
      assert.deepEqual(JSON.parse(String(options?.body)), {
        tenant_id: args.tenant_id, business_date: args.business_date, messages: ['synthetic message body'],
      })
      return Response.json({ summary: '讨论了测试安排。', unexpected_body: 'synthetic message body' })
    },
  })
  const result = await operation.execute(args, context())
  assert.equal(result.success, true)
  assert.deepEqual(result.data, { summary: '讨论了测试安排。', message_count: 1, partial: true })
  assert.doesNotMatch(JSON.stringify(result), /synthetic|gggg/)
})

test('verified empty, uncertain coverage, untrusted URL and native refusal never upload a body', async () => {
  let requests = 0
  const fetchFn = async () => { requests++; throw new Error('unexpected request') }
  for (const partial of [false, true]) {
    const result = await createWeixinGroupDailySummaryOperation({
      serverUrl: 'https://paired.example.test', fetchFn,
      runDriverFn: async () => ({ messages: [], partial }),
    }).execute(args, context())
    assert.equal(result.code, partial ? 'SUMMARY_COVERAGE_UNAVAILABLE' : 'OK')
    assert.deepEqual(result.data, partial ? {} : { summary: null, message_count: 0, partial: false })
  }
  const refused = await createWeixinGroupDailySummaryOperation({
    serverUrl: 'https://paired.example.test', fetchFn,
    runDriverFn: async () => { throw new CodedOperationError('TARGET_NOT_FOUND', 'synthetic private diagnostic') },
  }).execute(args, context())
  assert.equal(refused.code, 'TARGET_NOT_FOUND')
  assert.doesNotMatch(JSON.stringify(refused), /synthetic/)
  const untrusted = await createWeixinGroupDailySummaryOperation({
    serverUrl: 'http://paired.example.test', fetchFn,
    runDriverFn: async () => ({ messages: ['synthetic'], partial: false }),
  }).execute(args, context())
  assert.equal(untrusted.code, 'SUMMARY_FAILED')
  assert.equal(requests, 0)
})

test('the paired service can report exhausted credits without putting its response in the receipt', async () => {
  for (const [body, code] of [
    [{ code: 'NO_CREDIT', details: 'private provider details' }, 'NO_CREDIT'],
    [{ code: 'unexpected', details: 'private provider details' }, 'SUMMARY_FAILED'],
  ] as const) {
    const result = await createWeixinGroupDailySummaryOperation({
      serverUrl: 'https://paired.example.test',
      runDriverFn: async () => ({ messages: ['synthetic'], partial: false }),
      fetchFn: async () => Response.json(body, { status: 403 }),
    }).execute(args, context())
    assert.equal(result.code, code)
    assert.deepEqual(result.data, {})
    assert.doesNotMatch(JSON.stringify(result), /private provider details/)
  }
})

test('native daily projection excludes earlier days, unknown dates, clipped text and media', { skip: process.platform !== 'win32' }, () => {
  const history = fileURLToPath(new URL('../../drivers/ps1/history-read.ps1', import.meta.url))
  const latest = fileURLToPath(new URL('../../drivers/ps1/latest-history.ps1', import.meta.url))
  const script = `
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
. '${latest.replaceAll("'", "''")}'
$ast=[Management.Automation.Language.Parser]::ParseFile('${history.replaceAll("'", "''")}',[ref]$null,[ref]$null)
$projection=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Select-WeixinDailyText'},$false)
. ([scriptblock]::Create($projection.Extent.Text))
function Throw-DriverError($Code,$Message) { throw $Code }
function Row($Kind,$Text,[bool]$Clipped=$false) { @{kind=$Kind;text=$Text;clipped=$Clipped} }
$now=[DateTimeOffset]::new(2026,11,23,12,30,0,[TimeSpan]::FromHours(8)).LocalDateTime
$previous=$now.AddDays(-1).ToString('yyyy年M月d日 HH:mm')
$earlier=$now.AddMinutes(-20).ToString('yyyy年M月d日 HH:mm')
$later=$now.AddMinutes(-10).ToString('yyyy年M月d日 HH:mm')
$sameWeekday='星期' + '日一二三四五六'.Substring([int]$now.DayOfWeek, 1) + ' 10:30'
$rows=@((Row text 'unknown-before'),(Row time $previous),(Row text 'previous-day'),
        (Row time $earlier),(Row text 'today-one'),(Row text 'clipped' $true),(Row media 'image'),
        (Row time $sameWeekday),(Row text 'unknown-after'),(Row time $later),(Row text 'today-two'))
$monday=[DateTime]::new(2026,11,23,14,30,0,[DateTimeKind]::Local)
$mondayBusinessDate=([DateTimeOffset]::new($monday)).ToOffset([TimeSpan]::FromHours(8)).ToString('yyyy-MM-dd')
$saturday=@((Row time '星期六 15:03'),(Row text 'older-one'),(Row time '周六 15:09'),(Row text 'older-two'))
ConvertTo-Json -Compress -Depth 6 @{
    mixed=Select-WeixinDailyText @{rows=$rows;complete_history=$false;empty_proven=$false} '2026-11-23' $now
    complete=Select-WeixinDailyText @{rows=@((Row time $previous),(Row text 'previous-day'));complete_history=$false;empty_proven=$false} '2026-11-23' $now
    insufficient=Select-WeixinDailyText @{rows=@((Row text 'unknown-day'));complete_history=$false;empty_proven=$false} '2026-11-23' $now
    weekend=Select-WeixinDailyText @{rows=@((Row text 'unknown-before'),(Row text 'clipped-before' $true))+$saturday;complete_history=$false} $mondayBusinessDate $monday
    unknownNewer=Select-WeixinDailyText @{rows=$saturday+@((Row time '周一 10:30'),(Row text 'ambiguous-monday'));complete_history=$false} $mondayBusinessDate $monday
    sameWeekdayOnly=Select-WeixinDailyText @{rows=@((Row time '星期一 10:30'),(Row text 'ambiguous-monday'));complete_history=$true} $mondayBusinessDate $monday
    sunday=Select-WeixinDailyText @{rows=@((Row time '星期天 09:30'),(Row text 'sunday-one'),(Row time '周日 10:00'),(Row text 'sunday-two'));complete_history=$false} $mondayBusinessDate $monday
    conflictingOrder=try {
        Select-WeixinDailyText @{rows=$saturday+@((Row time '星期五 18:00'),(Row text 'out-of-order'));complete_history=$false} $mondayBusinessDate $monday
    } catch { $_.Exception.Message }
}
`
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')],
    { encoding: 'utf8', timeout: 20_000, windowsHide: true })
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(JSON.parse(result.stdout), {
    mixed: { messages: ['today-one', 'today-two'], partial: true },
    complete: { messages: [], partial: false },
    insufficient: { messages: [], partial: true },
    weekend: { messages: [], partial: false },
    unknownNewer: { messages: [], partial: true },
    sameWeekdayOnly: { messages: [], partial: true },
    sunday: { messages: [], partial: false },
    conflictingOrder: 'CONTENT_UNAVAILABLE',
  })
})
