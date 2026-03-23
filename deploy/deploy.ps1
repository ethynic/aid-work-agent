
# 加载配置参数
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "deploy_config.ps1")  # 包含配置文件

# 记录开始时间
$startTime = Get-Date

# 检查 $remoteDirectory 尾部如果不是 "/" ,则添加 "/"
if ($remoteDirectory[-1] -ne "/") {
    $remoteDirectory += "/"
}

function uploadFile($file, $session, $remoteDirectory, $transferOptions, [ref]$uploadCount, $localBasePath) {
    # 计算相对路径（保留子目录结构）
    $relativePath = $file.FullName.Substring($localBasePath.Length).Replace('\', '/')
    $relativeDir = Split-Path -Parent $relativePath

    # 直接上传文件，WinSCP 会自动创建不存在的目录
    $remoteFilePath = "$($remoteDirectory)$($relativePath)"
    try {
        $remoteFileInfo = $session.GetFileInfo($remoteFilePath)
    }
    catch [WinSCP.SessionRemoteException] {
        # 如果远程文件不存在，则设置 $remoteFileInfo 为 $null
        $remoteFileInfo = $null
    }
    
    # 检查远程文件是否存在或本地文件的修改时间是否晚于远程文件。将远程时间加1秒，防止时间误差导致重复上传。
    if ((-not $remoteFileInfo) -or ($remoteFileInfo.LastWriteTime.AddSeconds(1) -lt $file.LastWriteTime)) {
        # 上传文件（WinSCP 会自动创建不存在的目录）
        $transferResult = $session.PutFiles($file.FullName, $remoteFilePath, $False, $transferOptions)
        # 检查上传结果
        if ($transferResult.IsSuccess) {
            $uploadCount.Value++  # 增加计数器
            $remoteTimeStr = if ($remoteFileInfo) { $remoteFileInfo.LastWriteTime } else { "无" }
            Write-Host "上传成功 [$($uploadCount.Value)]: $($relativePath.PadRight(40)) 本地时间 $($file.LastWriteTime) > 远程时间 $remoteTimeStr"
        } else {
            # 输出详细错误信息，但如果错误信息包含 "was successful"，说明文件已成功上传，只是修改时间设置失败，不报错
            foreach ($error in $transferResult.Failures) {
                if ($error.Message -like "*was successful*") {
                    $uploadCount.Value++  # 仍然增加计数器
                    $remoteTimeStr = if ($remoteFileInfo) { $remoteFileInfo.LastWriteTime } else { "无" }
                    Write-Host "上传成功 [$($uploadCount.Value)]: $($relativePath.PadRight(40)) 本地时间 $($file.LastWriteTime) > 远程时间 $remoteTimeStr"
                } else {
                    Write-Host "上传失败: $($relativePath) 错误: $($error.Message)"
                }
            }
        }
    }
}

$sessionOptions = New-Object WinSCP.SessionOptions -Property @{
    Protocol = [WinSCP.Protocol]::Sftp
    HostName = $sftpServer
    UserName = $username
    Password = $password
    PortNumber = $port
    GiveUpSecurityAndAcceptAnySshHostKey = $true  # 接受任何 SSH 主机密钥（仅用于测试环境）
}

$session = New-Object WinSCP.Session
# 设置日志路径
# $session.SessionLogPath = $logPath
# 打开会话
$session.Open($sessionOptions)
if (-not $session.Opened) {
    Write-Host "FTP会话连接失败，退出程序。"
    Write-Host "`n按任意键继续..."
    $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
    exit 1  # 退出程序
}
$transferOptions = New-Object WinSCP.TransferOptions
$transferOptions.TransferMode = [WinSCP.TransferMode]::Binary

$uploadCount = 0

# 获取当前脚本所在目录的父目录（项目根目录）
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$localBasePath = Split-Path -Parent $scriptDir

# ============================================================
# 更新版本号时间戳
# 作用：每次发布时自动更新版本号，HTML 页面会根据此时间戳刷新浏览器缓存
# 原理：HTML 中的内联脚本会读取此文件，为 js/css 引用添加 ?v=时间戳 参数
# ============================================================
$versionFile = Join-Path $localBasePath "version.json"
if (Test-Path $versionFile) {
    $timestamp = Get-Date -Format "yyyyMMddHHmm"
    $versionContent = "{`"timestamp`": `"$timestamp`",`"说明`":`"用于解决浏览器缓存问题。每次发布时，deploy.ps1 会自动更新此时间戳，HTML 页面会自动在 js/css 引用后加上 ?v=时间戳，强制浏览器加载最新资源`"}"
    Set-Content -Path $versionFile -Value $versionContent -Encoding UTF8
    Write-Host "已更新版本号: $timestamp"
}

Write-Host "本地源目录: $localBasePath"
Write-Host "远程目标目录: $remoteDirectory"
Write-Host "----------------------------------------"

# 递归获取所有文件（排除指定目录和 .md 文件）
$files = Get-ChildItem -Path $localBasePath -File -Recurse | Where-Object {
    $fullPath = $_.FullName
    # 排除目录：.git, .codebuddy, .workbuddy, deploy, docs, frontend\src, logs, plans, test_uploads
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar).git*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar).codebuddy*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar).workbuddy*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)deploy*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)docs*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)frontend$([System.IO.Path]::DirectorySeparatorChar)src*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)logs*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)plans*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)test_uploads*") -and
    -not ($_.Extension -eq ".md")
}

foreach ($file in $files) {
    uploadFile $file $session $remoteDirectory $transferOptions ([ref]$uploadCount) $localBasePath
}

# 关闭会话
$session.Dispose()

# 计算并显示总共耗时
$endTime = Get-Date
$duration = $endTime - $startTime
Write-Host "共上传 [$($uploadCount)] 个文件。耗时: $($duration.TotalSeconds) 秒"

