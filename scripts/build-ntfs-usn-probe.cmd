@echo off
setlocal

set "REPO_DIR=%~dp0.."
set "SRC=%REPO_DIR%\native\ntfs_usn_probe.cpp"
set "OUT_DIR=%REPO_DIR%\native\build"
set "OUT_EXE=%OUT_DIR%\ntfs_usn_probe.exe"

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VCVARS%" set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"

if not exist "%VCVARS%" (
  echo Could not find vcvars64.bat
  exit /b 1
)

call "%VCVARS%" >nul 2>nul
if errorlevel 1 (
  echo Failed to initialize MSVC build environment.
  exit /b 1
)

cl /nologo /std:c++17 /EHsc /W4 /O2 /Fe:"%OUT_EXE%" "%SRC%" Advapi32.lib
exit /b %errorlevel%
