@echo off
TITLE OpEdTagger - Setup
cd /d "%~dp0"

where wt.exe >nul 2>nul
if errorlevel 1 (
    start "OpEdTagger Setup - PowerShell 7" pwsh.exe -NoLogo -NoExit -ExecutionPolicy Bypass -Command "& '%~dp0setup_windows.ps1' -NoPause"
) else (
    wt.exe pwsh.exe -NoLogo -NoExit -ExecutionPolicy Bypass -Command "& '%~dp0setup_windows.ps1' -NoPause"
)
