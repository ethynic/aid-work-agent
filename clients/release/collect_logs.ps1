# 诊断包采集（双击 collect_logs.bat 触发本脚本）
# 采集：runtime 日志 + 配置 + 环境/包版本 + status/doctor 输出，打包 zip 到桌面。
# 红线：绝不采集 credentials.bin（DPAPI 加密绑本机，外发无意义且含设备凭证）。
$ErrorActionPreference = 'Continue'
# cmd 子进程输出按 UTF-8 解码（PS5.1 默认 GBK 会把 node 的 UTF-8 输出搞成乱码）
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$dir = Join-Path $env:APPDATA 'aidwork-tool-runtime'
$desktop = [Environment]::GetFolderPath('Desktop')
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'
$tmp = Join-Path $env:TEMP "aidwork-diag-$stamp"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

# 1) runtime 日志（含 5MB 轮转的旧档）
if (Test-Path "$dir\logs") { Copy-Item "$dir\logs\*" $tmp -Force -ErrorAction SilentlyContinue }

# 2) 配置（server 地址 / device_id / 名称；config.json 本身不含 token）
if (Test-Path "$dir\config.json") { Copy-Item "$dir\config.json" $tmp -Force }

# 3) 环境信息
$pkgJson = Join-Path $env:APPDATA 'npm\node_modules\agent-tool-runtime\package.json'
$rtVer = if (Test-Path $pkgJson) { (Get-Content $pkgJson -Raw | ConvertFrom-Json).version } else { '(未安装)' }
@(
  "采集时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
  "计算机: $env:COMPUTERNAME  用户: $env:USERNAME",
  "node: $(node -v 2>&1)",
  "npm: $(npm -v 2>&1)",
  "agent-tool-runtime 包版本: $rtVer"
) | Out-File (Join-Path $tmp '环境信息.txt') -Encoding utf8

# 4) status / doctor（doctor 含网络探测与 boss CLI 全链路检查，约 30-60 秒）
cmd /c aid-runtime status 2>&1 | Out-File (Join-Path $tmp 'status.txt') -Encoding utf8
cmd /c aid-runtime doctor 2>&1 | Out-File (Join-Path $tmp 'doctor.txt') -Encoding utf8

# 5) 打包到桌面
$zip = Join-Path $desktop "aidwork-诊断包-$stamp.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$tmp\*" -DestinationPath $zip -Force
Remove-Item $tmp -Recurse -Force
Write-Host ""
Write-Host "诊断包已生成：$zip"
Write-Host "请把这个 zip 文件发给管理员。"
