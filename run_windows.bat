@echo off
TITLE OpEdTagger - Anime Chapter Detector ^& Ingester
cd /d "%~dp0"

where wt.exe >nul 2>nul
if errorlevel 1 (
    start "OpEdTagger - PowerShell 7" pwsh.exe -NoLogo -NoExit -ExecutionPolicy Bypass -Command "& '%~dp0run_windows.ps1'"
) else (
    wt.exe pwsh.exe -NoLogo -NoExit -ExecutionPolicy Bypass -Command "& '%~dp0run_windows.ps1'"
)
