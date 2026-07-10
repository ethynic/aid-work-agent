#requires -Version 5.1
<#
.SYNOPSIS
  发布 Client.App 与 Client.Supervisor 为自包含单文件 exe（目标机无需装 .NET）。
.EXAMPLE
  powershell scripts\publish.ps1 -Configuration Release -Runtime win-x64
#>
[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')][string]$Configuration = 'Release',
    [string]$Runtime = 'win-x64'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$out = Join-Path $root 'publish'
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
$appOut = Join-Path $out 'app'
$supOut = Join-Path $out 'supervisor'
$cfgOut = Join-Path $out 'config-tool'

Write-Host "==> 发布 App -> $appOut"
dotnet publish (Join-Path $root 'src\Client.App\Client.App.csproj') `
    -c $Configuration -r $Runtime --self-contained --nologo `
    -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true `
    -o $appOut
if ($LASTEXITCODE -ne 0) { Write-Error "App 发布失败"; exit $LASTEXITCODE }

Write-Host "==> 发布 Supervisor -> $supOut"
dotnet publish (Join-Path $root 'src\Client.Supervisor\Client.Supervisor.csproj') `
    -c $Configuration -r $Runtime --self-contained --nologo `
    -p:PublishSingleFile=true -o $supOut
if ($LASTEXITCODE -ne 0) { Write-Error "Supervisor 发布失败"; exit $LASTEXITCODE }

Write-Host "==> 发布 ConfigTool -> $cfgOut"
dotnet publish (Join-Path $root 'src\Client.ConfigTool\Client.ConfigTool.csproj') `
    -c $Configuration -r $Runtime --self-contained --nologo `
    -p:PublishSingleFile=true -o $cfgOut
if ($LASTEXITCODE -ne 0) { Write-Error "ConfigTool 发布失败"; exit $LASTEXITCODE }

# 拷贝配置/资产到 app 目录旁，便于目标机配置
$configsOut = Join-Path $appOut 'configs'
New-Item $configsOut -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $root 'configs\client.example.yaml') (Join-Path $configsOut 'client.example.yaml') -Force
$assetsOut = Join-Path $appOut 'assets'
New-Item $assetsOut -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $root 'assets\wecom_nodes.yaml') (Join-Path $assetsOut 'wecom_nodes.yaml') -Force

# 拷贝运行时依赖的 PowerShell 脚本到 app\scripts\：
# - capture-wecom-for-csharp.ps1：截图（C# ScreenCapturer 委托）
# - wecom-ops.ps1 + wecom-ops-lib.ps1：PowershellOpsInvoker 企微操作主入口与公共库
# - prompts\：ops 脚本依赖的提示词文件（keywords.txt 等）
# 注意：缺少 wecom-ops.ps1 / prompts 会导致客户端连服务端正常，但
#       get_login_state / send_text 等全部返回 ps_no_json（找不到脚本）。
$scriptsOut = Join-Path $appOut 'scripts'
New-Item $scriptsOut -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $root 'scripts\capture-wecom-for-csharp.ps1') (Join-Path $scriptsOut 'capture-wecom-for-csharp.ps1') -Force
Copy-Item (Join-Path $root 'scripts\wecom-ops.ps1') (Join-Path $scriptsOut 'wecom-ops.ps1') -Force
Copy-Item (Join-Path $root 'scripts\wecom-ops-lib.ps1') (Join-Path $scriptsOut 'wecom-ops-lib.ps1') -Force
$promptsOut = Join-Path $scriptsOut 'prompts'
New-Item $promptsOut -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $root 'scripts\prompts\*.txt') $promptsOut -Force

Write-Host "[OK] 发布完成：$out"
Write-Host "   App:        $appOut\Client.App.exe"
Write-Host "   Supervisor: $supOut\Client.Supervisor.exe"
Write-Host "   ConfigTool: $cfgOut\Client.ConfigTool.exe"
Write-Host "   下一步：拷贝 publish\ 到目标机 → 用 config-tool\Client.ConfigTool.exe 写入配置 → install-service.ps1"
