$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$exePath = Join-Path $repoRoot 'native\build\ntfs_usn_probe.exe'
$outPath = Join-Path $repoRoot 'native\build\mft_probe_output.json'

if (-not (Test-Path $exePath)) {
    throw "Missing helper executable: $exePath"
}

if (Test-Path $outPath) {
    Remove-Item $outPath -Force
}

$command = "& `"$exePath`" probe-mft-file C:\ | Out-File -FilePath `"$outPath`" -Encoding ascii"
Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $command -Verb RunAs -Wait

if (Test-Path $outPath) {
    Get-Content $outPath
} else {
    throw "MFT probe output file was not created: $outPath"
}
