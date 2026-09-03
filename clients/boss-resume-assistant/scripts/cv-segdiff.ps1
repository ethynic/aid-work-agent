# Pixel-level "same view?" check for two full-page screenshot segments (P1 bottom-detection hardening).
# Usage: powershell -File scripts/cv-segdiff.ps1 -A part-raw-3.png -B part-raw-2.png -X 168 -Y 40 -W 760 -H 1264
#
# Why it exists (device evidence 2026-09-02): the P0 bottom detection compared PNG byte sizes only,
# and a swallowed Win32 wheel event or a slow lazy canvas repaint also produces near-identical
# screenshots mid-resume -> false "reached bottom" truncated the resume. The Node caller now calls
# this script when adjacent segments differ by < BOTTOM_SIZE_EPSILON bytes, to confirm pixel-wise
# that the CANVAS REGION [X,Y,W,H] really shows the same frame (same view => candidate bottom).
#
# Pipeline (all GDI+/.NET, zero third-party deps, same constants/style as cv-stitch.ps1):
#   1. Load both full-page PNGs, crop the canvas region [X,Y,W,H] via Bitmap.Clone.
#      Coordinates are device px straight from DOMSnapshot - same coordinate space as the
#      full-page screenshot, NO DPI conversion (see cv-stitch.ps1 trap note).
#   2. Downscale both crops to width 180 and sample x step 4 / y step 3 (cv-stitch.ps1
#      GetPixel performance trap: full-resolution GetPixel takes minutes).
#   3. mismatch rate = sampled points with |dR|+|dG|+|dB| > 60 / all sampled points
#      (color-distance tolerance because WASM repaints have sub-pixel differences even
#      on identical content - strict equality never matches).
#
# stdout: "misrate=0.0123" (InvariantCulture F4). The Node caller compares it against
#   SAME_VIEW_MISRATE_MAX (0.01) - identical frames repaint within <1% sampled points.
# Exit codes: 0=ok; 1=file/IO or image error (including crop rect out of bounds - the
#   window changed mid-capture; the Node caller fails loud on this); 2=bad params
param(
  [Parameter(Mandatory=$true)][string]$A,
  [Parameter(Mandatory=$true)][string]$B,
  [Parameter(Mandatory=$true)][int]$X,
  [Parameter(Mandatory=$true)][int]$Y,
  [Parameter(Mandatory=$true)][int]$W,
  [Parameter(Mandatory=$true)][int]$H
)

if ($W -le 0 -or $H -le 0) { Write-Error "W/H must be positive (got ${W}x${H})"; exit 2 }
if ($X -lt 0 -or $Y -lt 0) { Write-Error "X/Y must be >= 0 (got ($X,$Y))"; exit 2 }
foreach ($f in @($A, $B)) {
  if (-not (Test-Path -LiteralPath $f)) { Write-Error "image not found: $f"; exit 1 }
}

Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @"
using System;
using System.Drawing;
using System.Drawing.Imaging;

public static class CvSegDiff {
    // Same constants as cv-stitch.ps1 (CvStitcher) so both scripts judge "mismatch"
    // identically: downscale width 180, x step 4 / y step 3, |dR|+|dG|+|dB| > 60.
    public const int SmallW = 180;
    public const int XStep = 4;
    public const int YStep = 3;
    public const int ColorTol = 60;

    public static Bitmap Crop(Bitmap src, int x, int y, int w, int h) {
        Rectangle rect = new Rectangle(x, y, w, h);
        return src.Clone(rect, PixelFormat.Format32bppArgb);
    }

    private static Bitmap Downscale(Bitmap src) {
        int sw = SmallW;
        int sh = Math.Max(1, (int)Math.Round((double)src.Height * sw / src.Width));
        Bitmap small = new Bitmap(sw, sh, PixelFormat.Format32bppArgb);
        using (Graphics g = Graphics.FromImage(small)) {
            g.DrawImage(src, new Rectangle(0, 0, sw, sh));
        }
        return small;
    }

    // Fraction of sampled points whose color distance exceeds ColorTol.
    public static double MismatchRate(Bitmap a, Bitmap b) {
        Bitmap sa = null, sb = null;
        try {
            sa = Downscale(a);
            sb = Downscale(b);
            int w = Math.Min(sa.Width, sb.Width);
            int h = Math.Min(sa.Height, sb.Height);
            int compared = 0, bad = 0;
            for (int y = 0; y < h; y += YStep) {
                for (int x = 0; x < w; x += XStep) {
                    Color ca = sa.GetPixel(x, y);
                    Color cb = sb.GetPixel(x, y);
                    compared++;
                    int d = Math.Abs(ca.R - cb.R) + Math.Abs(ca.G - cb.G) + Math.Abs(ca.B - cb.B);
                    if (d > ColorTol) bad++;
                }
            }
            return compared > 0 ? (double)bad / compared : 1.0;
        } finally {
            if (sa != null) sa.Dispose();
            if (sb != null) sb.Dispose();
        }
    }
}
"@

$bmpA = $null
$bmpB = $null
$cropA = $null
$cropB = $null
try {
  $bmpA = New-Object System.Drawing.Bitmap($A)
  $bmpB = New-Object System.Drawing.Bitmap($B)
  foreach ($pair in @(@($bmpA, $A), @($bmpB, $B))) {
    $bmp = $pair[0]
    $name = $pair[1]
    if (($X + $W) -gt $bmp.Width -or ($Y + $H) -gt $bmp.Height) {
      # Crop out of bounds: the window changed mid-capture - fail loud, never guess.
      Write-Error ("crop rect ({0},{1},{2}x{3}) exceeds image {4}x{5}: {6}" -f $X, $Y, $W, $H, $bmp.Width, $bmp.Height, $name)
      exit 1
    }
  }
  $cropA = [CvSegDiff]::Crop($bmpA, $X, $Y, $W, $H)
  $cropB = [CvSegDiff]::Crop($bmpB, $X, $Y, $W, $H)
  $mis = [CvSegDiff]::MismatchRate($cropA, $cropB)
  Write-Output ("misrate=" + $mis.ToString('F4', [System.Globalization.CultureInfo]::InvariantCulture))
  exit 0
} catch {
  Write-Error ("segdiff failed: " + $_.Exception.Message)
  exit 1
} finally {
  if ($cropA -ne $null) { $cropA.Dispose() }
  if ($cropB -ne $null) { $cropB.Dispose() }
  if ($bmpA -ne $null) { $bmpA.Dispose() }
  if ($bmpB -ne $null) { $bmpB.Dispose() }
}
