@echo off
TITLE OpEdTagger - Anime Chapter Detector & Ingester
cd /d "%~dp0"
wt.exe pwsh.exe -NoLogo -NoExit -Command "python '%~dp0main.py'"