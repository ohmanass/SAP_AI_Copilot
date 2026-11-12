<#
.SYNOPSIS
    Builds and deploys SAP AI Copilot to SAP BTP, Cloud Foundry environment.

.DESCRIPTION
    Idempotent: run it again to update. Only creates or updates resources whose
    name starts with "sap-ai-copilot-". Secrets are read from the local .env and
    passed to Cloud Foundry through temporary files, never on the command line.

    Steps:
      1. Build   deploy/build/backend    (Python app, pinned requirements, RAG index)
                 deploy/build/approuter  (approuter + SAPUI5 build, UI5 from the SAP CDN)
      2. Services: sap-ai-copilot-xsuaa (xsuaa/application),
                   sap-ai-copilot-aicore, sap-ai-copilot-s4 (user-provided)
      3. cf push backend then approuter
      4. Network policy approuter -> backend (backend has no public route)

.PARAMETER BuildOnly
    Only build deploy/build, do not touch Cloud Foundry.

.EXAMPLE
    cf login -a https://api.cf.eu10-005.hana.ondemand.com --sso
    .\deploy\deploy.ps1
#>
param(
    [switch]$BuildOnly
)

$ErrorActionPreference = "Stop"

$Root      = Split-Path -Parent $PSScriptRoot
$DeployDir = $PSScriptRoot
$BuildDir  = Join-Path $DeployDir "build"
$Ui5Version = "1.136.24"

$Backend   = "sap-ai-copilot-backend"
$Approuter = "sap-ai-copilot-approuter"
$Xsuaa     = "sap-ai-copilot-xsuaa"
$AiCore    = "sap-ai-copilot-aicore"
$S4        = "sap-ai-copilot-s4"

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

function Invoke-Native {
    param([string]$Label, [scriptblock]$Command)
    # Windows PowerShell turns stderr output of native tools into errors when
    # "Stop" is active: rely on the exit code instead.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command 2>&1 | ForEach-Object { "$_" }
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit code $LASTEXITCODE)" }
}

function Read-DotEnv($path) {
    $values = @{}
    foreach ($line in Get-Content $path) {
        if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$') {
            $values[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $values
}

function Test-ServiceExists($name) {
    # "not found" is written to stderr: must not become a terminating error
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        cf service $name 2>&1 | Out-Null
    } finally {
        $ErrorActionPreference = $previous
    }
    return $LASTEXITCODE -eq 0
}

function Get-ServiceStatus($name) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = cf service $name 2>&1 | ForEach-Object { "$_" }
    } finally {
        $ErrorActionPreference = $previous
    }
    $line = $output | Select-String 'status:\s+(.+)$' | Select-Object -First 1
    if ($line) { return $line.Matches[0].Groups[1].Value.Trim() }
    return ""
}

function Set-UserProvidedService($name, [hashtable]$credentials) {
    # Credentials go through a temporary file so they never appear in the process list
    $file = Join-Path ([IO.Path]::GetTempPath()) ("cups-" + [guid]::NewGuid() + ".json")
    try {
        # UTF-8 without BOM: the cf CLI rejects a BOM as invalid JSON
        [IO.File]::WriteAllText($file, ($credentials | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding $false))
        if (Test-ServiceExists $name) {
            Invoke-Native "cf update-user-provided-service $name" { cf update-user-provided-service $name -p $file }
        } else {
            Invoke-Native "cf create-user-provided-service $name" { cf create-user-provided-service $name -p $file }
        }
    } finally {
        Remove-Item $file -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------------------
# 0. Configuration
# ---------------------------------------------------------------------------
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) { throw ".env not found at $EnvFile" }
$envValues = Read-DotEnv $EnvFile

$required = "AICORE_CLIENT_ID", "AICORE_CLIENT_SECRET", "AICORE_AUTH_URL", "AICORE_BASE_URL", "AICORE_RESOURCE_GROUP", "SAP_API_KEY"
$missing = $required | Where-Object { -not $envValues[$_] }
if ($missing) { throw "Missing in .env: $($missing -join ', ')" }
$GenAiModel = if ($envValues["GENAI_MODEL_NAME"]) { $envValues["GENAI_MODEL_NAME"] } else { "gpt-4o-mini" }

# ---------------------------------------------------------------------------
# 1. Build
# ---------------------------------------------------------------------------
Step "Building deploy/build"
if (Test-Path $BuildDir) { Remove-Item $BuildDir -Recurse -Force }
$BackendBuild   = Join-Path $BuildDir "backend"
$ApprouterBuild = Join-Path $BuildDir "approuter"
New-Item -ItemType Directory -Force $BackendBuild, $ApprouterBuild | Out-Null

# --- Backend: application code (no tests, no caches) ---
robocopy (Join-Path $Root "app") (Join-Path $BackendBuild "app") /E /XD __pycache__ /XF "test_*.py" "cleanup_test_data.py" *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy app failed" }

# --- Backend: RAG documents + index, demo database (never the chat history) ---
$DataBuild = Join-Path $BackendBuild "data"
New-Item -ItemType Directory -Force $DataBuild | Out-Null
robocopy (Join-Path $Root "data\documents") (Join-Path $DataBuild "documents") /E /NFL /NDL /NJH /NJS /NP | Out-Null
robocopy (Join-Path $Root "data\chroma_db") (Join-Path $DataBuild "chroma_db") /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy data failed" }
if (Test-Path (Join-Path $Root "data\sap.db")) { Copy-Item (Join-Path $Root "data\sap.db") $DataBuild }
if (-not (Test-Path (Join-Path $DataBuild "chroma_db\chroma.sqlite3"))) {
    throw "RAG index missing: run 'python -m app.rag.index_documents' first"
}

# --- Backend: requirements pinned to the versions tested locally ---
$python = Join-Path $Root ".venv\Scripts\python.exe"
$frozen = & $python -m pip freeze
$pinned = foreach ($requirement in Get-Content (Join-Path $Root "requirements.txt")) {
    $requirement = $requirement.Trim()
    $name = ($requirement -replace '\[.*\]', '').Trim()
    if (-not $name -or $name -match '^pytest' -or $name.StartsWith('#')) { continue }
    $match = $frozen | Where-Object { $_ -match "^$([regex]::Escape($name))==" } | Select-Object -First 1
    if (-not $match) { throw "Version of '$name' not found in .venv" }
    # keep extras: uvicorn[standard] -> uvicorn[standard]==x.y.z
    "$requirement==$(($match -split '==')[1])"
}
$pinned | Set-Content (Join-Path $BackendBuild "requirements.txt") -Encoding ascii
"python-3.12.x" | Set-Content (Join-Path $BackendBuild "runtime.txt") -Encoding ascii

# --- Frontend: UI5 build, framework loaded from the SAP CDN ---
Push-Location (Join-Path $Root "frontend")
try {
    Invoke-Native "ui5 build" { npx.cmd ui5 build --clean-dest --dest (Join-Path $ApprouterBuild "webapp") }
} finally {
    Pop-Location
}
$index = Join-Path $ApprouterBuild "webapp\index.html"
$html = Get-Content $index -Raw
$html = $html.Replace('src="resources/sap-ui-core.js"', "src=`"https://ui5.sap.com/$Ui5Version/resources/sap-ui-core.js`"")
if ($html -notmatch "ui5.sap.com/$Ui5Version") { throw "Could not switch the UI5 bootstrap to the CDN" }
[IO.File]::WriteAllText($index, $html)

Copy-Item (Join-Path $DeployDir "approuter\package.json"), (Join-Path $DeployDir "approuter\xs-app.json") $ApprouterBuild

Write-Host "Build ready in $BuildDir"
if ($BuildOnly) { return }

# ---------------------------------------------------------------------------
# 2. Cloud Foundry target check
# ---------------------------------------------------------------------------
Step "Cloud Foundry target"
$target = cf target
if ($LASTEXITCODE -ne 0) { throw "Not logged in. Run: cf login -a <api endpoint> --sso" }
$target | Select-String 'API endpoint|org:|space:|user:' | ForEach-Object { Write-Host "   $($_.Line.Trim())" }

# ---------------------------------------------------------------------------
# 3. Services
# ---------------------------------------------------------------------------
Step "Service $Xsuaa"
$xsSecurity = Join-Path $DeployDir "xs-security.json"
if (Test-ServiceExists $Xsuaa) {
    Invoke-Native "cf update-service $Xsuaa" { cf update-service $Xsuaa -c $xsSecurity }
} else {
    Invoke-Native "cf create-service $Xsuaa" { cf create-service xsuaa application $Xsuaa -c $xsSecurity }
}
for ($i = 0; $i -lt 30; $i++) {
    $status = Get-ServiceStatus $Xsuaa
    if ($status -match 'succeeded') { break }
    if ($status -match 'failed') { throw "${Xsuaa}: $status" }
    Start-Sleep -Seconds 5
}

Step "Service $AiCore (user-provided, from .env)"
Set-UserProvidedService $AiCore @{
    clientid       = $envValues["AICORE_CLIENT_ID"]
    clientsecret   = $envValues["AICORE_CLIENT_SECRET"]
    url            = $envValues["AICORE_AUTH_URL"]
    serviceurls    = @{ AI_API_URL = $envValues["AICORE_BASE_URL"] }
    resource_group = $envValues["AICORE_RESOURCE_GROUP"]
}

Step "Service $S4 (user-provided, from .env)"
Set-UserProvidedService $S4 @{ api_key = $envValues["SAP_API_KEY"] }

# ---------------------------------------------------------------------------
# 4. Applications
# ---------------------------------------------------------------------------
$Manifest = Join-Path $DeployDir "manifest.yml"

Step "Push $Backend"
Invoke-Native "cf push $Backend" { cf push $Backend -f $Manifest --var "genai_model=$GenAiModel" }

Step "Push $Approuter"
Invoke-Native "cf push $Approuter" { cf push $Approuter -f $Manifest --var "genai_model=$GenAiModel" }

# ---------------------------------------------------------------------------
# 5. Network policy: only the approuter can reach the backend
# ---------------------------------------------------------------------------
Step "Network policy $Approuter -> $Backend"
Invoke-Native "cf add-network-policy" { cf add-network-policy $Approuter $Backend --protocol tcp --port 8080 }

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
$route = (cf app $Approuter | Select-String 'routes:\s+(\S+)' | Select-Object -First 1).Matches.Groups[1].Value
Write-Host "`nSAP AI Copilot is deployed: https://$route" -ForegroundColor Green
