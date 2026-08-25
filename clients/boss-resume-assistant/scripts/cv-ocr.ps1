# Windows WinRT OCR for the stitched resume image (design doc section 10.8).
# Usage: powershell -File scripts/cv-ocr.ps1 -Img stitched.png -Out text.txt
#
# Zero-dependency OCR via Windows.Media.Ocr (zh-Hans-CN). Text is written to Out as UTF-8
# (no BOM) for the Node caller to read back. stdout prints "chars=<n>".
#
# PowerShell 5.1 WinRT traps (all verified on device 2026-08-14 - miss any and you get
# TypeNotFound or NullReference from RecognizeAsync):
#   1. EVERY WinRT type used must be preloaded with its winmd assembly + ContentType=
#      WindowsRuntime before first use (list below).
#   2. IAsyncOperation awaits need the System.WindowsRuntimeSystemExtensions.AsTask
#      generic helper (Add-Type System.Runtime.WindowsRuntime first).
#   3. GetSoftwareBitmapAsync MUST request explicit (Bgra8, Premultiplied) - the default
#      bitmap format makes RecognizeAsync throw NullReference.
#
# PaddleOCR cloud service is the optional future enhancement (currently 504/timeout,
# not integrated in this iteration).
# Exit codes: 0=ok; 1=file/IO error; 2=bad params; 3=no Chinese OCR engine (install the
# zh-Hans OCR language pack: Settings -> Time & Language -> Language -> Chinese -> Options -> OCR)
param(
  [Parameter(Mandatory=$true)][string]$Img,
  [Parameter(Mandatory=$true)][string]$Out
)

if (-not (Test-Path -LiteralPath $Img)) { Write-Error "image not found: $Img"; exit 1 }
$imgPath = (Resolve-Path -LiteralPath $Img).Path
$outDir = Split-Path -Parent $Out
if ($outDir -and -not (Test-Path -LiteralPath $outDir)) { Write-Error "output directory not found: $outDir"; exit 1 }
$outPath = [System.IO.Path]::GetFullPath($Out)

# --- preload every WinRT type used below (assembly differs per namespace) ---
[void][Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
[void][Windows.Storage.FileAccessMode,Windows.Storage,ContentType=WindowsRuntime]
[void][Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapPixelFormat,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapAlphaMode,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]
Add-Type -AssemblyName System.Runtime.WindowsRuntime

# --- await helper: IAsyncOperation<T> -> Task<T> via AsTask(IAsyncOperation`1) ---
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
  Where-Object {
    $_.Name -eq 'AsTask' -and
    $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
  } | Select-Object -First 1)
if ($null -eq $asTaskGeneric) { Write-Error "AsTask(IAsyncOperation`1) extension not found"; exit 1 }

function Await($WinRtTask, $ResultType) {
  $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
  $netTask = $asTask.Invoke($null, @($WinRtTask))
  $netTask.Wait()
  if ($netTask.IsFaulted) { throw $netTask.Exception }
  return $netTask.Result
}

# --- OCR engine: zh-Hans-CN only; missing language pack must fail loud ---
$lang = [Windows.Globalization.Language]::new('zh-Hans-CN')
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
if ($null -eq $engine) {
  Write-Error "no OCR engine for zh-Hans-CN: install the Chinese OCR language pack (Settings -> Time & Language -> Language -> Chinese -> Options -> OCR), then retry"
  exit 3
}

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($imgPath)) ([Windows.Storage.StorageFile])
$stream = $null
try {
  $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  # Explicit (Bgra8, Premultiplied): the decoder default format breaks RecognizeAsync (NullReference).
  $soft = Await ($decoder.GetSoftwareBitmapAsync(
    [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
    [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $result = Await ($engine.RecognizeAsync($soft)) ([Windows.Media.Ocr.OcrResult])
  $text = $result.Text
  # UTF-8 without BOM so the Node caller can read it back losslessly
  [System.IO.File]::WriteAllText($outPath, $text, [System.Text.UTF8Encoding]::new($false))
  Write-Output ("chars=" + $text.Length)
  exit 0
} catch {
  Write-Error ("OCR failed: " + $_.Exception.Message)
  exit 1
} finally {
  if ($stream -ne $null) { $stream.Dispose() }
}
