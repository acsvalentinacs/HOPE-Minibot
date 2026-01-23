# Force restart tgbot - kill all instances and start fresh
$ErrorActionPreference = "Stop"

Write-Host "Force restarting tgbot..."

# Kill any python process with tg_bot in command line
$allPython = Get-Process -Name python*, python3* -ErrorAction SilentlyContinue
foreach ($proc in $allPython) {
    try {
        $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($proc.Id)").CommandLine
        if ($cmdLine -like "*tg_bot*") {
            Write-Host "Killing tgbot process PID=$($proc.Id)"
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    } catch {
        # Ignore errors
    }
}

Start-Sleep -Seconds 2

# Remove lock files
$locks = @(
    "C:\Users\kirillDev\Desktop\TradingBot\state\pids\tg_bot_simple.lock",
    "C:\Users\kirillDev\Desktop\TradingBot\minibot\state\pids\tg_bot_simple.lock"
)
foreach ($lock in $locks) {
    if (Test-Path $lock) {
        Remove-Item $lock -Force -ErrorAction SilentlyContinue
        Write-Host "Removed lock: $lock"
    }
}

# Start new tgbot
Write-Host "Starting tgbot..."
Set-Location "C:\Users\kirillDev\Desktop\TradingBot"

$proc = Start-Process -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList "-u", "minibot\tg_bot_simple.py" `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 3

# Check if still running
$check = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
if ($check) {
    Write-Host "SUCCESS: tgbot running with PID=$($proc.Id)"
} else {
    Write-Host "WARNING: Process exited - check logs"
}

Write-Host "Done."
