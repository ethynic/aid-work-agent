
# 加载 WinSCP .NET 程序集
Add-Type -Path "C:\Program Files (x86)\WinSCP\WinSCPnet.dll"  # 根据您的安装路径调整
$sftpServer = "124.222.3.254"                     # SFTP 服务器地址
$port = 22                                         # 端口号
$username = "gaofang"                                  # 用户名
$password = "Dandian=6"                              # SSH 密码
$remoteDirectory = "/var/www/agent2/"       # 远程目录
