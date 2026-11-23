import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { ConversationAligner } from '../src/platform/aligner.js'
import { parseNativeObservation } from '../src/platform/nativeMessages.js'
import { createNameOperations } from '../src/operations/nameOperations.js'
import { classifySessionObservation, validateSessionObservation } from '../src/platform/sessionObserver.js'

const sessionId = '123:42:42.123.17'
const otherSessionId = '123:42:42.123.18'

const nativeText = (runtime_id: string, text: string, y0: number) => ({
  runtime_id, kind: 'text', text, sender: 'peer', bounds: { x0: 0, x1: 100, y0, y1: y0 + 10 }, clipped: false,
})

test('native observation preserves exact multiline text and cannot treat an unconfirmed row as new messages', () => {
  const row = (runtime_id: string, kind: string, text: string, sender: string, y0: number) => ({
    runtime_id, kind, text, sender, bounds: { x0: 0, x1: 100, y0, y1: y0 + 10 }, clipped: false,
  })
  const base = {
    title: '会话', session_identity: sessionId, is_group: false, input_empty: true, png: 'local-only',
    rows: [row('1', 'time', '昨天 10:00', 'system', 0), row('2', 'text', ' 上一行\n下一行 ', 'peer', 20)],
  }
  const alignment = new ConversationAligner()
  const first = parseNativeObservation(base, { targetName: '会话' })
  assert.deepEqual(first.objects.map(({ sender, text }) => ({ sender, text })), [{ sender: 'peer', text: ' 上一行\n下一行 ' }])
  const aligned = alignment.align(first.objects, null)
  assert.equal(aligned.kind, 'ok')
  assert.ok(alignment.lastKnownId)
  const next = parseNativeObservation({
    ...base, rows: [...base.rows, row('3', 'text', '待确认', 'unknown', 40)],
  }, { targetName: '会话' })
  assert.notEqual(next.frame, first.frame)
  assert.deepEqual(alignment.align(next.objects, alignment.lastKnownId), { kind: 'gap', reason: 'sender_ambiguous' })
  assert.throws(() => parseNativeObservation(base, { targetName: '另一个会话' }), /会话证据不可用/)
  assert.throws(() => parseNativeObservation({ ...base, session_identity: otherSessionId }, {
    targetName: '会话', nativeSessionIdentity: sessionId,
  }), /会话证据不可用/)
  assert.notEqual(parseNativeObservation({ ...base, session_identity: otherSessionId }, { targetName: '会话' }).frame, first.frame)
  assert.throws(() => parseNativeObservation({ ...base, rows: [base.rows[1], base.rows[1]] }, { targetName: '会话' }), /消息行顺序不确定/)
})

test('local native watch pins the selected row through later same-title reads and needs no evidence files', async () => {
  const baselineRow = nativeText('one', '相同历史', 0)
  let round = 0
  const { observe } = createNameOperations({
    observeCurrentOnly: true,
    nativeDriver: async (options) => {
      assert.equal(options.useVisionProxy, false)
      const req = JSON.parse(options.stdin!)
      assert.equal(req.target_name, '同名会话')
      assert.equal(req.include_evidence, false)
      assert.equal('open' in req, false)
      assert.equal(req.session_identity, round === 0 ? undefined : sessionId)
      round++
      return {
        title: req.target_name, session_identity: round === 2 || round === 4 ? otherSessionId : sessionId,
        is_group: false, input_empty: true,
        rows: round === 1 ? [{ ...baselineRow, sender: 'unknown' }] : round === 3 ? [baselineRow]
          : [baselineRow, nativeText('two', round === 5 ? '真实新增' : '其他同名会话正文', 20)],
      }
    },
  })
  const ctx = { signal: new AbortController().signal, progress() {} }
  const request = { target_name: '同名会话', conversation_binding_id: 'local-binding', binding_version: 1, account_identity_version: 0 }
  const unalignable = await observe.execute({ ...request, watermark: null }, ctx)
  assert.equal(unalignable.data.gap_reason, 'sender_ambiguous')
  const wrongBeforeBaseline = await observe.execute({ ...request, watermark: null }, ctx)
  assert.equal(wrongBeforeBaseline.data.coverage, 'gap')
  assert.deepEqual(wrongBeforeBaseline.data.ordered_messages, [])
  const first = await observe.execute({ ...request, watermark: null }, ctx)
  const baseline = validateSessionObservation(first.data)
  assert.ok(baseline.ok)
  assert.equal('session_identity' in first.data, false)
  assert.match(baseline.value.ordered_messages[0]!.source_evidence_ref, /^local-frame:[a-f0-9]{64}$/)
  const state = classifySessionObservation({ ...request, watermark: null }, baseline.value)
  assert.equal(state.kind, 'baseline')

  const wrong = await observe.execute({ ...request, watermark: state.watermark }, ctx)
  assert.equal(wrong.data.coverage, 'gap')
  assert.deepEqual(wrong.data.ordered_messages, [])
  const next = await observe.execute({ ...request, watermark: state.watermark }, ctx)
  const resumed = validateSessionObservation(next.data)
  assert.ok(resumed.ok)
  assert.deepEqual(resumed.value.ordered_messages.map((row) => row.text), ['真实新增'])
})

test('shared driver confirms directions by bubble pixels and stops if the native viewport changes mid-capture',
  { skip: process.platform !== 'win32' }, () => {
    const dir = mkdtempSync(join(tmpdir(), 'weixin-native-message-pixels-'))
    try {
      const script = join(dir, 'native-messages-test.ps1')
      const shared = fileURLToPath(new URL('../../drivers/ps1/native-messages.ps1', import.meta.url)).replaceAll("'", "''")
      writeFileSync(script, `\ufeff
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$null=Add-Type -AssemblyName System.Drawing
. '${shared}'
Add-Type -TypeDefinition @'
using System;
public static class WeixinProbeWin32 {
 public struct RECT { public int Left,Top,Right,Bottom; }
 public static int GetDpiForWindow(IntPtr hwnd){ return 96; }
 public static bool GetWindowRect(IntPtr h,ref RECT rect) { rect.Left=100;rect.Top=200;rect.Right=700;rect.Bottom=600;return true; }
}
'@
function Throw-DriverError($code,$message){ throw "WXDRIVE|$code|$message" }
function Resolve-WeixinMainWindowHwnd(){ return 123 }
function Get-WindowIdentityByHwnd($h){ @{ProcessId=42} }
function Test-WeixinMainIdentity($identity,$h){ $h -eq 123 -and $identity.ProcessId -eq 42 }
function Get-WeixinNativeTitleInfo($session){ if (-not $script:hideTitle) { @{title='合成会话';is_group=$true} } }
function Get-WeixinNativeList($Session,$Id,$Class) { if ($script:hideList) { throw 'no UIA session list' }; return @{} }
function Get-WeixinNativeSelectedSessionId($session){
    $script:selectionCalls++
    if ($script:sameTitleSwitch -and $script:selectionCalls -gt 1) { return '123:42:17.2' }
    return $script:selectedSessionId
}
function Get-WeixinNativeSenderName { return }
function Get-WeixinNativeRows($session){
    $script:probeCalls++
    $i=0
    $rows=@(foreach($name in @('昨天 10:00','原生左侧正文','原生右侧正文','两侧无法确认','[文件]','被裁切文本')){
        $top=200+$i*45
        $state=if($script:drift -and $script:probeCalls -eq 2 -and $i -eq 1){'新状态文本'}else{$name}
        $class=if($i -eq 0){'mmui::ChatItemView'}elseif($i -eq 4){'mmui::ChatBubbleItemView'}else{'mmui::ChatTextItemView'}
        [ordered]@{runtime_id=[string]$i; class=$class; text=$state; left=100; top=$top; right=700; bottom=$top+44; clipped=$i -eq 5}
        $i++
    })
    [ordered]@{title='合成会话';is_group=$true;viewport=[ordered]@{left=100;top=200;right=700;bottom=600};rows=$rows}
}
function Get-WeixinWindowSnapshot($h){
    $bitmap=[Drawing.Bitmap]::new(600,400)
    $graphics=[Drawing.Graphics]::FromImage($bitmap)
    $stream=[IO.MemoryStream]::new()
    $left=[Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(47,47,48))
    $right=[Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(53,210,141))
    try{
        $graphics.Clear([Drawing.Color]::FromArgb(30,30,31))
        $graphics.FillRectangle($left,72,55,85,20)
        $graphics.FillRectangle($right,445,100,80,20)
        $graphics.FillRectangle($left,72,145,85,20)
        $graphics.FillRectangle($right,445,145,80,20)
        $graphics.FillRectangle($left,72,230,85,20)
        $bitmap.Save($stream,[Drawing.Imaging.ImageFormat]::Png)
        $rect=New-Object WeixinProbeWin32+RECT
        $null=[WeixinProbeWin32]::GetWindowRect([IntPtr]123,[ref]$rect)
        @{WindowRect=$rect;Image=$stream.ToArray()}
    }finally{ $left.Dispose();$right.Dispose();$stream.Dispose();$graphics.Dispose();$bitmap.Dispose() }
}
$script:selectedSessionId='123:42:17.1'
$script:sameTitleSwitch=$false
$script:selectionCalls=0
$session=Get-WeixinCurrentNativeSession -TargetName '合成会话'
$wrongName=try { Get-WeixinCurrentNativeSession -TargetName '另一个会话' } catch { $_.Exception.Message }
$script:hideTitle=$true
$missingTitle=try { Get-WeixinCurrentNativeSession -TargetName '合成会话' } catch { $_.Exception.Message }
$script:hideList=$true
$missingList=try { Get-WeixinCurrentNativeSession -TargetName '合成会话' } catch { $_.Exception.Message }
$script:hideTitle=$false
$script:hideList=$false
$wrongSelection=try { Get-WeixinCurrentNativeSession -TargetName '合成会话' -ExpectedSessionIdentity '123:42:17.2' } catch { $_.Exception.Message }
$script:probeCalls=0
$script:drift=$false
$result=Get-WeixinNativeMessageSnapshot -Session $session
$observed=@($result.rows | ForEach-Object {@($_.kind,$_.sender) -join ':'})
$peerNickname=@($result.rows | Where-Object { $_.sender -eq 'peer' })[0].sender_name
$script:probeCalls=0
$script:drift=$true
$failure=try { Get-WeixinNativeMessageSnapshot -Session $session } catch { $_.Exception.Message }
$script:probeCalls=0
$script:drift=$false
$script:selectionCalls=0
$script:sameTitleSwitch=$true
$wrongSnapshot=try { Get-WeixinNativeMessageSnapshot -Session $session } catch { $_.Exception.Message }
ConvertTo-Json -InputObject @{
    observed=$observed;failure=$failure;peer_nickname=$peerNickname;has_evidence=[bool]$result.png
    wrong_name=$wrongName;missing_title=$missingTitle;missing_list=$missingList
    wrong_selection=$wrongSelection;wrong_snapshot=$wrongSnapshot;session_identity=$result.session_identity
} -Compress
`, 'utf8')
      const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', script],
        { encoding: 'utf8', timeout: 15_000, windowsHide: true })
      assert.equal(result.status, 0, result.stderr)
      const data = JSON.parse(result.stdout)
      assert.deepEqual(data.observed, ['time:system', 'text:peer', 'text:self', 'text:unknown', 'media:unknown', 'text:unknown'])
      assert.equal(data.peer_nickname, null)
      assert.equal(data.has_evidence, false)
      assert.equal(data.session_identity, '123:42:17.1')
      assert.match(data.wrong_name, /^WXDRIVE\|UI_CHANGED\|.*会话与目标不一致/)
      assert.match(data.missing_title, /^WXDRIVE\|UI_CHANGED\|.*未读到当前会话标题/)
      assert.match(data.missing_list, /^WXDRIVE\|UI_CHANGED\|.*无法读取原生会话列表（UIA）/)
      assert.match(data.wrong_selection, /^WXDRIVE\|UI_CHANGED\|.*选中会话/)
      assert.match(data.wrong_snapshot, /^WXDRIVE\|UI_CHANGED\|.*选中会话/)
      assert.match(data.failure, /^WXDRIVE\|UI_CHANGED\|/)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
