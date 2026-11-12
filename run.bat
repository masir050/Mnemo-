@echo off
rem Mnemo launcher.
rem All logic and messages live in scripts\bootstrap.py (UTF-8 safe) so this file
rem stays ASCII-only: a Chinese .bat saved as UTF-8 shows mojibake under GBK cmd.
setlocal
cd /d "%~dp0"

set "PY=%MNEMO_PYTHON%"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY set "PY=python"

"%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo.
  echo [x] Python not found.
  echo     Install Python 3.10+ and tick "Add python.exe to PATH",
  echo     or set MNEMO_PYTHON to the full path of python.exe
  echo.
  pause
  exit /b 1
)

"%PY%" "scripts\bootstrap.py" %*
if errorlevel 1 pause
