# drivers/ps1/add-customer.ps1 — wecom_add_customer 驱动（写动作，单次单号码）
# M12 新流程（2026-09-29 真机逐步验证重标定；取代旧「通讯录→子窗口⊕添加」路线——
# 客户端更新后内容子窗口 WXworkWindow-企业微信-<页名> 已不存在、④添加按钮
# PostMessage/mouse_event 均无效，旧路线整体废弃）：
#   1) 解析主窗口 → attachstate Ctrl+F 聚焦搜索框（Send-WeComAttachChordKey，全后台）
#   2) 残留检测 + 清空（Clear-WeComSearchBoxV2 = Ctrl+A+Delete；Get-WeComSearchBoxState
#      复核，清不空 fail-closed）——清空副作用会关掉上次 search 留下的 overlay
#   3) Send-WeComText 输入手机号（纯数字走 WM_KEYDOWN）→ 搜索框带 token 回读验证
#      （contains 手机号即过：天然容忍前导 Q 图标伪读/末尾光标伪影/overlay「网络查找」
#      行混入带内；截断丢字不含完整手机号必失败）；失败清空补输 1 次，仍失败 → UI_CHANGED
#   4) 等 SearchResultWindow2 overlay → 高度稳定轮询（连续两次等高；手机号形态实测高 148）→
#      boxes OCR 找「网络查找」行（实测单 token 形态「网络查找手机W/邮箱：13671705876」
#      @y37-48，W=「号」误读——按 contains '网络查找' 匹配，同视觉行拼起来校验含手机号，
#      防点错行）→ PostMessage 点该行中心（显式投 overlay hwnd，同 M2 结果行点击模式；
#      点击前关遗留同类弹窗——残留旧号码结果行会被当新结果误发，见 Close-StaleDialogGuard）
#   5) 等 SearchExternalsWnd 弹窗（400x292，类名与 M1 相同）→ OCR 校验已自动填入手机号
#      （实测标题「网络查找手机/邮箱：<号>」+ 输入框数字两处可验）；未自动填（客户端行为
#      变化）→ 回退：点输入框（M1 标定 0.5w/0.257h）+ 清空 + Send-WeComText 手输 + 回读
#   6) PostMessage Enter 触发检索 → 轮询截图 OCR（add_customer_result.py result 模式，
#      新弹窗与 M1 同布局，结果行「<微信名>+添加」可解析）等结果行；结果区持续为空时
#      每 2s 重发一次 Enter（只读检索，重复安全），最多重发 5 次仍无结果 →
#      CUSTOMER_NOT_FOUND，不重试
#   7) PostMessage 点击结果行「添加」（InputReasonWnd 弹窗有数秒延迟，等 15s；
#      点击前同样关遗留 InputReasonWnd）
#   8) OCR 定位「发送」按钮 → PostMessage 点击 → 等 InputReasonWnd 关闭
#   9) 终态校验：SearchExternalsWnd 结果行按钮变「已发送申请」（实测发送后 InputReasonWnd
#      ≤1s 关闭、结果行 ≤3s 才刷新；轮询每 1s 上限 10s），仍非 sent → EXECUTION_UNKNOWN
#      （绝不重试）
# 步骤 5-9 弹窗段与 M1（2026-08-29/08-30 真机标定）完全一致，原样保留。
# 关键步骤截图存 -ArtifactDir（step1..step6）；点击坐标与 OCR 原始输出写 driver-log.txt
# （真机排障）。失败/异常路径 finally best-effort 恢复原状：关两个弹窗 + 清空搜索框
# 手机号残留（Ctrl+F 聚焦 + Ctrl+A+Delete，同时关掉可能残留的 overlay）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$Phone,
    [Parameter(Mandatory)][string]$ArtifactDir
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

# artifact 目录兜底创建（正式链路由 TS 侧 mkdir；直跑驱动时保证 driver-log 可写）
if (-not (Test-Path -LiteralPath $ArtifactDir)) { New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null }

# 诊断日志：点击目标坐标 / OCR 原始输出，写 artifact 目录 driver-log.txt（真机排障用）
$script:DriverLogPath = Join-Path $ArtifactDir 'driver-log.txt'
function Write-DriverLog([string]$Msg) {
    $line = '[' + ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')) + '] ' + $Msg
    Add-Content -Path $script:DriverLogPath -Value $line -Encoding UTF8
}

# SearchExternalsWnd 弹窗比例坐标（M1 真机标定 400x292，2026-09-29 复核仍有效；
# 旧路线的 NavContactsPx/Py、ChildAddRx/Ry 与通讯录导航整段已随 M12 废弃删除）
$script:DlgInputRx = 0.5000       # 弹窗输入框中心：宽 50%
$script:DlgInputRy = 0.2570       #                 高 25.7%
$script:DlgCloseRx = 0.9075       # 弹窗右上角 × ：宽 90.8%
$script:DlgCloseRy = 0.1027       #               高 10.3%

function Save-StepShot([int64]$Hwnd, [string]$Name) {
    # 关键步骤截图存档；返回 @{ path; left; top; w; h }（截图坐标系原点 = 窗口左上角）
    $path = Join-Path $ArtifactDir $Name
    $snap = Get-WeComWindowSnapshot -Hwnd $Hwnd -Path $path
    return @{ path = $path; left = [int]$snap[0]; top = [int]$snap[1]; w = [int]$snap[2]; h = [int]$snap[3] }
}

function Invoke-AddCustomerOcr([string]$ImagePath, [string]$Mode) {
    # 仓库 venv python 跑 RapidOCR（drivers/py/add_customer_result.py：result/reason 模式）；
    # drivers/ps1 上四级为仓库根。经 System.Diagnostics.Process 直启（同 Invoke-WeComChatOcr，
    # 规避 MCP stdio 白名单环境下 PS 5.1 管道原生命令的 CantActivateDocumentInPipeline）
    $repoRoot = (Resolve-Path (Join-Path $script:DriverPs1Dir '..\..\..\..')).Path
    $pythonExe = Join-Path $repoRoot 'venv\Scripts\python.exe'
    $ocrScript = Join-Path $script:DriverPs1Dir '..\py\add_customer_result.py'
    if (-not (Test-Path $ocrScript)) { Throw-DriverError 'INTERNAL_ERROR' ('未找到 OCR 脚本：' + $ocrScript) }
    if (-not (Test-Path $pythonExe)) { Throw-DriverError 'CONFIG_MISSING' ('未找到仓库 venv python（RapidOCR 所在解释器）：' + $pythonExe) }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $pythonExe
    $psi.Arguments = ('"' + $ocrScript + '" "' + $ImagePath + '" ' + $Mode)
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $out = $proc.StandardOutput.ReadToEnd()
    $errText = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    if ($proc.ExitCode -ne 0) {
        Throw-DriverError 'INTERNAL_ERROR' ('OCR 脚本执行失败（exit=' + $proc.ExitCode + '；stderr: ' + ($errText.Substring(0, [Math]::Min(300, $errText.Length))) + '）')
    }
    $jsonLine = @($out | Where-Object { $_ -match '^ADDCUSTOMER_JSON:' }) | Select-Object -Last 1
    if (-not $jsonLine) { Throw-DriverError 'INTERNAL_ERROR' 'OCR 脚本未输出 ADDCUSTOMER_JSON' }
    $parsed = ($jsonLine -replace '^ADDCUSTOMER_JSON:\s*', '') | ConvertFrom-Json
    Write-DriverLog ('OCR[' + $Mode + '] ' + ($jsonLine -replace '^ADDCUSTOMER_JSON:\s*', ''))
    if ($parsed.error) {
        if ($parsed.error -eq 'OCR_UNAVAILABLE') { Throw-DriverError 'CONFIG_MISSING' ([string]$parsed.message) }
        Throw-DriverError 'INTERNAL_ERROR' ('OCR 失败：' + [string]$parsed.message)
    }
    return $parsed
}

function Close-SearchDialogBestEffort([int64]$DlgHwnd) {
    # best-effort 关闭「网络查找手机/邮箱」弹窗（× 对 PostMessage 点击有效，真机实测）；失败不影响结果
    try {
        if ([WeComWin32]::IsWindow([IntPtr]$DlgHwnd)) {
            $info = Get-WeComWindowInfo ([IntPtr]$DlgHwnd)
            $closeX = [int]($info.X + $info.W * $script:DlgCloseRx)
            $closeY = [int]($info.Y + $info.H * $script:DlgCloseRy)
            Write-DriverLog ('关闭检索弹窗 × screen=(' + $closeX + ',' + $closeY + ') dlgHwnd=' + $DlgHwnd)
            [void](Send-WeComClick -Hwnd $DlgHwnd -ScreenX $closeX -ScreenY $closeY)
        }
    } catch {}
}

function Close-ReasonDialogBestEffort([int64]$Hwnd) {
    # best-effort 关闭「发送添加邀请」弹窗（失败路径清理：邀请未发出时 WM_CLOSE 即取消该对话框）
    try {
        if ([WeComWin32]::IsWindow([IntPtr]$Hwnd)) {
            [void][WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) # WM_CLOSE
        }
    } catch {}
}

function Close-StaleDialogGuard([string]$ClassName) {
    # 陈旧弹窗防御（误发零容忍）：上一轮驱动被超时 kill（finally 未执行）或人工操作可能
    # 遗留同类弹窗——SearchExternalsWnd 结果区会残留旧号码的「添加」行、InputReasonWnd
    # 残留旧号码的「发送」窗；结果行/reason OCR 均无法把弹窗内容绑定到本次手机号
    # （结果行只显示微信名+按钮），Wait-WeComWindow 也不区分新开与遗留，残留窗会被
    # 当成新弹窗继续操作 → 可能向旧号码发邀请。因此在「点击开新弹窗」之前先关闭已
    # 存在的同类可见弹窗并等其不可见（≤2s），保证之后等到的弹窗必为本次点击新开
    # （结果区为空，恢复 M1 标定前提）。正常路径无残留时为空操作；关不掉 → UI_CHANGED
    # fail-closed（绝不带着旧弹窗往下走）。
    $stale = Wait-WeComWindow -ClassName $ClassName -TimeoutMs 10
    if ($stale -eq 0) { return }
    Write-DriverLog ('检测到遗留 ' + $ClassName + ' 弹窗（hwnd=' + $stale + '），先关闭再开新弹窗（防残留内容误发）')
    if ($ClassName -eq 'InputReasonWnd') { Close-ReasonDialogBestEffort $stale } else { Close-SearchDialogBestEffort $stale }
    $deadline = [DateTime]::UtcNow.AddSeconds(2)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (-not [WeComWin32]::IsWindowVisible([IntPtr]$stale)) { return }
        Start-Sleep -Milliseconds 200
    }
    Throw-DriverError 'UI_CHANGED' ('存在关不掉的遗留 ' + $ClassName + ' 弹窗，为避免向旧号码误发已中止，请人工关闭后重试')
}

function Clear-SearchResidualBestEffort([int64]$MainHwnd) {
    # 收尾恢复原状（成功/失败路径均调用）：Ctrl+F 聚焦搜索框 → Ctrl+A+Delete 清空手机号
    # 残留（不给下次搜索留旧词、手机号不留屏）；清空副作用同时关掉可能残留的搜索 overlay。
    # best-effort：失败仅记日志，不影响结果语义。
    try {
        if ($MainHwnd -ne 0 -and [WeComWin32]::IsWindow([IntPtr]$MainHwnd)) {
            [void](Send-WeComAttachChordKey -Hwnd $MainHwnd -Vk 0x46)
            Start-Sleep -Milliseconds 150
            [void](Clear-WeComSearchBoxV2 -MainHwnd $MainHwnd)
            $sb = Get-WeComSearchBoxState -MainHwnd $MainHwnd -Tag 'cleanup'
            Write-DriverLog ('收尾清空搜索框 state=' + $sb.State + ' content=[' + $sb.Content + ']')
        }
    } catch {
        # best-effort 契约：catch 内日志自身失败（如磁盘满 Add-Content 抛错）也不得外抛——
        # 否则会顶掉 try 内原始业务错误码（WECOMDRIVE|code 被 finally 异常覆盖成 INTERNAL_ERROR）
        try { Write-DriverLog ('收尾清空搜索框失败（best-effort 忽略）：' + ([string]$_.Exception.Message)) } catch {}
    }
}

function Get-BoxesText([object]$Boxes) {
    # boxes OCR 原始 token 摘要（driver-log 排障用）
    return (@($Boxes | ForEach-Object { [string]$_.text + '@(' + [int]$_.x0 + ',' + [int]$_.y0 + ')' }) -join ' | ')
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.AddCustomer' -Body {
    $shots = New-Object System.Collections.ArrayList
    # 句柄跟踪：失败路径 finally 恢复原状（关弹窗 + 清搜索框残留）
    $mainHwnd = [int64]0
    $dlgHwnd = [int64]0
    $reasonHwnd = [int64]0
    try {
    # 1) 解析主窗口 + attachstate Ctrl+F 聚焦搜索框（attach 失败 fail-closed：不聚焦就
    #    输入会把手机号打进当前会话输入框，绝不带着不确定焦点往下走）
    $mainHwnd = Resolve-WeComMainWindow
    $main = Get-WeComWindowInfo ([IntPtr]$mainHwnd)
    Write-DriverLog ('step1 mainHwnd=' + $mainHwnd + ' rect=(' + $main.X + ',' + $main.Y + ',' + $main.W + 'x' + $main.H + ')')
    if (-not (Send-WeComAttachChordKey -Hwnd $mainHwnd -Vk 0x46)) {
        Throw-DriverError 'UI_CHANGED' 'attachstate Ctrl+F 聚焦搜索框失败（AttachThreadInput 失败），为避免输入落错位置已中止'
    }
    Start-Sleep -Milliseconds 150

    # 2) 残留检测 + 清空（同 chat-search：has_content/unknown 都清；复核仍非 empty → UI_CHANGED）
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

    # 3) 输入手机号 + 搜索框带 token 回读验证：剥空白后 contains 手机号即过——前导 Q 图标
    #    伪读（「Q13671705876」）、末尾光标伪影、overlay「网络查找」行混入带内都天然容忍；
    #    截断/丢字（如末位丢失）不含完整手机号必失败。失败 → 清空补输 1 次 → 仍失败 UI_CHANGED
    $typedOk = $false
    $echo = ''
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        Send-WeComText -Hwnd $mainHwnd -Text $Phone
        Start-Sleep -Milliseconds 400
        $sbT = Get-WeComSearchBoxState -MainHwnd $mainHwnd -Tag ('typed' + $attempt)
        $echo = ($sbT.Content -replace '\s+', '')
        Write-DriverLog ('step3 typed#' + $attempt + ' state=' + $sbT.State + ' content=[' + $sbT.Content + '] tokens: ' + $sbT.Tokens)
        if ($echo.Contains($Phone)) { $typedOk = $true; break }
        if ($attempt -lt 2) {
            # 补输前重新聚焦 + 清空（焦点可能被 overlay 抢走；清空同时关掉已弹 overlay）
            [void](Send-WeComAttachChordKey -Hwnd $mainHwnd -Vk 0x46)
            Start-Sleep -Milliseconds 150
            [void](Clear-WeComSearchBoxV2 -MainHwnd $mainHwnd)
        }
    }
    if (-not $typedOk) {
        Throw-DriverError 'UI_CHANGED' ('搜索框回读文本不含完整手机号（补输 1 次后仍不符：框内「' + $echo + '」），聚焦/输入链路未生效，已中止')
    }
    $s1 = Save-StepShot $mainHwnd 'step1-typed.png'
    [void]$shots.Add([string]$s1.path)

    # 4) 等 SearchResultWindow2 overlay → 高度稳定轮询 → boxes OCR 找「网络查找」行
    #    （overlay 关闭后窗口以 visible=False 残留，Wait-WeComWindow 已含可见性过滤）
    $overlayHwnd = Wait-WeComWindow -ClassName 'SearchResultWindow2' -TimeoutMs 8000
    if ($overlayHwnd -eq 0) {
        Throw-DriverError 'UI_CHANGED' '未等到搜索结果面板（SearchResultWindow2）：搜索框输入未生效或页面结构已变化'
    }
    # 高度稳定轮询（每 400ms 截图，连续两次等高即稳定，≤10s；手机号搜索形态实测高 148：
    # 单「网络查找」行 + 底部按钮行）；稳定帧直接写 artifact
    $shotOverlay = Join-Path $ArtifactDir 'step2-overlay.png'
    $lastH = -1
    $ovInfo = Get-WeComWindowInfo ([IntPtr]$overlayHwnd)
    $stableDeadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $stableDeadline) {
        Start-Sleep -Milliseconds 400
        $ovInfo = Get-WeComWindowInfo ([IntPtr]$overlayHwnd)
        [void](Get-WeComWindowSnapshot -Hwnd $overlayHwnd -Path $shotOverlay)
        if ($ovInfo.H -eq $lastH) { break }
        $lastH = $ovInfo.H
    }
    [void]$shots.Add($shotOverlay)
    Write-DriverLog ('step4 overlay hwnd=' + $overlayHwnd + ' rect=(' + $ovInfo.X + ',' + $ovInfo.Y + ',' + $ovInfo.W + 'x' + $ovInfo.H + ') h_stable=' + $lastH)

    # boxes OCR 找「网络查找」行（contains '网络查找'，容忍「号→W」等误读）+ 同视觉行拼接
    # 校验含手机号（防点错行/陈旧面板）；首帧不中再取一帧重试一次
    $netRow = $null
    $netLine = $null
    for ($tryIdx = 1; $tryIdx -le 2 -and $null -eq $netRow; $tryIdx++) {
        if ($tryIdx -gt 1) {
            Start-Sleep -Milliseconds 400
            [void](Get-WeComWindowSnapshot -Hwnd $overlayHwnd -Path $shotOverlay)
        }
        $ovOcr = Invoke-WeComChatOcr -ImagePath $shotOverlay -Mode 'boxes'
        Write-DriverLog ('step4 OCR[boxes] ' + (Get-BoxesText $ovOcr.boxes))
        $label = @($ovOcr.boxes | Where-Object { (([string]$_.text) -replace '\s+', '').Contains('网络查找') }) | Select-Object -First 1
        if ($null -eq $label) { continue }
        # 同视觉行（y 中心差 ≤ 行高）全部 token 拼接校验手机号；OCR 可能拆成
        # 「网络查找手机W/邮箱：」+「13671705876」两个 token，单 token 校验会误杀
        $rowMid = ([double]$label.y0 + [double]$label.y1) / 2
        $rowH = [Math]::Max([double]$label.y1 - [double]$label.y0, 10)
        $lineTokens = @($ovOcr.boxes | Where-Object {
            $cy = ([double]$_.y0 + [double]$_.y1) / 2
            [Math]::Abs($cy - $rowMid) -le $rowH
        })
        $lineText = (($lineTokens | ForEach-Object { [string]$_.text }) -join '') -replace '\s+', ''
        if ($lineText.Contains($Phone)) {
            $netRow = $label
            $netLine = $lineTokens
        } else {
            Write-DriverLog ('step4 网络查找行不含本机手机号（行文本「' + $lineText + '」），面板可能是陈旧内容')
        }
    }
    if ($null -eq $netRow) {
        Throw-DriverError 'UI_CHANGED' ('搜索面板未找到含本机手机号的「网络查找」行（overlay 高=' + $ovInfo.H + '），客户端行为可能已变化，详见 driver-log.txt')
    }
    # 陈旧弹窗防御：清掉可能遗留的检索弹窗（上轮被 kill / 人工遗留时，残留旧号码结果行
    # 会被 Wait-WeComWindow 当成新弹窗、结果轮询读到旧「添加」行误发，见函数注释）
    Close-StaleDialogGuard 'SearchExternalsWnd'

    # 点该行中心（行 token 并集包围盒中心 = 图像坐标 + overlay 实时 rect → 屏幕坐标；
    # 显式投递 overlay 顶层 hwnd，不用 WindowFromPoint 路由——同 M2 结果行点击模式，
    # Chromium 嵌入窗口 Z 序可能盖住 overlay 吞点击）
    $rowX0 = [double]::MaxValue; $rowY0 = [double]::MaxValue; $rowX1 = [double]::MinValue; $rowY1 = [double]::MinValue
    foreach ($tk in $netLine) {
        if ([double]$tk.x0 -lt $rowX0) { $rowX0 = [double]$tk.x0 }
        if ([double]$tk.y0 -lt $rowY0) { $rowY0 = [double]$tk.y0 }
        if ([double]$tk.x1 -gt $rowX1) { $rowX1 = [double]$tk.x1 }
        if ([double]$tk.y1 -gt $rowY1) { $rowY1 = [double]$tk.y1 }
    }
    $ovl = Get-WeComWindowInfo ([IntPtr]$overlayHwnd)
    $netClickX = [int]($ovl.X + ($rowX0 + $rowX1) / 2)
    $netClickY = [int]($ovl.Y + ($rowY0 + $rowY1) / 2)
    Write-DriverLog ('step4 click 网络查找行 screen=(' + $netClickX + ',' + $netClickY + ')（overlay rect=(' + $ovl.X + ',' + $ovl.Y + ') + 行bbox=(' + [int](($rowX0 + $rowX1) / 2) + ',' + [int](($rowY0 + $rowY1) / 2) + '）投递 overlay hwnd=' + $overlayHwnd)
    [void](Send-WeComClick -Hwnd $overlayHwnd -ScreenX $netClickX -ScreenY $netClickY)

    # 5) 等 SearchExternalsWnd 弹窗（类名与 M1 相同）→ OCR 校验已自动填入手机号
    #    （实测点击网络查找行后弹窗自动填号：标题「网络查找手机/邮箱：<号>」+ 输入框数字
    #    两处可验）；未自动填 → 回退手输（点输入框 M1 标定 0.5w/0.257h + 清空 + 输入 + 回读）
    $dlgHwnd = Wait-WeComWindow -ClassName 'SearchExternalsWnd' -TimeoutMs 10000
    if ($dlgHwnd -eq 0) {
        Throw-DriverError 'UI_CHANGED' '未等到「网络查找手机/邮箱」弹窗（SearchExternalsWnd）：网络查找行点击未生效或页面结构已变化'
    }
    Start-Sleep -Milliseconds 500
    $s2 = Save-StepShot $dlgHwnd 'step3-dialog.png'
    [void]$shots.Add([string]$s2.path)
    $dlgOcr = Invoke-WeComChatOcr -ImagePath ([string]$s2.path) -Mode 'boxes'
    Write-DriverLog ('step5 OCR[boxes] ' + (Get-BoxesText $dlgOcr.boxes))
    $filled = @($dlgOcr.boxes | Where-Object { (([string]$_.text) -replace '\s+', '').Contains($Phone) }).Count -gt 0
    Write-DriverLog ('step5 弹窗自动填号校验 filled=' + $filled)
    if (-not $filled) {
        # 回退：客户端行为变化时手动输入（先点输入框建立焦点 + 清空可能的部分内容，再输入回读）
        Write-DriverLog 'step5 弹窗未自动填号，回退手动输入'
        $dlg = Get-WeComWindowInfo ([IntPtr]$dlgHwnd)
        $inputX = [int]($dlg.X + $dlg.W * $script:DlgInputRx)
        $inputY = [int]($dlg.Y + $dlg.H * $script:DlgInputRy)
        Write-DriverLog ('step5 click 输入框 screen=(' + $inputX + ',' + $inputY + ') dlgHwnd=' + $dlgHwnd + ' rect=(' + $dlg.X + ',' + $dlg.Y + ',' + $dlg.W + 'x' + $dlg.H + ')')
        [void](Send-WeComClick -Hwnd $dlgHwnd -ScreenX $inputX -ScreenY $inputY)
        Start-Sleep -Milliseconds 300
        [void](Clear-WeComFocusedInput -Hwnd $dlgHwnd)
        Send-WeComText -Hwnd $dlgHwnd -Text $Phone
        Start-Sleep -Milliseconds 400
        $s2b = Save-StepShot $dlgHwnd 'step3-dialog-typed.png'
        [void]$shots.Add([string]$s2b.path)
        $dlgOcr2 = Invoke-WeComChatOcr -ImagePath ([string]$s2b.path) -Mode 'boxes'
        Write-DriverLog ('step5 回输后 OCR[boxes] ' + (Get-BoxesText $dlgOcr2.boxes))
        $filled2 = @($dlgOcr2.boxes | Where-Object { (([string]$_.text) -replace '\s+', '').Contains($Phone) }).Count -gt 0
        if (-not $filled2) {
            Throw-DriverError 'UI_CHANGED' ('弹窗输入框回读不含完整手机号（自动填号与手动回退均不匹配），已中止；详见 driver-log.txt')
        }
    }

    # 6) PostMessage Enter 触发检索（焦点由自动填号/步骤 5 点击输入框 + WM_KEYDOWN 输入建立，
    #    无需任何真实点击——企微 5.0.9 丢弃一切 SendInput 注入输入，2026-08-30 真机复核）
    Write-DriverLog 'step6 PostMessage Enter（初始检索触发）'
    Send-WeComEnter -Hwnd $dlgHwnd

    # 轮询等结果行（OCR result 模式，M1 原样）：结果区持续为空时每 2s 重发一次 Enter
    # （只读检索，重复安全），最多重发 5 次仍无结果 → CUSTOMER_NOT_FOUND（不重试）
    $ocr = $null
    $enterReposts = 0
    $lastEnterAt = [DateTime]::UtcNow
    $resultDeadline = [DateTime]::UtcNow.AddSeconds(15)
    while ([DateTime]::UtcNow -lt $resultDeadline) {
        Start-Sleep -Milliseconds 800
        $s4 = Save-StepShot $dlgHwnd 'step4-result.png'
        $ocr = Invoke-AddCustomerOcr ([string]$s4.path) 'result'
        if ($ocr.state -eq 'not_found') {
            Throw-DriverError 'CUSTOMER_NOT_FOUND' ('手机号 ' + $Phone + ' 未检索到微信用户（弹窗提示无结果），不会重试')
        }
        if ($ocr.state -eq 'sent') {
            Throw-DriverError 'UI_CHANGED' '结果行已是「已发送申请」状态（该号码可能此前已发出邀请），为避免重复发送已中止'
        }
        if ($ocr.state -eq 'addable') { break }
        $ocr = $null
        if ($enterReposts -lt 5 -and ([DateTime]::UtcNow - $lastEnterAt).TotalMilliseconds -ge 2000) {
            $enterReposts++
            Write-DriverLog ('step6 结果区为空，重发 Enter #' + $enterReposts)
            Send-WeComEnter -Hwnd $dlgHwnd
            $lastEnterAt = [DateTime]::UtcNow
        }
    }
    if ($null -eq $ocr) {
        Throw-DriverError 'CUSTOMER_NOT_FOUND' ('手机号 ' + $Phone + ' 检索超时（重发 Enter ' + $enterReposts + ' 次后 15s 内仍无结果行），视为无检索结果，不会重试')
    }
    [void]$shots.Add([string]$s4.path)
    $wechatName = [string]$ocr.wechat_name
    if ([string]::IsNullOrWhiteSpace($wechatName)) {
        Throw-DriverError 'EXECUTION_UNKNOWN' '结果行已出现但 OCR 读不出客户微信名（邀请未发送，但状态不可知，不会自动重试，请人工核对）'
    }

    # 7) PostMessage 点击结果行「添加」（2026-08-30 实测 PostMessage 有效；弹窗有数秒延迟，等 15s）
    #    点击前关遗留 InputReasonWnd（同陈旧弹窗防御：残留旧「发送」窗会被当成新窗误点发送）
    Close-StaleDialogGuard 'InputReasonWnd'
    $addX = [int]($s4.left + [int]$ocr.button.x)
    $addY = [int]($s4.top + [int]$ocr.button.y)
    Write-DriverLog ('step7 click 添加 screen=(' + $addX + ',' + $addY + ')（图像坐标=(' + $ocr.button.x + ',' + $ocr.button.y + ') + 截图原点=(' + $s4.left + ',' + $s4.top + ')）wechat_name=' + $wechatName)
    [void](Send-WeComClick -Hwnd $dlgHwnd -ScreenX $addX -ScreenY $addY)
    $reasonHwnd = Wait-WeComWindow -ClassName 'InputReasonWnd' -TimeoutMs 15000
    if ($reasonHwnd -eq 0) {
        Throw-DriverError 'EXECUTION_UNKNOWN' '点击「添加」后未等到「发送添加邀请」弹窗（InputReasonWnd）：邀请状态不可知，不会自动重试，请人工核对'
    }
    $s5 = Save-StepShot $reasonHwnd 'step5-reason.png'
    [void]$shots.Add([string]$s5.path)

    # 8) OCR 定位「发送」按钮 → PostMessage 点击（该按钮 PostMessage 有效，真机实测）
    #    坐标换算：reason 模式返回图像坐标（原点 = step5 截图即 InputReasonWnd 窗口左上角），
    #    屏幕坐标 = 截图原点($s5.left/$s5.top) + 图像坐标；Send-WeComClick 内部再 ScreenToClient 到弹窗客户区。
    $reasonOcr = Invoke-AddCustomerOcr ([string]$s5.path) 'reason'
    if ($reasonOcr.found -ne $true) {
        Throw-DriverError 'EXECUTION_UNKNOWN' '「发送添加邀请」弹窗内 OCR 未定位到「发送」按钮：邀请未发送但状态不可知，不会自动重试，请人工核对'
    }
    $sendX = [int]($s5.left + [int]$reasonOcr.send_button.x)
    $sendY = [int]($s5.top + [int]$reasonOcr.send_button.y)
    Write-DriverLog ('step8 click 发送 screen=(' + $sendX + ',' + $sendY + ')（图像坐标=(' + $reasonOcr.send_button.x + ',' + $reasonOcr.send_button.y + ') + 截图原点=(' + $s5.left + ',' + $s5.top + ')）reasonHwnd=' + $reasonHwnd)
    [void](Send-WeComClick -Hwnd $reasonHwnd -ScreenX $sendX -ScreenY $sendY)

    # 等弹窗关闭（实测发送后 InputReasonWnd ≤1s 自动关闭；上限 10s）；未关闭 → 状态不可知
    $closedDeadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $closedDeadline) {
        if (-not [WeComWin32]::IsWindow([IntPtr]$reasonHwnd)) { break }
        Start-Sleep -Milliseconds 300
    }
    if ([WeComWin32]::IsWindow([IntPtr]$reasonHwnd)) {
        Throw-DriverError 'EXECUTION_UNKNOWN' '点击「发送」后邀请弹窗 10s 内未关闭：邀请可能未发出，不会自动重试，请人工核对'
    }
    Write-DriverLog 'step8 InputReasonWnd 已关闭'
    $reasonHwnd = [int64]0

    # 9) 终态校验：SearchExternalsWnd 结果行按钮变「已发送申请」。
    #    实测时序（2026-08-30 真机）：发送后结果行 ≤3s 才刷新为「已发送申请」（tail-1 存档
    #    显示 1s 时仍是「添加」）——单次快照会误报，必须轮询：每 1s OCR 一次，上限 10s，
    #    仍非 sent → EXECUTION_UNKNOWN（fail-closed）。
    $final = $null
    $s6 = $null
    $finalDeadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $finalDeadline) {
        Start-Sleep -Milliseconds 1000
        $s6 = Save-StepShot $dlgHwnd 'step6-sent.png'
        $final = Invoke-AddCustomerOcr ([string]$s6.path) 'result'
        if ($final.state -eq 'sent') { break }
        $final = $null
    }
    if ($null -ne $s6) { [void]$shots.Add([string]$s6.path) }
    if ($null -eq $final) {
        Throw-DriverError 'EXECUTION_UNKNOWN' '终态校验失败：结果行按钮 10s 内未变为「已发送申请」（邀请可能已发出，不会自动重试，请人工核对；详见 driver-log.txt）'
    }
    Write-DriverLog 'step9 终态校验通过：已发送申请'

    return @{ wechat_name = $wechatName; screenshot_paths = $shots.ToArray() }
    } finally {
        # 成功/失败路径统一收尾（恢复原状）：关「发送添加邀请」（WM_CLOSE）与检索弹窗（点 ×），
        # 清空搜索框手机号残留（同时关掉可能残留的 overlay）；成功路径 reasonHwnd 已置 0、
        # 检索弹窗点 × 后 IsWindow 兜底为空操作
        if ($reasonHwnd -ne 0) { Close-ReasonDialogBestEffort $reasonHwnd }
        if ($dlgHwnd -ne 0) { Close-SearchDialogBestEffort $dlgHwnd }
        if ($mainHwnd -ne 0) { Clear-SearchResidualBestEffort $mainHwnd }
    }
}
