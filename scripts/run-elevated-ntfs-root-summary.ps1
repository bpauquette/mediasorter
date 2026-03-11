$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$exePath = Join-Path $repoRoot 'native\build\ntfs_usn_probe.exe'
$jsonPath = Join-Path $repoRoot 'native\build\ntfs_root_summary_output.json'
$summaryPath = Join-Path $repoRoot 'native\build\ntfs_root_summary.tsv'

if (-not (Test-Path $exePath)) {
    throw "Missing helper executable: $exePath"
}

if (Test-Path $jsonPath) {
    Remove-Item $jsonPath -Force
}

$command = "& `"$exePath`" scan-ntfs-root-summary C:\ `"$summaryPath`" | Out-File -FilePath `"$jsonPath`" -Encoding ascii"
Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $command -Verb RunAs -Wait

if (Test-Path $jsonPath) {
    Get-Content $jsonPath
} else {
    throw "NTFS root summary output file was not created: $jsonPath"
}
