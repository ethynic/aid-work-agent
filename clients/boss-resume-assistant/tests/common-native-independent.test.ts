import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execPowerShellScript, PowerShellScriptError } from '../src/main/input/powerShell.js'

test('independent: production native ownership selects one main window behind owned dialogs and refuses two independent windows',
  { skip: process.platform !== 'win32', timeout: 30_000 }, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'boss-native-independent-'))
  assert.ok(directory.startsWith(join(tmpdir(), 'boss-native-independent-')))
  try {
    const source = await readFile(fileURLToPath(new URL('../../scripts/BossWindowExperiment.cs', import.meta.url)), 'utf8')
    const start = source.indexOf('    public static IntPtr Find(')
    const end = source.indexOf('    public static IntPtr[] OwnedDialogs(', start)
    assert.ok(start > 0 && end > start)
    // Execute the actual two ownership algorithms; replace only their native boundary.
    const compiled = `using System; using System.Collections.Generic;
public static class IndependentNativeOwnership {
    public delegate bool EnumProc(IntPtr window, IntPtr data);
    static int[] Windows = new int[] { 20, 12, 11, 10 };
    static bool SecondMain;
    static bool EnumWindows(EnumProc cb, IntPtr data) { foreach(int id in Windows) cb(new IntPtr(id),data); if(SecondMain) cb(new IntPtr(30),data); return true; }
    static bool IsWindowVisible(IntPtr window) { return true; }
    static uint ProcessId(IntPtr window) { return window.ToInt32() == 20 ? (uint)200 : (uint)100; }
    static string ClassName(IntPtr window) { return "Chrome_WidgetWin_1"; }
    static string Title(IntPtr window) { return "BOSS"; }
    static IntPtr GetAncestor(IntPtr window, uint flags) { return window; }
    static IntPtr GetWindow(IntPtr window, uint command) { return new IntPtr(window.ToInt32() == 12 ? 11 : window.ToInt32() == 11 ? 10 : 0); }
${source.slice(start, end)}
    public static void Probe() {
        if(Find(100).ToInt32()!=10) throw new Exception("Owned print/dialog must not displace main browser");
        if(Find(200).ToInt32()!=20) throw new Exception("Other debug PID must stay isolated");
        if(!BelongsTo(new IntPtr(12),new IntPtr(10))) throw new Exception("Nested owned dialog chain lost");
        if(BelongsTo(new IntPtr(20),new IntPtr(10))) throw new Exception("Foreign PID admitted");
        SecondMain=true;
        bool refused=false;
        try { Find(100); } catch(InvalidOperationException) { refused=true; }
        if(!refused) throw new Exception("Ambiguous independent browser windows must not use foreground guessing");
    }
}`
    const script = join(directory, 'visual-region-compare.ps1')
    await writeFile(script, `Add-Type -TypeDefinition @'
${compiled}
'@
[IndependentNativeOwnership]::Probe()
Write-Output 'independent-native-ownership-passed'
`)
    const result = await execPowerShellScript(script, [], { timeout: 15_000 })
    assert.match(result.stdout, /independent-native-ownership-passed/)
  } finally { await rm(directory, { recursive: true, force: true }) }
})

test('independent: missing and hidden bound render widgets execute the production zero-input exit-two path',
  { skip: process.platform !== 'win32', timeout: 30_000 }, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'boss-native-independent-'))
  assert.ok(directory.startsWith(join(tmpdir(), 'boss-native-independent-')))
  try {
    const source = await readFile(fileURLToPath(new URL('../../scripts/win-click.ps1', import.meta.url)), 'utf8')
    const start = source.indexOf('$rw = [Win32]::FindRenderWidget($win)')
    const end = source.indexOf('$pt = Get-ScreenPoint', start)
    const finalCatch = source.lastIndexOf('} catch {')
    assert.ok(start > 0 && end > start && finalCatch > end)
    for (const handle of [0, 100]) {
      const script = join(directory, 'visual-region-compare.ps1')
      await writeFile(script, `\ufeff$ErrorActionPreference='Stop'
Add-Type -TypeDefinition 'using System; public class Win32 { public static IntPtr FindRenderWidget(IntPtr w) { return new IntPtr(${handle}); } public static bool IsWindowVisible(IntPtr w) { return false; } }'
$CdpPort=9333
$win=[IntPtr]10
function Assert-BoundForeground {}
$physicalScope=[PSCustomObject]@{InputStarted=$false}
$physicalScope | Add-Member -MemberType ScriptMethod -Name Dispose -Value {}
try {
${source.slice(start, end)}
throw 'Unexpected input path'
${source.slice(finalCatch)}
`)
      await assert.rejects(execPowerShellScript(script, [], { timeout: 10_000 }), (error: unknown) => {
        assert.ok(error instanceof PowerShellScriptError)
        assert.equal(error.exitCode, 2)
        assert.match(error.message, handle ? /render\s+widget\s+hidden/ : /render\s+widget\s+unavailable/)
        assert.doesNotMatch(error.message, /BOSS_INPUT_UNKNOWN|Unexpected input path/)
        return true
      })
    }
  } finally { await rm(directory, { recursive: true, force: true }) }
})
