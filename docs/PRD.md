# PRD — kripto_bot (ICT Scalping Signal Bot)

| Alan | Değer |
|---|---|
| Versiyon | 0.1 (taslak) |
| Tarih | 2026-05-10 |
| Sahip | isamurat233@gmail.com |
| Durum | Faz 0 — geliştirme başladı |

## 1. Problem

Kripto piyasasında ICT metodolojisi popüler ve manuel olarak işe yarayan bir
yaklaşım. Ancak:
- Manuel takip yorucu — 5 coin × 5m timeframe sürekli grafik başında olmayı gerektiriyor
- Killzone'lar (London/NY) gün içinde belirli saatlere denk geliyor
- Setup'ların algoritmik kurallara çevrilmesi disiplini artırır ve duygusal kararı azaltır
- Stratejinin gerçekten edge'i olup olmadığını anlamak için backtest gerekiyor

## 2. Hedef

ICT konseptlerini (özellikle "Liquidity Sweep + FVG Entry" setup'ı) algoritmik
kurallara dökerek:
1. Otomatik sinyal üretimi (Telegram'a)
2. Paper trading ile gerçek piyasa şartlarında performans ölçümü
3. Tarihsel backtest ile setup'ın istatistiksel edge'inin doğrulanması

## 3. Kapsam (MVP)

### Dahil
- Binance USDT-M Futures (perp)
- 5 coin: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT
- Timeframe'ler: 1m (entry tetik), 5m (ana entry timeframe), 1h (bias)
- Setup: **Liquidity Sweep + FVG Entry**
- HTF bias: 1h trend + premium/discount
- Killzone filtresi: London (10:00–13:00 TR) ve NY (15:30–18:30 TR)
- Paper trading: virtual portföy, slippage + fee simülasyonu
- Telegram bildirim: entry/exit/günlük özet
- Backtest: aynı engine ile tarihsel veri üzerinde

### Hariç (sonraki fazlar)
- Gerçek emir gönderimi
- Multi-exchange (sadece Binance)
- Diğer ICT setup'ları (Silver Bullet, OTE, breaker blocks)
- ML tabanlı sinyal üretimi
- SMT divergence (correlated assets)
- Daily/Weekly bias
- Web dashboard (CLI + Telegram yeterli)

## 4. Kullanıcı senaryoları

### S1 — Sabah brief
Kullanıcı sabah Telegram'da botun gece ne yaptığını görür: kaç sinyal üretildi,
açık pozisyon var mı, dünkü P&L ne?

### S2 — Killzone içi sinyal
London açılışında BTCUSDT'de bullish liquidity sweep + FVG oluşur. Bot:
1. Sinyali tespit eder
2. Telegram'a "🟢 BTCUSDT LONG @ 95430, SL 95280, TP 95730" gönderir
3. Paper portföyde pozisyon açar, equity'nin %1'i risk
4. SL/TP'ye değince kapatır, sonucu Telegram'a yazar

### S3 — Backtest çalıştırma
Kullanıcı CLI'dan `python -m src.backtest.runner --symbol BTCUSDT --from 2025-11-01`
çalıştırır. Bot 6 aylık 5m veri üzerinde Sweep+FVG'yi simüle eder ve metrikleri
üretir: win rate, profit factor, max drawdown, equity curve, trade dağılımı.

### S4 — Parametre tuning
Kullanıcı `strategy_params.yaml`'da swing lookback'i 5'ten 7'ye çekip backtest'i
tekrar koşturur, metrik karşılaştırması yapar.

## 5. Fonksiyonel gereksinimler

| ID | Gereksinim |
|---|---|
| F-01 | Bot, 5 sembol için 1m/5m/1h OHLCV verisini WebSocket ile real-time alır |
| F-02 | Bot bağlantı koparsa otomatik yeniden bağlanır, eksik mumları REST ile doldurur |
| F-03 | Tarihsel OHLCV Parquet olarak diske cache'lenir |
| F-04 | Her 5m bar kapanışında ICT analiz pipeline'ı tetiklenir |
| F-05 | 1h bias hesaplanır: trend (BOS yönü) + premium/discount |
| F-06 | 5m'de sweep tespit edilir; bias yönüne uyuyorsa devam |
| F-07 | Sweep sonrası MSS + FVG aranır |
| F-08 | FVG'ye retest entry'si limit emir olarak paper portföye girer |
| F-09 | SL = sweep fitilinin 0.2×ATR ötesi, TP = opposite liquidity (min RR=2) |
| F-10 | Killzone dışında sinyal üretilmez |
| F-11 | Aynı sembolde 30dk cooldown |
| F-12 | Maksimum 2 eşzamanlı pozisyon |
| F-13 | Paper broker fee (0.04% taker) ve slippage (1-2 tick) uygular |
| F-14 | Her sinyal/entry/exit Telegram'a gönderilir |
| F-15 | Günlük 23:00 TR'de özet rapor: sinyal sayısı, win/loss, P&L, equity |
| F-16 | Tüm trade'ler ve equity snapshot'ları SQLite'a yazılır |
| F-17 | Backtest engine canlı engine ile aynı ICT primitives'i kullanır |
| F-18 | Backtest çıktısı: win rate, PF, max DD, Sharpe, trade listesi |

## 6. Fonksiyonel olmayan gereksinimler

| ID | Gereksinim |
|---|---|
| NF-01 | Tüm zaman damgaları UTC'de saklanır, gösterimde TR'ye çevrilir |
| NF-02 | Look-ahead bias yok: bar kapanmadan karar verilmez |
| NF-03 | ICT primitives unit-testli (FVG, swing, sweep, BOS) |
| NF-04 | Config değişikliği kod değişikliği gerektirmez (YAML) |
| NF-05 | Hata durumunda bot çökmez; log + Telegram alert + recovery |
| NF-06 | API key'ler `.env`'de, repo'ya commit edilmez |
| NF-07 | Telegram mesajları rate-limit'e uyar (saniyede 1 mesaj) |

## 7. Başarı kriterleri (MVP kabul)

- [ ] 1 hafta kesintisiz canlı paper çalışma
- [ ] En az 20 paper trade tamamlanmış olmalı
- [ ] Bütün trade'lerde SL/TP doğru tetiklendi
- [ ] Backtest 6 aylık veride çalışıyor
- [ ] Backtest metrikleri raporlanıyor (win rate, PF, max DD)
- [ ] Telegram bildirimleri kesintisiz geliyor
- [ ] Bot bağlantı kopmasından sonra kendini toparlayabiliyor

## 8. Açık konular

→ [docs/memory_bank/open_questions.md](memory_bank/open_questions.md)
