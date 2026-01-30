# Check ALL python processes
Write-Host "=== ALL PYTHON PROCESSES ===" -ForegroundColor Red
Get-WmiObject Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
    Write-Host "PID: $($_.ProcessId)"
    Write-Host "CMD: $($_.CommandLine)"
    Write-Host "---"
}
Write-Host "=== END ===" -ForegroundColor Red
