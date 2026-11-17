import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

test('native submit checks the current title and composer before clicking and again before sending', { skip: process.platform !== 'win32' }, () => {
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
$branches=@{}
foreach($node in $ifs){
 foreach($clause in $node.Clauses){
  foreach($action in @('submit','send_capture')){
   if($clause.Item1.Extent.Text -ceq "$"+"req.action -eq '$action'"){
    $body=$clause.Item2.Extent.Text
    $branches[$action]=[scriptblock]::Create($body.Substring(1,$body.Length-2))
   }
  }
 }
}
if($branches.Count -ne 2){throw 'Send branch missing'}
$submit=$branches.submit
$capture=$branches.send_capture
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
$script:windowProcessId=42
$script:handle=123
function Capture {throw 'Send must not take an OCR screenshot'}
function Get-WeixinWindowSnapshot {throw 'Send must not take a window screenshot'}
function Invoke-WeixinActivation {param($Hwnd) return ($Hwnd -eq 123)}
function Get-SendUiaElements([int64]$Hwnd){
 if($Hwnd -ne 123){throw 'Title query must use the expected handle'}
 $script:titleReads++
 if($script:mode -eq 'observation-unavailable'){throw 'UIA unavailable'}
 $name=if($script:mode -eq 'changed-title' -or ($script:mode -eq 'changed-after-copy' -and $script:copied) -or
  ($script:mode -eq 'observation-changed' -and $script:capturedObservation)){
   'different chat'
 }else{'synthetic target'}
 $node=@{ProcessId=42; IsOffscreen=$false; ClassName='mmui::XTextView'; AutomationId='window.current_chat_name_label'; Name=$name}
 if($script:mode -ne 'missing-title'){$node}
 if($script:mode -eq 'ambiguous-title'){$node}
 @{ProcessId=99; IsOffscreen=$false; ClassName='mmui::XTextView'; AutomationId='foreign.current_chat_name_label'; Name='foreign chat'}
 @{ProcessId=42; IsOffscreen=$true; ClassName='mmui::XTextView'; AutomationId='hidden.current_chat_name_label'; Name='hidden chat'}
 $right=if($script:mode -eq 'changed-composer'){761}else{760}
 @{ProcessId=42; IsOffscreen=$false; ClassName='mmui::ChatInputField'; AutomationId='chat_input_field'; IsEnabled=$true;
   BoundingRectangle=@{IsEmpty=$false; Left=310; Top=420; Right=$right; Bottom=570}}
}
foreach($name in @('Select-WeixinNativeTitle','Get-SendCapture')){
 $definition=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
 . ([scriptblock]::Create($definition.Extent.Text))
}
function Send-CheckedComposerClick {param($Hwnd,$ScreenX,$ScreenY)
 if($Hwnd -ne 123 -or $ScreenX -ne 535 -or $ScreenY -ne 495){throw 'Expected the live composer center'}
 $script:clicks++
}
function Set-ClipboardTextRetry {param($Text) $script:copied=$Text;if($script:mode -eq 'clipboard'){throw 'clipboard unavailable'};if($script:mode -eq 'lost-after-copy'){[WeixinProbeWin32]::Foreground=999}}
function Write-DriverJson($value){$script:result=$value}
$script:mode='ok';$script:titleReads=0
. $capture
$sendCapture=$script:result.data
if((@($sendCapture.Keys|Sort-Object) -join ',') -cne 'handle,input_region,rect,title'){throw 'Send capture must omit screenshot and other metadata'}
if((@($sendCapture.rect) -join ',') -cne '10,20,800,600'){throw 'Send capture rectangle must contain exactly four coordinates'}
if($sendCapture.title -cne 'synthetic target' -or $script:titleReads -ne 1){throw 'Expected one native title read'}
$rows=@()
foreach($scenario in @('ok','clipboard','deadline','foreground','rect','lost-after-copy','changed-title','changed-after-copy','missing-title','ambiguous-title','changed-composer')+@(0..9|ForEach-Object{"partial-$_"})){
 $script:mode=$scenario;$script:handle=123;$script:clicks=0;$script:copied=$null;$script:result=$null;$script:titleReads=0
 [WeixinProbeWin32]::Foreground=123;[WeixinProbeWin32]::Offset=0;[WeixinProbeWin32]::Calls=0;[WeixinProbeWin32]::Accepted=10;[WeixinProbeWin32]::Releases=0;[WeixinProbeWin32]::Events.Clear()
 $req=[pscustomobject]@{target_name='formatted synthetic target';expected_native_title=$sendCapture.title;text='synthetic whole message';handle=$sendCapture.handle;rect=$sendCapture.rect;input_region=$sendCapture.input_region;deadline_ms=([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()+60000)}
 if($scenario -eq 'deadline'){$req.deadline_ms=0}
 if($scenario -eq 'foreground'){[WeixinProbeWin32]::Foreground=999}
 if($scenario -eq 'rect'){[WeixinProbeWin32]::Offset=1}
 if($scenario -like 'partial-*'){[WeixinProbeWin32]::Accepted=[int]$scenario.Substring(8)}
 $errorText=$null
 try{. $submit}catch{$errorText=$_.Exception.Message}
 $rows+=@{scenario=$scenario;calls=[WeixinProbeWin32]::Calls;events=@([WeixinProbeWin32]::Events.ToArray());releases=[WeixinProbeWin32]::Releases;clicks=$script:clicks;copied=$script:copied;done=$script:result.data.done;error=$errorText;title_reads=$script:titleReads}
}
Add-Type -AssemblyName System.Drawing
$work='${dir.replace(/'/g, "''")}'
function Get-WeixinWindowSnapshot {
 param($Hwnd,$Path)
 $script:capturedObservation=$true
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
foreach($name in @('Read-ObservedNativeTitle','Hash','CropHash','Capture')){
 $definition=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
 . ([scriptblock]::Create($definition.Extent.Text))
}
$observations=@(foreach($scenario in @('observation-stable','observation-changed','observation-unavailable')){
 $script:mode=$scenario;$script:capturedObservation=$false;$script:titleReads=0
 $baseline=Capture -ReadNativeTitle
 @{scenario=$scenario;has_frame=[bool]$baseline.frame;native_title=$baseline.native_title;title_reads=$script:titleReads}
})
ConvertTo-Json -InputObject @{submissions=$rows;observations=$observations} -Depth 6 -Compress
`, 'utf8')
    const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', script], { windowsHide: true, encoding: 'utf8', timeout: 30_000 })
    assert.equal(result.status, 0, result.stderr)
    const { submissions: rows, observations } = JSON.parse(result.stdout.trim()) as {
      submissions: Array<{scenario:string;calls:number;events:string[];releases:number;clicks:number;copied:string|null;done:boolean|null;error:string|null;title_reads:number}>
      observations: unknown[]
    }
    assert.deepEqual(observations, [
      { scenario: 'observation-stable', has_frame: true, native_title: 'synthetic target', title_reads: 2 },
      { scenario: 'observation-changed', has_frame: true, native_title: null, title_reads: 2 },
      { scenario: 'observation-unavailable', has_frame: true, native_title: null, title_reads: 1 },
    ])
    const ok = rows.find(row => row.scenario === 'ok')!
    assert.equal(ok.done, true)
    assert.equal(ok.calls, 1)
    assert.equal(ok.clicks, 1)
    assert.equal(ok.title_reads, 2)
    assert.equal(ok.copied, 'synthetic whole message')
    assert.deepEqual(ok.events, ['17:0','65:0','65:2','17:2','17:0','86:0','86:2','17:2','13:0','13:2'])
    for (const row of rows.filter(row => row.scenario !== 'ok' && !row.scenario.startsWith('partial-'))) {
      assert.equal(row.calls, 0, row.scenario)
      assert.ok(row.error, row.scenario)
      assert.doesNotMatch(row.error!, /synthetic target|different chat|synthetic whole message/)
      if (['deadline','foreground','rect','changed-title','missing-title','ambiguous-title','changed-composer'].includes(row.scenario)) {
        assert.equal(row.clicks, 0, row.scenario)
        assert.equal(row.copied, null, row.scenario)
      }
      if (row.scenario.startsWith('changed-') && row.scenario !== 'changed-composer') assert.match(row.error!, /TARGET_AMBIGUOUS/)
      if (row.scenario === 'changed-after-copy') {
        assert.equal(row.title_reads, 2)
        assert.equal(row.clicks, 1)
      }
    }
    for (const partial of rows.filter(row => row.scenario.startsWith('partial-'))) {
      assert.equal(partial.calls, 1)
      assert.equal(partial.releases, 4)
      assert.match(partial.error!, /EXECUTION_UNKNOWN/)
      assert.notEqual(partial.done, true)
    }
  } finally { rmSync(dir, { recursive: true, force: true }) }
})
