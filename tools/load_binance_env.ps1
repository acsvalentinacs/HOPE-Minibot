# Load BINANCE_* vars into Process env (no value printing)
$Path = "C:\secrets\hope\.env"
if (-not (Test-Path $Path)) { throw "NO_ENV_FILE: $Path" }

$lines = Get-Content -LiteralPath $Path -ErrorAction Stop
foreach ($line in $lines) {
  $t = $line.Trim()
  if (-not $t) { continue }
  if ($t.StartsWith("#")) { continue }
  $eq = $t.IndexOf("=")
  if ($eq -lt 1) { continue }

  $name = $t.Substring(0, $eq).Trim()
  if ($name -notmatch "^BINANCE_") { continue }

  $value = $t.Substring($eq + 1)
  [Environment]::SetEnvironmentVariable($name, $value, "Process")
}

Write-Host "== BINANCE_* present/length (Process) =="
Get-ChildItem Env: | Where-Object Name -like "BINANCE_*" |
  Sort-Object Name |
  ForEach-Object {
    [PSCustomObject]@{ Name=$_.Name; Present=$true; Length=$_.Value.Length }
  } | Format-Table -AutoSize
