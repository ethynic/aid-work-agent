# Local cache and window-ownership records for the serialized friend-add driver.
function Get-WeixinFriendStatePath([string]$Name) {
    $directory = Join-Path $env:LOCALAPPDATA 'aid-weixin\friend-add'
    $session = [Diagnostics.Process]::GetCurrentProcess().SessionId
    return (Join-Path $directory "$session-$Name.json")
}

function Read-WeixinFriendState([string]$Name) {
    $path = Get-WeixinFriendStatePath $Name
    if (-not [IO.File]::Exists($path)) { return $null }
    return ([IO.File]::ReadAllText($path, [Text.Encoding]::UTF8) | ConvertFrom-Json)
}

function Write-WeixinFriendState([string]$Name, $Value) {
    $path = Get-WeixinFriendStatePath $Name
    [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path))
    $tempPath = $path + '.tmp'
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Value -Depth 5 -Compress))
        $file = [IO.File]::Open($tempPath, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $file.Write($bytes, 0, $bytes.Length); $file.Flush($true) }
        finally { $file.Dispose() }
        if ([IO.File]::Exists($path)) { [IO.File]::Replace($tempPath, $path, [NullString]::Value) }
        else { [IO.File]::Move($tempPath, $path) }
    } finally { [IO.File]::Delete($tempPath) }
}

function Get-WeixinFriendControlHash($Frame, $Point) {
    # Store only a digest of a small neighborhood, never a screenshot.
    $image = [IO.MemoryStream]::new([byte[]]$Frame.Image)
    try {
        $bitmap = [Drawing.Bitmap]::new($image)
        try {
            $x = [Math]::Max(0, [int]$Point.x - 12)
            $y = [Math]::Max(0, [int]$Point.y - 12)
            $width = [Math]::Min($bitmap.Width, [int]$Point.x + 13) - $x
            $height = [Math]::Min($bitmap.Height, [int]$Point.y + 13) - $y
            $area = [Drawing.Rectangle]::new($x, $y, $width, $height)
            $crop = $bitmap.Clone($area, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
            $output = [IO.MemoryStream]::new()
            $hasher = [Security.Cryptography.SHA256]::Create()
            try {
                $crop.Save($output, [Drawing.Imaging.ImageFormat]::Png)
                return [Convert]::ToBase64String($hasher.ComputeHash($output.ToArray()))
            } finally { $hasher.Dispose(); $output.Dispose(); $crop.Dispose() }
        } finally { $bitmap.Dispose() }
    } finally { $image.Dispose() }
}

function Get-WeixinFriendLayoutKey($Window, $Frame) {
    $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($Window.ProcessPath).FileVersion
    if ([string]::IsNullOrWhiteSpace($version)) { return $null }
    $dpi = [WeixinProbeWin32]::GetDpiForWindow([IntPtr]$Window.Hwnd)
    $rect = $Frame.WindowRect
    $bounds = $Frame.Bounds
    return "$version|$($Window.ClassName)|$dpi|$($rect.Right - $rect.Left)|$($rect.Bottom - $rect.Top)|$($bounds.X),$($bounds.Y),$($bounds.Width),$($bounds.Height)"
}

function Get-WeixinFriendControl($Window, $Frame, [string]$Step, [string]$Question, [string]$Field, [switch]$Refresh) {
    $key = $null
    try { $key = Get-WeixinFriendLayoutKey $Window $Frame } catch {}
    if ($key -and -not $Refresh) {
        try {
            $cached = Read-WeixinFriendState $Step
            if ($cached -and $cached.layout -ceq $key) {
                $point = $cached.point
                $null = Get-WeixinFriendPoint $Frame $point
                if ($cached.hash -ceq (Get-WeixinFriendControlHash $Frame $point)) {
                    return @{ Point=$point; Cached=$true; Entry=$cached; Step=$Step }
                }
            }
        } catch {} # Cache damage or a changed control is a cache miss.
    }
    $point = (Get-WeixinFriendVision $Frame $Step $Question).$Field
    $null = Get-WeixinFriendPoint $Frame $point
    $entry = $null
    if ($key) {
        try {
            $entry = @{ layout=$key; point=@{ x=$point.x; y=$point.y }; hash=(Get-WeixinFriendControlHash $Frame $point) }
        } catch {}
    }
    return @{ Point=$point; Cached=$false; Entry=$entry; Step=$Step }
}

function Invoke-WeixinFriendControlClick($Window, $Frame, $Control, [string]$Field, [string]$Question, [int64]$MenuOwner = 0) {
    if ($Control.Cached) {
        $current = Get-WeixinFriendFrame $Window $Frame.Bounds $MenuOwner
        $matches = $false
        try {
            $matches = $Control.Entry.layout -ceq (Get-WeixinFriendLayoutKey $Window $current) -and
                $Control.Entry.hash -ceq (Get-WeixinFriendControlHash $current $Control.Point)
        } catch {}
        if (-not $matches) {
            $Control = Get-WeixinFriendControl $Window $current $Control.Step $Question $Field -Refresh
        }
        $Frame = $current
    }
    Click-WeixinFriendPoint $Window $Frame $Control.Point $MenuOwner
    return $Control
}

function Save-WeixinFriendControl($Control) {
    if ($Control.Entry -and -not $Control.Cached) {
        try { Write-WeixinFriendState $Control.Step $Control.Entry } catch {}
    }
}

function Test-WeixinFriendOwnedWindow($Record) {
    if (-not (Test-WeixinFriendWindow $Record)) { return $false }
    $live = Get-WindowIdentityByHwnd ([int64]$Record.Hwnd)
    return ($live.ProcessPath -ieq $Record.ProcessPath -and
        [WeixinProbeWin32]::GetProp([IntPtr]$Record.Hwnd, 'AidWorkAgent.WeixinCli.FriendDialog.v1').ToInt64() -eq $Record.Marker)
}

function Add-WeixinFriendOwnedWindow($Owned, $Window) {
    if (-not (Test-WeixinFriendWindow $Window)) {
        Throw-DriverError 'UI_CHANGED' '新打开的微信窗口已改变'
    }
    $marker = [BitConverter]::ToUInt32([guid]::NewGuid().ToByteArray(), 0) -band 2147483647
    if ($marker -eq 0) { $marker = 1 }
    $record = [pscustomobject]@{
        Hwnd=$Window.Hwnd; ProcessId=$Window.ProcessId; ProcessPath=$Window.ProcessPath
        Title=$Window.Title; ClassName=$Window.ClassName; Marker=$marker
    }
    $Owned.Add($record)
    if (-not [WeixinProbeWin32]::SetProp([IntPtr]$record.Hwnd, 'AidWorkAgent.WeixinCli.FriendDialog.v1', [IntPtr]$marker)) {
        Throw-DriverError 'UI_CHANGED' '无法登记本轮打开的微信窗口'
    }
    Write-WeixinFriendState 'owned-windows' @($Owned.ToArray())
}

function Close-WeixinFriendOwnedWindows($Owned, $KeepWindow = $null) {
    $remaining = [Collections.Generic.List[object]]::new()
    for ($index = $Owned.Count - 1; $index -ge 0; $index--) {
        $record = $Owned[$index]
        if (-not (Test-WeixinFriendOwnedWindow $record)) { continue }
        if ($KeepWindow -and $record.Hwnd -eq $KeepWindow.Hwnd) {
            [void][WeixinProbeWin32]::RemoveProp([IntPtr]$record.Hwnd, 'AidWorkAgent.WeixinCli.FriendDialog.v1')
            continue
        }
        for ($attempt = 0; $attempt -lt 3 -and (Test-WeixinFriendOwnedWindow $record); $attempt++) {
            [void][WeixinProbeWin32]::PostMessage([IntPtr]$record.Hwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
            for ($poll = 0; $poll -lt 8 -and (Test-WeixinFriendOwnedWindow $record); $poll++) {
                Start-Sleep -Milliseconds 75
            }
        }
        if (Test-WeixinFriendOwnedWindow $record) { $remaining.Add($record) }
    }
    if ($Owned.Count -gt 0) {
        Write-WeixinFriendState 'owned-windows' @($remaining.ToArray())
    }
    $Owned.Clear()
    foreach ($record in $remaining) { $Owned.Add($record) }
    return ($remaining.Count -eq 0)
}

function Restore-WeixinFriendOwnedWindows {
    $owned = [Collections.Generic.List[object]]::new()
    foreach ($record in @(Read-WeixinFriendState 'owned-windows')) {
        if ($record -and $record.Marker -and $record.Hwnd -and $record.ProcessId) { $owned.Add($record) }
    }
    if (-not (Close-WeixinFriendOwnedWindows $owned)) {
        Throw-DriverError 'UI_CHANGED' '上次操作的微信窗口尚未清理，请核对后再执行'
    }
}
