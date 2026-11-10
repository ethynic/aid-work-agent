[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][Guid]$ExpectedDeviceId
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$product = Join-Path $env:APPDATA 'aidwork-runtime-app-acceptance'
$metadata = Join-Path $product 'installation-v2'
$pointerFile = Join-Path $metadata 'current.json'
$pendingFile = Join-Path $metadata 'clean-registration.bin'
$expectedOrigin = 'https://agent.aidingyi.cn'

function Assert-RuntimeClosed {
    $processes = Get-CimInstance Win32_Process -Filter "Name LIKE 'AID Work Runtime%.exe' OR Name = 'node.exe'"
    if ($processes | Where-Object {
        $_.Name -like 'AID Work Runtime*.exe' -or
        ($_.CommandLine -match '[\\/]runtime[\\/]host[\\/]managed-entry\.js' -and
         $_.CommandLine -match '--supervisor=runtime_app')
    }) { throw 'RUNTIME_STILL_RUNNING' }
}

function Assert-LocalPath([string]$Path) {
    for ($item = Get-Item -LiteralPath $Path -Force; $null -ne $item; $item = $item.Parent) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'LINKED_RUNTIME_PATH' }
        if (-not $item.PSIsContainer) { $item = $item.Directory; if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'LINKED_RUNTIME_PATH' } }
    }
}

function Read-LocalText([string]$Path) {
    Assert-LocalPath $Path
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or $item.Length -gt 65536) { throw 'INVALID_RUNTIME_FILE' }
    return [IO.File]::ReadAllText($Path)
}

function Unprotect-LocalText([string]$Path) {
    $cipher = [Convert]::FromBase64String((Read-LocalText $Path))
    $plain = [Security.Cryptography.ProtectedData]::Unprotect(
        $cipher, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    return [Text.Encoding]::UTF8.GetString($plain)
}

function Confirm-ConnectionDirectory([string]$Directory, [Guid]$Generation) {
    $absolute = [IO.Path]::GetFullPath($Directory)
    $leaf = [Guid]::Empty
    $parent = Split-Path -Parent $absolute
    if ($absolute -ne (Join-Path $product 'rt') -and
        (-not [Guid]::TryParseExact((Split-Path -Leaf $absolute), 'D', [ref]$leaf) -or
         $parent -notin @((Join-Path $product 'connections'), (Join-Path $metadata "generations\$Generation\connections")))) {
        throw 'UNEXPECTED_CONNECTION_DIRECTORY'
    }
    Assert-LocalPath $absolute
    return $absolute
}

try {
    Assert-RuntimeClosed
    $pointerText = Read-LocalText $pointerFile
    $pointer = $pointerText | ConvertFrom-Json
    $pendingText = Read-LocalText $pendingFile
    $pending = (Unprotect-LocalText $pendingFile) | ConvertFrom-Json
    if ($pointer.version -ne 2 -or $pointer.initialized -ne $true -or $pending.version -ne 1 -or
        $pending.phase -ne 'requested' -or $null -ne $pending.device_id -or
        $pending.request.pairing_protocol -ne 3 -or $pending.server.TrimEnd('/') -cne 'http://agent.aidingyi.cn') {
        throw 'UNEXPECTED_PAIRING_STATE'
    }
    $generation = [Guid]$pointer.id
    $active = Confirm-ConnectionDirectory $pointer.home $generation
    $original = Confirm-ConnectionDirectory $pointer.priorHome $generation
    if ($active -eq $original -or [IO.Path]::GetFullPath($pending.home) -ne $active -or
        (Test-Path -LiteralPath (Join-Path $active 'config.json')) -or
        (Test-Path -LiteralPath (Join-Path $active 'credentials.bin'))) {
        throw 'UNEXPECTED_ACTIVE_IDENTITY'
    }

    $config = (Read-LocalText (Join-Path $original 'config.json')) | ConvertFrom-Json
    $token = Unprotect-LocalText (Join-Path $original 'credentials.bin')
    if ([Guid]$config.device_id -ne $ExpectedDeviceId -or $config.server.TrimEnd('/') -cne $expectedOrigin -or
        $token -cnotmatch '^[0-9a-f]{64}$') { throw 'ORIGINAL_DEVICE_MISMATCH' }
    $matchingReceipt = $false
    foreach ($file in Get-ChildItem -LiteralPath $metadata -File -Filter 'completed-registration-*.bin') {
        try {
            $receipt = (Unprotect-LocalText $file.FullName) | ConvertFrom-Json
            if ($receipt.version -eq 1 -and $receipt.phase -eq 'installed' -and
                [Guid]$receipt.device_id -eq $ExpectedDeviceId -and
                $receipt.server.TrimEnd('/') -ceq $expectedOrigin -and
                $receipt.request.registration_token -ceq $token) { $matchingReceipt = $true; break }
        } catch { continue }
    }
    if (-not $matchingReceipt) { throw 'ORIGINAL_DEVICE_RECEIPT_MISMATCH' }

    $id = [Guid]::NewGuid().ToString('N')
    $pointerBackup = Join-Path $metadata "current.pre-recovery-$id.json"
    $pendingBackup = Join-Path $metadata "quarantined-clean-registration-$id.bin"
    $pointerNext = Join-Path $metadata "current.recovery-$id.tmp"
    $pointer.home = $original
    $pointer.priorHome = $active
    [IO.File]::WriteAllText($pointerNext, ($pointer | ConvertTo-Json -Depth 5 -Compress), [Text.UTF8Encoding]::new($false))
    Assert-RuntimeClosed
    if ((Read-LocalText $pointerFile) -cne $pointerText -or (Read-LocalText $pendingFile) -cne $pendingText) {
        throw 'RUNTIME_STATE_CHANGED'
    }
    [IO.File]::Move($pendingFile, $pendingBackup)
    try { [IO.File]::Replace($pointerNext, $pointerFile, $pointerBackup) }
    catch {
        if (-not (Test-Path -LiteralPath $pendingFile)) { [IO.File]::Move($pendingBackup, $pendingFile) }
        throw 'POINTER_RESTORE_FAILED'
    }
    [ordered]@{
        status = 'original-device-restored'
        deviceId = $ExpectedDeviceId.ToString()
        pointerBackup = [IO.Path]::GetFileName($pointerBackup)
        quarantinedPending = [IO.Path]::GetFileName($pendingBackup)
    } | ConvertTo-Json
} catch {
    $reason = if ($_.Exception.Message -cmatch '^[A-Z_]+$') { $_.Exception.Message } else { 'CHECK_OR_RESTORE_FAILED' }
    [ordered]@{ status = 'stopped'; reason = $reason } | ConvertTo-Json
    exit 1
}
