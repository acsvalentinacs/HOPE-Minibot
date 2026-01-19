# HOPE E2E Test Script for PowerShell
# Usage: .\scripts\test_e2e.ps1

$ErrorActionPreference = "Stop"
$TOKEN = $env:FRIEND_BRIDGE_TOKEN
if (-not $TOKEN) {
    Write-Host "ERROR: Set FRIEND_BRIDGE_TOKEN environment variable first" -ForegroundColor Red
    Write-Host "  `$env:FRIEND_BRIDGE_TOKEN = 'your_token_here'"
    exit 1
}

$BASE_URL = "http://127.0.0.1:18765"
$HEADERS = @{
    "Content-Type" = "application/json"
    "X-HOPE-Token" = $TOKEN
}

function Test-Healthz {
    Write-Host "`n=== E2E-0: Healthz ===" -ForegroundColor Cyan
    try {
        $response = Invoke-RestMethod -Uri "$BASE_URL/healthz" -Method Get
        Write-Host "OK: version=$($response.version), auth_enabled=$($response.auth_enabled)" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "FAIL: Cannot connect to Friend Bridge" -ForegroundColor Red
        Write-Host "  Make sure tunnel is open: scripts\friend_chat_tunnel.cmd"
        return $false
    }
}

function Test-E2E1-Legacy {
    Write-Host "`n=== E2E-1: Legacy Message ===" -ForegroundColor Cyan
    $body = @{
        to = "gpt"
        message = "E2E-1 test from PowerShell $(Get-Date -Format 'HH:mm:ss')"
        context = "friend_chat"
    } | ConvertTo-Json

    try {
        $response = Invoke-RestMethod -Uri "$BASE_URL/send" -Method Post -Headers $HEADERS -Body $body
        if ($response.ok) {
            Write-Host "OK: ipc_id=$($response.ipc_id)" -ForegroundColor Green
            Write-Host "    filename=$($response.filename)"
            return $true
        } else {
            Write-Host "FAIL: $($response.error)" -ForegroundColor Red
            return $false
        }
    } catch {
        Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

function Test-E2E2-TaskRequest {
    Write-Host "`n=== E2E-2: Task Request ===" -ForegroundColor Cyan
    $correlationId = [guid]::NewGuid().ToString()

    $body = @{
        to = "gpt"
        type = "task_request"
        payload = @{
            correlation_id = $correlationId
            context = "friend_chat"
            message = "Give me a simple verification task"
        }
    } | ConvertTo-Json -Depth 3

    try {
        $response = Invoke-RestMethod -Uri "$BASE_URL/send" -Method Post -Headers $HEADERS -Body $body
        if ($response.ok) {
            Write-Host "OK: type=task_request sent" -ForegroundColor Green
            Write-Host "    correlation_id=$correlationId"
            Write-Host "    ipc_id=$($response.ipc_id)"
            return @{ok=$true; correlation_id=$correlationId; ipc_id=$response.ipc_id}
        } else {
            Write-Host "FAIL: $($response.error)" -ForegroundColor Red
            return @{ok=$false}
        }
    } catch {
        Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
        return @{ok=$false}
    }
}

function Get-ClaudeInbox {
    param([int]$Limit = 5)

    Write-Host "`n=== Reading Claude Inbox ===" -ForegroundColor Cyan
    try {
        $response = Invoke-RestMethod -Uri "$BASE_URL/inbox/claude?limit=$Limit" -Method Get -Headers $HEADERS
        Write-Host "OK: $($response.count) messages" -ForegroundColor Green
        foreach ($msg in $response.messages) {
            Write-Host "  [$($msg.type)] from=$($msg.from) id=$($msg.id.Substring(0,20))..."
        }
        return $response
    } catch {
        Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }
}

function Send-Result {
    param(
        [string]$CorrelationId,
        [string]$ReplyTo,
        [string]$Outcome = "pass"
    )

    Write-Host "`n=== E2E-2: Sending Result ===" -ForegroundColor Cyan

    $body = @{
        to = "gpt"
        type = "result"
        reply_to = $ReplyTo
        payload = @{
            correlation_id = $CorrelationId
            outcome = $Outcome
            changed_files = @()
            artifact_paths = @()
            commands_run = @(
                @{cmd = "echo test"; exit_code = 0; output = "test"}
            )
        }
    } | ConvertTo-Json -Depth 4

    try {
        $response = Invoke-RestMethod -Uri "$BASE_URL/send" -Method Post -Headers $HEADERS -Body $body
        if ($response.ok) {
            Write-Host "OK: result sent" -ForegroundColor Green
            Write-Host "    ipc_id=$($response.ipc_id)"
            return $true
        } else {
            Write-Host "FAIL: $($response.error)" -ForegroundColor Red
            return $false
        }
    } catch {
        Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

# Main
Write-Host "============================================" -ForegroundColor Yellow
Write-Host "  HOPE E2E Test Suite" -ForegroundColor Yellow
Write-Host "============================================" -ForegroundColor Yellow

if (-not (Test-Healthz)) {
    exit 1
}

$e2e1 = Test-E2E1-Legacy
$e2e2 = Test-E2E2-TaskRequest

Write-Host "`n============================================" -ForegroundColor Yellow
Write-Host "  Results:" -ForegroundColor Yellow
Write-Host "  E2E-1 (legacy):  $(if($e2e1){'PASS'}else{'FAIL'})" -ForegroundColor $(if($e2e1){'Green'}else{'Red'})
Write-Host "  E2E-2 (task_request): $(if($e2e2.ok){'PASS'}else{'FAIL'})" -ForegroundColor $(if($e2e2.ok){'Green'}else{'Red'})
Write-Host "============================================" -ForegroundColor Yellow

if ($e2e2.ok) {
    Write-Host "`nTo complete E2E-2 cycle:" -ForegroundColor Cyan
    Write-Host "  1. Wait for GPT to respond with 'task' type"
    Write-Host "  2. Read inbox: Get-ClaudeInbox"
    Write-Host "  3. Send result: Send-Result -CorrelationId '$($e2e2.correlation_id)' -ReplyTo 'sha256:...'"
}
