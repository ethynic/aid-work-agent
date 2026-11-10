[CmdletBinding()]
param(
    [ValidateSet('acceptance', 'production')][string]$Profile = 'acceptance',
    [Guid]$ExpectedDeviceId = [Guid]::Empty
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$appData = [IO.Path]::GetFullPath($env:APPDATA)
$suffix = if ($Profile -eq 'acceptance') { '-acceptance' } else { '' }
$userData = Join-Path $appData ('aidwork-runtime-app' + $suffix)
$metadata = Join-Path $userData 'installation-v2'
$legacyHome = Join-Path $appData ('aidwork-tool-runtime' + $suffix)

function Read-LocalFile([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    if (-not $absolute.StartsWith($appData + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'outside-profile'
    }
    $item = Get-Item -LiteralPath $absolute -Force
    if ($item.PSIsContainer -or $item.Length -gt 65536) { throw 'invalid-file' }
    for ($parent = $item; $null -ne $parent; $parent = Get-Item -LiteralPath (Split-Path -Parent $parent.FullName) -Force) {
        if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'linked-path' }
        if ($parent.FullName -eq $appData) { break }
    }
    return [IO.File]::ReadAllText($absolute)
}

function Unprotect-Text([string]$Value) {
    $cipher = [Convert]::FromBase64String($Value)
    $bytes = [Security.Cryptography.ProtectedData]::Unprotect(
        $cipher, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    return [Text.Encoding]::UTF8.GetString($bytes)
}

function Read-Protected([string]$Path) {
    return Unprotect-Text (Read-LocalFile $Path)
}

function Get-Origin([string]$Server) {
    $uri = $null
    if ([Uri]::TryCreate($Server, [UriKind]::Absolute, [ref]$uri) -and
        $uri.Scheme -in @('https', 'http') -and -not $uri.UserInfo) {
        return $uri.GetLeftPart([UriPartial]::Authority)
    }
    return $null
}

function Get-Identity([string]$Source, $Config, [string]$Token) {
    $device = [Guid]::Empty
    if (-not [Guid]::TryParse([string]$Config.device_id, [ref]$device)) { throw 'invalid-device' }
    $origin = Get-Origin $Config.server
    if (-not $origin -or $Token -notmatch '^[0-9a-f]{64}$') { throw 'invalid-identity' }
    return [ordered]@{
        source = $Source
        status = 'readable'
        devicePrefix = $device.ToString().Substring(0, 8)
        matchesExpectedDevice = if ($ExpectedDeviceId -ne [Guid]::Empty) { $device -eq $ExpectedDeviceId } else { $null }
        origin = $origin
    }
}

function Resolve-RuntimeHome([string]$HomePath) {
    $absolute = [IO.Path]::GetFullPath($HomePath)
    $root = [Regex]::Escape($userData)
    if ($absolute -ne $legacyHome -and $absolute -ne (Join-Path $userData 'rt') -and
        $absolute -notmatch "^$root\\(?:installation-v2\\generations\\[0-9a-f-]{36}\\)?connections\\[0-9a-f-]{36}$") {
        throw 'unexpected-home'
    }
    return $absolute
}

function Read-HomeIdentity([string]$Source, [string]$HomePath) {
    if (-not $HomePath) { return @{ source = $Source; status = 'no-home' } }
    try {
        $absolute = Resolve-RuntimeHome $HomePath
        $configFile = Join-Path $absolute 'config.json'
        $credentials = Join-Path $absolute 'credentials.bin'
        if (-not (Test-Path -LiteralPath $configFile) -or -not (Test-Path -LiteralPath $credentials)) {
            return @{ source = $Source; status = 'identity-files-missing' }
        }
        return Get-Identity $Source ((Read-LocalFile $configFile) | ConvertFrom-Json) (Read-Protected $credentials)
    } catch { return @{ source = $Source; status = 'identity-unreadable-or-invalid' } }
}

function Read-HomeCreatedUtc([string]$HomePath) {
    if (-not $HomePath) { return $null }
    try {
        $folder = Get-Item -LiteralPath (Resolve-RuntimeHome $HomePath) -Force
        if (-not $folder.PSIsContainer) { return $null }
        for ($parent = $folder; $null -ne $parent; $parent = $parent.Parent) {
            if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) { return $null }
            if ($parent.FullName -eq $appData) { return $folder.CreationTimeUtc.ToString('o') }
        }
    } catch { return $null }
    return $null
}

function Read-RegistrationSummary([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return @{ status = 'missing' } }
    try {
        $record = (Read-Protected $Path) | ConvertFrom-Json
        $origin = Get-Origin $record.server
        if ($record.version -ne 1 -or $record.request.pairing_protocol -ne 3 -or
            $record.phase -notin @('requested', 'paired', 'installed') -or -not $origin) {
            throw 'invalid-pairing-record'
        }
        $version = [string]$record.request.runtime_version
        $registrationId = [Guid]::Empty
        $hasRegistrationId = [Guid]::TryParse([string]$record.request.registration_id, [ref]$registrationId)
        $item = Get-Item -LiteralPath $Path -Force
        $previousWrite = $earliestArchivedWrite[$registrationId]
        return [ordered]@{
            status = 'readable'
            phase = $record.phase
            origin = $origin
            homeAllocated = [bool]$record.home
            writerRuntimeVersion = if ($version -cmatch '^[0-9]+\.[0-9]+\.[0-9]+$') { $version } else { $null }
            fileCreatedUtc = $item.CreationTimeUtc.ToString('o')
            fileLastWriteUtc = $item.LastWriteTimeUtc.ToString('o')
            allocatedHomeCreatedUtc = Read-HomeCreatedUtc $record.home
            earliestMatchingArchiveLastWriteUtc = if ($previousWrite) { $previousWrite.ToString('o') } else { $null }
            matchesCompletedRegistration = if ($hasRegistrationId -and $completedRegistrationIds.Count -gt 0) {
                $completedRegistrationIds.Contains($registrationId)
            } else { $null }
        }
    } catch { return @{ status = 'unreadable-or-invalid' } }
}
$pointer = $null
$pointerStatus = 'missing'
$pointerFile = Join-Path $metadata 'current.json'
if (Test-Path -LiteralPath $pointerFile) {
    try {
        $pointer = (Read-LocalFile $pointerFile) | ConvertFrom-Json
        if ($pointer.version -ne 2) { throw 'invalid-pointer' }
        $pointerStatus = 'readable'
    } catch { $pointer = $null; $pointerStatus = 'unreadable-or-invalid' }
}

$candidates = @(
    Read-HomeIdentity 'active-home' $pointer.home
    Read-HomeIdentity 'prior-home' $pointer.priorHome
    Read-HomeIdentity 'legacy-home' $legacyHome
)
$activeDeviceId = $null
$hostFiles = $null
$executionEvidence = $null
if ($pointer.home) {
    try {
        $activeHome = Resolve-RuntimeHome $pointer.home
        $activeConfig = (Read-LocalFile (Join-Path $activeHome 'config.json')) | ConvertFrom-Json
        $activeDeviceId = [string]$activeConfig.device_id
        $hostFiles = [ordered]@{
            pairingWritePending = Test-Path -LiteralPath (Join-Path $activeHome 'pairing.pending.json')
            legacyConnectionState = Test-Path -LiteralPath (Join-Path ($activeHome + '.connection-registration') 'state.bin')
            runningMarker = Test-Path -LiteralPath (Join-Path $activeHome 'host-running.json')
            managementSecret = Test-Path -LiteralPath (Join-Path $activeHome 'management-secret.bin')
            managementOperations = Test-Path -LiteralPath (Join-Path $activeHome 'management-operations.json')
        }
    } catch { $hostFiles = $null }
}
if ($hostFiles) {
    $executionEvidence = [ordered]@{ marker = $null; operations = @(); operationsStatus = 'missing' }
    if ($hostFiles.runningMarker) {
        try {
            $markerFile = Join-Path $activeHome 'host-running.json'
            $marker = (Read-LocalFile $markerFile) | ConvertFrom-Json
            $modified = (Get-Item -LiteralPath $markerFile -Force).LastWriteTimeUtc
            $executionEvidence.marker = [ordered]@{
                status = 'readable'
                schemaVersion = if ($marker.schemaVersion -eq 2) { 2 } elseif ($marker.instance_id) { 1 } else { $null }
                lastWriteUtc = $modified.ToString('o')
                olderThanWindowsBoot = $null
            }
            try {
                $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
                if ($boot) { $executionEvidence.marker.olderThanWindowsBoot = $modified -lt $boot.ToUniversalTime() }
            } catch { }
        } catch { $executionEvidence.marker = @{ status = 'unreadable-or-invalid' } }
    }
    if ($hostFiles.managementOperations) {
        try {
            $operationsFile = Join-Path $activeHome 'management-operations.json'
            $records = (Read-LocalFile $operationsFile) | ConvertFrom-Json
            $executionEvidence.operations = @($records.PSObject.Properties | ForEach-Object { $_.Value.operation } |
                Where-Object { $_.operation -in @('start', 'stop') -and $_.status -in @('running', 'succeeded', 'failed', 'reconciling') } |
                Select-Object -Last 8 | ForEach-Object {
                    $message = [string]$_.message + [string]$_.error
                    $reason = switch -Regex ($message) {
                        '^旧执行尚未确认停止' { 'previous-execution-not-confirmed-stopped'; break }
                        '^执行停止事实尚需核对' { 'stop-not-confirmed'; break }
                        '^启动未完成' { 'start-not-completed'; break }
                        '^设备凭证不可用' { 'device-credential-unavailable'; break }
                        '^重启恢复已登记' { 'reboot-recovery-pending'; break }
                        '^Runtime尚未安全停止' { 'start-blocked-by-stop-state'; break }
                        '^$' { $null; break }
                        default { 'other-local-error' }
                    }
                    @{ operation = $_.operation; status = $_.status; code = [int]$_.code; reason = $reason }
                })
            $executionEvidence.operationsStatus = 'readable'
            $executionEvidence.operationsLastWriteUtc = (Get-Item -LiteralPath $operationsFile -Force).LastWriteTimeUtc.ToString('o')
        } catch { $executionEvidence.operationsStatus = 'unreadable-or-invalid' }
    }
}
$legacyReplacement = @{ status = 'missing' }
$legacyReplacementFile = Join-Path $metadata 'legacy-replacement-pending.json'
if (Test-Path -LiteralPath $legacyReplacementFile) {
    try {
        $legacy = (Read-LocalFile $legacyReplacementFile) | ConvertFrom-Json
        $legacyId = [Guid]::Empty
        if ($legacy.version -ne 1 -or -not [Guid]::TryParse([string]$legacy.device_id, [ref]$legacyId)) {
            throw 'invalid-legacy-replacement'
        }
        $legacyReplacement = [ordered]@{
            status = 'readable'
            matchesActiveDevice = if ($activeDeviceId) { $legacyId.ToString() -eq $activeDeviceId } else { $null }
            replacementPending = $legacy.replacement_pending
        }
    } catch { $legacyReplacement = @{ status = 'unreadable-or-invalid' } }
}
$runtimeProcesses = @()
$detectedProcesses = @()
try {
    $detectedProcesses = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'AID Work Runtime%.exe' OR Name = 'node.exe'" |
        Where-Object { $_.Name -like 'AID Work Runtime*.exe' -or
            ($_.CommandLine -match '[\\/]runtime[\\/]host[\\/]managed-entry\.js' -and
             $_.CommandLine -match '--supervisor=runtime_app') })
    $runtimeProcesses = @($detectedProcesses | ForEach-Object {
            $role = if ($_.Name -eq 'node.exe') { 'host' } else { 'desktop' }
            $started = if ($_.CreationDate) { $_.CreationDate.ToUniversalTime().ToString('o') } else { $null }
            @{ role = $role; pid = $_.ProcessId; parentPid = $_.ParentProcessId; startedUtc = $started }
        })
} catch { $runtimeProcesses = $null }
if ($executionEvidence -and $executionEvidence.marker -and $executionEvidence.marker.schemaVersion -eq 2) {
    $supervision = [ordered]@{ proofStatus = 'unreadable-or-invalid'; nativeBootProbe = 'host-not-identified' }
    $executionEvidence.supervision = $supervision
    try {
        $proof = (Unprotect-Text $marker.supervision) | ConvertFrom-Json
        $proofBoot = [Guid]::Empty
        if (-not [Guid]::TryParse([string]$proof.bootId, [ref]$proofBoot) -or
            $proof.generation -cne $marker.instance_id -or $proof.home -cne $activeHome -or
            $proof.rebootOnly -isnot [bool]) { throw 'invalid-supervision-binding' }
        $supervision.proofStatus = 'matches-active-home'
        $supervision.rebootOnly = $proof.rebootOnly
        $hosts = @($detectedProcesses | Where-Object { $_.Name -eq 'node.exe' })
        if ($hosts.Count -eq 1 -and $hosts[0].ExecutablePath -match '[\\/]resources[\\/]runtime[\\/]node[\\/]win-x64[\\/]node\.exe$') {
            $supervision.sessionMatchesHost = $proof.sessionId -eq $hosts[0].SessionId
            $native = Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $hosts[0].ExecutablePath))) 'host\native\RuntimeProcessSupervisor.exe'
            $supervision.nativeBootProbe = 'missing'
            if (Test-Path -LiteralPath $native -PathType Leaf) {
                $supervision.nativeBootProbe = 'unverified-binary'
                $sha256 = [Security.Cryptography.SHA256]::Create()
                try { $nativeHash = [BitConverter]::ToString($sha256.ComputeHash([IO.File]::ReadAllBytes($native))).Replace('-', '') }
                finally { $sha256.Dispose() }
                if ($nativeHash -eq 'CB56DBD00CD00B2444A28DC426824C82D6FDB8C7AB8E765CCC693DBFFAD64F74') {
                    $supervision.nativeBootProbe = 'failed'
                    $current = (& $native boot-id 2>$null) | ConvertFrom-Json
                    $currentBoot = [Guid]::Empty
                    if ($LASTEXITCODE -eq 0 -and [Guid]::TryParse([string]$current.bootId, [ref]$currentBoot)) {
                        $supervision.nativeBootProbe = 'ok'
                        $supervision.bootMatchesMarker = $current.bootId -ceq $proof.bootId
                    }
                }
            }
        }
    } catch { }
}
$proofFile = Join-Path $metadata 'previous-identity.bin'
if (Test-Path -LiteralPath $proofFile) {
    try {
        $proof = (Read-Protected $proofFile) | ConvertFrom-Json
        $candidates += Get-Identity 'migration-proof' $proof.config $proof.token
    } catch { $candidates += @{ source = 'migration-proof'; status = 'identity-unreadable-or-invalid' } }
}
$completedRegistrationIds = [System.Collections.Generic.HashSet[Guid]]::new()
$earliestArchivedWrite = @{}
$quarantined = @()
if (Test-Path -LiteralPath $metadata -PathType Container) {
    foreach ($file in Get-ChildItem -LiteralPath $metadata -File -Filter 'previous-registration-*.bin') {
        try {
            $archive = (Read-Protected $file.FullName) | ConvertFrom-Json
            $registrationId = [Guid]::Empty
            if ($archive.version -eq 1 -and $archive.request.pairing_protocol -eq 3 -and
                [Guid]::TryParse([string]$archive.request.registration_id, [ref]$registrationId) -and
                (-not $earliestArchivedWrite.ContainsKey($registrationId) -or
                 $file.LastWriteTimeUtc -lt $earliestArchivedWrite[$registrationId])) {
                $earliestArchivedWrite[$registrationId] = $file.LastWriteTimeUtc
            }
        } catch { continue }
    }
    foreach ($file in Get-ChildItem -LiteralPath $metadata -File -Filter 'completed-registration-*.bin') {
        try {
            $receipt = (Read-Protected $file.FullName) | ConvertFrom-Json
            if ($receipt.version -ne 1 -or $receipt.phase -ne 'installed') { throw 'invalid-receipt' }
            $candidates += Get-Identity 'completed-registration' $receipt $receipt.request.registration_token
            $registrationId = [Guid]::Empty
            if ([Guid]::TryParse([string]$receipt.request.registration_id, [ref]$registrationId)) {
                [void]$completedRegistrationIds.Add($registrationId)
            }
        } catch { $candidates += @{ source = 'completed-registration'; status = 'identity-unreadable-or-invalid' } }
    }
    $quarantined = @(Get-ChildItem -LiteralPath $metadata -File -Filter 'quarantined-clean-registration-*.bin' |
        Sort-Object LastWriteTimeUtc -Descending | ForEach-Object { Read-RegistrationSummary $_.FullName })
}
$pending = Read-RegistrationSummary (Join-Path $metadata 'clean-registration.bin')

[ordered]@{
    profile = $Profile
    pending = $pending
    quarantined = $quarantined
    pointer = $pointerStatus
    identities = $candidates
    legacyReplacement = $legacyReplacement
    activeHostFiles = $hostFiles
    executionEvidence = $executionEvidence
    runtimeProcesses = $runtimeProcesses
} | ConvertTo-Json -Depth 5
