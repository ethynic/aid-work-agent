# drivers/ps1/add-customer.ps1 — wecom_add_customer 驱动（写动作，单次单号码）
# 全链路（设计文档 §3.5，2026-08-29 真机实测闭环；2026-08-30 真机复核修订：
# 企微 5.0.9 丢弃一切 SendInput/keybd_event 注入输入——含鼠标，全链路纯 PostMessage）：
#   1) 解析主窗口 → PostMessage 点「通讯录」导航（比例坐标，投递到主窗口）
#   2) 校验内容子窗口类名变为「WXworkWindow - 企业微信-通讯录」（fail-closed）
#      + OCR 校验落在「新的客户」页签（落错页签即中止，避免点错右上角按钮）
#   3) PostMessage 点内容子窗口右上角「⊕添加」→ 等 SearchExternalsWnd「添加客户」弹窗
#   4) 弹窗内 PostMessage 点输入框 → 纯 WM_KEYDOWN 逐字输入手机号
#   5) PostMessage Enter 触发检索（焦点由步骤 4 建立，无需真实点击）
#   6) 轮询截图 + RapidOCR（drivers/py/add_customer_result.py result 模式）等结果行，
#      读出微信名 + 「添加」按钮坐标；结果区持续为空时每 2s 重发一次 Enter（只读检索，重复安全），
#      最多重发 5 次仍无结果 → CUSTOMER_NOT_FOUND，不重试
#   7) PostMessage 点击「添加」（2026-08-30 实测有效；InputReasonWnd 弹窗有数秒延迟，等 15s）
#   8) OCR 定位「发送」按钮 → PostMessage 点击 → 等 InputReasonWnd 关闭
#   9) 终态校验：SearchExternalsWnd 结果行按钮变「已发送申请」（实测发送后 InputReasonWnd ≤1s 关闭、
#      结果行 ≤3s 才刷新；轮询每 1s 上限 10s），仍非 sent → EXECUTION_UNKNOWN（绝不重试）
# 关键步骤截图存 -ArtifactDir（step1..step6）；点击坐标与 OCR 原始输出写 driver-log.txt（真机排障）。
# 失败/异常路径 finally best-effort 关闭两个弹窗（设计 §3.5-7：恢复原状）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$Phone,
    [Parameter(Mandatory)][string]$ArtifactDir
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

# 诊断日志：点击目标坐标 / OCR 原始输出，写 artifact 目录 driver-log.txt（真机排障用）
$script:DriverLogPath = Join-Path $ArtifactDir 'driver-log.txt'
function Write-DriverLog([string]$Msg) {
    $line = '[' + ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')) + '] ' + $Msg
    Add-Content -Path $script:DriverLogPath -Value $line -Encoding UTF8
}

# 比例坐标（真机参考系：主窗口 1089x828 / 内容子窗口 929x828 / 弹窗 400x292，2026-08-29 实测）
$script:NavContactsPx = 61         # 导航栏「通讯录」：导航栏为固定像素列（2026-09-04 实测
$script:NavContactsPy = 1175       #   图标+文本块中心 x≈61、y≈1175；新版导航栏新增智能文档/
                                   #   智能总结/工作台等项，旧比例 0.4444h 会点进聊天列表，必须像素锚定）
$script:ChildAddRx = 0.9343       # 内容子窗口右上角「⊕添加」：宽 93.4%
$script:ChildAddRy = 0.0459       #                              高 4.6%
$script:DlgInputRx = 0.5000       # SearchExternalsWnd 输入框中心：宽 50%
$script:DlgInputRy = 0.2570       #                               高 25.7%
$script:DlgCloseRx = 0.9075       # 弹窗右上角 × ：宽 90.8%
$script:DlgCloseRy = 0.1027       #                高 10.3%

function Save-StepShot([int64]$Hwnd, [string]$Name) {
    # 关键步骤截图存档；返回 @{ path; left; top; w; h }（截图坐标系原点 = 窗口左上角）
    $path = Join-Path $ArtifactDir $Name
    $snap = Get-WeComWindowSnapshot -Hwnd $Hwnd -Path $path
    return @{ path = $path; left = [int]$snap[0]; top = [int]$snap[1]; w = [int]$snap[2]; h = [int]$snap[3] }
}

function Invoke-AddCustomerOcr([string]$ImagePath, [string]$Mode) {
    # 仓库 venv python 跑 RapidOCR；drivers/ps1 上四级为仓库根
    $repoRoot = (Resolve-Path (Join-Path $script:DriverPs1Dir '..\..\..\..')).Path
    $pythonExe = Join-Path $repoRoot 'venv\Scripts\python.exe'
    $ocrScript = Join-Path $script:DriverPs1Dir '..\py\add_customer_result.py'
    if (-not (Test-Path $ocrScript)) { Throw-DriverError 'INTERNAL_ERROR' ('未找到 OCR 脚本：' + $ocrScript) }
    if (-not (Test-Path $pythonExe)) { Throw-DriverError 'CONFIG_MISSING' ('未找到仓库 venv python（RapidOCR 所在解释器）：' + $pythonExe) }
    $out = & $pythonExe $ocrScript $ImagePath $Mode 2>$null
    if ($LASTEXITCODE -ne 0) { Throw-DriverError 'INTERNAL_ERROR' ('OCR 脚本执行失败（exit=' + $LASTEXITCODE + '）') }
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
    # best-effort 关闭「添加客户」弹窗（× 对 PostMessage 点击有效，真机实测）；失败不影响结果
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

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.AddCustomer' -Body {
    $shots = New-Object System.Collections.ArrayList
    # 弹窗 hwnd 跟踪：失败路径 finally 清理（设计 §3.5-7：任一步骤与预期不符 → 关闭弹窗恢复原状）
    $dlgHwnd = [int64]0
    $reasonHwnd = [int64]0
    try {
    # 1) 主窗口 → 点「通讯录」导航
    $mainHwnd = Resolve-WeComMainWindow
    $main = Get-WeComWindowInfo ([IntPtr]$mainHwnd)
    $navX = [int]($main.X + $script:NavContactsPx)
    $navY = [int]($main.Y + $script:NavContactsPy)
    Write-DriverLog ('step1 click 通讯录导航 screen=(' + $navX + ',' + $navY + ') mainHwnd=' + $mainHwnd + ' rect=(' + $main.X + ',' + $main.Y + ',' + $main.W + 'x' + $main.H + ')')
    [void](Send-WeComClick -Hwnd $mainHwnd -ScreenX $navX -ScreenY $navY)

    # 2) 等「通讯录」内容子窗口可见（fail-closed：类名编码页名，错页即中止）
    $child = $null
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while ([DateTime]::UtcNow -lt $deadline) {
        $vis = @(Get-WeComContentChildWindow -MainHwnd $mainHwnd -PageName '通讯录' | Where-Object { $_.Visible })
        if ($vis.Count -gt 0) { $child = $vis[0]; break }
        Start-Sleep -Milliseconds 300
    }
    if ($null -eq $child) { Throw-DriverError 'UI_CHANGED' '点击「通讯录」后未出现通讯录内容子窗口（导航失败或页面结构已变化）' }
    $s1 = Save-StepShot $mainHwnd 'step1-contacts.png'
    [void]$shots.Add([string]$s1.path)

    # 2.5) OCR 校验落在「新的客户」页签（设计 §3.5-2，fail-closed：落在其他页签时点右上角「⊕添加」可能开错弹窗/误点其他按钮）
    $pageOcr = Invoke-AddCustomerOcr ([string]$s1.path) 'page'
    if ($pageOcr.found -ne $true) {
        Throw-DriverError 'UI_CHANGED' '通讯录页未落在「新的客户」页签（OCR 未识别到页头），为避免误点已中止，请人工切到「新的客户」后重试'
    }

    # 3) 点内容子窗口右上角「⊕添加」→ 等 SearchExternalsWnd 弹窗
    $child = Get-WeComWindowInfo ([IntPtr]$child.Hwnd)  # 重新取 rect（可能重排）
    $childAddX = [int]($child.X + $child.W * $script:ChildAddRx)
    $childAddY = [int]($child.Y + $child.H * $script:ChildAddRy)
    Write-DriverLog ('step3 click ⊕添加 screen=(' + $childAddX + ',' + $childAddY + ') childHwnd=' + $child.Hwnd + ' rect=(' + $child.X + ',' + $child.Y + ',' + $child.W + 'x' + $child.H + ')')
    [void](Send-WeComClick -Hwnd ([int64]$child.Hwnd) -ScreenX $childAddX -ScreenY $childAddY)
    $dlgHwnd = Wait-WeComWindow -ClassName 'SearchExternalsWnd' -TimeoutMs 10000
    if ($dlgHwnd -eq 0) { Throw-DriverError 'UI_CHANGED' '未等到「添加客户」弹窗（SearchExternalsWnd），页面结构可能已变化' }
    $s2 = Save-StepShot $dlgHwnd 'step2-dialog.png'
    [void]$shots.Add([string]$s2.path)

    # 4) 弹窗内点输入框 → 纯 WM_KEYDOWN 输入手机号
    $dlg = Get-WeComWindowInfo ([IntPtr]$dlgHwnd)
    $inputX = [int]($dlg.X + $dlg.W * $script:DlgInputRx)
    $inputY = [int]($dlg.Y + $dlg.H * $script:DlgInputRy)
    Write-DriverLog ('step4 click 输入框 screen=(' + $inputX + ',' + $inputY + ') dlgHwnd=' + $dlgHwnd + ' rect=(' + $dlg.X + ',' + $dlg.Y + ',' + $dlg.W + 'x' + $dlg.H + ')')
    [void](Send-WeComClick -Hwnd $dlgHwnd -ScreenX $inputX -ScreenY $inputY)
    Start-Sleep -Milliseconds 300
    Send-WeComText -Hwnd $dlgHwnd -Text $Phone
    $s3 = Save-StepShot $dlgHwnd 'step3-typed.png'
    [void]$shots.Add([string]$s3.path)

    # 5) PostMessage Enter 触发检索（焦点由步骤 4 的 PostMessage 点击 + WM_KEYDOWN 输入建立，
    #    无需任何真实点击——企微 5.0.9 丢弃一切 SendInput 注入输入，2026-08-30 真机复核）
    Write-DriverLog 'step5 PostMessage Enter（初始检索触发）'
    Send-WeComEnter -Hwnd $dlgHwnd

    # 6) 轮询等结果行（OCR result 模式）：结果区持续为空时每 2s 重发一次 Enter（只读检索，重复安全），
    #    最多重发 5 次仍无结果 → CUSTOMER_NOT_FOUND（不重试）
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

    # 9) 终态校验：SearchExternalsWnd 结果行按钮变「已发送申请」。
    #    实测时序（2026-08-30 真机）：发送后结果行 ≤3s 才刷新为「已发送申请」（tail-1 存档显示 1s 时仍是「添加」）——
    #    单次快照会误报，必须轮询：每 1s OCR 一次，上限 10s，仍非 sent → EXECUTION_UNKNOWN（fail-closed）。
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

    Close-SearchDialogBestEffort $dlgHwnd
    $dlgHwnd = [int64]0
    return @{ wechat_name = $wechatName; screenshot_paths = $shots.ToArray() }
    } finally {
        # 失败/异常路径恢复原状（设计 §3.5-7）：关闭「发送添加邀请」（WM_CLOSE）与「添加客户」（点 ×）弹窗；
        # 成功路径两个弹窗均已关闭/置 0，此处为空操作
        if ($reasonHwnd -ne 0) { Close-ReasonDialogBestEffort $reasonHwnd }
        if ($dlgHwnd -ne 0) { Close-SearchDialogBestEffort $dlgHwnd }
    }
}
