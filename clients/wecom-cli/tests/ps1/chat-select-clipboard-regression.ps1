param([Parameter(Mandatory)][string]$DriverDir)
$ErrorActionPreference = 'Stop'
$driver = Join-Path $DriverDir 'chat-select.ps1'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($driver, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$main = $ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] -and
    $node.GetCommandName() -eq 'Invoke-DriverMain' }, $true)[0]
$body = ($main.CommandElements | Where-Object { $_ -is [Management.Automation.Language.ScriptBlockExpressionAst] } |
    Select-Object -First 1).ScriptBlock.GetScriptBlock()
. (Join-Path $DriverDir '_send-points.ps1')
function Assert-True($Value, [string]$Why) { if (-not $Value) { throw $Why } }
function Resolve-WeComMainWindow { return 101 }
function Get-WeComCachedPoints { param($Hwnd)
    if ($global:cacheReady) { return @{ x = 440; y = 38; source = 'glm' } }
    return $null
}
function Invoke-WeComCalibration { param($SelfName, $ArtifactDir)
    $global:calibrations++
    $global:cacheReady = $true
    return @{ x = 440; y = 38; source = 'glm' }
}
function Open-WeComNamedChat { param($Hwnd, $Name) $global:opened = $Name }
function Get-WeComCopiedTitle { param($Hwnd, $Section, $Point)
    $global:titleCopies++
    return $global:copiedTitle
}
function Write-DriverLog { param($Message) }
function Throw-DriverError { param($Code, $Message) throw ('WECOMDRIVE|' + $Code + '|' + $Message) }
$TargetName = '孙晨'; $Section = 'contact'; $SelfName = '本人'; $ArtifactDir = $env:TEMP

# 已有坐标时只复制目标标题一次并核对。
$global:cacheReady = $true
$global:calibrations = 0; $global:titleCopies = 0; $global:copiedTitle = $TargetName
$result = & $body
Assert-True ($result.title -ceq $TargetName -and $global:calibrations -eq 0 -and
    $global:titleCopies -eq 1 -and $global:opened -ceq $TargetName) 'select must verify one fresh target title'

# 无缓存时先标定，然后继续核对目标标题。
$global:cacheReady = $false; $global:calibrations = 0; $global:titleCopies = 0
$result = & $body
Assert-True ($result.title -ceq $TargetName -and $global:calibrations -eq 1 -and
    $global:titleCopies -eq 1) 'calibration followed by select must verify the target title'

$global:copiedTitle = '别人'; $global:titleCopies = 0
$failure = ''; try { & $body | Out-Null } catch { $failure = $_.Exception.Message }
Assert-True ($failure -like 'WECOMDRIVE|UI_CHANGED|*' -and $global:titleCopies -eq 1) 'wrong copied title must still reject select'
Write-Output 'PS_CHAT_SELECT_CLIPBOARD_OK cases=3'
