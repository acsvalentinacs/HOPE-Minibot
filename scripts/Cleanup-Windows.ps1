# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-26T10:35:00Z
# Purpose: Clean up duplicate HOPE windows, keep only essential processes
# === END SIGNATURE ===

<#
.SYNOPSIS
    Cleans up duplicate HOPE-related windows and processes.
.DESCRIPTION
    Kills duplicate agent processes and old CMD windows.
    Keeps: 1 bot instance, 1 of each agent (if running).
#>

$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  HOPE Window Cleanup Tool" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================
# 1. SCAN PYTHON PROCESSES
# ============================================================
Write-Host "Scanning Python processes..." -ForegroundColor Yellow

$pythonProcs = Get-Process -Name "python" -ErrorAction SilentlyContinue | ForEach-Object {
    $proc = $_
    try {
        $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($proc.Id)" -ErrorAction SilentlyContinue).CommandLine
    } catch {
        $cmdLine = "unknown"
    }
    [PSCustomObject]@{
        PID = $proc.Id
        Name = $proc.ProcessName
        CommandLine = $cmdLine
        StartTime = $proc.StartTime
    }
}

if ($pythonProcs) {
    Write-Host ""
    Write-Host "Python processes found:" -ForegroundColor White

    # Categorize
    $botProcs = @()
    $executorProcs = @()
    $gptProcs = @()
    $claudeProcs = @()
    $otherProcs = @()

    foreach ($p in $pythonProcs) {
        $cmd = $p.CommandLine
        if ($cmd -match "tg_bot_simple") {
            $botProcs += $p
            Write-Host "  [BOT]      PID $($p.PID) - tg_bot_simple.py" -ForegroundColor Green
        }
        elseif ($cmd -match "claude_executor") {
            $executorProcs += $p
            Write-Host "  [EXECUTOR] PID $($p.PID) - claude_executor" -ForegroundColor Cyan
        }
        elseif ($cmd -match "gpt_orchestrator") {
            $gptProcs += $p
            Write-Host "  [GPT]      PID $($p.PID) - gpt_orchestrator" -ForegroundColor Magenta
        }
        elseif ($cmd -match "ipc_agent|claude_agent") {
            $claudeProcs += $p
            Write-Host "  [CLAUDE]   PID $($p.PID) - claude_agent" -ForegroundColor Blue
        }
        else {
            $otherProcs += $p
            $shortCmd = if ($cmd.Length -gt 60) { $cmd.Substring(0,60) + "..." } else { $cmd }
            Write-Host "  [OTHER]    PID $($p.PID) - $shortCmd" -ForegroundColor Gray
        }
    }

    Write-Host ""

    # Kill duplicates (keep newest of each type)
    $killed = 0

    # Keep only 1 bot (newest)
    if ($botProcs.Count -gt 1) {
        $sorted = $botProcs | Sort-Object StartTime -Descending
        $toKill = $sorted | Select-Object -Skip 1
        foreach ($p in $toKill) {
            Write-Host "Killing duplicate BOT PID $($p.PID)..." -ForegroundColor Red
            Stop-Process -Id $p.PID -Force -ErrorAction SilentlyContinue
            $killed++
        }
    }

    # Keep only 1 executor (newest)
    if ($executorProcs.Count -gt 1) {
        $sorted = $executorProcs | Sort-Object StartTime -Descending
        $toKill = $sorted | Select-Object -Skip 1
        foreach ($p in $toKill) {
            Write-Host "Killing duplicate EXECUTOR PID $($p.PID)..." -ForegroundColor Red
            Stop-Process -Id $p.PID -Force -ErrorAction SilentlyContinue
            $killed++
        }
    }

    # Keep only 1 GPT agent (newest)
    if ($gptProcs.Count -gt 1) {
        $sorted = $gptProcs | Sort-Object StartTime -Descending
        $toKill = $sorted | Select-Object -Skip 1
        foreach ($p in $toKill) {
            Write-Host "Killing duplicate GPT PID $($p.PID)..." -ForegroundColor Red
            Stop-Process -Id $p.PID -Force -ErrorAction SilentlyContinue
            $killed++
        }
    }

    # Keep only 1 Claude agent (newest)
    if ($claudeProcs.Count -gt 1) {
        $sorted = $claudeProcs | Sort-Object StartTime -Descending
        $toKill = $sorted | Select-Object -Skip 1
        foreach ($p in $toKill) {
            Write-Host "Killing duplicate CLAUDE PID $($p.PID)..." -ForegroundColor Red
            Stop-Process -Id $p.PID -Force -ErrorAction SilentlyContinue
            $killed++
        }
    }

    if ($killed -gt 0) {
        Write-Host ""
        Write-Host "Killed $killed duplicate Python processes" -ForegroundColor Yellow
    }
} else {
    Write-Host "  No Python processes found" -ForegroundColor Gray
}

# ============================================================
# 2. SCAN CMD WINDOWS
# ============================================================
Write-Host ""
Write-Host "Scanning CMD windows..." -ForegroundColor Yellow

$cmdProcs = Get-Process -Name "cmd" -ErrorAction SilentlyContinue | ForEach-Object {
    $proc = $_
    [PSCustomObject]@{
        PID = $proc.Id
        Title = $proc.MainWindowTitle
        StartTime = $proc.StartTime
    }
}

if ($cmdProcs) {
    # Group by title
    $groups = $cmdProcs | Where-Object { $_.Title } | Group-Object Title

    Write-Host ""
    Write-Host "CMD windows found:" -ForegroundColor White

    $cmdKilled = 0
    foreach ($g in $groups) {
        $title = $g.Name
        $count = $g.Count

        # Identify HOPE-related windows
        $isHope = $title -match "HOPE|Claude|GPT|Executor|Agent|Bot"

        if ($isHope) {
            Write-Host "  [$count] $title" -ForegroundColor $(if ($count -gt 1) { "Red" } else { "Green" })

            # Kill duplicates (keep newest)
            if ($count -gt 1) {
                $sorted = $g.Group | Sort-Object StartTime -Descending
                $toKill = $sorted | Select-Object -Skip 1
                foreach ($p in $toKill) {
                    Write-Host "      Killing duplicate PID $($p.PID)..." -ForegroundColor Red
                    Stop-Process -Id $p.PID -Force -ErrorAction SilentlyContinue
                    $cmdKilled++
                }
            }
        } else {
            Write-Host "  [$count] $title" -ForegroundColor Gray
        }
    }

    # Also show CMD windows without titles (potential orphans)
    $noTitle = $cmdProcs | Where-Object { -not $_.Title }
    if ($noTitle) {
        Write-Host "  [?] $($noTitle.Count) CMD windows without title (may be orphaned)" -ForegroundColor DarkYellow
    }

    if ($cmdKilled -gt 0) {
        Write-Host ""
        Write-Host "Killed $cmdKilled duplicate CMD windows" -ForegroundColor Yellow
    }
} else {
    Write-Host "  No CMD windows found" -ForegroundColor Gray
}

# ============================================================
# 3. SUMMARY
# ============================================================
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Cleanup Complete" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Show what's still running
Write-Host "Remaining HOPE processes:" -ForegroundColor White

$finalPython = Get-Process -Name "python" -ErrorAction SilentlyContinue
$finalCmd = Get-Process -Name "cmd" -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -match "HOPE|Claude|GPT|Executor|Agent|Bot" }

$botCount = 0
$agentCount = 0

foreach ($p in $finalPython) {
    try {
        $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($p.Id)" -ErrorAction SilentlyContinue).CommandLine
        if ($cmdLine -match "tg_bot_simple") {
            Write-Host "  [RUNNING] Telegram Bot (PID $($p.Id))" -ForegroundColor Green
            $botCount++
        }
        elseif ($cmdLine -match "executor|orchestrator|agent") {
            Write-Host "  [RUNNING] Agent (PID $($p.Id))" -ForegroundColor Cyan
            $agentCount++
        }
    } catch {}
}

if ($botCount -eq 0) {
    Write-Host "  [WARNING] No bot running! Start with: scripts\start_tg_bot.cmd" -ForegroundColor Red
}

Write-Host ""
Write-Host "To close ALL agent windows, use button '🔄 All Agents' in /chat menu" -ForegroundColor Gray
Write-Host ""
