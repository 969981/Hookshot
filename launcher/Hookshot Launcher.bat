@echo off
rem Hookshot Launcher: updates the build tools from GitHub and rebuilds the game. Keep this file beside HookshotBuilder.exe.
cd /d "%~dp0"
if not exist "tools\python\python.exe" (
  echo The tools folder with its Python was not found beside this file.
  pause
  exit /b 1
)
"tools\python\python.exe" -I "tools\launcher\launcher.py"
if errorlevel 1 pause
