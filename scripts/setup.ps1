#Requires -Version 5.1
<#
.SYNOPSIS
    Automated local setup for the AI SEO Agent project (Windows PowerShell).

.DESCRIPTION
    Prepares a fresh clone of this repository for local development:
      1. Verifies Python 3.12+ is available.
      2. Creates .venv if it does not already exist (never recreates an existing venv).
      3. Activates the venv for this session and upgrades pip.
      4. Installs everything in requirements.txt.
      5. Copies .env.example to .env if .env does not already exist
         (never overwrites an existing .env - your API keys are safe).
      6. Runs the test suite to confirm the environment is healthy (unless -SkipTests).
      7. Prints next steps.

    Safe to re-run at any time: every step is idempotent and skips work that
    is already done, so running this again after editing requirements.txt
    (for example) simply installs the new packages.

.PARAMETER SkipTests
    Skip running the pytest suite after installing dependencies.

.PARAMETER Start
    Start the FastAPI dev server (uvicorn --reload) once setup finishes.

.EXAMPLE
    .\scripts\setup.ps1

.EXAMPLE
    .\scripts\setup.ps1 -SkipTests -Start
#>

[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$Start
)

$ErrorActionPreference = "Stop"

# Resolve the repository root as the parent of this script's directory,
# so the script works regardless of the caller's current directory.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "    OK: $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "    WARNING: $Message" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Step 1: Verify Python 3.12+
# ---------------------------------------------------------------------------

Write-Step "Checking Python version"

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error "Python was not found on PATH. Install Python 3.12+ from https://www.python.org/downloads/ and re-run this script."
}

$versionOutput = & python --version 2>&1
if ($versionOutput -notmatch "Python (\d+)\.(\d+)\.(\d+)") {
    Write-Error "Could not parse Python version from '$versionOutput'."
}
$major = [int]$Matches[1]
$minor = [int]$Matches[2]
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 12)) {
    Write-Error "Python 3.12+ is required; found $versionOutput. Install a newer Python and re-run this script."
}
Write-Ok "$versionOutput"

# ---------------------------------------------------------------------------
# Step 2: Create the virtual environment if it does not already exist
# ---------------------------------------------------------------------------

Write-Step "Setting up the virtual environment (.venv)"

$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    Write-Ok ".venv already exists - leaving it as-is."
} else {
    Write-Host "    Creating .venv ..."
    python -m venv .venv
    Write-Ok ".venv created."
}

# ---------------------------------------------------------------------------
# Step 3: Allow venv activation for this process, then activate it
# ---------------------------------------------------------------------------

Write-Step "Activating the virtual environment"

Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
$activateScript = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
. $activateScript
Write-Ok "Virtual environment activated for this session."

# ---------------------------------------------------------------------------
# Step 4: Install dependencies
# ---------------------------------------------------------------------------

Write-Step "Installing dependencies from requirements.txt"

& $venvPython -m pip install --upgrade pip | Out-Null
& $venvPython -m pip install -r requirements.txt
Write-Ok "Dependencies installed."

# ---------------------------------------------------------------------------
# Step 5: Create .env from .env.example if missing
# ---------------------------------------------------------------------------

Write-Step "Checking .env configuration"

$envPath = Join-Path $RepoRoot ".env"
$envExamplePath = Join-Path $RepoRoot ".env.example"
if (Test-Path $envPath) {
    Write-Ok ".env already exists - leaving it as-is (your API keys are safe)."
} else {
    Copy-Item $envExamplePath $envPath
    Write-Ok ".env created from .env.example."
    Write-Warn "Open .env and fill in the API key for whichever LLM_PROVIDER you plan to use (gemini | perplexity | openai)."
}

# ---------------------------------------------------------------------------
# Step 6: Run the test suite
# ---------------------------------------------------------------------------

if ($SkipTests) {
    Write-Step "Skipping test suite (-SkipTests was passed)"
} else {
    Write-Step "Running the test suite to verify the environment"
    & $venvPython -m pytest test/ -q --tb=short
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Some tests failed. Review the output above before continuing."
    } else {
        Write-Ok "All tests passed."
    }
}

# ---------------------------------------------------------------------------
# Step 7: Summary / next steps
# ---------------------------------------------------------------------------

Write-Step "Setup complete"
Write-Host "    Next steps:"
Write-Host "      1. Open .env and confirm LLM_PROVIDER and its matching API key are set."
Write-Host "      2. Start the app:  uvicorn src.main:app --reload"
Write-Host "      3. Open:           http://127.0.0.1:8000/"
Write-Host ""
Write-Host "    See SETUP_GUIDE.md for a full walkthrough and repo tour."

if ($Start) {
    Write-Step "Starting the dev server (Ctrl+C to stop)"
    & $venvPython -m uvicorn src.main:app --reload
}
