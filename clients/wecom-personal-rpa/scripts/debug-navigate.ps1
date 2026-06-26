# ====================================================================================
# debug-navigate.ps1
#
# 用 qwen3-vl-plus 视觉模型 + 截图逐步调试"搜索进入文件传输助手会话"流程。
# 按用户要求：一步一步手动跳，每步停下让用户确认。
#
# 用法：
#   pwsh -File debug-navigate.ps1 -Step screenshot        # Step 1: 截图企微主窗口
#   pwsh -File debug-navigate.ps1 -Step locate_search     # Step 2: 视觉定位搜索框 bbox
#   pwsh -File debug-navigate.ps1 -Step click_search      # Step 3: 点击搜索框
#   pwsh -File debug-navigate.ps1 -Step type_keyword      # Step 4: 清空+输入"文件传输助手"
#   pwsh -File debug-navigate.ps1 -Step locate_result     # Step 5: 视觉定位搜索结果中目标项
#   pwsh -File debug-navigate.ps1 -Step click_result      # Step 6: 点击进入会话
#
# 状态持久化：debug-state.json 保存每步的截图路径、bbox、窗口 origin，下一步读取复用。
# 调试产物：debug-out/ 目录下，每步的截图都画上 bbox 红框方便人工核对。
# ====================================================================================

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('screenshot', 'locate_search', 'click_search', 'type_keyword', 'locate_result', 'click_result', 'send_text', 'send_screenshot')]
    [string]$Step,

    [string]$Keyword = '',
    [string]$Model = 'qwen3-vl-plus',
    [string]$OutDir = ''
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ---------- 路径基础 ----------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $OutDir) { $OutDir = Join-Path $ScriptDir '..\debug-out' }
$OutDir = (New-Object -TypeName System.IO.DirectoryInfo -ArgumentList $OutDir).FullName
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$StateFile = Join-Path $OutDir 'debug-state.json'

function Write-Step([string]$msg) { [Console]::Error.WriteLine("[debug-navigate] $msg") }
function Write-Json([hashtable]$r) { [Console]::Out.WriteLine(($r | ConvertTo-Json -Depth 5 -Compress)) }

# ---------- 关键词字典（从 UTF-8 文件读，避免 PS 5.1 按 GBK 解析中文字面量导致字符串不匹配） ----------
# 必要性：中文 Windows 系统上 PS 5.1 默认按 GBK(CP936) 解析脚本里的中文字面量，
# 但 HttpClient 拿到的 API 响应按 UTF-8 解码，两边字符不相等导致 Where-Object / Contains 失败。
# 解决：所有中文关键词放到 UTF-8 编码的 keywords.txt，运行时读取。
$keywordsFile = Join-Path $ScriptDir 'prompts\keywords.txt'
$global:Kw = @{}
foreach ($line in Get-Content $keywordsFile -Encoding UTF8) {
    $trimmed = $line.Trim()
    if (-not $trimmed) { continue }
    if ($trimmed.StartsWith('#')) { continue }
    $idx = $trimmed.IndexOf('=')
    if ($idx -le 0) { continue }
    $key = $trimmed.Substring(0, $idx).Trim()
    $val = $trimmed.Substring($idx + 1).Trim()
    $global:Kw[$key] = $val
}
Write-Step ("加载关键词字典：{0} 个键" -f $global:Kw.Count)

# -Keyword 参数没传时从字典取默认值（避免脚本字面量被 GBK 解析错）
if ([string]::IsNullOrEmpty($Keyword)) {
    $Keyword = $global:Kw['default_keyword']
}
Write-Step ("使用关键词: '$Keyword'")

# ---------- 状态文件读写 ----------
function Load-State {
    if (-not (Test-Path $StateFile)) { return @{} }
    try {
        # PS 5.1 不支持 -AsHashtable，ConvertFrom-Json 返回 PSCustomObject，
        # 用一个辅助函数把 PSCustomObject 递归转成 hashtable，方便后续 .Clone() / 字段更新。
        $obj = Get-Content $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        return ConvertTo-Hashtable $obj
    } catch {
        Write-Step "状态文件损坏，重置：$($_.Exception.Message)"
        return @{}
    }
}
function ConvertTo-Hashtable($obj) {
    if ($obj -is [Array]) {
        $arr = @()
        foreach ($i in $obj) { $arr += (ConvertTo-Hashtable $i) }
        return $arr
    }
    if ($obj -is [System.Management.Automation.PSCustomObject]) {
        $h = @{}
        foreach ($p in $obj.PSObject.Properties) {
            $h[$p.Name] = ConvertTo-Hashtable $p.Value
        }
        return $h
    }
    return $obj
}
function Save-State([hashtable]$s) {
    $s | ConvertTo-Json -Depth 5 | Set-Content $StateFile -Encoding UTF8
}

# ---------- 读 API key ----------
function Resolve-ApiKey {
    # 优先级：环境变量 > .env 文件
    $k = $env:QWEN_API_KEYS
    if ($k) { return ($k -split ',') | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -First 1 }
    $envFile = 'c:\repos\aid-work-agent\.env'
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile -Encoding UTF8) {
            if ($line -match '^\s*QWEN_API_KEYS\s*=\s*(.+)\s*$') {
                $val = $Matches[1].Trim().Trim('"').Trim("'")
                return ($val -split ',') | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -First 1
            }
        }
    }
    throw 'QWEN_API_KEYS 未配置（环境变量或 c:\repos\aid-work-agent\.env）'
}

# ---------- 截图企微主窗口（复用 capture-wecom-for-csharp.ps1 的能力） ----------
function Capture-WeCom {
    $captureScript = Join-Path $ScriptDir 'capture-wecom-for-csharp.ps1'
    if (-not (Test-Path $captureScript)) { throw "找不到 $captureScript" }
    $tmpOut = Join-Path $OutDir ('cap_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
    New-Item -ItemType Directory -Force -Path $tmpOut | Out-Null
    $stdoutFile = Join-Path $tmpOut 'stdout.log'
    # 必要性：$ErrorActionPreference=Stop 会让子进程任何 stderr 输出（即使中文日志）变成 NativeCommandError。
    # 这里临时降为 SilentlyContinue，调用完恢复。
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try {
        # 用 cmd /c 包一层让 PS 不感知 stderr（关键：避免 NativeCommandError 中断）
        $cmdLine = 'powershell -ExecutionPolicy Bypass -NoProfile -File "' + $captureScript + '" -OutDir "' + $tmpOut + '"'
        cmd /c "$cmdLine > `"$stdoutFile`" 2>&1" | Out-Null
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    if (-not (Test-Path $stdoutFile)) { throw "capture 脚本未生成 stdout 文件" }
    $capOut = Get-Content $stdoutFile -Encoding UTF8
    $lastJson = ($capOut | Where-Object { $_ -match '^\{' }) | Select-Object -Last 1
    if (-not $lastJson) { throw "capture 脚本无 JSON 输出，stdout 见：$stdoutFile" }
    $cap = $lastJson | ConvertFrom-Json
    if (-not $cap.ok) { throw "截图失败：$($cap.error)" }
    return @{
        png_path = $cap.png_path
        left = [int]$cap.left
        top = [int]$cap.top
        width = [int]$cap.width
        height = [int]$cap.height
        dpi_scale = [double]$cap.dpi_scale
        wecom_version = [string]$cap.wecom_version
        hwnd = [string]$cap.hwnd
    }
}

# ---------- 调 qwen3-vl-plus（OpenAI 兼容协议） ----------
function Call-QwenVl {
    param(
        [string]$ApiKey,
        [string]$PromptText,
        [string]$ImagePath
    )
    $endpoint = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
    $bytes = [System.IO.File]::ReadAllBytes($ImagePath)
    $b64 = [Convert]::ToBase64String($bytes)

    # 注意 MIME 推断：capture 脚本保存为 PNG
    $dataUri = "data:image/png;base64,$b64"

    $body = @{
        model = $Model
        messages = @(
            @{
                role = 'user'
                content = @(
                    @{ type = 'image_url'; image_url = @{ url = $dataUri } }
                    @{ type = 'text'; text = $PromptText }
                )
            }
        )
        temperature = 0.1
        max_tokens = 4096
    } | ConvertTo-Json -Depth 10

    $hdr = @{
        'Authorization' = "Bearer $ApiKey"
        'Content-Type' = 'application/json'
    }
    Write-Step "调 API: model=$Model endpoint=$endpoint"

    # 必要性：PS 5.1 的 Invoke-RestMethod 默认按 ISO-8859-1 解码响应，
    # 中文 content 会被错误地解码成 "é´ä¼" 这种乱码（字节对，无法再还原）。
    # 用 HttpClient 拿 raw 字节，再用 UTF-8 显式 decode。
    Add-Type -AssemblyName System.Net.Http
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromSeconds(120)
    try {
        $content = New-Object System.Net.Http.StringContent($body, [System.Text.Encoding]::UTF8, 'application/json')
        $req = New-Object System.Net.Http.HttpRequestMessage('Post', $endpoint)
        $req.Headers.Add('Authorization', "Bearer $ApiKey")
        $req.Content = $content
        $respMsg = $client.SendAsync($req).Result
        $rawBytes = $respMsg.Content.ReadAsByteArrayAsync().Result
        # 显式 UTF-8 decode
        $jsonStr = [System.Text.Encoding]::UTF8.GetString($rawBytes)
        $resp = $jsonStr | ConvertFrom-Json
        # 把 content 也保存一份到文件，方便排查（content 中的中文已经是正确的 UTF-8 字符串了）
        return $resp
    } finally {
        $client.Dispose()
    }
}

# ---------- 调 PaddleOCR layout-parsing（OpenAI 兼容协议） ----------
# 必要性：qwen3-vl-plus 在企微 UI 上 bbox 不稳定（同一张图调多次结果不同）。
# PaddleOCR 的 layout-parsing 返回的 parsing_res_list[].block_bbox 是稳定的像素坐标，
# 文字识别 100% 准确，适合"按文字内容定位坐标"的场景。
# 参考：src/tools/ocr/ocr_tool.py 的 _make_paddleocr_request
function Call-PaddleOcr {
    param([string]$ImagePath)

    # 从 .env 读 PaddleOCR 配置
    $apiUrl = ''; $token = ''
    foreach ($line in Get-Content 'c:\repos\aid-work-agent\.env' -Encoding UTF8) {
        if ($line -match '^\s*PADDLEOCR_DOC_PARSING_API_URL\s*=\s*(.+?)\s*$') { $apiUrl = $Matches[1].Trim().Trim('"').Trim("'") }
        if ($line -match '^\s*PADDLEOCR_ACCESS_TOKEN\s*=\s*(.+?)\s*$') { $token = $Matches[1].Trim().Trim('"').Trim("'") }
    }
    if (-not $apiUrl -or -not $token) { throw 'PADDLEOCR 配置缺失（.env 中 PADDLEOCR_DOC_PARSING_API_URL/PADDLEOCR_ACCESS_TOKEN）' }

    $bytes = [System.IO.File]::ReadAllBytes($ImagePath)
    $b64 = [Convert]::ToBase64String($bytes)

    $body = @{ file = $b64; fileType = 1 } | ConvertTo-Json -Depth 5 -Compress

    Add-Type -AssemblyName System.Net.Http
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromSeconds(300)
    try {
        $content = New-Object System.Net.Http.StringContent($body, [System.Text.Encoding]::UTF8, 'application/json')
        $req = New-Object System.Net.Http.HttpRequestMessage('Post', $apiUrl)
        $req.Headers.Add('Authorization', "token $token")
        $req.Headers.Add('Client-Platform', 'official-skill')
        $req.Content = $content
        Write-Step "调 PaddleOCR: $apiUrl"
        $respMsg = $client.SendAsync($req).Result
        $rawBytes = $respMsg.Content.ReadAsByteArrayAsync().Result
        $jsonStr = [System.Text.Encoding]::UTF8.GetString($rawBytes)
        $resp = $jsonStr | ConvertFrom-Json
        if ($resp.errorCode -ne 0) {
            throw "PaddleOCR API 报错：errorCode=$($resp.errorCode) errorMsg=$($resp.errorMsg)"
        }
        return $resp
    } finally {
        $client.Dispose()
    }
}

# ---------- 从 PaddleOCR 响应解析出 [{text, bbox}] 数组 ----------
function Parse-PaddleOcrBlocks {
    param($Resp)
    $result = @()
    $pages = $Resp.result.layoutParsingResults
    if (-not $pages) { return $result }
    foreach ($page in $pages) {
        $prl = $page.prunedResult.parsing_res_list
        if (-not $prl) { continue }
        foreach ($b in $prl) {
            $bb = $b.block_bbox
            if (-not $bb -or $bb.Count -lt 4) { continue }
            # 标准化 bbox（x1<=x2, y1<=y2）
            $x1 = [int]$bb[0]; $y1 = [int]$bb[1]; $x2 = [int]$bb[2]; $y2 = [int]$bb[3]
            if ($x1 -gt $x2) { $t = $x1; $x1 = $x2; $x2 = $t }
            if ($y1 -gt $y2) { $t = $y1; $y1 = $y2; $y2 = $t }
            $content = if ($b.block_content) { [string]$b.block_content } else { '' }
            $result += [pscustomobject]@{
                text = $content
                label = [string]$b.block_label
                bbox = @($x1, $y1, $x2, $y2)
            }
        }
    }
    return $result
}

# ---------- 给截图标注 bbox（红色矩形），输出到标注图 ----------
function Draw-Bbox {
    param(
        [string]$SrcPng,
        [int[]]$Bbox,          # [x1,y1,x2,y2]，相对图像左上角
        [string]$OutPng,
        [string]$Label = ''
    )
    Add-Type -AssemblyName System.Drawing
    $bmp = [System.Drawing.Bitmap]::FromFile($SrcPng)
    try {
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        try {
            # 红框线宽 10 + 半透明黄色填充，让 bbox 在 1936x2088 高分辨率截图上清晰可见
            $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::Red, 10)
            $rect = New-Object System.Drawing.Rectangle ($Bbox[0], $Bbox[1], ($Bbox[2] - $Bbox[0]), ($Bbox[3] - $Bbox[1]))
            $g.DrawRectangle($pen, $rect)
            # 半透明黄色填充让框区域更显眼
            $fillBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(80, 255, 255, 0))
            $g.FillRectangle($fillBrush, $rect)
            $fillBrush.Dispose()
            if ($Label) {
                $font = New-Object System.Drawing.Font ('Microsoft YaHei', 22, [System.Drawing.FontStyle]::Bold)
                $brush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::Red)
                # 标签位置：bbox 右上角，避免覆盖文字本身
                $labelX = $Bbox[2] + 8
                $labelY = [Math]::Max(0, $Bbox[1] - 5)
                $g.DrawString($Label, $font, $brush, $labelX, $labelY)
                $font.Dispose(); $brush.Dispose()
            }
            $pen.Dispose()
        } finally { $g.Dispose() }
        $bmp.Save($OutPng, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $bmp.Dispose() }
}

# ---------- 鲁棒解析模型返回的 bbox 列表（只挑 text+bbox，不挑语义字段） ----------
function Parse-Elements([string]$content) {
    if (-not $content) { return @() }
    $s = $content.Trim()
    # 剥 markdown fence
    if ($s -match '```(?:json)?\s*([\s\S]*?)\s*```') { $s = $Matches[1].Trim() }
    $elements = @()
    try {
        $parsed = $s | ConvertFrom-Json
        if ($parsed -is [Array]) {
            foreach ($el in $parsed) { $elements += $el }
        } elseif ($parsed.elements) {
            foreach ($el in $parsed.elements) { $elements += $el }
        } elseif ($parsed.bbox) {
            $elements += $parsed
        }
    } catch {
        # fallback：正则抓所有 {...}
        $matches = [regex]::Matches($s, '\{[^{}]*\}')
        foreach ($m in $matches) {
            try { $elements += ($m.Value | ConvertFrom-Json) } catch {}
        }
    }
    # 过滤出有 bbox 的，标准化
    $result = @()
    foreach ($el in $elements) {
        $bb = $el.bbox
        if (-not $bb -or $bb.Count -lt 4) { continue }
        $x1 = [int][Math]::Round([double]$bb[0]); $y1 = [int][Math]::Round([double]$bb[1])
        $x2 = [int][Math]::Round([double]$bb[2]); $y2 = [int][Math]::Round([double]$bb[3])
        if ($x1 -gt $x2) { $t = $x1; $x1 = $x2; $x2 = $t }
        if ($y1 -gt $y2) { $t = $y1; $y1 = $y2; $y2 = $t }
        $text = if ($el.text) { [string]$el.text } elseif ($el.label) { [string]$el.label } else { '' }
        $role = if ($el.role) { [string]$el.role } else { '' }
        $result += [pscustomobject]@{
            text = $text
            role = $role
            bbox = @($x1, $y1, $x2, $y2)
        }
    }
    return $result
}

# ---------- Win32 点击/键盘（用 user32） ----------
# 必要性：脚本启动时默认非 DPI-aware，SetCursorPos 在虚拟坐标系工作（基于 96 DPI），
# 但企微窗口在物理坐标系（150% DPI），会导致点击位置错位。
# 必须在 Add-Type 之后立即设为 PER_MONITOR_AWARE_V2，让 SetCursorPos/GetCursorPos 用物理坐标。
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class DbgWin32 {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, IntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, IntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern short VkKeyScanW(char ch);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    public const uint MOUSEEVENTF_LEFTDOWN = 0x02;
    public const uint MOUSEEVENTF_LEFTUP = 0x04;
    public const uint KEYEVENTF_KEYDOWN = 0x00;
    public const uint KEYEVENTF_KEYUP = 0x02;
}
"@
try {
    [DbgWin32]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null
    Write-Step 'DPI awareness: PER_MONITOR_AWARE_V2'
} catch {
    try { [DbgWin32]::SetProcessDPIAware() | Out-Null; Write-Step 'DPI awareness: SYSTEM_AWARE' }
    catch { Write-Step "DPI awareness 设置失败: $($_.Exception.Message)" }
}

function Click-At([int]$x, [int]$y) {
    [DbgWin32]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 80
    [DbgWin32]::mouse_event([DbgWin32]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 60
    [DbgWin32]::mouse_event([DbgWin32]::MOUSEEVENTF_LEFTUP, 0, 0, 0, [IntPtr]::Zero)
}

function Press-CtrlA-Delete {
    [DbgWin32]::keybd_event(0x11, 0, [DbgWin32]::KEYEVENTF_KEYDOWN, [IntPtr]::Zero)  # Ctrl down
    [DbgWin32]::keybd_event(0x41, 0, [DbgWin32]::KEYEVENTF_KEYDOWN, [IntPtr]::Zero)  # A down
    Start-Sleep -Milliseconds 40
    [DbgWin32]::keybd_event(0x41, 0, [DbgWin32]::KEYEVENTF_KEYUP, [IntPtr]::Zero)
    [DbgWin32]::keybd_event(0x11, 0, [DbgWin32]::KEYEVENTF_KEYUP, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 40
    [DbgWin32]::keybd_event(0x2E, 0, [DbgWin32]::KEYEVENTF_KEYDOWN, [IntPtr]::Zero)  # Delete down
    Start-Sleep -Milliseconds 40
    [DbgWin32]::keybd_event(0x2E, 0, [DbgWin32]::KEYEVENTF_KEYUP, [IntPtr]::Zero)
}

function Type-Text([string]$text) {
    # 用输入法兼容的方式：用 SendInput 中文不行，这里用剪贴板 + Ctrl+V
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Clipboard]::SetText($text)
    Start-Sleep -Milliseconds 80
    [DbgWin32]::keybd_event(0x11, 0, [DbgWin32]::KEYEVENTF_KEYDOWN, [IntPtr]::Zero)  # Ctrl down
    [DbgWin32]::keybd_event(0x56, 0, [DbgWin32]::KEYEVENTF_KEYDOWN, [IntPtr]::Zero)  # V down
    Start-Sleep -Milliseconds 40
    [DbgWin32]::keybd_event(0x56, 0, [DbgWin32]::KEYEVENTF_KEYUP, [IntPtr]::Zero)
    [DbgWin32]::keybd_event(0x11, 0, [DbgWin32]::KEYEVENTF_KEYUP, [IntPtr]::Zero)
}

# ====================================================================================
# 各步骤实现
# ====================================================================================

# ---------- Step 1: 截图 ----------
function Step-Screenshot {
    Write-Step 'Step 1: 截图企微主窗口'
    $cap = Capture-WeCom
    Write-Step ("截图成功：{0}x{1} @ ({2},{3}) dpi={4} ver={5}" -f `
        $cap.width, $cap.height, $cap.left, $cap.top, $cap.dpi_scale, $cap.wecom_version)
    Write-Step "PNG: $($cap.png_path)"
    Save-State @{
        step = 'screenshot'
        png_path = $cap.png_path
        window_left = $cap.left
        window_top = $cap.top
        window_width = $cap.width
        window_height = $cap.height
        dpi_scale = $cap.dpi_scale
        wecom_version = $cap.wecom_version
        timestamp = (Get-Date).ToString('o')
    }
    Write-Json @{ ok = $true; step = 'screenshot'; png_path = $cap.png_path;
                  window_left = $cap.left; window_top = $cap.top;
                  window_width = $cap.width; window_height = $cap.height }
}

# ---------- Step 2: 定位搜索框（固定坐标方案 + PaddleOCR 参考验证） ----------
# 必要性：qwen3-vl-plus bbox 不稳定（同一张图返回不同结果），PaddleOCR layout-parsing
# 是文档解析（识别 text/figure/title），不识别 UI 输入框，搜索框 placeholder 灰色文字也识别不到。
# 但企微 5.0.8 的搜索框位置是固定的（顶部，左侧导航栏右边），用窗口相对固定坐标最稳。
# 仍然调 PaddleOCR 是为了在标注图上画出 OCR 识别到的顶部元素作为参考，验证固定坐标位置合理。
function Step-LocateSearch {
    $st = Load-State
    if (-not $st.png_path -or -not (Test-Path $st.png_path)) {
        throw '请先跑 -Step screenshot'
    }
    Write-Step 'Step 2: 定位搜索框（固定坐标 + PaddleOCR 参考验证）'

    # PaddleOCR 参考调用：dump 顶部 text 块作为参考
    $ocrResp = Call-PaddleOcr -ImagePath $st.png_path
    # 保存完整原始响应到文件，方便人工检查所有识别内容
    $rawOut = Join-Path $OutDir ('paddleocr_step2_' + (Get-Date -Format 'HHmmss') + '.json')
    $ocrResp | ConvertTo-Json -Depth 20 | Set-Content $rawOut -Encoding UTF8
    Write-Step "PaddleOCR 完整响应保存到：$rawOut"
    $blocks = Parse-PaddleOcrBlocks -Resp $ocrResp
    Write-Step ("PaddleOCR 识别 {0} 个 block" -f $blocks.Count)
    Write-Step '顶部 (y<60) 的 OCR block：'
    foreach ($b in $blocks) {
        if ($b.bbox[1] -lt 60) {
            $preview = if ($b.text.Length -gt 40) { $b.text.Substring(0, 40) + '...' } else { $b.text }
            Write-Step ("  label={0} text='{1}' bbox={2}" -f $b.label, $preview, ($b.bbox -join ','))
        }
    }

    # 固定坐标方案：企微 5.0.8 在 100% DPI (1284x1392) 下，搜索框中心约在 (253, 33)。
    # 历史校准记录（DPI=1.5 时是 380,50，按 1.5 倍反算得到 100% DPI 下的位置）：
    #   - DPI=1.5 (1936x2088): bbox [330,34,430,66], 中心 (380, 50) ✅ 已验证
    #   - DPI=1.0 (1284x1392): bbox [220,23,287,44], 中心 (253, 33) (按比例换算)
    # 不同 DPI 下窗口尺寸不同但 UI 相对位置不变，按窗口尺寸自动换算更稳：
    $scale = [double]$st.window_width / 1936.0  # 以 1936x2088 (DPI=1.5) 为基准
    $searchBbox = @(
        [int][Math]::Round(330 * $scale),
        [int][Math]::Round(34 * $scale),
        [int][Math]::Round(430 * $scale),
        [int][Math]::Round(66 * $scale)
    )
    Write-Step ("搜索框 bbox（按 scale={0} 换算）={1}（中心 {2},{3}）" -f $scale, ($searchBbox -join ','), [int](($searchBbox[0]+$searchBbox[2])/2), [int](($searchBbox[1]+$searchBbox[3])/2))

    # 画标注图：固定 bbox + 顶部 OCR block 全画出来作为参考
    $annotated = Join-Path $OutDir ('step2_annotated_' + (Get-Date -Format 'HHmmss') + '.png')
    Draw-Bbox -SrcPng $st.png_path -Bbox $searchBbox -OutPng $annotated -Label '搜索框(固定)'

    Write-Step "标注图：$annotated"

    $newSt = $st.Clone()
    $newSt.step = 'locate_search'
    $newSt.search_bbox = $searchBbox
    $newSt.search_text = '(fixed-coordinate)'
    $newSt.search_annotated = $annotated
    Save-State $newSt

    Write-Json @{ ok = $true; step = 'locate_search'; bbox = $searchBbox;
                  annotated = $annotated; ocr_blocks_count = $blocks.Count }
}

# ---------- Step 3: 点击搜索框 ----------
function Step-ClickSearch {
    $st = Load-State
    if (-not $st.search_bbox) { throw '请先跑 -Step locate_search' }
    $bbox = $st.search_bbox
    # 实时取窗口 origin（窗口可能被移动过，Step 1 时的 origin 已过期）
    $origin = Get-WeWorkWindowOrigin
    if (-not $origin) { throw '找不到 WeWorkWindow，企微是否启动？' }
    # bbox 是相对图像左上角的，加窗口 origin 变成屏幕绝对坐标
    $cx = [int][Math]::Round(($bbox[0] + $bbox[2]) / 2.0) + $origin.Left
    $cy = [int][Math]::Round(($bbox[1] + $bbox[3]) / 2.0) + $origin.Top
    Write-Step ("Step 3: 点击搜索框 bbox={0} window_origin=({1},{2}) -> 屏幕坐标 ({3},{4})" -f ($bbox -join ','), $origin.Left, $origin.Top, $cx, $cy)
    Click-At -x $cx -y $cy
    Start-Sleep -Milliseconds 500
    $fg = [DbgWin32]::GetForegroundWindow()
    Write-Step "点击完成，当前前台窗口 hwnd=$($fg.ToInt64().ToString('X'))"
    $newSt = $st.Clone()
    $newSt.step = 'click_search'
    Save-State $newSt
    Write-Json @{ ok = $true; step = 'click_search'; clicked_x = $cx; clicked_y = $cy }
}

# 取企微主窗口左上角物理屏幕坐标（实时，DPI-aware 后的值）
function Get-WeWorkWindowOrigin {
    # 用 DbgWin32 里已有的 GetForegroundWindow 即可，但需要 EnumWindows 找窗口。
    # 加 try/catch 因为 Add-Type 在第二次调用时会报"类型已存在"。
    try {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class DbgFind {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetClassName(IntPtr hWnd, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rc);
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
    } catch { }

    $script:target = [IntPtr]::Zero
    $script:targetArea = 0
    $script:allWeWork = New-Object System.Collections.ArrayList
    [DbgFind]::EnumWindows({
        param($h, $l)
        $sb = New-Object System.Text.StringBuilder 256
        [DbgFind]::GetClassName($h, $sb, 256) | Out-Null
        if ($sb.ToString() -eq 'WeWorkWindow' -and [DbgFind]::IsWindowVisible($h)) {
            $r = New-Object DbgFind+RECT
            [void][DbgFind]::GetWindowRect($h, [ref]$r)
            $w = $r.Right - $r.Left; $hgt = $r.Bottom - $r.Top
            $area = $w * $hgt
            $script:allWeWork.Add("hwnd=0x$($h.ToInt64().ToString('X')) ${w}x${hgt} L=$($r.Left) T=$($r.Top)") | Out-Null
            if ($w -ge 600 -and $hgt -ge 400 -and $area -gt $script:targetArea) {
                $script:target = $h; $script:targetArea = $area
            }
        }
        return $true
    }, [IntPtr]::Zero) | Out-Null

    Write-Step ("Get-WeWorkWindowOrigin: found {0} WeWorkWindow windows" -f $script:allWeWork.Count)
    foreach ($w in $script:allWeWork) { Write-Step "  $w" }

    if ($script:target -eq [IntPtr]::Zero) { return $null }
    $r = New-Object DbgFind+RECT
    [void][DbgFind]::GetWindowRect($script:target, [ref]$r)
    return @{ Left = $r.Left; Top = $r.Top; Width = $r.Right - $r.Left; Height = $r.Bottom - $r.Top }
}

# ---------- Step 4: 清空 + 输入关键词 ----------
function Step-TypeKeyword {
    $st = Load-State
    Write-Step "Step 4: 清空搜索框 + 输入 '$Keyword'"
    Press-CtrlA-Delete
    Start-Sleep -Milliseconds 200
    Type-Text -text $Keyword
    Start-Sleep -Milliseconds 1000  # 给企微搜索结果渲染
    $newSt = $st.Clone()
    $newSt.step = 'type_keyword'
    Save-State $newSt
    Write-Json @{ ok = $true; step = 'type_keyword'; keyword = $Keyword }
}

# ---------- Step 5: 按回车进入第一个搜索结果（不需要视觉定位） ----------
# 必要性：企微搜索下拉第一个结果默认高亮，搜索框按 Enter 会直接进入第一个高亮项（不需要点击）。
# 这跳过了整个视觉定位（PaddleOCR 漏识 + qwen3-vl-plus bbox 偏移不稳定两个问题），大幅简化。
# 风险：如果企微搜索结果顺序变了（如把"聊天记录"放前面），Enter 进的不是"联系人"的第一个。
# 用户实测当前企微版本 5.0.8 Enter 直接进入正确的联系人会话。
function Step-LocateResult {
    $st = Load-State
    Write-Step "Step 5: 按回车进入第一个搜索结果（跳过视觉定位）"

    # 按下 Enter 键
    # VK_RETURN = 0x0D, KEYEVENTF_KEYDOWN = 0, KEYEVENTF_KEYUP = 2
    [DbgWin32]::keybd_event(0x0D, 0, 0, [IntPtr]::Zero)    # Enter down
    Start-Sleep -Milliseconds 50
    [DbgWin32]::keybd_event(0x0D, 0, 2, [IntPtr]::Zero)    # Enter up
    Start-Sleep -Milliseconds 2500  # 给企微切换会话视图 + 渲染输入框时间

    $fg = [DbgWin32]::GetForegroundWindow()
    Write-Step "Enter 已发送，当前前台窗口 hwnd=$($fg.ToInt64().ToString('X'))"

    $newSt = $st.Clone()
    $newSt.step = 'locate_result'
    $newSt.method = 'enter_key'
    Save-State $newSt
    Write-Json @{ ok = $true; step = 'locate_result'; method = 'enter_key' }
}

# ---------- Step 6: 发送文本消息（"你好啊！"）+ Enter ----------
# 必要性：Step 5 Enter 进入会话后，光标默认在消息输入框里。直接 Type-Text + Enter 发送。
function Step-SendText {
    $st = Load-State
    $msg = $global:Kw['send_text']
    Write-Step ("Step 6: 输入消息 '{0}' + Enter 发送" -f $msg)
    Type-Text -text $msg
    Start-Sleep -Milliseconds 300
    # 按 Enter 发送（VK_RETURN = 0x0D）
    [DbgWin32]::keybd_event(0x0D, 0, 0, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 50
    [DbgWin32]::keybd_event(0x0D, 0, 2, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 500
    $newSt = $st.Clone()
    $newSt.step = 'send_text'
    $newSt.sent_text = $msg
    Save-State $newSt
    Write-Json @{ ok = $true; step = 'send_text'; sent_text = $msg }
}

# ---------- Step 7: 截屏 → 粘贴 → Enter 发送图片 ----------
# 必要性：企微聊天会话里 Ctrl+V 粘贴图片会进入图片预览模式，再按 Enter 发送图片。
# 截屏用 PrintWindow 截当前活动窗口（避免截到整个屏幕的杂乱内容）。
function Step-SendScreenshot {
    $st = Load-State
    Write-Step 'Step 7: 截当前企微窗口到剪贴板 + Ctrl+V + Enter'

    # 截当前前台窗口（不是整屏）— 用 SendInput 模拟 Win+Shift+S 太复杂，直接截 WeWorkWindow 客户区
    Add-Type -AssemblyName System.Drawing
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class DbgCapture {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetClassName(IntPtr hWnd, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rc);
    [DllImport("user32.dll")] public static extern bool GetForegroundWindow();
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
    # 找 WeWorkWindow（同 Get-WeWorkWindowOrigin 的逻辑）
    $script:tgt = [IntPtr]::Zero; $script:tgtArea = 0
    [DbgCapture]::EnumWindows({
        param($h, $l)
        $sb = New-Object System.Text.StringBuilder 256
        [DbgCapture]::GetClassName($h, $sb, 256) | Out-Null
        if ($sb.ToString() -eq 'WeWorkWindow' -and [DbgCapture]::IsWindowVisible($h)) {
            $r = New-Object DbgCapture+RECT
            [void][DbgCapture]::GetWindowRect($h, [ref]$r)
            $w = $r.Right - $r.Left; $hgt = $r.Bottom - $r.Top; $area = $w * $hgt
            if ($w -ge 600 -and $hgt -ge 400 -and $area -gt $script:tgtArea) {
                $script:tgt = $h; $script:tgtArea = $area
            }
        }
        return $true
    }, [IntPtr]::Zero) | Out-Null
    if ($script:tgt -eq [IntPtr]::Zero) { throw 'Step 7: 找不到 WeWorkWindow' }
    $r = New-Object DbgCapture+RECT
    [void][DbgCapture]::GetWindowRect($script:tgt, [ref]$r)
    $w = $r.Right - $r.Left; $hgt = $r.Bottom - $r.Top

    # 截图到 Bitmap
    $bmp = New-Object System.Drawing.Bitmap $w, $hgt
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($r.Left, $r.Top, 0, 0, (New-Object System.Drawing.Size $w, $hgt))
    $g.Dispose()

    # 放到剪贴板
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Clipboard]::SetImage($bmp)
    Write-Step ("截图已放剪贴板 ({0}x{1})" -f $w, $hgt)
    $bmp.Dispose()
    Start-Sleep -Milliseconds 300

    # Ctrl+V 粘贴（企微会弹出图片预览对话框）
    [DbgWin32]::keybd_event(0x11, 0, 0, [IntPtr]::Zero)  # Ctrl down
    [DbgWin32]::keybd_event(0x56, 0, 0, [IntPtr]::Zero)  # V down
    Start-Sleep -Milliseconds 50
    [DbgWin32]::keybd_event(0x56, 0, 2, [IntPtr]::Zero)  # V up
    [DbgWin32]::keybd_event(0x11, 0, 2, [IntPtr]::Zero)  # Ctrl up
    Start-Sleep -Milliseconds 1500  # 等企微图片预览对话框弹出

    # Enter 发送图片
    [DbgWin32]::keybd_event(0x0D, 0, 0, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 50
    [DbgWin32]::keybd_event(0x0D, 0, 2, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 800

    $newSt = $st.Clone()
    $newSt.step = 'send_screenshot'
    Save-State $newSt
    Write-Json @{ ok = $true; step = 'send_screenshot'; captured_size = "$w x $hgt" }
}

# ====================================================================================
# 主入口
# ====================================================================================
switch ($Step) {
    'screenshot'      { Step-Screenshot }
    'locate_search'   { Step-LocateSearch }
    'click_search'    { Step-ClickSearch }
    'type_keyword'    { Step-TypeKeyword }
    'locate_result'   { Step-LocateResult }
    'click_result'    { Write-Step 'click_result 已废弃（Enter 方案不需要点击）' }
    'send_text'       { Step-SendText }
    'send_screenshot' { Step-SendScreenshot }
}
