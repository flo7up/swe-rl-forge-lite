param(
    [switch]$InLaunchedTerminal
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Test-CommandExists {
    param([Parameter(Mandatory = $true)][string]$Name)
    return [bool](Get-Command -Name $Name -ErrorAction SilentlyContinue)
}

function Start-InExternalTerminal {
    param([Parameter(Mandatory = $true)][string]$ScriptPath)

    $PowerShellHost = if (Test-CommandExists -Name "pwsh") { "pwsh.exe" } else { "powershell.exe" }
    $Command = "& '$ScriptPath' -InLaunchedTerminal"
    $EncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))
    $PowerShellArgs = @("-NoExit", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $EncodedCommand)

    if (Test-CommandExists -Name "wt.exe") {
        $TerminalArgs = @("new-tab", $PowerShellHost) + $PowerShellArgs
        Start-Process -FilePath "wt.exe" -ArgumentList $TerminalArgs
    }
    else {
        Start-Process -FilePath $PowerShellHost -ArgumentList $PowerShellArgs
    }
}

function Test-DockerEngineReady {
    if (-not (Test-CommandExists -Name "docker")) {
        return $false
    }

    try {
        docker info *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Get-DockerDesktopPath {
    $Candidates = @()
    if ($env:ProgramFiles) {
        $Candidates += Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    }
    if ($env:LOCALAPPDATA) {
        $Candidates += Join-Path $env:LOCALAPPDATA "Programs\Docker\Docker\Docker Desktop.exe"
    }

    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath $Candidate) {
            return $Candidate
        }
    }

    return $null
}

function Start-DockerEngine {
    if (-not (Test-CommandExists -Name "docker")) {
        Write-Error "'docker' was not found on PATH. Install Docker Desktop and retry."
    }

    Write-Host "Docker engine is not running; starting Docker Desktop..." -ForegroundColor Cyan

    try {
        docker desktop start *> $null
        if ($LASTEXITCODE -eq 0) {
            return
        }
    }
    catch {
    }

    $DockerDesktopPath = Get-DockerDesktopPath
    if (-not $DockerDesktopPath) {
        Write-Error "Docker engine is not running and Docker Desktop could not be found. Start Docker Desktop manually and retry."
    }

    Start-Process -FilePath $DockerDesktopPath
}

function Wait-DockerEngineReady {
    param([int]$TimeoutSeconds = 120)

    if (Test-DockerEngineReady) {
        return
    }

    Start-DockerEngine
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $Deadline) {
        if (Test-DockerEngineReady) {
            Write-Host "Docker engine is ready." -ForegroundColor Green
            return
        }
        Start-Sleep -Seconds 2
    }

    Write-Error "Docker Desktop did not become ready within $TimeoutSeconds seconds. Start Docker Desktop manually and retry."
}

if (-not $InLaunchedTerminal) {
    Start-InExternalTerminal -ScriptPath $PSCommandPath
    Write-Host "Started SWE RL Forge in a new terminal." -ForegroundColor Cyan
    return
}

$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

if (-not (Test-CommandExists -Name "forge")) {
    Write-Error "'forge' was not found on PATH. Install this repo first (for example: python -m pip install -e .)."
}

if (-not (Test-CommandExists -Name "npm")) {
    Write-Error "'npm' was not found on PATH. Install Node.js (18+) and retry."
}

Wait-DockerEngineReady

if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "frontend\node_modules"))) {
    Write-Host "Installing frontend dependencies..." -ForegroundColor Cyan
    npm --prefix frontend install
}

Write-Host "Starting forge live API with controls at http://127.0.0.1:8765 ..." -ForegroundColor Cyan
$ApiJob = Start-Job -Name "forge-dashboard-live" -ScriptBlock {
    Set-Location $using:RepoRoot
    forge dashboard-live --host 127.0.0.1 --port 8765 --enable-controls
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
