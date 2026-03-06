@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\\Scripts\\python.exe" (
  echo Creating venv...
  py -3.12 -m venv .venv || exit /b 1
  ".venv\\Scripts\\python.exe" -m pip install --upgrade pip || exit /b 1
)

echo Syncing runtime dependencies...
".venv\\Scripts\\python.exe" -m pip install -r requirements.txt || exit /b 1

".venv\\Scripts\\python.exe" mediasorter.py %*
