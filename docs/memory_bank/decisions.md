# Decisions (ADR)

> Verilen tüm önemli kararların kayıt defteri. Eski kararlar silinmez, sadece
> superseded işaretlenir.

---

## ADR-0001 — Strateji ailesi: ICT (Inner Circle Trader)

**Tarih:** 2026-05-11
**Durum:** Accepted

**Bağlam:** Kripto sinyal botunda strateji yaklaşımı seçimi. Üç ana aday vardı:
klasik teknik analiz (RSI/EMA/MACD), order flow / piyasa mikroyapısı, ML tabanlı.

**Karar:** ICT (Inner Circle Trader) metodolojisi.

**Alternatifler:**
- Klasik TA: çok yaygın, edge'i tartışmalı
- Order flow: veri toplama zor, futures için L2 datası ek maliyet
- ML: önce kural tabanlı baseline kurmadan ML overengineering olur

**Sonuçlar:** ICT konseptlerini algoritmik kurallara çevirme yükü var (subjektif
kavramlar — Order Block, swing tespiti). Bunun karşılığında trader topluluğunda
yaygın, doğrulaması ve görselle karşılaştırılması kolay.

---

## ADR-0002 — MVP setup: Liquidity Sweep + FVG Entry

**Tarih:** 2026-05-11
**Durum:** Accepted

**Bağlam:** ICT'nin onlarca setup'ı var (Silver Bullet, OTE, Power of 3, Breaker
Block Retest vb.). MVP'de hepsini paralel çalıştırmak iterasyonu öldürür.

**Karar:** Tek setup ile başla — **Liquidity Sweep + FVG Entry**.

Akış: Sweep → MSS → oluşan FVG'ye retest → entry. SL sweep ötesi, TP opposite
liquidity, min RR=2.

**Alternatifler:**
- Silver Bullet (NY killzone): çok dar zaman penceresi, az sinyal, backtest datası az
- Multi-setup paralel: erken karmaşıklık, debug zor

**Sonuçlar:** Daha sonra `strategies/` altına yeni modüller eklenerek genişletilebilir.
Signal router'ın multi-strategy desteklemesi için tasarlanması lazım.

---

## ADR-0003 — Şimdilik saf Python, Rust ertelendi

**Tarih:** 2026-05-11
**Durum:** Accepted

**Bağlam:** Performans için Rust + PyO3 hibrit mimari düşünüldü. Live trading
yükü düşük (5 coin × 5m bar close), gerçek bottleneck backtest tarafında olur.

**Karar:** Faz 0-3'te saf Python + Polars (pandas yerine). Faz 4 backtest'inde
profile et, gerçek bottleneck çıkarsa hot path'i Rust'a taşı (PyO3 + maturin).

**Alternatifler:**
- Rust-first hibrit (rust_core/ crate, ICT primitives Rust'ta): mimarisi temiz
  ama upfront cost yüksek, ICT kuralları sık değişeceği için iterasyon yavaşlar
- Numba (@njit): %80 performans %10 iş, ama Polars zaten çoğu yerde yeterli
- Tamamen pandas: yavaş, ileride bottleneck garanti

**Sonuçlar:** Faz 4'te bir checkpoint var — backtest hızını ölçeceğiz. Eğer
parametre optimizasyonu makul sürede tamamlanmıyorsa Rust devreye girer.

---

## ADR-0004 — Coin evreni: Top 5 (BTC/ETH/SOL/BNB/XRP)

**Tarih:** 2026-05-11
**Durum:** Accepted

**Bağlam:** Geniş tarayıcı modu (top 50+) cazip ama düşük likidite coin'lerde
ICT (özellikle sweep tespiti) sık yanıltır.

**Karar:** BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT — Binance USDT-M perp.

**Alternatifler:**
- Sadece BTC + ETH: çok az sinyal
- Top 20-30 tarayıcı: rate limit, paralelizm, gürültü artar

**Sonuçlar:** Sembol listesi `config/settings.yaml`'da tutulur, kolay genişletilir.

---

## ADR-0005 — Mod: Sadece sinyal + paper trading

**Tarih:** 2026-05-11
**Durum:** Accepted

**Bağlam:** Üç seçenek vardı: notify-only, paper trading, gerçek emir.

**Karar:** **Paper trading.** Sinyal üret + sanal portföyde işlem aç + P&L takip.

**Alternatifler:**
- Notify-only: P&L görünmüyor, stratejiyi değerlendirmek zor
- Gerçek emir: API key güvenliği, kill-switch, slippage, regülasyon — çok erken

**Sonuçlar:** Paper broker'da gerçekçi fee + slippage uygulamak ZORUNLU, yoksa
"kâr ediyoruz" yanılsaması olur.

---

## ADR-0006 — Stack: Python 3.11+ + Polars + CCXT + python-telegram-bot

**Tarih:** 2026-05-11
**Durum:** Accepted

**Bağlam:** Hızlı iterasyon, geniş ekosistem, kullanıcı tanıdık olduğu için Python.
Pandas yerine Polars (Rust üstüne kurulu, lazy execution, hızlı).

**Karar:**
- Dil: Python 3.11+
- DataFrame: Polars
- Borsa: CCXT (REST) + Binance native WebSocket (live stream)
- Notify: python-telegram-bot
- Scheduler: APScheduler + asyncio
- Config: pydantic-settings + YAML
- Log: loguru
- Test: pytest

**Alternatifler:**
- Pandas: yavaş, ileride değiştirme maliyeti yüksek
- ccxt.pro (paid WebSocket): native ile aynı işi yapıyor, parasız
- Standard logging: boilerplate fazla

---

## ADR-0007 — Memory bank konumu: docs/memory_bank/ (repo içi)

**Tarih:** 2026-05-11
**Durum:** Accepted

**Bağlam:** Claude'un balık hafızası — gelecekteki oturumlar için kalıcı bağlam
gerekiyor. İki seçenek: (a) Claude'un global memory sistemi
(`~/.claude/projects/...`), (b) repo içi.

**Karar:** Repo içinde `docs/memory_bank/`. Ek olarak global memory de
kullanılır (kullanıcı tercihleri, çalışma stili gibi proje üstü bilgiler için).

**Alternatifler:**
- Sadece global memory: kullanıcı göremez, başka makinede çalışmaz, repo ile taşınmaz
- Sadece repo içi: kullanıcı tercihi gibi proje üstü bilgiler her repo'da
  tekrarlanır

**Sonuçlar:** AGENT.md repo içi memory bank'i workflow'un parçası yapar.
Her oturum başında progress.md + decisions.md + open_questions.md okunmalı.

---

## ADR-0010 — Containerization: Docker + docker-compose

**Tarih:** 2026-05-11
**Durum:** Accepted

**Bağlam:** Bot 7/24 çalışacak bir worker process. Lokalde Python 3.13 + uv + .venv ile çalışıyor ama:
- Sanal ortam state'i kaybolabilir, "global pip vs uv" tuzağı yaşandı
- İleride VPS'e taşıma hızlı olmalı
- ICT primitives + paper engine gelişimi sırasında stable runtime gerekli

**Karar:** Docker + docker-compose ile container'laştırma.
- Tek `Dockerfile` (Python 3.13 slim + uv tabanlı deps install)
- `docker-compose.yml` ile volume mount (data, logs, config), restart policy, signal handling
- `init: true` + `stop_grace_period: 30s` → temiz shutdown (Ctrl+C bug'ını da kompanse eder)
- Lokal'de `docker compose up -d`, ileride VPS'te aynı komut

**Alternatifler:**
- Sadece `.venv` ile devam: portability düşük, "benim makinemde çalışıyor" sendromu
- Multi-stage build: MVP için overkill, tek stage yeterli
- Poetry/pip-tools tabanlı kurulum: kullanıcı zaten uv kullanıyor, image'ta da uv tutarlı
- Kubernetes / Podman / nerdctl: aşırı, tek bot için Docker yeter

**Sonuçlar:**
- `pyproject.toml` değişince `docker compose up -d --build` gerek (cache sayesinde hızlı)
- `config/` read-only mount → host'tan düzenlenip `docker compose restart` ile yeni değer okunur
- `data/` volume → Parquet/SQLite container restart'ta korunur
- `docs/deployment.md` lokal + VPS akışını anlatıyor
- Faz 5 (production deploy) için VPS opsiyonu açık, ek refactor gerekmez
- **Hot reload:** `docker-compose.yml` içinde `develop.watch` bloğu var. `docker compose watch` ile src/config/scripts değişiklikleri otomatik sync+restart, pyproject/Dockerfile değişiklikleri rebuild. Production `up`'ta etkilenmez.

---

## ADR-0009 — Veri kaynağı: WebSocket yerine REST polling (Binance Futures)

**Tarih:** 2026-05-11
**Durum:** Accepted

**Bağlam:** Binance USDT-M Futures WebSocket endpoint'leri (`wss://fstream.binance.com/...`)
TR ev IP'sinden ve Romanya VPN'den veri akışı sağlamıyor. Tanı kanıtlı:
- TCP+TLS+WS handshake başarılı
- 0 mesaj geliyor (15-70sn dinlemede)
- Aynı sonuç hem `websockets` hem `aiohttp` kütüphanesinde
- Aynı sonuç hem combined hem single stream path'inde
- **Spot WS aynı IP'den çalışıyor** (mesaj akışı var)
- REST `/fapi/v1/klines` aynı IP'den çalışıyor (downloader 238 bar başarıyla indirdi)

Yorumlama: Binance Futures WS'sine seçici IP/region filter uygulanıyor; REST açık.
Kullanıcı VPS almak istemiyor, Bybit'e geçmek istemiyor, Binance Futures verilerini
istiyor.

**Karar:** Live veri kaynağı olarak **REST polling**. Her bar boundary'sinde + 5sn
gecikme ile son N barı `fetch_ohlcv` ile çek, yeni closed barları aynı `on_bar_close`
callback'iyle emit et. WS modülü repo'da kalır (ileride VPS deploy veya farklı VPN
ile aktif olursa kullanılabilir).

**Alternatifler:**
- Bybit: kullanıcı reddetti — Binance ekosisteminde kalmak istiyor
- Avrupa VPS (Hetzner): kullanıcı şimdi istemiyor; ileride Faz 5'te tekrar değerlendirilir
- Spot WS: short imkanı yok, ICT setup'ları sınırlanır
- VPN değiştirme: ücretsiz yol ama deterministik değil, kalıcı çözüm değil

**Sonuçlar / yan etkiler:**
- Latency ~5-10sn (5m bar için %1.6); MVP scalping için kabul edilebilir
- Rate limit: 5 sembol × 3 TF için worst case 15 fetch/dk (limit 2400 weight/dk içinde rahat)
- `config/settings.yaml`'a `data.source` flag'i eklendi (`rest_polling` | `websocket`),
  ileride dönüş kolay
- `src/data/rest_poller.py` yeni modül; mevcut WS modülü dokunulmadı
- Faz 5'te VPS opsiyonu yeniden açılırsa flag ile WS'ye geçilebilir

---

## ADR-0008 — Bias kaynağı: 1h trend → 5m entry

**Tarih:** 2026-05-11
**Durum:** Accepted

**Bağlam:** Multi-timeframe ICT akışında HTF bias seçimi.

**Karar:** 1h timeframe'de bias, 5m timeframe'de entry.

**Alternatifler:**
- 15m bias → 1m entry: çok hızlı, gürültü yüksek, scalping fee'sini yer
- 1h + 15m + 5m + 1m multi-confluence: implementasyon karmaşık, MVP'de gereksiz

**Sonuçlar:** Faz 5'te 4h üst-bias ek katman olarak eklenebilir.

---

## ADR-0011 — Sweep+FVG MVP setup'ı invalidate edildi (top-5, 6 ay, N=309)

**Tarih:** 2026-05-11
**Durum:** Accepted (ADR-0002 ile birlikte — ADR-0002 yeni strateji denemesinde gözden geçirilecek)

**Bağlam:** ADR-0002 ile MVP olarak seçilen "Liquidity Sweep + FVG Entry"
setup'ı, 5 sembol (BTC/ETH/SOL/BNB/XRP) × 6 ay (2025-11-01 → 2026-05-11)
penceresinde, doğru fee modeli (maker/taker ayrı) ve ranging filter
ile bile **N=309 trade'de pooled expectancy negatif**.

Erken sonuçlar (BTC-only N=8 pozitif; multi-symbol N=80'de bias filtre
"umutlu") küçük-N tuzağıydı — örnek büyüdükçe negatif tarafa sabitlendi.
Detaylı bulgular [findings.md](findings.md) — F-01..F-10.

**Karar:** Sweep+FVG setup'ını **mevcut parametre ailesiyle** terk et.
Bir sonraki strateji denemesi için:
1. Tamamen farklı bir setup ailesi dene (Silver Bullet, OTE, Breaker
   Block Retest, ya da non-ICT — örn. mean-reversion XRP için)
2. **Sembol-bazlı parametre setleri** ile başla (uniform setup yok)
3. Regime filter + maker/taker fee + N≥200 validation diskiplinini koru
4. Killzone gibi Forex-port filtreleri default-off

**Alternatifler:**
- Sweep+FVG'yi tuning ile kurtarmaya devam: 6+ ay deneme sonrası
  marginal improvement var ama break-even'e ulaşmadı; her filtre
  N'i düşürüp overfitting riski yaratıyor
- BTC+BNB-only çalıştır (pozitif görünenler): single-instrument
  cherry-pick, true edge kanıtı değil
- Mevcut setup'ı production'a koy: -EV bilinçli olarak alınmaz

**Sonuçlar:**
- `src/strategies/sweep_fvg.py` ve test'leri **silinmiyor** — referans
  + regression için kalıyor; signal router multi-strategy desteklediği
  için yeni setup yanına eklenir
- Faz 4 backtest harness, expectancy reporting, fee modeli, regime
  filter, multi-symbol runner = **reusable infrastructure** (bunlar
  ADR-0011 sayesinde yeniden yazılmıyor)
- Faz 5 (production deploy) ertelendi; önce yeni strateji ailesi seçimi
  + validate
- [findings.md](findings.md) **yeni strateji denemesinin önkoşul okuması**

**İlgili dosyalar:**
- [findings.md](findings.md) — F-01..F-10 ampirik bulgular
- [src/utils/rr_metrics.py](../../src/utils/rr_metrics.py) — maker/taker
- [src/filters/regime_filter.py](../../src/filters/regime_filter.py) — regime
- [src/analysis/trend_classifier.py](../../src/analysis/trend_classifier.py)
