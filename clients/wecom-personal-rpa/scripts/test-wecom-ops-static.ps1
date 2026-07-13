$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$main = Get-Content -Raw (Join-Path $scriptDir 'wecom-ops.ps1')
$libPath = Join-Path $scriptDir 'wecom-ops-lib.ps1'
$lib = Get-Content -Raw $libPath
. $libPath
$targetHwnd = [IntPtr]123
$foregroundHelperBehaviorOk =
    (Test-WeComForegroundWindow -Hwnd $targetHwnd -ForegroundHwnd $targetHwnd) -and
    -not (Test-WeComForegroundWindow -Hwnd $targetHwnd -ForegroundHwnd ([IntPtr]456))
$checks = @(
    @{ Name = 'search calls double-Alt focus helper'; Ok = $main -match 'Focus-WeComSearchBox\s+-Hwnd\s+\$origin\.Hwnd' },
    @{ Name = 'search no longer clicks coordinates'; Ok = $main -notmatch 'Click-At\s+-x' },
    @{ Name = 'search coordinate calculation removed'; Ok = $main -notmatch '\$searchBbox|1936\.0|clicked_x|clicked_y' },
    @{ Name = 'activation failure aborts safely'; Ok = $main -match 'wecom_window_activation_failed' },
    @{ Name = 'foreground Win32 APIs declared'; Ok = @('GetWindowThreadProcessId', 'GetCurrentThreadId', 'AttachThreadInput', 'BringWindowToTop', 'SetActiveWindow', 'SetForegroundWindow', 'ShowWindow') | ForEach-Object { $lib -match $_ } | Where-Object { -not $_ } | Measure-Object | Select-Object -ExpandProperty Count | ForEach-Object { $_ -eq 0 } },
    @{ Name = 'input threads attach only when needed'; Ok = $lib -match 'AttachThreadInput\(\$currentThreadId, \$threadId, \$true\)' -and $lib -match '\$threadId -ne \$currentThreadId' },
    @{ Name = 'successful thread attachments are detached in finally'; Ok = $lib -match '(?s)finally\s*\{.*AttachThreadInput\(\s*\$currentThreadId, \[uint32\]\$attachedThreadIds\[\$index\], \$false' },
    @{ Name = 'activation uses full foreground sequence'; Ok = $lib -match 'BringWindowToTop\(\$Hwnd\)' -and $lib -match 'SetActiveWindow\(\$Hwnd\)' -and $lib -match 'SetForegroundWindow\(\$Hwnd\)' },
    @{ Name = 'Alt key is pressed exactly twice'; Ok = $lib -match 'for \(\$i = 0; \$i -lt 2; \$i\+\+\)' -and $lib -match 'keybd_event\(0x12' },
    @{ Name = 'foreground is guarded during double Alt'; Ok = $lib -match 'GetForegroundWindow\(\) -ne \$Hwnd' },
    @{ Name = 'foreground is verified after double Alt'; Ok = $lib -match 'return \(\[WeOpsWin32\]::GetForegroundWindow\(\) -eq \$Hwnd\)' },
    @{ Name = 'reuse defaults false for backward compatibility'; Ok = $main -match '\$reuseCurrentConversation\s*=\s*\$false' },
    @{ Name = 'normal send still searches'; Ok = $main -match '(?s)if \(-not \$ReuseCurrentConversation\).*Search-WeComUserInternal' },
    @{ Name = 'reuse skips search and validates foreground'; Ok = $main -match '(?s)function Resolve-WeComSendNavigation.*Test-WeComForegroundWindow' },
    @{ Name = 'all send actions use navigation resolver'; Ok = ([regex]::Matches($main, 'Resolve-WeComSendNavigation -Keyword \$Keyword -ReuseCurrentConversation \$ReuseCurrentConversation')).Count -eq 3 },
    @{ Name = 'all send entrypoints forward reuse flag'; Ok = ([regex]::Matches($main, '-ReuseCurrentConversation \$reuseCurrentConversation')).Count -eq 3 },
    @{ Name = 'foreground helper rejects a different application window'; Ok = $foregroundHelperBehaviorOk },
    @{ Name = 'reuse foreground failure happens before text input'; Ok = $main -match '(?s)\$nav = Resolve-WeComSendNavigation.*if \(-not \$nav\.success\) \{ return \$nav \}.*Type-Text -text \$Text' },
    @{ Name = 'reuse text revalidates before clipboard paste and Enter'; Ok = $lib -match '(?s)function Type-Text.*Test-WeComForegroundWindow.*Clipboard\]::SetText.*Test-WeComForegroundWindow.*keybd_event\(0x11' -and $main -match 'Press-Enter -ExpectedForegroundHwnd \$expectedHwnd' },
    @{ Name = 'reuse attachments revalidate before clipboard mutation'; Ok = ([regex]::Matches($main, 'Test-WeComForegroundWindow -Hwnd \$expectedHwnd')).Count -eq 2 },
    @{ Name = 'reuse attachments revalidate before paste and confirm'; Ok = ([regex]::Matches($main, 'Press-CtrlV -ExpectedForegroundHwnd \$expectedHwnd')).Count -eq 2 -and ([regex]::Matches($main, 'Press-Enter -ExpectedForegroundHwnd \$expectedHwnd')).Count -eq 3 },
    @{ Name = 'send_file uses file drop clipboard'; Ok = $main -match 'Clipboard\]::SetFileDropList\(\$dropList\)' },
    @{ Name = 'send_file pastes and confirms'; Ok = $main -match '(?s)SetFileDropList.*Press-CtrlV.*Press-Enter' },
    @{ Name = 'send_file clears clipboard'; Ok = $main -match 'Clipboard\]::Clear\(\)' },
    @{ Name = 'send_image uses image clipboard'; Ok = $main -match 'Clipboard\]::SetImage\(\$bmp\)' }
)
$failed = @($checks | Where-Object { -not $_.Ok })
$checks | ForEach-Object { if ($_.Ok) { Write-Host "PASS: $($_.Name)" } else { Write-Host "FAIL: $($_.Name)" } }
if ($failed.Count -gt 0) { throw "$($failed.Count) static checks failed" }
Write-Host "All $($checks.Count) static checks passed; no real WeCom operation was executed."
