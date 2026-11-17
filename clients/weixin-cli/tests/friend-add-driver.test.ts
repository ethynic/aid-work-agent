import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, rmdirSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

test('friend-add PowerShell verifies the search target and settles one click against the resulting UI',
  { skip: process.platform !== 'win32' }, () => {
    const dir = mkdtempSync(join(tmpdir(), 'weixin-friend-driver-'))
    const script = join(dir, 'test.ps1')
    const driver = fileURLToPath(new URL('../../drivers/ps1/friend-add.ps1', import.meta.url)).replaceAll("'", "''")
    try {
      writeFileSync(script, `\ufeff
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$null = Add-Type -AssemblyName System.Drawing
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile('${driver}', [ref]$null, [ref]$errors)
if ($errors) { throw 'PowerShell driver parse failed' }
# Load production functions without the real Win32 setup or driver entry point.
foreach ($function in $ast.EndBlock.Statements) {
    if ($function -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($function.Extent.Text))
    }
}
Add-Type -TypeDefinition @'
using System;
public static class WeixinProbeWin32 {
    public struct RECT { public int Left, Top, Right, Bottom; }
    public static int Foreground;
    public static uint GetDpiForWindow(IntPtr h) { return 96; }
    public static IntPtr GetForegroundWindow() { return new IntPtr(Foreground); }
    public static bool IsWindowVisible(IntPtr h) { return true; }
    public static bool GetWindowRect(IntPtr h, ref RECT r) { r = new RECT { Left = -500, Top = 20, Right = 100, Bottom = 720 }; return true; }
    public static bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l) { return true; }
}
'@
function Throw-DriverError($Code, $Message) { throw "WXDRIVE|$Code|$Message" }
function Resolve-WeixinMainWindowHwnd { return 1 }
function Test-WeixinExecutablePath($Path) { return $Path -eq 'C:\\Fake\\Weixin.exe' }
function Get-VisibleWindowList { return $script:windows.Values }
function Get-WindowIdentityByHwnd($Hwnd) { return $script:windows[[int]$Hwnd] }
function Invoke-WeixinActivation($Hwnd) { [WeixinProbeWin32]::Foreground = $Hwnd; return $true }
function Start-Sleep { param($Milliseconds, $Seconds) }
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
function Send-WeixinPostMessageClick($Hwnd, $ScreenX, $ScreenY) {
    $script:clicks.Add("$Hwnd/$ScreenX/$ScreenY")
    if ($Hwnd -eq 1) { Open-FixtureWindow 2 '微信' 'Qt51514QWindowToolSaveBits' }
    if ($Hwnd -eq 2) { $script:windows.Remove(2); Open-FixtureWindow 3 '添加朋友' }
    if ($Hwnd -eq 3 -and $ScreenY -eq 320 -and $script:scenario -ne 'direct-add') { Open-FixtureWindow 4 '申请添加朋友' }
    if ($Hwnd -eq 4 -and $ScreenY -eq 620 -and $script:scenario -ne 'form-remains') {
        $script:windows.Remove(4); [WeixinProbeWin32]::Foreground = 3
    }
}
function Send-WeixinKeyChord($Keys, $ForegroundGuard) {
    if (-not (& $ForegroundGuard)) { throw 'unsafe foreground' }
    $script:inputs.Add(($Keys -join '+'))
}
function Send-WeixinPostMessageText($Hwnd, $Text) { $script:inputs.Add("$Hwnd/$Text") }
function Send-WeixinPostMessageKey($Hwnd, $Vk, $ScanCode) { $script:inputs.Add("$Hwnd/Enter") }
function Invoke-KimiVision { param($ImageBytes, $SystemPrompt, $UserText, $ArtifactPrefix, [switch]$ProxyOnly)
    if (-not $ProxyOnly) { throw 'Request must use billing proxy' }
    switch ($ImageBytes[0]) {
        1 { return @{ plus=@{x=300;y=30} } }
        2 { return @{ item=@{x=60;y=80} } }
        4 {
            if ($script:scenario -eq 'form-changed') { $script:formContent = 1 }
            return @{ verify=@{x=120;y=100}; confirm=@{x=140;y=600} }
        }
        3 {
            $script:searchFrames++
            if ($script:searchFrames -eq 1) { return @{ input=@{x=120;y=80} } }
            if ($script:scenario -eq 'different-query') { return @{ query='another_account'; state='add' } }
            if ($script:scenario -eq 'already-friend') { return @{ query='wx_test'; state='friend' } }
            # Return the captured result even if the live window changes during model inference.
            if ($script:searchFrames -eq 2) {
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
$rows = foreach ($scenario in @(
    'confirmed', 'closed-unverified', 'form-remains', 'different-query', 'already-friend', 'direct-add',
    'search-changed', 'form-changed', 'settlement-changed', 'caret-blinks', 'default-message'
)) {
    $script:scenario = $scenario
    $script:searchFrames = 0
    $script:searchContent = 0
    $script:formContent = 0
    $script:changedCaret = $false
    $script:caretDifferences = 0
    $script:windows = @{}
    $script:clicks = [Collections.Generic.List[string]]::new()
    $script:inputs = [Collections.Generic.List[string]]::new()
    Open-FixtureWindow 1 '微信'
    $result = $null
    $failure = $null
    $message = if ($scenario -ne 'default-message') { '你好🤝' }
    try { $result = Invoke-WeixinFriendAdd 'wx_test' $message }
    catch { $failure = $_.Exception.Message }
    @{
        scenario=$scenario; status=$result.status; failure=$failure
        clicks=$script:clicks.ToArray(); inputs=$script:inputs.ToArray(); caretDifferences=$script:caretDifferences
    }
}
ConvertTo-Json -InputObject @($rows) -Depth 4 -Compress
`, 'utf8')
      const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', script],
        { encoding: 'utf8', timeout: 30_000, windowsHide: true })
      assert.equal(result.status, 0, result.stderr)
      const rows = JSON.parse(result.stdout) as Array<{
        scenario: string; status?: string; failure: string; clicks: string[]; inputs: string[]; caretDifferences: number
      }>
      assert.equal(rows.length, 11)
      const searchClicks = ['1/-200/50', '2/-440/100', '3/-380/100']
      const searchInputs = ['CTRL+A', 'DEL', '3/wx_test', '3/Enter']
      for (const row of rows) {
        const confirmation = row.clicks.filter((event) => event === '4/-360/620')
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
    } finally {
      unlinkSync(script)
      rmdirSync(dir)
    }
  })
