# kripto_bot

ICT (Inner Circle Trader) metodolojisi ile kripto futures piyasasında scalping
sinyalleri üretip paper trading yapan bot.

## Durum

🚧 **Aktif geliştirme — Faz 0 (altyapı kurulumu)**

Detay için [docs/memory_bank/progress.md](docs/memory_bank/progress.md).

## Ne yapar?

- Binance USDT-M Futures'tan 1m/5m/1h OHLCV verisi çeker (WebSocket + REST)
- ICT konseptlerini (FVG, Order Block, Liquidity Sweep, BOS/CHoCH, Killzone)
  algoritmik kurallara çevirir
- "Liquidity Sweep + FVG Entry" setup'ına göre sinyal üretir
- Sanal portföy üzerinde paper trade açar/kapatır, P&L takip eder
- Sinyalleri ve P&L raporlarını Telegram'a gönderir
- (Faz 4) Tarihsel veri üzerinde backtest + parametre optimizasyonu

**Gerçek emir göndermez.** Bu bir araştırma/sinyal botu.

## Coin evreni (MVP)

BTC, ETH, SOL, BNB, XRP — Binance USDT-M perpetual futures.

## Stack

- **Dil:** Python 3.11+
- **Veri:** Polars + Parquet (OHLCV cache), SQLite (trade/equity log)
- **Borsa:** CCXT + Binance native WebSocket
- **Notify:** python-telegram-bot
- **Scheduler:** APScheduler + asyncio
- **Test:** pytest
- **Config:** pydantic-settings + YAML
- **Log:** loguru

Rust'a şimdilik gerek yok; backtest fazında profile edip gerçek bottleneck
çıkarsa sıcak yolu PyO3 ile Rust'a taşıyacağız.
[docs/memory_bank/decisions.md](docs/memory_bank/decisions.md) → ADR-0003.

## Kurulum

İki yol var: **Docker** (önerilen, izole runtime) veya **lokal uv** (geliştirme için).

### Docker (önerilen)

`docker-compose.yml` repo kökünde, `Dockerfile` `docker/` altında.

```powershell
# .env doldur
Copy-Item .env.example .env
# notepad .env  → BINANCE_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# Build + run
docker compose up -d --build

# Log takip
docker compose logs -f
```

Detay: [docs/deployment.md](docs/deployment.md)

### Lokal (uv ile)

Proje [uv](https://docs.astral.sh/uv/) ile yönetiliyor.

```powershell
# Sanal ortam + bağımlılıklar
uv venv
.\.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
# veya tek komutla: uv sync --extra dev

# Config
Copy-Item .env.example .env
# .env içine BINANCE_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID doldur

# Sanity check (network yok)
python -m src.main check

# Testler
pytest

# Telegram doğrulama
python -m scripts.test_telegram

# Tarihsel veri indirme (örn. son 6 ay, tüm konfig)
python -m src.data.downloader all --from 2025-11-01

# Canlı (paper) bot
python -m src.main run
```

## Dokümantasyon

- [docs/PRD.md](docs/PRD.md) — Product Requirements
- [docs/architecture.md](docs/architecture.md) — Mimari
- [AGENT.md](AGENT.md) — Bu repo üzerinde çalışan AI agent için talimat
- [docs/memory_bank/](docs/memory_bank/) — Kararlar, ilerleme, sözlük

## Risk uyarısı

Bu yazılım eğitim ve araştırma amaçlıdır. Üretilen sinyaller yatırım tavsiyesi
değildir. Gerçek parayla işlem açan bir kullanım için tasarlanmamıştır.
