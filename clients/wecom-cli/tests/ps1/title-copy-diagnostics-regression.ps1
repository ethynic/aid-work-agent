param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference = 'Stop'
. (Join-Path $DriverDir '_send-points.ps1')
Add-Type @'
public static class WeComWin32 {
 public static uint Sequence = 10; public static long Owner = 404; public static string Text = "old-rich-data";
 public static uint GetClipboardSequenceNumber() { return Sequence; }
 public static System.IntPtr GetClipboardOwner() { return (System.IntPtr)Owner; }
 public static uint GetWindowThreadProcessId(System.IntPtr hwnd, out uint pid) {
  pid = hwnd.ToInt64() == 202 ? 7u : 49232u; return 1;
 }
 public static bool IsWindow(System.IntPtr hwnd) { return true; }
}
public static class TitleClipboard {
 public static int Reads = 0;
 public static bool ContainsText() { Reads++; return true; }
 public static string GetText() { Reads++; return WeComWin32.Text; }
}
'@
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $DriverDir '_send-points.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$fn = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $n.Name -eq 'Get-WeComCopiedTitle' }, $true)
$definition = $fn.Extent.Text.Replace('function Get-WeComCopiedTitle', 'function global:Get-WeComCopiedTitle').Replace('[Windows.Forms.Clipboard]', '[TitleClipboard]')
. ([scriptblock]::Create($definition))
function Get-WeComWindowInfo { param($Hwnd) return @{ X = 0; Y = 0; W = 1000; H = 800; Pid = 7 } }
function Get-WeComPlainClipboard { throw 'old clipboard must not be read' }
function Send-WeComClick { param($Hwnd, $ScreenX, $ScreenY)
    if ($Hwnd -ne 101) { throw 'focus reset and title selection must use the original window' }
    if ($ScreenX -eq 620 -and $ScreenY -eq 710) {
        [void]$script:ClickOrder.Add('input')
        if (-not $script:KeepEdit) { $script:Edit = $false }
    } elseif ($ScreenX -eq 440 -and $ScreenY -eq 38) {
        [void]$script:ClickOrder.Add('group-title')
        $script:TitleClicks++
        $script:Edit = $true
    } else { throw 'unexpected click location' }
}
function Get-WeComTitleEditWindow { param($Hwnd, $ScreenX, $ScreenY)
    if ($script:Edit) { return [IntPtr]303 }
    return [IntPtr]::Zero
}
function Send-WeComTitleDoubleClick { param($Hwnd, $ScreenX, $ScreenY)
    [void]$script:ClickOrder.Add('contact-title')
    $script:TitleClicks++
}
function Send-WeComAttachChordKey { param($Hwnd, $Vk)
    $script:Chords++
    if ($Vk -eq 0x43 -and -not $script:NoCopy) {
        [WeComWin32]::Text = 'Peer'
        [WeComWin32]::Owner = if ($script:RemoteTakeover) { 404 } else { 202 }
        [WeComWin32]::Sequence++
    }
    return $true
}
function Start-Sleep { param($Milliseconds) }
function Throw-DriverError { param($Code, $Message) throw ('WECOMDRIVE|' + $Code + '|' + $Message) }
function Write-DriverLog { param($Message) [void]$script:Logs.Add($Message) }
function Assert-True($Value, [string]$Why) { if (-not $Value) { throw $Why } }
function Run-Case([bool]$RemoteTakeover, [bool]$NoCopy, [string]$Section = 'contact', [bool]$KeepEdit = $false) {
    [WeComWin32]::Sequence = 10; [WeComWin32]::Owner = 404; [WeComWin32]::Text = 'old-rich-data'
    [TitleClipboard]::Reads = 0
    $script:RemoteTakeover = $RemoteTakeover; $script:NoCopy = $NoCopy
    $script:Logs = New-Object Collections.ArrayList; $script:TitleClicks = 0; $script:Chords = 0
    $script:ClickOrder = New-Object Collections.ArrayList
    $script:Edit = ($Section -eq 'group'); $script:KeepEdit = $KeepEdit
    $title = $null; $failure = ''
    try { $title = Get-WeComCopiedTitle -Hwnd 101 -Section $Section -Point @{ x = 440; y = 38; input_x = 620; input_y = 710; source = 'glm' } }
    catch { $failure = $_.Exception.Message }
    return @{ title = $title; failure = $failure; logs = @($script:Logs);
        text = [WeComWin32]::Text; reads = [TitleClipboard]::Reads;
        titleClicks = $script:TitleClicks; chords = $script:Chords; order = ($script:ClickOrder -join ',') }
}
$formsLoaded = @([AppDomain]::CurrentDomain.GetAssemblies() | Where-Object {
    $_.GetName().Name -eq 'System.Windows.Forms' }).Count -gt 0
Assert-True (-not $formsLoaded) 'fresh PowerShell must not preload Windows Forms'
$r = Run-Case $false $false
Assert-True ($r.failure -eq '' -and $r.title -ceq 'Peer' -and $r.titleClicks -eq 1 -and
    $r.chords -eq 1 -and $r.text -ceq 'Peer' -and $r.reads -gt 0 -and
    $r.order -ceq 'input,contact-title') 'fresh title copy must first focus the cached input point'
Assert-True (@([AppDomain]::CurrentDomain.GetAssemblies() | Where-Object {
    $_.GetName().Name -eq 'System.Windows.Forms' }).Count -gt 0) 'fresh path must load Windows Forms before reading copied text'
Assert-True (($r.logs -join ';') -match 'title_ui_begin.*copy_read_end' -and
    ($r.logs -join ';') -notmatch 'old-rich-data|Peer|original_read|nonce|restore') 'trace must describe only this copy and contain no title or old clipboard text'
$r = Run-Case $true $false
Assert-True ($r.failure -like '*clipboard_changed*' -and $r.titleClicks -eq 1 -and
    $r.reads -eq 0) 'another clipboard owner must not be mistaken for WeCom'
$r = Run-Case $false $true
Assert-True ($r.failure -like '*title_copy_unconfirmed*' -and $r.titleClicks -eq 1 -and
    $r.chords -eq 1 -and $r.reads -eq 0) 'a copy chord without a new sequence must not prove the title'
$r = Run-Case $false $false 'group'
Assert-True ($r.failure -eq '' -and $r.title -ceq 'Peer' -and $r.order -ceq 'input,group-title' -and
    $r.titleClicks -eq 1 -and $r.chords -eq 2) 'reset stale group edit before selecting and copying the actual title'
$r = Run-Case $false $false 'group' $true
Assert-True ($r.failure -like '*title_edit_not_reset*' -and $r.order -ceq 'input' -and
    $r.chords -eq 0) 'persistent edit mode must fail without selecting or typing into a stale editor'
Write-Output 'PS_TITLE_COPY_DIAGNOSTICS_OK cases=5'
