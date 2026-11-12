@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ---- 找 Python：环境变量 > 项目内 .venv > PATH
set "PY=%MNEMO_PYTHON%"
if "%PY%"=="" if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if "%PY%"=="" set "PY=python"

"%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo [x] 找不到可用的 Python。装好 Python 3.10+ 后重试，或用 MNEMO_PYTHON 指定解释器路径。
  pause
  exit /b 1
)

rem ---- 首次运行：装依赖 + 灌演示数据
if not exist "data\mnemo.db" (
  echo [*] 第一次运行，安装依赖...
  "%PY%" -m pip install -q -r requirements.txt
  if errorlevel 1 (
    echo [x] 依赖安装失败，先解决网络或换镜像：pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
  )
  echo [*] 灌入演示语料...
  "%PY%" scripts\seed.py
)

set "PORT=8731"
echo.
echo   Mnemo 记忆面板  http://127.0.0.1:%PORT%
echo   关掉这个窗口即停止服务。
echo.

start "" "http://127.0.0.1:%PORT%/"
"%PY%" -m uvicorn mnemo.api:app --host 127.0.0.1 --port %PORT%

pause
