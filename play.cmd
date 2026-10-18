@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -File "%~dp0run_play.ps1"
pause
