[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendDirectory = Join-Path $ProjectRoot 'frontend'
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    Write-Host 'Python environment not found. Run .\setup_windows.ps1 first.' -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath (Join-Path $FrontendDirectory 'node_modules') -PathType Container)) {
    Write-Host 'Frontend dependencies not found. Run .\setup_windows.ps1 first.' -ForegroundColor Red
    exit 1
}

$NpmCommand = Get-Command 'npm.cmd' -CommandType Application -ErrorAction SilentlyContinue
$NpmCmd = if ($NpmCommand) { $NpmCommand.Source } else { $null }
if (-not $NpmCmd) {
    $NodeCommand = Get-Command 'node.exe' -CommandType Application -ErrorAction SilentlyContinue
    if ($NodeCommand) {
        $Candidate = Join-Path (Split-Path -Parent $NodeCommand.Source) 'npm.cmd'
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) { $NpmCmd = $Candidate }
    }
}
if (-not $NpmCmd -or -not (Test-Path -LiteralPath $NpmCmd -PathType Leaf)) {
    Write-Host 'npm.cmd could not be located. Run .\setup_windows.ps1 and review Node.js/npm installation.' -ForegroundColor Red
    exit 1
}

# Vite runs in a separate Windows Terminal tab with PowerShell 7 when available.
# The backend stays in the current terminal.
$FrontendLiteral = $FrontendDirectory.Replace("'", "''")
$NpmLiteral = $NpmCmd.Replace("'", "''")
$FrontendCommand = "Set-Location -LiteralPath '$FrontendLiteral'; & '$NpmLiteral' run dev"
$EncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($FrontendCommand))
$WindowsTerminal = Get-Command 'wt.exe' -CommandType Application -ErrorAction SilentlyContinue
$PwshCommand = Get-Command 'pwsh.exe' -CommandType Application -ErrorAction SilentlyContinue
if ($WindowsTerminal) {
    Start-Process -FilePath $WindowsTerminal.Source -ArgumentList "new-tab pwsh.exe -NoLogo -NoExit -EncodedCommand $EncodedCommand" -WorkingDirectory $FrontendDirectory
} elseif ($PwshCommand) {
    Start-Process -FilePath $PwshCommand.Source -ArgumentList "-NoLogo -NoProfile -ExecutionPolicy Bypass -NoExit -EncodedCommand $EncodedCommand" -WorkingDirectory $FrontendDirectory
} else {
    Write-Host 'PowerShell 7 (pwsh.exe) was not found for the Vite process.' -ForegroundColor Red
    exit 1
}

Write-Host 'Starting OpEdTagger backend...' -ForegroundColor Green
Write-Host 'Frontend: http://127.0.0.1:5173'
Write-Host 'Backend:  http://127.0.0.1:8000'
Write-Host 'Stop the backend with Ctrl+C; stop Vite in its Windows Terminal tab separately.'
& $VenvPython -m backend
exit $LASTEXITCODE
