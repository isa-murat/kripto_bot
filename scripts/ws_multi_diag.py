"""Çoklu endpoint + çoklu kütüphane WS tanı script'i.

Test eder:
    1. Binance USDT-M Futures - combined stream (mevcut yöntem)
    2. Binance USDT-M Futures - single stream (alternatif path)
    3. Binance Spot - single stream (farklı host:port)
    4. Aynı endpoint aiohttp ile (websockets kütüphanesi yerine)

Run:
    python -m scripts.ws_multi_diag
"""

from __future__ import annotations

import asyncio
import json
import time

import websockets

ENDPOINTS = [
    ("Futures combined", "wss://fstream.binance.com/stream?streams=btcusdt@kline_1m"),
    ("Futures single",   "wss://fstream.binance.com/ws/btcusdt@kline_1m"),
    ("Spot single",      "wss://stream.binance.com:9443/ws/btcusdt@kline_1m"),
]

LISTEN_PER_ENDPOINT = 15  # seconds


async def test_websockets_lib(name: str, url: str) -> tuple[bool, int]:
    """Returns (connected_ok, msg_count)."""
    print(f"\n--- [websockets] {name} ---")
    print(f"    {url}")
    try:
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            print("    ✅ TCP+TLS+WS handshake OK")
            n = 0
            start = time.time()
            while time.time() - start < LISTEN_PER_ENDPOINT:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                    n += 1
                    if n == 1:
                        print(f"    ✅ FIRST MESSAGE received: {raw[:120]}...")
                except asyncio.TimeoutError:
                    pass
            print(f"    {LISTEN_PER_ENDPOINT}s içinde {n} mesaj")
            return (True, n)
    except Exception as e:
        print(f"    ❌ Hata: {type(e).__name__}: {e}")
        return (False, 0)


async def test_aiohttp_lib(name: str, url: str) -> tuple[bool, int]:
    print(f"\n--- [aiohttp]    {name} ---")
    print(f"    {url}")
    try:
        import aiohttp
    except ImportError:
        print("    (aiohttp yok, atlanıyor)")
        return (False, 0)

    try:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(url, heartbeat=20) as ws:
                print("    ✅ TCP+TLS+WS handshake OK")
                n = 0
                start = time.time()
                while time.time() - start < LISTEN_PER_ENDPOINT:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=3)
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            n += 1
                            if n == 1:
                                print(f"    ✅ FIRST MESSAGE received: {msg.data[:120]}...")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            print(f"    ⚠ Connection ended: {msg.type}")
                            break
                    except asyncio.TimeoutError:
                        pass
                print(f"    {LISTEN_PER_ENDPOINT}s içinde {n} mesaj")
                return (True, n)
    except Exception as e:
        print(f"    ❌ Hata: {type(e).__name__}: {e}")
        return (False, 0)


async def main() -> None:
    print("=" * 70)
    print("WS multi-endpoint diagnostic")
    print("=" * 70)

    results = []

    for name, url in ENDPOINTS:
        ok, n = await test_websockets_lib(name, url)
        results.append(("websockets", name, ok, n))

    # aiohttp sadece futures combined ile dene (kontrol amaçlı)
    name, url = ENDPOINTS[0]
    ok, n = await test_aiohttp_lib(name, url)
    results.append(("aiohttp", name, ok, n))

    print("\n" + "=" * 70)
    print("ÖZET")
    print("=" * 70)
    for lib, name, ok, n in results:
        status = "✅" if n > 0 else ("⚠ connect OK / msg yok" if ok else "❌ connect fail")
        print(f"  {lib:12} | {name:20} | msgs={n:3} | {status}")

    any_ok = any(r[3] > 0 for r in results)
    if not any_ok:
        print("\n❌ Hiçbir endpoint mesaj göndermedi.")
        print("   Lokal sorun çok olası: Windows Defender / antivirus / firewall WS")
        print("   payload'larını filtreliyor olabilir. Test:")
        print("   1. Windows Defender Firewall'da python.exe için outbound izin var mı?")
        print("   2. Antivirus 'Network protection' / 'Web shield' geçici kapatıp tekrar dene")
        print("   3. Romanya VPN provider farklı protokol (WireGuard vs OpenVPN) destekliyorsa onu dene")
    elif any(r[0] == "aiohttp" and r[3] > 0 for r in results) and not any(
        r[0] == "websockets" and r[3] > 0 for r in results
    ):
        print("\n⚠ aiohttp çalışıyor, websockets çalışmıyor.")
        print("   Çözüm: ws_stream.py'ı aiohttp.ws_connect ile yeniden yaz.")
    else:
        print("\n✅ En az bir endpoint çalışıyor — çalışan endpoint'i kullanabiliriz.")


if __name__ == "__main__":
    asyncio.run(main())
