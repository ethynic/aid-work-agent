#requires -Version 5.1
# 高于 5.1 时使用 $PSNativeCommandUseErrorActionPreference 等特性会更稳，但本脚本兼容 5.1
<#
.SYNOPSIS
  一键完成 RPA 客户端「编译发布 → 打 MSI → 卸载旧版 → 装新版 → 验证」全流程。

.DESCRIPTION
  封装了 publish.ps1 + build-msi.ps1 + msiexec 卸载/安装 + 6 项安装验证。
  适用于内部人员在 clients/wecom-personal-rpa/ 目录下一键重建并重装客户端。

  详见 docs/build-install-guide.md。

.PARAMETER Version
  MSI 版本号，默认 1.0.0。升级时改为更高版本（如 1.1.0）。

.PARAMETER SkipInstall
  只构建 + 打 MSI，不卸载旧版也不安装新版。适用于纯出包场景。

.PARAMETER SkipPublish
  跳过 publish.ps1（假设 publish\ 已有产物）。适用于 MSI 重新打包但不想重 publish。

.PARAMETER ForceUninstall
  即使没检测到旧版也强制尝试卸载。适用于怀疑旧版残留但 ProductCode 查不到的场景。

.EXAMPLE
  # 完整流程：构建 + 卸旧 + 装新 + 验证
  powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1

  # 只出 MSI 不安装
  powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1 -SkipInstall

  # 升级到 1.2.3
  powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1 -Version 1.2.3
#>
[CmdletBinding()]
param(
    [string]$Version = "1.0.0",
    [switch]$SkipInstall,
    [switch]$SkipPublish,
    [switch]$ForceUninstall
)
$ErrorActionPreference = 'Stop'

$here = Split-Path $PSScriptRoot -Parent
$publishScript = Join-Path $here 'scripts\publish.ps1'
$buildMsiScript = Join-Path $here 'scripts\build-msi.ps1'
$installDir = Join-Path $env:ProgramFiles 'WeComRpa'
$x86InstallDir = Join-Path ${env:ProgramFiles(x86)} 'WeComRpa'
$msiPath = Join-Path $here "publish\WeComRpa-$Version.msi"

function Write-Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg){ Write-Host "[!]    $msg" -ForegroundColor Yellow }
function Write-Err([string]$msg)  { Write-Host "[FAIL] $msg" -ForegroundColor Red }
function Die([string]$msg)        { Write-Err $msg; exit 1 }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# 找已安装的 RPA 客户端 ProductCode（同时查 64 位和 32 位注册表）
function Find-InstalledProductCode {
    $paths = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($p in $paths) {
        try {
            $found = Get-ItemProperty $p -ErrorAction SilentlyContinue |
                Where-Object { $_.DisplayName -like "*企业微信*RPA*" -or $_.DisplayName -like "*WeComRpa*" } |
                Select-Object -First 1
            if ($found) {
                return [PSCustomObject]@{
                    ProductCode = $found.PSChildName
                    DisplayName = $found.DisplayName
                    Version     = $found.DisplayVersion
                    Location    = $found.InstallLocation
                }
            }
        } catch { }
    }
    return $null
}

# ==================== Step 0: 前置检查 ====================
Write-Step "Step 0/5: 前置检查"

if (-not (Test-Admin)) {
    Die "必须以管理员身份运行 PowerShell（msiexec 写 Program Files + 注册服务需要管理员权限）"
}
Write-Ok "管理员权限"

# 检查 WiX CLI
$wixCmd = Get-Command wix -ErrorAction SilentlyContinue
if (-not $wixCmd) {
    Die "未找到 wix CLI。先装：dotnet tool install -g wix --version 5.0.2"
}
$wixVer = (& wix --version) 2>$null
if ($wixVer -notmatch "^5\.") {
    Die "WiX 版本不对（当前 $wixVer）。本工程只支持 WiX v5。卸载后重装：dotnet tool uninstall -g wix; dotnet tool install -g wix --version 5.0.2"
}
Write-Ok "WiX CLI: $wixVer"

# 检查 WiX UI 扩展（看目录，因为 v5.0.2 的 wix extension list 有 bug）
$uiExtDir = Join-Path $env:USERPROFILE ".wix\extensions\WixToolset.UI.wixext"
if (-not (Test-Path $uiExtDir)) {
    Write-Warn2 "WiX UI 扩展未装，正在安装..."
    & wix extension add -g WixToolset.UI.wixext/5.0.2
    if (-not (Test-Path $uiExtDir)) {
        Die "WiX UI 扩展安装失败。手动跑：wix extension add -g WixToolset.UI.wixext/5.0.2"
    }
}
Write-Ok "WiX UI 扩展就绪"

# ==================== Step 1: 编译发布 ====================
if ($SkipPublish) {
    Write-Step "Step 1/5: 跳过 publish（-SkipPublish）"
} else {
    Write-Step "Step 1/5: 编译发布（publish.ps1）"
    & powershell -ExecutionPolicy Bypass -File $publishScript
    if ($LASTEXITCODE -ne 0) { Die "publish.ps1 失败 (exit $LASTEXITCODE)" }
    Write-Ok "publish 完成"
}

# ==================== Step 2: 构建 MSI ====================
Write-Step "Step 2/5: 构建 MSI（build-msi.ps1, version=$Version）"
& powershell -ExecutionPolicy Bypass -File $buildMsiScript -Version $Version
if ($LASTEXITCODE -ne 0) { Die "build-msi.ps1 失败 (exit $LASTEXITCODE)" }
if (-not (Test-Path $msiPath)) { Die "MSI 未生成：$msiPath" }
$msiSize = [math]::Round((Get-Item $msiPath).Length / 1MB, 1)
Write-Ok "MSI 就绪：$msiPath ($msiSize MB)"

if ($SkipInstall) {
    Write-Host ""
    Write-Ok "完成（-SkipInstall，未安装）。"
    Write-Host "    MSI: $msiPath"
    Write-Host "    手动安装：msiexec /i `"$msiPath`""
    exit 0
}

# ==================== Step 3: 卸载旧版 ====================
Write-Step "Step 3/5: 检测并卸载旧版"

$old = Find-InstalledProductCode
if ($old) {
    Write-Warn2 "检测到已安装：$($old.DisplayName) v$($old.Version) (ProductCode=$($old.ProductCode))"
    Write-Host "    正在卸载..."
    $unxProc = Start-Process -FilePath "msiexec.exe" `
        -ArgumentList "/x $($old.ProductCode) /qn /norestart" `
        -Wait -PassThru
    if ($unxProc.ExitCode -ne 0) {
        Die "卸载旧版失败 (exit $($unxProc.ExitCode))。请手动卸载后重跑：msiexec /x $($old.ProductCode)"
    }
    Start-Sleep -Seconds 2
    # 再次确认
    $stillThere = Find-InstalledProductCode
    if ($stillThere) { Die "卸载后仍能检测到旧版，请手动卸载" }
    Write-Ok "旧版已卸载"
} elseif ($ForceUninstall) {
    Write-Warn2 "-ForceUninstall：未检测到旧版，但强制尝试清理残留目录"
    foreach ($d in @($installDir, $x86InstallDir)) {
        if (Test-Path $d) {
            Write-Warn2 "清理残留目录：$d"
            Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
} else {
    Write-Ok "未检测到旧版，跳过卸载"
}

# ==================== Step 4: 安装新版 ====================
Write-Step "Step 4/5: 安装新版（msiexec）"

# 安装前确保企微进程已退出（避免视觉定位联调时被遮挡）
$wecomProcs = Get-Process | Where-Object { $_.ProcessName -like "*WXWork*" -or $_.ProcessName -like "*WeCom*" }
if ($wecomProcs) {
    Write-Warn2 "检测到企业微信进程在运行，安装前强制结束（避免后续 App 启动时截图被遮挡）"
    $wecomProcs | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Write-Ok "企业微信进程已结束"
} else {
    Write-Ok "企业微信未运行（OK）"
}

# 静默安装（自动化场景）。如需 UI 把 /qn 去掉。
# 注意：用 Start-Process -Wait 等 msiexec 真正完成，避免脚本立即进验证阶段时
# Windows Installer Service 还在后台拷文件、注册服务，导致验证误判失败。
$msiProc = Start-Process -FilePath "msiexec.exe" `
    -ArgumentList "/i `"$msiPath`" /qn /norestart" `
    -Wait -PassThru
if ($msiProc.ExitCode -ne 0) { Die "MSI 安装失败 (exit $($msiProc.ExitCode))" }
Start-Sleep -Seconds 2
Write-Ok "MSI 安装完成"

# ==================== Step 5: 验证 ====================
Write-Step "Step 5/5: 验证安装结果"

$results = @()

function Add-Check([string]$name, [bool]$ok, [string]$detail = "") {
    $script:results += [PSCustomObject]@{
        Item   = $name
        Result = if ($ok) { "PASS" } else { "FAIL" }
        Detail = $detail
    }
}

# 1. 安装位置
$appExe = Join-Path $installDir "app\Client.App.exe"
Add-Check "安装位置 (Program Files\WeComRpa)" (Test-Path $appExe) $appExe

# 2. 子目录文件
Add-Check "app\assets\wecom_nodes.yaml" (Test-Path (Join-Path $installDir "app\assets\wecom_nodes.yaml")) ""
Add-Check "app\configs\client.example.yaml" (Test-Path (Join-Path $installDir "app\configs\client.example.yaml")) ""

# 3. Supervisor exe
Add-Check "supervisor\Client.Supervisor.exe" (Test-Path (Join-Path $installDir "supervisor\Client.Supervisor.exe")) ""

# 4. HKCU Run 自启路径
try {
    $runPath = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "WeComRpaApp" -ErrorAction Stop).WeComRpaApp
    $runOk = $runPath -like "*\Program Files\WeComRpa\app\Client.App.exe" -and $runPath -notlike "*(x86)*"
    Add-Check "HKCU Run 自启" $runOk $runPath
} catch {
    Add-Check "HKCU Run 自启" $false "注册表项不存在"
}

# 5. 服务注册
try {
    $svc = Get-Service -Name "WeComRpaSupervisor" -ErrorAction Stop
    Add-Check "Windows 服务 WeComRpaSupervisor" $true "Status=$($svc.Status), StartType=$($svc.StartType)"
} catch {
    Add-Check "Windows 服务 WeComRpaSupervisor" $false "服务未注册"
}

# 6. 开始菜单快捷方式
$shortcut = "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\企业微信RPA\企业微信RPA客户端.lnk"
Add-Check "开始菜单快捷方式" (Test-Path $shortcut) ""

# 7. 已安装程序列表
$newInstall = Find-InstalledProductCode
Add-Check "已安装程序列表" ($null -ne $newInstall) $(if ($newInstall) { "$($newInstall.DisplayName) v$($newInstall.Version)" } else { "" })

# 8. 旧 (x86) 位置不应存在
Add-Check "旧 (x86) 位置已清理" (-not (Test-Path $x86InstallDir)) ""

# 打印结果
Write-Host ""
$results | Format-Table -AutoSize

$failed = $results | Where-Object { $_.Result -eq "FAIL" }
if ($failed) {
    Write-Err ""
    Write-Err "有 $($failed.Count) 项验证失败，请排查："
    $failed | ForEach-Object { Write-Err ("  - " + $_.Item + ": " + $_.Detail) }
    Write-Err ""
    Write-Err "排查指南：docs/build-install-guide.md §3 和 §9"
    exit 1
} else {
    Write-Host ""
    Write-Ok "全部 $($results.Count) 项验证通过！"
    Write-Host ""
    Write-Host "下一步："
    Write-Host "  1. 配置 client.yaml（复制 client.example.yaml 改值）"
    Write-Host "  2. 启动 Client.App 或重启 Windows 触发自启"
    Write-Host "  3. 详见 docs/build-install-guide.md 和 docs/操作手册.md"
}
