"""WebSocket diagnostic: 70 sn boyunca ham Binance kline mesajlarını dinle.

Soruları cevaplar:
    1. Bu ağdan Binance WS erişilebiliyor mu?
    2. Mesajlar akıyor mu? (Binance saniyede ~1 update gönderir)
    3. `x: true` (bar kapanışı) gerçekten geliyor mu? (1m'de dakikada 1 kez)

Run:
    python -m scripts.ws_diag
"""

from __future__ import annotations

import asyncio
import json
import time

import websockets

URL = "wss://fstream.binance.com/stream?streams=btcusdt@kline_1m"
LISTEN_SECONDS = 70  # 1m'de en az 1 closed bar görmek için


async def main() -> None:
    print(f"Connecting to {URL}")
    print(f"Listening for {LISTEN_SECONDS} seconds...\n")
    try:
        async with websockets.connect(URL, ping_interval=20, ping_timeout=20) as ws:
            print("✅ Connected.\n")
            start = time.time()
            n_total = 0
            n_closed = 0
            while time.time() - start < LISTEN_SECONDS:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    print("⚠ no message in last 5s")
                    continue
                msg = json.loads(raw)
                k = msg.get("data", {}).get("k", {})
                n_total += 1
                x = k.get("x")
                if x is True:
                    n_closed += 1
                    print(
                        f"[CLOSED]  symbol={k.get('s')} tf={k.get('i')} "
                        f"close={k.get('c')} vol={k.get('v')}"
                    )
                else:
                    if n_total % 10 == 1:  # her 10 update'te bir göster
                        print(
                            f"[update]  symbol={k.get('s')} close={k.get('c')} "
                            f"x={x} (msg #{n_total})"
                        )
            print(f"\n--- Summary ---")
            print(f"Total messages received: {n_total}")
            print(f"Closed bars (x=True):    {n_closed}")
            if n_total == 0:
                print("\n❌ NO MESSAGES — WS bağlantı kuruldu ama veri gelmedi.")
                print("   Olası: firewall, Binance bölgesel kısıtlama, proxy/VPN gerekli.")
            elif n_closed == 0:
                print("\n⚠ Mesajlar geliyor ama 70sn içinde hiç closed bar yok — beklenmedik.")
            else:
                print("\n✅ WS akışı sağlıklı.")
    except Exception as e:
        print(f"\n❌ WS error: {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
