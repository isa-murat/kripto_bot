# AGENT.md — Bu repo üzerinde çalışan Claude'a talimat

> Bu dosyayı **her oturumun başında oku.** İçinde projenin amacı, kuralları,
> aktif kararlar ve nereye bakacağına dair pointer'lar var. Sen balık hafızalısın
> — bu dosya ve `docs/memory_bank/` senin "kalıcı hafızan".

## 1. Proje tek cümlede

ICT (Inner Circle Trader) metodolojisi ile kripto futures piyasasında
**scalping sinyalleri üretip paper trading yapan bot** — MVP odak: Binance USDT-M
futures, top 5 coin (BTC/ETH/SOL/BNB/XRP), 1h bias → 5m entry, "Liquidity Sweep
+ FVG Entry" setup'ı.

## 2. Kullanıcı hakkında

- Türkçe yazıyor, sen de Türkçe cevap ver. Kod ve teknik terimler İngilizce kalır.
- Email: isamurat233@gmail.com
- Bu projeyi **uzun vadede geliştirmek istiyor**, tek atımlık bir iş değil.
- Tartışmayı seviyor — büyük kararlar öncesi `AskUserQuestion` ile seçenek sun,
  varsayım yapıp ilerleme.

## 3. Her oturumda yapacakların (sırayla)

1. **Bu dosyayı oku** (zaten okuyorsun).
2. `docs/memory_bank/progress.md` → şu an hangi fazdayız, ne kaldı?
3. `docs/memory_bank/decisions.md` → hangi kararlar verildi, neden?
4. `docs/memory_bank/open_questions.md` → askıda ne var?
5. Gerekirse `docs/memory_bank/glossary.md` → ICT terimi anlamadıysan.
6. İş bitince **`progress.md`, `decisions.md`, `open_questions.md`'yi güncelle.**
   Bu kritik. Güncellemezsen bir sonraki oturum bağlam kaybeder.

## 4. Verilmiş kararlar (özet — detay `decisions.md`'de)

| Konu | Karar |
|---|---|
| Dil/stack | Python 3.11+, Polars (pandas yerine), CCXT, python-telegram-bot, loguru, pydantic-settings, APScheduler, pytest |
| Borsa | Binance USDT-M Futures |
| Mod | Sadece sinyal + paper trading. Gerçek emir YOK. |
| Strateji ailesi | ICT (Inner Circle Trader) |
| MVP setup | Liquidity Sweep + FVG Entry (1h bias → 5m entry) |
| Coin evreni | BTC, ETH, SOL, BNB, XRP (perp futures) |
| Rust | Şimdilik HAYIR. Faz 4'te profile et, gerçek bottleneck çıkarsa hot path'i Rust'a taşı (PyO3 + maturin). |
| Memory bank | Repo içi: `docs/memory_bank/`. Ek olarak global memory de kullanılır. |

## 5. Mimari özet

```
WebSocket (Binance) → OHLCV cache (Polars + Parquet)
                          ↓
           ┌──────── ICT primitives ────────┐
           │ structure │ poi │ liquidity │ bias │ killzone │
           └──────────────────┬──────────────────────────────┘
                              ↓
                    strategies/sweep_fvg.py
                              ↓
                       signal_router
                              ↓
              ┌───────────────┼───────────────┐
        paper_broker      position_mgr     notify (Telegram)
              │
         SQLite (trades, equity, signals)
```

Detay: [docs/architecture.md](docs/architecture.md)

## 6. Yol haritası — fazlar

- **Faz 0** — Altyapı: iskelet, config, log, CCXT, WS, Parquet cache, Telegram hello
- **Faz 1** — ICT primitives: structure, poi, liquidity, bias, killzone (her biri unit-testli)
- **Faz 2** — Sweep+FVG strateji modülü + signal router
- **Faz 3** — Paper trading engine + position manager + Telegram bildirimleri
- **Faz 4** — Backtest engine + metrikler (burada Rust gerekirse devreye)
- **Faz 5** — İyileştirme: filtreler, parametre tuning, monitoring

Aktif faz ve görevler: [docs/memory_bank/progress.md](docs/memory_bank/progress.md)

## 7. Davranış kuralları

- **Dokümante et:** Her büyük karar `decisions.md`'ye ADR tarzında eklenir.
  Format: tarih, bağlam, seçilen, alternatifler, neden.
- **Open questions yönet:** Cevaplayamadığın ya da kullanıcıya sormak istediğin
  her şeyi `open_questions.md`'ye yaz. Cevaplanınca taşı/sil.
- **Glossary:** Yeni ICT konsepti kullanırsan `glossary.md`'ye ekle.
- **Progress disiplini:** Bir görevi tamamlayınca `progress.md`'de tikle.
  Yarım kalan iş varsa "in progress" altında bırak ve nerede kaldığını yaz.
- **Look-ahead bias:** Backtest ve sinyal üretiminde **bar kapanmadan** karar
  verme. Hep `t-1` ve öncesine bak. Bu kuralı bozma.
- **Türkçe vs İngilizce:** Kod, değişken, fonksiyon adları, log mesajları,
  commit mesajları İngilizce. Docs ve memory bank Türkçe.
- **Yorum yazma alışkanlığı:** Niye yapıldığı belli olmayan satıra kısa yorum.
  "Ne yaptığını" anlatan yorum yazma — kod zaten anlatıyor.
- **Test:** ICT primitives için unit test ZORUNLU. Bilinen mum dizilerinden
  sentetik fixture üret, beklenen FVG/swing/sweep'i assert et.

## 8. Komutlar

Proje **uv** ile yönetiliyor. `pip` yerine `uv pip` veya `uv run` kullan.

```powershell
# Bağımlılık kurulumu / sync
uv sync --extra dev
# veya: uv pip install -e ".[dev]"

# Sanity check (network yok)
python -m src.main check

# Test
pytest

# Tarihsel veri (tek sembol)
python -m src.data.downloader download --symbol BTCUSDT --tf 5m --from 2025-11-01

# Tarihsel veri (tüm config × {entry, bias} TF)
python -m src.data.downloader all --from 2025-11-01

# Telegram smoke test
python -m scripts.test_telegram

# Canlı (paper) bot
python -m src.main run

# Backtest (Faz 4'te aktif olacak)
python -m src.backtest.runner --symbol BTCUSDT --from 2025-11-01
```

**Not:** `uv run python -m ...` da çalışır ve sanal ortamı aktive etmeden komut çalıştırmanı sağlar. Sanal ortam aktifse düz `python -m ...` da yeterlidir.

### Docker (önerilen — uzun süreli çalıştırma)

`docker-compose.yml` repo kökünde, `Dockerfile` ise `docker/` altında. Komutları
repo kökünden çalıştır:

```powershell
# Build + run (detached, production-style)
docker compose up -d --build
docker compose logs -f                # canlı log
docker compose ps                      # durum
docker compose restart                 # config değişikliğinden sonra
docker compose down                    # durdur (volume korunur)

# Geliştirme: hot reload — kod değişince otomatik sync + restart
# src/, config/, scripts/ → sync+restart
# pyproject.toml, docker/Dockerfile → rebuild
docker compose watch                   # foreground, Ctrl+C ile dur
docker compose up --watch -d           # detached + watch (alternatif)

# Tek seferlik komut (downloader, test, vs.)
docker compose run --rm kripto-bot pytest
docker compose run --rm kripto-bot python -m src.data.downloader all --from 2025-11-01
```

Detay: [docs/deployment.md](docs/deployment.md)

## 9. Önemli pointer'lar

- PRD: [docs/PRD.md](docs/PRD.md)
- Mimari: [docs/architecture.md](docs/architecture.md)
- Kararlar (ADR): [docs/memory_bank/decisions.md](docs/memory_bank/decisions.md)
- İlerleme: [docs/memory_bank/progress.md](docs/memory_bank/progress.md)
- Açık sorular: [docs/memory_bank/open_questions.md](docs/memory_bank/open_questions.md)
- Sözlük (ICT): [docs/memory_bank/glossary.md](docs/memory_bank/glossary.md)
