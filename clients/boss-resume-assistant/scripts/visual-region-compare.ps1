param(
  [Parameter(Mandatory=$true)][string]$Original,
  [Parameter(Mandatory=$true)][string]$Fresh,
  [Parameter(Mandatory=$true)][int]$X,
  [Parameter(Mandatory=$true)][int]$Y,
  [Parameter(Mandatory=$true)][int]$Width,
  [Parameter(Mandatory=$true)][int]$Height
)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$a=$null
$b=$null
try {
  $a=New-Object System.Drawing.Bitmap($Original)
  $b=New-Object System.Drawing.Bitmap($Fresh)
  if ($X -lt 0 -or $Y -lt 0 -or $Width -lt 1 -or $Height -lt 1 -or $Width -gt 32 -or $Height -gt 32 -or
      $a.Width -ne $b.Width -or $a.Height -ne $b.Height -or $X+$Width -gt $a.Width -or $Y+$Height -gt $a.Height) {
    throw 'Invalid visual comparison region'
  }
  $equal=$true
  for ($dy=0; $dy -lt $Height -and $equal; $dy++) {
    for ($dx=0; $dx -lt $Width; $dx++) {
      if ($a.GetPixel($X+$dx,$Y+$dy).ToArgb() -ne $b.GetPixel($X+$dx,$Y+$dy).ToArgb()) { $equal=$false; break }
    }
  }
  @{equal=$equal} | ConvertTo-Json -Compress
} finally {
  if ($null -ne $a) { $a.Dispose() }
  if ($null -ne $b) { $b.Dispose() }
}
