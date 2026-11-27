import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

test('friend-add PowerShell verifies the search target and settles one click against the resulting UI',
  { skip: process.platform !== 'win32' }, () => {
    const dir = mkdtempSync(join(tmpdir(), 'weixin-friend-driver-'))
    const script = join(dir, 'test.ps1')
    const state = fileURLToPath(new URL('../../drivers/ps1/friend-add-state.ps1', import.meta.url)).replaceAll("'", "''")
    const driver = fileURLToPath(new URL('../../drivers/ps1/friend-add.ps1', import.meta.url)).replaceAll("'", "''")
    try {
      writeFileSync(script, `\ufeff
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$null = Add-Type -AssemblyName System.Drawing
# Load production functions without the real Win32 setup or driver entry point.
foreach ($path in @('${state}', '${driver}')) {
    $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errors)
    if ($errors) { throw "PowerShell driver parse failed: $errors" }
    foreach ($function in $ast.EndBlock.Statements) {
        if ($function -is [Management.Automation.Language.FunctionDefinitionAst]) {
            . ([scriptblock]::Create($function.Extent.Text))
        }
    }
}
Add-Type -TypeDefinition @'
using System;
public static class WeixinProbeWin32 {
    public struct RECT { public int Left, Top, Right, Bottom; }
    public struct POINT { public int X, Y; }
    public static int Foreground;
    public static int Moves, DriftAt, OccludedAt, FocusLostAt, ZeroInputAt, PartialInputAt;
    public static bool ButtonHeld, MenuOwnerForeground;
    public static POINT Cursor;
    public static Action<int,int,int> Click;
    public static bool SetCursorPos(int x, int y) {
        Moves++;
        Cursor = new POINT { X = x, Y = y };
        if (Moves == FocusLostAt) Foreground = 999;
        return true;
    }
    public static bool GetCursorPos(out POINT point) {
        point = Cursor;
        if (Moves == DriftAt) point.X++;
        return true;
    }
    public static short GetAsyncKeyState(int vk) { return ButtonHeld && vk == 1 ? short.MinValue : (short)0; }
    public static IntPtr WindowFromPoint(POINT point) {
        if (Moves == OccludedAt) return new IntPtr(999);
        int root = MenuOwnerForeground && Moves == 2 ? 2 : Foreground;
        return new IntPtr(root + 100);
    }
    public static IntPtr GetAncestor(IntPtr window, uint flags) { return flags == 2 ? new IntPtr(window.ToInt32() - 100) : IntPtr.Zero; }
    public static uint SendLeftClick() {
        if (Moves == ZeroInputAt) return 0;
        int root = GetAncestor(WindowFromPoint(Cursor), 2).ToInt32();
        Click(root, Cursor.X, Cursor.Y);
        return Moves == PartialInputAt ? 1u : 2u;
    }
    public static System.Collections.Generic.Dictionary<int,IntPtr> Props = new System.Collections.Generic.Dictionary<int,IntPtr>();
    public static bool SetProp(IntPtr h, string name, IntPtr value) { Props[h.ToInt32()] = value; return true; }
    public static IntPtr GetProp(IntPtr h, string name) { return Props.ContainsKey(h.ToInt32()) ? Props[h.ToInt32()] : IntPtr.Zero; }
    public static IntPtr RemoveProp(IntPtr h, string name) { IntPtr value=GetProp(h,name); Props.Remove(h.ToInt32()); return value; }
    public static System.Collections.Generic.List<int> Closed = new System.Collections.Generic.List<int>();
    public static uint GetDpiForWindow(IntPtr h) { return 96; }
    public static IntPtr GetForegroundWindow() { return new IntPtr(Foreground); }
    public static bool IsWindowVisible(IntPtr h) { return true; }
    public static bool GetWindowRect(IntPtr h, ref RECT r) { r = new RECT { Left = -500, Top = 20, Right = 100, Bottom = 720 }; return true; }
    public static bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l) {
        if (m == 0x0010) Closed.Add(h.ToInt32());
        return true;
    }
}
'@
function Throw-DriverError($Code, $Message) { throw "WXDRIVE|$Code|$Message" }
function Resolve-WeixinMainWindowHwnd { return 1 }
function Test-WeixinExecutablePath($Path) { return $Path -eq 'C:\\Fake\\Weixin.exe' }
function Get-VisibleWindowList { return $script:windows.Values }
function Get-WindowIdentityByHwnd($Hwnd) { return $script:windows[[int]$Hwnd] }
function Invoke-WeixinActivation($Hwnd) { [WeixinProbeWin32]::Foreground = $Hwnd; return $true }
function Start-Sleep {
    param($Milliseconds, $Seconds)
    while ($script:closeRead -lt [WeixinProbeWin32]::Closed.Count) {
        $handle = [WeixinProbeWin32]::Closed[$script:closeRead]
        $script:closeRead++
        if (-not $script:windows.ContainsKey($handle) -or $script:scenario -eq 'close-blocked') { continue }
        if ($script:scenario -eq 'close-retry' -and
            @([WeixinProbeWin32]::Closed | Where-Object { $_ -eq $handle }).Count -lt 2) { continue }
        $script:windows.Remove($handle)
        [void][WeixinProbeWin32]::Props.Remove($handle)
        if ([WeixinProbeWin32]::Foreground -eq $handle) { [WeixinProbeWin32]::Foreground = 1 }
    }
}
function Get-WeixinFriendStatePath([string]$Name) {
    return (Join-Path (Join-Path $PSScriptRoot 'fixture-state') "$Name.json")
}
function Get-WeixinFriendLayoutKey($Window, $Frame) { return "fixture|$($Window.Hwnd)|$($Frame.Bounds.Width)" }
function Get-WeixinFriendControlHash($Frame, $Point) {
    return ([Convert]::ToBase64String($Frame.Image) + "/$($Point.x)/$($Point.y)")
}
function Open-FixtureWindow($Hwnd, $Title, $ClassName='Qt51514QWindowIcon') {
    $script:windows[$Hwnd] = [pscustomobject]@{ Hwnd=$Hwnd; ProcessId=42; Title=$Title; ClassName=$ClassName; ProcessPath='C:\\Fake\\Weixin.exe' }
    [WeixinProbeWin32]::Foreground = $Hwnd
}
function Get-WeixinWindowSnapshot($Hwnd, $Crop) {
    $rect = New-Object WeixinProbeWin32+RECT
    $null = [WeixinProbeWin32]::GetWindowRect([IntPtr]$Hwnd, [ref]$rect)
    $bounds = if ($Crop.IsEmpty) { New-Object Drawing.Rectangle 0, 0, 600, 700 } else { $Crop }
    $content = 0
    if ($Hwnd -eq 3) {
        $content = $script:searchContent
        if ($script:changedCaret) {
            $content = 2
            $script:changedCaret = $false
            $script:caretDifferences++
        }
    }
    if ($Hwnd -eq 4) { $content = $script:formContent }
    return @{ Image=[byte[]]@($Hwnd, $content); Bounds=$bounds; WindowRect=$rect }
}
[WeixinProbeWin32]::Click = [Action[int,int,int]]{
    param($Hwnd, $ScreenX, $ScreenY)
    $script:clicks.Add("$Hwnd/$ScreenX/$ScreenY")
    if ($Hwnd -eq 1 -and $script:scenario -eq 'cache-plus-miss' -and $ScreenX -eq -200) { return }
    if ($Hwnd -eq 1) {
        Open-FixtureWindow 2 '微信' 'Qt51514QWindowToolSaveBits'
        if ([WeixinProbeWin32]::MenuOwnerForeground) { [WeixinProbeWin32]::Foreground = 1 }
    }
    if ($Hwnd -eq 2 -and $script:scenario -eq 'cache-menu-miss' -and $ScreenX -eq -440) { return }
    if ($Hwnd -eq 2) { $script:windows.Remove(2); [void][WeixinProbeWin32]::Props.Remove(2); Open-FixtureWindow 3 '添加朋友' }
    if ($Hwnd -eq 3 -and $ScreenY -eq 320 -and $script:scenario -ne 'direct-add') { Open-FixtureWindow 4 '申请添加朋友' }
    if ($Hwnd -eq 4 -and $ScreenY -eq 620 -and $script:scenario -eq 'form-remains') {
        [WeixinProbeWin32]::Foreground = 999
    }
    if ($Hwnd -eq 4 -and $ScreenY -eq 620 -and $script:scenario -ne 'form-remains') {
        $script:windows.Remove(4); [void][WeixinProbeWin32]::Props.Remove(4); [WeixinProbeWin32]::Foreground = 3
    }
}
function Send-WeixinKeyChord($Keys, $ForegroundGuard) {
    if (-not (& $ForegroundGuard)) { throw 'unsafe foreground' }
    $script:inputs.Add(($Keys -join '+'))
}
function Send-WeixinPostMessageText($Hwnd, $Text) { $script:inputs.Add("$Hwnd/$Text") }
function Send-WeixinPostMessageKey($Hwnd, $Vk, $ScanCode) { $script:inputs.Add("$Hwnd/Enter") }
function Invoke-KimiVision { param($ImageBytes, $SystemPrompt, $UserText, $ArtifactPrefix, [switch]$ProxyOnly, $RuntimeFriendStep)
    if (-not $ProxyOnly) { throw 'Request must use billing proxy' }
    $script:visualSteps.Add($RuntimeFriendStep)
    switch ($ImageBytes[0]) {
        1 {
            $x = if ($script:scenario -eq 'cache-plus-miss') { 301 } else { 300 }
            return @{ plus=@{x=$x;y=30} }
        }
        2 {
            $x = if ($script:scenario -eq 'cache-menu-miss') { 61 } else { 60 }
            return @{ item=@{x=$x;y=80} }
        }
        4 {
            if ($script:scenario -eq 'form-changed') { $script:formContent = 1 }
            return @{ verify=@{x=120;y=100}; confirm=@{x=140;y=600} }
        }
        3 {
            if ($RuntimeFriendStep -eq 'search_input') {
                $x = if ($script:scenario -eq 'cache-input-miss') { 121 } else { 120 }
                return @{ input=@{x=$x;y=80} }
            }
            $script:searchFrames++
            if ($script:scenario -eq 'cache-input-miss' -and $script:searchFrames -eq 1) {
                return @{ query='stale_field'; state='unknown' }
            }
            if ($script:scenario -eq 'missing') { return @{ query='wx_test'; state='missing' } }
            if ($script:scenario -eq 'different-query') { return @{ query='another_account'; state='add' } }
            if ($script:scenario -eq 'already-friend') { return @{ query='wx_test'; state='friend' } }
            # Return the captured result even if the live window changes during model inference.
            if ($script:searchFrames -eq 1 -or ($script:scenario -eq 'cache-input-miss' -and $script:searchFrames -eq 2)) {
                if ($script:scenario -eq 'search-changed') { $script:searchContent = 1 }
                if ($script:scenario -eq 'caret-blinks') { $script:changedCaret = $true }
                return @{ query='wx_test'; state='add'; add=@{x=120;y=300} }
            }
            if ($script:scenario -eq 'direct-add') { return @{ query='wx_test'; state='friend' } }
            if ($script:scenario -eq 'closed-unverified') { return @{ query='wx_test'; state='add' } }
            if ($script:scenario -eq 'settlement-changed') { $script:searchContent = 1 }
            return @{ query='wx_test'; state='pending' }
        }
    }
}
$env:AID_WEIXIN_SERVER_URL = 'https://example.invalid'
$env:AID_WEIXIN_ACCESS_TOKEN = 'fixture'
$env:AID_WEIXIN_PROXY_ERROR = ''
function Run-Fixture([string]$scenario, [switch]$ReuseDesktop) {
    $script:scenario = $scenario
    $script:searchFrames = 0
    $script:searchContent = 0
    $script:formContent = 0
    $script:changedCaret = $false
    $script:caretDifferences = 0
    $script:clicks = [Collections.Generic.List[string]]::new()
    $script:inputs = [Collections.Generic.List[string]]::new()
    $script:visualSteps = [Collections.Generic.List[string]]::new()
    [WeixinProbeWin32]::Closed.Clear()
    [WeixinProbeWin32]::Moves = 0
    [WeixinProbeWin32]::DriftAt = if ($scenario -eq 'cursor-drift') { 1 } else { 0 }
    [WeixinProbeWin32]::OccludedAt = if ($scenario -eq 'window-covered') { 4 } else { 0 }
    [WeixinProbeWin32]::FocusLostAt = if ($scenario -eq 'focus-stolen') { 4 } else { 0 }
    [WeixinProbeWin32]::ZeroInputAt = if ($scenario -eq 'input-blocked') { 6 } else { 0 }
    [WeixinProbeWin32]::PartialInputAt = if ($scenario -eq 'input-partial') { 6 } else { 0 }
    [WeixinProbeWin32]::ButtonHeld = $scenario -eq 'button-held'
    [WeixinProbeWin32]::MenuOwnerForeground = $scenario -eq 'menu-owner-foreground'
    $script:closeRead = 0
    if (-not $ReuseDesktop) {
        [IO.Directory]::CreateDirectory((Join-Path $PSScriptRoot 'fixture-state')) | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'fixture-state') -File | ForEach-Object { [IO.File]::Delete($_.FullName) }
        $script:windows = @{}
        [WeixinProbeWin32]::Props.Clear()
        Open-FixtureWindow 1 '微信'
    }
    $result = $null
    $failure = $null
    $message = if ($scenario -notin @('search-only', 'default-message')) { '你好🤝' }
    try { $result = Invoke-WeixinFriendAdd 'wx_test' $message -SearchOnly:($scenario -eq 'search-only') }
    catch { $failure = $_.Exception.Message }
    @{
        scenario=$scenario; status=$result.status; failure=$failure; foreground=[WeixinProbeWin32]::Foreground
        moves=[WeixinProbeWin32]::Moves; clicks=$script:clicks.ToArray(); inputs=$script:inputs.ToArray(); caretDifferences=$script:caretDifferences
        closed=[WeixinProbeWin32]::Closed.ToArray(); visualSteps=$script:visualSteps.ToArray()
        ownedCount=@((Read-WeixinFriendState 'owned-windows') | Where-Object {$_}).Count
    }
}
$rows = foreach ($scenario in @(
    'confirmed', 'closed-unverified', 'form-remains', 'different-query', 'already-friend', 'direct-add',
    'search-changed', 'form-changed', 'settlement-changed', 'caret-blinks', 'default-message', 'search-only'
)) {
    Run-Fixture $scenario
}
$rows += Run-Fixture 'confirmed'
$rows += Run-Fixture 'warm' -ReuseDesktop
$rows += Run-Fixture 'cache-plus-miss' -ReuseDesktop
$rows += Run-Fixture 'cache-menu-miss' -ReuseDesktop
$rows += Run-Fixture 'cache-input-miss' -ReuseDesktop
$rows += Run-Fixture 'form-remains' -ReuseDesktop
$rows += Run-Fixture 'missing' -ReuseDesktop
$rows += Run-Fixture 'close-retry'
$rows += Run-Fixture 'close-blocked'
$rows += Run-Fixture 'recovered' -ReuseDesktop
$rows += Run-Fixture 'close-blocked'
# Closing and reopening manually reuses the mock handle but not the native property.
$script:windows.Remove(3)
[void][WeixinProbeWin32]::Props.Remove(3)
Open-FixtureWindow 3 '添加朋友'
$rows += Run-Fixture 'manual-reused' -ReuseDesktop
foreach ($scenario in @('cursor-drift', 'window-covered', 'focus-stolen', 'input-blocked', 'input-partial', 'button-held', 'menu-owner-foreground')) {
    $rows += Run-Fixture $scenario
}
ConvertTo-Json -InputObject @($rows) -Depth 4 -Compress
`, 'utf8')
      const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', script],
        { encoding: 'utf8', timeout: 30_000, windowsHide: true })
      assert.equal(result.status, 0, result.stderr)
      const rows = JSON.parse(result.stdout.trim().split(/\r?\n/).at(-1)!) as Array<{
        scenario: string; status?: string; failure: string; clicks: string[]; inputs: string[]; caretDifferences: number
        foreground: number; closed: number[]; ownedCount: number; visualSteps: string[]; moves: number
      }>
      assert.equal(rows.length, 31)
      const searchClicks = ['1/-200/50', '2/-440/100', '3/-380/100']
      const searchInputs = ['CTRL+A', 'DEL', '3/wx_test', '3/Enter']
      for (const row of rows.slice(0, 12)) {
        const confirmation = row.clicks.filter((event) => event === '4/-360/620')
        if (row.scenario === 'search-only') {
          assert.deepEqual(row.clicks, searchClicks)
          assert.equal(row.moves, searchClicks.length)
          assert.deepEqual(row.inputs, searchInputs)
          assert.equal(row.status, 'addable')
          assert.equal(row.foreground, 3)
          assert.deepEqual(row.closed, [])
          continue
        }
        if (row.scenario === 'direct-add') {
          assert.deepEqual(row.clicks, [...searchClicks, '3/-380/320'])
          assert.deepEqual(row.inputs, searchInputs)
          assert.equal(row.status, 'friend_added')
          continue
        }
        if (['different-query', 'already-friend', 'search-changed'].includes(row.scenario)) {
          assert.deepEqual(row.clicks, searchClicks, row.scenario)
          if (row.scenario === 'already-friend') assert.equal(row.status, 'already_friend')
          else assert.match(row.failure, /WXDRIVE\|UI_CHANGED/)
          continue
        }
        if (row.scenario === 'form-changed') {
          assert.deepEqual(row.clicks, [...searchClicks, '3/-380/320'])
          assert.deepEqual(row.inputs, searchInputs)
          assert.match(row.failure, /WXDRIVE\|UI_CHANGED/)
          continue
        }
        assert.equal(confirmation.length, 1, row.scenario)
        assert.deepEqual(row.inputs, row.scenario === 'default-message'
          ? searchInputs : [...searchInputs, 'CTRL+A', 'DEL', '4/你好🤝'])
        if (row.scenario === 'caret-blinks') assert.equal(row.caretDifferences, 1)
        if (['confirmed', 'caret-blinks', 'default-message'].includes(row.scenario)) assert.equal(row.status, 'request_sent')
        else assert.match(row.failure, /WXDRIVE\|EXECUTION_UNKNOWN/, row.scenario)
      }

      const [cold, warm, refreshed, menuRefreshed, inputRefreshed, unknown, missing, closeRetry, blocked, recovered, , manualReused] = rows.slice(12)
      assert.deepEqual(cold!.visualSteps, ['plus', 'menu', 'search_input', 'search_result', 'verify_form', 'search_result'])
      assert.deepEqual(warm!.visualSteps, ['search_result', 'verify_form', 'search_result'])
      assert.equal(warm!.status, 'request_sent')
      assert.equal(warm!.ownedCount, 0)
      assert.deepEqual(refreshed!.visualSteps, ['plus', 'search_result', 'verify_form', 'search_result'])
      assert.deepEqual(refreshed!.clicks.slice(0, 2), ['1/-200/50', '1/-199/50'])
      assert.deepEqual(menuRefreshed!.visualSteps, ['menu', 'search_result', 'verify_form', 'search_result'])
      assert.equal(menuRefreshed!.status, 'request_sent')
      assert.deepEqual(inputRefreshed!.visualSteps, ['search_result', 'search_input', 'search_result', 'verify_form', 'search_result'])
      assert.equal(inputRefreshed!.clicks.filter(click => click === '4/-360/620').length, 1)
      assert.equal(inputRefreshed!.status, 'request_sent')
      assert.match(unknown!.failure, /WXDRIVE\|EXECUTION_UNKNOWN/)
      assert.deepEqual(unknown!.closed, [4, 3])
      assert.match(missing!.failure, /WXDRIVE\|TARGET_NOT_FOUND/)
      assert.deepEqual(missing!.closed, [3])
      assert.equal(closeRetry!.status, 'request_sent')
      assert.deepEqual(closeRetry!.closed, [3, 3])
      assert.equal(blocked!.status, 'request_sent')
      assert.equal(blocked!.ownedCount, 1)
      assert.equal(recovered!.status, 'request_sent')
      assert.equal(recovered!.ownedCount, 0)
      assert.deepEqual(recovered!.closed, [3, 3])
      assert.match(manualReused!.failure, /WXDRIVE\|UI_CHANGED/)
      assert.deepEqual(manualReused!.closed, [])
      assert.deepEqual(manualReused!.clicks, [])

      const [cursorDrift, windowCovered, focusStolen, inputBlocked, inputPartial, buttonHeld, menuOwnerForeground] = rows.slice(24)
      assert.equal(cursorDrift!.moves, 1)
      assert.deepEqual(cursorDrift!.clicks, [])
      assert.match(cursorDrift!.failure, /WXDRIVE\|UI_CHANGED/)
      assert.deepEqual(windowCovered!.clicks, searchClicks)
      assert.deepEqual(windowCovered!.closed, [3])
      assert.match(windowCovered!.failure, /WXDRIVE\|UI_CHANGED/)
      assert.deepEqual(focusStolen!.clicks, searchClicks)
      assert.deepEqual(focusStolen!.closed, [3])
      assert.match(focusStolen!.failure, /WXDRIVE\|FOREGROUND_LOST/)
      assert.deepEqual(inputBlocked!.clicks, [...searchClicks, '3/-380/320', '4/-380/120'])
      assert.deepEqual(inputBlocked!.closed, [4, 3])
      assert.match(inputBlocked!.failure, /WXDRIVE\|UI_CHANGED/)
      assert.deepEqual(inputPartial!.clicks, [...inputBlocked!.clicks, '4/-360/620'])
      assert.match(inputPartial!.failure, /WXDRIVE\|EXECUTION_UNKNOWN/)
      assert.equal(buttonHeld!.moves, 0)
      assert.deepEqual(buttonHeld!.clicks, [])
      assert.match(buttonHeld!.failure, /WXDRIVE\|UI_CHANGED/)
      assert.deepEqual(menuOwnerForeground!.clicks.slice(0, 3), searchClicks)
      assert.equal(menuOwnerForeground!.status, 'request_sent')
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
