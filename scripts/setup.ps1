$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) { py -3.11 -m venv .venv } else { python -m venv .venv }
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }

Push-Location frontend
npm install
Pop-Location
Write-Host "Setup complete. Add HF_TOKEN to .env, then start backend and desktop." -ForegroundColor Green

