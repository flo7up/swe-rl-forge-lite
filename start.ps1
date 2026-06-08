$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

function Test-CommandExists {
    param([Parameter(Mandatory = $true)][string]$Name)
    return [bool](Get-Command -Name $Name -ErrorAction SilentlyContinue)
}

if (-not (Test-CommandExists -Name "forge")) {
    Write-Error "'forge' was not found on PATH. Install this repo first (for example: python -m pip install -e .)."
}

if (-not (Test-CommandExists -Name "npm")) {
    Write-Error "'npm' was not found on PATH. Install Node.js (18+) and retry."
}

if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "frontend\node_modules"))) {
    Write-Host "Installing frontend dependencies..." -ForegroundColor Cyan
    npm --prefix frontend install
}

Write-Host "Starting forge live API at http://127.0.0.1:8765 ..." -ForegroundColor Cyan
$ApiJob = Start-Job -Name "forge-dashboard-live" -ScriptBlock {
    Set-Location $using:RepoRoot
    forge dashboard-live --host 127.0.0.1 --port 8765
}

Start-Sleep -Seconds 1
if ($ApiJob.State -eq "Failed") {
    Receive-Job -Job $ApiJob -Keep | Write-Host
    throw "Failed to start forge dashboard-live."
}

Write-Host "Starting Vite frontend at http://127.0.0.1:5173 ..." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop both services." -ForegroundColor Yellow

try {
    npm --prefix frontend run dev
}
finally {
    if ($ApiJob -and ($ApiJob.State -eq "Running" -or $ApiJob.State -eq "NotStarted")) {
        Stop-Job -Job $ApiJob -ErrorAction SilentlyContinue
    }
    if ($ApiJob) {
        Remove-Job -Job $ApiJob -Force -ErrorAction SilentlyContinue
    }
}
