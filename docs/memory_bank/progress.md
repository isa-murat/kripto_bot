# Progress

> Şu an nerede olduğumuzun canlı tablosu. Her oturumda güncellenir.

**Son güncelleme:** 2026-05-11
**Aktif faz:** **ICT projesi TERK edildi** (F-14, Run22 sözleşme FAIL) — Q-011 cevabı A: yeni paradigma seçimi (statistical/ML/order-flow)

> ⚠️ **Bir sonraki strateji denemesinden önce [findings.md](findings.md)'yi oku.**
> F-01..F-14 bulguları parametre/filtre seçimlerinde ay kazandırır.
> Son güncel: F-14 — Run22 (OTE 1h+15m) pre-signed contract FAIL.
> 20 backtest iterasyonu sonucu hiçbir ICT setup-symbol-TF kombinasyonu
> ardışık iki pencerede +EV göstermedi. ICT terk edildi.

---

## Genel yol haritası

| Faz | Durum | Tahmini |
|---|---|---|
| **Faz 0** — Altyapı (iskelet, config, CCXT, REST poller, cache, Telegram) | ✅ Tamam | 2 gün |
| **Faz 0.5** — Containerization (Docker + Compose Watch) | ✅ Tamam | ~1 saat |
| **Faz 1** — ICT primitives (structure, poi, liquidity, bias, killzone) | ✅ Tamam (75 test) | 1 oturum |
| **Faz 2** — Sweep+FVG strateji + signal router + main.py entegrasyon | ✅ Tamam | 1 oturum |
| **Faz 3** — Paper engine + position manager + günlük rapor | ✅ Tamam | 1 oturum |
| **Faz 4** — Backtest + metrikler (Rust checkpoint) | ✅ Tamam (N=309 final, Sweep+FVG -EV doğrulandı, ADR-0011) | — |
| **Faz 4.5** — OTE setup'ı dene (5m+1h) | ✅ Tamam (Run20 IS pool -EV, Run21 XRP OOS -EV, F-12/F-13) | — |
| **Faz 4.6** — OTE TF pivot (15m+1h, Run22) | ✅ Tamam — sözleşme FAIL (F-14), ICT terk | — |
| **Faz 4.7** — Q-011 cevabı: yeni paradigma seçimi (statistical/ML/order-flow) | 🟡 Aktif — kullanıcı tartışması bekliyor | — |
| **Faz 5** — İyileştirme (filtreler, tuning, monitoring) | ⏳ Yeni paradigma +EV olduktan sonra | sürekli |

---

## Faz 0 — Altyapı

### Done ✅

- [x] Repo klasör yapısı kararı (architecture.md)
- [x] AGENT.md
- [x] README.md
- [x] PRD (docs/PRD.md)
- [x] Architecture (docs/architecture.md)
- [x] Memory bank iskeleti (docs/memory_bank/)
- [x] ADR'ler (0001-0008)
- [x] pyproject.toml — bağımlılıklar tanımlı
- [x] .gitignore + .env.example
- [x] config/settings.yaml + config/strategy_params.yaml
- [x] src/ Python paket iskeleti (boş __init__.py'lar + placeholder modüller)
- [x] src/config.py — pydantic-settings + YAML loader
- [x] src/utils/logging.py — loguru setup
- [x] src/utils/time.py — UTC↔TR + killzone + timeframe helpers (unit testli)
- [x] src/data/exchange.py — CCXT wrapper (sync + async), symbol normalize, retry
- [x] src/data/ohlcv_cache.py — Polars hot buffer + Parquet cold storage (unit testli)
- [x] src/data/downloader.py — REST tarihsel OHLCV → Parquet (typer CLI: `download` + `all`)
- [x] src/data/ws_stream.py — Binance combined kline WS, bar-close handler, reconnect + gap-fill
- [x] src/notify/telegram.py — async queue, rate limit, getMe sanity, graceful stop
- [x] src/main.py — `run` (canlı paper) + `check` (config sanity) komutları
- [x] tests/conftest.py + test_config.py + test_time_utils.py + test_ohlcv_cache.py

### In progress / kullanıcı eylemi bekliyor 🟡

- [x] **Bağımlılık kurulumu** (uv ile): `uv pip install -e ".[dev]"`
- [x] **`.env` dosyası:** Binance read-only key + Telegram token/chat_id dolduruldu
- [x] **Smoke test:** `python -m src.main check` → Telegram + Binance "True"
- [x] **Pytest:** 19/19 PASSED
- [x] **Tarihsel veri (küçük test):** BTCUSDT 1h 238 bar OK (Türkiye IP problemi yok)
- [x] **Telegram smoke test:** `scripts/test_telegram.py` çalıştı, mesaj kullanıcının telefonuna ulaştı
- [x] **Canlı WS testi (başarısız → tanı):** Binance Futures WS bu IP'den (TR ev + Romanya VPN) data akışı yok.
      Spot WS çalışıyor, REST çalışıyor → ADR-0009: REST polling fallback'e geçildi.
- [x] **REST poller modülü:** `src/data/rest_poller.py` yazıldı, `data.source: rest_polling` flag'iyle main.py'da seçiliyor
- [x] **Canlı REST polling testi:** çalıştı ✅ — ilk dakikada 5 sembol × 1m × 2 bar = 10 log
- [ ] **Tarihsel veri (tam):** `python -m src.data.downloader all --from 2025-11-01` (Faz 4'te de yapılabilir, opsiyonel)

### Faz 0 kabul kriterleri

1. ✅ `python -m src.main run` çalışınca:
   - 5 sembol için WebSocket bağlantısı kurulur (kline_1m, 5m, 1h)
   - Her bar kapanışında log atılır
   - Telegram'a başlangıç mesajı gönderilir
2. ✅ `python -m src.data.downloader download --symbol BTCUSDT --tf 5m --from 2026-01-01`
   ile tarihsel veri Parquet'e iner
3. ✅ `pytest` hatasız geçer

(Kabul **kod düzeyinde** karşılandı; runtime doğrulama kullanıcı testinde yapılacak.)

---

## Faz 0.5 — Containerization (aktif)

### Done ✅

- [x] `docker/Dockerfile` (Python 3.13 slim + uv, single stage, healthcheck)
- [x] `docker/Dockerfile.dockerignore` (BuildKit syntax — Dockerfile yanında)
- [x] `docker-compose.yml` repo kökünde (kullanıcı isteği — UX için kökte kalsın)
- [x] `Dockerfile` ve `Dockerfile.dockerignore` ise `docker/` altında
- [x] `docs/deployment.md` (lokal + VPS akışı + hot reload bölümü)
- [x] ADR-0010
- [x] **Hot reload (Compose Watch):** `develop.watch` bloğu eklendi. `docker compose watch` ile src/config/scripts değişiklikleri auto sync+restart; pyproject/Dockerfile rebuild.

### Kullanıcı doğrulaması ✅

- [x] Docker Desktop kuruldu/çalıştı
- [x] `docker compose up -d --build` → image build OK
- [x] `docker compose watch` → hot reload akışı doğrulandı, kullanıcı OK dedi

---

## Faz 2 — Sweep+FVG strateji + signal router (aktif)

### Done ✅

- [x] `src/strategies/sweep_fvg.py` — `evaluate(symbol, df_ltf, df_htf, ltf_bar_index, htf_bar_index, params)`
  - 6 adımlı pipeline: HTF bias → killzone → SSL/BSL pool sweep → MSS → FVG → entry/SL/TP
  - `TradeSignal` dataclass (symbol, side, entry, sl, tp, rr, setup_name, ts, meta dict)
  - `SetupParams` dataclass + `from_strategy_params(sp)` factory (yaml'dan yükleme)
  - Pure function, no I/O — orchestration: bias, killzone, structure, liquidity, poi modüllerini birleştirir
- [x] `tests/test_sweep_fvg.py` — 7 test (negatif path + parametre yapısı)
  - empty df → None, htf empty → None
  - bias NEUTRAL → None
  - killzone dışı (require_killzone=True) → None
  - require_killzone=False bypass çalışıyor
  - SetupParams default'ları + from_strategy_params(yaml) round-trip + boş StrategyParams default fallback

### Done ✅ (devamı)

- [x] `src/engine/signal_router.py` — `SignalRouter` (cooldown + dedup + max_concurrent filter, async handler list, hata izolasyonu)
- [x] `src/notify/telegram.py` — `format_signal_message(signal)` + `notifier.signal_handler` SignalRouter ile uyumlu async handler
- [x] `tests/test_signal_router.py` — 11 test (dispatch, dedup, cooldown enter/exit/per-symbol, max_concurrent enter/exit, multi-handler order, exception isolation, no-handlers OK)
- [x] `tests/test_telegram_format.py` — 4 test (long/short markers, key fields, meta fallback)

- [x] `src/main.py` — bar callback içinde her LTF (5m) bar kapanışında `evaluate_sweep_fvg()` çağrılıyor
  - `MIN_BARS_FOR_STRATEGY=60` ile cache warmup süresince signal üretimi atlanıyor
  - `SignalRouter` ayağa kaldırıldı, `notifier.signal_handler` register edildi
  - Strategy / router exception'ları yakalanıp loglanıyor (akış kesintiye uğramaz)

### Faz 3'e ertelenenler

- [ ] **Pozitif integration test** — Q-007, gerçek tarihsel data ile Faz 4'te
- [ ] Yan iş: `ws_stream.py` Ctrl+C bug fix (Docker `init=true` zaten kompanse ediyor)

---

## Faz 1 — ICT primitives ✅ tamam

### Plan (sırayla)

1. **`structure.py`** ✅ yazıldı + 12 test passed
   - `Swing`, `StructureEvent` dataclass; `SwingType`, `Trend`, `EventType` enum
   - `find_swings(df, lookback)` — strict-> fractal, look-ahead guard
   - `compute_atr(df, period)` — Polars rolling SMA of TR
   - `detect_events(df, swings, atr, displacement_atr_mult)` — trend tracker
   - `current_trend(events)`

2. **`liquidity.py`** ✅ yazıldı — equal H/L pool'ları + sweep tespiti
   - `LiquidityPool` (BSL/SSL, price, swings, confirmed_at_index, is_equal property)
   - `Sweep` (pool, wick_size, body_size, expected_reaction property)
   - `find_pools(swings, tolerance_price, min_count)` — greedy clustering by price
   - `active_pools_at(pools, bar_index)` — live-safety filter
   - `detect_sweep(df, bar_index, pools, min_wick_pct)` — wick > pool, body inside, largest-wick wins
   - **Test (`tests/test_liquidity.py`):** pool grouping (equal highs/lows, distant separation, min_count, chronological), active filter, sweep detection (BSL bullish, SSL bearish, close-beyond rejection, small wick rejection, unconfirmed pool ignore, largest-wick selection, zero-range doji)

4. **`bias.py`** ✅ yazıldı — HTF trend + premium/discount → final Bias
   - `BiasState` dataclass (bias, trend, zone, range_high/low/mid, current_price, bar_index, ts)
   - `PriceZone` enum (PREMIUM / DISCOUNT / EQUILIBRIUM)
   - `compute_bias(df_htf, bar_index, swing_lookback, range_lookback_swings, premium/discount thresholds)` — look-ahead safe
   - `_classify_zone(price, range_low, range_high, premium_thr, discount_thr)`
   - `_final_bias(trend, zone)` — kararı 8-satırlık matrix ile
   - **Test (`tests/test_bias.py`):** zone classification (defaults + custom thresholds + zero span), final bias matrix (8 satır), compute_bias (no swings, BULL+discount, BULL+premium=neutral, no look-ahead, metadata)

3. **`poi.py`** ✅ yazıldı — FVG + Order Block
   - `FVG` (direction, middle_index, top, bottom, midpoint, size, confirmed_at_index)
   - `OrderBlock` (direction, index, OHLC, midpoint, confirmed_at_index)
   - `find_fvgs(df, atr, min_atr_mult)` — 3-bar imbalance, ATR-based size filter
   - `active_fvgs_at(fvgs, bar_index, max_age_bars)` — fresh + observable filter
   - `is_fvg_mitigated(df, fvg, until_index)` — price entered the gap
   - `find_order_blocks(df, atr, displacement_atr_mult, lookback)` — opposite-color candle preceding displacement, dedup'lı
   - **Test (`tests/test_poi.py`):** bullish/bearish FVG, overlap rejection, ATR-min filter, active filter, mitigation (in/out), bull/bear OB, lookback limit, unique-per-(index,direction)

2. **`poi.py`** — FVG + Order Block
   - `find_fvgs(df, min_atr_mult)` → bullish/bearish FVG listesi
   - `find_order_blocks(df, swings)` → displacement öncesi son opposite mum
   - Test: bilinen 3-mum patternlerinde FVG'yi tespit

3. **`liquidity.py`** — equal H/L + sweep
   - `find_pools(df, swings, tolerance_atr)` → liquidity zone listesi
   - `detect_sweep(bar, pools, min_wick_pct)` → sweep mumu mu?
   - Test: bilinen sweep mum'larında doğru pool'u bulma

4. **`bias.py`** — 1h trend + premium/discount → Bias
   - `compute_bias(df_htf, df_ltf)` → BULL / BEAR / NEUTRAL
   - Test: bilinen trend datalarında doğru bias

5. **`killzone.py`** — zaten var, Faz 1'de helper genişletilebilir (örn. `next_killzone_start`)

### Bonus (Faz 1 yapacağım küçük fix)

- `ws_stream.py` Ctrl+C bug fix: `await asyncio.wait_for(stop_event.wait(), timeout=...)`
  ile WS recv'i race ettirip temiz kapanış sağla. (REST polling'de zaten temiz.)

### Notebook gözlemi (her modül sonrası)

`notebooks/<modul>_visual.ipynb` — Parquet'ten BTCUSDT 5m datası çek, modülün
çıktısını matplotlib ile çiz, "evet doğru görüyor" doğrulama. (matplotlib +
jupyter `[backtest]` extra'sında zaten tanımlı.)

---

## Notlar / log

- **2026-05-11 (oturum N+7 — Run22 OTE TF pivot, sözleşme FAIL, ICT terk):**
  - **Branch:** `feature/ote-htf-1h-15m` (4h+5m → 1h+15m TF pivot).
    Önceden `feature/ote-setup` v0.2-ote-mixed-results olarak tag'lendi.
  - **Pre-signed acceptance contract** ([run22_contract.md](run22_contract.md)):
    Run22 öncesi 4 IS kriteri + 3 OOS kriteri + yasak liste imzalandı.
    Kullanıcı 4. yasak madde ekledi: "yakın fail" rasyonalizasyonu yasak.
  - **Implementasyon:** settings.yaml entry 5m→15m, regime_filter htf 4h→1h
    (classification_lookback 20→30); strategy_params.yaml min_leg_atr_mult
    1.5→2.5; runner.py default entry_tf 15m. Native 15m data Binance'ten
    indi (5 sembol × 12 ay, 36k bar/sembol). Test suite 235/235 PASSED.
  - **Run22 IS (2025-11..2026-05, OTE 1h+15m, 5 sembol, N=541):**
    | Sembol | N | WR | Margin | PnL ($) |
    |---|---|---|---|---|
    | BTC | 113 | 31.9% | -4.9pp | -1734 |
    | ETH | 105 | 37.1% | +1.6pp | +327 |
    | SOL | 102 | 39.2% | **+3.8pp** | +1075 |
    | BNB | 111 | 25.2% | -11.4pp | -3339 |
    | XRP | 110 | 30.9% | -5.0pp | -1695 |
    | **POOL** | **541** | **32.7%** | **-3.4pp** | **-5366** |
  - **Sözleşme verdict:** 4 kriterden 3'ü kesin altta:
    - Pooled margin -3.4pp (hedef +3pp) — 6.4pp altta
    - 1 sembol margin>+2pp (hedef ≥3) — sadece SOL
    - Pooled WR 32.7% (hedef >BE+3 = 39.1%) — 6.4pp altta
    - Sample N=541 (hedef >200) ✅ tek pass kriter
  - **FAIL → ICT projesi resmen terk edildi.** Run23 OOS atlandı (sözleşme
    şartı). 20 backtest iterasyonu boyunca hiç bir ICT setup-symbol-TF
    kombinasyonu ardışık iki pencerede +EV göstermedi.
  - **XRP TF-flip:** Run20 5m'de tek +EV (+7.0pp) idi, 15m'de -5.0pp.
    Sembol karakteri TF'e göre değişiyor — F-13'ün "window fluke" tezi
    şimdi TF-fluke olarak da doğrulandı.
  - **SOL kurtuldu (-7.9 → +3.8pp), ETH zayıf-yeşil (-3.6 → +1.6pp);
    BTC/BNB iki TF'de aynı kötü** (-4.8/-4.9pp, -11.2/-11.4pp). TF pivot
    semboller arası karakteri yeniden dağıttı ama uniform edge üretmedi.
  - **findings.md F-14 + Özet madde 10-11 eklendi. Q-011 cevabı A
    (ICT terk).** open_questions.md güncelleniyor; sıradaki tartışma:
    yeni paradigma seçimi (statistical arbitrage, ML-based, order-flow,
    funding-rate arbitrage adaylarından hangisi).

- **2026-05-11 (oturum N+6 — OTE setup + Run20 IS + Run21 XRP OOS):**
  - **Yeni modüller:** `src/strategies/ote.py` (klasik ICT OTE: HTF bias → 5m MSS → impulse leg → fib 0.618-0.786 zone first-touch → 2R fixed TP).
    `setup_ote` config bölümü, `STRATEGY_REGISTRY` dispatcher (`src/backtest/runner.py` + `scripts/multi_symbol_backtest.py` `--strategy` flag'i).
  - **Testler:** `tests/test_ote.py` 12 yeni test (sentetik bull-HTF + LTF retrace positive integration test dahil). Suite **247/247 PASSED** (eski 235 + 12 yeni).
  - **Run20 (OTE IS, 2025-11..2026-05, 5 sembol, N=1375):**
    | Sembol | N | WR | Margin | Return |
    |---|---|---|---|---|
    | BTC | 276 | 34.1% | -4.8pp | -37.1% |
    | ETH | 252 | 34.1% | -3.6pp | -28.3% |
    | SOL | 275 | 29.5% | -7.9pp | -50.9% |
    | BNB | 292 | 27.7% | -11.2pp | -65.5% |
    | **XRP** | 280 | **44.6%** | **+7.0pp** | **+68.9%** |
    | **POOL** | **1375** | **34.0%** | **-4.1pp** | — |
  - **Paradigma sürprizi (F-12):** Trend-following OTE en iyiyi mean-reverter karakter XRP'de verdi. F-07'nin "sembol karakteri" tezi ters yöne çalıştı.
  - **Run21 (XRP OOS, 2025-05..2025-10, F-11 disiplini):**
    | Metrik | IS (Run20) | OOS (Run21) |
    |---|---|---|
    | N | 280 | 276 |
    | WR | 44.6% | 29.3% |
    | Margin | +7.0pp | **-8.3pp** |
    | Return | +68.9% | -53.1% |
    | Sharpe | 2.03 | -3.39 |
    | Max DD | 17.5% | 54.0% |
  - **15pp margin swing** (ETH'in 11pp swing'inden büyük) → **F-13**: XRP-OTE de window-specific fluke. F-11 üçüncü kez tetiklendi.
  - **Karar:** ICT paradigm fundamental gözden geçirme gerekli (Q-011). Sweep+FVG ve OTE'nin ikisi de pool -EV, tek pozitif sembol-pencere kombinasyonları OOS'ta çökü̧yor. 19 backtest iterasyonu, ICT bilançosu sıfır.
  - **findings.md F-12 + F-13 + Özet madde 8-10 eklendi. Q-008 cevaplandı, Q-011 açıldı.**

- **2026-05-11 (oturum N+5 — Run15 ETH OOS, B opsiyonu kesinleşti):**
  - Hipotez: Run13'te ETH-only +EV (N=67, margin +2.85pp, +$11.18/trade) → ETH niche bot yapılabilir mi?
  - Test: Run15 = aynı config (`--no-killzone`, regime filter aktif, maker/taker fee), pencere 2025-05-01 → 2025-10-31 (Run13'ün 6 ay öncesi, OOS).
  - **Sonuç: -EV decisively.** N=86, WR 25.58%, margin **-8.10pp**, -$28.62/trade, -24.61% return, Max DD 32%, profit factor 0.69.
  - İki bağımsız 6-ay penceresi arasında margin swing'i +2.85 → -8.10 ≈ **11pp**. Run13'ün +EV'si sample variance / regime luck.
  - **Karar: Opsiyon B.** ETH niche bot yok. Yeni strateji ailesi pivotu (Q-008) kesinleşti.
  - findings.md'ye **F-11 eklendi** (Run13 +EV window-specific, OOS'ta çöktü). Özete madde 8 (single-window +EV ≠ edge) eklendi.
  - Docker container içinde koşuldu (`docker compose exec -e PYTHONIOENCODING=utf-8 ...`) — host'taki Windows console cp1254 → typer.echo Unicode bug'ı by-pass'lı.

- **2026-05-11 (oturum N+4 — N=309 final, Sweep+FVG invalidate):**
  - Penceler genişletildi: 2025-11-01 → 2026-05-11, tam 5 sembol, doğru fee modeli + regime filter aktif → **N=309 pooled expectancy negatif**.
  - Önceki N=80'de güçlü görünen HTF bias / counter-trend hipotezi N=309'da invalidate oldu → küçük-N tuzağı kanıtı.
  - Sembol-bazlı çarpıklık devam: BTC/BNB için TP-nearest negatif, ETH/SOL için pozitif → uniform TP imkansız.
  - Fee dağılımı: gross RR'in ~%30'u fee'ye gidiyor. Maker (entry+TP) / taker (SL+EXPIRED) ayrımı zorunlu çıktı, tek-rate model yanıltıcıydı.
  - Ranging filter kayıbın **~%96.5'ini** sildi (run9: -$2480 → run10: -$86) — yeni strateji denemesinde de **mandatory** kalacak.
  - Kill zone bu sembol setinde anlamlı edge üretmedi → ICT'nin Forex'ten port edilmiş filtreleri default-off.
  - Weekend effect N=9 ile teyit edilmedi → tek başına filter justification'ı yapma.
  - **Karar (ADR-0011):** Sweep+FVG terk. Yeni strateji ailesi seçimi sırada. Reusable: backtest harness, expectancy reporting, fee modeli, regime filter, multi-symbol runner.
  - **Detaylı ampirik bulgular:** [findings.md](findings.md) F-01..F-10.

- **2026-05-11 (oturum N+3 — regime filter, run10):**
  - `src/analysis/trend_classifier.py` (HH/LL swing + EMA20 slope, ikisi anlaşırsa o, anlaşmazsa 'ranging'; `aggregate_to_4h` partial-tail drop ile look-ahead safe).
  - `src/filters/regime_filter.py` + `FiltersConfig.regime_filter` (settings.yaml `enabled/exclude_ranging/exclude_trending/htf_timeframe/lookback`).
  - `SignalRouter.regime_check` callable (rejection `regime_<label>`; fail-open). Backtest runner symbol'ün 1h df'ini bir kez 4h'a aggregate edip closure veriyor.
  - `multi_symbol_backtest.py` `--output-prefix` ile per-symbol JSON ve `multi_<prefix>.json` yazıyor.
  - Test: 36 yeni (trend_classifier, regime_filter, signal_router regime_check). Suite **208/208 PASSED**.
  - **Run10 vs Run9 (aynı pencere, regime filter farkı):**
    | Metrik | Run9 | Run10 |
    |---|---|---|
    | N | 80 | 36 (-55%) |
    | Pooled WR | 25.0% | 30.6% |
    | Pooled PnL | -$2480 | **-$86** |
    | Margin | -6.4 pp | **-1.2 pp** |
    | Exp/T | -$31 | -$2.40 |
  - **Sembol bazlı:** BTC 2 trade %100 +$617, BNB 6 trade %50 +$787, SOL 13 trade %23 -$561 (büyük iyileşme), ETH 7 trade %14 -$560 (filtre işe yaramadı — ETH'in sorunu ranging değil), XRP 8 trade %25 -$369 (ranging filter XRP'nin pozitif trade'lerini kesti).
  - **Bulgu:** Ranging hipotezi doğrulandı (kayıbın %96'sı silindi) ama XRP **low-vol mean-reversion** olduğu için filtre onu yanlış kesti. ETH'in problemi ayrı (ranging dışı da kötü).
  - Sıradaki: Adım A = ATR-based low-vol ranging exception. XRP'yi kurtarıp pozitife geçmeyi hedefliyoruz.

- **2026-05-11 (oturum N+2 — multi-symbol, kritik bulgu):**
  - 5 sembol × 6 ay backtest (BTC, ETH, SOL, BNB, XRP), aynı parametrelerle, her sembol bağımsız $10k başlangıç. N=80 toplam trade.
  - **Pooled expectancy NEGATİF:** WR %25, net RR 2.19, break-even WR %31.4 → margin **-6.4 pp**, -$31/trade, -0.34R.
  - **Sembol bazlı çarpıklık:**
    | Sembol | N | WR | Margin | Exp/T |
    |---|---|---|---|---|
    | BTCUSDT | 8 | 37.5% | +3.1pp | +$25 ✅ |
    | BNBUSDT | 14 | 35.7% | +4.9pp | +$25 ✅ |
    | XRPUSDT | 19 | 31.6% | -1.0pp | -$15 ~ |
    | ETHUSDT | 18 | **16.7%** | -14.5pp | -$67 ❌ |
    | SOLUSDT | 21 | **14.3%** | -15.6pp | -$73 ❌ |
  - **Asıl bulgu:** ETH ve SOL'da WR %15 civarı çöküyor — sweep+FVG bu sembollerde çalışmıyor. BTC-only optimistic backtest cherry-picking'di. Diversifikasyon sistemi öldürdü.
  - Sample artık `sample_too_small=False` (N=80 ≥ 30) → istatistiksel olarak **güvenilir negatif sonuç**. Önceki BTC N=8 sonuçları rastgelelikte kaybolmuş.
  - `multi_symbol_backtest.py` güncellendi: per-symbol + pooled expectancy, JSON output. `runner.trade_to_dict` public yapıldı.
  - `update_config_expectancy.py` artık multi-run JSON shape'ini (`pooled.expectancy`) de okuyor.
  - Config strategy_params.yaml auto-update'i multi_run1.json'dan yapıldı: -$31/trade -0.34R.
  - **Sıradaki açık sorular:** (1) sembol seçimi (sadece BTC+BNB)? (2) sembol-bazlı parametre tuning? (3) farklı setup (Silver Bullet, OTE)? (4) ETH/SOL'un neden farklı davrandığını araştır — volatilite, liquidity profile farkı.

- **2026-05-11 (oturum N+1 — maker/taker fee modeli + TTL 24):**
  - Fee modeli düzeltildi: entry (FVG mid limit) + TP exit (resting limit) → **maker**; SL exit (stop-market) + EXPIRED mark-to-end → **taker**. `PaperBroker.try_fill_pending` ve `close_position` `reason`'a göre uygun fee uyguluyor.
  - `Position.fee_paid` → `entry_fee` rename. `_position_from_dict` eski snapshot anahtarlarını da kabul ediyor (backwards-compat read).
  - `rr_metrics.calculate_net_rr` ve `calculate_expectancy`: `fee_rate` tek parametre yerine `maker_rate` + `taker_rate`. Dict shape: `entry_fee`, `tp_fee`, `sl_fee`, `total_fees_at_tp`.
  - `pending_ttl_bars` default 12 → 24 (60 dk → 120 dk). run8'de %61 expire oranı; ICT setup'ları çoğunlukla 1-2 saatte retest aldığı için TTL gevşetildi. `cancel_invalidated_pending` zaten yanlış kalanları süpürüyor.
  - Test'ler: 7 yeni assert (maker/taker beklentileri, EXPIRED on OPEN → taker, SL fee taker, TP fee maker) + 1 yeni (`test_exit_fee_uses_taker_rate_on_expired`). Suite: **191/191 PASSED**.
  - **btc_run9 vs btc_run8 (aynı pencere, fee modeli + TTL farkı):**
    | Metrik | run8 (eski) | run9 (yeni) |
    |---|---|---|
    | Total PnL | +$46.46 | **+$202.01** |
    | Profit factor | 1.06 | 1.27 |
    | Net RR | 1.56 | 1.91 |
    | Break-even WR | 39.1% | 34.4% |
    | Margin | -1.6 pp ❌ | **+3.1 pp ✅** |
    | Expectancy | $+5.81 / +0.06R | **$+25.25 / +0.26R** |
    | Max DD | 3.37% | 3.01% |
  - Kullanıcı beklentisi (net RR ~2.2, margin +6pp) tam tutmadı — fee modeli düzeldi ama SL fee'leri hâlâ taker (doğru tasarım). Margin'i +6'ya taşımak için **min_rr=2.5 → 3.0** ya da loss trades'in size'ını sıkıştırmak gerek.
  - Sample hâlâ N=8 → `sample_too_small=True`. Multi-symbol backtest sırada.

- **2026-05-11 (oturum N — expectancy reporting):**
  - `src/utils/rr_metrics.py` eklendi: `calculate_net_rr` (entry/sl/tp/size/fee_rate → gross + fee-adjusted net RR), `calculate_expectancy` (closed trade listesi → WR, avg_win/loss, break-even WR, margin_pct, expectancy_per_trade, expectancy_r, sample_too_small/thin_margin/negative_expectancy bayrakları).
  - `src/backtest/runner.py`: `BACKTEST RESULTS` sonrası `EXPECTANCY ANALYSIS` bloğu basıyor; output JSON şemasına `expectancy` alanı eklendi.
  - `scripts/update_config_expectancy.py`: backtest JSON'undan (veya `data/backtest/` içindeki en yeni) expectancy okur, `config/strategy_params.yaml` içindeki `min_rr:` üstündeki yorum bloğunu line-based regex ile değiştirir (idempotent). ruamel.yaml denendi ama `yaml_set_comment_before_after_key` append davranışı idempotency'yi bozduğu için terk edildi.
  - 13 yeni test (test_rr_metrics.py) + 6 yeni test (test_update_config_expectancy.py) → toplam **184/184 PASSED**.
  - btc_run7 expectancy: N=6, WR %50.0, break-even %37.5, margin +12.5pp, $62.51/trade, +0.61R. Sample küçük (N<30) → istatistik anlamlı değil, **çoklu symbol + uzun pencere backtest hâlâ gerekli**.
  - **Önemli not:** Kullanıcının bir önceki talimat draft'ı (TTL=3 → 12, price-invalidation ekle) ile çakıştı. Doğrulama: `pending_ttl_bars` config'de **zaten 12**; `cancel_invalidated_pending` (SL pre-fill iptali) **zaten mevcut** [paper_broker.py:209](src/engine/paper_broker.py#L209). O draft uygulanmadı, kullanıcı yön değiştirdi.

- **2026-05-11 (oturum 1):** Proje başlatıldı. Mimari + strateji + stack tartışması.
  ADR-0001..0008 yazıldı. Dokümantasyon altyapısı + iskelet + config + utils tamam.
- **2026-05-11 (oturum 2):** Faz 0 kod tarafı bitirildi:
  exchange.py (CCXT async + symbol convert + retry), ohlcv_cache.py (Polars + Parquet,
  unit testli), downloader.py (typer CLI, all-symbols batch), ws_stream.py (combined
  stream, reconnect, gap-fill), notify/telegram.py (queue + rate limit), main.py
  (run + check).
- **2026-05-11 (oturum 4):** Faz 0.5 — Containerization tamamlandı:
  - `docker-compose.yml` repo kökünde, `docker/Dockerfile` ve `docker/Dockerfile.dockerignore` ayrı klasörde
  - Docker Compose Watch ile hot reload (`docker compose watch` tek komut, src/config/scripts → sync+restart, pyproject/Dockerfile → rebuild)
  - Kullanıcı doğruladı, çalışıyor.
  - Sıradaki: Faz 1 — ICT primitives (`structure.py` ile başla).

- **2026-05-11 (oturum 3):** Manuel test:
  - Kullanıcı **uv** kullanıyor; `pip install` global Python'a kurmuştu, `uv pip install`
    veya `uv sync` kullanılması gerekiyor (AGENT.md, README.md güncellendi).
  - `python -m src.main check` → Telegram + Binance True
  - `pytest` → 19/19 PASSED
  - `downloader BTCUSDT 1h --from 2026-05-01` → 238 bar Parquet'e indi (TR IP OK)
  - `scripts/test_telegram.py` → Telegram'a mesaj ulaştı (`@kripto_signals_bot`)
  - **WS canlı testi başarısız:** 18 dk hiç bar gelmedi.
  - Tanı script'leri (`ws_diag`, `ws_multi_diag`) sonucu: Binance Futures WS bu IP'den
    (TR ev + Romanya VPN) data akışı yok; Spot WS çalışıyor; REST çalışıyor.
    Yorum: Binance Futures WS'ye seçici IP/region filter.
  - **Karar (ADR-0009):** WS yerine REST polling. Kullanıcı VPS/Bybit istemiyor.
  - **Implementasyon:** `src/data/rest_poller.py` yazıldı, `config/settings.yaml`'a
    `data.source` flag'i eklendi, `main.py` flag'e göre WS veya REST seçiyor.
  - Kalan: REST polling canlı test + (ardından Docker mı Faz 1 mi karar).
