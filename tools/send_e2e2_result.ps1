# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-20 12:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 14:00:00 UTC
# === END SIGNATURE ===
<#
.SYNOPSIS
    Send E2E-2 result message to GPT via Friend Bridge
.DESCRIPTION
    Reads token from file (no exposure), computes sha256 of artifact, sends JSON
.PARAMETER CorrelationId
    Correlation ID for the E2E-2 task
.EXAMPLE
    .\send_e2e2_result.ps1 -CorrelationId "e2e2-artifacts-choice-20260120"
#>
param(
    [Parameter(Mandatory=$true)][string]$CorrelationId
)

function Get-EnvValueFromFile([string]$Path, [string]$Key) {
    $line = Select-String -Path $Path -Pattern ("^{0}=" -f [regex]::Escape($Key)) | Select-Object -First 1
    if (-not $line) { throw "Key '$Key' not found in $Path" }
    return ($line.Line.Split("=",2)[1]).Trim()
}

function Get-Sha256Hex([string]$Text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    $hash = $sha.ComputeHash($bytes)
    ($hash | ForEach-Object { $_.ToString("x2") }) -join ""
}

# Load token from file (never print it)
$token = Get-EnvValueFromFile "C:\secrets\hope\.env" "FRIEND_BRIDGE_TOKEN"

# Artifact content (what was actually changed)
$artifactContent = @"
Added SENSITIVE TOPICS fail-closed detection:
- Keywords: war, bomb, missile, drone, shelling, explosion, killed, dead, casualty, violence, rape, torture, terror, suicide (RU+EN)
- Function: is_sensitive_topic(text) -> bool
- Deterministic reply: empathy + no details + offer help
- No AI call, no delegation for sensitive topics
"@.Trim()

$artifactSha256 = Get-Sha256Hex $artifactContent

Write-Host "Artifact SHA256: $artifactSha256"

$body = @{
    to = "gpt"
    type = "result"
    payload = @{
        correlation_id = $CorrelationId
        outcome = "pass"
        decision = "Option B - Inline artifacts with sha256"
        rationale = @(
            "Simpler: no /artifact endpoint needed",
            "Atomic: artifact inside message JSON",
            "Verifiable: sha256 over artifact content"
        )
        changed_files = @(
            @{ path = "core/gpt_orchestrator_runner.py"; action = "modified" }
        )
        commands_run = @(
            @{ cmd = "python -m py_compile core/gpt_orchestrator_runner.py"; exit_code = 0; output = "SYNTAX OK" }
        )
        artifacts = @(
            @{
                name = "sensitive_detection_changes.txt"
                content_type = "text/plain"
                content = $artifactContent
                sha256 = $artifactSha256
            }
        )
    }
} | ConvertTo-Json -Depth 8

Write-Host "Sending result to Friend Bridge..."

try {
    $response = Invoke-RestMethod -Method Post `
        -Uri "http://127.0.0.1:18765/send" `
        -Headers @{ "X-HOPE-Token" = $token } `
        -ContentType "application/json; charset=utf-8" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body))

    Write-Host "Response:" -ForegroundColor Green
    $response | ConvertTo-Json -Depth 4
} catch {
    Write-Host "Error: $_" -ForegroundColor Red
    exit 1
}
