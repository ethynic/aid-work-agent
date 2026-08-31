# drivers/ps1/message-send.ps1 — wecom_message_send 驱动（写动作，单次单目标 1 条文本）
# 全链路（2026-08-31 真机实测结论）：
#   1) 搜索定位：Open-WeComSearchOverlay（点搜索框 → 清空上次查询残留 → 输入目标名 →
#      OCR 回读验证框内文本 → 等 SearchResultWindow2 → 轮询等渲染稳定，稳定帧 OCR）
#      → OCR 结果列表，按 name+section+subtitle 精确匹配（同名多项歧义 → TARGET_AMBIGUOUS；
#      subtitle 精确匹配落空时降级 name+section 再判一次）
#   2) PostMessage 点结果行进会话（overlay 自动关闭）
#   3) fail-closed 校验链：OCR 会话标题严格匹配目标（归一化后相等或为「名字+@微信/（…）」形式，
#      拒绝纯子串误判如「陆伟」落入「陆伟民」会话；不一致 → UI_CHANGED 中止）→
#      OCR 输入区无残留草稿（有残留 → UI_CHANGED 中止，防串消息）
#   4) 点输入框（比例坐标）→ 输入 text（中文自动 WM_CHAR）→ 发送前再校验标题
#   5) PostMessage Enter 发送 → 终态校验三选二：输入框清空 / 消息区末尾任一行含 text 前缀 /
#      会话列表任一行含目标名且任一行含 text 前缀；不过 → EXECUTION_UNKNOWN 绝不重试
# 关键步骤截图存 -ArtifactDir（step1..step4）；点击坐标 / OCR 原始输出 / 三选二各项判定
# 明细写 driver-log.txt（真机排障，格式同 add-customer.ps1）。
# 注意：打开外部联系人会话主窗口可能变宽（实测 1089→1449），任何坐标使用前重新取 rect。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$TargetName,
    [string]$Subtitle = '',
    [string]$Section = '',
    [Parameter(Mandatory)][string]$Text,
    [Parameter(Mandatory)][string]$ArtifactDir
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

# 诊断日志：点击目标坐标 / OCR 原始输出 / 终态三选二各项判定，写 artifact 目录 driver-log.txt（真机排障用）
$script:DriverLogPath = Join-Path $ArtifactDir 'driver-log.txt'
function Write-DriverLog([string]$Msg) {
    $line = '[' + ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')) + '] ' + $Msg
    Add-Content -Path $script:DriverLogPath -Value $line -Encoding UTF8
}

function Save-StepShot([int64]$Hwnd, [string]$Name) {
    # 关键步骤截图存档；返回 @{ path; left; top; w; h }（截图坐标系原点 = 窗口左上角）
    $path = Join-Path $ArtifactDir $Name
    $snap = Get-WeComWindowSnapshot -Hwnd $Hwnd -Path $path
    return @{ path = $path; left = [int]$snap[0]; top = [int]$snap[1]; w = [int]$snap[2]; h = [int]$snap[3] }
}

function Invoke-SendChatOcr([string]$ImagePath, [string]$Mode) {
    # _common 的 OCR 入口 + 原始输出落 driver-log（真机排障）
    $parsed = Invoke-WeComChatOcr -ImagePath $ImagePath -Mode $Mode
    Write-DriverLog ('OCR[' + $Mode + '] ' + ($parsed | ConvertTo-Json -Compress -Depth 10))
    return $parsed
}

function ConvertTo-WeComNormalized([string]$s) {
    # 归一化（与 chat_ocr.py normalize_text 同规则，终态前缀比对两侧必须一致）：
    # 去全部空白（OCR 可能插入/丢失空格）、全角 ASCII（U+FF01–FF5E，含：，！？；（）
    # 字母数字）转半角、常见全角标点（。、～）转半角、小写折叠
    $sb = New-Object System.Text.StringBuilder
    foreach ($ch in $s.ToCharArray()) {
        $o = [int][char]$ch
        if ([char]::IsWhiteSpace($ch)) { continue }
        if ($o -ge 0xFF01 -and $o -le 0xFF5E) { [void]$sb.Append([char]($o - 0xFEE0)); continue }
        if ($ch -eq '。') { [void]$sb.Append('.'); continue }
        if ($ch -eq '、') { [void]$sb.Append(','); continue }
        if ($ch -eq '～') { [void]$sb.Append('~'); continue }
        [void]$sb.Append($ch)
    }
    return $sb.ToString().ToLower()
}

function Get-WeComChatTitle([int64]$MainHwnd) {
    # 标题校验为瞬时检查（非关键步骤存档），截图仍走 TEMP；OCR 结果落 driver-log
    $shot = Join-Path $env:TEMP 'wecom-driver-send-title.png'
    Get-WeComWindowSnapshot -Hwnd $MainHwnd -Path $shot | Out-Null
    $ocr = Invoke-SendChatOcr -ImagePath $shot -Mode 'title'
    return [string]$ocr.title
}

function Assert-WeComChatTitle([int64]$MainHwnd, [string]$Expected) {
    $title = Get-WeComChatTitle $MainHwnd
    # 严格匹配（防串消息）：归一化后相等，或以「名字 + 后缀分隔符」形式开头
    # （外部联系人「陆伟 @微信」、群「产品讨论群（13）」）。
    # 纯子串匹配会把「陆伟」误放进「陆伟民」的会话——写动作不接受这种误判。
    $t = ConvertTo-WeComNormalized $title
    $e = ConvertTo-WeComNormalized $Expected
    $ok = ($t -eq $e) -or $t.StartsWith($e + '@') -or $t.StartsWith($e + '（') -or $t.StartsWith($e + '(')
    if (-not $ok) {
        Throw-DriverError 'UI_CHANGED' ("会话标题「" + $title + "」与目标「" + $Expected + "」不一致，已中止（防串消息）")
    }
    return $title
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.MessageSend' -Body {
    $shots = New-Object System.Collections.ArrayList
    # 1) 搜索定位
    Write-DriverLog ('step1 搜索定位 target=' + $TargetName + ' subtitle=' + $Subtitle + ' section=' + $Section)
    $s = Open-WeComSearchOverlay -Query $TargetName
    $mainHwnd = [int64]$s.MainHwnd
    $overlayHwnd = [int64]$s.OverlayHwnd
    try {
        # 稳定帧截图从 TEMP 拷入 artifact；OCR 复用 Open-WeComSearchOverlay 稳定帧结果（不再二次截图/OCR）
        $s1path = Join-Path $ArtifactDir 'step1-search.png'
        Copy-Item -LiteralPath ([string]$s.ShotPath) -Destination $s1path -Force
        [void]$shots.Add($s1path)
        $ocr = $s.Ocr
        Write-DriverLog ('OCR[search] ' + ($ocr | ConvertTo-Json -Compress -Depth 10))

        $sectionMap = @{ contact = '联系人'; group = '群聊' }
        $wantSection = ''
        if (-not [string]::IsNullOrEmpty($Section) -and $sectionMap.ContainsKey($Section)) { $wantSection = $sectionMap[$Section] }
        $all = @($ocr.items)
        # 精确匹配：name + section + subtitle；落空则降级 name + section（subtitle 可能随账号状态变化）
        $cands = @($all | Where-Object {
            $_.name -eq $TargetName -and
            ([string]::IsNullOrEmpty($wantSection) -or $_.section -eq $wantSection) -and
            ([string]::IsNullOrEmpty($Subtitle) -or $_.subtitle -eq $Subtitle)
        })
        if ($cands.Count -eq 0 -and -not [string]::IsNullOrEmpty($Subtitle)) {
            $cands = @($all | Where-Object {
                $_.name -eq $TargetName -and
                ([string]::IsNullOrEmpty($wantSection) -or $_.section -eq $wantSection)
            })
        }
        if ($cands.Count -eq 0) {
            Throw-DriverError 'TARGET_NOT_FOUND' ("搜索结果中未找到与「" + $TargetName + "」匹配的条目（section=" + $Section + "）")
        }
        if ($cands.Count -gt 1) {
            Throw-DriverError 'TARGET_AMBIGUOUS' ("搜索结果中「" + $TargetName + "」有 " + $cands.Count + " 个同名匹配，无法唯一确定，已拒绝发送（请改用更精确的目标）")
        }
        $item = $cands[0]

        # 2) 点结果行进会话（点击后 overlay 自动关闭）
        $ovl = Get-WeComWindowInfo ([IntPtr]$overlayHwnd)
        $clickX = [int]($ovl.X + [int]$item.x)
        $clickY = [int]($ovl.Y + [int]$item.y)
        Write-DriverLog ('step2 click 搜索结果行 screen=(' + $clickX + ',' + $clickY + ')（图像坐标=(' + $item.x + ',' + $item.y + ') + 截图原点=(' + $ovl.X + ',' + $ovl.Y + ')）overlayHwnd=' + $overlayHwnd)
        [void](Send-WeComClick -Hwnd $overlayHwnd -ScreenX $clickX -ScreenY $clickY)
    } finally {
        # 恢复原状：无论匹配/截图/OCR 成败都尽力关闭搜索 overlay（成功点击后多半已自动关闭，空操作）
        Close-WeComSearchOverlay -OverlayHwnd $overlayHwnd -MainHwnd $mainHwnd
    }
    Start-Sleep -Milliseconds 1500

    # 3) fail-closed 校验链（打开外部联系人会话主窗口可能变宽：重新解析主窗口）
    $mainHwnd = Resolve-WeComMainWindow
    $title = Assert-WeComChatTitle $mainHwnd $TargetName
    Write-DriverLog ('step3 会话标题校验通过 title=' + $title)

    $main = Get-WeComWindowInfo ([IntPtr]$mainHwnd)
    $s2 = Save-StepShot $mainHwnd 'step2-opened.png'
    [void]$shots.Add([string]$s2.path)
    $inputOcr = Invoke-SendChatOcr ([string]$s2.path) 'input'
    if ($inputOcr.input_empty -ne $true) {
        Throw-DriverError 'UI_CHANGED' '输入框存在残留草稿，为避免串消息已中止；请人工清空后重试'
    }

    # 4) 点输入框 → 输入 text（rect 已重新取）→ 截图存档 → 发送前再校验标题
    $inputX = [int]($main.X + $main.W * $script:ChatInputRx)
    $inputY = [int]($main.Y + $main.H * $script:ChatInputRy)
    Write-DriverLog ('step4 click 输入框 screen=(' + $inputX + ',' + $inputY + ') mainHwnd=' + $mainHwnd + ' rect=(' + $main.X + ',' + $main.Y + ',' + $main.W + 'x' + $main.H + ')')
    [void](Send-WeComClick -Hwnd $mainHwnd -ScreenX $inputX -ScreenY $inputY)
    Start-Sleep -Milliseconds 300
    Send-WeComText -Hwnd $mainHwnd -Text $Text
    Start-Sleep -Milliseconds 400
    $s3 = Save-StepShot $mainHwnd 'step3-typed.png'
    [void]$shots.Add([string]$s3.path)
    [void](Assert-WeComChatTitle $mainHwnd $TargetName)

    # 5) Enter 发送 → 终态校验三选二
    Write-DriverLog 'step5 PostMessage Enter 发送'
    Send-WeComEnter -Hwnd $mainHwnd
    Start-Sleep -Milliseconds 1200

    $mainHwnd = Resolve-WeComMainWindow
    $s4 = Save-StepShot $mainHwnd 'step4-after.png'
    [void]$shots.Add([string]$s4.path)
    $prefix = (ConvertTo-WeComNormalized $Text)
    if ($prefix.Length -gt 12) { $prefix = $prefix.Substring(0, 12) }

    $checks = 0
    # ① 输入框已清空
    $inputAfter = Invoke-SendChatOcr ([string]$s4.path) 'input'
    $inputEmptyHit = ($inputAfter.input_empty -eq $true)
    Write-DriverLog ('终态校验① 输入框清空：' + $(if ($inputEmptyHit) { 'PASS' } else { 'FAIL' }))
    if ($inputEmptyHit) { $checks++ }
    # ② 消息区末尾任一行含 text 前缀（消费 py 侧已归一化的 last_messages_norm 数组：
    # last_message 末尾行检出不可靠——真机实测检出「X·三8」时间戳/装饰行碎字，
    # 正确气泡在数组中间；归一化规则两侧一致，OCR 把全角：读成半角:也能命中）
    $bubble = Invoke-SendChatOcr ([string]$s4.path) 'bubble'
    $bubbleHit = $false
    foreach ($m in @($bubble.last_messages_norm)) {
        if (([string]$m).Contains($prefix)) { $bubbleHit = $true; break }
    }
    Write-DriverLog ('终态校验② 气泡含 text 前缀「' + $prefix + '」：' + $(if ($bubbleHit) { 'PASS' } else { 'FAIL' }))
    if ($bubbleHit) { $checks++ }
    # ③ 会话列表：任一行含目标名 且 任一行含 text 前缀（消费 py 侧已归一化的
    # texts_norm 平铺行；preview 区域已收窄到会话列表列 x∈[0.16w,0.38w]，排除导航栏）
    $preview = Invoke-SendChatOcr ([string]$s4.path) 'preview'
    $nameHit = $false
    $prefixHit = $false
    $normTarget = ConvertTo-WeComNormalized $TargetName
    foreach ($t in @($preview.texts_norm)) {
        $nt = [string]$t
        if ($nt.Contains($normTarget)) { $nameHit = $true }
        if ($nt.Contains($prefix)) { $prefixHit = $true }
    }
    $previewPass = ($nameHit -and $prefixHit)
    Write-DriverLog ('终态校验③ 会话列表 preview（含目标名=' + $nameHit + ' 含 text 前缀=' + $prefixHit + '）：' + $(if ($previewPass) { 'PASS' } else { 'FAIL' }))
    if ($previewPass) { $checks++ }

    Write-DriverLog ('终态校验三选二：通过 ' + $checks + '/3 项')
    if ($checks -lt 2) {
        Throw-DriverError 'EXECUTION_UNKNOWN' ("发送后校验失败（三选二仅过 " + $checks + " 项）：消息可能已发出，不会自动重试，请人工核对（详见 driver-log.txt）")
    }
    return @{ title = $title; screenshot_paths = $shots.ToArray() }
}
