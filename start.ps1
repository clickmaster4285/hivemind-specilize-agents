<#
.SYNOPSIS
  Start MulteAgent for local development, in the right order, skipping whatever already runs.

.DESCRIPTION
  1. Docker Desktop (if not running) -> brain database container (Postgres + pgvector)
  2. Ollama (tray app)
  3. API  (python main.py --gui)  in its own window
  4. UI   (npm run dev)          in its own window
  5. Health check

.EXAMPLE
  .\start.ps1            # everything
  .\start.ps1 -Tools     # also start pgAdmin (http://localhost:5050)
  .\start.ps1 -NoUI      # backend only
#>
param(
    [switch]$Tools,
    [switch]$NoUI
)

# Continue (not Stop): Windows PowerShell 5.1 turns harmless stderr from docker into fatal errors otherwise
$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

function Write-Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "   OK   $text" -ForegroundColor Green }
function Write-Skip($text) { Write-Host "   --   $text" -ForegroundColor DarkGray }
function Write-Bad($text)  { Write-Host "   FAIL $text" -ForegroundColor Red }

function Test-Port([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return [bool]$conn
}

function Test-Docker {
    docker info *> $null
    return ($LASTEXITCODE -eq 0)
}

function Test-Http([string]$Url) {
    try {
        $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return $true
    } catch {
        return $false
    }
}

function Wait-Until([scriptblock]$Check, [int]$Seconds, [string]$What) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Check) { return $true }
        Start-Sleep -Seconds 2
    }
    Write-Bad "$What did not become ready within $Seconds s"
    return $false
}

# ── 1. Docker + database ──────────────────────────────────────────────
Write-Step "Docker and the brain database"
if (Test-Docker) {
    Write-Skip "Docker already running"
} else {
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $desktop)) { $desktop = Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe" }
    if (Test-Path $desktop) {
        Write-Host "   starting Docker Desktop..."
        Start-Process -FilePath $desktop
        $null = Wait-Until { Test-Docker } 120 "Docker"
    } else {
        Write-Bad "Docker Desktop not found - start it manually"
    }
}
Push-Location $Root
docker compose up -d db *> $null
if ($Tools) { docker compose --profile tools up -d pgadmin *> $null }
Pop-Location
$dbReady = Wait-Until { (docker inspect -f "{{.State.Health.Status}}" multeagent-db-1 2>$null) -eq "healthy" } 90 "Database"
if ($dbReady) { Write-Ok "database healthy (127.0.0.1:5432)" }
if ($Tools) { Write-Ok "pgAdmin: http://localhost:5050" }

# ── 2. Ollama ─────────────────────────────────────────────────────────
Write-Step "Ollama"
if (Test-Http "http://127.0.0.1:11434/api/version") {
    Write-Skip "Ollama already running"
} else {
    $ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
    if (Test-Path $ollama) {
        Start-Process -FilePath $ollama
        if (Wait-Until { Test-Http "http://127.0.0.1:11434/api/version" } 60 "Ollama") { Write-Ok "Ollama started" }
    } else {
        Write-Bad "Ollama not found at $ollama"
    }
}

# ── 3. API ────────────────────────────────────────────────────────────
Write-Step "API (port 8787)"
if (Test-Port 8787) {
    Write-Skip "already listening on 8787 - assuming the API runs (restart it yourself to load new code)"
} elseif (-not (Test-Path $Python)) {
    Write-Bad "virtual environment not found: $Python"
} else {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root'; & '$Python' main.py --gui"
    Write-Ok "API window opened"
}

# ── 4. UI ─────────────────────────────────────────────────────────────
if (-not $NoUI) {
    Write-Step "UI (port 3000)"
    if (Test-Port 3000) {
        Write-Skip "already listening on 3000 - assuming the UI runs"
    } else {
        $front = Join-Path $Root "frontend"
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$front'; npm run dev"
        Write-Ok "UI window opened"
    }
}

# ── 5. Health ─────────────────────────────────────────────────────────
Write-Step "Health check"
if (Wait-Until { Test-Http "http://127.0.0.1:8787/api/health" } 120 "API") {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8787/api/health" -TimeoutSec 5
    Write-Ok ("API up - model {0}, Ollama reachable: {1}, image model: {2}" -f $health.model, $health.llm_reachable, $health.image_pipeline.state)
}
if (-not $NoUI) {
    if (Wait-Until { Test-Http "http://127.0.0.1:3000" } 120 "UI") { Write-Ok "UI: http://localhost:3000" }
}
Write-Host ""
