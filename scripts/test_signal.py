# -*- coding: utf-8 -*-
"""Quick test signal sender"""
import httpx
import time

print("=== AUTOTRADER STATUS (BEFORE) ===")
try:
    resp = httpx.get("http://127.0.0.1:8200/status", timeout=5)
    status = resp.json()
    print(f"Mode: {status.get('mode', '?')}")
    print(f"Signals received: {status.get('stats', {}).get('signals_received', 0)}")
    print(f"Signals traded: {status.get('stats', {}).get('signals_traded', 0)}")
    print(f"Circuit breaker: {status.get('circuit_breaker', '?')}")
except Exception as e:
    print(f"Error: {e}")

print()
print("=== SENDING TEST SIGNAL ===")
signal = {
    "symbol": "KITEUSDT",
    "strategy": "HOPE_WHITELIST_TEST",
    "direction": "Long",
    "price": 0.15,
    "buys_per_sec": 55,  # >= 50 triggers SUPER_SCALP
    "delta_pct": 2.5,    # >= 2.0 for SCALP
    "vol_raise_pct": 150,
}
print(f"Signal: {signal}")

try:
    resp = httpx.post("http://127.0.0.1:8200/signal", json=signal, timeout=5)
    print(f"Response: {resp.json()}")
except Exception as e:
    print(f"Error: {e}")

time.sleep(2)

print()
print("=== AUTOTRADER STATUS (AFTER) ===")
try:
    resp = httpx.get("http://127.0.0.1:8200/status", timeout=5)
    status = resp.json()
    print(f"Mode: {status.get('mode', '?')}")
    print(f"Signals received: {status.get('stats', {}).get('signals_received', 0)}")
    print(f"Signals traded: {status.get('stats', {}).get('signals_traded', 0)}")
    print(f"Positions opened: {status.get('stats', {}).get('positions_opened', 0)}")
    print(f"Circuit breaker: {status.get('circuit_breaker', '?')}")
except Exception as e:
    print(f"Error: {e}")
