[CmdletBinding()]
param(
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Stop-Setup([string]$Message, [int]$Code = 1) {
    Write-Host "`nSETUP FAILED: $Message" -ForegroundColor Red
    if (-not $NoPause -and [Environment]::UserInteractive) {
        Write-Host "`nPress Enter to close this window..." -ForegroundColor Yellow
        [void](Read-Host)
    }
    exit $Code
}

function Get-CommandPath($Command) {
    if ($null -eq $Command) { return $null }
    if ($Command.Source) { return $Command.Source }
    if ($Command.Path) { return $Command.Path }
    return $null
}

Write-Host 'OpEdTagger - Windows setup' -ForegroundColor Green
Write-Host "Project: $ProjectRoot"

# Python launcher and version
Write-Step 'Checking Python 3.11+'
$PyCommand = Get-Command 'py.exe' -CommandType Application -ErrorAction SilentlyContinue
if (-not $PyCommand) {
    Stop-Setup 'Python Launcher (py.exe) was not found. Install Python 3.11+ from python.org and enable the Python Launcher.'
}
$PyPath = Get-CommandPath $PyCommand
& $PyPath -3 -c "import sys; print('Python', sys.version.split()[0]); raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    Stop-Setup 'Python 3.11 or newer is required. The Python Launcher did not find a compatible installation.'
}

# Resolve Node.js and npm using the current session, Windows registry PATH,
# standard installation directories and common Node version managers.
Write-Step 'Locating Node.js and npm'

# Refresh environment variables from Windows registry so a terminal opened
# before the Node installation/PATH update does not retain a stale PATH.
$VariableNames = @('NVM_HOME', 'NVM_SYMLINK', 'VOLTA_HOME', 'FNM_DIR', 'FNM_MULTISHELL_PATH', 'SCOOP', 'ChocolateyInstall')
foreach ($Name in $VariableNames) {
    $Value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($Value)) { $Value = [Environment]::GetEnvironmentVariable($Name, 'User') }
    if ([string]::IsNullOrWhiteSpace($Value)) { $Value = [Environment]::GetEnvironmentVariable($Name, 'Machine') }
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
    }
}

$PathEntries = @()
$PathSources = @(
    [Environment]::GetEnvironmentVariable('Path', 'Machine'),
    [Environment]::GetEnvironmentVariable('Path', 'User'),
    $env:PATH
)
foreach ($Source in $PathSources) {
    if ([string]::IsNullOrWhiteSpace($Source)) { continue }
    foreach ($Entry in ($Source -split ';')) {
        $Entry = $Entry.Trim().Trim('"')
        if ([string]::IsNullOrWhiteSpace($Entry)) { continue }
        $Expanded = [Environment]::ExpandEnvironmentVariables($Entry)
        if (Test-Path -LiteralPath $Expanded -PathType Container) { $PathEntries += $Expanded }
    }
}
$env:PATH = ($PathEntries | Select-Object -Unique) -join ';'

# Locate node.exe via PowerShell command resolution, where.exe, PATH and known managers.
$NodeCandidates = @()
foreach ($CommandName in @('node.exe', 'node')) {
    $Commands = Get-Command $CommandName -CommandType Application -All -ErrorAction SilentlyContinue
    foreach ($Command in $Commands) {
        $Candidate = if ($Command.Source) { $Command.Source } else { $Command.Path }
        if (-not [string]::IsNullOrWhiteSpace($Candidate)) { $NodeCandidates += $Candidate }
    }
}
$WhereExe = Join-Path $env:SystemRoot 'System32\where.exe'
if (Test-Path -LiteralPath $WhereExe) {
    $NodeCandidates += (& $WhereExe node.exe 2>$null)
}

$NodeDirectories = @()
if ($env:ProgramFiles) { $NodeDirectories += (Join-Path $env:ProgramFiles 'nodejs') }
if (${env:ProgramFiles(x86)}) { $NodeDirectories += (Join-Path ${env:ProgramFiles(x86)} 'nodejs') }
if ($env:LOCALAPPDATA) {
    $NodeDirectories += (Join-Path $env:LOCALAPPDATA 'Programs\nodejs')
    $NodeDirectories += (Join-Path $env:LOCALAPPDATA 'Volta\bin')
    $NodeDirectories += (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links')
}
if ($env:NVM_SYMLINK) { $NodeDirectories += $env:NVM_SYMLINK }
if ($env:VOLTA_HOME) { $NodeDirectories += (Join-Path $env:VOLTA_HOME 'bin') }
if ($env:FNM_MULTISHELL_PATH) { $NodeDirectories += $env:FNM_MULTISHELL_PATH }
if ($env:SCOOP) {
    $NodeDirectories += (Join-Path $env:SCOOP 'apps\nodejs\current')
    $NodeDirectories += (Join-Path $env:SCOOP 'shims')
}
if ($env:USERPROFILE) { $NodeDirectories += (Join-Path $env:USERPROFILE 'scoop\apps\nodejs\current') }
if ($env:ChocolateyInstall) { $NodeDirectories += (Join-Path $env:ChocolateyInstall 'bin') }
$NodeDirectories += ($env:PATH -split ';')

foreach ($Directory in $NodeDirectories) {
    if ([string]::IsNullOrWhiteSpace($Directory)) { continue }
    $Candidate = Join-Path $Directory 'node.exe'
    if (Test-Path -LiteralPath $Candidate -PathType Leaf) { $NodeCandidates += $Candidate }
}

$NodeExe = $null
foreach ($Candidate in $NodeCandidates) {
    if (-not [string]::IsNullOrWhiteSpace($Candidate) -and (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        $NodeExe = $Candidate
        break
    }
}
if (-not $NodeExe) {
    Write-Host "`nNode.js diagnostic:" -ForegroundColor Yellow
    Write-Host "PowerShell: $($PSVersionTable.PSVersion)"
    Write-Host 'PATH entries containing node-related paths:'
    $env:PATH -split ';' | Where-Object { $_ -match 'node|nvm|volta|fnm|scoop' } | ForEach-Object { Write-Host "  $_" }
    Stop-Setup 'Node.js was not found. Check the installation and PATH.'
}

$NodeOutput = (& $NodeExe --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { Stop-Setup "Node.js failed to execute: $NodeExe" }
$NodeVersionMatch = [regex]::Match($NodeOutput, '^v?(\d+)\.')
if (-not $NodeVersionMatch.Success) { Stop-Setup "Could not identify Node.js version. Output: $NodeOutput" }
$NodeMajor = [int]$NodeVersionMatch.Groups[1].Value
$NodeDirectory = Split-Path -Parent $NodeExe
Write-Host "Node.js: $NodeOutput ($NodeExe)" -ForegroundColor Green
if ($NodeMajor -lt 20) { Stop-Setup "Node.js 20+ is required. Detected $NodeOutput." }

# npm.ps1 may resolve in PowerShell while npm.cmd is the executable needed by
# child processes. Prefer the npm.cmd beside the selected node.exe.
$NpmCandidates = @((Join-Path $NodeDirectory 'npm.cmd'))
$NpmCommands = Get-Command 'npm.cmd' -CommandType Application -All -ErrorAction SilentlyContinue
foreach ($Command in $NpmCommands) {
    $Candidate = if ($Command.Source) { $Command.Source } else { $Command.Path }
    if ($Candidate) { $NpmCandidates += $Candidate }
}
if (Test-Path -LiteralPath $WhereExe) { $NpmCandidates += (& $WhereExe npm.cmd 2>$null) }
$NpmPowerShellCommand = Get-Command 'npm.ps1' -CommandType ExternalScript -ErrorAction SilentlyContinue
if ($NpmPowerShellCommand) {
    $NpmPowerShellPath = if ($NpmPowerShellCommand.Source) { $NpmPowerShellCommand.Source } else { $NpmPowerShellCommand.Path }
    if ($NpmPowerShellPath) {
        $NpmCandidates += (Join-Path (Split-Path -Parent $NpmPowerShellPath) 'npm.cmd')
    }
}

$NpmCmd = $null
foreach ($Candidate in $NpmCandidates) {
    if (-not [string]::IsNullOrWhiteSpace($Candidate) -and (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        $NpmCmd = $Candidate
        break
    }
}
$NpmCli = Join-Path $NodeDirectory 'node_modules\npm\bin\npm-cli.js'
$UseNpmCli = (-not $NpmCmd -and (Test-Path -LiteralPath $NpmCli -PathType Leaf))
if (-not $NpmCmd -and -not $UseNpmCli) {
    Stop-Setup "Node.js $NodeOutput was found, but npm could not be located. Repair the Node.js installation and ensure npm is installed."
}

$NpmDirectory = if ($NpmCmd) { Split-Path -Parent $NpmCmd } else { $NodeDirectory }
$env:PATH = ((@($NodeDirectory, $NpmDirectory) + ($env:PATH -split ';') |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique) -join ';')

if ($UseNpmCli) {
    $NpmVersion = (& $NodeExe $NpmCli --version 2>&1 | Out-String).Trim()
} else {
    $NpmVersion = (& $NpmCmd --version 2>&1 | Out-String).Trim()
}
if ($LASTEXITCODE -ne 0) { Stop-Setup "npm was found but failed to execute. Output: $NpmVersion" }
Write-Host "npm: $NpmVersion" -ForegroundColor Green

# Create / reuse virtual environment and install Python dependencies.
Write-Step 'Preparing Python environment'
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    & $PyPath -3 -m venv (Join-Path $ProjectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { Stop-Setup 'Could not create the .venv virtual environment.' }
}
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    Stop-Setup 'The virtual environment was not created. Check Python permissions and installation.'
}

& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Stop-Setup 'Updating pip failed.' }
& $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Stop-Setup 'Installing Python dependencies failed. Review the pip output above.' }

# Install exact frontend dependencies from package-lock.json and build.
Write-Step 'Installing frontend dependencies with npm ci'
$FrontendDirectory = Join-Path $ProjectRoot 'frontend'
Push-Location -LiteralPath $FrontendDirectory
try {
    if ($UseNpmCli) {
        & $NodeExe $NpmCli ci
        if ($LASTEXITCODE -ne 0) { Stop-Setup 'npm ci failed. Review the npm output above.' }
        Write-Step 'Building frontend with Vite'
        & $NodeExe $NpmCli run build
    } else {
        & $NpmCmd ci
        if ($LASTEXITCODE -ne 0) { Stop-Setup 'npm ci failed. Review the npm output above.' }
        Write-Step 'Building frontend with Vite'
        & $NpmCmd run build
    }
    if ($LASTEXITCODE -ne 0) { Stop-Setup 'The Vite frontend build failed. Review the output above.' }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath (Join-Path $FrontendDirectory 'dist\index.html') -PathType Leaf)) {
    Stop-Setup 'The build command ended without producing frontend\dist\index.html.'
}

Write-Host "`nSETUP COMPLETE" -ForegroundColor Green
Write-Host 'Start OpEdTagger with: .\run_windows.ps1'
Write-Host 'Or double-click run_windows.bat to open it in Windows Terminal with PowerShell 7.'
if (-not $NoPause -and [Environment]::UserInteractive) {
    Write-Host "`nPress Enter to close this window..." -ForegroundColor Yellow
    [void](Read-Host)
}
exit 0
