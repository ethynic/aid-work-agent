# Run Client.VisionRegression (Task E, Stage 3B)
# Purpose: real-machine visual regression — C# QwenVisionLocator vs Python baseline.
# Output: vision-regression-out/ - report_<timestamp>.yaml + annotated_<timestamp>.png
#
# 两阶段执行（阶段 3B.2 改造）：
#   阶段 1：调 capture-wecom-for-csharp.ps1 截图（独立 PS 进程，前台权限正常）
#   阶段 2：调 Client.VisionRegression.exe --image <PNG> --left --top --dpi --version
# 原因：Client.VisionRegression 是 console 子进程，调 PS 子进程时 PS 也拿不到前台权限，
#       会截到被遮挡的内容（白色 82.5%）；改用 PS 先截图（前台 OK）→ C# 加载 PNG 跑下游。
#       生产环境 Client.App 是常驻 GUI 进程，前台权限天然 OK，用真实 ScreenCapturer 即可。

param(
    [string[]]$RegressionArgs = @()
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$projDir = Join-Path $rootDir 'src\Client.VisionRegression'
$captureScript = Join-Path $scriptDir 'capture-wecom-for-csharp.ps1'

Write-Host '================================================================' -ForegroundColor Cyan
Write-Host ' Vision Regression Runner (Task E, Stage 3B.2 - 两阶段)' -ForegroundColor Cyan
Write-Host '================================================================' -ForegroundColor Cyan
Write-Host "Project dir: $projDir"
Write-Host ''

# 前置检查：.env 存在
$envPath = 'c:\repos\aid-work-agent\.env'
if (-not (Test-Path $envPath)) {
    Write-Host "[WARN] .env not found at $envPath" -ForegroundColor Yellow
    Write-Host "       场景 1/2 会被 blocked，场景 5 仍可运行（验证失败降级）" -ForegroundColor Yellow
} else {
    $envContent = Get-Content $envPath -Raw
    if ($envContent -notmatch 'QWEN_API_KEYS\s*=') {
        Write-Host "[WARN] QWEN_API_KEYS not found in .env" -ForegroundColor Yellow
    } else {
        Write-Host "[OK] .env contains QWEN_API_KEYS" -ForegroundColor Green
    }
}
Write-Host ''

# 1. Build
Write-Host '[1/3] Building Client.VisionRegression ...' -ForegroundColor Yellow
& dotnet build $projDir -c Debug --nologo
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] Build failed (exit=$LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host '[OK] Build succeeded' -ForegroundColor Green
Write-Host ''

# 2. 阶段 1：PS 截图（独立进程，前台权限正常）
Write-Host '[2/3] Stage 1: PowerShell 截图（前台权限正常）...' -ForegroundColor Yellow
$captureOutDir = Join-Path $rootDir 'vision-regression-out\captures'
New-Item -ItemType Directory -Force -Path $captureOutDir | Out-Null

if (-not (Test-Path $captureScript)) {
    Write-Host "[FAIL] 截图脚本不存在：$captureScript" -ForegroundColor Red
    exit 1
}

# 单次调用：同时拿 stdout 和退出码
$outLines = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $captureScript -OutDir $captureOutDir 2>&1
$captureExit = $LASTEXITCODE

# 从输出中找 JSON 行（其他行是 stderr 日志或错误）
$jsonLine = ($outLines | Where-Object { "$_" -match '^\s*\{' } | Select-Object -Last 1)
if (-not $jsonLine) {
    Write-Host "[FAIL] PS 截图未输出 JSON 行（exit=$captureExit）。输出内容：" -ForegroundColor Red
    $outLines | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
    exit 2
}

try {
    $cap = $jsonLine | ConvertFrom-Json
} catch {
    Write-Host "[FAIL] JSON 解析失败：$($_.Exception.Message) raw=$jsonLine" -ForegroundColor Red
    exit 3
}

if (-not $cap.ok) {
    Write-Host "[FAIL] PS 截图返回 ok=false error=$($cap.error)（exit=$captureExit）" -ForegroundColor Red
    Write-Host "       可能原因：企微未在前台或被遮挡" -ForegroundColor Yellow
    exit 4
}

$pngPath = $cap.png_path
$left = [int]$cap.left
$top = [int]$cap.top
$dpiScale = [double]$cap.dpi_scale
$wecomVersion = if ($cap.wecom_version) { [string]$cap.wecom_version } else { 'unknown' }
$whitePct = [double]$cap.white_ratio
$colorDiv = [int]$cap.color_diversity

Write-Host "[OK] PS 截图成功" -ForegroundColor Green
Write-Host "  PNG:        $pngPath"
Write-Host "  窗口坐标:   left=$left top=$top"
Write-Host "  DPI 缩放:   $dpiScale"
Write-Host "  企微版本:   $wecomVersion"
$whitePctStr = '{0:P1}' -f $whitePct
Write-Host "  白色占比:   $whitePctStr"
Write-Host "  颜色多样性: $colorDiv"
Write-Host ''

# 自检：截图质量。白色 > 40% 或颜色多样性 < 50 说明截图异常（可能仍被遮挡）。
if ($whitePct -gt 0.40 -or $colorDiv -lt 50) {
    Write-Host "[FAIL] 截图像素自检失败：白色=$whitePctStr 颜色多样性=$colorDiv" -ForegroundColor Red
    Write-Host "       可能原因：企微被其他窗口遮挡、或 PS 脚本未正确前台化企微" -ForegroundColor Yellow
    exit 5
}

# 3. 阶段 2：C# 跑下游视觉定位
Write-Host '[3/3] Stage 2: C# QwenVisionLocator 用外部 PNG 跑场景 1/2/3 ...' -ForegroundColor Yellow
$allArgs = @('run', '--project', $projDir, '-c', 'Debug', '--no-build', '--',
             '--image', $pngPath,
             '--left', "$left",
             '--top', "$top",
             '--dpi', "$dpiScale",
             '--version', $wecomVersion)
$allArgs += $RegressionArgs
& dotnet @allArgs
$exit = $LASTEXITCODE

Write-Host ''
if ($exit -eq 0) {
    Write-Host '[OK] Regression finished. See vision-regression-out/ directory.' -ForegroundColor Green
    Write-Host '     场景 1/2/3 由 C# 跑（真实视觉定位+缓存）；场景 4/5 标记为 skipped' -ForegroundColor Gray
} else {
    Write-Host "[FAIL] Regression failed (exit=$exit)" -ForegroundColor Red
}
exit $exit
