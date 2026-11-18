# Backend for local development, restarted automatically on every change of app/*.py
# (see scripts/dev_server.py for why "uvicorn --reload" is not used on Windows).
#
#   .\dev-backend.ps1             port 8000
#   .\dev-backend.ps1 -Port 8001

param([int]$Port = 8000)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Virtual environment not found: create it first (see README, Installation)." }

& $python (Join-Path $PSScriptRoot "scripts\dev_server.py") $Port
