# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-22 10:30:00 UTC
# === END SIGNATURE ===
<#
.SYNOPSIS
    Launch Live If Gate Passed - HOPE Trading Bot deployment pipeline.

.DESCRIPTION
    Executes the following pipeline:
    1. SMOKE: Run night_test_v3.py
    2. GATE: Run ai_quality_gate.py
    3. PROBE: Validate health_v5.json N times
    4. START: Execute start command

    FAIL-CLOSED: Any failure at any step = exit with error code.

.PARAMETER PythonExe
    Path to python.exe

.PARAMETER SmokeScript
    Path to tools/night_test_v3.py

.PARAMETER GateScript
    Path to tools/ai_quality_gate.py

.PARAMETER SmokeHours
    Duration for smoke test (e.g., 0.16 for ~10 minutes)

.PARAMETER HealthPath
    Path to health_v5.json

.PARAMETER HealthMaxAgeSec
    Maximum allowed age of heartbeat in seconds

.PARAMETER HealthStableSamples
    Number of consecutive probe passes required

.PARAMETER HealthSampleIntervalSec
    Interval between probe samples in seconds

.PARAMETER StartCommand
    Command to execute on successful gate pass

.PARAMETER ExpectedCmdlineSha256
    Optional: Expected cmdline hash for SSoT validation

.EXAMPLE
    .\launch_live_if_gate_passed.ps1 `
        -PythonExe "C:\path\to\python.exe" `
        -SmokeScript "C:\path\to\night_test_v3.py" `
        -GateScript "C:\path\to\ai_quality_gate.py" `
        -SmokeHours 0.16 `
        -HealthPath "C:\path\to\health_v5.json" `
        -HealthMaxAgeSec 15 `
        -HealthStableSamples 3 `
        -HealthSampleIntervalSec 2 `
        -StartCommand "python -m minibot.run_live_v5"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$PythonExe,

    [Parameter(Mandatory=$true)]
    [string]$SmokeScript,

    [Parameter(Mandatory=$true)]
    [string]$GateScript,

    [Parameter(Mandatory=$true)]
    [double]$SmokeHours,

    [Parameter(Mandatory=$true)]
    [string]$HealthPath,

    [Parameter(Mandatory=$true)]
    [int]$HealthMaxAgeSec,

    [Parameter(Mandatory=$true)]
    [int]$HealthStableSamples,

    [Parameter(Mandatory=$true)]
    [int]$HealthSampleIntervalSec,

    [Parameter(Mandatory=$true)]
    [string]$StartCommand,

    [Parameter(Mandatory=$false)]
    [string]$ExpectedCmdlineSha256 = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ANSI colors for output
$RED = "`e[31m"
$GREEN = "`e[32m"
$YELLOW = "`e[33m"
$CYAN = "`e[36m"
$RESET = "`e[0m"

function Write-Stage {
    param([string]$Stage, [string]$Message)
    Write-Host "${CYAN}=== $Stage ===${RESET} $Message"
}

function Write-Pass {
    param([string]$Message)
    Write-Host "${GREEN}[PASS]${RESET} $Message"
}

function Write-Fail {
    param([string]$Message)
    Write-Host "${RED}[FAIL]${RESET} $Message"
}

function Exec-Command {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Description,
        [Parameter(Mandatory=$true)]
        [string]$Command
    )

    Write-Host ">>> $Command"

    # Execute command and capture exit code
    $process = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $Command -Wait -PassThru -NoNewWindow
    return $process.ExitCode
}

function Main {
    Write-Host ""
    Write-Host "${CYAN}================================================================${RESET}"
    Write-Host "${CYAN}  HOPE Launch Pipeline: SMOKE -> GATE -> PROBE -> START${RESET}"
    Write-Host "${CYAN}================================================================${RESET}"
    Write-Host ""

    # Set SSoT environment variable if provided
    if ($ExpectedCmdlineSha256 -and $ExpectedCmdlineSha256.Trim().Length -gt 0) {
        $env:EXPECTED_CMDLINE_SHA256 = $ExpectedCmdlineSha256.Trim()
        Write-Host "ENV EXPECTED_CMDLINE_SHA256 set (fail-closed SSoT enabled)"
    }

    # Validate inputs
    if (-not (Test-Path $PythonExe)) {
        Write-Fail "Python executable not found: $PythonExe"
        exit 1
    }
    if (-not (Test-Path $SmokeScript)) {
        Write-Fail "Smoke script not found: $SmokeScript"
        exit 1
    }
    if (-not (Test-Path $GateScript)) {
        Write-Fail "Gate script not found: $GateScript"
        exit 1
    }

    # --- STAGE 1: SMOKE TEST ---
    Write-Stage "SMOKE" "Running night test for $SmokeHours hours..."

    $smokeCmd = "`"$PythonExe`" `"$SmokeScript`" $SmokeHours"
    $code = Exec-Command -Description "Smoke test" -Command $smokeCmd

    if ($code -ne 0) {
        Write-Fail "Smoke test failed with exit code $code"
        if ($code -eq 2) {
            Write-Host "Reason: Insufficient data for reliable verdict"
        }
        Write-Host ""
        Write-Host "${RED}NO-GO: Pipeline aborted at SMOKE stage${RESET}"
        exit $code
    }
    Write-Pass "Smoke test completed"

    # --- STAGE 2: QUALITY GATE ---
    Write-Stage "GATE" "Running AI quality gate..."

    $gateCmd = "`"$PythonExe`" `"$GateScript`""
    $code = Exec-Command -Description "Quality gate" -Command $gateCmd

    if ($code -ne 0) {
        Write-Fail "Quality gate failed with exit code $code"
        Write-Host ""
        Write-Host "${RED}NO-GO: Pipeline aborted at GATE stage${RESET}"
        exit 1
    }
    Write-Pass "Quality gate passed"

    # --- STAGE 3: HEALTH PROBE ---
    Write-Stage "PROBE" "Validating health file ($HealthStableSamples samples required)..."

    # Find health probe script
    $probeScript = Join-Path -Path (Split-Path -Parent $SmokeScript) -ChildPath "health_probe_v5.py"
    if (-not (Test-Path $probeScript)) {
        Write-Fail "Health probe script not found: $probeScript"
        exit 1
    }

    # Validate parameters
    if ($HealthStableSamples -lt 1) {
        Write-Fail "HealthStableSamples must be >= 1"
        exit 1
    }
    if ($HealthSampleIntervalSec -lt 1) {
        Write-Fail "HealthSampleIntervalSec must be >= 1"
        exit 1
    }

    for ($i = 1; $i -le $HealthStableSamples; $i++) {
        Write-Host "Probe sample $i/$HealthStableSamples..."

        $probeCmd = "`"$PythonExe`" `"$probeScript`" `"$HealthPath`" $HealthMaxAgeSec"
        $code = Exec-Command -Description "Health probe" -Command $probeCmd

        if ($code -ne 0) {
            Write-Fail "Health probe failed on sample $i with exit code $code"
            Write-Host ""
            Write-Host "${RED}NO-GO: Pipeline aborted at PROBE stage${RESET}"
            exit 1
        }

        if ($i -lt $HealthStableSamples) {
            Write-Host "Waiting $HealthSampleIntervalSec seconds..."
            Start-Sleep -Seconds $HealthSampleIntervalSec
        }
    }
    Write-Pass "Health probe passed ($HealthStableSamples consecutive samples)"

    # --- STAGE 4: START ---
    Write-Stage "START" "Launching start command..."

    $code = Exec-Command -Description "Start command" -Command $StartCommand

    if ($code -ne 0) {
        Write-Fail "Start command failed with exit code $code"
        Write-Host ""
        Write-Host "${RED}NO-GO: Pipeline aborted at START stage${RESET}"
        exit 1
    }

    Write-Host ""
    Write-Host "${GREEN}================================================================${RESET}"
    Write-Host "${GREEN}  GO: Pipeline completed successfully!${RESET}"
    Write-Host "${GREEN}================================================================${RESET}"
    Write-Host ""

    exit 0
}

# Run main with exception handling
try {
    Main
}
catch {
    Write-Fail ("Exception: " + $_.Exception.Message)
    Write-Host ""
    Write-Host "${RED}NO-GO: Pipeline aborted due to exception${RESET}"
    exit 1
}
