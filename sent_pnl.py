import httpx

# Текущая цена SENT
r = httpx.get("https://api.binance.com/api/v3/ticker/price?symbol=SENTUSDT")
current_price = float(r.json()["price"])

# Расчёт PnL
entry_price = 0.04327
qty = 138.0
entry_value = entry_price * qty
current_value = current_price * qty
pnl = current_value - entry_value
pnl_pct = (current_price / entry_price - 1) * 100

# Targets
target_price = entry_price * 1.015  # +1.5%
stop_price = entry_price * 0.99    # -1.0%

print("=" * 50)
print("SENT POSITION STATUS")
print("=" * 50)
print(f"Entry:    ${entry_price:.5f}")
print(f"Current:  ${current_price:.5f}")
print(f"Target:   ${target_price:.5f} (+1.5%)")
print(f"Stop:     ${stop_price:.5f} (-1.0%)")
print("-" * 50)
print(f"Qty:      {qty} SENT")
print(f"Value:    ${current_value:.2f}")
print(f"PnL:      ${pnl:.2f} ({pnl_pct:+.2f}%)")
print("-" * 50)
if current_price >= target_price:
    print("STATUS: 🎯 TARGET HIT!")
elif current_price <= stop_price:
    print("STATUS: 🛑 STOP HIT!")
else:
    to_target = (target_price / current_price - 1) * 100
    to_stop = (stop_price / current_price - 1) * 100
    print(f"STATUS: ⏳ WAITING")
    print(f"  To Target: {to_target:+.2f}%")
    print(f"  To Stop:   {to_stop:+.2f}%")
print("=" * 50)
