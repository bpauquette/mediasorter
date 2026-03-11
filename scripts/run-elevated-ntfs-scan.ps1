$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$exePath = Join-Path $repoRoot 'native\build\ntfs_usn_probe.exe'
$jsonPath = Join-Path $repoRoot 'native\build\ntfs_scan_output.json'
$streamPath = Join-Path $repoRoot 'native\build\ntfs_stream.tsv'

if (-not (Test-Path $exePath)) {
    throw "Missing helper executable: $exePath"
}

if (Test-Path $jsonPath) {
    Remove-Item $jsonPath -Force
}

$command = "& `"$exePath`" scan-ntfs C:\ `"$streamPath`" | Out-File -FilePath `"$jsonPath`" -Encoding ascii"
Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $command -Verb RunAs -Wait

if (Test-Path $jsonPath) {
    Get-Content $jsonPath
} else {
    throw "NTFS scan output file was not created: $jsonPath"
}
