# drivers/ps1/chat-search.ps1 — wecom_chat_search 驱动（M4：Jev 决策 + overlay 保持打开）
# 流程（2026-09-28 真机验证，experiments/probes/e1-e3 系列）：
#   1) 解析主窗口 → Focus-WeComSearchBox（attachstate Ctrl+F 主路径；降级 OCR+Jev 选 token
#      点击；再降级规则点占位符 token）；
#   2) 动态搜索框状态检测（裁切放大 OCR）→ 非 empty 清空（attachstate Ctrl+A + Delete），
#      复核仍非 empty → UI_CHANGED fail-closed；
#   3) 输入 query → OCR 回读验证（剥前导 Qq 图标误读 + 末尾光标伪影容忍；失败 UI_CHANGED）；
#   4) 等 SearchResultWindow2 可见 + 高度稳定（400ms 轮询，连续两次等高，≤10s）→ 稳定帧
#      chat_ocr search 模式 OCR 结果条目；
#   5) Jev 单次合并调用（best_result + is_ambiguous 两问）；降级 → 规则 best + 概率全 null；
#   6) DRIVER_JSON 输出 items/best_index/overlay/jev/timing_ms。
# **overlay 保持打开不清理**：坐标句柄（overlay rect + 条目相对坐标）要被后续 select 命令
#   消费；下一次 search 开头的残留清空会自动关掉它。search_successful 由 TS 层按
#   items.Count > 0 规则判定（不用 Jev）。target_ref 签发在 TS 侧完成。
# artifact：稳定帧截图 + driver-log.txt（OCR 原始 token、Jev state/answer 摘要、各阶段耗时；
# 绝不含 TYPESAFE_API_KEY——key 只在 Invoke-WeComJev 内经临时头文件瞬态使用）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$Query,
    [Parameter(Mandatory)][string]$ArtifactDir
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

# artifact 目录兜底创建（正式链路由 TS 侧 mkdir；直跑驱动时保证 driver-log 可写）
if (-not (Test-Path -LiteralPath $ArtifactDir)) { New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null }

# 诊断日志（同 message-send.ps1）：各阶段判定写 artifact 目录 driver-log.txt（真机排障用）
$script:DriverLogPath = Join-Path $ArtifactDir 'driver-log.txt'
function Write-DriverLog([string]$Msg) {
    $line = '[' + ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')) + '] ' + $Msg
    Add-Content -Path $script:DriverLogPath -Value $line -Encoding UTF8
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.ChatSearch' -Body {
    $swTotal = [System.Diagnostics.Stopwatch]::StartNew()
    $timing = @{ focus = 0; box_check = 0; typing = 0; overlay = 0; jev = 0; total = 0 }

    # 1) 主窗口 + 聚焦搜索框（主路径 attachstate Ctrl+F，降级 OCR+Jev/规则点击）
    $mainHwnd = Resolve-WeComMainWindow
    Write-DriverLog ('query=' + $Query + ' mainHwnd=' + $mainHwnd)
    $t0 = $swTotal.ElapsedMilliseconds
    $focusMethod = Focus-WeComSearchBox -MainHwnd $mainHwnd
    $timing.focus = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step1 focus method=' + $focusMethod + ' 耗时=' + $timing.focus + 'ms')

    # 2) 残留检测 + 清空（动态裁切 OCR 判态；has_content/unknown 都清，清不空 fail-closed）
    $t0 = $swTotal.ElapsedMilliseconds
    $sb = Get-WeComSearchBoxState -MainHwnd $mainHwnd -Tag 'pre'
    Write-DriverLog ('step2 box pre state=' + $sb.State + ' content=[' + $sb.Content + '] tokens: ' + $sb.Tokens)
    if ($sb.State -ne 'empty') {
        [void](Clear-WeComSearchBoxV2 -MainHwnd $mainHwnd)
        $sb2 = Get-WeComSearchBoxState -MainHwnd $mainHwnd -Tag 'cleared'
        Write-DriverLog ('step2 box cleared state=' + $sb2.State + ' content=[' + $sb2.Content + '] tokens: ' + $sb2.Tokens)
        if ($sb2.State -ne 'empty') {
            Throw-DriverError 'UI_CHANGED' ('搜索框残留清空后仍非空（state=' + $sb2.State + '，残留：' + $sb2.Content + '），为避免拼接旧查询词已中止')
        }
    }
    $timing.box_check = [int]($swTotal.ElapsedMilliseconds - $t0)

    # 3) 输入 query + OCR 回读验证（失败 UI_CHANGED；聚焦降级链是否成功也由此兜底）
    $t0 = $swTotal.ElapsedMilliseconds
    Send-WeComText -Hwnd $mainHwnd -Text $Query
    Start-Sleep -Milliseconds 400
    $sb3 = Get-WeComSearchBoxState -MainHwnd $mainHwnd -Tag 'typed'
    Write-DriverLog ('step3 typed content=[' + $sb3.Content + '] state=' + $sb3.State + ' tokens: ' + $sb3.Tokens)
    $normQuery = ($Query -replace '\s+', '')
    # 剥前导 [Qq]：OCR 常把框内图标+文本合并成「Q 陆伟」。剥前/剥后基底任一匹配即可——
    # 只比剥后值会误杀 q/Q 开头的 query 本体；光标伪影容忍：echo 仅比 query 多末尾 1 个
    # 字符且属于误识集合 l|I1i! 才放行（方向不可逆，真丢字符必须失败），两种基底都查
    $rawNorm = ($sb3.Content -replace '\s+', '')
    $echoNorm = $rawNorm -replace '^[Qq]', ''
    $echoOk = ($rawNorm -eq $normQuery) -or ($echoNorm -eq $normQuery) -or (
        $rawNorm.Length -eq $normQuery.Length + 1 -and
        $rawNorm.StartsWith($normQuery) -and
        'l|I1i!'.Contains($rawNorm.Substring($rawNorm.Length - 1))
    ) -or (
        $echoNorm.Length -eq $normQuery.Length + 1 -and
        $echoNorm.StartsWith($normQuery) -and
        'l|I1i!'.Contains($echoNorm.Substring($echoNorm.Length - 1))
    )
    if (-not $echoOk) {
        Throw-DriverError 'UI_CHANGED' ('搜索框回读文本与 query 不一致（框内「' + $sb3.Content + '」≠「' + $normQuery + '），聚焦/输入链路未生效，已中止')
    }
    $timing.typing = [int]($swTotal.ElapsedMilliseconds - $t0)

    # 4) 等 SearchResultWindow2 可见 + 高度稳定（overlay 关闭后窗口以 visible=False 残留，
    #    Wait-WeComWindow 已含可见性过滤），稳定帧 OCR；超时未稳定用最后一帧兜底
    $t0 = $swTotal.ElapsedMilliseconds
    $overlayHwnd = Wait-WeComWindow -ClassName 'SearchResultWindow2' -TimeoutMs 8000
    if ($overlayHwnd -eq 0) {
        Throw-DriverError 'UI_CHANGED' '未等到搜索结果面板（SearchResultWindow2）：搜索框聚焦/输入未生效或页面结构已变化'
    }
    $shotOverlay = Join-Path $env:TEMP 'wecom-driver-search-overlay.png'
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    $lastH = -1
    $ovInfo = Get-WeComWindowInfo ([IntPtr]$overlayHwnd)
    while ([DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 400
        $ovInfo = Get-WeComWindowInfo ([IntPtr]$overlayHwnd)
        Get-WeComWindowSnapshot -Hwnd $overlayHwnd -Path $shotOverlay | Out-Null
        if ($ovInfo.H -eq $lastH) { break }
        $lastH = $ovInfo.H
    }
    $ocr = Invoke-WeComChatOcr -ImagePath $shotOverlay -Mode 'search'
    # 稳定帧截图存档（select 消费坐标时的现场证据）
    Copy-Item -LiteralPath $shotOverlay -Destination (Join-Path $ArtifactDir 'overlay-stable.png') -Force
    $timing.overlay = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step4 overlay hwnd=' + $overlayHwnd + ' rect=(' + $ovInfo.X + ',' + $ovInfo.Y + ',' + $ovInfo.W + 'x' + $ovInfo.H + ') h_stable=' + $lastH + ' 耗时=' + $timing.overlay + 'ms')
    Write-DriverLog ('OCR[search] ' + ($ocr | ConvertTo-Json -Compress -Depth 10))

    # 5) 条目 + Jev 单次合并调用（best_result + is_ambiguous；state 措辞保持稳定，Jev
    #    置信度对措辞敏感）。空 items 不调 Jev（search_successful 由 TS 层规则判定）。
    $t0 = $swTotal.ElapsedMilliseconds
    $rawItems = @($ocr.items)
    $items = @($rawItems | ForEach-Object {
        @{ name = [string]$_.name; subtitle = [string]$_.subtitle; section = [string]$_.section
           x = [int]$_.x; y = [int]$_.y; probability = $null }
    })
    $jev = @{ used = $false; latency_ms = 0; reason = 'no_items' }
    $bestIndex = $null
    if ($items.Count -gt 0) {
        $rLines = @(); $critBest = @{}
        for ($i = 0; $i -lt $items.Count; $i++) {
            $it = $items[$i]
            $rLines += ('R{0}: name={1} subtitle={2} section={3} center=({4},{5})' -f $i, $it.name, $it.subtitle, $it.section, $it.x, $it.y)
            $critBest[('R' + $i)] = ('[' + $it.section + '] ' + $it.name + ' / ' + $it.subtitle)
        }
        $stateText = '企业微信搜索结果面板 OCR 结果（面板内像素坐标）：' + "`n" + ($rLines -join "`n") + "`n" + ('搜索关键词：' + $Query) + "`n" + '任务：选出与搜索关键词最匹配、最适合进入会话的一个候选。'
        $r = Invoke-WeComJev -StateText $stateText -Questions @{
            best_result  = @{ type = 'choice'; instructions = '选出与搜索关键词最匹配、最适合进入会话的一个候选。'; criteria = $critBest }
            is_ambiguous = @{ type = 'choice'; instructions = '候选之间是否存在无法消除的歧义？'; criteria = @{ yes = '存在歧义'; no = '可唯一确定' } }
        }
        $choiceOk = $false
        if ($r.used) {
            $choice = [string]$r.answers.best_result.choice
            if ($choice -match '^R(\d+)$') {
                $idx = [int]$Matches[1]
                if ($idx -ge 0 -and $idx -lt $items.Count) {
                    $choiceOk = $true
                    $bestIndex = $idx
                    # 概率分布回填到条目（缺键/非数值保持 null；[double] 强转走 invariant）
                    $probs = $r.answers.best_result.probabilities
                    for ($i = 0; $i -lt $items.Count; $i++) {
                        $p = $null
                        if ($null -ne $probs) { $p = [string]$probs.('R' + $i) }
                        if ($p -match '^-?\d+(\.\d+)?$') { $items[$i].probability = [double]$p }
                    }
                    $jev = @{ used = $true; latency_ms = [int]$r.latency_ms
                              is_ambiguous = ([string]$r.answers.is_ambiguous.choice -eq 'yes')
                              ambiguous_confidence = [double]$r.answers.is_ambiguous.confidence
                              best_confidence = [double]$r.answers.best_result.confidence }
                }
            }
            if (-not $choiceOk) {
                # Jev 返回了不可用 choice：按降级处理（规则 best + 概率全 null）
                $jev = @{ used = $false; latency_ms = [int]$r.latency_ms; reason = 'invalid_choice' }
            }
        } else {
            $jev = @{ used = $false; latency_ms = [int]$r.latency_ms; reason = [string]$r.reason }
        }
        if (-not $jev.used) {
            # 规则 best：name 归一化精确 == query 的第一条，否则 0
            $bestIndex = 0
            $normQ = ConvertTo-WeComNormalized $Query
            for ($i = 0; $i -lt $items.Count; $i++) {
                if ((ConvertTo-WeComNormalized $items[$i].name) -eq $normQ) { $bestIndex = $i; break }
            }
        }
        Write-DriverLog ('step5 jev used=' + $jev.used + ' latency=' + $jev.latency_ms + 'ms reason=' + [string]$jev.reason + ' best=R' + $bestIndex + ' best_confidence=' + [string]$jev.best_confidence + ' is_ambiguous=' + [string]$jev.is_ambiguous + ' ambiguous_confidence=' + [string]$jev.ambiguous_confidence)
        Write-DriverLog ('step5 jev state: ' + ($stateText -replace "`r?`n", ' / '))
    } else {
        Write-DriverLog 'step5 jev 跳过（无结果条目）'
    }
    $timing.jev = [int]($swTotal.ElapsedMilliseconds - $t0)
    $timing.total = [int]$swTotal.ElapsedMilliseconds
    Write-DriverLog ('done items=' + $items.Count + ' best_index=' + [string]$bestIndex + ' timing=' + ($timing | ConvertTo-Json -Compress))

    # 6) 返回（overlay 保持打开：坐标句柄供 select 消费）
    return @{
        query = $Query
        items = $items
        best_index = $bestIndex
        overlay = @{ x = [int]$ovInfo.X; y = [int]$ovInfo.Y; w = [int]$ovInfo.W; h = [int]$ovInfo.H }
        jev = $jev
        timing_ms = $timing
    }
}
