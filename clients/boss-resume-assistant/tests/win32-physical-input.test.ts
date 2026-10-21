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

// 2026-10-21：win-click.ps1 / cv-wheel.ps1 已删（native 输入全量替代，守卫由共享包
// win32-native-input 的测试覆盖）。BossPhysicalInput.cs 仍随 window-experiment.ps1 出厂，
// 本文件继续回归它的 DPI 作用域/光标守卫/输入已开始语义（fake native 边界）。
test('physical input: production DPI scope and guards refuse unsafe native results', { skip: !windows }, () => {
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
  const probe = runProbe(`$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
${fakeSource}
'@
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
})

test('the remaining Windows experiment entry script compiles and refuses a missing debug target without input', { skip: !windows }, () => {
  const file = path.join(SCRIPTS, 'window-experiment.ps1')
  assert.deepEqual([...fs.readFileSync(file).subarray(0, 3)], [0xef, 0xbb, 0xbf], 'window-experiment.ps1 needs UTF-8 BOM')
  const result = spawnSync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', file,
    '-Action', 'capture', '-CdpPort', '49999'],
    { encoding: 'utf8', timeout: 30000 })
  assert.equal(result.status, 1, result.stderr || result.stdout)
  assert.match(result.stderr.replace(/\s+/g, ''), /debugChromebrowserprocess/)
  assert.doesNotMatch(result.stderr, /Add-Type.*:|CompilerError|SOURCE_CODE_ERROR/)
  assert.doesNotMatch(result.stdout, /captured|已点击|已输入/)
})
