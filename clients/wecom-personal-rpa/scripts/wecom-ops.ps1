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
#   get_login_state 入参：{}                        检测企微登录态（占位实现）
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
# 必要性：企微聊天会话 Ctrl+V 粘贴文件路径（如果是文本路径会变成文本消息），
# 文件发送需要专门的「发送文件」入口（拖拽或文件传输助手），目前先复用图片粘贴路径，
# 但只对图片文件有效。非图片文件留待后续完善（占位实现）。
function Send-WeComFileInternal {
    param([string]$Keyword, [string]$FilePath)

    if ([string]::IsNullOrEmpty($FilePath) -or -not (Test-Path $FilePath)) {
        return @{
            success = $false
            error_code = 'invalid_params'
            error_message = "file_path 不存在或为空：$FilePath"
        }
    }

    # 占位：复用图片发送逻辑（仅对图片文件可靠），其他文件类型 TODO
    return Send-WeComImageInternal -Keyword $Keyword -ImagePath $FilePath
}

# ---------- 内部：检测企微登录态（占位） ----------
# Phase 2 仅占位：找到 WeWorkWindow 即返回 online。
# 实际登录态识别（区分「未登录二维码页」「已登录主界面」「锁定」）由 Phase 3 F7 详细实现。
function Get-WeComLoginStateInternal {
    $origin = Get-WeWorkWindowOrigin
    if (-not $origin) {
        return @{
            success = $true
            state = 'offline'
            message = '未找到企微主窗口，可能未启动或未登录'
        }
    }
    return @{
        success = $true
        state = 'online'
        hwnd = [string]$origin.Hwnd
        window_origin = @{ left = $origin.Left; top = $origin.Top; width = $origin.Width; height = $origin.Height }
        message = '占位实现：找到 WeWorkWindow 即视为 online，详细识别见 Phase 3 F7'
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
