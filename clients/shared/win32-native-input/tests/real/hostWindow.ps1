# 真实输入测试的目标窗口宿主（测试专用，不属于生产输入链路）。
# 启动两个 WinForms 窗口：
#   A（目标）：多行 TextBox 充满客户区——接收物理点击/逐字键入，内容可 WM_GETTEXT 读回
#   B（遮挡）：topmost 小窗，初始移到屏幕外，由测试移动到 A 的客户区上方制造遮挡
# 用法: powershell.exe -STA -File hostWindow.ps1 -Marker <id>
param(
  [Parameter(Mandatory=$true)][string]$Marker
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$a = New-Object System.Windows.Forms.Form
$a.Text = "AID_TEST_A_$Marker"
$a.TopMost = $false
$a.StartPosition = 'Manual'
$a.Location = New-Object System.Drawing.Point(300, 300)
$a.Size = New-Object System.Drawing.Size(640, 400)
$a.MinimumSize = $a.Size
$a.MaximumSize = $a.Size
$box = New-Object System.Windows.Forms.TextBox
$box.Multiline = $true
$box.Dock = 'Fill'
$box.Font = New-Object System.Drawing.Font('Segoe UI', 14)
$a.Controls.Add($box)

$b = New-Object System.Windows.Forms.Form
$b.Text = "AID_TEST_B_$Marker"
$b.TopMost = $true
$b.StartPosition = 'Manual'
$b.Location = New-Object System.Drawing.Point(3000, 3000)
$b.Size = New-Object System.Drawing.Size(180, 140)
$b.MinimumSize = $b.Size
$b.MaximumSize = $b.Size

# 目标编辑框脱离 IME：中文输入法会拦截 VK_PACKET（KEYEVENTF_UNICODE）进入候选，
# 使断言依赖用户当前输入法状态。仅影响此测试窗口，不改系统设置。
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class AidImmDetach {
    [DllImport("imm32.dll")] public static extern IntPtr ImmAssociateContext(IntPtr hWnd, IntPtr hImc);
}
'@
$a.Add_Shown({
    try { [void][AidImmDetach]::ImmAssociateContext($box.Handle, [IntPtr]::Zero) } catch {}
    $b.Show()
})
[void]$a.ShowDialog()
$b.Close()
exit 0
