#Requires -Version 5.1
<#
.SYNOPSIS
  方案 E（红点检测 + 多模态 VL）验证脚本：用 qwen3-vl-plus 视觉模型识别企微会话列表
  中有未读红点的用户名。

  背景：
    - 方案 A（Toast 窗口监听）已实测不可行：企微 Electron 不创建独立顶层 Toast 窗口。
    - 方案 D（SQLite 缓存文件监控）已实测不可行：企微在本地没有任何标准 SQLite/LevelDB
      文件，聊天数据走加密私有格式，读取需要逆向 + 解密，升级即废。
    - 剩下唯一可行路径是"屏幕检测"，但放弃像素级红点匹配（颜色阈值脆弱），
      改用 VL 大模型直接看截图输出"哪些会话有未读 + 用户名"。

  本脚本验证 3 个问题：
    1. qwen3-vl-plus 能否稳定识别企微会话列表中的红色未读角标
    2. 用户名识别准确率（中文、英文、混合）
    3. 同一张图多次调用结果是否稳定（bbox 不稳是 qwen3-vl-plus 的已知问题，
       但本任务只要名字不要 bbox）

  用法：
    powershell -ExecutionPolicy Bypass -File scripts\verify-unread-vl.ps1
    powershell -ExecutionPolicy Bypass -File scripts\verify-unread-vl.ps1 -Runs 3        # 同一张图调 3 次看稳定性
    powershell -ExecutionPolicy Bypass -File scripts\verify-unread-vl.ps1 -SkipCapture   # 复用上次截图

  产物（保存到 clients/wecom-personal-rpa/debug-out/vl-unread_<timestamp>/）：
    - screenshot.png          原始企微截图
    - run_1_response.json     VL 原始响应
    - run_2_response.json     （-Runs N 时）
    - run_3_response.json
    - summary.json            解析后的结构化结果 + 一致性报告
    - summary.txt             人眼可读的报告

  关联设计：
    docs/system/wecom-personal-rpa-client-design.md §F5 Fallback 监听方案
    （fallback 实现方案待整体功能完成后开发，本脚本作为方案验证）
#>

param(
    [int]$Runs = 1,
    [switch]$SkipCapture,
    [string]$Model = 'qwen3-vl-plus',
    [string]$OutDir = ''
)

# 强制 UTF-8 输出
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---------- 路径基础 ----------
if (-not $OutDir) {
    $OutDir = Join-Path $ScriptDir '..\debug-out'
}
$OutDir = (New-Object -TypeName System.IO.DirectoryInfo -ArgumentList $OutDir).FullName
$sessionDir = Join-Path $OutDir ('vl-unread_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null
Write-Host "[verify-unread-vl] 输出目录：$sessionDir"

# ---------- 读 API key ----------
function Resolve-ApiKey {
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

# ---------- 截企微主窗口（复用 debug-navigate.ps1 的截图能力） ----------
function Capture-WeCom {
    $captureScript = Join-Path $ScriptDir 'capture-wecom-for-csharp.ps1'
    if (-not (Test-Path $captureScript)) {
        throw "找不到 $captureScript（该脚本用于截企微主窗口）"
    }
    $tmpOut = Join-Path $sessionDir 'cap'
    New-Item -ItemType Directory -Force -Path $tmpOut | Out-Null
    $stdoutFile = Join-Path $tmpOut 'stdout.log'
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try {
        $cmdLine = 'powershell -ExecutionPolicy Bypass -NoProfile -File "' + $captureScript + '" -OutDir "' + $tmpOut + '"'
        cmd /c "$cmdLine > `"$stdoutFile`" 2>&1" | Out-Null
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    $capOut = Get-Content $stdoutFile -Encoding UTF8
    $lastJson = ($capOut | Where-Object { $_ -match '^\{' }) | Select-Object -Last 1
    if (-not $lastJson) { throw "capture 脚本无 JSON 输出，stdout 见：$stdoutFile" }
    $cap = $lastJson | ConvertFrom-Json
    if (-not $cap.ok) { throw "截图失败：$($cap.error)" }
    return @{
        png_path = $cap.png_path
        width    = [int]$cap.width
        height   = [int]$cap.height
    }
}

# ---------- 调 qwen3-vl-plus ----------
function Call-QwenVl {
    param(
        [string]$ApiKey,
        [string]$PromptText,
        [string]$ImagePath
    )
    $endpoint = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
    $bytes = [System.IO.File]::ReadAllBytes($ImagePath)
    $b64 = [Convert]::ToBase64String($bytes)
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
        $jsonStr = [System.Text.Encoding]::UTF8.GetString($rawBytes)
        return $jsonStr | ConvertFrom-Json
    }
    finally {
        $client.Dispose()
    }
}

# ---------- 加载 prompt 文本 ----------
$promptFile = Join-Path $ScriptDir 'prompts\verify_unread_vl.txt'
if (-not (Test-Path $promptFile)) { throw "找不到 prompt 文件：$promptFile" }
$promptText = (Get-Content $promptFile -Encoding UTF8 -Raw).Trim()
Write-Host "[verify-unread-vl] prompt 加载完成（$($promptText.Length) 字符）"

# ---------- 步骤 1：截图 ----------
$screenshotPath = Join-Path $sessionDir 'screenshot.png'
if ($SkipCapture -and (Test-Path $screenshotPath)) {
    Write-Host "[verify-unread-vl] -SkipCapture 模式：复用已有截图 $screenshotPath"
} else {
    Write-Host "[verify-unread-vl] Step 1: 截企微主窗口..."
    $cap = Capture-WeCom
    Copy-Item -Path $cap.png_path -Destination $screenshotPath -Force
    Write-Host "[verify-unread-vl] 截图保存到：$screenshotPath"
}

# ---------- 读 API key ----------
$apiKey = Resolve-ApiKey
Write-Host "[verify-unread-vl] API key 已解析"

# ---------- 步骤 2：调 VL N 次 ----------
$allResults = @()
for ($i = 1; $i -le $Runs; $i++) {
    Write-Host "[verify-unread-vl] Step 2: 调用 $Model 第 $i 次..."
    $resp = Call-QwenVl -ApiKey $apiKey -PromptText $promptText -ImagePath $screenshotPath

    # 保存原始响应
    $rawPath = Join-Path $sessionDir ("run_{0}_response.json" -f $i)
    $resp | ConvertTo-Json -Depth 20 | Set-Content $rawPath -Encoding UTF8
    Write-Host "[verify-unread-vl]   原始响应保存到：$rawPath"

    # 提取 content
    $content = ''
    try {
        $content = $resp.choices[0].message.content
    } catch {
        Write-Host "[verify-unread-vl]   [警告] 无法解析 content：$($_.Exception.Message)" -ForegroundColor Yellow
    }

    $allResults += @{
        run       = $i
        content   = $content
        raw_path  = $rawPath
    }
}

# ---------- 步骤 3：解析 + 一致性检查 ----------
$parsedSets = @()
foreach ($r in $allResults) {
    # 尝试解析 JSON 数组
    $users = $null
    try {
        $trimmed = $r.content.Trim()
        # 兼容 markdown ```json 包裹
        if ($trimmed -match '^```(?:json)?\s*(.+?)\s*```$') { $trimmed = $Matches[1] }
        $parsed = $trimmed | ConvertFrom-Json
        if ($parsed -is [Array]) {
            $users = $parsed | ForEach-Object {
                if ($_ -is [string]) { $_ }
                elseif ($_.name) { $_.name }
                elseif ($_.user) { $_.user }
                else { $_.ToString() }
            }
        } else {
            $users = @($parsed)
        }
    } catch {
        # 解析失败，按行分割作为兜底
        $users = $r.content -split "[\r\n,]" | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() }
    }
    $parsedSets += ,@($users)
    Write-Host "[verify-unread-vl]   Run $($r.run) 识别 $($users.Count) 个未读："
    foreach ($u in $users) { Write-Host "     - $u" }
}

# 一致性：多次调用结果是否相同
$consistency = $null
if ($allResults.Count -gt 1) {
    $firstSet = $parsedSets[0] | Sort-Object
    $allMatch = $true
    foreach ($s in $parsedSets[1..($parsedSets.Count - 1)]) {
        $sortedS = $s | Sort-Object
        if (($firstSet -join '|') -ne ($sortedS -join '|')) { $allMatch = $false; break }
    }
    $consistency = if ($allMatch) { 'STABLE' } else { 'UNSTABLE' }
}

# ---------- 输出 summary ----------
$summary = @{
    timestamp     = (Get-Date).ToString('o')
    model         = $Model
    runs          = $Runs
    screenshot    = $screenshotPath
    parsed_sets   = $parsedSets
    consistency   = $consistency
}
$summaryPath = Join-Path $sessionDir 'summary.json'
$summary | ConvertTo-Json -Depth 10 | Set-Content $summaryPath -Encoding UTF8

$summaryTxt = New-Object System.Text.StringBuilder
[void]$summaryTxt.AppendLine("VL 未读会话检测验证报告")
[void]$summaryTxt.AppendLine("时间：$($summary.timestamp)")
[void]$summaryTxt.AppendLine("模型：$Model")
[void]$summaryTxt.AppendLine("调用次数：$Runs")
[void]$summaryTxt.AppendLine("截图：$screenshotPath")
[void]$summaryTxt.AppendLine("")
for ($i = 0; $i -lt $parsedSets.Count; $i++) {
    [void]$summaryTxt.AppendLine("Run $($i + 1) 识别 $($parsedSets[$i].Count) 个未读：")
    foreach ($u in $parsedSets[$i]) { [void]$summaryTxt.AppendLine("  - $u") }
    [void]$summaryTxt.AppendLine("")
}
if ($consistency) {
    [void]$summaryTxt.AppendLine("一致性：$consistency")
}
$summaryTxtPath = Join-Path $sessionDir 'summary.txt'
$summaryTxt.ToString() | Set-Content $summaryTxtPath -Encoding UTF8

Write-Host ""
Write-Host "=========================================="
Write-Host " 验证完成"
Write-Host "=========================================="
Write-Host "输出目录：$sessionDir"
Write-Host "Summary：$summaryTxtPath"
if ($consistency) { Write-Host "一致性：$consistency" }
