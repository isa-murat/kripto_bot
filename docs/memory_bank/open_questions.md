# Open Questions

> Cevap bekleyen sorular. Cevaplananlar `decisions.md`'ye taşınır veya silinir.

---

## Aktif sorular

- [ ] **Q-012:** ICT terk edildi (Q-011 cevabı A). Hangi yeni paradigma?
  - Bağlam: F-14, 20 backtest iterasyonu hiçbir ICT setup-symbol-TF
    kombinasyonu ardışık iki pencerede +EV vermedi. Sweep+FVG ve OTE
    (5m+1h ve 15m+1h) terk edildi.
  - Adaylar:
    - **A) Statistical arbitrage** — pair trading (BTC/ETH spread, BNB/SOL), Bollinger Z-score mean reversion, cointegration based.
    - **B) Volume profile / VWAP-based** — VWAP deviasyon stratejileri, POC retest entry. Volume-driven, ICT'den bağımsız.
    - **C) ML-based** — feature engineering (rolling stats, microstructure) → classifier veya regressor. XGBoost / LightGBM. Walk-forward validation şart.
    - **D) Funding rate arbitrage** — Binance funding rate'in extreme'leri vs spot/futures premium. Cash-and-carry tarzı veya kontra-funding entry.
    - **E) Order flow / orderbook imbalance** — limit order book depth/imbalance, footprint chart benzeri features. Live data lazım (geçmiş tape replay zor).
  - Etkilediği yerler: tüm strateji ailesi, yeni feature pipeline, downloader (D için funding rate, E için book data).
  - **Plan:** Kullanıcı tartışması bekliyor. Aday başına minimum data
    seti ve feasibility değerlendirmesi gerek.

- [ ] **Q-009:** Sembol seçimi yeniden değerlendirilmeli mi?
  - Bağlam: F-07 → XRP low-vol mean-reverter, F-03 → BTC/BNB trend ETH/SOL choppy. Top-5 uniform tedavi etmek edge'i öldürdü.
  - Etkilediği yerler: `config/settings.yaml` symbols listesi
  - **Plan:** Yeni strateji seçildiğinde sembol filtresi de tasarlanır.

- [ ] **Q-009:** Sembol seçimi yeniden değerlendirilmeli mi?
  - Bağlam: F-07 → XRP low-vol mean-reverter, F-03 → BTC/BNB trend ETH/SOL choppy. Top-5 uniform tedavi etmek edge'i öldürdü. F-12'de paradigm reversal sürprizi gözlemlendi ama F-13'te o da OOS'ta çöktü.
  - Etkilediği yerler: `config/settings.yaml` symbols listesi
  - **Plan:** Q-011 cevabına bağlı. Eğer B (sembol tarama) seçilirse bu otomatik genişler.

- [ ] **Q-010:** TP stratejisi volatility-aware nasıl modellenmeli?
  - Bağlam: F-03 → TP-nearest BTC/BNB negatif, ETH/SOL pozitif. Uniform formül imkansız.
  - Adaylar: ATR-based TP multiplier, BB width gating, 1st/2nd/3rd nearest pool seçimi rejime göre
  - Etkilediği yerler: yeni strateji modülü + `sweep_fvg.py` (referans için kalsa da)

- [ ] **Q-007:** `sweep_fvg.evaluate` için pozitif sentetik integration test
  - Bağlam: Tüm 6 ICT koşulunu (HTF bias, killzone, SSL/BSL pool, sweep, MSS,
    bias-aligned FVG, opposite TP pool, RR ≥ 2) sentetik mum dizisiyle
    birlikte sağlamak ciddi bir fixture gerektirir. Faz 1'de 75 negative test
    geçti, Faz 2'de signal pipeline'ı yazıldı; pozitif yol Faz 4 backtest'inde
    gerçek BTCUSDT 5m + 1h tarihsel datasıyla doğrulanacak.
  - Etkilediği yerler: `tests/test_sweep_fvg.py`, Faz 4 backtest harness.
  - **Plan:** Faz 4'te ilk job: 6 aylık BTC datası üzerinde bilinen bir
    sweep+FVG setup'ını gözle bul, fixture'a indirip regression test yaz.



- [ ] **Q-002:** Swing/pivot lookback parametresi 5m için kaç olmalı?
  - Bağlam: ICT'de standart bir değer yok, mum dalgalı ise küçük (3-5),
    sakin ise büyük (5-10) lookback genelde tercih ediliyor.
  - Etkilediği yerler: `structure.py`, `strategy_params.yaml`
  - **Plan:** Faz 1'de varsayılan 3 (strategy_params.yaml'da set) ile yaz, Faz 4 backtest'te tune et.

- [ ] **Q-003:** Equal highs/lows için tolerance — sabit pip mi, ATR oranı mı?
  - Bağlam: BTC 95k, XRP 0.5 — sabit pip semboller için ayrı kalibrasyon ister,
    ATR oranı (örn. 0.1×ATR(14)) tüm semboller için uniform.
  - Etkilediği yerler: `liquidity.py`
  - **Eğilim (kararlaştırıldı 2026-05-11):** ATR oranı.
    Default `equal_level_tolerance_atr: 0.10` strategy_params.yaml'da. Faz 1 implementasyonu
    bunu kullanacak. Q kapanmaya yakın ama Faz 1 sonrası backtest sonucuna göre
    re-evaluate edilebilir.

- [ ] **Q-006:** Asia session high/low'ları liquidity pool'una otomatik dahil
  edilsin mi?
  - Bağlam: ICT'de Asia range'i (genelde 03:00-11:00 TR civarı) önemli sweep
    hedefi. Setup'a kalite katar ama implementasyon ek iş.
  - Etkilediği yerler: `liquidity.py`, `bias.py`
  - **Plan:** Faz 1'de basit swing-only pool ile başla. Faz 5'te Asia range ekle.

---

## Cevaplanmış (referans için)

- ✅ **Q-011 (2026-05-11):** ICT paradigma terk mi, sembol-tarama mı, farklı TF mi?
  - **Cevap: A — ICT tamamen terk.**
  - **Yöntem:** C (1h+15m TF pivot) ardışık opsiyon olarak test edildi
    (Run22, peşinen imzalanmış sözleşme). 4 IS kriterden 3'ü FAIL
    (pooled margin -3.4pp, 1 sembol +EV, WR BE'nin 6.4pp altında).
    Sözleşme tetiklendi, OOS atlandı (F-14).
  - 20 backtest iterasyonu (Sweep+FVG 5 sweep + OTE 5m+1h × 16 +
    OTE 15m+1h × 1) hiç ardışık-pencere +EV göstermedi. ICT'nin Forex
    varsayımları crypto için temelden uygun değil.
  - Yeni soru Q-012: hangi alternatif paradigma seçilmeli?

- ✅ **Q-008 (2026-05-11):** Yeni strateji ailesi hangisi olmalı?
  - **Seçim:** OTE (Klasik ICT, 2R sabit TP, 5 sembol birden).
  - **Sonuç:** Run20 IS pool -EV (-4.1pp, N=1375), tek pozitif XRP +7.0pp.
    Run21 OOS'da XRP -8.3pp → window-specific fluke (F-13).
    Run22 TF pivot (15m+1h) da FAIL (F-14). ICT projesi resmen terk
    edildi → Q-011 cevabı A.

- ✅ **Q-001 (2026-05-11):** Binance API key permission?
  - **Cevap:** Read-only. Paper trading için trade izni gereksiz; gerçek trading'e
    geçiş kararı alındığında permission revize edilir. `.env.example` ve docs
    bunu belirtiyor.

- ✅ **Q-004 (2026-05-11):** Backtest verisi nereden ve rate limit?
  - **Cevap:** Binance REST `/fapi/v1/klines`, 1500 mum/çağrı limit. 6 ay × 5m
    × 5 sembol ≈ 175 çağrı toplam, 2400 weight/dk içinde rahat.
    `src/data/downloader.py` zaten bunu uyguluyor (CHUNK_LIMIT=1500, sequential).

- ✅ **Q-005 (2026-05-11):** Telegram tek kanal mı multi-user mı?
  - **Cevap:** Tek kanal, `.env`'de `TELEGRAM_CHAT_ID`. MVP için yeterli.
    `notify/telegram.py` tek `chat_id` parametresi alıyor.
