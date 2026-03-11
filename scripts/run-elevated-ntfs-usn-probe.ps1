$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$exePath = Join-Path $repoRoot 'native\build\ntfs_usn_probe.exe'
$outPath = Join-Path $repoRoot 'native\build\ntfs_usn_probe_output.json'

if (-not (Test-Path $exePath)) {
    throw "Missing helper executable: $exePath"
}

$command = "& `"$exePath`" probe C:\ | Out-File -FilePath `"$outPath`" -Encoding ascii"
Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $command -Verb RunAs -Wait

if (Test-Path $outPath) {
    Get-Content $outPath
} else {
    throw "Probe output file was not created: $outPath"
}
