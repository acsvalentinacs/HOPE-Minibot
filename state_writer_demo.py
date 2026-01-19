# minibot/state_writer_demo.py
import time, random
from minibot.state_writer import write_positions, write_balance, append_order, now_ts

def main():
    print("[demo] pumping state snapshots every 5s; writing one order per 15s")
    t0 = time.time()
    n  = 0
    while True:
        # псевдо-позиции
        pos = [{
            "symbol": "BTCUSDT",
            "side": "LONG",
            "qty": round(0.01 + 0.005*random.random(), 6),
            "entry_price": 98000 + 5000*random.random(),
            "unrealized_pnl": round(random.uniform(-25, 25), 2),
        }]
        write_positions(pos)

        # псевдо-баланс
        bal = {
            "total": {"USDT": round(1000 + 200*random.random(), 2)},
            "free":  {"USDT": round(200 + 50*random.random(), 2)},
        }
        write_balance(bal)

        # раз в ~15 сек добавим «ордер»
        if int(time.time() - t0) % 15 == 0:
            n += 1
            append_order({
                "ts": now_ts(),
                "symbol": "BTCUSDT",
                "side": "BUY" if n % 2 else "SELL",
                "type": "MARKET",
                "qty": round(25 + 5*random.random(), 3),
                "price": round(98000 + 6000*random.random(), 2),
                "status": "filled",
                "demo": True,
                "n": n,
            })

        time.sleep(5)

if __name__ == "__main__":
    main()
