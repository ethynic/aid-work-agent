param(
    [string]$Mode,
    [string]$AppId,
    [string]$InstallerPath,
    [string]$InstallDirectory,
    [string]$SupervisorPath,
    # Isolated developer fixture only. Never selects the real Run/RunOnce keys.
    [Parameter(DontShow = $true)][string]$FixtureRegistryId
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$script:FailureCode = 'LEGACY_UPGRADE_FAILED'
function Fail([string]$Code) { $script:FailureCode = $Code; throw [InvalidOperationException]::new($Code) }
function Require([bool]$Condition, [string]$Code) { if (-not $Condition) { Fail $Code } }
function Safe-Path([string]$Path, [bool]$MustExist = $false, [bool]$Directory = $false) {
    Require (-not [string]::IsNullOrWhiteSpace($Path) -and $Path -match '\A[A-Za-z]:\\' -and $Path -notmatch '[\x00-\x1f"<>|?*]') 'INVALID_PATH'
    $full = [IO.Path]::GetFullPath($Path)
    Require ([string]::Equals($full, $Path, [StringComparison]::OrdinalIgnoreCase)) 'INVALID_PATH'
    Require ($full.Substring(2) -notmatch ':') 'INVALID_PATH'
    $cursor = $full
    while ($cursor) {
        try { $attributes = [IO.File]::GetAttributes($cursor) }
        catch [IO.FileNotFoundException] { $attributes = $null }
        catch [IO.DirectoryNotFoundException] { $attributes = $null }
        if ($null -ne $attributes) { Require (($attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'REPARSE_PATH_REJECTED' }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }; $cursor = $parent
    }
    if ($MustExist) {
        if ($Directory) { Require ([IO.Directory]::Exists($full)) 'DIRECTORY_MISSING' }
        else { Require ([IO.File]::Exists($full)) 'FILE_MISSING' }
    }
    return $full
}
function Ensure-Directory([string]$Path) {
    $null = Safe-Path $Path
    $null = [IO.Directory]::CreateDirectory($Path)
    $null = Safe-Path $Path $true $true
}
function Read-Bytes([string]$Path) {
    $null = Safe-Path $Path $true
    Require ([IO.FileInfo]::new($Path).Length -le 262144) 'STATE_TOO_LARGE'
    return ,([IO.File]::ReadAllBytes($Path))
}
function Hash-File([string]$Path) {
    $null = Safe-Path $Path $true
    $stream = $null
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $stream = [IO.FileStream]::new($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read, 65536, [IO.FileOptions]::SequentialScan)
        return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        $sha.Dispose()
    }
}
function Protect-Text([string]$Text) {
    return [Convert]::ToBase64String([Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($Text), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser))
}
function Read-Protected([string]$Path) {
    $bytes = Read-Bytes $Path
    $blob = $script:StrictUtf8.GetString($bytes)
    $plain = [Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($blob), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    return ($script:StrictUtf8.GetString($plain) | ConvertFrom-Json)
}
function Write-NewProtected([string]$Path, [object]$Value) {
    $null = Safe-Path $Path
    Require (-not [IO.File]::Exists($Path)) 'STATE_ALREADY_EXISTS'
    $temp = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    try {
        $security = [Security.AccessControl.FileSecurity]::new()
        $security.SetAccessRuleProtection($true, $false)
        $security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($script:Sid, [Security.AccessControl.FileSystemRights]::FullControl, [Security.AccessControl.AccessControlType]::Allow))
        $security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new([Security.Principal.WellKnownSidType]::LocalSystemSid, $null), [Security.AccessControl.FileSystemRights]::FullControl, [Security.AccessControl.AccessControlType]::Allow))
        $stream = [IO.FileStream]::new($temp, [IO.FileMode]::CreateNew, [Security.AccessControl.FileSystemRights]::Write, [IO.FileShare]::None, 4096, [IO.FileOptions]::WriteThrough, $security)
        try {
            $data = [Text.Encoding]::UTF8.GetBytes((Protect-Text ($Value | ConvertTo-Json -Depth 6 -Compress)))
            $stream.Write($data, 0, $data.Length); $stream.Flush($true)
        } finally { $stream.Dispose() }
        $null = Safe-Path $Path
        [IO.File]::Move($temp, $Path)
    } finally { if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) } }
}
function Get-RunValue([Microsoft.Win32.RegistryKey]$Key, [string]$Name) {
    if ($null -eq $Key -or $Key.GetValueNames() -notcontains $Name) { return [pscustomobject]@{ exists = $false; kind = 0; value = $null } }
    $kind = $Key.GetValueKind($Name)
    $value = $Key.GetValue($Name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    Require (($kind -eq [Microsoft.Win32.RegistryValueKind]::String -or $kind -eq [Microsoft.Win32.RegistryValueKind]::ExpandString) -and $value -is [string] -and $value.Length -le 32768) 'UNSUPPORTED_RUN_VALUE'
    return [pscustomobject]@{ exists = $true; kind = [int]$kind; value = $value }
}
function Valid-RunValue([object]$Value) {
    Require ($null -ne $Value -and $Value.PSObject.Properties.Name -contains 'exists' -and $Value.exists -is [bool]) 'PENDING_STATE_INVALID'
    Require ($Value.PSObject.Properties.Name -contains 'kind' -and $Value.PSObject.Properties.Name -contains 'value') 'PENDING_STATE_INVALID'
    if ($Value.exists) { Require ($Value.kind -in @(1, 2) -and $Value.value -is [string] -and $Value.value.Length -le 32768) 'PENDING_STATE_INVALID' }
    else { Require ($Value.kind -eq 0 -and $null -eq $Value.value) 'PENDING_STATE_INVALID' }
}
function Set-RunValue([Microsoft.Win32.RegistryKey]$Key, [string]$Name, [object]$Value) {
    Valid-RunValue $Value
    if ($Value.exists) { $Key.SetValue($Name, $Value.value, [Microsoft.Win32.RegistryValueKind]$Value.kind) }
    else { $Key.DeleteValue($Name, $false) }
}
function Read-Pending([string]$Path) {
    $state = Read-Protected $Path
    Require ($state.PSObject.Properties.Name -contains 'schemaVersion' -and $state.schemaVersion -eq 1) 'PENDING_STATE_INVALID'
    Require ($state.appId -ceq $AppId -and $state.home -ceq $script:RuntimeHome -and $state.userData -ceq $script:UserData -and $state.installDirectory -ceq $InstallDirectory) 'PENDING_STATE_SCOPE_MISMATCH'
    Require ($state.bootId -match $script:UuidPattern -and $state.installerHash -match '\A[0-9a-f]{64}\z') 'PENDING_STATE_INVALID'
    Valid-RunValue $state.originalRun
    return $state
}
function Archive-PendingDirectory([string]$Directory) {
    $null = Safe-Path $Directory $true $true
    $history = [IO.Path]::Combine($script:UserData, 'upgrade-history')
    Ensure-Directory $history
    $destination = Safe-Path ([IO.Path]::Combine($history, [Guid]::NewGuid().ToString('N')))
    [IO.Directory]::Move($Directory, $destination)
}

$mutex = $null; $locked = $false; $run = $null; $runOnce = $null
$registryTouched = $false; $newWitness = $false; $newState = $false; $newInstaller = $false
$archiveCommitted = $false; $archiveRollbackRun = $null; $archiveRollbackOnce = $null
$stateFile = $null; $witnessFile = $null; $pendingInstaller = $null
try {
    Require (@('Prepare', 'PrepareDeferred', 'Restore', 'Cancel') -ccontains $Mode) 'INVALID_MODE'
    $identities = @{
        'cn.aidingyi.agent.runtime' = @{ home = 'aidwork-tool-runtime'; userData = 'aidwork-runtime-app' }
        'cn.aidingyi.agent.runtime.acceptance' = @{ home = 'aidwork-tool-runtime-acceptance'; userData = 'aidwork-runtime-app-acceptance' }
    }
    Require (@('cn.aidingyi.agent.runtime', 'cn.aidingyi.agent.runtime.acceptance') -ccontains $AppId) 'INVALID_APP_ID'
    $script:UuidPattern = '\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\z'
    if ($FixtureRegistryId) { Require ($FixtureRegistryId -match $script:UuidPattern) 'INVALID_FIXTURE_ID' }
    Add-Type -AssemblyName System.Security
    $script:StrictUtf8 = [Text.UTF8Encoding]::new($false, $true)
    $script:Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $appData = Safe-Path $env:APPDATA $true $true
    $script:RuntimeHome = Safe-Path ([IO.Path]::Combine($appData, $identities[$AppId].home))
    $script:UserData = Safe-Path ([IO.Path]::Combine($appData, $identities[$AppId].userData))
    if ($Mode -in @('Prepare', 'PrepareDeferred')) { $InstallDirectory = Safe-Path $InstallDirectory $true $true }
    $mutexName = 'Local\AidWorkRuntimeUpgrade.' + $script:Sid.Value + '.' + $AppId
    if ($FixtureRegistryId) { $mutexName += '.' + $FixtureRegistryId }
    $mutex = [Threading.Mutex]::new($false, $mutexName)
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    Require $locked 'UPGRADE_BUSY'
    $pendingDirectory = [IO.Path]::Combine($script:UserData, 'upgrade-pending')
    $stateFile = Safe-Path ([IO.Path]::Combine($pendingDirectory, 'state.bin'))
    $pendingInstaller = Safe-Path ([IO.Path]::Combine($pendingDirectory, 'installer.exe'))
    $state = $null
    if ([IO.File]::Exists($stateFile)) {
        $InstallDirectory = Safe-Path $InstallDirectory $true $true
        $state = Read-Pending $stateFile
    }
    if ($Mode -notin @('Prepare', 'PrepareDeferred') -and $null -eq $state) { [Console]::Out.WriteLine('NO_PENDING'); exit 0 }
    $base = 'Software\Microsoft\Windows\CurrentVersion'
    if ($FixtureRegistryId) { $base = 'Software\AidWorkRuntimeUpgradeFixtures\' + $FixtureRegistryId }
    $run = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($base + '\Run')
    $runOnce = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($base + '\RunOnce')
    $beforeRun = Get-RunValue $run $AppId
    $onceName = $AppId + '.upgrade'
    $beforeOnce = Get-RunValue $runOnce $onceName
    if ($Mode -ceq 'Restore' -or $Mode -ceq 'Cancel') {
        $expectedCommand = '"' + $pendingInstaller + '" /S --force-run /D=' + $InstallDirectory
        Require (-not $beforeOnce.exists -or ($beforeOnce.kind -eq 1 -and $beforeOnce.value -ceq $expectedCommand)) 'RUNONCE_CONFLICT'
        if ($Mode -ceq 'Restore') {
            Require ([IO.File]::Exists($pendingInstaller) -and (Hash-File $pendingInstaller) -ceq $state.installerHash) 'PENDING_INSTALLER_MISMATCH'
            $archive = [IO.Path]::Combine($pendingDirectory, 'archive')
            Ensure-Directory $archive
            $archiveFile = Safe-Path ([IO.Path]::Combine($archive, 'state-' + [Guid]::NewGuid().ToString('N') + '.bin'))
        }
        $registryTouched = $true
        Set-RunValue $run $AppId $state.originalRun
        $runOnce.DeleteValue($onceName, $false)
        $run.Flush(); $runOnce.Flush()
        if ($Mode -ceq 'Cancel') {
            # Failed/cancelled installer: restore startup, retain bound retry evidence.
            [Console]::Out.WriteLine('CANCELLED'); exit 0
        } else {
            [IO.File]::Move($stateFile, $archiveFile)
            [Console]::Out.WriteLine('RESTORED'); exit 0
        }
    }

    $InstallerPath = Safe-Path $InstallerPath $true
    Require ([IO.Path]::GetExtension($InstallerPath) -ieq '.exe') 'INVALID_INSTALLER'
    $SupervisorPath = Safe-Path $SupervisorPath $true
    Require ([IO.Path]::GetFileName($SupervisorPath) -ceq 'RuntimeProcessSupervisor.exe') 'INVALID_SUPERVISOR'
    $bootOutput = & $SupervisorPath boot-id 2>$null
    Require ($LASTEXITCODE -eq 0) 'BOOT_ID_UNAVAILABLE'
    $boot = ($bootOutput -join '') | ConvertFrom-Json
    Require (@($boot.PSObject.Properties.Name).Count -eq 1 -and $boot.bootId -match $script:UuidPattern) 'BOOT_ID_INVALID'
    # Deferred scheduling owns startup/installer control only. It never treats a
    # missing/corrupt/v2 marker as a stopped resource or creates a business ACK.
    if ($Mode -ceq 'Prepare') {
    $markerFile = [IO.Path]::Combine($script:RuntimeHome, 'host-running.json')
    try { $null = [IO.File]::GetAttributes($markerFile) }
    catch [IO.FileNotFoundException] { Fail 'NO_LEGACY_MARKER' }
    catch [IO.DirectoryNotFoundException] { Fail 'NO_LEGACY_MARKER' }
    $markerBytes = Read-Bytes $markerFile
    $markerRaw = $script:StrictUtf8.GetString($markerBytes)
    $markerPattern = '\A[ \t\r\n]*\{[ \t\r\n]*"instance_id"[ \t\r\n]*:[ \t\r\n]*"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"[ \t\r\n]*\}[ \t\r\n]*\z'
    Require ([regex]::IsMatch($markerRaw, $markerPattern)) 'UNSUPPORTED_LEGACY_MARKER'
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = ([BitConverter]::ToString($sha.ComputeHash($markerBytes))).Replace('-', '').ToLowerInvariant() } finally { $sha.Dispose() }
    $supervision = [IO.Path]::Combine($script:RuntimeHome, 'process-supervision')
    Ensure-Directory $supervision
    $witnessFile = Safe-Path ([IO.Path]::Combine($supervision, 'legacy-' + $digest + '.bin'))
    if ([IO.File]::Exists($witnessFile)) {
        $witness = Read-Protected $witnessFile
        Require ($witness.home -ceq $script:RuntimeHome -and $witness.digest -ceq $digest -and $witness.bootId -match $script:UuidPattern) 'LEGACY_WITNESS_INVALID'
    } else {
        Write-NewProtected $witnessFile @{ home = $script:RuntimeHome; digest = $digest; bootId = $boot.bootId }
        $newWitness = $true
    }
    }
    Ensure-Directory $pendingDirectory
    $installerHash = Hash-File $InstallerPath
    if ($null -ne $state -and $state.installerHash -cne $installerHash) {
        # A failed/cancelled earlier revision may retain its protected restart proof.
        # Validate it, restore the FIRST login choice, then archive the whole control
        # directory before preparing the new revision. Business facts are untouched.
        Require ([IO.File]::Exists($pendingInstaller) -and (Hash-File $pendingInstaller) -ceq $state.installerHash) 'PENDING_INSTALLER_MISMATCH'
        $oldCommand = '"' + $pendingInstaller + '" /S --force-run /D=' + $InstallDirectory
        Require (-not $beforeOnce.exists -or ($beforeOnce.kind -eq 1 -and $beforeOnce.value -ceq $oldCommand)) 'RUNONCE_CONFLICT'
        $archiveRollbackRun = $state.originalRun
        $archiveRollbackOnce = [pscustomobject]@{ exists = $false; kind = 0; value = $null }
        $registryTouched = $true
        Set-RunValue $run $AppId $archiveRollbackRun
        $runOnce.DeleteValue($onceName, $false)
        $run.Flush(); $runOnce.Flush()
        Archive-PendingDirectory $pendingDirectory
        $archiveCommitted = $true
        # Any subsequent failure restores these values, never a moved/dead RunOnce.
        $beforeRun = $archiveRollbackRun; $beforeOnce = $archiveRollbackOnce
        $state = $null
        Ensure-Directory $pendingDirectory
    } elseif ($null -eq $state -and [IO.File]::Exists($pendingInstaller)) {
        # Restore archived state.bin but left installer.exe in r3/r4. Only matching
        # CurrentUser protected archive evidence can authorize retiring that cache.
        Require (-not $beforeOnce.exists) 'RUNONCE_CONFLICT'
        $cachedHash = Hash-File $pendingInstaller
        if ($cachedHash -cne $installerHash) {
            $archive = Safe-Path ([IO.Path]::Combine($pendingDirectory, 'archive')) $true $true
            $proofs = [IO.Directory]::GetFiles($archive, 'state-*.bin')
            Require ($proofs.Length -le 256) 'PENDING_ARCHIVE_TOO_LARGE'
            $matched = $false
            foreach ($proof in $proofs) {
                $oldState = Read-Pending $proof
                if ($oldState.installerHash -ceq $cachedHash) { $matched = $true; break }
            }
            Require $matched 'PENDING_INSTALLER_MISMATCH'
            Archive-PendingDirectory $pendingDirectory
            $archiveRollbackRun = $beforeRun; $archiveRollbackOnce = $beforeOnce
            $archiveCommitted = $true
            Ensure-Directory $pendingDirectory
        }
        # Matching the current explicit installer needs no archive: a crash after
        # copy publication but before state publication must remain resumable.
    }
    if ([IO.File]::Exists($pendingInstaller)) {
        Require ((Hash-File $pendingInstaller) -ceq $installerHash) 'PENDING_INSTALLER_MISMATCH'
    } else {
        # Copy to a scoped temporary file, check both copies, then publish atomically.
        $copyTemp = $pendingInstaller + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
        try {
            [IO.File]::Copy($InstallerPath, $copyTemp, $false)
            Require ((Hash-File $copyTemp) -ceq $installerHash -and (Hash-File $InstallerPath) -ceq $installerHash) 'INSTALLER_COPY_MISMATCH'
            $copyStream = [IO.FileStream]::new($copyTemp, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None, 4096, [IO.FileOptions]::WriteThrough)
            try { $copyStream.Flush($true) } finally { $copyStream.Dispose() }
            [IO.File]::Move($copyTemp, $pendingInstaller); $newInstaller = $true
        } finally { if ([IO.File]::Exists($copyTemp)) { [IO.File]::Delete($copyTemp) } }
    }
    $onceCommand = '"' + $pendingInstaller + '" /S --force-run /D=' + $InstallDirectory
    Require ($onceCommand.Length -le 260) 'RUNONCE_COMMAND_TOO_LONG'
    if ($null -eq $state) {
        Require (-not $beforeOnce.exists) 'RUNONCE_CONFLICT'
        Write-NewProtected $stateFile @{
            schemaVersion = 1; appId = $AppId; home = $script:RuntimeHome; userData = $script:UserData
            installDirectory = $InstallDirectory; bootId = $boot.bootId; installerHash = $installerHash; originalRun = $beforeRun
        }
        $newState = $true
    } else { Require (-not $beforeOnce.exists -or $beforeOnce.value -ceq $onceCommand) 'RUNONCE_CONFLICT' }
    # State is durable before changing startup. Repeated Prepare retains the first backup.
    $registryTouched = $true
    $run.DeleteValue($AppId, $false)
    $runOnce.SetValue($onceName, $onceCommand, [Microsoft.Win32.RegistryValueKind]::String)
    $run.Flush(); $runOnce.Flush()
    [Console]::Out.WriteLine('PREPARED'); exit 0
} catch {
    $failure = $script:FailureCode
    try {
        if ($registryTouched) {
            if ($archiveCommitted) { $beforeRun = $archiveRollbackRun; $beforeOnce = $archiveRollbackOnce }
            Set-RunValue $run $AppId $beforeRun; Set-RunValue $runOnce $onceName $beforeOnce; $run.Flush(); $runOnce.Flush()
        }
        if ($newState -and [IO.File]::Exists($stateFile)) { $null = Safe-Path $stateFile $true; [IO.File]::Delete($stateFile) }
        if ($newInstaller -and [IO.File]::Exists($pendingInstaller)) { $null = Safe-Path $pendingInstaller $true; [IO.File]::Delete($pendingInstaller) }
        if ($newWitness -and [IO.File]::Exists($witnessFile)) { $null = Safe-Path $witnessFile $true; [IO.File]::Delete($witnessFile) }
    } catch { $failure = 'UPGRADE_ROLLBACK_FAILED' }
    [Console]::Error.WriteLine($failure)
    if ($failure -in @('NO_LEGACY_MARKER', 'UNSUPPORTED_LEGACY_MARKER')) { exit 2 }
    exit 1
} finally {
    if ($null -ne $run) { $run.Dispose() }
    if ($null -ne $runOnce) { $runOnce.Dispose() }
    if ($locked -and $null -ne $mutex) { $mutex.ReleaseMutex() }
    if ($null -ne $mutex) { $mutex.Dispose() }
}
