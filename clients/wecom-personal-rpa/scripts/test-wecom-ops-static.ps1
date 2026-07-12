$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$main = Get-Content -Raw (Join-Path $scriptDir 'wecom-ops.ps1')
$lib = Get-Content -Raw (Join-Path $scriptDir 'wecom-ops-lib.ps1')
$checks = @(
    @{ Name = 'search calls double-Alt focus helper'; Ok = $main -match 'Focus-WeComSearchBox\s+-Hwnd\s+\$origin\.Hwnd' },
    @{ Name = 'search no longer clicks coordinates'; Ok = $main -notmatch 'Click-At\s+-x' },
    @{ Name = 'search coordinate calculation removed'; Ok = $main -notmatch '\$searchBbox|1936\.0|clicked_x|clicked_y' },
    @{ Name = 'activation failure aborts safely'; Ok = $main -match 'wecom_window_activation_failed' },
    @{ Name = 'foreground Win32 APIs declared'; Ok = $lib -match 'SetForegroundWindow' -and $lib -match 'ShowWindow' },
    @{ Name = 'Alt key is pressed exactly twice'; Ok = $lib -match 'for \(\$i = 0; \$i -lt 2; \$i\+\+\)' -and $lib -match 'keybd_event\(0x12' },
    @{ Name = 'foreground is guarded during double Alt'; Ok = $lib -match 'GetForegroundWindow\(\) -ne \$Hwnd' },
    @{ Name = 'foreground is verified after double Alt'; Ok = $lib -match 'return \(\[WeOpsWin32\]::GetForegroundWindow\(\) -eq \$Hwnd\)' }
)
$failed = @($checks | Where-Object { -not $_.Ok })
$checks | ForEach-Object { if ($_.Ok) { Write-Host "PASS: $($_.Name)" } else { Write-Host "FAIL: $($_.Name)" } }
if ($failed.Count -gt 0) { throw "$($failed.Count) static checks failed" }
Write-Host "All $($checks.Count) static checks passed; no real WeCom operation was executed."
