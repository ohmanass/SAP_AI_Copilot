<#
.SYNOPSIS
    Builds and deploys SAP AI Copilot to SAP BTP, Cloud Foundry environment.

.DESCRIPTION
    Idempotent: run it again to update. Only creates or updates resources whose
    name starts with "sap-ai-copilot-". Secrets are read from the local .env and
    passed to Cloud Foundry through temporary files, never on the command line.

    Steps:
      1. Build   deploy/build/backend    (Python app, pinned requirements, shipped documents)
                 deploy/build/approuter  (approuter + SAPUI5 build, UI5 from the SAP CDN)
      2. Services: sap-ai-copilot-xsuaa (xsuaa/application),
                   sap-ai-copilot-aicore (user-provided),
                   sap-ai-copilot-destination (destination/lite, free),
                   sap-ai-copilot-hana (hana/schema, free: chat history in SAP HANA Cloud)
         The SAP S/4HANA system is read from a BTP destination:
           - BTP_S4_DESTINATION in .env: name of an existing subaccount destination
             (e.g. a real S/4HANA system), used as is;
           - otherwise: instance destination "sap-ai-copilot-s4" created from
             SAP_API_BASE_URL (default: sandbox) and SAP_API_KEY.
      3. cf push backend then approuter
      4. Security check: the backend must answer 401 without an XSUAA token

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
# SAPUI5 version: the one of frontend/ui5.yaml (index.html loads it from the SAP CDN)
$Ui5Version = ((Get-Content (Join-Path $Root "frontend\ui5.yaml") -Raw) | Select-String '(?m)^\s+version:\s*"([^"]+)"').Matches[0].Groups[1].Value

$Backend   = "sap-ai-copilot-backend"
$Approuter = "sap-ai-copilot-approuter"
$Xsuaa     = "sap-ai-copilot-xsuaa"
$AiCore    = "sap-ai-copilot-aicore"
$Destination = "sap-ai-copilot-destination"
$Hana      = "sap-ai-copilot-hana"
$LegacyS4  = "sap-ai-copilot-s4"     # user-provided service of the first versions, replaced by the destination

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

function Get-AppRoutes($name) {
    # Routes of an app, empty when the app does not exist yet
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = cf app $name 2>&1 | ForEach-Object { "$_" }
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($LASTEXITCODE -ne 0) { return @() }
    $line = $output | Select-String '^routes:\s+(.+)$' | Select-Object -First 1
    if (-not $line) { return @() }
    return $line.Matches[0].Groups[1].Value -split ',\s*' | Where-Object { $_ }
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

function Write-SecretFile([hashtable]$content) {
    # Secrets go through a temporary file so they never appear in the process list.
    # UTF-8 without BOM: the cf CLI rejects a BOM as invalid JSON.
    $file = Join-Path ([IO.Path]::GetTempPath()) ("cf-" + [guid]::NewGuid() + ".json")
    [IO.File]::WriteAllText($file, ($content | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding $false))
    return $file
}

function Wait-Service($name) {
    for ($i = 0; $i -lt 30; $i++) {
        $status = Get-ServiceStatus $name
        if ($status -match 'succeeded') { return }
        if ($status -match 'failed') { throw "${name}: $status" }
        Start-Sleep -Seconds 5
    }
}

function Set-UserProvidedService($name, [hashtable]$credentials) {
    $file = Write-SecretFile $credentials
    try {
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

$S4Destination = $envValues["BTP_S4_DESTINATION"]
$required = "AICORE_CLIENT_ID", "AICORE_CLIENT_SECRET", "AICORE_AUTH_URL", "AICORE_BASE_URL", "AICORE_RESOURCE_GROUP"
if (-not $S4Destination) { $required += "SAP_API_KEY" }
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
robocopy (Join-Path $Root "app") (Join-Path $BackendBuild "app") /E /XD __pycache__ /XF "test_*.py" *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy app failed" }

# --- Backend: shipped RAG documents, demo database (never the chat history) ---
# No vector index: on SAP BTP the knowledge base lives in the SAP HANA Cloud Vector
# Engine, filled by the backend at startup from data/documents.
$DataBuild = Join-Path $BackendBuild "data"
New-Item -ItemType Directory -Force $DataBuild | Out-Null
robocopy (Join-Path $Root "data\documents") (Join-Path $DataBuild "documents") /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy data failed" }
if (Test-Path (Join-Path $Root "data\sap.db")) { Copy-Item (Join-Path $Root "data\sap.db") $DataBuild }

# --- Backend: English interface texts, source of the translations by the AI model (app/i18n) ---
$I18nBuild = Join-Path $BackendBuild "ui_i18n"
New-Item -ItemType Directory -Force $I18nBuild | Out-Null
Copy-Item (Join-Path $Root "frontend\webapp\i18n\i18n.properties") $I18nBuild

# --- Backend: requirements pinned to the versions tested locally ---
$python = Join-Path $Root ".venv\Scripts\python.exe"
$frozen = & $python -m pip freeze
# Local only: tests, and ChromaDB (local vector store; its dependencies, such as
# onnxruntime, kubernetes and grpc, made every push several minutes longer)
$LocalOnly = "pytest", "chromadb"
$pinned = foreach ($requirement in Get-Content (Join-Path $Root "requirements.txt")) {
    $requirement = $requirement.Trim()
    $name = ($requirement -replace '\[.*\]', '').Trim()
    if (-not $name -or $name.StartsWith('#') -or $LocalOnly -contains $name.ToLower()) { continue }
    $match = $frozen | Where-Object { $_ -match "^$([regex]::Escape($name))==" } | Select-Object -First 1
    if (-not $match) { throw "Version of '$name' not found in .venv" }
    # keep extras: uvicorn[standard] -> uvicorn[standard]==x.y.z
    "$requirement==$(($match -split '==')[1])"
}
$pinned | Set-Content (Join-Path $BackendBuild "requirements.txt") -Encoding ascii
"python-3.12.x" | Set-Content (Join-Path $BackendBuild "runtime.txt") -Encoding ascii

# --- Frontend: UI5 build (Component-preload), framework loaded from the SAP CDN ---
Push-Location (Join-Path $Root "frontend")
try {
    Invoke-Native "ui5 build" { npx.cmd ui5 build --clean-dest --dest (Join-Path $ApprouterBuild "webapp") }
} finally {
    Pop-Location
}
$index = Join-Path $ApprouterBuild "webapp\index.html"
$html = Get-Content $index -Raw
if ($html -notmatch [regex]::Escape("https://ui5.sap.com/$Ui5Version/resources/sap-ui-core.js")) {
    throw "index.html must load SAPUI5 $Ui5Version (frontend/ui5.yaml) from https://ui5.sap.com"
}

# Cache busting: the stylesheet name carries a hash of its content, so no browser
# or proxy cache can serve an outdated version after a deployment.
$webappBuild = Join-Path $ApprouterBuild "webapp"
$cssFile = Join-Path $webappBuild "css\style.css"
$cssHash = (Get-FileHash $cssFile -Algorithm SHA256).Hash.Substring(0, 10).ToLower()
$cssName = "style.$cssHash.css"
Rename-Item $cssFile $cssName
foreach ($file in "manifest.json", "Component-preload.js") {
    $path = Join-Path $webappBuild $file
    $content = [IO.File]::ReadAllText($path)
    if (-not $content.Contains('"css/style.css"')) { throw "css/style.css reference not found in $file" }
    [IO.File]::WriteAllText($path, $content.Replace('"css/style.css"', "`"css/$cssName`""), (New-Object Text.UTF8Encoding $false))
}
Write-Host "Stylesheet: css/$cssName"

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
# "cf target" answers from the local config even with an expired token: check the
# session for real, otherwise a missing service could not be told from an auth error.
$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try { cf oauth-token 2>&1 | Out-Null } finally { $ErrorActionPreference = $previous }
if ($LASTEXITCODE -ne 0) { throw "Cloud Foundry session expired. Run: cf login -a https://api.cf.eu10-005.hana.ondemand.com --sso" }

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
Wait-Service $Xsuaa

Step "Service $AiCore (user-provided, from .env)"
Set-UserProvidedService $AiCore @{
    clientid       = $envValues["AICORE_CLIENT_ID"]
    clientsecret   = $envValues["AICORE_CLIENT_SECRET"]
    url            = $envValues["AICORE_AUTH_URL"]
    serviceurls    = @{ AI_API_URL = $envValues["AICORE_BASE_URL"] }
    resource_group = $envValues["AICORE_RESOURCE_GROUP"]
}

Step "Service $Destination (destination/lite)"
$destinationConfig = @{ HTML5Runtime_enabled = $false }
if ($S4Destination) {
    Write-Host "   SAP S/4HANA system: subaccount destination '$S4Destination' (BTP_S4_DESTINATION)"
} else {
    # Instance destination for the sandbox: the API key travels as an additional header
    $S4Destination = "sap-ai-copilot-s4"
    $s4Url = if ($envValues["SAP_API_BASE_URL"]) { $envValues["SAP_API_BASE_URL"] } else { "https://sandbox.api.sap.com/s4hanacloud" }
    Write-Host "   SAP S/4HANA system: instance destination '$S4Destination' -> $s4Url"
    $destinationConfig.init_data = @{
        instance = @{
            existing_destinations_policy = "update"
            destinations = @(@{
                Name             = $S4Destination
                Type             = "HTTP"
                URL              = $s4Url
                Authentication   = "NoAuthentication"
                ProxyType        = "Internet"
                Description      = "SAP AI Copilot - SAP S/4HANA Cloud"
                "URL.headers.APIKey" = $envValues["SAP_API_KEY"]
            })
        }
    }
}
$file = Write-SecretFile $destinationConfig
try {
    if (Test-ServiceExists $Destination) {
        Invoke-Native "cf update-service $Destination" { cf update-service $Destination -c $file }
    } else {
        Invoke-Native "cf create-service $Destination" { cf create-service destination lite $Destination -c $file }
    }
} finally {
    Remove-Item $file -Force -ErrorAction SilentlyContinue
}
Wait-Service $Destination

Step "Service $Hana (hana/schema)"
# Schema on the SAP HANA Cloud database of the subaccount: the chat history survives
# restarts and deployments. Created once, never updated (it holds the data).
if (-not (Test-ServiceExists $Hana)) {
    Invoke-Native "cf create-service $Hana" { cf create-service hana schema $Hana }
    Wait-Service $Hana
} else {
    Write-Host "   already exists"
}

# ---------------------------------------------------------------------------
# 4. Applications
# ---------------------------------------------------------------------------
$Manifest = Join-Path $DeployDir "manifest.yml"

# Stable public host name for the backend: reuse the existing one, else generate one
$BackendHost = Get-AppRoutes $Backend |
    Where-Object { $_ -match '^(sap-ai-copilot-backend-[a-z0-9]+)\.cfapps\.' } |
    ForEach-Object { $Matches[1] } | Select-Object -First 1
if (-not $BackendHost) {
    $suffix = -join ((97..122) + (48..57) | Get-Random -Count 8 | ForEach-Object { [char]$_ })
    $BackendHost = "sap-ai-copilot-backend-$suffix"
}
$PushVars = @("--var", "genai_model=$GenAiModel", "--var", "backend_host=$BackendHost", "--var", "s4_destination=$S4Destination")

# The API key used to live in a user-provided service: the destination replaces it
if (Test-ServiceExists $LegacyS4) {
    Step "Removing obsolete service $LegacyS4"
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { cf unbind-service $Backend $LegacyS4 2>&1 | Out-Null } finally { $ErrorActionPreference = $previous }
    Invoke-Native "cf delete-service $LegacyS4" { cf delete-service $LegacyS4 -f }
}

Step "Push $Backend ($BackendHost)"
Invoke-Native "cf push $Backend" { cf push $Backend -f $Manifest @PushVars }

# The first deployment used an internal route + network policy (needs network.write,
# not granted in this org). Remove that route if it is still mapped.
if ((Get-AppRoutes $Backend) -contains "$Backend.apps.internal") {
    Step "Removing obsolete internal route"
    Invoke-Native "cf unmap-route" { cf unmap-route $Backend apps.internal --hostname $Backend }
    Invoke-Native "cf delete-route" { cf delete-route apps.internal --hostname $Backend -f }
}

Step "Push $Approuter"
Invoke-Native "cf push $Approuter" { cf push $Approuter -f $Manifest @PushVars }

# ---------------------------------------------------------------------------
# 5. Security check: the backend must refuse calls without a token
# ---------------------------------------------------------------------------
Step "Security check"
$backendUrl = "https://$BackendHost.cfapps.eu10-005.hana.ondemand.com"
$health = curl.exe -s -o NUL -w "%{http_code}" "$backendUrl/api/health"
$status = curl.exe -s -o NUL -w "%{http_code}" "$backendUrl/api/status"
Write-Host "   /api/health without token: HTTP $health (expected 200)"
Write-Host "   /api/status without token: HTTP $status (expected 401)"
if ($status -ne "401") { throw "Security check failed: the backend answered $status without a token" }

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
$route = Get-AppRoutes $Approuter | Select-Object -First 1
Write-Host "`nSAP AI Copilot is deployed: https://$route" -ForegroundColor Green
