"""Quick smoke test: send a single Telegram message via TelegramNotifier.

Run:
    python -m scripts.test_telegram
"""

from __future__ import annotations

import asyncio

from src.notify.telegram import TelegramNotifier
from src.utils.logging import setup_logging


async def main() -> None:
    setup_logging()
    notifier = TelegramNotifier.from_env()
    await notifier.start()
    await notifier.send("✅ kripto_bot Telegram test — eğer bunu görüyorsan her şey yolunda.")
    await asyncio.sleep(2)  # give the worker time to flush
    await notifier.stop()


if __name__ == "__main__":
    asyncio.run(main())
