$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $root 'scripts\wechat-souyisou.ps1'
$lib = Join-Path $root 'scripts\wechat-souyisou-lib.ps1'
. $lib

function Assert($condition, $message) { if (-not $condition) { throw $message } }
foreach ($file in @($entry,$lib)) {
    $tokens=$null; $errors=$null
    [void][Management.Automation.Language.Parser]::ParseFile($file,[ref]$tokens,[ref]$errors)
    Assert ($errors.Count -eq 0) "AST failed: $file $($errors[0])"
}
$entryText = Get-Content -LiteralPath $entry -Encoding UTF8 -Raw
Assert ($entryText -notmatch '(?im)\$pid\s*=') 'must not overwrite PowerShell read-only $PID'
Assert ($entryText -notmatch '(?im)^\s*\$keys\s*=\s*@\{') 'virtual key map must not shadow Invoke-SafeKeyChord Keys parameter'
Assert ($entryText -match '\$points\s*=\s*@\(if\s*\(\$explicitItems\)') 'single explicit locator point remains an array on PowerShell 5.1'
Assert ($entryText -match '\$checked\s+-ge\s+\$Limit\s+-and\s+\$failures\s+-eq\s+0') 'not_found requires every limited detail to succeed'
Assert ((ConvertTo-MouseWheelData -480) -eq 4294966816) 'negative wheel delta uses Win32 uint32 two-complement encoding'
Assert ((New-SearchQuery '中国游艺设备游乐园协会' '王承展') -eq '中国游艺设备游乐园协会 王承展 联系人') 'query'
Assert ((Get-MobileCandidates '王承展 18511597486').Count -eq 1) 'mobile extraction'
$validJudgeResult = [pscustomobject]@{
    matched=$true;person_name='王承展';mobile='18511597486'
    evidence_quote='王承展 18511597486';confidence=1.0;reason='same line'
}
Assert (Test-JudgeResult $validJudgeResult '王承展 18511597486' '王承展') 'judge validation'
Assert (-not (Test-JudgeResult ([pscustomobject]@{matched=$true;person_name='王承展';mobile='13900000000';evidence_quote='王承展 13900000000';confidence=1.0;reason='same line'}) '王承展 18511597486' '王承展')) 'hallucination rejected'
Assert (-not (Test-JudgeResult ([pscustomobject]@{matched=$true;person_name='王承展';mobile='13900000000';evidence_quote='王承展 13900000000';confidence=1.0;reason='same line'}) "王承展`n其他人 13900000000" '王承展')) 'name binding required'
Assert (-not (Test-JudgeResult ([pscustomobject]@{matched=$true;person_name='王承展';mobile='18511597486';evidence_quote='王承展 18511597486';confidence='1';reason='same line'}) '王承展 18511597486' '王承展')) 'judge confidence type is strict'
$repeatedSemanticEvidence = @'
结果一 刘甲、陈戟 联系电话 13912345678
结果二 联系人陈乙、陈戟 电话 13912345678
结果三 刘丙 陈戟 联系电话：13912345678
'@
$repeatedSemanticResult = [pscustomobject]@{
    matched=$true;person_name='陈戟';mobile='13912345678'
    evidence_quote='结果三 刘丙 陈戟 联系电话：13912345678'
    confidence=0.91;reason='llm_repeated_independent_binding'
}
Assert (Test-JudgeResult $repeatedSemanticResult $repeatedSemanticEvidence '陈戟') 'semantic repeated evidence preserves exact quote contract'
$malformedExternal = Invoke-EvidenceJudge '王承展 18511597486' '协会' '王承展' {
    [pscustomobject]@{matched=$false}
}
Assert ($malformedExternal.inconclusive -eq $true) 'malformed external schema is inconclusive'
$failedJudge = Invoke-EvidenceJudge '王承展 18511597486' '协会' '王承展' { throw 'provider down' }
Assert ($failedJudge.inconclusive -eq $true) 'provider failure is inconclusive'
$pythonExecutable = (Get-Command python.exe -ErrorAction Stop).Source
$externalCode = "import sys,json;p=json.load(sys.stdin);assert sys.argv[1]=='value with spaces & `"quotes`"';print(json.dumps({'matched':True,'person_name':p['person_name'],'mobile':'18511597486','evidence_quote':p['person_name']+' 18511597486','confidence':1,'reason':'ok'},ensure_ascii=False))"
$externalJudge = New-ExternalJudge $pythonExecutable @(
    '-c', $externalCode, 'value with spaces & "quotes"'
) 5000
$externalResult = & $externalJudge ([pscustomobject]@{
    association_name='协会'; person_name='王承展'; text='王承展 18511597486'
})
Assert ($externalResult.person_name -eq '王承展') 'external judge UTF-8 stdin/stdout'
Assert (Test-JudgeResult $externalResult '王承展 18511597486' '王承展') 'external judge evidence validation'
$strictUtf8Helper = Join-Path $PSScriptRoot 'utf8-stdin-helper.py'
$strictUtf8Judge = New-ExternalJudge $pythonExecutable @($strictUtf8Helper) 5000
$previousConsoleInputEncoding = [Console]::InputEncoding
try {
    [Console]::InputEncoding = [Text.Encoding]::ASCII
    $strictUtf8Result = & $strictUtf8Judge ([pscustomobject]@{
        association_name='中国缝制机械协会'
        person_name='陈戟'
        text="第一行`n第二行　陈戟 13912345678"
    })
    Assert ([Console]::InputEncoding.CodePage -eq [Text.Encoding]::ASCII.CodePage) 'external judge restores caller console encoding'
} finally {
    [Console]::InputEncoding = $previousConsoleInputEncoding
}
Assert ($strictUtf8Result.reason -eq 'utf8_roundtrip') 'external judge reads strict UTF-8 Chinese JSON independent of console encoding'
Assert (Test-JudgeResult $strictUtf8Result "第一行`n第二行　陈戟 13912345678" '陈戟') 'UTF-8 roundtrip preserves newline full-width space and evidence'
$libText = Get-Content -LiteralPath $lib -Encoding UTF8 -Raw
Assert ($libText -match 'StandardInputEncoding') 'modern runtime stdin encoding is configured when supported'
Assert ($libText -match '\$stdin\.BaseStream\.Write\(\$stdinBytes') 'legacy Windows PowerShell writes explicit UTF-8 stdin bytes'
Assert ($libText -match '\[Console\]::InputEncoding\s*=\s*\$previousInputEncoding') 'legacy preamble compatibility restores console encoding'
$timeoutJudge = New-ExternalJudge $pythonExecutable @('-c','import time;time.sleep(2)') 50
$timeoutStopped = [Diagnostics.Stopwatch]::StartNew()
$timeoutCode = $null
try { & $timeoutJudge ([pscustomobject]@{association_name='协会';person_name='姓名';text='证据'}) }
catch { $timeoutCode = $_.Exception.Message }
$timeoutStopped.Stop()
Assert ($timeoutCode -eq 'JUDGE_TIMEOUT' -and $timeoutStopped.ElapsedMilliseconds -lt 1500) 'external judge timeout'
$exitJudge = New-ExternalJudge $pythonExecutable @('-c','import sys;sys.exit(7)') 5000
$exitResult = Invoke-EvidenceJudge '王承展 18511597486' '协会' '王承展' $exitJudge
Assert ($exitResult.inconclusive -eq $true) 'external judge nonzero exit is inconclusive'
Assert ((Get-CfHtmlLinks '<a href="https://example.test/a">x</a>').Count -eq 1) 'html link'
[void](Add-Type -AssemblyName System.Drawing)
$bitmap = New-Object Drawing.Bitmap 1000, 700
$graphics = [Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.Clear([Drawing.Color]::FromArgb(28,28,28))
    $cardBrush = New-Object Drawing.SolidBrush ([Drawing.Color]::FromArgb(58,58,58))
    try {
        $graphics.FillRectangle($cardBrush,45,90,620,120)
        $graphics.FillRectangle($cardBrush,45,245,620,145)
    } finally { $cardBrush.Dispose() }
} finally { $graphics.Dispose() }
try {
    $bands = @(Find-DarkThemeCardBands $bitmap)
    Assert ($bands.Count -eq 2) 'dark card bands'
    Assert ($bands[0].y_ratio -lt $bands[1].y_ratio) 'card visual order'
    Assert ($bands[0].x_ratio -ge 0.28 -and $bands[0].x_ratio -le 0.32) 'title click x is safe'
    Assert ((Get-BitmapSha256 $bitmap).Length -eq 64) 'bitmap hash'
} finally { $bitmap.Dispose() }
$light = New-Object Drawing.Bitmap 1000,700
$lightGraphics = [Drawing.Graphics]::FromImage($light)
try { $lightGraphics.Clear([Drawing.Color]::White) } finally { $lightGraphics.Dispose() }
try { Assert (@(Find-DarkThemeCardBands $light).Count -eq 0) 'light theme fail closed' }
finally { $light.Dispose() }

function New-SyntheticDarkViewport([int]$width, [int]$height, [int[]]$cards, [switch]$Interference) {
    $result = New-Object Drawing.Bitmap $width,$height
    $drawing = [Drawing.Graphics]::FromImage($result)
    try {
        $drawing.Clear([Drawing.Color]::FromArgb(28,28,28))
        $cardBrush = New-Object Drawing.SolidBrush ([Drawing.Color]::FromArgb(58,58,58))
        try {
            for ($index=0; $index -lt $cards.Count; $index+=2) {
                $drawing.FillRectangle($cardBrush,[int]($width*.05),$cards[$index],[int]($width*.62),$cards[$index+1])
            }
            if ($Interference) {
                # 搜索页 header 从扫描区顶边开始；sidebar 贯穿视口，两者都不能成为卡片。
                $drawing.FillRectangle($cardBrush,[int]($width*.05),[int]($height*.10),[int]($width*.62),[int]($height*.07))
                $drawing.FillRectangle($cardBrush,[int]($width*.05),[int]($height*.10),[int]($width*.12),[int]($height*.84))
            }
        } finally { $cardBrush.Dispose() }
    } finally { $drawing.Dispose() }
    $result
}

foreach ($case in @(
    @{w=600;h=400;cards=@(80,55)},
    @{w=1000;h=700;cards=@(125,72,245,138)},
    @{w=1500;h=1050;cards=@(190,95,345,180,585,245)}
)) {
    $synthetic = New-SyntheticDarkViewport $case.w $case.h $case.cards
    try {
        $syntheticBands = @(Find-DarkThemeCardBands $synthetic)
        Assert ($syntheticBands.Count -eq ($case.cards.Count / 2)) "scaled card count $($case.w)x$($case.h)"
        foreach ($band in $syntheticBands) {
            Assert ($band.x_ratio -ge 0.29 -and $band.x_ratio -le 0.31) 'scaled click x stays in title safe area'
            Assert ($band.y_ratio -gt ($band.top / $case.h) -and $band.y_ratio -lt ($band.bottom / $case.h)) 'click y inside card'
            Assert ($band.fingerprint.Length -eq 64) 'card fingerprint'
        }
    } finally { $synthetic.Dispose() }
}
$emptyDark = New-SyntheticDarkViewport 800 500 @()
try { Assert (@(Find-DarkThemeCardBands $emptyDark).Count -eq 0) 'empty dark viewport' }
finally { $emptyDark.Dispose() }
$interference = New-SyntheticDarkViewport 1000 700 @(180,80,310,105) -Interference
try {
    $interferenceBands = @(Find-DarkThemeCardBands $interference)
    Assert ($interferenceBands.Count -eq 2) 'header and sidebar excluded'
    Assert ($interferenceBands[0].top -ge 180) 'header is not clickable'
} finally { $interference.Dispose() }
$scrollViewportA = New-SyntheticDarkViewport 1000 700 @(180,90)
$scrollViewportB = New-SyntheticDarkViewport 1000 700 @(320,90)
try {
    $fingerprintA = @(Find-DarkThemeCardBands $scrollViewportA)[0].fingerprint
    $fingerprintB = @(Find-DarkThemeCardBands $scrollViewportB)[0].fingerprint
    Assert ($fingerprintA -eq $fingerprintB) 'same card deduplicates after scrolling'
} finally {
    $scrollViewportA.Dispose()
    $scrollViewportB.Dispose()
}
$weakContrast = New-Object Drawing.Bitmap 800,500
$weakGraphics = [Drawing.Graphics]::FromImage($weakContrast)
try {
    $weakGraphics.Clear([Drawing.Color]::FromArgb(28,28,28))
    $weakBrush = New-Object Drawing.SolidBrush ([Drawing.Color]::FromArgb(37,37,37))
    try { $weakGraphics.FillRectangle($weakBrush,40,100,500,90) } finally { $weakBrush.Dispose() }
} finally { $weakGraphics.Dispose() }
try { Assert (@(Find-DarkThemeCardBands $weakContrast).Count -eq 0) 'ambiguous weak contrast fails closed' }
finally { $weakContrast.Dispose() }

Assert (Test-DetailEvidence ('协会会议联系人王承展，正文内容。' * 10) '列表内容' '协会' '王承展') 'detail evidence'
Assert (-not (Test-DetailEvidence '列表内容' '列表内容' '协会' '王承展')) 'list is not detail'
Assert (Test-DetailNeedsOcr ('协会联系人图片附件。' * 12) '列表内容' '协会' '王承展') 'image detail needs ocr'
Assert (Test-DetailNeedsOcr ('协会会议联系人王承展正文。' * 12) '列表内容' '协会' '王承展') 'detail without target mobile uses ocr fallback'
Assert (Test-DetailNeedsOcr '' '列表结果正文' '协会' '王承展') 'pure image detail without copied marker uses ocr'
$locatorPath = Join-Path $PSScriptRoot 'liu-changlei-first-card.locator.json'
Assert ((Resolve-LocatorFilePath $locatorPath $root) -eq (Resolve-Path $locatorPath).Path) 'absolute locator path'
Assert ((Resolve-LocatorFilePath 'tests\liu-changlei-first-card.locator.json' $root) -eq
    (Resolve-Path $locatorPath).Path) 'locator relative to client root'
$liuLocator = Get-Content -Raw -LiteralPath $locatorPath | ConvertFrom-Json
Assert (@($liuLocator.items).Count -eq 1 -and
    [double]$liuLocator.items[0].x_ratio -eq 0.3 -and
    [double]$liuLocator.items[0].y_ratio -eq 0.215) 'real screenshot ratio locator fixture'
$validatedFixturePoints = @(Get-ValidatedLocatorPoints $liuLocator)
Assert ($validatedFixturePoints.Count -eq 1) 'fixture locator points valid'
foreach ($invalidLocator in @(
    [pscustomobject]@{items=@()},
    [pscustomobject]@{items=@([pscustomobject]@{title='x';x_ratio='0.3';y_ratio=0.2})},
    [pscustomobject]@{items=@([pscustomobject]@{title='x';x_ratio=0.19;y_ratio=0.2})},
    [pscustomobject]@{items=@([pscustomobject]@{title='x';x_ratio=0.70;y_ratio=0.2})},
    [pscustomobject]@{items=@([pscustomobject]@{title='x';x_ratio=0.3;y_ratio=0.96})},
    [pscustomobject]@{items=@([pscustomobject]@{title='x';x_ratio=0.3;y_ratio=0.2;unexpected='x'})},
    [pscustomobject]@{items=@(
        [pscustomobject]@{title='x';x_ratio=0.3;y_ratio=0.2},
        [pscustomobject]@{title='y';x_ratio=0.01;y_ratio=0.2}
    )},
    [pscustomobject]@{items=@(1..11 | ForEach-Object {
        [pscustomobject]@{title="x$_";x_ratio=0.3;y_ratio=0.2}
    })}
)) {
    $invalidLocatorRejected=$false
    try { [void](Get-ValidatedLocatorPoints $invalidLocator) }
    catch { $invalidLocatorRejected=$_.Exception.Message -eq 'LOCATOR_POINTS_INVALID' }
    Assert $invalidLocatorRejected 'malicious locator points fail closed'
}
Assert (Test-WindowRectDimensions 400 300) 'minimum window rect accepted'
Assert (-not (Test-WindowRectDimensions 399 300)) 'narrow window rect rejected'
Assert (-not (Test-WindowRectDimensions 400 299)) 'short window rect rejected'
Assert (-not (Test-WindowRectDimensions 10001 300)) 'oversized window width rejected'
Assert (-not (Test-WindowRectDimensions 8000 6000)) 'oversized window pixel area rejected'
$locatorSandbox = Join-Path ([IO.Path]::GetTempPath()) ('wechat-locator-' + [guid]::NewGuid().ToString('N'))
$locatorRoot = Join-Path $locatorSandbox 'root'
$locatorSibling = Join-Path $locatorSandbox 'secret.json'
$malformedLocator = Join-Path $locatorRoot 'malformed.json'
$oversizedLocator = Join-Path $locatorRoot 'oversized.json'
try {
    [void](New-Item -ItemType Directory -Path $locatorRoot -Force)
    [IO.File]::WriteAllText($locatorSibling,'{"items":[]}',(New-Object Text.UTF8Encoding($false)))
    [IO.File]::WriteAllText($malformedLocator,'{"items":[}',(New-Object Text.UTF8Encoding($false)))
    [IO.File]::WriteAllText($oversizedLocator,(' ' * 65537),(New-Object Text.UTF8Encoding($false)))
    $traversalRejected=$false
    try { [void](Resolve-LocatorFilePath '..\secret.json' $locatorRoot $locatorRoot) }
    catch { $traversalRejected=$_.Exception.Message -eq 'LOCATOR_READ_FAILED' }
    Assert $traversalRejected 'relative locator path traversal rejected'
    $uncRejected=$false
    try { [void](Resolve-LocatorFilePath '\\server\share\locator.json' $locatorRoot $locatorRoot) }
    catch { $uncRejected=$_.Exception.Message -eq 'LOCATOR_READ_FAILED' }
    Assert $uncRejected 'UNC locator path rejected without network access'
    $wrongExtensionRejected=$false
    try { [void](Resolve-LocatorFilePath (Join-Path $root 'README.md') $root) }
    catch { $wrongExtensionRejected=$_.Exception.Message -eq 'LOCATOR_READ_FAILED' }
    Assert $wrongExtensionRejected 'non-json locator rejected'
    $malformedRejected=$false
    try { [void](Read-LocatorJson $malformedLocator) }
    catch { $malformedRejected=$_.Exception.Message -eq 'LOCATOR_READ_FAILED' }
    Assert $malformedRejected 'malformed locator JSON rejected'
    $oversizedRejected=$false
    try { [void](Read-LocatorJson $oversizedLocator) }
    catch { $oversizedRejected=$_.Exception.Message -eq 'LOCATOR_READ_FAILED' }
    Assert $oversizedRejected 'oversized locator JSON rejected before parsing'
} finally {
    if (Test-Path -LiteralPath $locatorSandbox) {
        Remove-Item -LiteralPath $locatorSandbox -Recurse -Force
    }
}
Assert (Test-DetailNeedsOcr "客服电话 13900000000`n协会联系人王承展 图片附件。" '列表内容' '协会' '王承展') 'unrelated mobile does not suppress target image ocr'
$listWithImageMarker = ('协会 图片 全部 文章 账号 相关搜索 搜索结果内容。' * 12)
Assert (-not (Test-DetailNeedsOcr $listWithImageMarker $listWithImageMarker '协会' '王承展')) 'result page image marker never triggers ocr'
$dynamicImageList = @'
协会 图片 搜索结果动态变化
全部
文章
账号
另一条完全变化的搜索结果
'@
Assert (-not (Test-DetailNeedsOcr $dynamicImageList '不相同的原始列表文本' '协会' '王承展')) 'dynamic result page navigation never triggers ocr'
Assert (Test-DetailNeedsOcr '协会 PDF 下载附件' '不同的结果列表文本' '协会' '王承展') 'short PDF detail triggers ocr'
Assert ((Get-ValidatedOcrText ([pscustomobject]@{
    ok=$true;text='王承展 18511597486';image_count=3
}) 3) -eq '王承展 18511597486') 'valid OCR schema'
foreach ($maliciousOcrResult in @(
    [pscustomobject]@{ok='true';text='王承展 18511597486';image_count=3},
    [pscustomobject]@{ok=$true;text=@('王承展 18511597486');image_count=3},
    [pscustomobject]@{ok=$true;text='王承展 18511597486';image_count=4},
    [pscustomobject]@{ok=$true;text='';image_count=3}
)) {
    $ocrRejected=$false
    try { [void](Get-ValidatedOcrText $maliciousOcrResult 3) } catch { $ocrRejected=$true }
    Assert $ocrRejected 'malicious OCR result rejected'
}
Assert (-not (Test-OcrEvidenceAttribution '' '协会' '王承展')) 'empty OCR cannot be attributed'
Assert (-not (Test-OcrEvidenceAttribution '协会 客服 13900000000' '协会' '王承展')) 'OCR without target person rejected'
Assert (-not (Test-OcrEvidenceAttribution '王承展 18511597486' '协会' '王承展')) 'OCR without target association rejected'
Assert (-not (Test-OcrEvidenceAttribution '登录失败 广告电话 13900000000' '协会' '王承展')) 'unknown page OCR rejected'
Assert (Test-OcrEvidenceAttribution '协会 联系人王承展 18511597486' '协会' '王承展') 'target OCR attribution'
$uniqueHashes=@()
foreach($candidateHash in @('hash-1','hash-2','hash-3')) {
    Assert (Test-NewOcrViewportHash $uniqueHashes $candidateHash) 'new OCR viewport accepted'
    $uniqueHashes += $candidateHash
}
Assert ($uniqueHashes.Count -eq 3) 'three distinct OCR viewports retained'
Assert (-not (Test-NewOcrViewportHash $uniqueHashes 'hash-2')) 'duplicate OCR viewport rejected'
Assert (-not (Test-NewOcrViewportHash $uniqueHashes '')) 'empty OCR viewport hash rejected'
$safeDiagnostics = ConvertTo-SafeDiagnosticRecords @(
    [pscustomobject]@{
        ordinal=1;stage='detail_judge';reason='checked'
        text='王承展 18511597486';ocr_text='协会 王承展 18511597486'
        text_length=18;detail_hash=('a'*64);ocr_hashes=@(('b'*64))
        ocr_region='right_detail'
    }
)
$safeDiagnosticJson = $safeDiagnostics | ConvertTo-Json -Depth 5 -Compress
Assert ($safeDiagnosticJson -notlike '*王承展*' -and $safeDiagnosticJson -notlike '*18511597486*') 'inconclusive diagnostics exclude PII body'
Assert ($safeDiagnostics[0].text_length -eq 18 -and $safeDiagnostics[0].detail_hash.Length -eq 64) 'diagnostics retain safe metadata'
$resultPageSample = @'
中国游艺设备游乐园协会 王承展 联系人
全部
文章
账号
相关搜索
关于召开中国游艺机游乐园协会理事会的通知
会议联系人和参会安排详细说明
中国游艺机游乐园协会CAAPA
'@
Assert (Test-ResultPageEvidence $resultPageSample $resultPageSample) 'result page restored'
Assert (Test-SearchResultReady $resultPageSample '协会 王承展 联系人') 'result page ready signal'
$whitespaceQuery = "中国游艺设备游乐园协会$([char]0x3000)王承展`r`n联系人"
Assert (
    Test-SearchResultReady $resultPageSample $whitespaceQuery
) 'query comparison tolerates Chinese display whitespace differences'
Assert (-not (Test-SearchResultReady '协会 王承展 联系人' '协会 王承展 联系人')) 'query alone is not ready'
$staleResultPageSample = $resultPageSample.Replace(
    '中国游艺设备游乐园协会 王承展 联系人',
    '中国轮胎循环利用协会 李四 联系人')
Assert (-not (Test-SearchResultReady $staleResultPageSample '协会 王承展 联系人')) 'stale result for another query is not ready'
$loadingPageSample = @'
全部
文章
正在加载
'@
Assert (-not (Test-SearchResultReady $loadingPageSample '协会 王承展 联系人')) 'loading page is not ready'
$dynamicResultPage = $resultPageSample.Replace(
    '会议联系人和参会安排详细说明',
    "会议联系人和参会安排详细说明`r`n刚刚更新")
Assert (Test-ResultPageEvidence $dynamicResultPage $resultPageSample) 'dynamic list content is tolerated'
$detailPageSample = @'
关于召开中国游艺机游乐园协会理事会的通知
会议联系人和参会安排详细说明
中国游艺机游乐园协会CAAPA
正文内容
'@
Assert (-not (Test-ResultPageEvidence $detailPageSample $resultPageSample)) 'detail is not accepted as restored list'
$forgedMarkerDetail = @'
本文介绍全部文章账号以及相关搜索功能
中国游艺设备游乐园协会 王承展 联系人
关于召开中国游艺机游乐园协会理事会的通知
会议联系人和参会安排详细说明
中国游艺机游乐园协会CAAPA
正文内容正文内容正文内容
'@
Assert (-not (Test-ResultPageEvidence $forgedMarkerDetail $resultPageSample)) 'navigation words inside detail sentence are rejected'
$twoMarkerPage = $dynamicResultPage.Replace("账号`r`n",'').Replace("账号`n",'').
    Replace("相关搜索`r`n",'').Replace("相关搜索`n",'')
Assert (-not (Test-ResultPageEvidence $twoMarkerPage $resultPageSample)) 'fewer than three navigation columns rejected'
$shortOriginal = @'
全部
文章
账号
相关搜索
唯一一条足够长度的初始搜索结果标题
'@
$shortDynamic = "$shortOriginal`r`n动态内容动态内容动态内容"
Assert (-not (Test-ResultPageEvidence $shortDynamic $shortOriginal)) 'fewer than three original long lines cannot prove recovery'
$dynamicWithTwoOverlaps = $dynamicResultPage.Replace(
    '关于召开中国游艺机游乐园协会理事会的通知',
    '完全不同的动态结果标题文本内容').Replace(
    '会议联系人和参会安排详细说明',
    '另一个完全不同的动态结果摘要文本')
Assert (-not (Test-ResultPageEvidence $dynamicWithTwoOverlaps $resultPageSample)) 'fewer than three long-line overlaps rejected'
Assert (-not (Test-WeixinExecutablePath 'C:\Program Files (x86)\Tencent\WeChat\WeChat.exe')) 'enterprise wechat rejected'
Assert (Test-WeixinExecutablePath 'C:\Program Files\Tencent\Weixin\Weixin.exe') 'ordinary weixin accepted'
$validPluginIdentity = [pscustomobject]@{
    Hwnd=222; ProcessPath=(Join-Path $script:WeixinPluginRoot '1\WeChatAppEx.exe')
    ClassName='Chrome_WidgetWin_0'; Title=([string]([char]0x5FAE) + [char]0x4FE1)
}
Assert (Test-WeixinForegroundIdentity $validPluginIdentity 111) 'trusted plugin identity'
$wrongPluginPath = $validPluginIdentity.PSObject.Copy()
$wrongPluginPath.ProcessPath='C:\Program Files (x86)\Tencent\WeChat\WeChatAppEx.exe'
Assert (-not (Test-WeixinForegroundIdentity $wrongPluginPath 111)) 'enterprise-like plugin path rejected'
$spoofedPluginPath = $validPluginIdentity.PSObject.Copy()
$spoofedPluginPath.ProcessPath='C:\untrusted\Tencent\xwechat\xplugin\plugins\RadiumWMPF\1\WeChatAppEx.exe'
Assert (-not (Test-WeixinForegroundIdentity $spoofedPluginPath 111)) 'plugin path outside trusted appdata root rejected'
$wrongPluginTitle = $validPluginIdentity.PSObject.Copy()
$wrongPluginTitle.Title='企业微信'
Assert (-not (Test-WeixinForegroundIdentity $wrongPluginTitle 111)) 'wrong plugin title rejected'

$validMainIdentity = [pscustomobject]@{
    Hwnd=111; ProcessPath='C:\Program Files\Tencent\Weixin\Weixin.exe'
    ClassName='WeChatMainWndForPC'; Title='微信'
}
$cleanupWindows = @{ 111=$true; 222=$true }
$cleanupVisible = @{ 111=$true; 222=$true }
$cleanupForeground = 222
$cleanupKeyEvents = @()
$cleanupActivations = @()
$cleanupResult = Close-WeixinPluginSession 222 111 {
    param($h)
    if ($h -eq 222) { $validPluginIdentity } elseif ($h -eq 111) { $validMainIdentity } else { $null }
} { param($h) [bool]$cleanupWindows[[int]$h] } {
    param($h) [bool]$cleanupVisible[[int]$h]
} { $cleanupForeground } {
    param($h)
    $script:cleanupActivations += [int]$h
    $script:cleanupForeground = [int]$h
    $true
} {
    param($key,$up)
    $script:cleanupKeyEvents += "$key/$up"
    if ($key -eq 'W' -and $up) {
        $script:cleanupWindows[222] = $false
        $script:cleanupVisible[222] = $false
    }
} {} {} 2
Assert ($cleanupResult.session_closed -eq $true) 'strict cleanup reports closed session'
Assert (($cleanupActivations -join ',') -eq '222,111') 'cleanup activates exact plugin then main hwnd'
Assert (($cleanupKeyEvents -join ',') -eq 'CTRL/False,W/False,W/True,CTRL/True') 'cleanup chord releases every key'
Assert ($cleanupForeground -eq 111) 'cleanup restores main foreground'

$hiddenPluginWindows = @{ 111=$true; 222=$true }
$hiddenPluginVisible = @{ 111=$true; 222=$false }
$hiddenPluginActivations = @()
$hiddenPluginForeground = 222
$hiddenPluginResult = Close-WeixinPluginSession 222 111 {
    param($h)
    if ($h -eq 222) {
        [pscustomobject]@{
            Hwnd=222
            ProcessPath=(Join-Path $env:APPDATA 'Tencent\xwechat\xplugin\plugins\RadiumWMPF\1\WeChatAppEx.exe')
            ClassName='Chrome_WidgetWin_0'
            Title=([string]([char]0x5FAE) + [char]0x4FE1)
        }
    } else {
        [pscustomobject]@{
            Hwnd=111
            ProcessPath='C:\Program Files\Tencent\Weixin\Weixin.exe'
            ClassName='WeChatMainWndForPC'
            Title='微信'
        }
    }
} { param($h) [bool]$hiddenPluginWindows[[int]$h] } {
    param($h) [bool]$hiddenPluginVisible[[int]$h]
} { $hiddenPluginForeground } {
    param($h)
    $script:hiddenPluginActivations += [int]$h
    $script:hiddenPluginForeground = [int]$h
    return $true
} {
    param($key,$up)
} {} {} 2
Assert ($hiddenPluginWindows[222] -eq $true) 'hidden plugin hwnd may remain alive after visual close'
Assert ($hiddenPluginResult.session_closed -eq $true) 'hidden plugin with restored main is a closed session'
Assert (($hiddenPluginActivations -join ',') -eq '111') 'cleanup never reactivates an already hidden plugin hwnd'

$layerEvents = @()
$layerDetailOpen = $true
$layerCompleted = $false
$layerResult = Complete-WeixinLayeredSession `
    ([ref]$layerDetailOpen) ([ref]$layerCompleted) {
        $script:layerEvents += 'close_detail'
    } {
        $script:layerEvents += 'verify_list'
        return $true
    } {
        $script:layerEvents += 'close_plugin'
        [pscustomobject]@{session_closed=$true}
    }
Assert (($layerEvents -join ',') -eq 'close_detail,verify_list,close_plugin') 'detail cleanup proves list before plugin close'
Assert (-not $layerDetailOpen -and $layerResult.session_closed) 'detail state closes exactly one layer at a time'

$listEvents = @()
$listDetailOpen = $false
$listCompleted = $false
[void](Complete-WeixinLayeredSession `
    ([ref]$listDetailOpen) ([ref]$listCompleted) {
        $script:listEvents += 'unexpected_detail_close'
    } {
        $script:listEvents += 'unexpected_list_verify'
        return $true
    } {
        $script:listEvents += 'close_plugin'
        [pscustomobject]@{session_closed=$true}
    })
Assert (($listEvents -join ',') -eq 'close_plugin') 'result list state sends only plugin close'

$mainEvents = @()
$mainDetailOpen = $false
$mainCompleted = $true
$mainResult = Complete-WeixinLayeredSession `
    ([ref]$mainDetailOpen) ([ref]$mainCompleted) {
        $script:mainEvents += 'unexpected_detail_close'
    } {
        $script:mainEvents += 'unexpected_list_verify'
        return $true
    } {
        $script:mainEvents += 'unexpected_plugin_close'
        [pscustomobject]@{session_closed=$true}
    }
Assert ($mainEvents.Count -eq 0 -and $mainResult.already_closed) 'main window terminal state sends no extra Ctrl+W'

$failedLayerDetailOpen = $true
$failedLayerCompleted = $false
$failedLayerRejected = $false
try {
    Complete-WeixinLayeredSession `
        ([ref]$failedLayerDetailOpen) ([ref]$failedLayerCompleted) {} {$false} {
            throw 'plugin close must not run before list recovery'
        }
} catch { $failedLayerRejected = $_.Exception.Message -eq 'SESSION_CLEANUP_FAILED' }
Assert $failedLayerRejected 'failed detail recovery blocks plugin Ctrl+W'

$completed = $false
$cleanupCalls = 0
$onceResult = Complete-WeixinPluginSession ([ref]$completed) {
    $script:cleanupCalls++
    [pscustomobject]@{session_closed=$true}
}
$duplicateResult = Complete-WeixinPluginSession ([ref]$completed) {
    $script:cleanupCalls++
    [pscustomobject]@{session_closed=$true}
}
Assert ($onceResult.session_closed -and $duplicateResult.already_closed) 'duplicate cleanup is idempotent'
Assert ($cleanupCalls -eq 1) 'duplicate cleanup executes close exactly once'

foreach ($terminal in @('found','not_found','inconclusive','blocked','search_success','exception')) {
    $terminalCompleted = $false
    $terminalCleanupCalls = 0
    try {
        if ($terminal -eq 'exception') { throw 'injected operation failure' }
    } catch {
    } finally {
        [void](Complete-WeixinPluginSession ([ref]$terminalCompleted) {
            $script:terminalCleanupCalls++
            [pscustomobject]@{session_closed=$true}
        })
    }
    Assert ($terminalCleanupCalls -eq 1) "terminal cleanup exactly once: $terminal"
}

$badCleanupIdentity = $validPluginIdentity.PSObject.Copy()
$badCleanupIdentity.ProcessPath = 'C:\untrusted\WeChatAppEx.exe'
$wrongWindowRejected = $false
try {
    Close-WeixinPluginSession 222 111 {
        param($h)
        if ($h -eq 222) { $badCleanupIdentity } else { $validMainIdentity }
    } {$true} {$true} {222} {$true} {} {} {} 1
} catch { $wrongWindowRejected = $_.Exception.Message -eq 'SESSION_CLEANUP_FAILED' }
Assert $wrongWindowRejected 'cleanup rejects wrong plugin identity'

$closeTimeoutRejected = $false
try {
    Close-WeixinPluginSession 222 111 {
        param($h)
        if ($h -eq 222) { $validPluginIdentity } else { $validMainIdentity }
    } {$true} {$true} {222} {$true} {} {} {} 1
} catch { $closeTimeoutRejected = $_.Exception.Message -eq 'SESSION_CLEANUP_FAILED' }
Assert $closeTimeoutRejected 'cleanup close timeout is fatal'

$wrongForegroundRejected = $false
try {
    Close-WeixinPluginSession 222 111 {
        param($h)
        if ($h -eq 222) { $validPluginIdentity } else { $validMainIdentity }
    } {$true} {
        param($h)
        if ($h -eq 222) { $false } else { $true }
    } {333} {
        param($h)
        # Simulate an unrelated trusted-looking plugin retaining foreground.
        return $true
    } {} {} {} 1
} catch { $wrongForegroundRejected = $_.Exception.Message -eq 'SESSION_CLEANUP_FAILED' }
Assert $wrongForegroundRejected 'hidden plugin cleanup rejects wrong foreground hwnd'

$missingMainRejected = $false
$missingMainPluginExists = $true
try {
    Close-WeixinPluginSession 222 111 {
        param($h)
        if ($h -eq 222) { $validPluginIdentity } else { $validMainIdentity }
    } {
        param($h)
        if ($h -eq 222) { $script:missingMainPluginExists } else { $false }
    } {
        param($h)
        if ($h -eq 222) { $script:missingMainPluginExists } else { $false }
    } {222} {$true} {
        param($key,$up)
        if ($key -eq 'W' -and $up) { $script:missingMainPluginExists=$false }
    } {} {} 1
} catch { $missingMainRejected = $_.Exception.Message -eq 'SESSION_CLEANUP_FAILED' }
Assert $missingMainRejected 'cleanup fails when main window disappears'

$entryCleanupText = [IO.File]::ReadAllText(
    (Join-Path $PSScriptRoot '..\scripts\wechat-souyisou.ps1'))
$cleanupCatchIndex = $entryCleanupText.IndexOf(
    '} catch { $sessionCleanupFailure = $_ }',
    [StringComparison]::Ordinal)
$clipboardRestoreIndex = $entryCleanupText.IndexOf(
    'if ($clipboardCaptured)',
    $cleanupCatchIndex,
    [StringComparison]::Ordinal)
$mutexReleaseIndex = $entryCleanupText.IndexOf(
    'if ($mutex)',
    $clipboardRestoreIndex,
    [StringComparison]::Ordinal)
$cleanupRethrowIndex = $entryCleanupText.IndexOf(
    'if ($sessionCleanupFailure)',
    $mutexReleaseIndex,
    [StringComparison]::Ordinal)
Assert (
    $cleanupCatchIndex -ge 0 -and
    $clipboardRestoreIndex -gt $cleanupCatchIndex -and
    $mutexReleaseIndex -gt $clipboardRestoreIndex -and
    $cleanupRethrowIndex -gt $mutexReleaseIndex
) 'cleanup failure is rethrown only after clipboard and mutex cleanup'
$strictFinallyStart = $entryCleanupText.IndexOf(
    '# search/collect 的严格终态',
    [StringComparison]::Ordinal)
$detailReturnIndex = $entryCleanupText.IndexOf(
    "& `$send @('CTRL','W') `$pluginGuard",
    $strictFinallyStart,
    [StringComparison]::Ordinal)
$detailVerifyIndex = $entryCleanupText.IndexOf(
    'Test-ResultPageEvidence $returned $text',
    $detailReturnIndex,
    [StringComparison]::Ordinal)
$pluginCloseIndex = $entryCleanupText.IndexOf(
    '} $cleanupSession)',
    $detailVerifyIndex,
    [StringComparison]::Ordinal)
Assert (
    $strictFinallyStart -ge 0 -and
    $detailReturnIndex -gt $strictFinallyStart -and
    $detailVerifyIndex -gt $detailReturnIndex -and
    $pluginCloseIndex -gt $detailVerifyIndex
) 'open detail returns to result list before plugin session closes'
$probeBranchIndex = $entryCleanupText.IndexOf(
    "if (`$Command -eq 'probe')",
    [StringComparison]::Ordinal)
$openBranchIndex = $entryCleanupText.IndexOf(
    "if (`$Command -eq 'open')",
    [StringComparison]::Ordinal)
$cleanupEnabledIndex = $entryCleanupText.IndexOf(
    '$requiresSessionCleanup = $true',
    [StringComparison]::Ordinal)
Assert (
    $probeBranchIndex -ge 0 -and
    $openBranchIndex -gt $probeBranchIndex -and
    $cleanupEnabledIndex -gt $openBranchIndex
) 'probe and open remain outside per-person cleanup ownership'

$events=@()
try {
    Invoke-SafeKeyChord @('CTRL','C') {param($k,$up) $script:events += "$k/$up"; if ($k -eq 'C' -and -not $up) { throw 'injected' }} {} {$true}
} catch {}
Assert (($events -join ',') -eq 'CTRL/False,C/False,C/True,CTRL/True') 'all pressed keys released'
$guardEvents=@()
try { Invoke-SafeKeyChord @('CTRL','C') {param($k,$up) $script:guardEvents += "$k/$up"} {} {$false} } catch {}
Assert ($guardEvents.Count -eq 0) 'foreground guard blocks all input'
$mouseEvents=@()
try {
    Invoke-SafeMouseClick {
        param($up)
        $script:mouseEvents += $(if($up){'up'}else{'down'})
        if (-not $up) { throw 'injected mouse failure' }
    } {$true}
} catch {}
Assert (($mouseEvents -join ',') -eq 'down,up') 'mouse button released after injected failure'
$guardedMouseEvents=@()
try { Invoke-SafeMouseClick {param($up) $script:guardedMouseEvents += $up} {$false} } catch {}
Assert ($guardedMouseEvents.Count -eq 0) 'foreground guard blocks mouse input'
Assert ((Get-RedactedSummary '联系人王承展 18511597486') -notlike '*18511597486*') 'PII redacted from summaries'

$artifactDir = Join-Path ([IO.Path]::GetTempPath()) ('wechat-rpa-test-' + [guid]::NewGuid().ToString('N'))
try {
    $protectedArtifact = Protect-EvidenceArtifact $artifactDir @{ text='王承展 18511597486'; links=@('https://example.test') }
    $artifactBytes = [IO.File]::ReadAllText($protectedArtifact.artifact_ref)
    Assert ($artifactBytes -notlike '*18511597486*') 'artifact encrypted at rest'
    $roundtrip = Unprotect-EvidenceArtifact $protectedArtifact.artifact_ref
    Assert ($roundtrip.text -eq '王承展 18511597486') 'dpapi current-user roundtrip'
    $failureArtifact = Protect-EvidenceArtifact $artifactDir @{
        kind='failure'; error_code='DETAIL_TEXT_INVALID'
        message=Get-RedactedSummary '详情失败 18511597486'
    }
    $failureRoundtrip = Unprotect-EvidenceArtifact $failureArtifact.artifact_ref
    Assert ($failureRoundtrip.message -notlike '*18511597486*') 'failure evidence is redacted and encrypted'
    $locateFailureArtifact = Protect-EvidenceArtifact $artifactDir @{
        kind='failure';error_code='LOCATOR_POINTS_INVALID';stage='points'
        reason='invalid_points';ordinal=1;text_length=0;hashes=@();region=$null
    }
    $locateFailureRoundtrip = Unprotect-EvidenceArtifact $locateFailureArtifact.artifact_ref
    Assert ($locateFailureRoundtrip.stage -eq 'points' -and
        $locateFailureRoundtrip.error_code -eq 'LOCATOR_POINTS_INVALID') 'locator error artifact stage and code'
    Assert (($locateFailureRoundtrip | ConvertTo-Json -Compress) -notlike '*18511597486*') 'locator error artifact has no PII'
    $cleanupFailureArtifact = Protect-EvidenceArtifact $artifactDir @{
        kind='failure';error_code='SESSION_CLEANUP_FAILED';stage='cleanup'
        result_artifact_id='safe-artifact-id';captured_at=[DateTimeOffset]::Now.ToString('o')
    }
    $cleanupFailureRoundtrip = Unprotect-EvidenceArtifact $cleanupFailureArtifact.artifact_ref
    Assert ($cleanupFailureRoundtrip.stage -eq 'cleanup' -and
        $cleanupFailureRoundtrip.error_code -eq 'SESSION_CLEANUP_FAILED') 'cleanup failure artifact stage'
    Assert (($cleanupFailureRoundtrip | ConvertTo-Json -Compress) -notlike '*18511597486*') 'cleanup failure artifact has no PII'
    $inconclusiveArtifact = Protect-EvidenceArtifact $artifactDir @{
        kind='collect_result';status='inconclusive'
        records=@([pscustomobject]@{text='王承展 18511597486';ocr_text='协会 王承展 18511597486'})
    }
    $inconclusiveBytes = [IO.File]::ReadAllText($inconclusiveArtifact.artifact_ref)
    Assert ($inconclusiveBytes -notlike '*18511597486*') 'inconclusive evidence encrypted at rest'
    $inconclusiveRoundtrip = Unprotect-EvidenceArtifact $inconclusiveArtifact.artifact_ref
    Assert ($inconclusiveRoundtrip.records[0].text -eq '王承展 18511597486') 'inconclusive evidence retained for manual review'
    $semanticEvidence = "王承展`n手机 18511597486"
    $semanticArtifact = Protect-EvidenceArtifact $artifactDir @{
        kind='collect_result';status='found'
        records=@([pscustomobject]@{text=$semanticEvidence;ocr_text=$null})
        found_result=[pscustomobject]@{
            matched=$true;person_name='王承展';mobile='18511597486'
            evidence_quote=$semanticEvidence;confidence=1.0;reason='llm_semantic_binding'
        }
    }
    $extractScript = Join-Path $PSScriptRoot '..\scripts\extract-mobile.ps1'
    $semanticOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $extractScript `
        -ArtifactPath $semanticArtifact.artifact_ref -AssociationName '测试协会' -PersonName '王承展'
    Assert ($LASTEXITCODE -eq 0) 'encrypted LLM found result extraction exits zero'
    $semanticResult = $semanticOutput | ConvertFrom-Json
    Assert ($semanticResult.matched -eq $true -and
        $semanticResult.mobile -eq '18511597486') 'helper preserves validated LLM semantic binding'
} finally {
    if (Test-Path -LiteralPath $artifactDir) { Remove-Item -LiteralPath $artifactDir -Recurse -Force }
}

$opened=@(); $closed=0
$items=@(
    [pscustomobject]@{title='a';source='s';date='d';text='无号码';scroll_after=$false},
    [pscustomobject]@{title='a';source='s';date='d';text='重复';scroll_after=$false},
    [pscustomobject]@{title='b';source='s';date='d';text='王承展 18511597486';scroll_after=$false}
)
$result=Invoke-CollectFramework $items 10 '协会' '王承展' `
    {param($i) $script:opened += $i.title} {param($i) $i.text} {$script:closed++} {} $null
Assert ($result.status -eq 'found' -and $result.checked -eq 2) 'collect stop/dedupe'
Assert ($closed -eq 2) 'detail always closed'
Assert ($result.evidence_items.Count -eq 2 -and $result.evidence_items[0].text -eq '无号码') 'all checked detail evidence retained'
$many = 1..12 | ForEach-Object { [pscustomobject]@{title="t$_";source='s';date='d';text='无号码';scroll_after=$false} }
$limited = Invoke-CollectFramework $many 10 '协会' '姓名' {} {param($i) $i.text} {} {} $null
Assert ($limited.checked -eq 10) 'collect hard limit ten'
$elevenItems = 1..11 | ForEach-Object {
    [pscustomobject]@{
        title="limit-$_";source='s';date='d'
        text=if($_ -eq 11){'王承展 18511597486'}else{'无号码'}
        scroll_after=$false
    }
}
$elevenOpened=0
$elevenResult=Invoke-CollectFramework $elevenItems 10 '协会' '王承展' {
    $script:elevenOpened++
} {param($item)$item.text} {} {} $null
Assert ($elevenResult.status -eq 'not_found' -and $elevenResult.checked -eq 10) 'eleventh result can never be found'
Assert ($elevenOpened -eq 10) 'only first ten details are clicked'
$providerOpened=0; $providerClosed=0
$providerFailureResult = Invoke-CollectFramework @(
    [pscustomobject]@{title='first';source='s';date='d';text='王承展 18511597486';scroll_after=$false},
    [pscustomobject]@{title='must-not-open';source='s';date='d';text='无号码';scroll_after=$false}
) 10 '协会' '王承展' {$script:providerOpened++} {param($item) $item.text} {
    $script:providerClosed++
} {} {throw 'provider down'}
Assert ($providerFailureResult.status -eq 'inconclusive') 'provider failure never becomes not_found'
Assert ($providerOpened -eq 1 -and $providerClosed -eq 1) 'provider failure stops further clicks'

$dry = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $entry -Command search `
    -AssociationName '中国游艺设备游乐园协会' -PersonName '王承展'
Assert ($LASTEXITCODE -eq 0) 'dry exit'
$json = $dry | ConvertFrom-Json
Assert (-not $json.executed -and $json.query -like '*王承展*') 'dry no execution'

$stdinStart = New-Object Diagnostics.ProcessStartInfo
$stdinStart.FileName = (Get-Command powershell.exe -ErrorAction Stop).Source
$stdinStart.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$entry`" -ReadStdin"
$stdinStart.UseShellExecute = $false
$stdinStart.RedirectStandardInput = $true
$stdinStart.RedirectStandardOutput = $true
$stdinStart.CreateNoWindow = $true
$stdinProcess = New-Object Diagnostics.Process
$stdinProcess.StartInfo = $stdinStart
$previousInputEncoding = [Console]::InputEncoding
try {
    [Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
    [void]$stdinProcess.Start()
    $stdinWriter = $stdinProcess.StandardInput
} finally { [Console]::InputEncoding = $previousInputEncoding }
$stdinWriter.WriteLine('{"command":"collect","association_name":"协会","person_name":"姓名","limit":99}')
$stdinWriter.Close()
$stdin = $stdinProcess.StandardOutput.ReadToEnd()
$stdinProcess.WaitForExit()
$stdinExitCode = $stdinProcess.ExitCode
$stdinProcess.Dispose()
$stdinJson = $stdin | ConvertFrom-Json
Assert ($stdinExitCode -eq 0 -and $stdinJson.limit -eq 10 -and -not $stdinJson.executed) 'stdin and limit'
Assert ($entryText -match 'stage=\$stage') 'failure artifact carries stage'
Assert ($entryText -match 'process_basename') 'failure artifact carries safe process identity'
Assert ($entryText -notmatch 'result_artifact_ref') 'failure artifact excludes full artifact path'
Assert ($entryText -notmatch 'foreground=.*title|title=\\$failureTitle') 'failure artifact excludes foreground title'
foreach ($requiredStage in @(
    'enum','activate','open','search','copy','list_judge','locate','click',
    'detail_copy','detail_judge','close','recover','scroll','cleanup'
)) {
    Assert ($entryText -match ([regex]::Escape("`$stage = '$requiredStage'"))) "stage present: $requiredStage"
}
Assert ($entryText -match 'Invoke-EvidenceJudge\s+\$text') 'project LLM judges list before details'
Assert ($entryText -match "kind='result_page_unbounded'") 'unbounded list artifact is labeled'
Assert ($entryText -match "reason='screenshot_unchanged'") 'click failure metadata recorded'
Assert ($entryText -match "reason='detail_evidence_invalid'") 'invalid detail metadata recorded'
Assert ($entryText -match 'text_length=\$detail.Length') 'failure metadata keeps length, not body'
Assert ($entryText -match 'list_judge_status=') 'list judge status retained in encrypted diagnostics'
Assert ($entryText -match 'list_judge_reason_code=') 'list judge safe reason code retained in encrypted diagnostics'
Assert (
    $entryText -match "source='result_page_unbounded';list_artifact_id=\`$artifact\.artifact_id\s+list_judge_status=\`$listJudgeStatus\s+list_judge_reason_code=\`$listJudgeReasonCode"
) 'direct list hit artifact retains safe judge diagnostics'
Assert ($entryText -match "source='result_page_unbounded'") 'found list result retains evidence source contract'
Assert ($entryText -match 'Test-NewOcrViewportHash \$ocrHashes \$candidateOcrHash') 'duplicate ocr viewport stops'
Assert ($entryText -match "'locator_read'") 'locator read diagnostic stage'
Assert ($entryText -match "'WINDOW_RECT_FAILED'") 'window rect distinct error'
Assert ($entryText -match 'Complete-WeixinPluginSession') 'search and collect use strict cleanup helper'
Assert ($entryText -match '\$sessionCleanupAttempted\s*=\s*\$true') 'cleanup attempt is recorded before execution'
Assert ($entryText -match '-not \$sessionCleanupAttempted') 'failed explicit cleanup is not retried in finally'
Assert ($entryText -match '\$detailCloseInProgress\s*=\s*\$true') 'detail close is marked before Ctrl+W'
Assert ($entryText -match '-not \$detailCloseInProgress') 'failed detail recovery is not retried in finally'
Assert ($entryText -match 'session_closed=') 'successful session result reports closure'
Assert ($entryText -match "'SESSION_CLEANUP_FAILED'") 'cleanup failure blocks next batch item'
Assert ($entryText -match '\[ValidateRange\(10000,60000\)\]\[int\]\$SearchReadyTimeoutMilliseconds\s*=\s*15000') 'slow network readiness timeout defaults above ten seconds'
Assert ($entryText -match '\$null\s+-ne\s+\$inputObject\.search_ready_timeout_milliseconds') 'stdin zero timeout reaches explicit range validation'
Assert ($entryText -match "catch\s*\{\s*throw 'INVALID_SEARCH_READY_TIMEOUT'\s*\}") 'stdin timeout conversion errors use stable code'
Assert ($entryText -match '\$stdinSearchReadyTimeout\s+-lt\s+10000') 'stdin timeout validates before assigning validated parameter'
Assert ($entryText -match 'Test-SearchResultReady\s+\$candidateText\s+\$query') 'search readiness uses copied result signal'
Assert ($entryText -match '\$readySamples\s+-lt\s+2') 'search readiness requires stable repeated evidence'
Assert ($entryText -match "'SEARCH_RESULTS_TIMEOUT'") 'unready search fails explicitly before evidence'
Assert ((Get-Content -Raw -LiteralPath $lib) -match "'LOCATOR_POINTS_INVALID'") 'invalid points distinct error'
Write-Output '{"ok":true,"tests":163}'
