@echo off
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -File "%~dp0scripts\run_windows.ps1"
if errorlevel 1 (
  echo.
  echo Launch failed. Read the error above and WINDOWS.md.
  pause
  exit /b 1
)
