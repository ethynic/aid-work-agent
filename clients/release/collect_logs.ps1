# 诊断包采集（双击 collect_logs.bat 触发本脚本）
# 采集：runtime 日志 + 配置 + 环境/包版本 + node 进程清单 + status/doctor 输出，打包 zip 到桌面。
# 红线：绝不采集 credentials.bin（DPAPI 加密绑本机，外发无意义且含设备凭证）。
# 2026-09-01 增强：文件名 ASCII 化（PS5.1 Compress-Archive 对中文文件名会写乱码）；
# 版本读取多级 fallback（残缺安装时 package.json 读不出要明确提示）；node 进程清单
# （排查旧 runtime 计划任务没停、双 runtime 并存）。
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

# 3) 环境信息（env_info.txt：ASCII 文件名，zip 内不乱码）
$pkgJson = Join-Path $env:APPDATA 'npm\node_modules\agent-tool-runtime\package.json'
$rtVer = '(未安装)'
$installNote = ''
if (Test-Path $pkgJson) {
  try {
    $v = (Get-Content $pkgJson -Raw -Encoding UTF8 | ConvertFrom-Json).version
    if ($v) { $rtVer = $v } else { $rtVer = '(package.json 存在但无 version 字段——安装残缺)' }
  } catch {
    $rtVer = '(package.json 存在但不可解析——安装可能残缺，建议干净重装)'
  }
} else {
  $npmRoot = (npm root -g 2>$null)
  if ($npmRoot -and (Test-Path "$npmRoot\agent-tool-runtime\package.json")) {
    # npm prefix 非默认位置：从 npm root -g 找
    try { $rtVer = (Get-Content "$npmRoot\agent-tool-runtime\package.json" -Raw | ConvertFrom-Json).version } catch { $rtVer = '(非默认 npm root 下读取失败)' }
    $installNote = "npm root -g: $npmRoot"
  }
}
@(
  "采集时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
  "计算机: $env:COMPUTERNAME  用户: $env:USERNAME",
  "node: $(node -v 2>&1)",
  "npm: $(npm -v 2>&1)",
  "agent-tool-runtime 包版本: $rtVer",
  $installNote
) | Out-File (Join-Path $tmp 'env_info.txt') -Encoding utf8

# 4) node 进程清单（含命令行：识别 aid-runtime / boss mcp --stdio，排查双 runtime 并存）
Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
  Select-Object ProcessId, ParentProcessId, CreationDate, CommandLine |
  Format-List | Out-File (Join-Path $tmp 'processes.txt') -Encoding utf8

# 5) status / doctor（doctor 含网络探测与 boss CLI 全链路检查 + 姓名配对自检，约 30-60 秒）
cmd /c aid-runtime status 2>&1 | Out-File (Join-Path $tmp 'status.txt') -Encoding utf8
cmd /c aid-runtime doctor 2>&1 | Out-File (Join-Path $tmp 'doctor.txt') -Encoding utf8

# 6) 打包到桌面（zip 外层文件名用中文无碍，乱码只发生在 zip 内部条目名，已全部 ASCII 化）
$zip = Join-Path $desktop "aidwork-诊断包-$stamp.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$tmp\*" -DestinationPath $zip -Force
Remove-Item $tmp -Recurse -Force
Write-Host ""
Write-Host "诊断包已生成：$zip"
Write-Host "请把这个 zip 文件发给管理员。"
