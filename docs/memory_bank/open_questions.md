# Open Questions

> Cevap bekleyen sorular. Cevaplananlar `decisions.md`'ye taşınır veya silinir.

---

## Aktif sorular

- [ ] **Q-008:** Yeni strateji ailesi hangisi olmalı?
  - Bağlam: ADR-0011 ile Sweep+FVG terk edildi. Adaylar:
    - **Silver Bullet** (NY killzone tabanlı) — ama F-06: killzone bu setup'ta edge vermedi; crypto'da Forex saatleri çalışmıyor
    - **OTE** (Optimal Trade Entry, fib 61.8-78.6) — momentum entry, Sweep'ten farklı tetikleyici
    - **Breaker Block Retest** — MSS sonrası ters OB retest; sweep gerektirmez
    - **Non-ICT mean-reversion** — özellikle XRP için (F-07)
    - **Setup-symbol pair'leri:** her sembol için farklı strateji ailesi
  - Etkilediği yerler: `src/strategies/` altına yeni modül, signal router
  - **Plan:** Kullanıcıyla tartışılacak; F-01..F-10 önkoşul okuma.

- [ ] **Q-009:** Sembol seçimi yeniden değerlendirilmeli mi?
  - Bağlam: F-07 → XRP low-vol mean-reverter, F-03 → BTC/BNB trend ETH/SOL choppy. Top-5 uniform tedavi etmek edge'i öldürdü.
  - Etkilediği yerler: `config/settings.yaml` symbols listesi
  - **Plan:** Yeni strateji seçildiğinde sembol filtresi de tasarlanır.

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
