import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const ROOT = fileURLToPath(new URL('../../', import.meta.url))
// Compiled tests live in dist/tests, while scripts are package resources.
const SCRIPTS = path.resolve(ROOT, 'scripts')
const windows = process.platform === 'win32'

function runProbe(source: string, resources: Record<string, string> = {}, args: string[] = []) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'boss-physical-test-'))
  try {
    const probe = path.join(dir, 'probe.ps1')
    fs.writeFileSync(probe, '\ufeff' + source.replace(/^\ufeff/, ''))
    for (const [name, contents] of Object.entries(resources)) fs.writeFileSync(path.join(dir, name), contents)
    return spawnSync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', probe, ...args],
      { encoding: 'utf8', timeout: 30000 })
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
}

test('physical input: production DPI scope, mapping and guards refuse unsafe native results', { skip: !windows }, () => {
  const production = fs.readFileSync(path.join(SCRIPTS, 'BossPhysicalInput.cs'), 'utf8')
  // Replace only the native boundary. All scope, validation and movement logic is production code.
  const fakeNative = `    private static class Native
    {
        public static IntPtr SetThreadDpiAwarenessContext(IntPtr context) {
            if (FakeInput.MissingApi) throw new EntryPointNotFoundException("DPI API unavailable");
            if (context.ToInt64() == -4 && FakeInput.RefuseEnter) return IntPtr.Zero;
            if (context.ToInt64() == -1 && FakeInput.RefuseRestore) return IntPtr.Zero;
            var previous = FakeInput.Context;
            if (!FakeInput.IgnoreContext) FakeInput.Context = context;
            return previous;
        }
        public static IntPtr GetThreadDpiAwarenessContext() { return FakeInput.Context; }
        public static bool AreDpiAwarenessContextsEqual(IntPtr a, IntPtr b) { return a == b; }
        public static uint GetCurrentThreadId() { return FakeInput.Thread; }
        public static bool GetWindowRect(IntPtr hwnd, out Rect rect) { rect = FakeInput.Bounds; return FakeInput.RectOk; }
        public static bool GetPhysicalCursorPos(out Point point) { point = FakeInput.Cursor; return FakeInput.CursorOk; }
        public static bool SetCursorPos(int x, int y) {
            FakeInput.Moves++;
            if (!FakeInput.MoveOk) return false;
            FakeInput.Cursor = new Point { X = x + (FakeInput.MoveMismatch ? 1 : 0), Y = y };
            return true;
        }
        public static IntPtr WindowFromPoint(Point point) { return FakeInput.Owner; }
    }`
  const nativeBlock = /    private static class Native\r?\n    \{[\s\S]*?\r?\n    \}/
  assert.match(production, nativeBlock)
  const fakeSource = production.replace(nativeBlock, fakeNative) + `
public static class FakeInput {
    public static IntPtr Context = new IntPtr(-1), Owner = new IntPtr(100);
    public static uint Thread = 1;
    public static int Moves;
    public static bool RefuseEnter, RefuseRestore, IgnoreContext, MissingApi, MoveMismatch;
    public static bool RectOk = true, CursorOk = true, MoveOk = true;
    public static BossPhysicalInput.Rect Bounds = new BossPhysicalInput.Rect { Left=49, Top=568, Right=2553, Bottom=2042 };
    public static BossPhysicalInput.Point Cursor;
}`
  const mappings = ['win-click.ps1', 'cv-wheel.ps1'].map((file, i) => {
    const script = fs.readFileSync(path.join(SCRIPTS, file), 'utf8')
    const mapping = script.match(/function Get-ScreenPoint\([^\n]+\) \{[\s\S]*?\r?\n\}/)?.[0]
    assert.ok(mapping, `${file} mapping must be exercised`)
    return mapping.replace('Get-ScreenPoint', `Get-ScreenPoint${i}`)
  }).join('\n')
  const probe = runProbe(`$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
${fakeSource}
'@
${mappings}
function Assert($condition, $message) { if (-not $condition) { throw $message } }
function Refused($body, $message) {
  $caught = $false
  try { & $body } catch { $caught = $true; Assert ($_.Exception.ToString().Contains($message)) $_.Exception.ToString() }
  Assert $caught "expected refusal: $message"
}
$widget = [IntPtr]100
[FakeInput]::RefuseEnter = $true
Refused { [BossPhysicalInput]::Enter() } 'Cannot enter'
Assert ([FakeInput]::Moves -eq 0) 'failed enter moved cursor'
[FakeInput]::RefuseEnter = $false
[FakeInput]::MissingApi = $true
Refused { [BossPhysicalInput]::Enter() } 'unavailable'
[FakeInput]::MissingApi = $false
[FakeInput]::IgnoreContext = $true
Refused { [BossPhysicalInput]::Enter() } 'context lost'
Assert ([FakeInput]::Context -eq [IntPtr](-1)) 'failed verification not restored'
[FakeInput]::IgnoreContext = $false
$scope = [BossPhysicalInput]::Enter()
try {
  foreach ($mapping in @('Get-ScreenPoint0', 'Get-ScreenPoint1')) {
    $point = & $mapping $widget 2504 1474 684 276
    Assert ($point[0] -eq 733 -and $point[1] -eq 844) 'real 200 percent geometry mapping incorrect'
  }
  [BossPhysicalInput]::MoveCursor(733,844)
  [BossPhysicalInput]::RequireCursorOnWidget($widget,733,844)
  [FakeInput]::Owner = [IntPtr]101
  Refused { [BossPhysicalInput]::RequireCursorOnWidget($widget,733,844) } 'ownership changed'
  [FakeInput]::Owner = $widget
  [FakeInput]::Cursor = New-Object BossPhysicalInput+Point
  Refused { [BossPhysicalInput]::RequireCursorOnWidget($widget,733,844) } 'ownership changed'
  [FakeInput]::MoveOk = $false
  Refused { [BossPhysicalInput]::MoveCursor(733,844) } 'Cannot move'
  [FakeInput]::MoveOk = $true
  [FakeInput]::MoveMismatch = $true
  Refused { [BossPhysicalInput]::MoveCursor(733,844) } 'differs'
  [FakeInput]::MoveMismatch = $false
  [FakeInput]::CursorOk = $false
  Refused { [BossPhysicalInput]::MoveCursor(733,844) } 'Cannot read physical cursor'
  [FakeInput]::CursorOk = $true
  [FakeInput]::RectOk = $false
  Refused { Get-ScreenPoint0 $widget 2504 1474 684 276 } 'Cannot read physical'
  [FakeInput]::RectOk = $true
  [FakeInput]::Bounds = New-Object BossPhysicalInput+Rect
  Refused { Get-ScreenPoint1 $widget 2504 1474 684 276 } 'Cannot read physical'
  [FakeInput]::Context = [IntPtr](-1)
  $moves = [FakeInput]::Moves
  Refused { [BossPhysicalInput]::MoveCursor(733,844) } 'context lost'
  Assert ([FakeInput]::Moves -eq $moves) 'context loss moved cursor'
  [FakeInput]::Context = [IntPtr](-4)
  [FakeInput]::Thread = 2
  Refused { $scope.Dispose() } 'thread changed'
  [FakeInput]::Thread = 1
} finally { $scope.Dispose() }
Assert ([FakeInput]::Context -eq [IntPtr](-1)) 'normal scope not restored'
$scope.Dispose()
$scope = [BossPhysicalInput]::Enter()
try { throw 'simulated action failure' } catch {} finally { $scope.Dispose() }
Assert ([FakeInput]::Context -eq [IntPtr](-1)) 'exception scope not restored'
$scope = [BossPhysicalInput]::Enter()
[FakeInput]::RefuseRestore = $true
Refused { $scope.Dispose() } 'Cannot restore DPI context'
[FakeInput]::RefuseRestore = $false
$scope.Dispose()
$scope = [BossPhysicalInput]::Enter()
$scope.MarkInputStarted()
[FakeInput]::RefuseRestore = $true
Refused { $scope.Dispose() } '[BOSS_INPUT_UNKNOWN]'
[FakeInput]::RefuseRestore = $false
$scope.Dispose()
Write-Output 'physical-input-probe-passed'
`)
  assert.equal(probe.status, 0, probe.stderr || probe.stdout)
  assert.match(probe.stdout, /physical-input-probe-passed/)

  // Run the complete click entrypoint with fake native APIs: an unrelated HWND must
  // retain the established exit=2, restore context, and emit no mouse/key input.
  const script = fs.readFileSync(path.join(SCRIPTS, 'win-click.ps1'), 'utf8').replace(/\r\n/g, '\n')
  const start = script.indexOf('public class Win32 {')
  const end = script.indexOf('\n"@', start)
  assert.ok(start >= 0 && end > start)
  const fakeWindow = `public class Win32 {
    public static IntPtr FindWindowByTitle(string title) { return new IntPtr(10); }
    public static IntPtr FindRenderWidget(IntPtr parent) { return new IntPtr(100); }
    public static bool IsIconic(IntPtr hwnd) { return false; }
    public static bool IsWindowVisible(IntPtr hwnd) { return true; }
    public static bool ShowWindow(IntPtr hwnd, int command) { return true; }
    public static IntPtr GetForegroundWindow() { return new IntPtr(10); }
    public static uint GetWindowThreadProcessId(IntPtr hwnd, ref uint pid) { return 1; }
    public static uint GetCurrentThreadId() { return 1; }
    public static bool AttachThreadInput(uint a, uint b, bool attach) { return true; }
    public static bool BringWindowToTop(IntPtr hwnd) { return true; }
    public static bool SetForegroundWindow(IntPtr hwnd) { return true; }
    public static bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int w, int h, uint flags) { return true; }
    public const uint SWP_NOMOVE=2, SWP_NOSIZE=1, MOUSEEVENTF_LEFTDOWN=2, MOUSEEVENTF_LEFTUP=4;
    public static readonly IntPtr HWND_TOP=IntPtr.Zero;
    public static void mouse_event(uint f, uint x, uint y, uint d, UIntPtr extra) { Console.WriteLine("UNSAFE_MOUSE_EVENT"); }
    public static void TypeUnicode(char ch) { Console.WriteLine("UNSAFE_KEY_EVENT"); }
}`
  const blockedSource = fakeSource.replace('Owner = new IntPtr(100)', 'Owner = new IntPtr(101)')
    .replace('var previous = FakeInput.Context;',
      'if (context.ToInt64() == -1) Console.WriteLine("DPI_CONTEXT_RESTORED"); var previous = FakeInput.Context;')
  const blocked = runProbe(script.slice(0, start) + fakeWindow + script.slice(end),
    { 'BossPhysicalInput.cs': blockedSource }, ['-X', '684', '-Y', '276', '-CssW', '2504', '-CssH', '1474'])
  assert.equal(blocked.status, 2, blocked.stderr || blocked.stdout)
  assert.match(blocked.stdout, /DPI_CONTEXT_RESTORED/)
  assert.doesNotMatch(blocked.stdout, /UNSAFE_MOUSE_EVENT|UNSAFE_KEY_EVENT|已点击|已输入/)

  const restoreFailure = fakeSource.replace('public static uint Thread = 1;',
    'static FakeInput() { RefuseRestore = true; } public static uint Thread = 1;')
  const afterInput = runProbe(script.slice(0, start) + fakeWindow + script.slice(end),
    { 'BossPhysicalInput.cs': restoreFailure }, ['-X', '684', '-Y', '276', '-CssW', '2504', '-CssH', '1474'])
  assert.notEqual(afterInput.status, 0, 'post-input cleanup failure must not return success')
  assert.match(afterInput.stdout, /UNSAFE_MOUSE_EVENT/)
  assert.match(afterInput.stderr, /\[BOSS_INPUT_UNKNOWN\]/)
})

test('both Windows entry scripts compile and refuse a missing target without input', { skip: !windows }, () => {
  for (const file of ['win-click.ps1', 'cv-wheel.ps1']) {
    const script = path.join(SCRIPTS, file)
    assert.deepEqual([...fs.readFileSync(script).subarray(0, 3)], [0xef, 0xbb, 0xbf], `${file} needs UTF-8 BOM`)
    const result = spawnSync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script,
      '-X', '684', '-Y', '276', '-CssW', '2504', '-CssH', '1474', '-TitleKeyword', '__aid_boss_no_window_test_01a1293d__'],
    { encoding: 'utf8', timeout: 30000 })
    assert.equal(result.status, 1, result.stderr || result.stdout)
    assert.match(result.stderr.replace(/\s+/g, ''), /__aid_boss_no_window_test_01a1293d__/)
    assert.doesNotMatch(result.stderr, /Add-Type.*:|CompilerError|SOURCE_CODE_ERROR/)
    assert.doesNotMatch(result.stdout, /已点击|wheeled|已输入/)
  }
})
