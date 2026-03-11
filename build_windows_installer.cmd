@echo off
setlocal

set "CLEAN=0"
set "PAYMENT_URL="
set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--help" goto help
if /I "%~1"=="--clean" (
  set "CLEAN=1"
  shift
  goto parse_args
)
if /I "%~1"=="--payment-url" (
  if "%~2"=="" (
    echo ERROR: --payment-url requires a value
    goto help_error
  )
  set "PAYMENT_URL=%~2"
  shift
  shift
  goto parse_args
)
echo Unknown option: %~1
goto help_error

:args_done
if not defined PAYMENT_URL if defined MEDIASORTER_SUPPORT_URL set "PAYMENT_URL=%MEDIASORTER_SUPPORT_URL%"
if not defined PAYMENT_URL if defined MEDIASORTER_PAYMENT_URL set "PAYMENT_URL=%MEDIASORTER_PAYMENT_URL%"
set "NSI_FILE=installer\windows\MediaSorter.nsi"
set "BUNDLE_EXE=dist\windows\MediaSorter.dist\mediasorter.exe"
set "OUT_EXE=dist\windows\MediaSorterSetup.exe"
set "MAKENSIS_EXE="

if not exist "%NSI_FILE%" (
  echo ERROR: NSIS script not found: %NSI_FILE%
  exit /b 1
)

if "%CLEAN%"=="1" (
  if exist "%OUT_EXE%" (
    echo Cleaning previous installer...
    del /f /q "%OUT_EXE%" >nul 2>nul
  )
  call "%ROOT_DIR%build_windows_bundle.cmd" --clean --standalone
) else (
  call "%ROOT_DIR%build_windows_bundle.cmd" --standalone
)
if errorlevel 1 (
  echo ERROR: Failed to produce standalone bundle.
  exit /b 1
)

if not exist "%BUNDLE_EXE%" (
  echo ERROR: Expected bundle executable missing: %BUNDLE_EXE%
  exit /b 1
)

where makensis >nul 2>nul
if not errorlevel 1 (
  for /f "delims=" %%I in ('where makensis') do (
    if not defined MAKENSIS_EXE set "MAKENSIS_EXE=%%I"
  )
)
if not defined MAKENSIS_EXE if exist "%ProgramFiles(x86)%\NSIS\makensis.exe" (
  set "MAKENSIS_EXE=%ProgramFiles(x86)%\NSIS\makensis.exe"
)
if not defined MAKENSIS_EXE if exist "%ProgramFiles%\NSIS\makensis.exe" (
  set "MAKENSIS_EXE=%ProgramFiles%\NSIS\makensis.exe"
)

if not defined MAKENSIS_EXE (
  echo ERROR: NSIS compiler ^(makensis.exe^) not found.
  echo Install NSIS from https://nsis.sourceforge.io/Download and rerun.
  exit /b 1
)

echo.
echo Building NSIS installer with:
echo   %MAKENSIS_EXE%
if defined PAYMENT_URL echo   PAYMENT_URL=%PAYMENT_URL%
echo.
if defined PAYMENT_URL (
  "%MAKENSIS_EXE%" /V2 /DPAYMENT_URL="%PAYMENT_URL%" "%NSI_FILE%"
) else (
  "%MAKENSIS_EXE%" /V2 "%NSI_FILE%"
)
if errorlevel 1 (
  echo ERROR: makensis failed.
  exit /b 1
)

if not exist "%OUT_EXE%" (
  echo ERROR: Installer output not found: %OUT_EXE%
  exit /b 1
)

echo.
echo Installer build completed.
echo Output: %CD%\%OUT_EXE%
exit /b 0

:help
echo Usage:
echo   build_windows_installer.cmd [--clean] [--payment-url "https://..."]
echo.
echo Options:
echo   --clean   Clean prior build output before building bundle + installer
echo   --payment-url  Hosted checkout URL ^(Gumroad/Stripe/PayPal/etc^)
echo   --help    Show this help
echo.
echo Env fallback:
echo   MEDIASORTER_SUPPORT_URL or MEDIASORTER_PAYMENT_URL
echo.
echo Notes:
echo   - Requires NSIS ^(makensis.exe^) installed
echo   - Produces dist\windows\MediaSorterSetup.exe
exit /b 0

:help_error
echo.
echo Run with --help for supported options.
exit /b 2
