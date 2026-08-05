$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\wechat-souyisou-lib.ps1')

function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "ASSERTION_FAILED: $Message" }
}

function New-UiaDescriptor {
    param(
        [string]$Name,
        [string]$ControlType = 'Button',
        [double]$Left = 2347,
        [double]$Top = 756,
        [double]$Width = 988,
        [double]$Height = 190,
        [double]$ClickableX = 2841,
        [double]$ClickableY = 851,
        [bool]$IsOffscreen = $false,
        [bool]$SupportsInvoke = $true,
        [bool]$HasClickablePoint = $true
    )
    [pscustomobject]@{
        Name=$Name;ControlType=$ControlType;Left=$Left;Top=$Top
        Width=$Width;Height=$Height;ClickableX=$ClickableX;ClickableY=$ClickableY
        IsOffscreen=$IsOffscreen;SupportsInvoke=$SupportsInvoke
        HasClickablePoint=$HasClickablePoint;Element=[pscustomobject]@{id=$Name}
    }
}

$windowRect = [pscustomobject]@{Left=2000;Top=600;Right=3400;Bottom=1800}
$association = ([string][char]0x4E2D)+[char]0x56FD+[char]0x9EC4+[char]0x91D1+
    [char]0x534F+[char]0x4F1A
$person = ([string][char]0x5468)+[char]0x6D32
$matchingName = $association + $person + ([string][char]0x8054)+[char]0x7CFB+[char]0x4EBA
$wikiName = $person + ' - ' + ([string][char]0x767E)+[char]0x79D1

$descriptors = @()
$descriptors += New-UiaDescriptor $wikiName -Top 650 -Height 70
$descriptors += New-UiaDescriptor $matchingName
# 同一卡片内的标题 ListItem 必须和优先 Button 去重。
$descriptors += New-UiaDescriptor $matchingName -ControlType ListItem -Left 2371 -Top 780 -Width 791 -Height 31 -ClickableX 2700 -ClickableY 795
$descriptors += New-UiaDescriptor $matchingName -Top 1000 -ClickableY 1095
$descriptors += New-UiaDescriptor $matchingName -Left 3100 -Top 900 -Width 250 -Height 100 -ClickableX 3225 -ClickableY 950
$descriptors += New-UiaDescriptor $matchingName -Top 1250 -ClickableY 1345 -IsOffscreen $true
$descriptors += New-UiaDescriptor $matchingName -Top 1450 -ClickableY 1545 -SupportsInvoke $false
$targets = @(Select-WeixinUiaResultTargets `
    $descriptors $association $person $windowRect)
Assert ($targets.Count -eq 2) `
    'dual semantic filter excludes wiki/sidebar/offscreen and deduplicates nested title'
Assert ($targets[0].control_type -eq 'Button' -and $targets[0].top -eq 756) `
    'visible result buttons are preferred and sorted from top to bottom'
Assert ($targets[1].top -eq 1000) 'second independent result remains available'
Assert (($targets | ConvertTo-Json -Depth 4) -notmatch [regex]::Escape($matchingName)) `
    'selected descriptor never carries result text into diagnostics'

$wrongAssociation = @(Select-WeixinUiaResultTargets `
    @((New-UiaDescriptor ($person + ([string][char]0x8054)+[char]0x7CFB+[char]0x4EBA))) `
    $association $person $windowRect)
Assert ($wrongAssociation.Count -eq 0) 'person-only result cannot target a detail card'

$excludedSemanticNames = @(
    $matchingName + ' ' + ([string][char]0x767E)+[char]0x79D1,
    $matchingName + ' ' + ([string][char]0x5C0F)+[char]0x7A0B+[char]0x5E8F
)
foreach ($excludedName in $excludedSemanticNames) {
    $excludedTargets = @(Select-WeixinUiaResultTargets `
        @((New-UiaDescriptor $excludedName)) $association $person $windowRect)
    Assert ($excludedTargets.Count -eq 0) `
        'wiki and mini-program nodes are excluded even when both semantic terms match'
}

$listItemOnly = @(Select-WeixinUiaResultTargets `
    @((New-UiaDescriptor $matchingName -ControlType ListItem `
        -Left 2371 -Top 780 -Width 791 -Height 31 `
        -ClickableX 2700 -ClickableY 795)) `
    $association $person $windowRect)
Assert ($listItemOnly.Count -eq 1 -and $listItemOnly[0].control_type -eq 'ListItem') `
    'a standalone ListItem with both semantic terms remains a valid target'

$noClickablePoint = @(Select-WeixinUiaResultTargets `
    @((New-UiaDescriptor $matchingName -HasClickablePoint $false)) `
    $association $person $windowRect)
Assert ($noClickablePoint.Count -eq 0) `
    'a semantic match without a clickable point fails closed'

$negativeWindowRect = [pscustomobject]@{
    Left=-1600;Top=0;Right=-200;Bottom=1200
}
$negativeWindowTarget = New-UiaDescriptor $matchingName `
    -Left -1500 -Top 200 -Width 900 -Height 190 `
    -ClickableX -1050 -ClickableY 295
$negativeTargets = @(Select-WeixinUiaResultTargets `
    @($negativeWindowTarget) $association $person $negativeWindowRect)
Assert ($negativeTargets.Count -eq 1 -and $negativeTargets[0].x -eq -1050) `
    'result filtering preserves valid coordinates on a monitor left of the primary display'

$dpi150 = ConvertTo-WeixinPhysicalClickPoint 2841 851 144 'PerMonitorV2'
Assert ($dpi150.x -eq 2841 -and $dpi150.y -eq 851) `
    '150 percent DPI keeps UIA physical screen coordinates unchanged'
$leftMonitor = ConvertTo-WeixinPhysicalClickPoint -1278 121 144 'PerMonitorV2'
Assert ($leftMonitor.x -eq -1278 -and $leftMonitor.y -eq 121) `
    'negative multi-monitor physical coordinates remain valid'
$dpiRejected = $false
try { [void](ConvertTo-WeixinPhysicalClickPoint 10 20 144 'SystemAware') }
catch { $dpiRejected = $_.Exception.Message -eq 'DPI_AWARENESS_INVALID' }
Assert $dpiRejected 'virtualized coordinate context is rejected'

$mouseEvents = @()
$guardCalls = 0
Invoke-SafeMouseClick `
    { param($up) $script:mouseEvents += $(if($up){'up'}else{'down'}) } `
    { $script:guardCalls++; $true }
Assert (($mouseEvents -join ',') -eq 'down,up') `
    'one target produces exactly one mouse down/up click'
Assert ($guardCalls -eq 2) 'foreground is verified immediately around the click'

$blockedEvents = @()
$blocked = $false
try {
    Invoke-SafeMouseClick `
        { param($up) $script:blockedEvents += $(if($up){'up'}else{'down'}) } `
        { $false }
} catch { $blocked = $_.Exception.Message -eq 'FOREGROUND_LOST' }
Assert ($blocked -and $blockedEvents.Count -eq 0) `
    'failed foreground verification sends no mouse event'

$initialExhaustion = New-WeixinUiaCandidateExhaustionRecord 0
Assert (
    $initialExhaustion.reason -eq 'uia_candidates_unavailable' -and
    (Get-WeixinCollectStatus $null $false 0 10 0) -eq 'inconclusive'
) 'initial zero UIA candidates is a normal inconclusive enumeration outcome'
$afterOneExhaustion = New-WeixinUiaCandidateExhaustionRecord 1
Assert (
    $afterOneExhaustion.reason -eq 'no_new_uia_candidate' -and
    (Get-WeixinCollectStatus $null $false 1 10 0) -eq 'inconclusive'
) 'no new candidate after one checked detail remains inconclusive with checked count'
Assert (
    ($initialExhaustion | ConvertTo-Json -Compress) -notmatch
        [regex]::Escape($matchingName)
) 'UIA exhaustion record contains no result text'

$entryText = Get-Content -Raw -LiteralPath (
    Join-Path $PSScriptRoot '..\scripts\wechat-souyisou.ps1')
Assert ($entryText -notmatch 'CARD_LOCATE_FAILED|LocatorPath|x_ratio|y_ratio') `
    'formal detail collection has no legacy pixel locator entry point'
Assert (
    $entryText -match 'New-WeixinUiaCandidateExhaustionRecord \$checked' -and
    $entryText -match '(?s)if \(-not \$newCount\) \{\s+\$records \+= New-WeixinUiaCandidateExhaustionRecord \$checked' -and
    $entryText -match '(?s)\$status=Get-WeixinCollectStatus.*?Complete-WeixinPluginSession'
) 'only UIA exhaustion is recorded and it flows through existing status and normal cleanup'

Write-Output 'UIA result target tests passed'
