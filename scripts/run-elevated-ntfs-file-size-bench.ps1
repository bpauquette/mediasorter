$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$exePath = Join-Path $repoRoot 'native\build\ntfs_usn_probe.exe'
$outPath = Join-Path $repoRoot 'native\build\bench_file_sizes.json'
$limit = if ($args.Count -gt 0) { $args[0] } else { '10000' }

if (-not (Test-Path $exePath)) {
    throw "Missing helper executable: $exePath"
}

if (Test-Path $outPath) {
    Remove-Item $outPath -Force
}

$command = "& `"$exePath`" bench-file-sizes C:\ $limit | Out-File -FilePath `"$outPath`" -Encoding ascii"
Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $command -Verb RunAs -Wait

if (Test-Path $outPath) {
    Get-Content $outPath
} else {
    throw "File-size benchmark output was not created: $outPath"
}
