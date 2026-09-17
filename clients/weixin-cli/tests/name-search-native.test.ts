import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

test('native search needs only window geometry and stops on unavailable foreground or geometry', { skip: process.platform !== 'win32' }, () => {
  const dir = mkdtempSync(join(tmpdir(), 'wx-search-native-'))
  try {
    const driver = fileURLToPath(new URL('../../drivers/ps1/name-ocr.ps1', import.meta.url)).replace(/'/g, "''")
    const script = join(dir, 'test.ps1')
    writeFileSync(script, `
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('${driver}',[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Driver has parse errors'}
$ifs=$ast.FindAll({param($node) $node -is [Management.Automation.Language.IfStatementAst]},$true)
$body=$null
foreach($node in $ifs){foreach($clause in $node.Clauses){if($clause.Item1.Extent.Text -eq "$"+"req.action -eq 'search'"){$body=$clause.Item2.Extent.Text}}}
if(-not $body){throw 'Search branch missing'}
$search=[scriptblock]::Create($body.Substring(1,$body.Length-2))
# Execute only the production branch; do not import user32 or the real driver helpers.
Add-Type -TypeDefinition @'
using System;
public static class WeixinProbeWin32 {
 public struct RECT { public int Left,Top,Right,Bottom; }
 public static bool GeometryAvailable=true;
 public static int Left=10,Top=20,Width=800,Height=600,RectCalls=0;
 public static IntPtr GetForegroundWindow(){return new IntPtr(123);}
 public static bool GetWindowRect(IntPtr h,ref RECT r){RectCalls++;if(h.ToInt64()!=123)throw new Exception("Wrong window");r.Left=Left;r.Top=Top;r.Right=Left+Width;r.Bottom=Top+Height;return GeometryAvailable;}
}
'@
function Throw-DriverError($code,$message){throw "WXDRIVE|$code|$message"}
function Capture {$script:captures++;throw 'No chat divider or composer on blank home screen'}
function SearchCapture {throw 'Search must not capture a popup'}
function Invoke-WeixinActivation($Hwnd){if($Hwnd -ne 123){throw 'Wrong activation window'};return $script:mode -ne 'foreground'}
function Send-WeixinPostMessageClick {param($Hwnd,$ScreenX,$ScreenY) $script:events.Add('click');$script:click=@($Hwnd,$ScreenX,$ScreenY)}
function Send-WeixinKeyChord {param($Keys,$ForegroundGuard) if(-not (& $ForegroundGuard)){throw 'Foreground guard failed'};$script:events.Add('keys');$script:keys=@($Keys)}
function Send-WeixinPostMessageText {param($Hwnd,$Text) if($Hwnd -ne 123){throw 'Wrong text window'};$script:events.Add('text');$script:typed=$Text}
function Start-Sleep {param($Milliseconds)}
function Write-DriverJson($value){$script:result=$value}
$rows=@()
foreach($scenario in @('blank-home','normal','negative-origin','geometry','foreground')){
 $script:mode=$scenario;$script:handle=123;$script:captures=0;$script:click=$null;$script:keys=$null;$script:typed=$null;$script:result=$null
 $script:events=[Collections.Generic.List[string]]::new()
 [WeixinProbeWin32]::GeometryAvailable=$scenario -ne 'geometry';[WeixinProbeWin32]::RectCalls=0
 [WeixinProbeWin32]::Left=10;[WeixinProbeWin32]::Top=20;[WeixinProbeWin32]::Width=800;[WeixinProbeWin32]::Height=600
 if($scenario -eq 'negative-origin'){[WeixinProbeWin32]::Left=-1920;[WeixinProbeWin32]::Top=-100;[WeixinProbeWin32]::Width=1200;[WeixinProbeWin32]::Height=900}
 $req=[pscustomobject]@{target_name='synthetic contact'}
 $errorText=$null
 try{. $search}catch{$errorText=$_.Exception.Message}
 $rows+=@{scenario=$scenario;captures=$script:captures;rectCalls=[WeixinProbeWin32]::RectCalls;click=$script:click;keys=$script:keys;typed=$script:typed;events=@($script:events.ToArray());done=$script:result.data.done;error=$errorText}
}
ConvertTo-Json -InputObject $rows -Depth 6 -Compress
`, 'utf8')
    const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', script], { windowsHide: true, encoding: 'utf8', timeout: 30_000 })
    assert.equal(result.status, 0, result.stderr)
    const rows = JSON.parse(result.stdout.trim()) as Array<{scenario:string;captures:number;rectCalls:number;click:number[]|null;keys:string[]|null;typed:string|null;events:string[];done:boolean|null;error:string|null}>
    assert.equal(rows.length, 5)
    for (const row of rows) {
      assert.equal(row.captures, 0, row.scenario)
      if (['geometry', 'foreground'].includes(row.scenario)) {
        assert.deepEqual(row.events, [], row.scenario)
        assert.equal(row.rectCalls, row.scenario === 'foreground' ? 0 : 1)
        assert.notEqual(row.done, true)
        assert.match(row.error!, row.scenario === 'foreground' ? /WXDRIVE\|FOREGROUND_LOST\|Search requires foreground/ : /WXDRIVE\|UI_CHANGED\|Search window geometry unavailable/)
      } else {
        assert.equal(row.error, null, row.scenario)
        assert.equal(row.done, true, row.scenario)
        assert.equal(row.rectCalls, 1)
        assert.deepEqual(row.click, row.scenario === 'negative-origin' ? [123, -1716, -46] : [123, 146, 56])
        assert.deepEqual(row.keys, ['CTRL', 'A'])
        assert.equal(row.typed, 'synthetic contact')
        assert.deepEqual(row.events, ['click', 'keys', 'text'])
      }
    }
  } finally { rmSync(dir, { recursive: true, force: true }) }
})
