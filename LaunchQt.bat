@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" qt_app.py
) else (
  python qt_app.py
)
if errorlevel 1 pause
endlocal
