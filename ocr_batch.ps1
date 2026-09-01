# OCR every PNG in a folder using the engine built into Windows.
#
# The per-image script spawns a PowerShell process per frame, and that startup
# dominates the runtime -- roughly a second each, against OCR itself taking a
# fraction of that. Handling the whole folder in one process removes that tax
# entirely and cuts a typical clip from minutes to under one.
#
# Output: one line per detected text line, with "##FILE <name>" separating
# each image's results so the caller can attribute lines to frames.
param(
  [Parameter(Mandatory=$true)][string]$Dir,
  # Several of these run at once over disjoint slices of the same folder.
  [int]$Skip = 0,
  [int]$Take = 0
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

$files = Get-ChildItem -Path $Dir -Filter *.png | Sort-Object Name
if ($Take -gt 0) { $files = @($files | Select-Object -Skip $Skip -First $Take) }

$files | ForEach-Object {
  $name = $_.Name
  Write-Output "##FILE $name"
  try {
    $file    = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($_.FullName)) ([Windows.Storage.StorageFile])
    $stream  = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap  = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result  = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    foreach ($line in $result.Lines) { $line.Text }
    $stream.Dispose()
    $bitmap.Dispose()
  } catch {
    # A frame that fails to decode contributes little on its own, since every
    # log line is read from many frames. Skip it and keep going.
  }
}
