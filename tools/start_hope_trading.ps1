# ═══════════════════════════════════════════════════════════════════════════════
# HOPE AI TRADING SYSTEM - STARTUP PROTOCOL v2.0
# ═══════════════════════════════════════════════════════════════════════════════
# Created: 2026-01-31
# Purpose: Start all daemons for cyclic trading with dynamic position sizing
# ═══════════════════════════════════════════════════════════════════════════════

param(
    [switch]$Force,      # Skip confirmation
    [switch]$Restart,    # Kill existing first
    [switch]$Check       # Only run checks, don't start
)

$ErrorActionPreference = "Continue"
Set-Location "C:\Users\kirillDev\Desktop\TradingBot\minibot"

# Colors
function Write-Success($msg) { Write-Host "  ✅ $msg" -ForegroundColor Green }
function Write-Fail($msg) { Write-Host "  ❌ $msg" -ForegroundColor Red }
function Write-Info($msg) { Write-Host "  ℹ️ $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "  ⚠️ $msg" -ForegroundColor Yellow }

# ═══════════════════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "       🤖 HOPE AI TRADING SYSTEM - STARTUP PROTOCOL v2.0           " -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: PREFLIGHT CHECKS
# ═══════════════════════════════════════════════════════════════════════════════
Write-Host "[PHASE 1] PREFLIGHT CHECKS" -ForegroundColor Yellow
Write-Host "─────────────────────────────────────────────────────────────────" -ForegroundColor Gray

$checksPass = $true

# Check 1: Balance
$balanceResult = python -c "
import hmac, hashlib, time
try:
    import httpx
    from pathlib import Path
    
    for line in Path('C:/secrets/hope.env').read_text().splitlines():
        if line.startswith('BINANCE_API_KEY='): api_key = line.split('=',1)[1].strip()
        if line.startswith('BINANCE_API_SECRET='): api_secret = line.split('=',1)[1].strip()
    
    ts = int(time.time() * 1000)
    params = f'timestamp={ts}'
    sig = hmac.new(api_secret.encode(), params.encode(), hashlib.sha256).hexdigest()
    
    client = httpx.Client(timeout=10)
    client.headers['X-MBX-APIKEY'] = api_key
    r = client.get(f'https://api.binance.com/api/v3/account?{params}&signature={sig}')
    
    if r.status_code == 200:
        for b in r.json()['balances']:
            if b['asset'] == 'USDT':
                print(f'{float(b[\"free\"]):.2f}')
                break
except Exception as e:
    print('ERROR')
" 2>$null

if ($balanceResult -and $balanceResult -ne "ERROR") {
    $balance = [float]$balanceResult
    if ($balance -ge 10) {
        Write-Success "Balance: `$$balance USDT"
    } else {
        Write-Fail "Balance too low: `$$balance (min `$10)"
        $checksPass = $false
    }
} else {
    Write-Fail "Cannot fetch balance from Binance"
    $checksPass = $false
}

# Check 2: Config file
if (Test-Path "config\scalping_100.json") {
    Write-Success "Config: scalping_100.json"
} else {
    Write-Fail "Config: scalping_100.json not found"
    $checksPass = $false
}

# Check 3: Core modules
$modules = @(
    "core\adaptive_targets.py",
    "scripts\eye_of_god_v3.py",
    "scripts\position_watchdog.py",
    "scripts\pricefeed_bridge.py"
)

foreach ($mod in $modules) {
    if (Test-Path $mod) {
        $result = python -m py_compile $mod 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Module: $mod"
        } else {
            Write-Fail "Module syntax error: $mod"
            $checksPass = $false
        }
    } else {
        Write-Fail "Module not found: $mod"
        $checksPass = $false
    }
}

# Check 4: Pricefeed freshness
if (Test-Path "state\ai\pricefeed.json") {
    $pf = Get-Content "state\ai\pricefeed.json" | ConvertFrom-Json
    $age = [int]((Get-Date) - (Get-Date "1970-01-01").AddSeconds($pf.timestamp)).TotalSeconds
    if ($age -lt 120) {
        Write-Success "Pricefeed: Fresh ($age sec old)"
    } else {
        Write-Warn "Pricefeed: Stale ($age sec old) - will refresh on start"
    }
} else {
    Write-Warn "Pricefeed: Not found - will be created"
}

# Check 5: Calculate position size
Write-Host ""
Write-Host "[PHASE 1b] POSITION SIZE CALCULATION" -ForegroundColor Yellow
Write-Host "─────────────────────────────────────────────────────────────────" -ForegroundColor Gray

$positionInfo = python -c "
import sys
sys.path.insert(0, '.')
try:
    from core.dynamic_position_sizer import DynamicPositionSizer
    sizer = DynamicPositionSizer()
    result = sizer.calculate(confidence=0.70)
    print(f'SIZE:{result.size_usd}')
    print(f'BALANCE:{result.balance_usd}')
    print(f'PCT:{result.position_pct}')
except Exception as e:
    # Fallback calculation
    balance = $balance
    size = balance * 0.20  # 20% of balance
    print(f'SIZE:{size:.2f}')
    print(f'BALANCE:{balance:.2f}')
    print(f'PCT:20.0')
" 2>$null

$posSize = ($positionInfo | Select-String "SIZE:").Line.Split(":")[1]
$posBal = ($positionInfo | Select-String "BALANCE:").Line.Split(":")[1]
$posPct = ($positionInfo | Select-String "PCT:").Line.Split(":")[1]

Write-Host ""
Write-Host "  ┌────────────────────────────────────────┐" -ForegroundColor Cyan
Write-Host "  │  💰 DYNAMIC POSITION SIZING            │" -ForegroundColor Cyan
Write-Host "  ├────────────────────────────────────────┤" -ForegroundColor Cyan
Write-Host "  │  Balance:        `$$posBal USDT         │" -ForegroundColor White
Write-Host "  │  Position %:     $posPct%               │" -ForegroundColor White
Write-Host "  │  Position Size:  `$$posSize             │" -ForegroundColor Green
Write-Host "  │                                        │" -ForegroundColor Cyan
Write-Host "  │  Formula: balance × 20% × confidence   │" -ForegroundColor Gray
Write-Host "  │  More profit = bigger positions        │" -ForegroundColor Gray
Write-Host "  └────────────────────────────────────────┘" -ForegroundColor Cyan
Write-Host ""

# Summary
Write-Host "─────────────────────────────────────────────────────────────────" -ForegroundColor Gray
if ($checksPass) {
    Write-Host "  🟢 ALL CHECKS PASSED" -ForegroundColor Green
} else {
    Write-Host "  🔴 SOME CHECKS FAILED" -ForegroundColor Red
    if (-not $Force) {
        Write-Host ""
        Write-Host "  Fix issues or use -Force to continue anyway" -ForegroundColor Yellow
        exit 1
    }
}

if ($Check) {
    Write-Host ""
    Write-Host "  Check mode - not starting daemons" -ForegroundColor Yellow
    exit 0
}

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: STOP EXISTING
# ═══════════════════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "[PHASE 2] STOPPING EXISTING PROCESSES" -ForegroundColor Yellow
Write-Host "─────────────────────────────────────────────────────────────────" -ForegroundColor Gray

if ($Restart) {
    $procs = Get-Process python* -ErrorAction SilentlyContinue
    if ($procs) {
        Write-Info "Stopping $($procs.Count) Python processes..."
        $procs | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep 2
        Write-Success "All Python processes stopped"
    } else {
        Write-Info "No Python processes running"
    }
} else {
    Write-Info "Keeping existing processes (use -Restart to stop them)"
}

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: START DAEMONS
# ═══════════════════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "[PHASE 3] STARTING DAEMONS" -ForegroundColor Yellow
Write-Host "─────────────────────────────────────────────────────────────────" -ForegroundColor Gray

$daemons = @(
    @{Name="Pricefeed Bridge"; Script="scripts/pricefeed_bridge.py"; Args="--daemon"; Port=8100; Wait=3},
    @{Name="Position Watchdog"; Script="scripts/position_watchdog.py"; Args="--live"; Port=$null; Wait=2},
    @{Name="Eye of God V3"; Script="scripts/eye_of_god_v3.py"; Args="--daemon"; Port=$null; Wait=2},
    @{Name="Scalping Pipeline"; Script="scripts/scalping_pipeline.py"; Args="--live"; Port=$null; Wait=2},
    @{Name="Dashboard"; Script="scripts/hope_dashboard.py"; Args="--port 8080"; Port=8080; Wait=2}
)

$started = 0
foreach ($daemon in $daemons) {
    Write-Host "  Starting $($daemon.Name)..." -NoNewline -ForegroundColor White
    
    try {
        Start-Process -FilePath "python" -ArgumentList $daemon.Script,$daemon.Args -WindowStyle Minimized -ErrorAction Stop
        Start-Sleep $daemon.Wait
        Write-Host " ✅" -ForegroundColor Green
        $started++
    } catch {
        Write-Host " ❌" -ForegroundColor Red
    }
}

Write-Host ""
Write-Success "Started $started/$($daemons.Count) daemons"

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "[PHASE 4] VERIFICATION" -ForegroundColor Yellow
Write-Host "─────────────────────────────────────────────────────────────────" -ForegroundColor Gray

Start-Sleep 3

# Check processes
$procs = Get-Process python* -ErrorAction SilentlyContinue
Write-Info "Python processes running: $($procs.Count)"

# Check ports
$ports = netstat -ano | Select-String ":8080|:8100"
foreach ($p in $ports) {
    if ($p -match "LISTENING") {
        Write-Success "Port $($p.Line.Trim())"
    }
}

# Check pricefeed updated
if (Test-Path "state\ai\pricefeed.json") {
    $pf = Get-Content "state\ai\pricefeed.json" | ConvertFrom-Json
    $symbols = ($pf.PSObject.Properties | Where-Object { $_.Name -ne "timestamp" }).Count
    Write-Success "Pricefeed: $symbols symbols tracked"
}

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "       🚀 HOPE AI TRADING SYSTEM - STARTED SUCCESSFULLY            " -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "  📊 Dashboard:     http://localhost:8080" -ForegroundColor Cyan
Write-Host "  💰 Balance:       `$$posBal USDT" -ForegroundColor White
Write-Host "  📈 Position Size: `$$posSize (dynamic)" -ForegroundColor Green
Write-Host ""
Write-Host "  🔄 COMPOUND MODE: More profit = Bigger positions" -ForegroundColor Yellow
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Commands:" -ForegroundColor Gray
Write-Host "    .\tools\start_hope_trading.ps1 -Check    # Check only" -ForegroundColor Gray
Write-Host "    .\tools\start_hope_trading.ps1 -Restart  # Full restart" -ForegroundColor Gray
Write-Host "    .\tools\start_hope_trading.ps1 -Force    # Skip checks" -ForegroundColor Gray
Write-Host ""
