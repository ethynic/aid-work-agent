param(
  [ValidateSet('capture','click')][string]$Action = 'capture',
  [int]$CdpPort = 9222,
  [string]$Output = '',
  [string]$Observation = '',
  [int]$X = -1,
  [int]$Y = -1,
  [int]$MaxAgeSeconds = 120
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
$physicalSource = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'BossPhysicalInput.cs') -Raw
$windowSource = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'BossWindowExperiment.cs') -Raw
# C# using declarations must precede both shared type declarations.
$windowSource = $windowSource -replace '(?m)^using [^;]+;\r?\n', ''
Add-Type -TypeDefinition ("using System.Collections.Generic;`n" + $physicalSource + "`n" + $windowSource)
$scope = [BossPhysicalInput]::Enter()
try {
  $portPattern = '(?:^|\s)--remote-debugging-port(?:=|\s+)' + $CdpPort + '(?:\s|$)'
  $processes = @(Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" | Where-Object { $_.CommandLine -match $portPattern -and $_.CommandLine -notmatch '(?:^|\s)--type=' })
  if ($processes.Count -ne 1) { throw 'Expected exactly one debug Chrome browser process for this port' }
  $browserPid = [int]$processes[0].ProcessId
  $processStartedAt = ([DateTimeOffset](Get-Process -Id $browserPid).StartTime).ToUniversalTime().ToString('o')
  $window = [BossWindowExperiment]::Find($browserPid)
  if ($Action -ne 'capture') {
    if ($MaxAgeSeconds -lt 1 -or $MaxAgeSeconds -gt 600) { throw 'Invalid observation age limit; input refused' }
    $meta = Get-Content -LiteralPath $Observation -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($meta.space -ne 'window_image_px' -or $meta.window.pid -ne $browserPid -or [string]$meta.window.hwnd -ne $window.ToInt64().ToString()) { throw 'Observation does not identify this debug Chrome window' }
    if ($meta.window.process_started_at -and $meta.window.process_started_at -ne $processStartedAt) { throw 'STALE_OBSERVATION: Chrome process identity changed' }
    $age = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($meta.captured_at)).TotalSeconds
    if ($age -lt 0 -or $age -gt $MaxAgeSeconds) { throw 'Observation expired; capture again' }
    [BossWindowExperiment]::RequireGeometry($window, $meta.window.left, $meta.window.top, $meta.window.width, $meta.window.height)
  }
  [BossWindowExperiment]::Foreground($window)
  Start-Sleep -Milliseconds 400
  [BossWindowExperiment]::RequireForeground($window)
  $rect = [BossWindowExperiment]::ReadCaptureRect($window)
  $width = $rect.Right - $rect.Left
  $height = $rect.Bottom - $rect.Top
  if ($Action -eq 'click') {
    if ($meta.cdp_port -ne $CdpPort -or $meta.width -ne $width -or $meta.height -ne $height -or $X -lt 0 -or $Y -lt 0 -or $X -ge $width -or $Y -ge $height) { throw 'Observation image/coordinate mismatch; input refused' }
    $actualHash = (Get-FileHash -LiteralPath $meta.output -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $meta.sha256) { throw 'Original observation image changed; input refused' }
    $original = [System.Drawing.Bitmap]::FromFile($meta.output)
    $roiX = [Math]::Max(0, $X - 16)
    $roiY = [Math]::Max(0, $Y - 16)
    $roiW = [Math]::Min(32, $width - $roiX)
    $roiH = [Math]::Min(32, $height - $roiY)
    $fresh = New-Object System.Drawing.Bitmap($roiW, $roiH)
    $graphics = [System.Drawing.Graphics]::FromImage($fresh)
    try {
      if ($original.Width -ne $width -or $original.Height -ne $height) { throw 'Original observation dimensions changed; input refused' }
      $graphics.CopyFromScreen(($rect.Left + $roiX), ($rect.Top + $roiY), 0, 0, $fresh.Size, [System.Drawing.CopyPixelOperation]::SourceCopy)
      $changed = 0
      for ($row = 0; $row -lt $roiH; $row++) {
        for ($col = 0; $col -lt $roiW; $col++) {
          $before = $original.GetPixel(($roiX + $col), ($roiY + $row))
          $after = $fresh.GetPixel($col, $row)
          if ([Math]::Abs([int]$before.R - [int]$after.R) -gt 8 -or [Math]::Abs([int]$before.G - [int]$after.G) -gt 8 -or [Math]::Abs([int]$before.B - [int]$after.B) -gt 8) { $changed++ }
        }
      }
      if ($changed -gt ($roiW * $roiH * 0.02)) { throw 'STALE_OBSERVATION: click target pixels changed; capture again' }
    } finally { $graphics.Dispose(); $fresh.Dispose(); $original.Dispose() }
    [BossWindowExperiment]::Click($window, $meta.window.left, $meta.window.top, $meta.window.width, $meta.window.height, $X, $Y, $scope)
    @{ clicked = $true; x = $X; y = $Y; space = 'window_image_px'; hwnd = $window.ToInt64().ToString() } | ConvertTo-Json -Compress
  } else {
    $screen = [System.Windows.Forms.SystemInformation]::VirtualScreen
    if ($rect.Left -lt $screen.Left -or $rect.Top -lt $screen.Top -or $rect.Right -gt $screen.Right -or $rect.Bottom -gt $screen.Bottom) { throw 'Browser window extends outside visible screen; capture refused' }
    $image = New-Object System.Drawing.Bitmap($width, $height)
    $graphics = [System.Drawing.Graphics]::FromImage($image)
    try {
      $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $image.Size, [System.Drawing.CopyPixelOperation]::SourceCopy)
      [BossWindowExperiment]::RequireGeometry($window, $rect.Left, $rect.Top, $width, $height)
      [BossWindowExperiment]::RequireForeground($window)
      $image.Save($Output, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $graphics.Dispose(); $image.Dispose() }
    $dialogs = @([BossWindowExperiment]::OwnedDialogs($window) | ForEach-Object { @{ hwnd = $_.ToInt64().ToString(); title = [BossWindowExperiment]::Title($_); class = [BossWindowExperiment]::ClassName($_) } })
    @{ captured_at = [DateTimeOffset]::UtcNow.ToString('o'); window = @{ hwnd = $window.ToInt64().ToString(); pid = $browserPid; process_started_at = $processStartedAt; left = $rect.Left; top = $rect.Top; width = $width; height = $height }; owned_dialogs = $dialogs } | ConvertTo-Json -Depth 5 -Compress
  }
} catch {
  # Preserve the zero-input/unknown boundary even if output serialization failed.
  if ($scope.InputStarted) { throw ('[BOSS_INPUT_UNKNOWN] ' + $_.Exception.Message) }
  throw ('[BOSS_WINDOW_REFUSED] ' + $_.Exception.Message)
} finally { $scope.Dispose() }
