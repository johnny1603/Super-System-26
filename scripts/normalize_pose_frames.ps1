<#
  Pose frames for an agent's idle animation (docs/AGENT-LIFE.md §Pose frames, 2026-11-19).

  Takes a folder of transparent PNG pose frames and the agent's CURRENT standing
  figure (the resting pose the animation returns to) and writes every frame onto
  ONE common canvas, the reference's aspect ratio, so that cross-fading between
  them never jumps:
    - feet:   the lowest opaque row lands on the reference's feet row;
    - centre: the LEGS' centre line (opaque pixels in the lower part of the
              body, where arms don't reach) lands on the reference's;
    - scale:  head-to-feet height (measured in a narrow band around the centre
              line, so a raised hand beside the head doesn't count) matches.
  Prints, per frame, how far it was off: the shift in px of the source frame,
  the scale factor, and the same relative to the set's own median (how tight
  the generation itself was).

  -CentreOnTorso <names>: frames whose STANCE differs (feet wider apart, one
  foot out) put the legs' centre somewhere the body is not. For those, the
  centre line is the torso's instead (opaque pixels at 20-45% of the height,
  arms included — fine for folded arms, wrong for a raised one). Uri's
  arms-folded frame needed it: legs-aligned, his head and torso sat 7 px (of a
  336 px canvas) to the side of the resting pose's.

  Windows PowerShell 5.1 + System.Drawing only (no Python imaging on this machine).
  Output is PNG at -OutHeight px tall (the figure is drawn 168 px tall: 448 =
  sharp up to ~2.7x screens).

  Example (אורי's set, 2026-11-22; then scripts/pack_pose_frames.js makes them light):
    powershell -File scripts/normalize_pose_frames.ps1 -Src C:\tmp\uri_raw `
      -Ref dashboard/assets/agents/uri-figure.png -Out C:\tmp\uri_norm -OutHeight 336 `
      -CentreOnTorso uri-arms-folded.png
    node scripts/pack_pose_frames.js C:\tmp\uri_norm dashboard/assets/agents/uri
#>
param(
  [Parameter(Mandatory = $true)][string]$Src,
  [Parameter(Mandatory = $true)][string]$Ref,
  [Parameter(Mandatory = $true)][string]$Out,
  [int]$OutHeight = 448,
  [int]$AlphaMin = 24,
  [string[]]$CentreOnTorso = @()
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

function Measure-Figure([string]$path, [int]$alphaMin) {
  $bmp = New-Object System.Drawing.Bitmap $path
  $w = $bmp.Width; $h = $bmp.Height
  $rect = New-Object System.Drawing.Rectangle 0, 0, $w, $h
  $data = $bmp.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::ReadOnly,
                        [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $bytes = New-Object byte[] ($data.Stride * $h)
  [System.Runtime.InteropServices.Marshal]::Copy($data.Scan0, $bytes, 0, $bytes.Length)
  $stride = $data.Stride
  $bmp.UnlockBits($data); $bmp.Dispose()
  $top = -1; $bottom = -1
  $rowHas = New-Object bool[] $h
  for ($y = 0; $y -lt $h; $y++) {
    $base = $y * $stride
    for ($x = 0; $x -lt $w; $x++) {
      if ($bytes[$base + 4 * $x + 3] -ge $alphaMin) { $rowHas[$y] = $true; break }
    }
    if ($rowHas[$y]) { if ($top -lt 0) { $top = $y }; $bottom = $y }
  }
  if ($top -lt 0) { throw "no opaque pixels in $path" }
  # the legs' centre line: opaque pixels between 70% and 93% of the figure's height
  $sum = 0.0; $n = 0
  $y0 = [int]($top + 0.70 * ($bottom - $top)); $y1 = [int]($top + 0.93 * ($bottom - $top))
  for ($y = $y0; $y -le $y1; $y++) {
    $base = $y * $stride
    for ($x = 0; $x -lt $w; $x++) { if ($bytes[$base + 4 * $x + 3] -ge $alphaMin) { $sum += $x; $n++ } }
  }
  $cx = $sum / [math]::Max($n, 1)
  # the torso's centre line (for -CentreOnTorso): every opaque pixel at 20-45% of the height
  $sum = 0.0; $n = 0
  $y0 = [int]($top + 0.20 * ($bottom - $top)); $y1 = [int]($top + 0.45 * ($bottom - $top))
  for ($y = $y0; $y -le $y1; $y++) {
    $base = $y * $stride
    for ($x = 0; $x -lt $w; $x++) { if ($bytes[$base + 4 * $x + 3] -ge $alphaMin) { $sum += $x; $n++ } }
  }
  $tx = $sum / [math]::Max($n, 1)
  # the head's top inside a band of +-12% width around that line
  $band = [int](0.12 * $w); $head = -1
  for ($y = $top; $y -le $bottom -and $head -lt 0; $y++) {
    $base = $y * $stride
    for ($x = [math]::Max(0, [int]($cx - $band)); $x -le [math]::Min($w - 1, [int]($cx + $band)); $x++) {
      if ($bytes[$base + 4 * $x + 3] -ge $alphaMin) { $head = $y; break }
    }
  }
  return [pscustomobject]@{ W = $w; H = $h; Feet = $bottom; Head = $head; Cx = $cx; Tx = $tx; Height = $bottom - $head }
}

function Median([double[]]$v) { $s = $v | Sort-Object; $m = [int][math]::Floor($s.Count / 2); if ($s.Count % 2) { $s[$m] } else { ($s[$m - 1] + $s[$m]) / 2 } }

$refM = Measure-Figure (Resolve-Path $Ref) $AlphaMin
$frames = Get-ChildItem -Path $Src -Filter *.png | Sort-Object Name
if (-not $frames) { throw "no PNG frames in $Src" }
$measured = @{}
foreach ($f in $frames) { $measured[$f.Name] = Measure-Figure $f.FullName $AlphaMin }

$medH = Median ($measured.Values | ForEach-Object { [double]$_.Height })
$medCx = Median ($measured.Values | ForEach-Object { [double]$_.Cx / $_.W })
$medFeet = Median ($measured.Values | ForEach-Object { [double]$_.Feet / $_.H })

New-Item -ItemType Directory -Force $Out | Out-Null
$outW = [int][math]::Round($OutHeight * $refM.W / $refM.H)
$k = $OutHeight / $refM.H                                   # reference px -> output px
"reference {0}x{1}: feet {2}, head {3}, centre {4:N1}, height {5}" -f $refM.W, $refM.H, $refM.Feet, $refM.Head, $refM.Cx, $refM.Height
"output canvas {0}x{1}" -f $outW, $OutHeight
""
"{0,-30} {1,10} {2,9} {3,9} {4,9} | vs set median: {5,8} {6,8} {7,8}" -f 'frame', 'source', 'scale', 'dx px', 'dy px', 'height%', 'centre px', 'feet px'
$total = 0
foreach ($f in $frames) {
  $m = $measured[$f.Name]
  $scale = $refM.Height / $m.Height                        # source px -> reference px
  $s = $scale * $k                                         # source px -> output px
  $torso = $CentreOnTorso -contains $f.Name
  $refC = if ($torso) { $refM.Tx } else { $refM.Cx }       # the centre line this frame is aligned on
  $frmC = if ($torso) { $m.Tx } else { $m.Cx }
  $dx = $refC * $k - $frmC * $s                            # where the source's (0,0) lands
  $dy = $refM.Feet * $k - $m.Feet * $s
  # NOT $src: PowerShell names ignore case, so that would overwrite the [string]$Src parameter
  $frameBmp = New-Object System.Drawing.Bitmap $f.FullName
  $dst = New-Object System.Drawing.Bitmap $outW, $OutHeight, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $g = [System.Drawing.Graphics]::FromImage($dst)
  $g.Clear([System.Drawing.Color]::Transparent)
  $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  $g.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
  $g.DrawImage($frameBmp, [single]$dx, [single]$dy, [single]($m.W * $s), [single]($m.H * $s))
  $g.Dispose(); $frameBmp.Dispose()
  $target = Join-Path $Out $f.Name
  $dst.Save($target, [System.Drawing.Imaging.ImageFormat]::Png); $dst.Dispose()
  $total += (Get-Item $target).Length
  # the correction in SOURCE pixels: how far this frame's figure sat from where the reference puts it
  $shiftX = $refC / $scale - $frmC * 1.0
  $shiftY = $refM.Feet / $scale - $m.Feet * 1.0
  "{0,-30} {1,10} {2,9:N3} {3,9:N1} {4,9:N1} | {5,8:N1} {6,8:N1} {7,8:N1}{8}" -f $f.Name, ("{0}x{1}" -f $m.W, $m.H), $scale,
    $shiftX, $shiftY, (100.0 * ($m.Height - $medH) / $medH), ($m.Cx - $medCx * $m.W), ($m.Feet - $medFeet * $m.H),
    $(if ($torso) { '  (centred on the torso)' } else { '' })
}
""
"written: {0} frames, {1:N0} KB in all" -f $frames.Count, ($total / 1KB)
