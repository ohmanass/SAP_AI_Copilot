<#
.SYNOPSIS
    Removes every Cloud Foundry resource created by deploy.ps1 — and nothing else.

.DESCRIPTION
    Deletes, by exact name only:
      apps      sap-ai-copilot-approuter, sap-ai-copilot-backend (with their routes)
      services  sap-ai-copilot-xsuaa, sap-ai-copilot-aicore, sap-ai-copilot-s4

.PARAMETER Force
    Skip the confirmation prompt.
#>
param(
    [switch]$Force
)

$Apps     = "sap-ai-copilot-approuter", "sap-ai-copilot-backend"
$Services = "sap-ai-copilot-xsuaa", "sap-ai-copilot-aicore", "sap-ai-copilot-s4"

cf target
if ($LASTEXITCODE -ne 0) { throw "Not logged in to Cloud Foundry." }

Write-Host "`nThe following resources will be deleted from the space above:" -ForegroundColor Yellow
$Apps + $Services | ForEach-Object { Write-Host "   $_" }

if (-not $Force) {
    $answer = Read-Host "`nType 'delete' to confirm"
    if ($answer -ne "delete") { Write-Host "Cancelled."; return }
}

foreach ($app in $Apps) {
    cf delete $app -f -r
}
foreach ($service in $Services) {
    cf delete-service $service -f
}

Write-Host "`nCleanup done." -ForegroundColor Green
