# OCR whole frames and report where each line of text sits.
#
# Used to locate the combat log automatically: the caller keeps the lines that
# look like combat and takes their bounding box as the crop, so the tool works
# on a screen layout other than the one it was written against.
#
# Output: one line per detected text line, "x y w h<TAB>text".
param(
  [Parameter(Mandatory=$true)][string]$Dir
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
  Where-Object {
    $_.Name -eq 'AsTask' -and
    $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
  })[0]

function Await($op, $type) {
  $t = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op))
  $t.Wait(-1) | Out-Null
  $t.Result
}

[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]            | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile,Windows.Foundation,ContentType=WindowsRuntime]            | Out-Null

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { Write-Error "no OCR engine available"; exit 1 }

Get-ChildItem -Path $Dir -Filter *.png | Sort-Object Name | ForEach-Object {
  Write-Output "##FILE $($_.Name)"
  try {
    $file    = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($_.FullName)) ([Windows.Storage.StorageFile])
    $stream  = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap  = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result  = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    foreach ($line in $result.Lines) {
      # A line carries no rectangle of its own, so take the span of its words.
      $x1 = [double]::MaxValue; $y1 = [double]::MaxValue
      $x2 = 0.0; $y2 = 0.0
      foreach ($w in $line.Words) {
        $r = $w.BoundingRect
        if ($r.X -lt $x1) { $x1 = $r.X }
        if ($r.Y -lt $y1) { $y1 = $r.Y }
        if (($r.X + $r.Width)  -gt $x2) { $x2 = $r.X + $r.Width }
        if (($r.Y + $r.Height) -gt $y2) { $y2 = $r.Y + $r.Height }
      }
      if ($line.Words.Count -gt 0) {
        "{0} {1} {2} {3}`t{4}" -f [int]$x1, [int]$y1, [int]($x2-$x1), [int]($y2-$y1), $line.Text
      }
    }
    $stream.Dispose()
    $bitmap.Dispose()
  } catch {
    # A frame that fails to decode tells us nothing; the others will do.
  }
}
