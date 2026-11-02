@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.10 -m venv .venv
  if errorlevel 1 (
    echo 请安装 Python 3.10 并启用 Python Launcher，或手动创建 .venv。
    pause
    exit /b 1
  )
)
if exist "wheels" (
  ".venv\Scripts\python.exe" -m pip install --no-index --find-links=wheels -r requirements.txt
) else (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
if errorlevel 1 (
  echo 依赖安装未完成，请检查网络后重试。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py --check
if errorlevel 1 (
  pause
  exit /b 1
)
exit /b 0
