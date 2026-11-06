param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference='Stop'
. (Join-Path $DriverDir '_send-points.ps1')
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class WeComWin32 {
    public static uint GetWindowThreadProcessId(IntPtr hwnd, ref uint pid) { pid=7; return 11; }
    public static uint GetDpiForWindow(IntPtr hwnd) { return 96; }
}
public static class WeComSearchFocusWin32 {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct GUITHREADINFO {
        public uint cbSize, flags;
        public IntPtr hwndActive, hwndFocus, hwndCapture, hwndMenuOwner, hwndMoveSize, hwndCaret;
        public RECT rcCaret;
    }
    public static IntPtr Focus, Caret;
    public static int X=212, Y=27;
    public static bool GetGUIThreadInfo(uint thread, ref GUITHREADINFO info) {
        info.hwndActive=(IntPtr)101; info.hwndFocus=Focus; info.hwndCaret=Caret;
        info.rcCaret.Left=X; info.rcCaret.Right=X+1;
        info.rcCaret.Top=Y; info.rcCaret.Bottom=Y+19;
        return true;
    }
}
'@
function Assert-True($Value,[string]$Why) { if (-not $Value) { throw $Why } }
function Throw-DriverError { param($Code,$Message) throw ('WECOMDRIVE|'+$Code+'|'+$Message) }
function Get-WeComWindowInfo { param($Hwnd) return @{Pid=$(if ($Hwnd.ToInt64() -eq 202 -and $script:foreign) {8} else {7});Visible=$true} }
function Focus-WeComSearchBox { param($MainHwnd) [void]$script:actions.Add('focus'); return 'shortcut' }
function Clear-WeComFocusedInput { param($Hwnd) [void]$script:actions.Add('clear');return $true }
function Get-WeComTopLevelWindows { if ($script:prior) {return @{Class='SearchResultWindow2';Visible=$true;Pid=7}} }
function Send-WeComText { param($Hwnd,$Text) [void]$script:actions.Add('query');Assert-True ($Text -eq '陆伟') 'must type the requested query' }
function Test-WeComSearchClipboardEcho { param($MainHwnd,$Query) [void]$script:actions.Add('echo');return @{matched=$script:echo;restored=$true;reason='copied'} }
function Wait-WeComWindow { param($ClassName,$TimeoutMs) [void]$script:actions.Add('wait');if ($script:overlay) {return 202};return 0 }
function Test-WeComSearchWindowUnchanged { param($Before,$After) return $true }
function Send-WeComEnter { param($Hwnd) [void]$script:actions.Add('enter') }
function Start-Sleep { param($Milliseconds) }
function Invoke-Scenario([string]$Name) {
    $script:actions=New-Object System.Collections.ArrayList
    $failure='';try { Open-WeComNamedChat -Hwnd 101 -Name '陆伟' } catch { $failure=$_.Exception.Message }
    return @{name=$Name;failure=$failure;actions=@($script:actions)}
}
[WeComSearchFocusWin32]::Focus=[IntPtr]101
[WeComSearchFocusWin32]::Caret=[IntPtr]101
$script:echo=$true;$script:overlay=$true;$script:prior=$false;$script:foreign=$false
$r=Invoke-Scenario 'valid search'
Assert-True (-not $r.failure -and ($r.actions -join ',') -eq 'focus,clear,query,echo,wait,enter') 'validated query and fresh result panel may Enter'

[WeComSearchFocusWin32]::X=600;[WeComSearchFocusWin32]::Y=700
$r=Invoke-Scenario 'caret in message editor'
Assert-True ($r.failure -like 'WECOMDRIVE|UI_CHANGED|*' -and ($r.actions -join ',') -eq 'focus') 'chat editor caret must prevent draft clearing and Enter'
[WeComSearchFocusWin32]::X=212;[WeComSearchFocusWin32]::Y=27
[WeComSearchFocusWin32]::Caret=[IntPtr]0
$r=Invoke-Scenario 'no caret'
Assert-True ($r.failure -like 'WECOMDRIVE|UI_CHANGED|*' -and ($r.actions -join ',') -eq 'focus') 'missing search caret must prevent draft clearing and Enter'
[WeComSearchFocusWin32]::Caret=[IntPtr]101
$script:echo=$false
$r=Invoke-Scenario 'query not copied from search'
Assert-True ($r.failure -like 'WECOMDRIVE|UI_CHANGED|*' -and $r.actions[-1] -eq 'echo' -and -not ($r.actions -contains 'enter')) 'unproven query must not Enter'
$script:echo=$true;$script:overlay=$false
$r=Invoke-Scenario 'no new result panel'
Assert-True ($r.failure -like 'WECOMDRIVE|UI_CHANGED|*' -and $r.actions[-1] -eq 'wait' -and -not ($r.actions -contains 'enter')) 'missing search result panel must not Enter'
$script:overlay=$true;$script:prior=$true
$r=Invoke-Scenario 'old result panel remained'
Assert-True ($r.failure -like 'WECOMDRIVE|UI_CHANGED|*' -and ($r.actions -join ',') -eq 'focus,clear') 'stale result panel must not reuse an old selection'
$script:prior=$false;$script:foreign=$true
$r=Invoke-Scenario 'result panel belongs to another process'
Assert-True ($r.failure -like 'WECOMDRIVE|UI_CHANGED|*' -and -not ($r.actions -contains 'enter')) 'foreign result panel must not Enter'
Write-Output 'PS_SEND_SEARCH_GATE_OK'
