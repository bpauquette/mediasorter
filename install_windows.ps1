param(
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[MediaSorter] $Message" -ForegroundColor Cyan
}

function Resolve-Python312 {
    # Prefer the Python launcher because the project uses py -3.12 elsewhere.
    try {
        $exe = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) {
            return ($exe | Select-Object -First 1).Trim()
        }
    } catch {
    }
    return $null
}

function Install-Python312-ViaWinget {
    try {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            return $false
        }
        Write-Step "Installing Python 3.12 via winget..."
        & winget install --id Python.Python.3.12 --exact --source winget --silent --accept-package-agreements --accept-source-agreements
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Install-Python312-ViaPythonOrg {
    $url = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
    $installer = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
    Write-Step "Downloading Python 3.12 installer from python.org..."
    Invoke-WebRequest -Uri $url -OutFile $installer
    Write-Step "Running Python installer (per-user, silent)..."
    & $installer /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1 Include_test=0
    if ($LASTEXITCODE -ne 0) {
        throw "Python installer failed with exit code $LASTEXITCODE."
    }
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

Write-Step "Starting Windows installer in $projectRoot"

$python312 = Resolve-Python312
if (-not $python312) {
    if (-not (Install-Python312-ViaWinget)) {
        Write-Step "winget install failed or unavailable. Falling back to python.org installer..."
        Install-Python312-ViaPythonOrg
    }
    $python312 = Resolve-Python312
    if (-not $python312) {
        throw "Python 3.12 was not detected after installation."
    }
}

Write-Step "Using Python: $python312"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Step "Creating virtual environment (.venv)..."
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create .venv."
    }
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Expected venv Python not found at $venvPython"
}

Write-Step "Upgrading pip/setuptools/wheel..."
& $venvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip/setuptools/wheel."
}

Write-Step "Installing project dependencies..."
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install requirements."
}

Write-Host ""
Write-Host "Optional recommended tools (not part of MediaSorter):" -ForegroundColor Yellow
Write-Host "  HandBrake:   https://handbrake.fr/"
Write-Host "  SequoiaView: https://www.sequoiaview.com/"
Write-Host ""

if (-not $NoLaunch) {
    Write-Step "Launching MediaSorter..."
    & $venvPython mediasorter.py
    exit $LASTEXITCODE
}

Write-Step "Install complete. Launch any time with .\run_gui.cmd"
exit 0

