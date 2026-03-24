param (
    [int]$mode = 0  # 0 精简模式，1 完整模式
)

# 加载配置参数
. .\deploy_config.ps1  # 包含配置文件

# 记录开始时间
$startTime = Get-Date

# 检查 $SshPrivateKeyPath 文件，如果不存在，报错退出
if (!(Test-Path $SshPrivateKeyPath)) {
    Write-Host "没有找到私钥文件: $SshPrivateKeyPath"
    Write-Host "`n按任意键继续..."
    $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
    exit 1
}

# 检查 $remoteDirectory 尾部如果不是 "/" ,则添加 "/"
if ($remoteDirectory[-1] -ne "/") {
    $remoteDirectory += "/"
}

function uploadFile($file, $session, $remoteDirectory, $transferOptions, [ref]$uploadCount, $relativePath="") {
    # 精简模式下，修改时间在1天前的文件不进行上传
    if ($global:mode -eq 0 -and $file.LastWriteTime -lt (Get-Date).AddDays(-1)) {
        return
    }
    
    # 构建完整的远程目录路径
    $remoteDir = "$($remoteDirectory)$($relativePath)".TrimEnd('/')
    
    # 检查并创建远程目录
    try {
        if (-not $session.FileExists($remoteDir)) {
            Write-Host "创建远程目录: $remoteDir"
            $session.CreateDirectory($remoteDir)
        }
    }
    catch {
        Write-Host "创建目录失败: $remoteDir - $($_.Exception.Message)"
        return
    }
    # 获取远程文件的修改时间
    $remoteFilePath = "$($remoteDirectory)$($relativePath)$($file.Name)"
    try {
        $remoteFileInfo = $session.GetFileInfo($remoteFilePath)
    }
    catch [WinSCP.SessionRemoteException] {
        # 如果远程文件不存在，则设置 $remoteFileInfo 为 $null
        $remoteFileInfo = $null
    }
    # 检查远程文件是否存在或本地文件的修改时间是否晚于远程文件。将远程时间加1秒，防止时间误差导致重复上传。
    if ((-not $remoteFileInfo) -or ($remoteFileInfo.LastWriteTime.AddSeconds(1) -lt $file.LastWriteTime)) {
        # 上传文件
        $transferResult = $session.PutFiles($file.FullName, "$($remoteDirectory)$($relativePath)", $False, $transferOptions)
        # 检查上传结果
        if ($transferResult.IsSuccess) {
            $uploadCount.Value++  # 增加计数器
            Write-Host "上传成功 [$($uploadCount.Value)]: $($file.Name.PadRight(24))本地时间 $($file.LastWriteTime) > 远程时间 $($remoteFileInfo.LastWriteTime)"
        } else {
            # 输出详细错误信息
            foreach ($error in $transferResult.Failures) {
                Write-Host "上传失败: $($file.Name) 错误: $($error.Message)"
            }
        }
    }
}

$sessionOptions = New-Object WinSCP.SessionOptions -Property @{
    Protocol = [WinSCP.Protocol]::Sftp
    HostName = $sftpServer
    UserName = $username
    PortNumber = $port
    SshHostKeyFingerprint = $sshHostKeyFingerprint  # 设置 SSH 主机密钥指纹
    SshPrivateKeyPath = $SshPrivateKeyPath
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
# 获取当前目录下的 qbwebproject.ini 文件句柄
$iniFilePath = Join-Path -Path (Get-Location) -ChildPath "qbwebproject.ini"
$file = Get-Item -Path $iniFilePath -ErrorAction SilentlyContinue

# 检查文件是否存在
if ($file) {
    uploadFile $file $session $remoteDirectory $transferOptions ([ref]$uploadCount)
}

# 获取本地目录下的 XML 文件
$files = Get-ChildItem -Path "deploy" -Filter *.xml
foreach ($file in $files) {
    uploadFile $file $session $remoteDirectory $transferOptions ([ref]$uploadCount)
}

# 获取本地目录下的 XLS 和 DOCX 文件
$files = Get-ChildItem -Path "xls" -Include @("*.xlsx", "*.docx") -File 
foreach ($file in $files) {
    uploadFile $file $session $remoteDirectory $transferOptions ([ref]$uploadCount)
}
# 递归获取 php 目录下所有 PHP 文件，包含子目录
$files = Get-ChildItem -Path "php" -Filter *.* -Recurse
foreach ($file in $files) {
    # 获取当前目录下 php 文件夹的完整路径
    $basePhpPath = (Get-Location).Path 
    # 计算相对路径：从文件的完整路径中移除 php 目录的完整路径
    $relativePath = $file.FullName.Replace($basePhpPath + "\", "")
    $relativePath = Split-Path $relativePath -Parent
    # 将反斜杠转换为正斜杠
    $relativePath = $relativePath.Replace("\", "/")
    # 如果相对路径不为空，确保以"/"结尾
    if ($relativePath) {
        $relativePath = "$relativePath/"
    }
    uploadFile $file $session $remoteDirectory $transferOptions ([ref]$uploadCount) $relativePath
}

# 关闭会话
$session.Dispose()
#if ($uploadCount -gt 0) {  # 不管上传了还是失败，都调用清除缓存接口。有可能其实上传成功了，改时间失败
    # 调用默认浏览器，打开指定网址
    Start-Process $url
#}
# 计算并显示总共耗时
$endTime = Get-Date
$duration = $endTime - $startTime
Write-Host "共上传 [$($uploadCount)] 个文件。耗时: $($duration.TotalSeconds) 秒"

Write-Host "`n30秒后自动关闭..."
Start-Sleep -Milliseconds 30000
