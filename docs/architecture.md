# Architecture

## High-level akış

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Binance USDT-M Futures                       │
└────────────┬────────────────────────────────────────┬────────────────┘
             │ WebSocket (kline_1m, kline_5m,         │ REST (history)
             │  kline_1h)                             │
             ↓                                        ↓
      ┌──────────────┐                        ┌──────────────┐
      │  ws_stream   │                        │  downloader  │
      └──────┬───────┘                        └──────┬───────┘
             │                                       │
             └───────────────┬───────────────────────┘
                             ↓
                    ┌──────────────────┐
                    │  ohlcv_cache     │  (Polars + Parquet + RAM buffer)
                    └────────┬─────────┘
                             │ on_bar_close(symbol, tf)
                             ↓
        ┌────────────────────────────────────────────┐
        │             ICT primitives                  │
        │  ┌──────────┐ ┌─────┐ ┌───────────┐        │
        │  │structure │ │ poi │ │ liquidity │        │
        │  └──────────┘ └─────┘ └───────────┘        │
        │  ┌──────────┐ ┌──────────┐                 │
        │  │   bias   │ │ killzone │                 │
        │  └──────────┘ └──────────┘                 │
        └────────────────────┬───────────────────────┘
                             ↓
                ┌────────────────────────┐
                │ strategies/sweep_fvg   │
                └───────────┬────────────┘
                            ↓
                  ┌──────────────────┐
                  │  signal_router   │  (cooldown, dedup, filters)
                  └────┬─────────┬───┘
                       │         │
              ┌────────┘         └────────────┐
              ↓                                ↓
     ┌────────────────┐              ┌──────────────┐
     │ paper_broker   │              │   notify     │
     │ + position_mgr │              │ (Telegram)   │
     └───────┬────────┘              └──────────────┘
             │
       ┌─────┴──────┐
       ↓            ↓
   ┌───────┐   ┌──────────┐
   │SQLite │   │ Parquet  │
   │trades │   │ equity   │
   └───────┘   └──────────┘
```

## Klasör yapısı

```
kripto_bot/
├── AGENT.md                       # AI agent için talimat
├── README.md                      # Proje özeti
├── pyproject.toml                 # Bağımlılıklar + paket meta
├── .env.example                   # ENV şablonu
├── .gitignore
├── config/
│   ├── settings.yaml              # Genel ayarlar (semboller, log seviyesi vs.)
│   └── strategy_params.yaml       # ICT parametreleri (lookback, ATR, RR vs.)
├── docs/
│   ├── PRD.md
│   ├── architecture.md            # Bu dosya
│   └── memory_bank/
│       ├── README.md
│       ├── decisions.md           # ADR
│       ├── progress.md
│       ├── open_questions.md
│       └── glossary.md
├── src/
│   ├── __init__.py
│   ├── main.py                    # Entry point
│   ├── config.py                  # Pydantic settings
│   ├── data/
│   │   ├── __init__.py
│   │   ├── exchange.py            # CCXT wrapper
│   │   ├── ws_stream.py           # WebSocket
│   │   ├── ohlcv_cache.py         # Polars + Parquet
│   │   └── downloader.py
│   ├── ict/
│   │   ├── __init__.py
│   │   ├── structure.py           # swing, BOS, CHoCH, MSS
│   │   ├── poi.py                 # FVG, OB
│   │   ├── liquidity.py           # eq H/L, sweep
│   │   ├── bias.py                # 1h bias + premium/discount
│   │   └── killzone.py
│   ├── strategies/
│   │   ├── __init__.py
│   │   └── sweep_fvg.py
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── signal_router.py
│   │   ├── paper_broker.py
│   │   ├── position_mgr.py
│   │   └── scheduler.py
│   ├── notify/
│   │   ├── __init__.py
│   │   └── telegram.py
│   ├── backtest/
│   │   ├── __init__.py
│   │   ├── runner.py
│   │   └── metrics.py
│   └── utils/
│       ├── __init__.py
│       ├── logging.py
│       └── time.py                # UTC↔TR, killzone helpers
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_structure.py
│   ├── test_poi.py
│   └── test_liquidity.py
├── notebooks/                     # Debug/visualization (gitignore'da değil)
└── data/                          # Parquet/SQLite (gitignore'da)
```

## Veri akışı detayları

### 1. WebSocket akışı
- Binance `wss://fstream.binance.com/ws/<symbol>@kline_<interval>`
- Her sembol için 3 stream: 1m, 5m, 1h
- Bar **kapandığında** (`x: true` flag) downstream tetiklenir
- Bağlantı kopması: exponential backoff retry, REST ile gap fill

### 2. OHLCV cache
- **Hot buffer (RAM):** Her (symbol, tf) için son N=500 mum, Polars DataFrame
- **Cold storage (Parquet):** Günlük dosyalar `data/ohlcv/{symbol}/{tf}/{date}.parquet`
- Buffer dolduğunda eski mum disk'e yazılır

### 3. ICT pipeline
Her 5m bar kapanışında sırayla:
1. `bias.compute(symbol, htf='1h')` → `Bias` enum (BULL/BEAR/NEUTRAL)
2. Bias NEUTRAL ise dur
3. Killzone aktif değilse dur
4. `liquidity.find_pools(symbol, tf='5m')` + `liquidity.detect_sweep(latest_bar)`
5. Sweep yoksa dur
6. `structure.detect_mss(symbol, tf='5m')` (sweep sonrası karşı yöne kırılım)
7. MSS yoksa dur
8. `poi.find_fvg(symbol, tf='5m')` (MSS oluşturan displacement içinde)
9. FVG yoksa dur
10. `Signal(symbol, side, entry=fvg_mid, sl, tp, rr, meta)` üret
11. `signal_router` cooldown/dedup uygular, geçerse paper_broker'a gönder

### 4. Paper broker
- Limit emir gibi davranır: fiyat FVG'ye dönerse fill olur
- Fee: 0.04% taker (Binance default)
- Slippage: spread'in yarısı + 1 tick
- Position lifecycle: PENDING → OPEN → CLOSED (TP/SL/manual)

### 5. Position manager
- Açık pozisyonları her bar'da kontrol et
- SL/TP fiyat değdiyse kapat
- Trailing stop YOK (Faz 5'te eklenebilir)

### 6. Signal router filtreleri
- Cooldown: aynı sembolde son sinyal üzerinden 30dk geçmeli
- Max concurrent: aktif pozisyon sayısı 2'yi aşmamalı
- Dedup: aynı FVG'ye birden fazla sinyal üretilmemeli (fvg_id bazlı)

## Concurrency modeli

- **Tek event loop** (asyncio)
- WebSocket consumer'ları async task
- Bar close → strategy pipeline aynı loop'ta sync olarak (hızlı, blocking yok)
- Telegram sender ayrı task (queue based, rate-limit'e uyar)
- APScheduler günlük 23:00 raporu için

## Hata toleransı

| Hata | Davranış |
|---|---|
| WebSocket disconnect | Exponential backoff + REST ile gap fill |
| Binance API hatası | Retry 3x, sonra log + Telegram alert |
| Parquet write hatası | Buffer'da tut, sonraki cycle'da retry |
| Telegram down | Mesajları queue'da biriktir, recovery'de gönder |
| Pipeline exception | Trade'i atla, log + alert, bir sonraki bar'a devam |

## Test stratejisi

- **Unit:** ICT primitives için sentetik mum dizileri ile bilinen
  FVG/swing/sweep'leri assert et
- **Integration:** Mock exchange ile bar feed → signal_router → paper_broker
  uçtan uca akış
- **Backtest:** Tarihsel veride bilinen setup'lar üzerinde regression test
