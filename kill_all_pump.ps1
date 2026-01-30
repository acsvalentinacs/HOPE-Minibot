# Kill all pump detector processes
Write-Host "Killing all python processes..."
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process nohup -ErrorAction SilentlyContinue | Stop-Process -Force

Write-Host "Checking remaining..."
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, ProcessName

Write-Host "DONE"
