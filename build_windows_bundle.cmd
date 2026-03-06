@echo off
setlocal

set "MODE=standalone"
set "CLEAN=0"
set "VENV_DIR=.bundle-venv"
set "SPEC_FILE=pysidedeploy.spec"
set "OUT_DIR=dist\windows"
set "MAIN_FILE=mediasorter.py"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--help" goto help
if /I "%~1"=="--onefile" (
  set "MODE=onefile"
  shift
  goto parse_args
)
if /I "%~1"=="--standalone" (
  set "MODE=standalone"
  shift
  goto parse_args
)
if /I "%~1"=="--clean" (
  set "CLEAN=1"
  shift
  goto parse_args
)
echo Unknown option: %~1
goto help_error

:args_done
cd /d "%~dp0"
set "ROOT_DIR=%CD%"
set "VENV_PY=%ROOT_DIR%\%VENV_DIR%\Scripts\python.exe"
set "DEPLOY_EXE=%ROOT_DIR%\%VENV_DIR%\Scripts\pyside6-deploy.exe"
set "DEPLOY_MAIN=%ROOT_DIR%\%MAIN_FILE%"
set "DEPLOY_SPEC=%ROOT_DIR%\%SPEC_FILE%"

if not exist "%SPEC_FILE%" (
  echo ERROR: %SPEC_FILE% not found in %CD%.
  call :beep_fail
  exit /b 1
)

if "%CLEAN%"=="1" (
  if exist "deployment" (
    echo Cleaning deployment folder...
    rmdir /s /q "deployment"
  )
  if exist "%OUT_DIR%" (
    echo Cleaning %OUT_DIR%...
    rmdir /s /q "%OUT_DIR%"
  )
)

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python launcher ^(py^) not found.
  echo Install Python 3.12+ from python.org and ensure the launcher is installed.
  call :beep_fail
  exit /b 1
)

if not exist "%VENV_PY%" (
  echo Creating bundling venv with Python 3.12...
  py -3.12 -m venv "%VENV_DIR%"
  if errorlevel 1 (
    echo ERROR: Failed to create venv with Python 3.12.
    call :beep_fail
    exit /b 1
  )
)

call "%ROOT_DIR%\%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
  echo ERROR: Failed to activate %VENV_DIR%.
  call :beep_fail
  exit /b 1
)

set "VCVARS64="
if exist "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" (
  set "VCVARS64=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
)
if not defined VCVARS64 if exist "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" (
  set "VCVARS64=C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
)

if not defined VCVARS64 goto vcvars_missing
echo Initializing MSVC toolchain via vcvars64.bat...
call "%VCVARS64%" >nul
if errorlevel 1 (
  echo WARNING: Failed to initialize Visual Studio toolchain.
)
goto vcvars_done

:vcvars_missing
echo WARNING: vcvars64.bat not found. Build may fall back to MinGW and warn about SDK.

:vcvars_done

python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
  echo ERROR: Failed to upgrade packaging tools.
  call :beep_fail
  exit /b 1
)

python -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERROR: Failed to install runtime dependencies.
  call :beep_fail
  exit /b 1
)

echo Ensuring optional AI packages are not bundled by default...
python -m pip uninstall -y torch open_clip_torch >nul 2>nul

python -m pip install "Nuitka==2.7.11"
if errorlevel 1 (
  echo ERROR: Failed to install Nuitka.
  call :beep_fail
  exit /b 1
)

if not exist "%DEPLOY_EXE%" (
  echo ERROR: pyside6-deploy.exe not found at "%DEPLOY_EXE%".
  echo Ensure PySide6 is installed successfully.
  call :beep_fail
  exit /b 1
)

where cl >nul 2>nul
if errorlevel 1 (
  echo WARNING: Microsoft C++ compiler ^(cl.exe^) not found on PATH.
  echo Nuitka may fail without Visual Studio Build Tools.
  echo Install "Desktop development with C++" workload if build fails.
)

echo.
echo Building MediaSorter bundle in %MODE% mode...
call "%DEPLOY_EXE%" "%DEPLOY_MAIN%" -c "%DEPLOY_SPEC%" --mode "%MODE%" --force --keep-deployment-files --extra-ignore-dirs=tests,__pycache__,deployment
set "DEPLOY_RC=%ERRORLEVEL%"
if not "%DEPLOY_RC%"=="0" (
  if exist "%ROOT_DIR%\deployment\mediasorter.dist\mediasorter.exe" (
    echo WARNING: pyside6-deploy returned code %DEPLOY_RC%, but deployment executable exists.
    echo Continuing because output was produced.
  ) else if exist "%ROOT_DIR%\%OUT_DIR%\MediaSorter.dist\mediasorter.exe" (
    echo WARNING: pyside6-deploy returned code %DEPLOY_RC%, but final executable exists.
    echo Continuing because output was produced.
  ) else (
    echo ERROR: pyside6-deploy failed with exit code %DEPLOY_RC%.
    call :beep_fail
    exit /b %DEPLOY_RC%
  )
)

echo.
echo Build completed.
echo Output folder: %CD%\%OUT_DIR%
if /I "%MODE%"=="standalone" (
  echo Expected bundle: %CD%\%OUT_DIR%\MediaSorter.dist
  if exist "%OUT_DIR%\MediaSorter.dist\mediasorter.exe" (
    echo.
    echo HEIC runtime check:
    "%OUT_DIR%\MediaSorter.dist\mediasorter.exe" --heic-status
    if errorlevel 1 (
      echo WARNING: HEIC runtime self-check failed.
    )
  )
) else (
  echo Expected executable: %CD%\%OUT_DIR%\MediaSorter.exe
)
call :beep_success
exit /b 0

:beep_success
powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; $files=@(\"$env:WINDIR\\Media\\Alarm10.wav\",\"$env:WINDIR\\Media\\Ring10.wav\",\"$env:WINDIR\\Media\\Windows Notify System Generic.wav\"); $f=$files | Where-Object { Test-Path $_ } | Select-Object -First 1; if($f){$p=New-Object System.Media.SoundPlayer $f; for($i=0;$i -lt 2;$i++){ $p.PlaySync(); Start-Sleep -Milliseconds 150 }} else { [System.Media.SystemSounds]::Asterisk.Play(); Start-Sleep -Milliseconds 200; [System.Media.SystemSounds]::Asterisk.Play() }" >nul 2>nul
exit /b 0

:beep_fail
powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; $files=@(\"$env:WINDIR\\Media\\Alarm10.wav\",\"$env:WINDIR\\Media\\Windows Error.wav\"); $f=$files | Where-Object { Test-Path $_ } | Select-Object -First 1; if($f){$p=New-Object System.Media.SoundPlayer $f; $p.PlaySync()} else { [System.Media.SystemSounds]::Hand.Play(); Start-Sleep -Milliseconds 250; [System.Media.SystemSounds]::Hand.Play() }" >nul 2>nul
exit /b 0

:help
echo Usage:
echo   build_windows_bundle.cmd [--standalone ^| --onefile] [--clean]
echo.
echo Options:
echo   --standalone  Build a folder-style bundle ^(default, recommended^)
echo   --onefile     Build a single .exe ^(larger startup cost^)
echo   --clean       Remove prior deployment/dist output before build
echo   --help        Show this help
echo.
echo Notes:
echo   - Requires Python 3.12 available via ^'py -3.12^'
echo   - First run installs dependencies into %VENV_DIR%
echo   - Optional AI packages ^(torch/open_clip^) are excluded from bundle by default
echo   - Standalone output is usually at dist\windows\MediaSorter.dist
exit /b 0

:help_error
echo.
echo Run with --help for supported options.
exit /b 2
