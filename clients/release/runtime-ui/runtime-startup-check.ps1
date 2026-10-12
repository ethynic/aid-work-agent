param(
    [Parameter(DontShow=$true)][string]$FixtureHome,
    [Parameter(DontShow=$true)][string]$FixtureOutput
)
$ErrorActionPreference = 'Stop'
$inheritedModulePath=$env:PSModulePath
# Process-local only: Windows PowerShell must not autoload PowerShell 7 modules.
$env:PSModulePath = [IO.Path]::Combine($PSHOME, 'Modules')
$homePath = [IO.Path]::Combine($env:APPDATA, 'aidwork-tool-runtime-acceptance')
if ($FixtureHome) { $homePath = $FixtureHome }
$homePath = [IO.Path]::GetFullPath($homePath)
$report = [ordered]@{ schemaVersion=2; collectedAtUtc=[DateTime]::UtcNow.ToString('o'); scope='acceptance-startup-read-only'; config=$null; managementSecret=$null; managementOperations=$null; pairingPending=$null; pluginInventory=$null; fileSystem=$null; dpapiRoundTrip=$false; runningMarkerExists=$false; candidateFailures=@(); errors=@(); limitations=@('PowerShell JSON shape checks are not direct Node JSON.parse execution','DPAPI check uses this Windows user and fixed Windows PowerShell context','Directory readability does not prove Node write permission; no write probe performed') }
$report.environment=[ordered]@{ inheritedPowerShell7Modules=$inheritedModulePath -match '(?i)PowerShell[\\/]7|pwsh'; barePowerShellResolvesToSystem=$null }
try { $resolved=(Get-Command powershell.exe -CommandType Application).Source; $expected=[IO.Path]::Combine($env:SystemRoot,'System32\WindowsPowerShell\v1.0\powershell.exe'); $report.environment.barePowerShellResolvesToSystem=[string]::Equals($resolved,$expected,[StringComparison]::OrdinalIgnoreCase) } catch { $report.errors += 'powershell-resolution-unavailable' }

function Read-LocalFile([string]$Name, [bool]$Json=$false) {
    $item = [ordered]@{ exists=$false; readable=$false; readOnly=$null; bytes=$null; utf8Bom=$false; status='missing' }
    if ($Json) { $item.jsonRootObject=$false }
    $path = [IO.Path]::Combine($homePath, $Name)
    $cursor = $path
    while ($cursor) {
        try {
            $attributes = [IO.File]::GetAttributes($cursor)
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { $item.status='linked-path'; return @{ info=$item; text=$null; value=$null } }
        } catch [IO.FileNotFoundException] {} catch [IO.DirectoryNotFoundException] {} catch { $item.status='unreadable'; return @{ info=$item; text=$null; value=$null } }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }; $cursor = $parent
    }
    try {
        $attributes = [IO.File]::GetAttributes($path)
        $item.exists=$true
        $item.readOnly=($attributes -band [IO.FileAttributes]::ReadOnly) -ne 0
        if (($attributes -band [IO.FileAttributes]::Directory) -ne 0) { $item.status='not-file'; return @{ info=$item; text=$null; value=$null } }
        $item.bytes=[IO.FileInfo]::new($path).Length
        if ($item.bytes -gt 4194304) { $item.status='oversize'; return @{ info=$item; text=$null; value=$null } }
        $bytes=[IO.File]::ReadAllBytes($path)
        $item.utf8Bom=$bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191
        $text=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)
        $item.readable=$true; $item.status='readable'
        $value=$null
        if ($Json) {
            # Node JSON.parse rejects a BOM even if PowerShell accepts it.
            if ($item.utf8Bom) { $item.status='json-bom'; return @{ info=$item; text=$null; value=$null } }
            $item.jsonRootObject=$text.TrimStart().StartsWith('{')
            try { $value=ConvertFrom-Json -InputObject $text; $item.status='json-readable' }
            catch { $item.status='json-invalid' }
        }
        return @{ info=$item; text=$text; value=$value }
    } catch [IO.FileNotFoundException] {} catch [IO.DirectoryNotFoundException] {} catch { $item.status='unreadable' }
    return @{ info=$item; text=$null; value=$null }
}
function Record([object]$Value) { return $null -ne $Value -and $Value -is [pscustomobject] }
function Has([object]$Value,[string]$Name) { return (Record $Value) -and $Value.PSObject.Properties.Name -ccontains $Name }
function Js-Truthy([object]$Value) {
    if ($null -eq $Value) { return $false }
    if ($Value -is [bool]) { return $Value }
    if ($Value -is [string]) { return $Value.Length -gt 0 }
    if ($Value -is [int] -or $Value -is [long] -or $Value -is [double] -or $Value -is [decimal]) { return [double]$Value -ne 0 -and -not [double]::IsNaN([double]$Value) }
    return $true
}
function Only([object]$Value,[string[]]$Keys) { if (-not (Record $Value)) { return $false }; foreach ($key in $Value.PSObject.Properties.Name) { if ($key -cnotin $Keys) { return $false } }; return $true }
function Valid-DeviceConfig([object]$Value) {
    if (-not (Only $Value @('server','device_id','name','bossCliEntry','providers','sessionTasks','skills')) -or -not (Has $Value 'server') -or -not (Has $Value 'device_id') -or $Value.server -isnot [string] -or -not $Value.server -or $Value.device_id -isnot [string] -or -not $Value.device_id) { return $false }
    try { $uri=[Uri]::new($Value.server); if (-not $uri.IsAbsoluteUri -or $uri.Scheme -cnotin @('http','https') -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) { return $false } } catch { return $false }
    foreach ($key in @('name','bossCliEntry')) { if ((Has $Value $key) -and $Value.$key -isnot [string]) { return $false } }
    if ((Has $Value 'sessionTasks') -and $Value.sessionTasks -isnot [bool]) { return $false }
    if (Has $Value 'providers') {
        if (-not (Record $Value.providers)) { return $false }
        foreach ($property in $Value.providers.PSObject.Properties) {
            $p=$property.Value
            if (-not (Only $p @('entry','v2Send')) -or -not (Has $p 'entry') -or $p.entry -isnot [string] -or ((Has $p 'v2Send') -and $p.v2Send -isnot [bool])) { return $false }
        }
    }
    if (Has $Value 'skills') { if (-not (Only $Value.skills @('dir','python'))) { return $false }; foreach ($p in $Value.skills.PSObject.Properties) { if ($p.Value -isnot [string]) { return $false } } }
    return $true
}

$config=Read-LocalFile 'config.json' $true
$report.config=$config.info
$report.config.validDeviceConfig=$config.info.jsonRootObject -and (Record $config.value) -and (Has $config.value 'server') -and (Has $config.value 'device_id') -and $config.value.server -is [string] -and $config.value.server.Length -gt 0 -and $config.value.device_id -is [string] -and $config.value.device_id.Length -gt 0
$secret=Read-LocalFile 'management-secret.bin'
$report.managementSecret=$secret.info
$report.managementSecret.base64Valid=$false; $report.managementSecret.decryptable=$false; $report.managementSecret.validPlainFormat=$false
try {
    Add-Type -AssemblyName System.Security
    $known=[Text.Encoding]::UTF8.GetBytes('AidWorkReadOnlyDpapiProbe')
    $sealed=[Security.Cryptography.ProtectedData]::Protect($known,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
    $opened=[Security.Cryptography.ProtectedData]::Unprotect($sealed,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
    $report.dpapiRoundTrip=[Convert]::ToBase64String($opened) -ceq [Convert]::ToBase64String($known)
} catch { $report.errors += 'dpapi-api-unavailable' }
if ($secret.text) {
    $plain=$null; $sealed=$null
    try {
        $sealed=[Convert]::FromBase64String($secret.text.Trim())
        $report.managementSecret.base64Valid=$true
        $plain=[Security.Cryptography.ProtectedData]::Unprotect($sealed,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
        $report.managementSecret.decryptable=$true
        $report.managementSecret.validPlainFormat=[Text.UTF8Encoding]::new($false,$true).GetString($plain) -cmatch '\A[0-9a-f]{64}\z'
    } catch { $report.errors += 'management-secret-validation-failed' }
    finally { if ($null -ne $plain) { [Array]::Clear($plain,0,$plain.Length) }; $plain=$null; $sealed=$null }
}
$operations=Read-LocalFile 'management-operations.json' $true
$report.managementOperations=$operations.info
$report.managementOperations.invalidEntries=0; $report.managementOperations.totalEntries=0
$report.managementOperations.schemaValid=$false
$report.managementOperations.schemaEvaluated=$operations.info.status -cin @('json-readable','json-invalid','json-bom')
if ($operations.info.jsonRootObject -and (Record $operations.value)) {
    foreach ($property in $operations.value.PSObject.Properties) {
        $report.managementOperations.totalEntries++
        $entry=$property.Value; $op=$null
        if (Has $entry 'operation') { $op=$entry.operation }
        $valid=$property.Name.Length -gt 0 -and (Has $entry 'digest') -and $entry.digest -is [string] -and $entry.digest -cmatch '\A[0-9a-f]{64}\z' -and (Record $op)
        foreach ($field in @('operation_id','operation','status','code','error')) { $valid=$valid -and (Has $op $field) }
        if ($valid) {
            $numeric=$op.code -is [int] -or $op.code -is [long] -or $op.code -is [double] -or $op.code -is [decimal]
            $valid=$op.operation_id -is [string] -and $op.operation_id.Length -gt 0 -and $op.operation -cin @('start','stop','pair','import','enable','disable','uninstall') -and $op.status -cin @('running','succeeded','failed','reconciling') -and $numeric -and -not [double]::IsInfinity([double]$op.code) -and [double]$op.code -eq [Math]::Truncate([double]$op.code) -and $op.error -is [string]
            if ($valid) { if ($op.status -ceq 'failed') { $valid=$op.code -ge 1 -and $op.error.Length -gt 0 } else { $valid=$op.code -eq 0 -and $op.error -ceq '' } }
            if ($valid -and (Has $entry 'prepared_import') -and (Js-Truthy $entry.prepared_import)) {
                $p=$entry.prepared_import
                $valid=$op.operation -ceq 'import' -and (Has $p 'snapshot_id') -and (Has $p 'sha256') -and $p.snapshot_id -is [string] -and $p.snapshot_id -cmatch '\A[0-9a-f-]{36}\z' -and $p.sha256 -is [string] -and $p.sha256 -cmatch '\A[0-9a-f]{64}\z'
            }
        }
        if (-not $valid) { $report.managementOperations.invalidEntries++ }
    }
    $report.managementOperations.schemaValid=$report.managementOperations.invalidEntries -eq 0
}
$pending=Read-LocalFile 'pairing.pending.json' $true
$report.pairingPending=$pending.info
$report.pairingPending.validStructure=$false; $report.pairingPending.unpair=$false; $report.pairingPending.credentialDecryptable=$false
if ($pending.info.exists -and $pending.info.jsonRootObject -and (Record $pending.value)) {
    $value=$pending.value
    if ((Only $value @('action')) -and (Has $value 'action') -and $value.action -ceq 'unpair') { $report.pairingPending.validStructure=$true; $report.pairingPending.unpair=$true }
    elseif ((Only $value @('config','encrypted_token')) -and (Has $value 'config') -and (Valid-DeviceConfig $value.config) -and (Has $value 'encrypted_token') -and $value.encrypted_token -is [string] -and $value.encrypted_token) {
        $plain=$null
        try {
            $sealed=[Convert]::FromBase64String($value.encrypted_token)
            if ([Convert]::ToBase64String($sealed) -ceq $value.encrypted_token) {
                $report.pairingPending.validStructure=$true
                $plain=[Security.Cryptography.ProtectedData]::Unprotect($sealed,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
                $report.pairingPending.credentialDecryptable=$plain.Length -gt 0
            }
        } catch {} finally { if ($null -ne $plain) { [Array]::Clear($plain,0,$plain.Length) }; $plain=$null; $sealed=$null }
    }
}
$report.pluginInventory=(Read-LocalFile 'plugins\inventory.json' $true).info
$report.runningMarkerExists=(Read-LocalFile 'host-running.json').info.exists
$report.fileSystem=[ordered]@{ homeAttributesReadable=$false; homeLinked=$null; freeBytes=$null; writeAccessTested=$false }
try { $attributes=[IO.File]::GetAttributes($homePath); $report.fileSystem.homeAttributesReadable=$true; $report.fileSystem.homeLinked=($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0; $drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($homePath)); $report.fileSystem.freeBytes=$drive.AvailableFreeSpace } catch { $report.errors += 'home-filesystem-metadata-unavailable' }
if ($report.pairingPending.exists -and -not $report.pairingPending.validStructure) {
    if ($report.pairingPending.status -cin @('json-readable','json-invalid','json-bom')) { $report.candidateFailures += 'pairing-pending-structure-invalid' }
    else { $report.candidateFailures += 'pairing-pending-unchecked' }
}
if ($report.pairingPending.validStructure -and -not $report.pairingPending.unpair -and -not $report.pairingPending.credentialDecryptable) { $report.candidateFailures += 'pairing-pending-credential-unavailable' }
if ($report.managementOperations.exists -and -not $report.managementSecret.exists -and $report.managementSecret.status -eq 'missing') { $report.candidateFailures += 'management-secret-missing-with-operations' }
if ($report.managementSecret.status -eq 'readable' -and -not $report.managementSecret.validPlainFormat) { $report.candidateFailures += 'management-secret-unavailable' }
elseif ($report.managementSecret.status -ne 'missing' -and $report.managementSecret.status -ne 'readable') { $report.candidateFailures += 'management-secret-unchecked' }
if ($report.managementOperations.exists -and $report.managementOperations.schemaEvaluated -and -not $report.managementOperations.schemaValid) { $report.candidateFailures += 'management-operation-store-invalid' }
elseif ($report.managementOperations.status -ne 'missing' -and -not $report.managementOperations.schemaEvaluated) { $report.candidateFailures += 'management-operation-store-unchecked' }
if (-not $report.dpapiRoundTrip) { $report.candidateFailures += 'current-user-dpapi-unavailable' }
# Never include file contents, property names, paths, exception messages or secrets.
$outputDir=[Environment]::GetFolderPath('Desktop')
if ($FixtureOutput) { $outputDir=$FixtureOutput }
$outputPath=[IO.Path]::Combine($outputDir,('Runtime启动检查-'+[DateTime]::Now.ToString('yyyyMMdd-HHmmss')+'.json'))
[IO.File]::WriteAllText($outputPath,($report|ConvertTo-Json -Depth 7),[Text.UTF8Encoding]::new($false))
[Console]::Out.WriteLine('Read-only startup check completed. A JSON summary is on the Desktop.')
