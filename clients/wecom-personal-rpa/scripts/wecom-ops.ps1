# ====================================================================================
# wecom-ops.ps1
#
# 企微操作 PowerShell 主入口。被 C# 端 PowershellOpsInvoker 通过 Process+JSON 调用。
#
# 调用契约（见设计文档 §F2）：
#   powershell -ExecutionPolicy Bypass -NoProfile -File wecom-ops.ps1 -Action <action>
#   stdin：UTF-8 JSON 字符串（参数对象）
#   stdout：最后一行为结果 JSON（ConvertTo-Json -Compress），C# 端按行解析
#
# 支持的 action：
#   search_user     入参：{keyword}                搜索用户并进入会话
#   send_text       入参：{keyword, text}          进入会话并发送文本
#   send_image      入参：{keyword, image_path}    进入会话并发送图片
#   send_file       入参：{keyword, file_path}     进入会话并发送文件（图片走 send_image）
#   get_login_state 入参：{} / {qr_region_bbox:[x1,y1,x2,y2]}
#                                                   检测企微登录态，未登录时附带二维码 base64
#
# 编码：UTF-8 with BOM。PS 5.1 看到 BOM 会按 UTF-8 解析源码，中文字面量不乱码。
# ====================================================================================

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('search_user', 'send_text', 'send_image', 'send_file', 'get_login_state')]
    [string]$Action
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

# dot-source lib
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir 'wecom-ops-lib.ps1')

# 从 stdin 读 JSON 参数（C# 端通过 StandardInput 写入 JSON 后 Close）
$stdinText = [Console]::In.ReadToEnd()
$params = if ($stdinText) { $stdinText | ConvertFrom-Json } else { [PSCustomObject]@{} }

# ---------- 内部：搜索用户并进入会话 ----------
# 算法：取 WeWorkWindow origin → 计算搜索框 bbox（按窗口宽度 scale）→ 点击搜索框
#       → Ctrl+A Delete 清空 → Type-Text 输入 keyword → 等 1500ms → 按 Enter → 等 2500ms
# 返回 hashtable，调用方包装成最终结果。
function Search-WeComUserInternal {
    param([string]$Keyword)

    if ([string]::IsNullOrEmpty($Keyword)) {
        return @{
            success = $false
            error_code = 'invalid_params'
            error_message = 'keyword 不能为空'
        }
    }

    $origin = Get-WeWorkWindowOrigin
    if (-not $origin) {
        return @{
            success = $false
            error_code = 'wecom_window_not_found'
            error_message = '找不到企微主窗口（WeWorkWindow），请确认企微已启动并登录'
        }
    }

    # 搜索框固定坐标方案：企微 5.0.8 在 1936x2088 (DPI=1.5) 下搜索框 bbox=[330,34,430,66]
    # 不同 DPI 下窗口尺寸不同但 UI 相对位置不变，按窗口宽度自动换算
    $scale = [double]$origin.Width / 1936.0
    $searchBbox = @(
        [int][Math]::Round(330 * $scale),
        [int][Math]::Round(34 * $scale),
        [int][Math]::Round(430 * $scale),
        [int][Math]::Round(66 * $scale)
    )
    $cx = [int][Math]::Round(($searchBbox[0] + $searchBbox[2]) / 2.0) + $origin.Left
    $cy = [int][Math]::Round(($searchBbox[1] + $searchBbox[3]) / 2.0) + $origin.Top

    # 点击搜索框 → 清空 → 输入 → 等渲染 → Enter 进入第一个搜索结果 → 等会话切换
    Click-At -x $cx -y $cy
    Start-Sleep -Milliseconds 500
    Press-CtrlA-Delete
    Start-Sleep -Milliseconds 200
    Type-Text -text $Keyword
    Start-Sleep -Milliseconds 1500
    Press-Enter
    Start-Sleep -Milliseconds 2500

    # TODO（Phase 2 后续补）：会话标题验证（PaddleOCR 识别企微顶部，确认标题包含 keyword）
    # 当前基于 debug-navigate.ps1 已验证行为：Enter 直接进入第一个高亮搜索结果（企微 5.0.8）

    return @{
        success = $true
        keyword = $Keyword
        window_origin = @{ left = $origin.Left; top = $origin.Top; width = $origin.Width; height = $origin.Height }
        clicked_x = $cx
        clicked_y = $cy
    }
}

# ---------- 内部：进入会话 + 输入文本 + Enter 发送 ----------
function Send-WeComTextInternal {
    param([string]$Keyword, [string]$Text)

    if ([string]::IsNullOrEmpty($Text)) {
        return @{
            success = $false
            error_code = 'invalid_params'
            error_message = 'text 不能为空'
        }
    }

    $nav = Search-WeComUserInternal -Keyword $Keyword
    if (-not $nav.success) { return $nav }

    Type-Text -text $Text
    Start-Sleep -Milliseconds 300
    Press-Enter
    Start-Sleep -Milliseconds 500

    return @{
        success = $true
        keyword = $Keyword
        sent_text = $Text
    }
}

# ---------- 内部：进入会话 + 剪贴板粘贴图片 + Enter 发送 ----------
function Send-WeComImageInternal {
    param([string]$Keyword, [string]$ImagePath)

    if ([string]::IsNullOrEmpty($ImagePath) -or -not (Test-Path $ImagePath)) {
        return @{
            success = $false
            error_code = 'invalid_params'
            error_message = "image_path 不存在或为空：$ImagePath"
        }
    }

    $nav = Search-WeComUserInternal -Keyword $Keyword
    if (-not $nav.success) { return $nav }

    Add-Type -AssemblyName System.Drawing
    Add-Type -AssemblyName System.Windows.Forms

    # 读图片到 Bitmap → Clipboard.SetImage，剪贴板重试 3 次（每次间隔 100ms）
    # 必要性：Clipboard.SetImage 偶发失败（被其他进程占住剪贴板锁），重试可恢复
    $bmp = $null
    $setOk = $false
    $lastErr = ''
    for ($i = 1; $i -le 3; $i++) {
        try {
            $bmp = [System.Drawing.Bitmap]::FromFile($ImagePath)
            [System.Windows.Forms.Clipboard]::SetImage($bmp)
            $setOk = $true
            break
        } catch {
            $lastErr = $_.Exception.Message
            Start-Sleep -Milliseconds 100
        } finally {
            if ($bmp) { $bmp.Dispose() }
        }
    }
    if (-not $setOk) {
        return @{
            success = $false
            error_code = 'clipboard_set_failed'
            error_message = "剪贴板 SetImage 失败（重试 3 次）：$lastErr"
        }
    }

    Start-Sleep -Milliseconds 300
    Press-CtrlV
    Start-Sleep -Milliseconds 1500  # 等企微图片预览对话框弹出
    Press-Enter
    Start-Sleep -Milliseconds 800

    return @{
        success = $true
        keyword = $Keyword
        sent_image = $ImagePath
    }
}

# ---------- 内部：进入会话 + 发送文件 ----------
# 实现：剪贴板 SetFileDropList（系统级文件拖放数据）+ Ctrl+V。
# 企微对图片走 SetImage（剪贴板图像数据），对任意文件需要 SetFileDropList（文件 drop 列表），
# 触发企微"发送文件给 X"对话框（再 Enter 确认）。
function Send-WeComFileInternal {
    param([string]$Keyword, [string]$FilePath)

    if ([string]::IsNullOrEmpty($FilePath) -or -not (Test-Path $FilePath)) {
        return @{
            success = $false
            error_code = 'invalid_params'
            error_message = "file_path 不存在或为空：$FilePath"
        }
    }

    # 进入会话
    $nav = Search-WeComUserInternal -Keyword $Keyword
    if (-not $nav.success) { return $nav }

    Add-Type -AssemblyName System.Windows.Forms

    # 剪贴板放文件 drop list，重试 3 次（被其他进程占住剪贴板锁时常见）
    $dropList = $null
    $setOk = $false
    $lastErr = ''
    for ($i = 1; $i -le 3; $i++) {
        try {
            $dropList = New-Object System.Collections.Specialized.StringCollection
            $dropList.Add((Resolve-Path $FilePath).Path) | Out-Null
            [System.Windows.Forms.Clipboard]::SetFileDropList($dropList)
            $setOk = $true
            break
        } catch {
            $lastErr = $_.Exception.Message
            Start-Sleep -Milliseconds 100
        }
    }
    if (-not $setOk) {
        return @{
            success = $false
            error_code = 'clipboard_set_failed'
            error_message = "剪贴板 SetFileDropList 失败（重试 3 次）：$lastErr"
        }
    }

    Start-Sleep -Milliseconds 300

    # Ctrl+V（企微弹出"发送给 X"对话框，确认要发送给当前会话）
    Press-CtrlV
    Start-Sleep -Milliseconds 1500

    # Enter 确认发送
    Press-Enter
    Start-Sleep -Milliseconds 800

    # 清空剪贴板，避免后续操作误用
    try { [System.Windows.Forms.Clipboard]::Clear() } catch { }

    return @{
        success = $true
        keyword = $Keyword
        sent_file_name = (Split-Path $FilePath -Leaf)
    }
}

# ---------- 内部：检测企微登录态（Phase 3 F7 详细化） ----------
# 算法：
#   1. EnumWindows 找 WeWorkWindow（Get-WeWorkWindowOrigin 已封装）→ 找不到继续找小窗口
#   2. 启发式判断未登录二维码页：
#      方法 A（主）：企微登录页通常窗口较小（约 380x540）居中显示，主界面 >=600x400。
#                    Get-WeWorkWindowOrigin 只接受 >=600x400 的窗口作为主窗口，
#                    因此返回 null 但存在更小的 WeWorkWindow → 二维码页。
#      方法 B（兜底）：若主窗口存在但宽 < 500 且高 < 700（罕见，留作降级）。
#   3. need_login 状态下：从配置取 RegionBboxBase，加上窗口 origin 偏移，截屏 → base64 PNG。
#
# 注意：qr_image_base64 为敏感数据，Write-Result 必须输出（契约需要），但 console 日志中
#       不得打印 base64 内容（仅记录长度）。
function Get-WeComLoginStateInternal {
    $origin = Get-WeWorkWindowOrigin

    if (-not $origin) {
        # 主窗口（>=600x400 的 WeWorkWindow）不存在。可能是：
        #   (a) 企微未启动 / 已退出 → offline
        #   (b) 企微处于登录二维码页（小窗口） → need_login
        # 区分：枚举所有 WeWorkWindow（不限制尺寸），若存在小窗口（宽 < 500 且高 < 700）→ need_login
        $smallWnd = Find-SmallWeWorkWindow
        if ($smallWnd) {
            $qrBase64 = Capture-QrCodeBase64 -WindowOrigin $smallWnd -RegionBboxBase (Get-QrRegionBBox)
            return @{
                success = $true
                state = 'need_login'
                hwnd = [string]$smallWnd.Hwnd
                window_origin = @{ left = $smallWnd.Left; top = $smallWnd.Top; width = $smallWnd.Width; height = $smallWnd.Height }
                qr_image_base64 = $qrBase64
                qr_image_length = ($qrBase64 | Measure-Object -Character).Characters
                message = '检测到登录二维码窗口（小尺寸 WeWorkWindow），等待扫码'
            }
        }
        return @{
            success = $true
            state = 'offline'
            message = '未找到企微主窗口，可能未启动或已退出'
        }
    }

    # 主窗口存在（>=600x400）：判定为 online
    # TODO（Phase 5 真机校准）：补充「二维码已过期」「账号被限制」「桌面锁定」等细化状态识别。
    return @{
        success = $true
        state = 'online'
        hwnd = [string]$origin.Hwnd
        window_origin = @{ left = $origin.Left; top = $origin.Top; width = $origin.Width; height = $origin.Height }
        message = '企微主窗口存在，视为 online'
    }
}

# ---------- 枚举所有可见 WeWorkWindow（不限尺寸），返回最小的那个 ----------
# 必要性：Get-WeWorkWindowOrigin 只挑 >=600x400 的主窗口，登录二维码页（约 380x540）
#         不满足条件会被跳过。需要单独枚举小窗口判定登录态。
function Find-SmallWeWorkWindow {
    $script:wopsSmall = $null
    $script:wopsSmallArea = [int]::MaxValue
    [WeOpsWin32]::EnumWindows({
        param($h, $l)
        $sb = New-Object System.Text.StringBuilder 256
        [WeOpsWin32]::GetClassName($h, $sb, 256) | Out-Null
        if ($sb.ToString() -eq 'WeWorkWindow' -and [WeOpsWin32]::IsWindowVisible($h)) {
            $r = New-Object WeOpsWin32+RECT
            [void][WeOpsWin32]::GetWindowRect($h, [ref]$r)
            $w = $r.Right - $r.Left; $hgt = $r.Bottom - $r.Top
            # 登录二维码页窗口：宽 < 500 且高 < 700（企微登录窗口典型 380x540）
            if ($w -lt 500 -and $hgt -lt 700) {
                $area = $w * $hgt
                if ($area -lt $script:wopsSmallArea) {
                    $script:wopsSmallArea = $area
                    $script:wopsSmall = @{
                        Hwnd = $h; Left = $r.Left; Top = $r.Top; Width = $w; Height = $hgt
                    }
                }
            }
        }
        return $true
    }, [IntPtr]::Zero) | Out-Null
    return $script:wopsSmall
}

# ---------- 从 stdin JSON 参数读取二维码 bbox 配置 ----------
# 必要性：QrCodeWatcher 通过 stdin JSON 把 RegionBboxBase 传给 PS，
#         参数 key 为 qr_region_bbox（驼峰）。缺失时用默认 [530,200,800,470]。
# 注意：$params 在脚本顶部已 ConvertFrom-Json，此处只读。
function Get-QrRegionBBox {
    $default = @(530, 200, 800, 470)
    if ($params -and $params.qr_region_bbox) {
        $arr = @($params.qr_region_bbox)
        if ($arr.Count -eq 4) { return @([int]$arr[0], [int]$arr[1], [int]$arr[2], [int]$arr[3]) }
    }
    return $default
}

# ---------- 截取二维码区域并返回 base64 PNG ----------
# 算法：Graphics.CopyFromScreen 截取 [x1,y1,x2,y2]（屏幕物理坐标）→ Bitmap → PNG → base64。
# 必要性：登录窗口 origin 由 Find-SmallWeWorkWindow 提供；bbox 为相对窗口左上角的偏移，
#         截屏前需加上 origin.Left / origin.Top。
function Capture-QrCodeBase64 {
    param(
        [Parameter(Mandatory = $true)]$WindowOrigin,
        [Parameter(Mandatory = $true)][int[]]$RegionBboxBase
    )
    try {
        Add-Type -AssemblyName System.Drawing

        $x1 = [int]$RegionBboxBase[0] + [int]$WindowOrigin.Left
        $y1 = [int]$RegionBboxBase[1] + [int]$WindowOrigin.Top
        $x2 = [int]$RegionBboxBase[2] + [int]$WindowOrigin.Left
        $y2 = [int]$RegionBboxBase[3] + [int]$WindowOrigin.Top
        $w = [Math]::Max(1, $x2 - $x1)
        $h = [Math]::Max(1, $y2 - $y1)

        $bmp = New-Object System.Drawing.Bitmap $w, $h
        try {
            $g = [System.Drawing.Graphics]::FromImage($bmp)
            try {
                $g.CopyFromScreen($x1, $y1, 0, 0, (New-Object System.Drawing.Size $w, $h))
            } finally { $g.Dispose() }
            $ms = New-Object System.IO.MemoryStream
            try {
                $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
                $bytes = $ms.ToArray()
                return [System.Convert]::ToBase64String($bytes)
            } finally { $ms.Dispose() }
        } finally { $bmp.Dispose() }
    } catch {
        # 截图失败不致命：返回 null，调用方仍能上报 state=need_login（仅缺二维码）
        return $null
    }
}

# ====================================================================================
# 主入口
# ====================================================================================
$sw = [System.Diagnostics.Stopwatch]::StartNew()
try {
    switch ($Action) {
        'search_user'     { $result = Search-WeComUserInternal -Keyword $params.keyword }
        'send_text'       { $result = Send-WeComTextInternal -Keyword $params.keyword -Text $params.text }
        'send_image'      { $result = Send-WeComImageInternal -Keyword $params.keyword -ImagePath $params.image_path }
        'send_file'       { $result = Send-WeComFileInternal -Keyword $params.keyword -FilePath $params.file_path }
        'get_login_state' { $result = Get-WeComLoginStateInternal }
    }
    $sw.Stop()
    # 注入统一字段（不覆盖业务返回的 success/error_code）
    if (-not $result.ContainsKey('action')) { $result['action'] = $Action }
    if (-not $result.ContainsKey('duration_ms')) { $result['duration_ms'] = $sw.ElapsedMilliseconds }
    Write-Result $result
} catch {
    $sw.Stop()
    Write-Result @{
        success = $false
        action = $Action
        error_code = 'ps_script_exception'
        error_message = $_.Exception.Message
        duration_ms = $sw.ElapsedMilliseconds
    }
}
