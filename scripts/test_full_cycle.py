# -*- coding: utf-8 -*-
"""Full Trading Cycle Test - Signal to Execution"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'scripts')

print('=' * 80)
print('FULL TRADING CYCLE TEST - FROM SIGNAL TO EXECUTION')
print('=' * 80)
print()

# === STEP 1: Initialize TradingView AllowList ===
print('[STEP 1] TradingView Dynamic AllowList')
print('-' * 40)
from tradingview_allowlist import get_manager, is_tradingview_allowed, SAMPLE_GAINERS, SAMPLE_MOST_TRADED

tv_manager = get_manager()
tv_manager.update_from_gainers(SAMPLE_GAINERS)
tv_manager.update_from_most_traded(SAMPLE_MOST_TRADED)

print(f'  HOT list:     {len(tv_manager.get_hot_list())} coins')
print(f'  DYNAMIC list: {len(tv_manager.get_dynamic_list())} coins')
print(f'  HOT coins:    {tv_manager.get_hot_list()[:5]}')
print()

# === STEP 2: Check symbol in AllowList ===
print('[STEP 2] AllowList Check')
print('-' * 40)
test_symbol = 'ENSOUSDT'  # From gainers
allowed, list_name, mult = is_tradingview_allowed(test_symbol)
print(f'  Symbol: {test_symbol}')
print(f'  Allowed: {allowed}')
print(f'  List: {list_name}')
print(f'  Position multiplier: {mult}')
print()

# === STEP 3: Adaptive Target AI ===
print('[STEP 3] Adaptive Target AI')
print('-' * 40)
from adaptive_target_ai import calculate_adaptive_target

delta = 8.5  # Strong pump
buys = 95.0
target_result = calculate_adaptive_target(test_symbol, delta, buys, 200)

print(f'  Input: delta={delta}%, buys={buys}/s')
print(f'  Tier: {target_result["tier"]}')
print(f'  Target: {target_result["target_pct"]:.2f}%')
print(f'  Stop: {target_result["stop_pct"]:.2f}%')
print(f'  Timeout: {target_result["timeout_sec"]}s')
print(f'  Confidence: {target_result["confidence"]:.0%}')
print()

# === STEP 4: Signal Aggregator ===
print('[STEP 4] Signal Aggregator (Telegram Decision)')
print('-' * 40)
from signal_aggregator import SignalAggregator

agg = SignalAggregator()
agg_signal = {
    'symbol': test_symbol,
    'delta_pct': delta,
    'buys_per_sec': buys,
    'price': 12.50,
    'tier': target_result['tier'],
    'target_pct': target_result['target_pct'],
    'confidence': target_result['confidence'],
}
agg_result = agg.process_signal(agg_signal)

print(f'  Action: {agg_result["action"]}')
print(f'  Send to Telegram: {agg_result["send_now"]}')
print(f'  Reason: {agg_result["reason"]}')
print()

# === STEP 5: Build final signal ===
print('[STEP 5] Build Final Signal for AutoTrader')
print('-' * 40)
from datetime import datetime, timezone

final_signal = {
    'symbol': test_symbol,
    'timestamp': datetime.now(timezone.utc).isoformat(),
    'strategy': 'PUMP_DETECTION_AI_V2',
    'direction': 'Long',
    'price': 12.50,
    'buys_per_sec': buys,
    'delta_pct': delta,
    'vol_raise_pct': 200.0,
    'confidence': target_result['confidence'],
    'signal_type': target_result['tier'],
    'target_pct': target_result['target_pct'],
    'stop_pct': target_result['stop_pct'],
    'timeout_seconds': target_result['timeout_sec'],
    'adaptive_tier': target_result['tier'],
    'allowlist_source': f'tradingview_{list_name}',
    'position_multiplier': mult * target_result['confidence'],
    'telegram_action': agg_result['action'],
}

print(f'  Signal ID: sig_{int(datetime.now().timestamp())}_{test_symbol}')
print(f'  Direction: {final_signal["direction"]}')
print(f'  Price: ${final_signal["price"]}')
print(f'  Target: +{final_signal["target_pct"]:.2f}%')
print(f'  Stop: -{final_signal["stop_pct"]:.2f}%')
print(f'  Position size: {final_signal["position_multiplier"]*100:.0f}% of base')
print()

# === STEP 6: Send to AutoTrader ===
print('[STEP 6] Send to AutoTrader')
print('-' * 40)
import httpx

try:
    with httpx.Client(timeout=5) as client:
        resp = client.post('http://127.0.0.1:8200/signal', json=final_signal)
        print(f'  HTTP Status: {resp.status_code}')
        if resp.status_code == 200:
            data = resp.json()
            print(f'  AutoTrader Response: {data}')
        else:
            print(f'  Error: {resp.text[:200]}')
except Exception as e:
    print(f'  AutoTrader not available: {e}')

print()
print('=' * 80)
print('ARCHITECTURE SUMMARY')
print('=' * 80)
print('''
SIGNAL FLOW:
============
  Binance WS -> Pump Detector -> TradingView AllowList -> Adaptive Target AI
                                                                |
                                                                v
  Binance API <- AutoTrader <- Eye of God <- Signal Aggregator <-

WHO DOES WHAT:
==============
  1. TradingView AllowList: WHICH coins (HOT/DYNAMIC lists)
  2. Adaptive Target AI:    HOW MUCH profit (target% based on delta)
  3. Signal Aggregator:     TELEGRAM control (spam filter)
  4. Eye of God:            FINAL GATE (confidence check)
  5. AutoTrader:            EXECUTION (orders on Binance)

DYNAMIC TARGETS (Adaptive Target AI):
=====================================
  Delta 0.2%  -> NOISE     -> Skip (don't trade)
  Delta 0.8%  -> MICRO     -> Target 0.4%
  Delta 2.0%  -> SCALP     -> Target 1.3%
  Delta 5.0%  -> STRONG    -> Target 2.7%
  Delta 12.0% -> EXPLOSION -> Target 4.5%
  Delta 22.0% -> MOONSHOT  -> Target 5.7%
''')

print('=' * 80)
print('FULL CYCLE TEST COMPLETE')
print('=' * 80)
