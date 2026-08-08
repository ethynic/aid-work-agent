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
Assert ((Get-Content -LiteralPath $lib -Encoding UTF8 -Raw) -match `
    '\$Checked\s+-ge\s+\$Limit\s+-and\s+\$Failures\s+-eq\s+0') `
    'not_found requires every limited detail to succeed'
Assert ((Get-MobileCandidates '王承展 18511597486').Count -eq 1) 'mobile extraction'
$validJudgeResult = [pscustomobject]@{
    matched=$true;person_name='王承展';mobile='18511597486'
    evidence_quote='王承展 18511597486';confidence=1.0;reason='same line'
}
Assert (Test-JudgeResult $validJudgeResult '王承展 18511597486' '王承展') 'judge validation'
Assert (-not (Test-JudgeResult ([pscustomobject]@{matched=$true;person_name='王承展';mobile='13900000000';evidence_quote='王承展 13900000000';confidence=1.0;reason='same line'}) '王承展 18511597486' '王承展')) 'hallucination rejected'
# 删除 evidence_quote 逐字校验后，LLM 的归属判断不再被二次否决：
# 只要手机号在原文中出现且格式合法即接受，'其他人 13900000000' 不再导致拒绝。
Assert (Test-JudgeResult ([pscustomobject]@{matched=$true;person_name='王承展';mobile='13900000000';evidence_quote='王承展 13900000000';confidence=1.0;reason='same line'}) "王承展`n其他人 13900000000" '王承展') 'model binding accepted without verbatim quote check'
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
$exitCode = $null
try { & $exitJudge ([pscustomobject]@{association_name='协会';person_name='姓名';text='证据'}) }
catch { $exitCode = $_.Exception.Message }
Assert ($exitCode -eq 'JUDGE_PROCESS_FAILED') 'external judge nonzero exit has a stable content-free code'
$exitResult = Invoke-EvidenceJudge '王承展 18511597486' '协会' '王承展' $exitJudge
Assert ($exitResult.inconclusive -eq $true) 'external judge nonzero exit is inconclusive'
Assert ((Get-CfHtmlLinks '<a href="https://example.test/a">x</a>').Count -eq 1) 'html link'
[void](Add-Type -AssemblyName System.Drawing)
$openCalls=0
$opened = Invoke-LimitedTrustedOpen {
    $script:openCalls++
} {
    if ($script:openCalls -eq 2) { [pscustomobject]@{Hwnd=222} } else { $null }
} 2
Assert ($opened.Hwnd -eq 222 -and $openCalls -eq 2) 'untrusted first open safely retries only once'
$openFailed=$false; $failedOpenCalls=0
try {
    [void](Invoke-LimitedTrustedOpen {$script:failedOpenCalls++} {$null} 2)
} catch { $openFailed=$_.Exception.Message -eq 'SOUYISOU_WINDOW_UNTRUSTED' }
Assert ($openFailed -and $failedOpenCalls -eq 2) 'plugin open retry is finite'
$cleanOpenCalls=0; $cleanCloseCalls=0
$firstCleanIdentity = Invoke-LimitedTrustedOpen {
    $script:cleanOpenCalls++
} { [pscustomobject]@{Hwnd=222} } 1
if ($firstCleanIdentity) { $cleanCloseCalls++ }
$secondCleanIdentity = Invoke-LimitedTrustedOpen {
    $script:cleanOpenCalls++
} { [pscustomobject]@{Hwnd=333} } 1
Assert (
    $secondCleanIdentity.Hwnd -eq 333 -and
    $cleanOpenCalls -eq 2 -and
    $cleanCloseCalls -eq 1
) 'dirty-session cleanup and reopen consume exactly two total open attempts'
Assert (-not (Test-ListHasActionableCandidates '无目标人员' '张三' @('https://example.cn'))) 'irrelevant list has no actionable candidate'
Assert (Test-ListHasActionableCandidates '结果 张三' '张三' @()) 'person text remains actionable when CF_HTML has no links'
Assert (Test-ListHasActionableCandidates '结果 张三' '张三' @('https://example.cn')) 'person and detail link form actionable candidate'
$capturedListPayload=$null
$textOnlyListJudge=Invoke-EvidenceJudge '协会搜索列表仅有相关文字' '协会' '张三' {
    param($payload)
    $script:capturedListPayload=$payload
    [pscustomobject]@{
        matched=$false;person_name='';mobile='';evidence_quote='';confidence=0;reason='not_related'
    }
}
Assert (
    $textOnlyListJudge.matched -eq $false -and
    (($capturedListPayload.PSObject.Properties.Name | Sort-Object) -join ',') -eq
        'association_name,person_name,text' -and
    $capturedListPayload.text -eq '协会搜索列表仅有相关文字'
) 'external list judge receives text-only payload even when the list has no mobile'
$builtInListJudge = Invoke-EvidenceJudge `
    '主办单位：中国游艺机游乐园协会 联系人：许萍：13901159531' `
    '中国游艺机游乐园协会' '许萍' $null
Assert (
    $builtInListJudge.matched -and
    $builtInListJudge.reason -eq 'deterministic_same_clause'
) 'built-in list judge uses exact same-clause binding before card location'
$crossClauseJudge = Invoke-EvidenceJudge `
    '张三手机：13912345678；陈戟另无手机' '协会' '陈戟' $null
Assert (-not $crossClauseJudge.matched) 'built-in list judge rejects another person mobile on same physical line'
$sharedContactJudge = Invoke-EvidenceJudge `
    '联系人刘甲、陈戟；联系电话：13912345678' '协会' '陈戟' $null
Assert (-not $sharedContactJudge.matched) 'built-in list judge rejects ambiguous multi-person contact binding'

Assert (Test-DetailEvidence ('协会会议联系人王承展，正文内容。' * 10) '列表内容' '协会' '王承展') 'detail evidence'
Assert (-not (Test-DetailEvidence '列表内容' '列表内容' '协会' '王承展')) 'list is not detail'
Assert (Test-DetailNeedsOcr ('协会联系人图片附件。' * 12) '列表内容' '协会' '王承展') 'image detail needs ocr'
Assert (Test-DetailNeedsOcr ('协会会议联系人王承展正文。' * 12) '列表内容' '协会' '王承展') 'detail without target mobile uses ocr fallback'
Assert (Test-DetailNeedsOcr '' '列表结果正文' '协会' '王承展') 'pure image detail without copied marker uses ocr'
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
    ok=$true;text='王承展 18511597486';image_count=2
}) 2) -eq '王承展 18511597486') 'valid OCR schema'
foreach ($maliciousOcrResult in @(
    [pscustomobject]@{ok='true';text='王承展 18511597486';image_count=2},
    [pscustomobject]@{ok=$true;text=@('王承展 18511597486');image_count=2},
    [pscustomobject]@{ok=$true;text='王承展 18511597486';image_count=3},
    [pscustomobject]@{ok=$true;text='';image_count=2}
)) {
    $ocrRejected=$false
    try { [void](Get-ValidatedOcrText $maliciousOcrResult 2) } catch { $ocrRejected=$true }
    Assert $ocrRejected 'malicious OCR result rejected'
}
Assert (-not (Test-OcrEvidenceAttribution '' '协会' '王承展')) 'empty OCR cannot be attributed'
Assert (-not (Test-OcrEvidenceAttribution '协会 客服 13900000000' '协会' '王承展')) 'OCR without target person rejected'
# 去掉协会名检查后，OCR 只要含目标人名即通过（去空格匹配）。
Assert (Test-OcrEvidenceAttribution '王承展 18511597486' '协会' '王承展') 'OCR with target person passes without association name'
# 人名含空格时去空格后匹配（如"陈 戟"）。
Assert (Test-OcrEvidenceAttribution '联系人陈 戟 13910906310' '协会' '陈戟') 'OCR person name matches after whitespace removal'
Assert (-not (Test-OcrEvidenceAttribution '登录失败 广告电话 13900000000' '协会' '王承展')) 'unknown page OCR rejected'
Assert (Test-OcrEvidenceAttribution '协会 联系人王承展 18511597486' '协会' '王承展') 'target OCR attribution'
$uniqueHashes=@()
foreach($candidateHash in @('hash-1','hash-2')) {
    Assert (Test-NewOcrViewportHash $uniqueHashes $candidateHash) 'new OCR viewport accepted'
    $uniqueHashes += $candidateHash
}
Assert ($uniqueHashes.Count -eq 2) 'two distinct OCR viewports retained'
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
Assert (-not (Test-SearchResultReady $resultPageSample '协会 王承展 联系人' '协会' '王承展' $false)) 'unverified input can never produce ready state'

$failedRetryEvents = @(); $failedRetryRejected = $false
try {
    [void](Invoke-VerifiedWeixinUaSearchSubmission '协会 张三' {$true} {
        '旧文本一'
    } {
        $script:failedRetryEvents += 'ENTER'
    } $true @{})
} catch { $failedRetryRejected = $_.Exception.Message -eq 'SEARCH_INPUT_READBACK_MISMATCH' }
Assert (
    $failedRetryRejected -and
    $failedRetryEvents -notcontains 'ENTER'
) 'first readback mismatch fails without unsafe keyboard refocus or search submission'

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
$freshPluginIdentity = $validPluginIdentity.PSObject.Copy()
$freshPluginIdentity.Hwnd = 333
Assert (Test-FreshWeixinPluginIdentity $freshPluginIdentity 222 111) 'fresh trusted plugin hwnd accepted'
Assert (-not (Test-FreshWeixinPluginIdentity $validPluginIdentity 222 111)) 'closed plugin hwnd cannot be reused'
Assert (-not (Test-FreshWeixinPluginIdentity $validPluginIdentity 333 222)) 'main hwnd cannot be accepted as fresh plugin'
Assert (-not (Test-FreshWeixinPluginIdentity $wrongPluginTitle 333 111)) 'fresh plugin still requires full trusted identity'

$firstListSession = New-WeixinWindowSession 111 @(222,444)
[void](Add-WeixinWindowSessionForeground `
    $firstListSession $validPluginIdentity 'list' -AllowPreexisting)
Assert (
    $firstListSession.ListHwnd -eq 222 -and
    $firstListSession.CurrentHwnd -eq 222 -and
    $firstListSession.OwnedPluginHwnds.Contains([int64]222)
) 'only the first list registration may adopt the foreground plugin opened by the shortcut'
$detailPreexistingRejected=$false
try {
    $unrelatedDetail = $validPluginIdentity.PSObject.Copy()
    $unrelatedDetail.Hwnd = 444
    [void](Add-WeixinWindowSessionForeground `
        $firstListSession $unrelatedDetail 'detail' -AllowPreexisting)
} catch { $detailPreexistingRejected=$_.Exception.Message -eq 'ALLOW_PREEXISTING_INVALID' }
Assert ($detailPreexistingRejected) 'detail role cannot bypass the preexisting-plugin boundary'
$secondListAdoptionRejected=$false
try {
    [void](Add-WeixinWindowSessionForeground `
        $firstListSession $validPluginIdentity 'list' -AllowPreexisting)
} catch { $secondListAdoptionRejected=$_.Exception.Message -eq 'ALLOW_PREEXISTING_INVALID' }
Assert ($secondListAdoptionRejected) 'preexisting adoption cannot be reused after initial list ownership'
$ordinaryPreexistingRejected=$false
$strictSession = New-WeixinWindowSession 111 @(222)
try {
    [void](Add-WeixinWindowSessionForeground $strictSession $validPluginIdentity 'detail')
} catch { $ordinaryPreexistingRejected=$_.Exception.Message -eq 'PREEXISTING_PLUGIN_REJECTED' }
Assert ($ordinaryPreexistingRejected) 'detail registration rejects an unowned preexisting plugin'

$qtRecoverySession = New-WeixinWindowSession 111 @()
[void](Add-WeixinWindowSessionForeground `
    $qtRecoverySession $validPluginIdentity 'list')
$qtDetailIdentity = $validPluginIdentity.PSObject.Copy()
$qtDetailIdentity.Hwnd = 333
[void](Add-WeixinWindowSessionForeground `
    $qtRecoverySession $qtDetailIdentity 'detail')
$qtForegroundReads=0; $qtCallbackFailureNormalized=$false
try {
    [void](Invoke-WeixinWindowSessionReturnToList `
        $qtRecoverySession {
            $script:qtForegroundReads++
            if ($script:qtForegroundReads -eq 1) {
                $qtDetailIdentity
            } else {
                [pscustomobject]@{
                    Hwnd=111; ProcessPath='C:\Program Files\Tencent\Weixin\Weixin.exe'
                    ClassName='Qt51514QWindowIcon'; Title='微信'
                }
            }
        } {} {} { throw 'window disappeared' } { $true } { $true })
} catch { $qtCallbackFailureNormalized=$_.Exception.Message -eq 'RECOVERY_FAILED' }
Assert ($qtCallbackFailureNormalized) 'Qt recovery callback races use stable recovery failure code'

$validMainIdentity = [pscustomobject]@{
    Hwnd=111; ProcessPath='C:\Program Files\Tencent\Weixin\Weixin.exe'
    ClassName='Qt51514QWindowIcon'; Title='微信'
}
$cleanupWindows = @{ 111=$true; 222=$true }
$cleanupVisible = @{ 111=$true; 222=$true }
$cleanupForeground = 222
$cleanupCloseTargets = @()
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
    param($h)
    $script:cleanupCloseTargets += [int]$h
    $script:cleanupWindows[[int]$h] = $false
    $script:cleanupVisible[[int]$h] = $false
    $true
} {} 2
Assert ($cleanupResult.session_closed -eq $true) 'strict cleanup reports closed session'
Assert (($cleanupActivations -join ',') -eq '111') 'cleanup activates main only after directed plugin close'
Assert (($cleanupCloseTargets -join ',') -eq '222') 'cleanup directs WM_CLOSE only to exact plugin hwnd'
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
            ClassName='Qt51514QWindowIcon'
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
    throw 'hidden plugin must not receive WM_CLOSE'
} {} 2
Assert ($hiddenPluginWindows[222] -eq $true) 'hidden plugin hwnd may remain alive after visual close'
Assert ($hiddenPluginResult.session_closed -eq $true) 'hidden plugin with restored main is a closed session'
Assert (($hiddenPluginActivations -join ',') -eq '111') 'cleanup never reactivates an already hidden plugin hwnd'

$terminalDetailMayBeOpen = $true
$terminalCompleted = $false
$terminalCloseCalls = 0
$terminalResult = Complete-WeixinPluginSession ([ref]$terminalCompleted) {
    $script:terminalCloseCalls++
    [pscustomobject]@{session_closed=$true}
}
Assert $terminalDetailMayBeOpen 'detail state remains diagnostic during top-level cleanup'
Assert ($terminalCloseCalls -eq 1) 'detail-open terminal state closes complete plugin exactly once'
Assert $terminalResult.session_closed 'detail-open terminal state accepts directed plugin close'
Assert ($libText -notmatch 'function Complete-WeixinLayeredSession') 'layered detail cleanup helper is removed'

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

$foregroundGuardCalls=0; $foregroundActivateCalls=0
$foregroundRestored = Test-OrRestoreTrustedForeground {
    $script:foregroundGuardCalls++
    $script:foregroundGuardCalls -ge 2
} {
    $script:foregroundActivateCalls++
    $true
} {}
Assert ($foregroundRestored -and $foregroundActivateCalls -eq 1) 'trusted foreground restores once then revalidates'
$foregroundFailed = Test-OrRestoreTrustedForeground {$false} {$false} {
    throw 'pause must not run after failed activation'
}
Assert (Test-WeixinMainIdentity $validMainIdentity 111) 'trusted main identity requires exact hwnd path class and title'
$wrongMainClass = $validMainIdentity.PSObject.Copy()
$wrongMainClass.ClassName = 'Chrome_WidgetWin_0'
Assert (-not (Test-WeixinMainIdentity $wrongMainClass 111)) 'main hwnd reused by another class is rejected'
$wrongMainTitle = $validMainIdentity.PSObject.Copy()
$wrongMainTitle.Title = '其他窗口'
Assert (-not (Test-WeixinMainIdentity $wrongMainTitle 111)) 'main hwnd with changed title is rejected'
Assert (-not $foregroundFailed) 'failed trusted activation remains fail closed'

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
    } {$true} {$true} {222} {$true} { throw 'wrong identity must not close' } {} 1
} catch { $wrongWindowRejected = $_.Exception.Message -eq 'PLUGIN_IDENTITY_INVALID' }
Assert $wrongWindowRejected 'cleanup rejects wrong plugin identity'

$closeRejected=$false
try {
    Close-WeixinPluginSession 222 111 {
        param($h)
        if ($h -eq 222) { $validPluginIdentity } else { $validMainIdentity }
    } {$true} {$true} {222} {$true} {$false} {} 1
} catch { $closeRejected=$_.Exception.Message -eq 'PLUGIN_CLOSE_REJECTED' }
Assert $closeRejected 'directed plugin close refusal is fatal'

$mainCloseAttempted=$false; $mainCloseRejected=$false
try {
    Close-WeixinPluginSession 111 111 { $validMainIdentity } {$true} {$true} {111} {$true} {
        $script:mainCloseAttempted=$true
        $true
    } {} 1
} catch { $mainCloseRejected=$_.Exception.Message -eq 'PLUGIN_IDENTITY_INVALID' }
Assert ($mainCloseRejected -and -not $mainCloseAttempted) 'cleanup never directs close to main hwnd'

$closeTimeoutRejected = $false
try {
    Close-WeixinPluginSession 222 111 {
        param($h)
        if ($h -eq 222) { $validPluginIdentity } else { $validMainIdentity }
    } {$true} {$true} {222} {$true} {$true} {} 1
} catch { $closeTimeoutRejected = $_.Exception.Message -eq 'PLUGIN_CLOSE_TIMEOUT' }
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
    } { throw 'hidden plugin must not close' } {} 1
} catch { $wrongForegroundRejected = $_.Exception.Message -eq 'MAIN_FOREGROUND_NOT_RESTORED' }
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
        param($h)
        $script:missingMainPluginExists=$false
        $true
    } {} 1
} catch { $missingMainRejected = $_.Exception.Message -eq 'MAIN_WINDOW_MISSING' }
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
$pluginCloseIndex = $entryCleanupText.IndexOf(
    'Complete-WeixinPluginSession',
    $strictFinallyStart,
    [StringComparison]::Ordinal)
$strictFinallyText = $entryCleanupText.Substring(
    $strictFinallyStart,
    $cleanupCatchIndex - $strictFinallyStart)
Assert (
    $strictFinallyStart -ge 0 -and
    $pluginCloseIndex -gt $strictFinallyStart -and
    $strictFinallyText -notmatch "CTRL','W" -and
    $strictFinallyText -notmatch 'Test-ResultPageEvidence'
) 'terminal cleanup only closes the complete trusted plugin session'
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
    $cleanupFailureArtifact = Protect-EvidenceArtifact $artifactDir @{
        kind='failure';error_code='PLUGIN_CLOSE_TIMEOUT';stage='cleanup'
        result_artifact_id='safe-artifact-id';captured_at=[DateTimeOffset]::Now.ToString('o')
    }
    $cleanupFailureRoundtrip = Unprotect-EvidenceArtifact $cleanupFailureArtifact.artifact_ref
    Assert ($cleanupFailureRoundtrip.stage -eq 'cleanup' -and
        $cleanupFailureRoundtrip.error_code -eq 'PLUGIN_CLOSE_TIMEOUT') 'cleanup failure artifact stage and root code'
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
Assert (-not $json.executed -and $json.query_present -and -not $json.query) 'dry no execution without plaintext query'

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
    'enum','activate','open','input_verify','search_wait','copy','list_judge','locate','click',
    'detail_settle','detail_copy','detail_judge','close','recover','scroll','cleanup'
)) {
    Assert ($entryText -match ([regex]::Escape("`$stage = '$requiredStage'"))) "stage present: $requiredStage"
}
Assert ($entryText -match 'Invoke-EvidenceJudge\s+\$text') 'project LLM judges list before details'
Assert (
    $entryText -match '(?s)if \(\$listJudge\.inconclusive\).*?status=''inconclusive''.*?Complete-WeixinPluginSession.*?session_closed=' -and
    $entryText -notmatch "\$stage\s*=\s*'visual_verify'"
) 'list text judge timeout or exception cleans up as inconclusive without visual_verify'
Assert ($entryText -notmatch 'if \(\$judge\) \{\s*\$listJudge = Invoke-EvidenceJudge') 'built-in list judge is never gated on external LLM availability'
Assert ($entryText -match "kind='result_page_unbounded'") 'unbounded list artifact is labeled'
Assert ($entryText -match "reason='screenshot_unchanged'") 'click failure metadata recorded'
Assert ($entryText -match "reason='detail_copy_unreadable'") 'unreadable detail copy metadata recorded'
Assert ($entryText -match 'text_length=if\(\$detail\)\{\$detail.Length\}else\{0\}') `
    'unreadable copy metadata keeps only its length'
Assert ($entryText -match 'list_judge_status=') 'list judge status retained in encrypted diagnostics'
Assert ($entryText -match 'list_judge_reason_code=') 'list judge safe reason code retained in encrypted diagnostics'
Assert ($libText -match 'if \(\$result\.token_usage\)') 'inconclusive judge preserves provider token usage'
Assert (
    $entryText -match "source='result_page_unbounded';list_artifact_id=\`$artifact\.artifact_id\s+list_judge_status=\`$listJudgeStatus\s+list_judge_reason_code=\`$listJudgeReasonCode"
) 'direct list hit artifact retains safe judge diagnostics'
Assert (
    $entryText -match "kind='collect_result';status=\`$status;checked=\`$checked;failures=\`$failures\s+list_artifact_id=\`$artifact\.artifact_id\s+records=\`$records"
) 'detail result links its unbounded list artifact'
Assert ($entryText -match "source='result_page_unbounded'") 'found list result retains evidence source contract'
Assert ($entryText -match 'Test-NewOcrViewportHash \$ocrHashes \$candidateOcrHash') 'duplicate ocr viewport stops'
Assert (
    $entryText -notmatch 'visual_verify|wechat-ocr-list-verify|visualOcr|visualViewportHash|RESULT_PAGE_VISUAL_STATE_INVALID'
) 'formal result relevance never uses screenshot OCR or visual judge'
Assert ($entryText -match 'kind=''failure''[\s\S]*llm_usages=if \(\$llmUsages\)') 'failure artifact preserves available llm usage'
Assert ($entryText -match "'WINDOW_RECT_FAILED'") 'window rect distinct error'
Assert ($entryText -match 'Complete-WeixinPluginSession') 'search and collect use strict cleanup helper'
Assert ($entryText -match '\$sessionCleanupAttempted\s*=\s*\$true') 'cleanup attempt is recorded before execution'
Assert ($entryText -match '-not \$sessionCleanupAttempted') 'failed explicit cleanup is not retried in finally'
Assert ($entryText -match '\$detailCloseInProgress\s*=\s*\$true') 'detail close is marked before Ctrl+W'
Assert (([regex]::Matches(
    $entryText,
    [regex]::Escape('$returnResult = & $returnToResultPage')
)).Count -eq 4) 'each detail branch uses the shared session return transition'
$productionStart = $entryText.IndexOf(
    '$pluginIdentity = Invoke-LimitedTrustedOpen $openSouyisou $verifySouyisou 1',
    [StringComparison]::Ordinal)
$productionText = $entryText.Substring($productionStart)
Assert (
    $libText -match '\$usedIndependentDetail = \[int64\]\$current\.Hwnd -ne \[int64\]\$Session\.ListHwnd' -and
    $libText -match "& \`$SendChord @\('CTRL','W'\)" -and
    $libText -notmatch "@\('ALT','LEFT'\)"
) 'shared return transition always uses ctrl+w to close detail'
Assert ($entryText -match '\$returnToResultPage') 'detail close recovery uses the shared state transition'
Assert (
    $entryText -notmatch '\$recoveryDeadline|Test-ResultPageEvidence \$returned \$text' -and
    $entryText -match 'hwnd=\$returnedHwnd;session_closed=\$false;evidence_verified=\$true'
) 'window recovery does not depend on copied result-page content'
Assert (
    ([regex]::Matches(
        $entryText,
        '\$recoveryEvidenceUnavailable=\$true'
    )).Count -eq 4 -and
    $entryText -match 'Get-WeixinCollectStatus' -and
    $libText -match "return 'inconclusive'"
) 'natural session close remains an inconclusive content outcome'
Assert (
    $libText -match '(?s)-not \$listExists.*?foreach \(\$ownedHwnd in \$Session\.OwnedPluginHwnds\).*?\$Session\.NaturallyClosed = \$true.*?return \[int64\]0' -and
    $entryText -match 'if \(\$windowSession\.NaturallyClosed\)'
) 'trusted main with every owned plugin gone is accepted as a naturally closed session'
Assert (
    $entryText -match '(?s)if \(-not \(Test-WeixinReadableDetailCopy \$detail \$text\)\).*?\$returnResult = & \$returnToResultPage.*?continue' -and
    $entryText.IndexOf('Test-WeixinReadableDetailCopy $detail $text', [StringComparison]::Ordinal) -lt
        $entryText.IndexOf('Test-DetailNeedsOcr $detail', [StringComparison]::Ordinal)
) 'unreadable copied detail skips OCR and judge then returns for the next candidate'
Assert (
    $entryText -match '(?s)if \(-not \(Test-WeixinDetailOpened \$beforeHash \$afterHash\)\).*?\$checked\+\+; \$failures\+\+\s+continue'
) 'an unchanged click is counted and skipped without ending the collection'
Assert (
    $entryText -match '(?s)Start-Sleep -Milliseconds \$WaitMilliseconds.*?Add-WeixinWindowSessionForeground.*?Get-TrustedForegroundIdentity -RequirePlugin.*?''detail''.*?\$after = & \$capture'
) 'production accepts a new independently opened detail hwnd only immediately after a guarded click and full plugin identity validation'
$detailSettleIndex = $productionText.IndexOf("`$stage = 'detail_settle'", [StringComparison]::Ordinal)
$detailCopyIndex = $productionText.IndexOf("`$stage = 'detail_copy'", $detailSettleIndex, [StringComparison]::Ordinal)
$detailSettleText = $productionText.Substring($detailSettleIndex, $detailCopyIndex - $detailSettleIndex)
Assert (
    $detailSettleIndex -gt $productionText.IndexOf('Test-WeixinDetailOpened $beforeHash $afterHash', [StringComparison]::Ordinal) -and
    $detailSettleText -match 'Wait-WeixinDetailSettled \$pluginGuard' -and
    $detailSettleText -match '& \$assertWorkBudget 65000' -and
    ([regex]::Matches($productionText, 'Wait-WeixinDetailSettled \$pluginGuard')).Count -eq 1
) 'formal collect waits exactly once after detail confirmation and before content copy'
Assert (
    $productionText -match '(?s)& \$assertWorkBudget 95000\s+Invoke-SafeMouseClick.*?Start-Sleep -Milliseconds \$WaitMilliseconds.*?& \$assertWorkBudget 65000\s+Wait-WeixinDetailSettled'
) 'detail click and settle budgets retain the final cleanup minute'
Assert (
    $libText -match 'function Wait-WeixinDetailSettled' -and
    $libText -match '\[ValidateRange\(1,30000\)\]\[int\]\$DelayMilliseconds = 5000'
) 'detail settle delay has one testable 5000 ms default'
Assert (
    $entryText -match '\$preexistingPluginHwnds = \[Collections\.Generic\.HashSet\[int64\]\]::new\(\)' -and
    $entryText -match 'New-WeixinWindowSession \(\[int64\]\$main\.Hwnd\)' -and
    $libText -match '(?s)\$Session\.PreexistingPluginHwnds\.Contains\(\$hwnd\).*?throw ''PREEXISTING_PLUGIN_REJECTED'''
) 'production detail handoff rejects every trusted plugin hwnd that predated this session'
Assert (
    $libText -match '(?s)\$Session\.OwnedPluginHwnds\.Contains\(\[int64\]\$current\.Hwnd\).*?throw ''FOREGROUND_LOST''' -and
    $libText -match '(?s)\[int64\]\$returned\.Hwnd -ne \[int64\]\$Session\.ListHwnd.*?throw ''RECOVERY_FAILED'''
) 'detail recovery rejects external, untrusted, and unseen plugin hwnds before result-page input'
Assert (
    $libText -match '(?s)\$usedIndependentDetail -and\s+\(Test-WeixinMainIdentity \$returned.*?\$Session\.MainHwnd' -and
    $libText -match '(?s)\$listIdentity = & \$GetWindowIdentityByHwnd.*?\$Session\.OwnedPluginHwnds\.Contains.*?Test-WeixinForegroundIdentity.*?& \$ActivateWindow' -and
    $entryText -match '(?s)Invoke-WeixinWindowSessionReturnToList.*?Get-WindowIdentityByHwnd.*?IsWindow.*?\$activateWindow'
) 'independent detail may restore only its existing strictly trusted list from trusted main'
Assert (
    $entryText -match '(?s)\$pluginHwnd = \[IntPtr\]\$returnedHwnd.*?\$stage = ''cleanup''.*?Complete-WeixinPluginSession' -and
    $entryText -match 'Close-WeixinWindowSession \$windowSession'
) 'detail recovery rebinds terminal cleanup to the proven current session hwnd'
Assert (
    $libText -match '(?s)\$targets = @\(\$Session\.OwnedPluginHwnds \| Sort-Object.*?foreach \(\$target in \$targets\)'
) 'terminal cleanup closes the current detail hwnd and every previously seen session hwnd'
Assert ($entryText -notmatch 'Complete-WeixinLayeredSession') 'terminal cleanup never guesses an internal detail layer'
Assert ($entryText -match 'cleanup_error_code=if \(\$stage -eq ''cleanup''\)') 'failure artifact records concrete cleanup error code'
Assert ($entryText -match 'session_closed=') 'successful session result reports closure'
Assert ($entryText -match "'SESSION_CLEANUP_FAILED'") 'cleanup failure blocks next batch item'
Assert ($entryText -match '\[ValidateRange\(10000,60000\)\]\[int\]\$SearchReadyTimeoutMilliseconds\s*=\s*15000') 'slow network readiness timeout defaults above ten seconds'
Assert ($entryText -match '\$null\s+-ne\s+\$inputObject\.search_ready_timeout_milliseconds') 'stdin zero timeout reaches explicit range validation'
Assert ($entryText -match "catch\s*\{\s*throw 'INVALID_SEARCH_READY_TIMEOUT'\s*\}") 'stdin timeout conversion errors use stable code'
Assert ($entryText -match '\$stdinSearchReadyTimeout\s+-lt\s+10000') 'stdin timeout validates before assigning validated parameter'
Assert ($entryText -match 'Test-SearchResultReady\s+\$candidateText\s+\$query') 'search readiness uses copied result signal'
Assert (
    ([regex]::Matches($productionText,'& \$send @\(''CTRL'',''TAB''\) \$pluginGuard')).Count -eq 0
) 'formal search and collect do not use the unsupported native article switch chord'
$initialReadyIndex = $productionText.IndexOf(
    'if ($readySamples -lt 2)', [StringComparison]::Ordinal)
$listJudgeIndex = $productionText.IndexOf(
    "`$stage = 'list_judge'", [StringComparison]::Ordinal)
Assert (
    $initialReadyIndex -ge 0 -and $listJudgeIndex -gt $initialReadyIndex
) 'initial stable result text flows directly to the list judge'
Assert (
    $productionText.IndexOf('$initialViewport = & $newPluginViewport', [StringComparison]::Ordinal) -gt
        $initialReadyIndex -and
    $productionText.IndexOf('Get-WeixinUiaResultDescriptors', [StringComparison]::Ordinal) -gt
        $initialReadyIndex
) 'trusted viewport and UIA detail candidates are built after the stable result list is ready'
Assert (
    $productionText -notmatch 'Get-WeixinArticleTab|Find-DarkThemeCategoryTabBands|SetCursorPos\(\$article|articleX|articleY'
) 'removed article switching has no pixel UIA or fixed-coordinate category fallback'
Assert (
    $productionText -notmatch 'Find-DarkThemeCardBands|x_ratio|y_ratio|LocatorPath' -and
    $entryText -match 'Select-WeixinUiaResultTargets' -and
    $entryText -match 'TryGetClickablePoint'
) 'formal detail collection uses only UIA clickable points for element targeting'
Assert (
    $entryText -match 'SetThreadDpiAwarenessContext' -and
    $entryText -match 'AreDpiAwarenessContextsEqual' -and
    $entryText -match "(?s)ConvertTo-WeixinPhysicalClickPoint.*?'PerMonitorV2'"
) 'UIA physical clicks require a verified Per-Monitor V2 thread context'
Assert ($entryText -match '\$stage\s*=\s*''input_verify''') 'input focus failures use explicit input_verify stage'
Assert ($entryText -match 'Invoke-VerifiedWeixinUaSearchSubmission') 'entry point verifies UIA value-pattern input readback before submission'
$focusedInputIndex = $entryText.IndexOf(
    'Invoke-VerifiedWeixinUaSearchSubmission',
    $productionStart,
    [StringComparison]::Ordinal)
$freshOpenIndex = $entryText.LastIndexOf(
    'Invoke-LimitedTrustedOpen $openSouyisou $verifySouyisou 1',
    $focusedInputIndex,
    [StringComparison]::Ordinal)
$focusCriticalSection = $entryText.Substring(
    $freshOpenIndex, $focusedInputIndex - $freshOpenIndex)
Assert (
    $focusCriticalSection -notmatch 'CopyFromScreen|SetCursorPos|mouse_event' -and
    $focusCriticalSection -notmatch '&\s*\$activateWindow'
) 'single trusted open reaches paste/readback without screenshot mouse or activation'
Assert ($libText -notmatch '\$Refocus|for \(\$attempt = 1; \$attempt -le 2') 'post-paste mismatch never triggers Enter-based refocus'
Assert ($entryText -notmatch '(?m)\bquery=') 'logs and artifacts do not add plaintext query fields'
Assert ($entryText -match '\[switch\]\$VerifyInputOnly') 'explicit input-only probe switch exists'
$probeBranchStart = $entryText.IndexOf('if ($VerifyInputOnly)', [StringComparison]::Ordinal)
$probeCleanupIndex = $entryText.IndexOf('Complete-WeixinPluginSession', $probeBranchStart, [StringComparison]::Ordinal)
$probeClipboardRestoreIndex = $entryText.IndexOf('$clipboardCaptured = $false', $probeBranchStart, [StringComparison]::Ordinal)
$probeOutputIndex = $entryText.IndexOf('submitted=$false', $probeBranchStart, [StringComparison]::Ordinal)
$probeReadyIndex = $entryText.IndexOf("`$stage = 'search_wait'", $probeBranchStart, [StringComparison]::Ordinal)
Assert (
    $probeBranchStart -ge 0 -and $probeCleanupIndex -gt $probeBranchStart -and
    $probeClipboardRestoreIndex -gt $probeBranchStart -and
    $probeCleanupIndex -gt $probeClipboardRestoreIndex -and
    $probeOutputIndex -gt $probeCleanupIndex -and $probeReadyIndex -gt $probeOutputIndex
) 'input-only probe restores clipboard, closes plugin, then exits before ready processing'
Assert ($entryText -match "catch \{ throw 'CLIPBOARD_RESTORE_FAILED' \}") 'input-only clipboard restore fails loud'
Assert ($entryText -match 'input_verified=\$true; submitted=\$false') 'input-only success output is query-free and explicitly unsubmitted'
Assert (
    $entryText -match 'readback_matched=\[bool\]\$inputDiagnostics\.readback_matched' -and
    $entryText -notmatch 'locator_found|post_click_structure|final_structure'
) 'failure artifact retains only the keyboard readback diagnostic'
Assert ($entryText -match '\$readySamples\s+-lt\s+2') 'search readiness requires stable repeated evidence'
Assert (
    $entryText -notmatch "throw 'SEARCH_RESULTS_TIMEOUT'" -and
    $entryText -match "reason='list_text_unavailable'" -and
    $entryText -match '(?s)if \(\$readySamples -lt 2\).*?status=''inconclusive''.*?Complete-WeixinPluginSession'
) 'unavailable list text is a cleaned inconclusive query outcome'
Assert ($entryText -match '\$uiaEnumerationRecoveryAttempts\s*-lt\s*1') 'UIA enumeration recovery is limited to one retry'
Assert (
    $productionText -notmatch 'CARD_LOCATE_FAILED|explicitItems' -and
    $productionText -match 'New-WeixinUiaCandidateExhaustionRecord \$checked' -and
    $productionText -match '(?s)if \(-not \$newCount\) \{\s+\$records \+= New-WeixinUiaCandidateExhaustionRecord \$checked'
) 'production UIA exhaustion is a normal bounded enumeration outcome'
Assert ($entryText -match '\$clickForegroundRecoveryUsed') 'pre-click foreground recovery is limited per card'
Assert ($libText -match 'function Test-OrRestoreTrustedForeground') 'foreground recovery revalidates trusted hwnd'
Assert (
    ([regex]::Matches(
        $productionText,
        'Invoke-LimitedTrustedOpen \$openSouyisou \$verifySouyisou 1'
    )).Count -eq 1
) 'formal search and collect share exactly one trusted open call'
$sessionRegistrationIndex = $productionText.IndexOf(
    'New-WeixinWindowSession', [StringComparison]::Ordinal)
$singleOpenSection = $productionText.Substring(0, $sessionRegistrationIndex)
Assert (
    $sessionRegistrationIndex -gt 0 -and
    $singleOpenSection -notmatch 'Close-WeixinPluginSession|Test-FreshWeixinPluginIdentity|closedPluginHwnd'
) 'formal search registers the first trusted plugin without pre-close and reopen'
Assert (
    $entryText -match '\$workDeadline = if \(\$Command -in @\(''search'',''collect''\)\)' -and
    $entryText -match '\[DateTimeOffset\]::UtcNow\.AddMinutes\(9\)'
) 'formal search and collect reserve the final minute for cleanup'
Assert ($libText -match 'function Assert-WeixinWorkBudget') `
    'work deadline uses a testable stable budget guard'
$budgetNow = [DateTimeOffset]::Parse('2026-08-05T00:00:00Z')
Assert (
    (Assert-WeixinWorkBudget $budgetNow.AddMilliseconds(120000) 120000 { $budgetNow }) -eq 120000
) 'long call may start exactly when its timeout plus cleanup reserve remain'
$longBudgetRejected=$false
try {
    [void](Assert-WeixinWorkBudget `
        $budgetNow.AddMilliseconds(119999) 120000 { $budgetNow })
} catch { $longBudgetRejected=$_.Exception.Message -eq 'WECHAT_WORK_TIMEOUT' }
Assert ($longBudgetRejected) 'long call is rejected when it would consume the cleanup reserve'
Assert (
    ([regex]::Matches($entryText,'& \$assertWorkBudget 120000')).Count -eq 3 -and
    ([regex]::Matches($entryText,'& \$assertWorkBudget 90000')).Count -eq 1 -and
    ([regex]::Matches($entryText,'& \$assertWorkBudget 60000')).Count -ge 4 -and
    $entryText -match "'FOREGROUND_LOST','WECHAT_WORK_TIMEOUT'"
) 'blocking open judge and OCR calls reserve their timeout plus cleanup minute'
$assertionCount = ([regex]::Matches(
    (Get-Content -Raw -LiteralPath $PSCommandPath), '(?m)^Assert\s')).Count
Write-Output (@{ok=$true;tests=$assertionCount} | ConvertTo-Json -Compress)
