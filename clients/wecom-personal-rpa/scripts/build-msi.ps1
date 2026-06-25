#requires -Version 5.1
<#
.SYNOPSIS
  构建 WeComRpa MSI 安装包（WiX v5）。
  前置：先执行 scripts\publish.ps1 生成 publish\app\ 与 publish\supervisor\。
.EXAMPLE
  powershell scripts\build-msi.ps1
  powershell scripts\build-msi.ps1 -Version 1.2.3
  powershell scripts\build-msi.ps1 -Version 1.2.3 -SignPfx "C:\cert\codesign.pfx" -SignPfxPassword secret
  powershell scripts\build-msi.ps1 -UseMsbuild
#>
[CmdletBinding()]
param(
    [string]$Version = "1.0.0",
    [switch]$UseMsbuild,
    [string]$SignPfx,
    [string]$SignPfxPassword,
    [string]$SignCertThumbprint,
    [string]$SignTimestampUrl = "http://timestamp.digicert.com"
)
$ErrorActionPreference = 'Stop'

$here = Split-Path $PSScriptRoot -Parent
$wxs = Join-Path $here 'installer\wix\WeComRpa.wxs'
$wixproj = Join-Path $here 'installer\wix\WeComRpa.wixproj'
$publishDir = Join-Path $here 'publish'
$appExe = Join-Path $publishDir 'app\Client.App.exe'
$supExe = Join-Path $publishDir 'supervisor\Client.Supervisor.exe'
$outMsi = Join-Path $publishDir "WeComRpa-$Version.msi"

function Write-Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg){ Write-Host "[!] $msg" -ForegroundColor Yellow }
function Die([string]$msg)        { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }

# ---------- 前置检查 ----------
Write-Step "检查 publish 产物"
if (-not (Test-Path $appExe)) { Die "缺少 $appExe。请先执行 scripts\publish.ps1 生成发布产物。" }
if (-not (Test-Path $supExe)) { Die "缺少 $supExe。请先执行 scripts\publish.ps1 生成发布产物。" }
Write-Ok "发布产物就绪：$publishDir"

# ---------- wix CLI 可用性 ----------
function Test-WixCli {
    try { $null = & wix --version 2>$null; return $true } catch { return $false }
}

$wixAvailable = Test-WixCli

if ($UseMsbuild) {
    Write-Step "使用 msbuild 构建（SDK 风格 wixproj）"
    $msbuild = (Get-Command msbuild -ErrorAction SilentlyContinue)
    if (-not $msbuild) { Die "找不到 msbuild。请装 Visual Studio Build Tools 或 WiX v5 VS 扩展。" }
    Write-Ok "msbuild: $($msbuild.Source)"
    & msbuild $wixproj /p:Configuration=Release /p:WixMsiVersion=$Version /restore
    if ($LASTEXITCODE -ne 0) { Die "msbuild 失败 (exit $LASTEXITCODE)" }
}
elseif ($wixAvailable) {
    $wixVer = & wix --version
    Write-Ok "wix CLI 可用: $wixVer"
    Write-Step "wix build -> $outMsi"
    & wix build $wxs -arch x64 -d "WixMsiVersion=$Version" -d "PublishRoot=$publishDir" -ext WixToolset.UI.wixext -o $outMsi
    if ($LASTEXITCODE -ne 0) { Die "wix build 失败 (exit $LASTEXITCODE)" }
}
else {
    Write-Warn2 "本机未安装 wix CLI。"
    Write-Host "    安装方式：dotnet tool install -g wix"
    Write-Host "    或装 Visual Studio WiX Toolset v5 扩展后改用 -UseMsbuild 参数。"
    Die "无可用构建工具，无法生成 MSI。"
}

if (-not (Test-Path $outMsi)) { Die "未生成预期产物：$outMsi" }
$sizeMb = [math]::Round((Get-Item $outMsi).Length / 1MB, 1)
Write-Ok "MSI 已生成：$outMsi ($sizeMb MB)"

# ---------- 代码签名（可选） ----------
$signRequested = (-not [string]::IsNullOrEmpty($SignPfx)) -or (-not [string]::IsNullOrEmpty($SignCertThumbprint))
if ($signRequested) {
    Write-Step "代码签名 MSI"
    $signtoolCandidates = @((Get-Command signtool.exe -ErrorAction SilentlyContinue).Source)
    $progX86 = ${env:ProgramFiles(x86)}
    if ($progX86) {
        $sdkBase = Join-Path $progX86 'Windows Kits\10\bin'
        if (Test-Path $sdkBase) {
            $signtoolCandidates += (Get-ChildItem (Join-Path $sdkBase '*\x64\signtool.exe') -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1).FullName
        }
    }
    $signtool = $signtoolCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $signtool) { Die "找不到 signtool.exe。请装 Windows SDK。" }
    Write-Ok "signtool: $signtool"

    $signArgs = @('sign', '/tr', $SignTimestampUrl, '/td', 'sha256', '/fd', 'sha256')
    if ($SignPfx) {
        if (-not (Test-Path $SignPfx)) { Die "PFX 文件不存在：$SignPfx" }
        $signArgs += @('/f', $SignPfx)
        if ($SignPfxPassword) { $signArgs += @('/p', $SignPfxPassword) }
    } elseif ($SignCertThumbprint) {
        $signArgs += @('/sha1', $SignCertThumbprint)
    } else {
        Die "签名参数不完整：需要 -SignPfx(+密码) 或 -SignCertThumbprint。"
    }
    $signArgs += $outMsi
    & $signtool @signArgs
    if ($LASTEXITCODE -ne 0) { Die "signtool 失败 (exit $LASTEXITCODE)" }
    Write-Ok "MSI 签名完成"
} else {
    Write-Warn2 "未指定签名参数，跳过签名。生产部署建议签名 MSI 与 exe（见 installer\wix\README.md）。"
}

Write-Host ""
Write-Ok "构建完成。"
Write-Host "    产物：$outMsi"
Write-Host "    安装：msiexec /i \"$outMsi\"  （或双击）"
Write-Host "    卸载：msiexec /x \"$outMsi\""
Write-Host "    静默安装：msiexec /i \"$outMsi\" /qn /norestart"
