
# 加载配置参数
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "deploy_config.ps1")  # 包含配置文件

# 记录开始时间
$startTime = Get-Date

# 检查 $remoteDirectory 尾部如果不是 "/" ,则添加 "/"
if ($remoteDirectory[-1] -ne "/") {
    $remoteDirectory += "/"
}

function ensureRemoteDirectory($session, $remotePath) {
    # 确保远程目录存在，逐层创建
    # $remotePath 应为完整的远程文件路径
    # 规范化路径分隔符：将所有 \ 替换为 /，并处理双斜杠
    $remotePath = $remotePath -replace '\\', '/'
    # 替换双斜杠为单斜杠（递归处理，确保所有 // 都变成 /）
    while ($remotePath.Contains('//')) {
        $remotePath = $remotePath -replace '//', '/'
    }

    # 安全地获取父目录（使用字符串操作，避免 Split-Path -Parent 对 Unix 路径的问题）
    # 先获取目录部分（去掉文件名）
    $lastSlashIndex = $remotePath.LastIndexOf('/')
    if ($lastSlashIndex -le 0) {
        return $true  # 没有父目录（或只有根目录）
    }
    $remoteDir = $remotePath.Substring(0, $lastSlashIndex)

    if ([string]::IsNullOrEmpty($remoteDir)) {
        return $true
    }

    # 规范化路径：确保以 / 开头
    if (-not $remoteDir.StartsWith("/")) {
        $remoteDir = "/" + $remoteDir
    }

    # 分解路径为各层目录，例如 /var/www/agent/src/services -> ['/', '/var', '/var/www', '/var/www/agent', ...]
    $pathParts = $remoteDir -split '/'
    $dirPaths = @()
    $accumulated = ""
    foreach ($part in $pathParts) {
        if ([string]::IsNullOrEmpty($part)) {
            $accumulated = "/"
        } else {
            if ($accumulated -eq "/") {
                $accumulated = "/" + $part
            } else {
                $accumulated = $accumulated + "/" + $part
            }
            $dirPaths += $accumulated
        }
    }

    # 逐层检查并创建目录
    foreach ($dirPath in $dirPaths) {
        if ([string]::IsNullOrEmpty($dirPath) -or $dirPath -eq "/" -or $dirPath -eq ".") {
            continue
        }
        try {
            # 检查目录是否存在（使用文件信息获取来判断目录是否存在）
            $null = $session.GetFileInfo($dirPath)
            # 如果没抛异常，说明目录或文件存在
        }
        catch {
            # 目录不存在，尝试创建
            try {
                $session.CreateDirectory($dirPath)
                Write-Host "  创建远程目录: $dirPath"
            }
            catch {
                # 如果创建失败，记录错误但不中断，因为父目录可能已存在
                Write-Host "  创建远程目录失败: $dirPath - $($_.Exception.Message)"
            }
        }
    }
    return $true
}

function uploadFile($file, $session, $remoteDirectory, $transferOptions, [ref]$uploadCount, $localBasePath) {
    # 计算相对路径（保留子目录结构）
    # 关键：使用正斜杠作为路径分隔符，确保与远程 Unix 路径兼容
    $relativePath = $file.FullName.Substring($localBasePath.Length).Replace('\\', '/')

    # 安全获取父目录（使用字符串操作，避免 Split-Path -Parent 对 Unix 路径的问题）
    $lastSlashIndex = $relativePath.LastIndexOf('/')
    if ($lastSlashIndex -gt 0) {
        $relativeDir = $relativePath.Substring(0, $lastSlashIndex)
    } else {
        $relativeDir = ""
    }

    # 直接上传文件，WinSCP 会自动创建不存在的目录
    $remoteFilePath = "$($remoteDirectory)$($relativePath)"
    # 规范化路径：替换反斜杠为正斜杠，处理双斜杠问题
    $normalizedPath = $remoteFilePath -replace '\\', '/' -replace '//', '/'
    # 先检查文件是否存在，避免 GetFileInfo 在文件不存在时抛出异常
    $remoteFileExists = $session.FileExists($normalizedPath)
    $remoteFileInfo = $null
    if ($remoteFileExists) {
        try {
            $remoteFileInfo = $session.GetFileInfo($normalizedPath)
        }
        catch {
            # 获取文件信息失败，视为文件不存在
            $remoteFileInfo = $null
        }
    }

    # 远程文件不存在或本地文件的修改时间 大于 远程文件，才需要上传文件。将远程时间加1秒，防止时间误差导致重复上传。
    if ((-not $remoteFileInfo) -or ($remoteFileInfo.LastWriteTime.AddSeconds(1) -lt $file.LastWriteTime)) {
        # 上传文件（WinSCP 会自动创建不存在的目录）
        # 使用规范化路径确保文件上传到正确的位置
        $transferResult = $session.PutFiles($file.FullName, $normalizedPath, $False, $transferOptions)
        # 检查上传结果
        if ($transferResult.IsSuccess) {
            $uploadCount.Value++  # 增加计数器
            # 使用上传前的远程文件时间进行对比显示
            $remoteTimeStr = if ($remoteFileInfo) { $remoteFileInfo.LastWriteTime } else { "无" }
            Write-Host "上传成功 [$($uploadCount.Value)]: $($relativePath.PadRight(40)) 本地时间 $($file.LastWriteTime) > 远程时间 $remoteTimeStr"
        } else {
            # 检查是否是目录不存在的错误
            $shouldRetry = $false
            foreach ($error in $transferResult.Failures) {
                if ($error.Message -like "*No such file*") {
                    $shouldRetry = $true
                    break
                }
            }

            # 如果是目录不存在，先创建目录再重试
            if ($shouldRetry) {
                Write-Host "远程目录不存在，正在创建: $($remoteDirectory)$($relativeDir)"
                if (ensureRemoteDirectory $session "$($remoteDirectory)$($relativePath)") {
                    # 重试上传
                    $transferResult = $session.PutFiles($file.FullName, $normalizedPath, $False, $transferOptions)
                    if ($transferResult.IsSuccess) {
                        $uploadCount.Value++  # 增加计数器
                        # 使用上传前的远程文件时间进行对比显示
                        $remoteTimeStr = if ($remoteFileInfo) { $remoteFileInfo.LastWriteTime } else { "无" }
                        Write-Host "上传成功(重试) [$($uploadCount.Value)]: $($relativePath.PadRight(40)) 本地时间 $($file.LastWriteTime) > 远程时间 $remoteTimeStr"
                    } else {
                        foreach ($error in $transferResult.Failures) {
                            if ($error.Message -like "*was successful*") {
                                $uploadCount.Value++
                                # 使用上传前的远程文件时间进行对比显示
                                $remoteTimeStr = if ($remoteFileInfo) { $remoteFileInfo.LastWriteTime } else { "无" }
                                Write-Host "上传成功(重试) [$($uploadCount.Value)]: $($relativePath.PadRight(40)) 本地时间 $($file.LastWriteTime) > 远程时间 $remoteTimeStr"
                            } else {
                                Write-Host "上传失败(重试): $($relativePath) 错误: $($error.Message)"
                            }
                        }
                    }
                } else {
                    Write-Host "上传失败: $($relativePath) - 无法创建远程目录"
                }
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
# 为 .py 文件设置执行权限 (755: rwxr-xr-x)
$transferOptions.FilePermissions = New-Object WinSCP.FilePermissions
$transferOptions.FilePermissions.Octal = "0755"

$uploadCount = 0

# 获取当前脚本所在目录的父目录（项目根目录）
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$localBasePath = Split-Path -Parent $scriptDir

Write-Host "本地源目录: $localBasePath"
Write-Host "远程目标目录: $remoteDirectory"
Write-Host "----------------------------------------"

# 递归获取所有文件（排除指定目录 和 指定文件类型，以及数据库文件和缓存文件）
$files = Get-ChildItem -Path $localBasePath -File -Recurse | Where-Object {
    $fullPath = $_.FullName
    # 排除目录：.git, .codebuddy, .workbuddy, deploy, docs, frontend/src, frontend/node_modules, log, plans, test_uploads, venv, __pycache__, uploads, memories, .idea, .vscode, frontend/dist, frontend/.vite, frontend/.output
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar).git*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar).codebuddy*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar).workbuddy*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)deploy*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)docs*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)frontend$([System.IO.Path]::DirectorySeparatorChar)src*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)frontend$([System.IO.Path]::DirectorySeparatorChar)node_modules*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar).idea*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar).vscode*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)log*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)uploads*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)memories*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)plans*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)test_uploads*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)venv*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)ENV*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)env*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar).__pycache__*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar).pytest_cache*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar).mypy_cache*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar).pyre*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar).DS_Store*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)Thumbs.db*") -and
    -not ($fullPath -like "*$([System.IO.Path]::DirectorySeparatorChar)frontend$([System.IO.Path]::DirectorySeparatorChar).env*") -and
    # 排除文件类型
    -not ($_.Extension -eq ".bat") -and
    -not ($_.Extension -eq ".log") -and
    -not ($_.Extension -eq ".env") -and
    -not ($_.Extension -eq ".env.local") -and
    -not ($_.Name -like "*.env.*.local") -and
    -not ($_.Extension -eq ".example") -and
    -not ($_.Extension -eq ".dockerignore") -and
    -not ($_.Extension -eq ".gitignore") -and
    -not ($_.Extension -eq ".installed.cfg") -and
    -not ($_.Extension -eq ".egg") -and
    -not ($_.Extension -eq ".db") -and
    -not ($_.Extension -eq ".sqlite3") -and
    -not ($_.Extension -eq ".db-wal") -and
    -not ($_.Extension -eq ".db-shm") -and
    -not ($_.Extension -eq ".cache") -and
    -not ($_.Extension -eq ".pyc") -and
    -not ($_.Extension -eq ".pyo") -and
    -not ($_.Extension -eq ".pyd") -and
    -not ($_.Name -like "*$py.class") -and
    -not ($_.Extension -eq ".so") -and
    -not ($_.Extension -eq ".coverage") -and
    -not ($_.Name -like "*.swp") -and
    -not ($_.Name -like "*.swo") -and
    -not ($_.Name -like "*~") -and
    -not ($_.Name -like "*.tmp") -and
    -not ($_.Name -like "*.temp") -and
    -not ($_.Name -like "*.bak")
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

