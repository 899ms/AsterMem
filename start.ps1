<#
    AsterMem one-command launcher (Windows / PowerShell)

    Background: Windows users don't have bash, and the ./venv/bin/python path from
    README doesn't work (Windows puts the interpreter under venv\Scripts\).
    Design intent: A four-step idempotent flow aligned with start.sh — create venv,
    install Python deps, build Web UI, start server; already-done steps are skipped.
    Double-click start.bat to run.
    Key constraints:
      - Prefer the py launcher to pick 3.11 precisely, avoiding the Microsoft Store
        python.exe stub
      - Whether to reinstall deps / rebuild frontend is decided by mtime comparison,
        no network checks, so offline boot works
      - Missing node/npm only warns, doesn't block (backend still serves /api/agent/call)
      - Does not touch data\ or config.yaml: port, credentials, etc. are decided by
        backend first-boot logic

    Copyright (c) 2026 Asterove
    AGPL-3.0 License
#>
[CmdletBinding()]
param(
    [switch]$RebuildUi,
    [switch]$SkipUi,
    [switch]$Reinstall,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

function Write-Log  { param([string]$Message) Write-Host "[AsterMem] $Message" -ForegroundColor White }
function Write-Warn { param([string]$Message) Write-Host "[AsterMem] $Message" -ForegroundColor Yellow }
function Stop-WithError {
    param([string]$Message)
    Write-Host "[AsterMem] $Message" -ForegroundColor Red
    exit 1
}

if ($Help) {
    Write-Host @"
Usage: .\start.ps1 [options]        (or double-click start.bat)

  -RebuildUi    Force rebuild the Web UI (normally detected automatically from source mtimes)
  -SkipUi       Skip frontend check, start backend directly
  -Reinstall    Force reinstall Python dependencies
  -Help         Show this help

First run automatically creates venv, installs dependencies and builds the Web UI; subsequent runs only perform checks.
"@
    exit 0
}

$repoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoDir

$venvDir     = Join-Path $repoDir "venv"
$venvPython  = Join-Path $venvDir "Scripts\python.exe"
$depsMarker  = Join-Path $venvDir ".astermem-deps-ok"
$uiEntry     = Join-Path $repoDir "web-ui\dist\index.html"
$requirements = Join-Path $repoDir "requirements.txt"

# Return an array of callable interpreter commands (py launcher with version flag, or standalone executable path)
function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($flag in @("-3.11", "-3.12", "-3.10")) {
            & py $flag -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) { return @("py", $flag) }
        }
    }
    foreach ($name in @("python3.11", "python3", "python")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source) {
            & $command.Source -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { return @($command.Source) }
        }
    }
    return $null
}

if (-not (Test-Path $venvPython)) {
    $python = Find-Python
    if (-not $python) {
        Stop-WithError "Python 3.10+ not found. Please install Python 3.11 from python.org (check 'Add python.exe to PATH' during installation)"
    }
    Write-Log "Creating virtual environment venv\"
    & $python[0] @($python[1..($python.Length - 1)] + @("-m", "venv", $venvDir))
    if ($LASTEXITCODE -ne 0) { Stop-WithError "Failed to create virtual environment" }
    $Reinstall = $true
}

$needInstall = $Reinstall.IsPresent -or -not (Test-Path $depsMarker)
if (-not $needInstall) {
    $needInstall = (Get-Item $requirements).LastWriteTime -gt (Get-Item $depsMarker).LastWriteTime
}

if ($needInstall) {
    Write-Log "Installing Python dependencies (first run takes ~1-3 minutes)"
    & $venvPython -m pip install --upgrade pip --quiet
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { Stop-WithError "Failed to install Python dependencies" }
    Set-Content -Path $depsMarker -Value "ok" -Encoding ascii
} else {
    Write-Log "Python dependencies are up to date"
}

# .env stores API keys only; copy from template if missing so provider config has a place to land
$envFile = Join-Path $repoDir ".env"
$envExample = Join-Path $repoDir ".env.example"
if (-not (Test-Path $envFile) -and (Test-Path $envExample)) {
    Copy-Item $envExample $envFile
    Write-Log "Generated .env from .env.example (fill in your API key and restart to enable semantic search)"
}

# A present dist\ says nothing about whether it matches the current source: editing web-ui\
# and restarting used to silently keep serving the previous bundle. Compare source mtimes
# against the built entry instead, so a rebuild is only skipped when it is genuinely current.
function Test-UiStale {
    if (-not (Test-Path $uiEntry)) { return $true }
    $builtAt = (Get-Item $uiEntry).LastWriteTimeUtc
    $uiDir = Join-Path $repoDir "web-ui"
    $newer = Get-ChildItem -Path $uiDir -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object {
            $_.FullName -notmatch '\\(node_modules|dist)\\' -and
            $_.Extension -ne ".tsbuildinfo" -and
            $_.LastWriteTimeUtc -gt $builtAt
        } |
        Select-Object -First 1
    return [bool]$newer
}

if ($SkipUi) {
    Write-Log "Skipped frontend check (-SkipUi)"
} elseif ($RebuildUi -or (Test-UiStale)) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Push-Location (Join-Path $repoDir "web-ui")
        try {
            if (-not (Test-Path "node_modules")) {
                Write-Log "Installing frontend dependencies"
                & npm install
                if ($LASTEXITCODE -ne 0) { Stop-WithError "npm install failed" }
            }
            Write-Log "Building Web UI"
            & npm run build
            if ($LASTEXITCODE -ne 0) { Stop-WithError "Failed to build Web UI" }
        } finally {
            Pop-Location
        }
    } else {
        Write-Warn "npm not found, skipping Web UI build: browser will only show the API hint page; AI channel /api/agent/call is unaffected"
        Write-Warn "After installing Node.js 18+, run .\start.ps1 -RebuildUi to build the frontend"
    }
} else {
    Write-Log "Web UI build is up to date (newer than all web-ui\ sources)"
}

Write-Log "Starting AsterMem... (Ctrl+C to stop; default credentials: admin / admin)"
& $venvPython (Join-Path $repoDir "server.py")
exit $LASTEXITCODE
