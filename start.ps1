<#
.SYNOPSIS
    Starts SAP AI Copilot locally: interface and API on http://127.0.0.1:8000

.DESCRIPTION
    Everything a start needs, in order:
      1. Python environment (.venv) created the first time;
      2. dependencies installed again only when requirements.txt changed;
      3. .env created from .env.example if missing (credentials to fill in);
      4. server started, restarted automatically on every change of app/*.py
         (scripts/dev_server.py); a change of the interface only needs F5;
      5. browser opened once the server answers.
    If the application is already running, the browser is just opened on it.

.EXAMPLE
    .\start.ps1                  (or double-click start.cmd)
    .\start.ps1 -Port 8001 -NoBrowser
#>
param(
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Url = "http://127.0.0.1:$Port/"
$Health = "${Url}api/health"
$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$Stamp = Join-Path $PSScriptRoot ".venv\requirements.sha256"

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

function Test-Running {
    try { return (Invoke-WebRequest $Health -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { return $false }
}

function Find-Python {
    # Python launcher first (py -3.12), then python on the PATH; 3.11 or later
    foreach ($candidate in @(@("py", "-3.12"), @("py", "-3"), @("python"))) {
        $exe = $candidate[0]
        $arguments = @($candidate | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        & $exe @arguments -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return , $candidate }
    }
    throw "Python 3.11 or later is required: install it from https://www.python.org/downloads/ and run start again."
}

# --- Already running: just open it -----------------------------------------
if (Test-Running) {
    Step "SAP AI Copilot is already running on $Url"
    if (-not $NoBrowser) { Start-Process $Url }
    exit 0
}

# --- 1. Python environment ----------------------------------------------------
if (-not (Test-Path $VenvPython)) {
    Step "Creating the Python environment (.venv), first start only"
    $python = Find-Python
    & $python[0] @($python | Select-Object -Skip 1) -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python environment." }
}

# --- 2. Dependencies, only when requirements.txt changed ----------------------
$requirementsHash = (Get-FileHash (Join-Path $PSScriptRoot "requirements.txt") -Algorithm SHA256).Hash
if (-not (Test-Path $Stamp) -or (Get-Content $Stamp -Raw).Trim() -ne $requirementsHash) {
    Step "Installing the dependencies (requirements.txt changed)"
    & $VenvPython -m pip install --disable-pip-version-check -q -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed: see the messages above." }
    Set-Content $Stamp $requirementsHash -Encoding ascii
}

# --- 3. Configuration ---------------------------------------------------------
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Warning ".env created from .env.example: fill in the SAP AI Core credentials (service key of your BTP subaccount), then run start again."
    Start-Process notepad.exe ".env"
    exit 1
}

# --- 4. Browser, opened as soon as the server answers -------------------------
if (-not $NoBrowser) {
    Start-Job -ArgumentList $Health, $Url -ScriptBlock {
        param($health, $url)
        foreach ($attempt in 1..90) {
            try {
                if ((Invoke-WebRequest $health -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { Start-Process $url; return }
            } catch { Start-Sleep -Seconds 1 }
        }
    } | Out-Null
}

# --- 5. Server (Ctrl+C to stop) -------------------------------------------------
Step "Starting SAP AI Copilot on $Url"
& $VenvPython (Join-Path $PSScriptRoot "scripts\dev_server.py") $Port
