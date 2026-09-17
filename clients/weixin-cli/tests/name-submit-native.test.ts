import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

test('native submit executes one ordered input batch and never retries failed inputs', { skip: process.platform !== 'win32' }, () => {
  const dir = mkdtempSync(join(tmpdir(), 'wx-submit-native-'))
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
foreach($node in $ifs){foreach($clause in $node.Clauses){if($clause.Item1.Extent.Text -eq "$"+"req.action -eq 'submit'"){$body=$clause.Item2.Extent.Text}}}
if(-not $body){throw 'Submit branch missing'}
$submit=[scriptblock]::Create($body.Substring(1,$body.Length-2))
# No user32 functions are imported: the actual production branch runs against this stub.
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class WeixinProbeWin32 {
 public struct RECT { public int Left,Top,Right,Bottom; }
 [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk,wScan; public uint dwFlags,time; public IntPtr dwExtraInfo; }
 [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx,dy; public uint mouseData,dwFlags,time; public IntPtr dwExtraInfo; }
 [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION { [FieldOffset(0)] public KEYBDINPUT ki; [FieldOffset(0)] public MOUSEINPUT mi; }
 [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public INPUTUNION u; }
 public static long Foreground=123;
 public static int Offset=0, Calls=0, Accepted=10, Releases=0;
 public static List<string> Events=new List<string>();
 public static IntPtr GetForegroundWindow(){return new IntPtr(Foreground);}
 public static bool GetWindowRect(IntPtr h, ref RECT r){r.Left=10+Offset;r.Top=20;r.Right=810;r.Bottom=620;return true;}
 public static uint SendInput(int n, INPUT[] events,int size){Calls++;foreach(var e in events)Events.Add(e.u.ki.wVk+":"+e.u.ki.dwFlags);return (uint)Accepted;}
 public static void keybd_event(byte key,byte scan,int flags,IntPtr extra){Releases++;}
}
'@
function Throw-DriverError($code,$message){throw "WXDRIVE|$code|$message"}
Add-Type -AssemblyName System.Drawing
$work='${dir.replace(/'/g, "''")}'
$script:handle=123
function Get-WeixinWindowSnapshot {
 param($Hwnd,$Path)
 $bitmap=[Drawing.Bitmap]::new(800,600)
 $graphics=[Drawing.Graphics]::FromImage($bitmap)
 try{
  $graphics.Clear([Drawing.Color]::White)
  $graphics.FillRectangle([Drawing.Brushes]::Black,230,0,2,600)
  $graphics.FillRectangle([Drawing.Brushes]::Black,230,450,570,2)
  $bitmap.Save($Path,[Drawing.Imaging.ImageFormat]::Png)
 }finally{$graphics.Dispose();$bitmap.Dispose()}
 return @(10,20,800,600,$true)
}
$captureDefinition=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Capture'},$true)
. ([scriptblock]::Create($captureDefinition.Extent.Text))
$sendCapture=Capture -SendOnly
if(@($sendCapture.rect).Count -ne 4){throw 'Send capture rectangle must contain exactly four coordinates'}
if($sendCapture.ContainsKey('rgba') -or $sendCapture.ContainsKey('frame')){throw 'Send capture must omit full-window pixel analysis'}
if($sendCapture.input_region.x1 -le $sendCapture.input_region.x0){throw 'Send capture composer invalid'}
function Capture {throw 'Submit must not capture again'}
function Send-CheckedComposerClick {param($Hwnd,$ScreenX,$ScreenY) $script:clicks++}
function Set-ClipboardTextRetry {param($Text) $script:copied=$Text;if($script:mode -eq 'clipboard'){throw 'clipboard unavailable'};if($script:mode -eq 'lost-after-copy'){[WeixinProbeWin32]::Foreground=999}}
function Write-DriverJson($value){$script:result=$value}
$rows=@()
foreach($scenario in @('ok','clipboard','deadline','foreground','rect','lost-after-copy')+@(0..9|ForEach-Object{"partial-$_"})){
 $script:mode=$scenario;$script:handle=123;$script:clicks=0;$script:copied=$null;$script:result=$null
 [WeixinProbeWin32]::Foreground=123;[WeixinProbeWin32]::Offset=0;[WeixinProbeWin32]::Calls=0;[WeixinProbeWin32]::Accepted=10;[WeixinProbeWin32]::Releases=0;[WeixinProbeWin32]::Events.Clear()
 $req=[pscustomobject]@{text='synthetic whole message';handle=123;rect=@(10,20,800,600);input_region=@{x0=300;y0=400;x1=750;y1=550};deadline_ms=([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()+60000)}
 if($scenario -eq 'deadline'){$req.deadline_ms=0}
 if($scenario -eq 'foreground'){[WeixinProbeWin32]::Foreground=999}
 if($scenario -eq 'rect'){[WeixinProbeWin32]::Offset=1}
 if($scenario -like 'partial-*'){[WeixinProbeWin32]::Accepted=[int]$scenario.Substring(8)}
 $errorText=$null
 try{. $submit}catch{$errorText=$_.Exception.Message}
 $rows+=@{scenario=$scenario;calls=[WeixinProbeWin32]::Calls;events=@([WeixinProbeWin32]::Events.ToArray());releases=[WeixinProbeWin32]::Releases;clicks=$script:clicks;copied=$script:copied;done=$script:result.data.done;error=$errorText}
}
ConvertTo-Json -InputObject $rows -Depth 6 -Compress
`, 'utf8')
    const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', script], { windowsHide: true, encoding: 'utf8', timeout: 30_000 })
    assert.equal(result.status, 0, result.stderr)
    const rows = JSON.parse(result.stdout.trim()) as Array<{scenario:string;calls:number;events:string[];releases:number;clicks:number;copied:string|null;done:boolean|null;error:string|null}>
    const ok = rows.find(row => row.scenario === 'ok')!
    assert.equal(ok.done, true)
    assert.equal(ok.calls, 1)
    assert.equal(ok.clicks, 1)
    assert.equal(ok.copied, 'synthetic whole message')
    assert.deepEqual(ok.events, ['17:0','65:0','65:2','17:2','17:0','86:0','86:2','17:2','13:0','13:2'])
    for (const row of rows.filter(row => row.scenario !== 'ok' && !row.scenario.startsWith('partial-'))) {
      assert.equal(row.calls, 0, row.scenario)
      assert.ok(row.error, row.scenario)
      if (['deadline','foreground','rect'].includes(row.scenario)) assert.equal(row.clicks, 0, row.scenario)
    }
    for (const partial of rows.filter(row => row.scenario.startsWith('partial-'))) {
      assert.equal(partial.calls, 1)
      assert.equal(partial.releases, 4)
      assert.match(partial.error!, /EXECUTION_UNKNOWN/)
      assert.notEqual(partial.done, true)
    }
  } finally { rmSync(dir, { recursive: true, force: true }) }
})
