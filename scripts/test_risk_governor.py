# Quick test of Risk Governor config loading
import sys
sys.path.insert(0, ".")
from core.risk.risk_governor import RiskGovernor, RiskLimits, SignalFilters

# Load from config
limits = RiskLimits.from_yaml("risk_config.yaml")
filters = SignalFilters.from_yaml("risk_config.yaml")

print("=== RISK LIMITS ===")
print(f"Max position: ${limits.max_position_notional}")
print(f"Max daily loss: ${limits.max_daily_loss}")
print(f"Stop loss: {limits.stop_loss_percent}%")
print(f"Max orders: {limits.max_open_orders}")
print(f"Cooldown: {limits.cooldown_seconds}s")

print("")
print("=== SIGNAL FILTERS ===")
print(f"Min score: {filters.min_score}")
print(f"Min strength: {filters.min_strength}")
print(f"Whitelist: {filters.symbol_whitelist}")
print(f"Blacklist: {filters.symbol_blacklist}")

print("")
print("=== SIGNAL CHECK TESTS ===")
gov = RiskGovernor(config_path="risk_config.yaml")

# Test signal checks
tests = [
    ("BTCUSDT", 90.0, "STRONG"),   # Should PASS
    ("ETHUSDT", 80.0, "OK"),       # Should FAIL (score < 85)
    ("DOGEUSDT", 95.0, "STRONG"),  # Should FAIL (blacklist)
    ("XRPUSDT", 90.0, "STRONG"),   # Should FAIL (not in whitelist)
]

for symbol, score, strength in tests:
    result = gov.check_signal(symbol, score, strength)
    status = "PASS" if result.passed else f"FAIL ({result.code}: {result.reason})"
    print(f"  {symbol} score={score} {strength}: {status}")

print("")
print("CONFIG LOADED SUCCESSFULLY")
