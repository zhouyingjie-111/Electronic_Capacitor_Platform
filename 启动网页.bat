@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call "%~dp0安装依赖.bat"
  if errorlevel 1 exit /b 1
)
if not exist ".venv\Scripts\python.exe" exit /b 1
".venv\Scripts\python.exe" run.py
if errorlevel 1 pause
