@echo off
TITLE OpEdTagger - Development
cd /d "%~dp0"

where wt.exe >nul 2>nul
if errorlevel 1 (
    start "OpEdTagger Development - PowerShell 7" pwsh.exe -NoLogo -NoExit -ExecutionPolicy Bypass -File "%~dp0dev_windows.ps1"
) else (
    wt.exe pwsh.exe -NoLogo -NoExit -ExecutionPolicy Bypass -Command "& '%~dp0dev_windows.ps1'"
)
