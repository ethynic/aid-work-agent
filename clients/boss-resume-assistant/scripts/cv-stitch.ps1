# Crop + overlap-align + vertical stitch of full-page screenshot segments (design doc 10.8).
# Usage: powershell -File scripts/cv-stitch.ps1 -Parts "a.png,b.png,c.png" -X 0 -Y 0 -W 727 -H 1237 -Out stitched.png [-CropDir dir]
#
# Pipeline (all GDI+/.NET, zero third-party deps, no Python in this npm package):
#   1. Each input is a full-page PNG; crop the canvas region [X,Y,W,H] via Bitmap.Clone.
#      Coordinates are device px straight from DOMSnapshot - the full-page screenshot and
#      DOMSnapshot bounds share the same coordinate space, NO DPI conversion here
#      (verified on device: dividing by 1.5 once cut off the right half of the resume).
#   2. Adjacent segments overlap because each round scrolls only ~15-25% of the canvas.
#      The canvas is a WASM virtual scroll, so repaints have sub-pixel differences and
#      strict pixel equality never matches (~20% mismatch on identical content) - align
#      with a COLOR DISTANCE tolerance instead: |dR|+|dG|+|dB| > 60 counts as mismatch,
#      pick overlap o minimizing the mismatch rate of "A bottom o rows vs B top o rows".
#   3. PowerShell GetPixel on 727px-wide images takes minutes, so alignment runs on
#      downscaled copies (width 180, x step 4, y step 3, o step 1) then o is restored
#      with o = round(o_small * origWidth / 180). ~4px accuracy is enough.
#   4. Vertical stitch: total height = h0 + sum(hi - overlap_i), Graphics.DrawImage per part.
#   5. Optional -CropDir: save each part's cropped canvas region as crop-00.png..crop-NN.png
#      (zero-padded 2 digits, index matches the Parts order; dir is created if missing).
#      The Node caller OCRs each crop separately (P1): one stitched long image can exceed
#      the local WinRT OCR MaxImageDimension (10000px) and fails as a single point.
#
# stdout: one "overlapNN=<rows> mis=<rate>" line per seam + "crops=<n>" (with -CropDir)
#   + final "stitched=<w>x<h>".
#   Top-of-resume live elements ("active"/"just now") can push seam 1 mismatch up to
#   ~0.15; that is acceptable and NOT a failure.
# Exit codes: 0=ok; 1=file/IO or image error; 2=bad params
param(
  [Parameter(Mandatory=$true)][string]$Parts,
  [Parameter(Mandatory=$true)][int]$X,
  [Parameter(Mandatory=$true)][int]$Y,
  [Parameter(Mandatory=$true)][int]$W,
  [Parameter(Mandatory=$true)][int]$H,
  [Parameter(Mandatory=$true)][string]$Out,
  [string]$CropDir = ''
)

if ($W -le 0 -or $H -le 0) { Write-Error "W/H must be positive (got ${W}x${H})"; exit 2 }
if ($X -lt 0 -or $Y -lt 0) { Write-Error "X/Y must be >= 0 (got ($X,$Y))"; exit 2 }
$partFiles = @($Parts -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
if ($partFiles.Count -eq 0) { Write-Error "Parts is empty: '$Parts'"; exit 2 }
foreach ($f in $partFiles) {
  if (-not (Test-Path -LiteralPath $f)) { Write-Error "part file not found: $f"; exit 1 }
}
if ($partFiles.Count -gt 64) { Write-Error "too many parts ($($partFiles.Count)), refusing"; exit 2 }

Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @"
using System;
using System.Drawing;
using System.Drawing.Imaging;

public static class CvStitcher {
    public const int SmallW = 180;      // downscale target width for fast alignment
    public const int XStep = 4;         // x sampling step on the downscaled image
    public const int YStep = 3;         // y sampling step on the downscaled image
    public const int ColorTol = 60;     // |dR|+|dG|+|dB| > Tol counts as mismatch
    public const double LoRatio = 0.10; // overlap search range: 10%..95% of segment height
    public const double HiRatio = 0.95;

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

    // Returns overlap o (original pixels): bottom o rows of a == top o rows of b (best mismatch).
    public static int FindOverlap(Bitmap a, Bitmap b, out double mismatchRate) {
        mismatchRate = 1.0;
        Bitmap sa = null, sb = null;
        try {
            sa = Downscale(a);
            sb = Downscale(b);
            int h = Math.Min(sa.Height, sb.Height);
            int lo = (int)Math.Floor(h * LoRatio);
            int hi = (int)Math.Ceiling(h * HiRatio);
            if (hi >= h) hi = h - 1;
            if (lo < 0) lo = 0;
            if (hi < lo) return 0;
            int bestO = lo;
            double bestMis = double.MaxValue;
            for (int os = lo; os <= hi; os++) {
                int compared = 0, bad = 0;
                for (int y = 0; y < os; y += YStep) {
                    for (int x = 0; x < sa.Width; x += XStep) {
                        Color ca = sa.GetPixel(x, sa.Height - os + y);
                        Color cb = sb.GetPixel(x, y);
                        compared++;
                        int d = Math.Abs(ca.R - cb.R) + Math.Abs(ca.G - cb.G) + Math.Abs(ca.B - cb.B);
                        if (d > ColorTol) bad++;
                    }
                }
                double mis = compared > 0 ? (double)bad / compared : 1.0;
                if (mis < bestMis) { bestMis = mis; bestO = os; }
            }
            mismatchRate = bestMis;
            int o = (int)Math.Round((double)bestO * a.Width / (double)SmallW);
            if (o > a.Height) o = a.Height;
            if (o > b.Height) o = b.Height;
            return o;
        } finally {
            if (sa != null) sa.Dispose();
            if (sb != null) sb.Dispose();
        }
    }

    public static Bitmap StitchVertical(Bitmap[] parts, int[] overlaps) {
        int w = parts[0].Width;
        long total = parts[0].Height;
        for (int i = 1; i < parts.Length; i++) total += parts[i].Height - overlaps[i - 1];
        if (total < 1 || total > int.MaxValue) {
            throw new InvalidOperationException("stitched height out of range: " + total);
        }
        Bitmap result = new Bitmap(w, (int)total, PixelFormat.Format32bppArgb);
        using (Graphics g = Graphics.FromImage(result)) {
            int y = 0;
            for (int i = 0; i < parts.Length; i++) {
                if (i > 0) y -= overlaps[i - 1];
                g.DrawImage(parts[i], new Rectangle(0, y, parts[i].Width, parts[i].Height));
                y += parts[i].Height;
            }
        }
        return result;
    }
}
"@

$raws = New-Object System.Collections.Generic.List[object]
$crops = New-Object System.Collections.Generic.List[object]
try {
  foreach ($f in $partFiles) {
    $bmp = New-Object System.Drawing.Bitmap($f)
    $raws.Add($bmp)
    if (($X + $W) -gt $bmp.Width -or ($Y + $H) -gt $bmp.Height) {
      Write-Error ("crop rect ({0},{1},{2}x{3}) exceeds image {4}x{5}: {6}" -f $X, $Y, $W, $H, $bmp.Width, $bmp.Height, $f)
      exit 1
    }
    $crops.Add([CvStitcher]::Crop($bmp, $X, $Y, $W, $H))
  }

  $n = $crops.Count
  $overlaps = New-Object int[] ([Math]::Max(1, $n - 1))
  for ($i = 1; $i -lt $n; $i++) {
    $mis = 0.0
    $overlaps[$i - 1] = [CvStitcher]::FindOverlap($crops[$i - 1], $crops[$i], [ref]$mis)
    Write-Output ("overlap{0:d2}={1} mis={2}" -f $i, $overlaps[$i - 1], $mis.ToString('F2', [System.Globalization.CultureInfo]::InvariantCulture))
  }

  # Optional per-segment crop dump (P1 segment-wise OCR); done before the stitch block
  # below because for n=1 the crop bitmap ownership moves to $result and gets disposed.
  if ($CropDir -ne '') {
    try {
      if (-not (Test-Path -LiteralPath $CropDir)) {
        New-Item -ItemType Directory -Path $CropDir -Force | Out-Null
      }
      if (-not (Test-Path -LiteralPath $CropDir -PathType Container)) {
        Write-Error "CropDir exists but is not a directory: $CropDir"; exit 1
      }
      for ($i = 0; $i -lt $n; $i++) {
        $cropPath = Join-Path $CropDir ("crop-{0:d2}.png" -f $i)
        $crops[$i].Save($cropPath, [System.Drawing.Imaging.ImageFormat]::Png)
      }
      Write-Output ("crops={0}" -f $n)
    } catch {
      Write-Error ("saving crops to ${CropDir} failed: " + $_.Exception.Message)
      exit 1
    }
  }

  if ($n -eq 1) {
    $result = $crops[0]
    $crops[0] = $null   # ownership moves to $result, do not double-dispose
  } else {
    $result = [CvStitcher]::StitchVertical($crops.ToArray(), $overlaps)
  }
  try {
    $stitchW = $result.Width
    $stitchH = $result.Height
    $result.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
  } finally {
    $result.Dispose()
  }
  Write-Output ("stitched={0}x{1}" -f $stitchW, $stitchH)
  exit 0
} finally {
  foreach ($c in $crops) { if ($c -ne $null) { $c.Dispose() } }
  foreach ($r in $raws) { if ($r -ne $null) { $r.Dispose() } }
}
