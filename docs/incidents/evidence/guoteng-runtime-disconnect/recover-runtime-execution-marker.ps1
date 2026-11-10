[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][Guid]$ExpectedDeviceId,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$product = Join-Path $env:APPDATA 'aidwork-runtime-app-acceptance'
$metadata = Join-Path $product 'installation-v2'
$pointerFile = Join-Path $metadata 'current.json'
$expectedWrite = [DateTimeOffset]::Parse('2026-11-05T03:39:54.9257267Z').UtcDateTime
$utf8 = [Text.UTF8Encoding]::new($false)

function Assert-PlainPath([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    while ($item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'LINKED_RUNTIME_PATH' }
        $item = if ($item.PSIsContainer) { $item.Parent } else { $item.Directory }
    }
}

function Read-Text([string]$Path) {
    Assert-PlainPath $Path
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or $item.Length -gt 2097152) { throw 'INVALID_RUNTIME_FILE' }
    return [IO.File]::ReadAllText($Path)
}

function Unprotect-Text([string]$Value) {
    $plain = [Security.Cryptography.ProtectedData]::Unprotect(
        [Convert]::FromBase64String($Value), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    return $utf8.GetString($plain)
}

function Assert-NoOldExecution([int]$OldSession) {
    if (@(Get-CimInstance Win32_Process -Filter "SessionId=$OldSession").Count -ne 0) {
        throw 'ORIGINAL_SESSION_HAS_PROCESSES'
    }
    $processes = Get-CimInstance Win32_Process -Filter "Name LIKE 'AID Work Runtime%.exe' OR Name = 'node.exe'"
    if ($processes | Where-Object {
        $_.Name -like 'AID Work Runtime*.exe' -or
        ($_.CommandLine -match '[\\/]runtime[\\/]host[\\/]managed-entry\.js' -and
         $_.CommandLine -match '--supervisor=runtime_app')
    }) { throw 'RUNTIME_STILL_RUNNING' }
}

try {
    $pointerText = Read-Text $pointerFile
    $pointer = $pointerText | ConvertFrom-Json
    $generation = [Guid]$pointer.id
    $directory = [IO.Path]::GetFullPath([string]$pointer.home)
    $directoryId = [Guid]::Empty
    if ($pointer.version -ne 2 -or $pointer.initialized -ne $true -or
        ($directory -ne (Join-Path $product 'rt') -and
         (-not [Guid]::TryParseExact((Split-Path -Leaf $directory), 'D', [ref]$directoryId) -or
          (Split-Path -Parent $directory) -notin @((Join-Path $product 'connections'), (Join-Path $metadata "generations\$generation\connections"))))) {
        throw 'UNEXPECTED_RUNTIME_DIRECTORY'
    }
    if (Test-Path -LiteralPath (Join-Path $metadata 'clean-registration.bin')) { throw 'PAIRING_STILL_PENDING' }
    $config = (Read-Text (Join-Path $directory 'config.json')) | ConvertFrom-Json
    $token = Unprotect-Text (Read-Text (Join-Path $directory 'credentials.bin'))
    if ([Guid]$config.device_id -ne $ExpectedDeviceId -or
        $config.server.TrimEnd('/') -cne 'https://agent.aidingyi.cn' -or $token -cnotmatch '^[0-9a-f]{64}$') {
        throw 'ORIGINAL_DEVICE_MISMATCH'
    }

    $markerFile = Join-Path $directory 'host-running.json'
    $markerText = Read-Text $markerFile
    $markerWrite = (Get-Item -LiteralPath $markerFile -Force).LastWriteTimeUtc
    if ($markerWrite -ne $expectedWrite) { throw 'EXECUTION_MARKER_CHANGED' }
    $marker = $markerText | ConvertFrom-Json
    $instance = [Guid]$marker.instance_id
    $proof = (Unprotect-Text $marker.supervision) | ConvertFrom-Json
    if ($marker.schemaVersion -ne 2 -or $proof.generation -cne $marker.instance_id -or
        $proof.home -cne $directory -or $proof.rebootOnly -isnot [bool] -or $proof.rebootOnly -or
        $proof.sessionId -isnot [int] -or $proof.sessionId -le 0 -or
        $proof.sessionId -eq [Diagnostics.Process]::GetCurrentProcess().SessionId) {
        throw 'UNEXPECTED_EXECUTION_PROOF'
    }
    $ledgerFile = Join-Path $directory "process-supervision\$instance.bin"
    $ledger = (Unprotect-Text (Read-Text $ledgerFile)) | ConvertFrom-Json
    if ($ledger.schemaVersion -ne 1 -or $ledger.home -cne $directory -or $ledger.generation -cne $proof.generation -or
        $ledger.bootId -cne $proof.bootId -or $ledger.sessionId -ne $proof.sessionId) {
        throw 'EXECUTION_LEDGER_MISMATCH'
    }
    $lastBoot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime()
    $bootEvent = Get-WinEvent -FilterHashtable @{
        LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Boot'; Id = 27
    } -MaxEvents 1
    $bootXml = [xml]$bootEvent.ToXml()
    $bootType = ($bootXml.Event.EventData.Data | Where-Object Name -eq 'BootType').'#text'
    if ($bootType -cne '0' -or $lastBoot -le $markerWrite -or
        [Math]::Abs(($bootEvent.TimeCreated.ToUniversalTime() - $lastBoot).TotalSeconds) -gt 120) {
        throw 'FULL_WINDOWS_RESTART_NOT_CONFIRMED'
    }
    Assert-NoOldExecution $proof.sessionId
    if (-not $Apply) {
        [ordered]@{ status = 'verified'; lastWindowsBootUtc = $lastBoot.ToString('o'); deviceId = $ExpectedDeviceId.ToString() } | ConvertTo-Json
        exit 0
    }

    if ((Read-Text $pointerFile) -cne $pointerText -or (Read-Text $markerFile) -cne $markerText -or
        (Get-Item -LiteralPath $markerFile -Force).LastWriteTimeUtc -ne $markerWrite) {
        throw 'RUNTIME_STATE_CHANGED'
    }
    Assert-NoOldExecution $proof.sessionId
    $hold = Join-Path $directory 'identity-reconciliation.bin'
    if (-not (Test-Path -LiteralPath $hold)) {
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $digest = [BitConverter]::ToString($sha.ComputeHash($utf8.GetBytes($markerText))).Replace('-', '').ToLowerInvariant() }
        finally { $sha.Dispose() }
        $fact = @{ markerDigest = $digest; instanceId = $marker.instance_id } | ConvertTo-Json -Compress
        $protectedFact = [Security.Cryptography.ProtectedData]::Protect(
            $utf8.GetBytes($fact), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        $temp = Join-Path $directory ('identity-reconciliation-' + [Guid]::NewGuid().ToString('N') + '.tmp')
        [IO.File]::WriteAllText($temp, [Convert]::ToBase64String($protectedFact), $utf8)
        [IO.File]::Move($temp, $hold)
    }
    $archive = 'host-interrupted-' + [Guid]::NewGuid().ToString() + '.json'
    [IO.File]::Move($markerFile, (Join-Path $directory $archive))
    [ordered]@{ status = 'execution-marker-archived'; deviceId = $ExpectedDeviceId.ToString(); backup = $archive } | ConvertTo-Json
} catch {
    $reason = if ($_.Exception.Message -cmatch '^[A-Z_]+$') { $_.Exception.Message } else { 'EXECUTION_EVIDENCE_UNAVAILABLE' }
    [ordered]@{ status = 'stopped'; reason = $reason } | ConvertTo-Json
    exit 1
}
