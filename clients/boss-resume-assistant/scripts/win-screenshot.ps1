# 截取屏幕指定区域（诊断点击落点）
param([int]$X, [int]$Y, [int]$W = 300, [int]$H = 150, [string]$Out = "C:\tmp\screen-region.png")
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap $W, $H
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($X, $Y, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
Write-Output "saved $Out"
