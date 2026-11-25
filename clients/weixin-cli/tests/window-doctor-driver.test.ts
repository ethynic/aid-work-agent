import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const driver = fileURLToPath(new URL('../../drivers/ps1/window-doctor.ps1', import.meta.url))
const win32 = fileURLToPath(new URL('../../drivers/ps1/win32-lib.ps1', import.meta.url))
const common = fileURLToPath(new URL('../../drivers/ps1/_common.ps1', import.meta.url))
const load = `
$ErrorActionPreference='Stop'
[void](Add-Type -AssemblyName UIAutomationClient)
[void](Add-Type -AssemblyName UIAutomationTypes)
Add-Type @'
using System;
public static class WeixinProbeWin32 {
 public static bool Visible=true, Iconic=false;
 public static IntPtr Foreground=(IntPtr)1;
 public static bool IsWindowVisible(IntPtr h) { return Visible; }
 public static bool IsIconic(IntPtr h) { return Iconic; }
 public static IntPtr GetForegroundWindow() { return Foreground; }
}
'@
$ast=[Management.Automation.Language.Parser]::ParseFile('${driver.replaceAll("'", "''")}',[ref]$null,[ref]$null)
foreach ($definition in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
 . ([ScriptBlock]::Create($definition.Extent.Text))
}
function Throw-DriverError($Code,$Message) { throw ('WXDRIVE|'+$Code+'|'+$Message) }
function Start-Sleep($Milliseconds) {}
$script:window=@{Hwnd=1;ProcessId=7}
function Get-WeixinDiagnosticWindows {
 $script:windowReads++
 if ($script:mode -eq 'alreadyRestored' -and $script:windowReads -gt 2) { [WeixinProbeWin32]::Visible=$true }
 if ($script:mode -eq 'identityChanged' -and $script:windowReads -gt 2) { return @(@{Hwnd=2;ProcessId=8}) }
 if ($script:mode -eq 'noMain') { return @() }
 if ($script:mode -eq 'ambiguous') { return @($script:window,@{Hwnd=2;ProcessId=8}) }
 return @($script:window)
}
function Get-WeixinNativeList($Session,$Id,$Class) { if (-not $script:loggedIn) { throw 'no session list' }; return @{} }
function Invoke-WeixinActivation($Hwnd,[switch]$PreservePlacement) {
 if (-not $PreservePlacement) { throw 'placement must be preserved' }
 $script:activation++;[WeixinProbeWin32]::Visible=$true;[WeixinProbeWin32]::Iconic=$false
 [WeixinProbeWin32]::Foreground=[IntPtr]1
 return $true
}
function Button($name,$id,$role,[int]$owner=42,[bool]$offscreen=$false) {
 $pattern=[pscustomobject]@{Role=$role}
 $pattern | Add-Member ScriptMethod Invoke {
   $script:actions+= $this.Role
   if ($this.Role -eq 'expand') { $script:overflow=$true }
   if ($this.Role -eq 'wechat') { [WeixinProbeWin32]::Visible=$true }
 }
 $node=[pscustomobject]@{Current=@{Name=$name;AutomationId=$id;ClassName='SystemTray.NormalButton';ProcessId=$owner;IsOffscreen=$offscreen};Pattern=$pattern}
 $node | Add-Member ScriptMethod TryGetCurrentPattern {param($kind,$out) $out.Value=$this.Pattern;return $true}
 return $node
}
function Get-WeixinTrayShellRoots {
 $buttons=@((Button 'browser' 'NotifyItemIcon' 'wrong'),(Button '微信' 'wrong-id' 'wrong'),
   (Button '微信' 'NotifyItemIcon' 'wrong' 99),(Button '微信' 'NotifyItemIcon' 'wrong' 42 $true))
 if ($script:overflow) {
   $buttons+=Button '微信' 'NotifyItemIcon' 'wechat'
   if ($script:mode -eq 'duplicateIcon') { $buttons+=Button 'WeChat' 'NotifyItemIcon' 'wechat' }
 } else { $buttons+=Button '显示隐藏的图标' 'SystemTrayIcon' 'expand' }
 $root=[pscustomobject]@{Buttons=$buttons}
 $root | Add-Member ScriptMethod FindAll {param($scope,$condition) return $this.Buttons}
 return @{Root=$root;ProcessId=42}
}
`
function run(script: string) {
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')],
    { encoding: 'utf8', timeout: 20_000, windowsHide: true })
  assert.equal(result.status, 0, result.stderr)
  return JSON.parse(result.stdout)
}

test('native diagnosis is passive and restoration invokes only the exact tray icon before checking the same main window', { skip: process.platform !== 'win32' }, () => {
  const result = run(load + `
$out=@{}
foreach ($mode in @('foreground','background','minimized','hidden','noMain','ambiguous','duplicateIcon','loginUnknown','alreadyRestored','identityChanged')) {
 $script:mode=$mode;$script:loggedIn=$mode -ne 'loginUnknown';$script:actions=@();$script:activation=0;$script:overflow=$false;$script:windowReads=0
 [WeixinProbeWin32]::Visible=$mode -notin @('hidden','duplicateIcon','alreadyRestored')
 [WeixinProbeWin32]::Iconic=$mode -eq 'minimized'
 [WeixinProbeWin32]::Foreground=if ($mode -eq 'foreground') {[IntPtr]1} else {[IntPtr]99}
 $passive=Invoke-WeixinWindowDoctor
 if ($script:actions.Count -or $script:activation) { throw 'passive diagnosis changed the desktop' }
 $restored=Invoke-WeixinWindowDoctor -Restore
 $out[$mode]=@{before=$passive;after=$restored;actions=$script:actions;activation=$script:activation}
}
ConvertTo-Json -Depth 8 -Compress $out
`)
  for (const state of ['foreground', 'background', 'minimized', 'hidden']) {
    assert.equal(result[state].before.window_state, state)
    assert.equal(result[state].before.login_state, 'online')
    assert.equal(result[state].after.restored, true)
    assert.equal(result[state].after.window_state, 'foreground')
  }
  assert.deepEqual(result.hidden.actions, ['expand', 'wechat'])
  assert.deepEqual(result.background.actions, [])
  assert.equal(result.noMain.after.error_code, 'WEIXIN_NOT_FOUND')
  assert.equal(result.ambiguous.after.error_code, 'WINDOW_AMBIGUOUS')
  assert.equal(result.duplicateIcon.after.restored, false)
  assert.deepEqual(result.duplicateIcon.actions, ['expand'])
  assert.equal(result.loginUnknown.after.login_state, 'unknown')
  assert.equal(result.loginUnknown.after.restored, false)
  assert.equal(result.alreadyRestored.after.restored, true)
  assert.deepEqual(result.alreadyRestored.actions, [], 'a window restored by the user must never be toggled through its tray icon')
  assert.equal(result.identityChanged.after.error_code, 'UI_CHANGED')
  assert.deepEqual(result.identityChanged.actions, [])
})

test('the updates popup is excluded by both window selectors only when UIA confirms its process and class', { skip: process.platform !== 'win32' }, () => {
  const result = run(`
$ErrorActionPreference='Stop'
Add-Type @'
using System;
namespace Windows.Automation {
 public class AutomationElement {
   public int ProcessId { get; set; }
   public string ClassName { get; set; }
   public AutomationElement Current { get { return this; } }
   public static AutomationElement FromHandle(IntPtr h) {
     int id = h.ToInt32();
     if (id == 3) throw new InvalidOperationException();
     return new AutomationElement {
       ProcessId = id == 4 ? 8 : 7,
       ClassName = id == 2 || id == 4 ? "mmui::WhatsNewWindow" : "mmui::MainWindow"
     };
   }
 }
}
'@
function Load-Function($Path,$Name) {
    $ast=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$null,[ref]$null)
    $definition=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $Name},$false)
    return [ScriptBlock]::Create($definition.Extent.Text)
}
. (Load-Function '${common.replaceAll("'", "''")}' 'Test-WeixinWhatsNewWindow')
. (Load-Function '${common.replaceAll("'", "''")}' 'Resolve-WeixinMainWindowHwnd')
. (Load-Function '${driver.replaceAll("'", "''")}' 'Get-WeixinDiagnosticWindows')
. (Load-Function '${win32.replaceAll("'", "''")}' 'Test-WeixinMainIdentity')
$script:WeixinTitle='fixture'
function Get-Process($Id,$Name,$ErrorAction) { return @{Id=7;SessionId=1} }
function Get-VisibleWindowList([switch]$IncludeHidden,$ProcessIds) {
    foreach ($h in $script:handles) { @{Hwnd=$h;ProcessPath='fixture.exe'} }
}
function Get-WindowIdentityByHwnd($h) { return [pscustomobject]@{
    Hwnd=$h;ProcessId=7;ProcessPath='fixture.exe';ClassName='Qt51514QWindowIcon';Title='fixture'
} }
function Test-WeixinExecutablePath($Path) { return $Path -eq 'fixture.exe' }
function Throw-DriverError($Code,$Message) { throw $Code }
function Case([long[]]$Ids) {
    $script:handles=$Ids
    $resolved=try { Resolve-WeixinMainWindowHwnd } catch { $_.Exception.Message }
    return @{resolved=$resolved;diagnostic=@(Get-WeixinDiagnosticWindows | ForEach-Object {$_.Hwnd})}
}
ConvertTo-Json -Compress @{
    popup=Case @(2,1)
    popupOnly=Case @(2)
    secondMain=Case @(1,5)
    unavailableUia=Case @(3)
    foreignProvider=Case @(1,4)
}
`)
  assert.deepEqual(result, {
    popup: { resolved: 1, diagnostic: [1] },
    popupOnly: { resolved: 'WEIXIN_NOT_FOUND', diagnostic: [] },
    secondMain: { resolved: 'WINDOW_AMBIGUOUS', diagnostic: [1, 5] },
    unavailableUia: { resolved: 3, diagnostic: [3] },
    foreignProvider: { resolved: 'WINDOW_AMBIGUOUS', diagnostic: [1, 4] },
  })
})

test('explicit restoration preserves an existing maximized placement and restores an iconic window', { skip: process.platform !== 'win32' }, () => {
  const result = run(`
$ErrorActionPreference='Stop'
Add-Type @'
using System;
public static class WeixinProbeWin32 {
 public static bool Iconic=false; public static int Shows=0;
 public static bool IsIconic(IntPtr h) { return Iconic; }
 public static bool ShowWindow(IntPtr h,int command) { Shows++;return true; }
 public static uint GetCurrentThreadId() { return 1; }
 public static uint GetWindowThreadProcessId(IntPtr h,out uint p) { p=7;return 1; }
 public static IntPtr GetForegroundWindow() { return (IntPtr)1; }
 public static bool AttachThreadInput(uint a,uint b,bool v) { return true; }
 public static bool BringWindowToTop(IntPtr h) { return true; }
 public static IntPtr SetActiveWindow(IntPtr h) { return h; }
 public static bool SetForegroundWindow(IntPtr h) { return true; }
}
'@
function Start-Sleep($Milliseconds) {}
$ast=[Management.Automation.Language.Parser]::ParseFile('${win32.replaceAll("'", "''")}',[ref]$null,[ref]$null)
$definition=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-WeixinActivation'},$true)
. ([ScriptBlock]::Create($definition.Extent.Text))
$null=Invoke-WeixinActivation -Hwnd 1 -PreservePlacement
$preserved=[WeixinProbeWin32]::Shows
[WeixinProbeWin32]::Iconic=$true
$null=Invoke-WeixinActivation -Hwnd 1 -PreservePlacement
ConvertTo-Json -Compress @{preserved=$preserved;restored=[WeixinProbeWin32]::Shows}
`)
  assert.deepEqual(result, { preserved: 0, restored: 1 })
})

test('window enumeration filters owner PIDs before process queries while preserving the visible-only default', { skip: process.platform !== 'win32' }, () => {
  const result = run(`
$ErrorActionPreference='Stop'
Add-Type @'
using System;
public static class WeixinProbeWin32 {
 public delegate bool EnumWindowsProc(IntPtr h,IntPtr p);
 public static bool EnumWindows(EnumWindowsProc cb,IntPtr p) { for(int i=1;i<=3;i++) cb((IntPtr)i,p);return true; }
 public static bool IsWindowVisible(IntPtr h) { return (int)h!=2; }
 public static uint GetWindowThreadProcessId(IntPtr h,out uint p) { p=(uint)((int)h==3?999:123);return 1; }
}
'@
$ast=[Management.Automation.Language.Parser]::ParseFile('${win32.replaceAll("'", "''")}',[ref]$null,[ref]$null)
$definition=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-VisibleWindowList'},$true)
. ([ScriptBlock]::Create($definition.Extent.Text))
function Get-Process($Id,$ErrorAction) { $script:queries++;return @{MainWindowHandle=[IntPtr]1;Path='fixture.exe'} }
$script:queries=0
$filtered=@(Get-VisibleWindowList -IncludeHidden -ProcessIds @(123))
$filteredQueries=$script:queries
$script:queries=0
$visible=@(Get-VisibleWindowList)
ConvertTo-Json -Compress @{filtered=@($filtered | ForEach-Object {$_.Hwnd});filteredQueries=$filteredQueries;visible=@($visible | ForEach-Object {$_.Hwnd});queries=$script:queries}
`)
  assert.deepEqual(result, { filtered: [1, 2], filteredQueries: 1, visible: [1, 3], queries: 2 })
})
