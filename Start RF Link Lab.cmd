@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Install the project environment using the Windows commands in README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m rf_link_lab gui
if errorlevel 1 pause
