param(
  [ValidateSet('Detect','Stop','Finalize','Verify')][string]$Mode,
  [string]$AppId,
  [string]$InstallDirectory,
  [int]$ExcludePid = 0,
  [switch]$RequireIdle,
  [switch]$Clean,
  [Parameter(DontShow = $true)][string]$FixtureRegistryId
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$failure = 'INSTALLATION_CHECK_FAILED'
function Require([bool]$Condition, [string]$Code) { if (-not $Condition) { throw [InvalidOperationException]::new($Code) } }
function SafePath([string]$Value) {
  Require ($Value -match '\A[A-Za-z]:\\' -and $Value -notmatch '[\x00-\x1f"<>|?*]' -and $Value.Substring(2) -notmatch ':') 'INVALID_ABSOLUTE_PATH'
  $full = [IO.Path]::GetFullPath($Value)
  Require ([string]::Equals($Value, $full, [StringComparison]::OrdinalIgnoreCase)) 'INVALID_ABSOLUTE_PATH'
  $cursor = $full
  while ($cursor) {
    if (Test-Path -LiteralPath $cursor) { Require (([IO.File]::GetAttributes($cursor) -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'DIRECTORY_LINK_REJECTED' }
    $cursor = [IO.Path]::GetDirectoryName($cursor)
  }
  return $full
}
function WriteAtomic([string]$File, [string]$Value) {
  $null = SafePath $File
  $temp = $File + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
  [IO.File]::WriteAllText($temp, $Value, [Text.UTF8Encoding]::new($false))
  if ([IO.File]::Exists($File)) { [IO.File]::Replace($temp, $File, [NullString]::Value) } else { [IO.File]::Move($temp, $File) }
}
try {
  $identities = @{
    'cn.aidingyi.agent.runtime' = @{home='aidwork-tool-runtime'; userData='aidwork-runtime-app'; profile='production'}
    'cn.aidingyi.agent.runtime.acceptance' = @{home='aidwork-tool-runtime-acceptance'; userData='aidwork-runtime-app-acceptance'; profile='acceptance'}
  }
  Require ($identities.ContainsKey($AppId)) 'UNKNOWN_PRODUCT'
  $identity = $identities[$AppId]
  $InstallDirectory = SafePath $InstallDirectory
  $appData = SafePath $env:APPDATA
  $userData = SafePath ([IO.Path]::Combine($appData, $identity.userData))
  $metadata = SafePath ([IO.Path]::Combine($userData, 'installation-v2'))
  $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  # A fixture chooses only an isolated HKCU namespace, never production Run keys.
  $registryBase = 'Software\Microsoft\Windows\CurrentVersion'
  if ($FixtureRegistryId) {
    Require ($AppId -eq 'cn.aidingyi.agent.runtime.acceptance') 'FIXTURE_NOT_ALLOWED_IN_PRODUCTION'
    $fixtureGuid = [Guid]::Empty
    Require ([Guid]::TryParse($FixtureRegistryId, [ref]$fixtureGuid) -and $FixtureRegistryId -ceq $fixtureGuid.ToString()) 'INVALID_FIXTURE_NAMESPACE'
    $fixtureRoot = SafePath ([IO.Path]::Combine([IO.Path]::GetTempPath(), 'aid-runtime-install-fixture-'+$FixtureRegistryId))
    Require ([string]::Equals($appData,[IO.Path]::Combine($fixtureRoot,'appData'),[StringComparison]::OrdinalIgnoreCase) -and
      [string]::Equals($InstallDirectory,[IO.Path]::Combine($fixtureRoot,'install'),[StringComparison]::OrdinalIgnoreCase)) 'FIXTURE_DIRECTORY_SCOPE_MISMATCH'
    $registryBase = 'Software\AidWorkRuntimeCleanInstallFixtures\' + $FixtureRegistryId
  }
  if ($Mode -in @('Detect','Stop')) {
    $failure = 'PROCESS_INVENTORY_FAILED'
    Import-Module (Join-Path $PSHOME 'Modules\CimCmdlets\CimCmdlets.psd1') -Force
    $all = @(Get-CimInstance -ClassName Win32_Process)
    $owned = @{}
    foreach ($p in $all) {
      if ($null -eq $p.ExecutablePath -or $null -eq $p.CommandLine) { continue }
      $owned[[int]$p.ProcessId] = $p
    }
    function IsProduct([object]$p) {
      # GUI is identified by its exact executable name AND adjacent product
      # configuration. A renamed generic node/browser is never selected.
      $exe = [string]$p.ExecutablePath
      if ([IO.Path]::GetFileName($exe) -in @('AID Work Runtime.exe','AID Work Runtime 验收版.exe')) {
        if ($FixtureRegistryId -and -not [string]::Equals([IO.Path]::GetDirectoryName($exe),$InstallDirectory,[StringComparison]::OrdinalIgnoreCase)) { return $false }
        $file = [IO.Path]::Combine([IO.Path]::GetDirectoryName($exe), 'resources\config\runtime-product.json')
        if (Test-Path -LiteralPath $file) {
          try { $c = [IO.File]::ReadAllText((SafePath $file)) | ConvertFrom-Json; return $c.productKind -eq 'runtime' -and $c.profile -eq $identity.profile } catch { return $false }
        }
        # Old production releases have no product record. Only the explicit
        # install target is trusted for this compatibility case.
        return [string]::Equals($exe, [IO.Path]::Combine($InstallDirectory,'AID Work Runtime.exe'), [StringComparison]::OrdinalIgnoreCase)
      }
      if ([IO.Path]::GetFileName($exe) -ne 'node.exe') { return $false }
      $command = [string]$p.CommandLine
      $candidates = @([IO.Path]::Combine($InstallDirectory,'resources\runtime\host\managed-entry.js'))
      if ($true) {
        # Uninstalling npm can remove these files while the process is alive.
        $candidates += [IO.Path]::Combine($appData,'npm\node_modules\agent-tool-runtime\dist\src\cli.js')
        $candidates += [IO.Path]::Combine($appData,'npm\node_modules\agent-tool-runtime\dist\cli.js')
      }
      foreach ($entry in $candidates) {
        $null = SafePath $entry
        $escaped = [Regex]::Escape($entry).Replace('\\','[\\/]')
        if ($command -match ('(?:^|\s)"?' + $escaped + '"?(?=\s|$)')) { return $true }
      }
      # A previous GUI installation may leave its Host alive at another path.
      # Verify the adjacent product profile rather than accepting any node.exe.
      if ($FixtureRegistryId) { return $false }
      foreach ($match in [Regex]::Matches($command, '(?:^|\s)"?([A-Za-z]:[\\/][^"\r\n]*?[\\/]resources[\\/]runtime[\\/]host[\\/]managed-entry\.js)"?(?=\s|$)')) {
        try {
          $entry = SafePath ($match.Groups[1].Value.Replace('/','\'))
          $root = [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($entry)))
          $c = [IO.File]::ReadAllText((SafePath ([IO.Path]::Combine($root,'config\runtime-product.json')))) | ConvertFrom-Json
          if ($c.productKind -eq 'runtime' -and $c.profile -eq $identity.profile) { return $true }
        } catch { }
      }
      return $false
    }
    $excluded = @{}; $excluded[$PID] = $true
    if ($ExcludePid -gt 0) { $excluded[$ExcludePid] = $true }
    do {
      $changed = $false
      foreach ($p in $owned.Values) { if ($excluded.ContainsKey([int]$p.ParentProcessId) -and -not $excluded.ContainsKey([int]$p.ProcessId)) { $excluded[[int]$p.ProcessId]=$true; $changed=$true } }
    } while ($changed)
    $targets = @{}
    foreach ($p in $owned.Values) {
      if (-not $excluded.ContainsKey([int]$p.ProcessId) -and (IsProduct $p)) {
        $owner = Invoke-CimMethod -InputObject $p -MethodName GetOwnerSid
        if ($owner.ReturnValue -eq 0 -and $owner.Sid -eq $sid) { $targets[[int]$p.ProcessId]=$p }
      }
    }
    do {
      $changed = $false
      foreach ($p in $owned.Values) {
        if ($targets.ContainsKey([int]$p.ParentProcessId) -and -not $targets.ContainsKey([int]$p.ProcessId) -and -not $excluded.ContainsKey([int]$p.ProcessId) -and
          $p.CreationDate -ge $targets[[int]$p.ParentProcessId].CreationDate) { $targets[[int]$p.ProcessId]=$p; $changed=$true }
      }
    } while ($changed)
    # Detection is deliberately read-only: cancellation must preserve both
    # running processes and startup/installation state.
    if ($Mode -eq 'Detect') {
      if ($targets.Count -gt 0) { Write-Output 'PROCESS_RUNNING' } else { Write-Output 'PROCESS_CLEAR' }
      exit 0
    }
    Require (-not $RequireIdle -or $targets.Count -eq 0) 'PROCESS_STARTED_DURING_INSTALL'
    $failure = 'OWNED_PROCESS_STOP_FAILED'
    # Children first. Recheck SID, creation time and executable immediately
    # before each termination so a recycled PID cannot hit an unrelated process.
    foreach ($p in @($targets.Values | Sort-Object CreationDate -Descending)) {
      $live = Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId=' + $p.ProcessId)
      if ($null -eq $live) { continue }
      $owner = Invoke-CimMethod -InputObject $live -MethodName GetOwnerSid
      if ($owner.ReturnValue -ne 0 -or $owner.Sid -ne $sid) { continue }
      Require ($owner.ReturnValue -eq 0 -and $owner.Sid -eq $sid -and $live.CreationDate -eq $p.CreationDate -and $live.ExecutablePath -eq $p.ExecutablePath) 'PROCESS_IDENTITY_CHANGED'
      Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
    }
    foreach ($p in $targets.Values) {
      $live = Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId=' + $p.ProcessId)
      if ($null -ne $live -and $live.CreationDate -eq $p.CreationDate) {
        $process = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
        if ($null -ne $process) { Require ($process.WaitForExit(5000)) 'OWNED_PROCESS_STOP_FAILED' }
      }
    }
    foreach ($subkey in @('Run','RunOnce')) {
      $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($registryBase+'\'+$subkey, $true)
      if ($null -eq $key) { continue }
      try {
        foreach ($name in $key.GetValueNames()) {
          $value = [string]$key.GetValue($name)
          $delete = $name -eq $AppId -or $name -eq ($AppId+'.upgrade')
          if (-not $delete) {
            $entry = [IO.Path]::Combine($appData,'npm\node_modules\agent-tool-runtime\dist\src\cli.js')
            $delete = $value -match ('(?:^|\s)"?'+[Regex]::Escape($entry)+'"?(?=\s|$)')
          }
          if ($delete) { $key.DeleteValue($name,$false) }
        }
      } finally { $key.Dispose() }
    }
    # Scheduled CLI startup and shortcut-based startup are independent of HKCU
    # Run. Remove only this user's actions that resolve to this exact product.
    if (-not $FixtureRegistryId) {
      $failure = 'STARTUP_TAKEOVER_FAILED'
      Import-Module (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\Modules\ScheduledTasks\ScheduledTasks.psd1') -Force
      $account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
      foreach ($task in @(Get-ScheduledTask)) {
        if ($task.Principal.UserId -notin @($sid,$account)) { continue }
        foreach ($action in $task.Actions) {
          if (-not $action.Execute) { continue }
          $argsText = if ($action.Arguments) { [string]$action.Arguments } else { '' }
          if (IsProduct ([PSCustomObject]@{ExecutablePath=[string]$action.Execute;CommandLine=('"'+$action.Execute+'" '+$argsText)})) {
            Disable-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath | Out-Null
            break
          }
        }
      }
      $startup = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
      if ($startup -and [IO.Directory]::Exists($startup)) {
        $shell = New-Object -ComObject WScript.Shell
        try {
          foreach ($file in @(Get-ChildItem -LiteralPath (SafePath $startup) -Filter '*.lnk' -File)) {
            $link = $shell.CreateShortcut($file.FullName)
            if (IsProduct ([PSCustomObject]@{ExecutablePath=$link.TargetPath;CommandLine=('"'+$link.TargetPath+'" '+$link.Arguments)})) {
              $null = SafePath $file.FullName
              Remove-Item -LiteralPath $file.FullName -Force
            }
          }
        } finally { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) | Out-Null }
      }
    }
    $marker = SafePath ([IO.Path]::Combine($userData, 'login-startup-initialized'))
    if ([IO.File]::Exists($marker)) { Remove-Item -LiteralPath $marker -Force }
    Write-Output 'PROCESS_TAKEOVER_PASS'
  } elseif ($Mode -eq 'Finalize') {
    $failure = 'INSTALLATION_DIRECTORY_WRITE_FAILED'
    $null = [IO.Directory]::CreateDirectory($metadata)
    $current = [IO.Path]::Combine($metadata,'current.json')
    $prior = [IO.Path]::Combine($appData,$identity.home)
    $reuse = $null
    if ([IO.File]::Exists($current)) {
      try {
        $old = [IO.File]::ReadAllText((SafePath $current)) | ConvertFrom-Json
        $expected = [IO.Path]::Combine($metadata,'generations', [string]$old.id, 'connections')
        $connectionHome = [IO.Path]::Combine($userData,'connections')
        $ownedHome = [string]::Equals($old.home,[IO.Path]::Combine($userData,'rt'),[StringComparison]::OrdinalIgnoreCase) -or
          (([string]::Equals([IO.Path]::GetDirectoryName($old.home),$expected,[StringComparison]::OrdinalIgnoreCase) -or
            [string]::Equals([IO.Path]::GetDirectoryName($old.home),$connectionHome,[StringComparison]::OrdinalIgnoreCase)) -and
            [IO.Path]::GetFileName($old.home) -match '\A[0-9a-f-]{36}\z')
        if ($old.version -eq 2 -and $old.id -match '\A[0-9a-f-]{36}\z' -and $old.initialized -is [bool] -and $ownedHome) {
          $prior = SafePath $old.home
          if (-not $Clean) { $reuse = $old }
        }
      } catch { # Damaged old pointer is not a prerequisite for clean installation.
      }
    }
    if ($reuse) {
      # Compatible program replacement keeps device identity, plugins and task facts.
      $id = $reuse.id
      WriteAtomic $current (@{version=2;id=$id;home=$reuse.home;priorHome=$reuse.priorHome;initialized=$reuse.initialized;installDirectory=$InstallDirectory} | ConvertTo-Json -Compress)
    } else {
      $id = [Guid]::NewGuid().ToString()
      $generationHome = SafePath ([IO.Path]::Combine($userData,'rt'))
      if ([IO.Directory]::Exists($generationHome) -and [IO.Directory]::EnumerateFileSystemEntries($generationHome).GetEnumerator().MoveNext()) {
        $generationHome = SafePath ([IO.Path]::Combine($userData,'connections',[Guid]::NewGuid().ToString()))
      }
      $null = [IO.Directory]::CreateDirectory($generationHome)
      WriteAtomic $current (@{version=2;id=$id;home=$generationHome;priorHome=$prior;initialized=$false;installDirectory=$InstallDirectory} | ConvertTo-Json -Compress)
    }
    Write-Output $id
  } else {
    $failure = 'FRESH_HOST_CHECK_FAILED'
    $node = SafePath ([IO.Path]::Combine($InstallDirectory,'resources\runtime\node\win-x64\node.exe'))
    $entry = SafePath ([IO.Path]::Combine($InstallDirectory,'resources\runtime\host\managed-entry.js'))
    $check = SafePath ([IO.Path]::Combine($InstallDirectory,'resources\runtime\runtime-installation-check.mjs'))
    Require ([IO.File]::Exists($node) -and [IO.File]::Exists($entry) -and [IO.File]::Exists($check) -and [IO.File]::Exists([IO.Path]::Combine($InstallDirectory,'resources\app.asar'))) 'INSTALLATION_ASSET_MISSING'
    $env:NODE_OPTIONS=''; $env:NODE_PATH=''
    $output = & $node $check $entry $metadata 2>&1
    Require ($LASTEXITCODE -eq 0) 'FRESH_HOST_CHECK_FAILED'
    Write-Output 'FRESH_HOST_CHECK_PASS'
  }
  exit 0
} catch {
  # Only machine codes leave this script; never display command lines, paths,
  # credential data or raw CIM/PowerShell exceptions from the customer machine.
  if ($_.Exception -is [InvalidOperationException] -and $_.Exception.Message -match '\A[A-Z_]+\z') { $failure=$_.Exception.Message }
  Write-Output $failure
  exit 1
}
