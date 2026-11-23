[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$FrontendIndex = Join-Path $ProjectRoot 'frontend\dist\index.html'

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    Write-Host 'Python environment not found. Run .\setup_windows.ps1 first.' -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $FrontendIndex -PathType Leaf)) {
    Write-Host 'Frontend build not found. Run .\setup_windows.ps1 first.' -ForegroundColor Red
    exit 1
}

Write-Host 'Starting OpEdTagger...' -ForegroundColor Green
Write-Host 'Frontend/API: http://127.0.0.1:8000'
Write-Host 'API docs:     http://127.0.0.1:8000/docs'
Write-Host 'Stop the server with Ctrl+C.'
Write-Host ''

& $VenvPython -m backend
$ExitCode = $LASTEXITCODE
if ($ExitCode -ne 0) {
    Write-Host "OpEdTagger stopped with exit code $ExitCode." -ForegroundColor Red
}
exit $ExitCode
