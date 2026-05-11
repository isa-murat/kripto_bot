# Findings — Sweep+FVG Backtest Sonuçları

> Bu dosya: Sweep+FVG stratejisini 5 sembol × 6 ay üzerinde test ederken
> öğrenilen ampirik bulguları kaydeder. **Bir sonraki strateji denemesinde
> bu bulguları okumadan başlama.** Her madde, parametre seçimi veya filter
> tasarımında ay kazandırır.

**Test penceresi:** 2025-11-01 → 2026-05-11 (~6 ay)
**Semboller:** BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT (Binance USDT-M perp)
**Timeframe:** 1h bias, 5m entry
**Toplam trade (final run):** N=309 (filtersiz çıplak setup)

---

## F-01 — Sweep+FVG bu sembol setinde -EV (N=309 güvenilir)

**Bulgu:** Aynı parametre setiyle 5 sembol × 6 ay → N=309 pooled expectancy
**negatif**. Daha önce N=80'de "kötü ama belki tuning kurtarır" görünen
sistemin, örnek büyüdükçe negatif tarafa **sabitlendiği** doğrulandı.

**Nasıl kullan:** Yeni bir setup'ı validate ederken N≥200 olmadan
"working" deme. Sweep+FVG zaten doğrulanmış edge yok — yeni strateji
denemesinde bu setup'ı baseline olarak kullanma, sıfırdan başla.

**Etkilenen ADR:** ADR-0002 (MVP setup seçimi) → ADR-0011 ile superseded.

---

## F-02 — Küçük-N tuzağı: HTF bias / counter-trend hipotezi N=80'de güçlü, N=309'da invalid

**Bulgu:** N=80 multi-symbol run'da HTF bias filtresi promising görünüyordu
(BTC+BNB pozitif, ETH/SOL felaket). "Counter-trend trade'leri kessek
sistem düzelir" hipotezi konuldu. N artırıldığında etki kayboldu —
örnek küçükken bias filtre lehte/aleyhte rastgele dağılıyor.

**Why:** Sample variance N<100'de margin (~6pp) etkisini dominate ediyor.
Break-even WR ~31% civarındayken, %5'lik WR oynaması margin'i tamamen
ters çeviriyor.

**Nasıl kullan:**
- Yeni filtre ekledikten sonra N'i koru, kıyasla; N düşürmek "lehte gibi
  görünen" yanlış olumlu yaratır.
- "X filtresi kaybı azaltıyor" iddiasını N≥150 olmadan kabul etme.
- HTF bias = ICT'nin ana satışı ama bu sembol setinde çalışmıyor.
  Counter-trend bias ile trade etmek negatif değil; **bias-aligned trade
  etmek de pozitif değil**. Bias bu setup için ayırt edici değil.

---

## F-03 — TP nearest mantığı sembol-bazlı asimetrik

**Bulgu:** "Karşı tarafın en yakın liquidity pool'una TP" yaklaşımı:
- **ETH, SOL:** pozitif etki (uzun TP'ye gitmediği için MFE'yi yakalıyor)
- **BTC, BNB:** negatif etki (kısa TP yüzünden büyük kazançlar kaçıyor)

**Why:** ETH/SOL choppy, fiyat hedefe yaklaşıp dönüyor; BTC/BNB trend
yaptığında uzun hareket ediyor — kısa TP profit-cap koyuyor.

**Nasıl kullan:**
- TP stratejisi uniform olamaz. **Volatility-aware TP** lazım: düşük-vol
  rejimde nearest, yüksek-vol rejimde 2nd/3rd nearest veya R-multiple.
- Sembol-bazlı parametre setleri kaçınılmaz. Tek param seti = ortalama
  altı her sembolde.

---

## F-04 — Fee gross RR'in ~%30'unu yiyor; maker/taker ayrımı zorunlu

**Bulgu:** Naïve "tek fee rate" modelinde net RR ≈ gross RR × 0.85
sanılıyordu. Gerçekte:
- Entry (FVG mid limit) → **maker** (0.02%)
- TP exit (resting limit) → **maker** (0.02%)
- SL exit (stop-market) → **taker** (0.05%)
- EXPIRED close (mark-to-end) → **taker**

Bu modelle gross RR 2.5 → **net RR ~1.75** çıkıyor (~%30 erime), break-even
WR taban olarak ~36-39% oluyor.

**Nasıl kullan:**
- Yeni strateji backtest'inde **maker/taker'ı baştan ayrı modelle**, tek
  rate kullanma. (`src/utils/rr_metrics.py` zaten ikiye ayrılmış durumda.)
- Min RR target'ı **net** RR üstünden seç. Gross 2.0 hedefleyen setup
  gerçekte break-even bile değil.
- Stop-market kullanmak çok pahalı; limit-stop yapısı düşünülmeli (ama
  slippage riskine dikkat).

---

## F-05 — Ranging filter zaruri (kayıbın ~%77'sini önler)

**Bulgu:** 4h trend classifier (HH/LL swing + EMA20 slope agreement;
disagree → ranging) eklenip ranging rejimde trade kesilince:
- Run9 (filtersiz N=80): pooled PnL **-$2480**
- Run10 (ranging excluded N=36): pooled PnL **-$86** → kayıbın **%96.5'i
  silindi** (XRP kaybı eklendi ama gene de net -$86)
- Yani filtersiz olarak ranging trade'ler kayıbın çoğunu üretiyor.

**Why:** ICT setup'ları (sweep + MSS + FVG) directional momentum üzerine
kurulu. Range içinde sweep-MSS sinyali çok üretiyor ama momentum yok;
TP'ye ulaşmadan ranging davranışı stop'a sürüklüyor.

**Nasıl kullan:**
- Hangi setup'ı denersen dene, **regime filter olmadan canlıya çıkma**.
- HTF trend agreement (HH/LL + EMA slope) basit ama etkili. Daha
  sofistike: ADX > 25 + Bollinger band width >median yaklaşımı da
  denenebilir.
- Implementation: [src/analysis/trend_classifier.py](../../src/analysis/trend_classifier.py),
  [src/filters/regime_filter.py](../../src/filters/regime_filter.py)

**Caveat:** XRP low-vol mean-reversion karakterinde — ranging filter
onun pozitif trade'lerini de kesti. Bkz F-07.

---

## F-06 — Kill zone (ICT killzone) crypto'da Forex'teki gibi çalışmıyor

**Bulgu:** ICT'nin Asia / London / NY killzone konsepti FX seansları
üzerine kurulu. Crypto 7/24 + global volume profili Forex'e benzemiyor.
Killzone içinde/dışında trade WR'sinde anlamlı fark gözlemlenmedi.

**Nasıl kullan:**
- Killzone gating'i **opsiyonel** tut, default `require_killzone=False`.
- Eğer kill zone benzeri bir filtre lazımsa kendi sembol-spesifik
  high-volume saatlerini hesapla (volume-of-day profile), Forex saatlerini
  copy etme.
- ICT literatürünün **Forex'ten direkt port edildiği** kısımları crypto'da
  validate edilmeden kullanılmamalı.

---

## F-07 — XRP low-vol mean-reverter (sembol karakteri ≠ uniform)

**Bulgu:** Ranging filter XRP'nin pozitif trade'lerini de kesti
(WR %25, 8 trade -$369). XRP'nin "ranging" görünen pencereleri aslında
mean-reversion için uygun pencereler.

**Nasıl kullan:**
- **Sembol seçimi stratejiden önce gelir.** Top-5 USDT pair eşit değil:
  - BTC: trend-following dominant
  - ETH/SOL: choppy momentum
  - BNB: trend-following ama daha temiz
  - XRP: low-vol mean-reverter (farklı strateji ailesi)
- Yeni strateji denemesinde XRP'yi ya çıkar ya **ayrı strateji** uygula.
  Top-5'i uniform tedavi etmek edge'i öldürür.

---

## F-08 — Weekend effect hipotezi (N=9 ile teyit edilmedi)

**Bulgu:** Cumartesi/Pazar trade'lerinde performansın farklı olduğuna
dair sinyal var (lower volume → choppy fills → düşük WR). Ama N=9 ile
hipotez **istatistiksel anlamlı değil**.

**Nasıl kullan:**
- Weekend filtre eklemek **ucuz** (1 line of code). N büyüdükçe gerçekten
  fark üretiyor mu diye **toggle'lu** dene, ama sonuçları tek başına
  filtre justification'ı olarak kabul etme.
- Forex'ten farklı olarak crypto hafta sonu açık, ama volume tipik olarak
  %30-50 düşüyor — bu sembol-volume profili filtresi olarak modellenmeli
  (saatlik volume z-score), gün-bazlı binary filtre olarak değil.

---

## F-09 — Diversifikasyon sistemi öldürdü (BTC-only cherry-pick'ti)

**Bulgu:** Erken backtest'lerde BTC-only N=8 ile +EV görünüyordu. 5 sembol
açılınca pooled negatif. ETH+SOL'un düşük WR'si (~%15) toplam expectancy'yi
domine etti.

**Nasıl kullan:**
- BTC-only result = single-instrument cherry-pick. Strateji "çalışıyor"
  iddiası için **en az 3-5 farklı karakterli sembol** üstünde validate
  edilmeli.
- Aynı sembolün 6 farklı window'unda backtest etmek de N'i artırmaz —
  sembol-bazlı bias devam eder. Multi-symbol > multi-window.

---

## F-14 — Run22 TF pivot (1h+15m OTE) sözleşmeyle peşinen reddedildi

**Bulgu:** Run20'nin "5m gürültüsü olabilir" hipotezini test etmek için OTE'yi
1h HTF + 15m LTF'e taşıdık. Aynı pencere (Nov 2025 → May 2026), aynı 5 sembol,
sadece TF değişti. Run22 ÖNCESİ imzalanmış sözleşme ([run22_contract.md](run22_contract.md))
4 kriterden 3'ünde net fail:

| Kriter | Hedef | Gerçek | Pass/Fail |
|---|---|---|---|
| Pooled margin > +3pp | > +3pp | **-3.4pp** | ❌ FAIL (6.4pp altta) |
| ≥3 sembolde margin > +2pp | ≥ 3 | **1** (sadece SOL +3.8pp) | ❌ FAIL |
| Pooled N > 200 | > 200 | 541 | ✅ PASS |
| Pooled WR > BE WR + 3pp | > 39.1% | **32.7%** | ❌ FAIL (6.4pp altta) |

Per-symbol breakdown:

| Sembol | N | WR% | Margin | PnL ($) | Run20 (5m) margin | Δ |
|---|---|---|---|---|---|---|
| BTC | 113 | 31.9% | -4.9pp | -1734 | -4.8pp | aynı |
| ETH | 105 | 37.1% | +1.6pp | +327 | -3.6pp | +5.2pp |
| SOL | 102 | 39.2% | **+3.8pp** | +1075 | -7.9pp | +11.7pp |
| BNB | 111 | 25.2% | -11.4pp | -3339 | -11.2pp | aynı |
| XRP | 110 | 30.9% | -5.0pp | -1695 | **+7.0pp** | **-12.0pp** |

**Why:** TF pivot semboller arası karakteri yeniden dağıttı ama edge üretmedi:
- SOL +EV oldu (-7.9 → +3.8pp), ETH ödün verdi (-3.6 → +1.6pp).
- XRP, Run20 IS'de tek +EV olan sembol, 15m'de tamamen çöktü
  (+7.0 → -5.0pp). F-13'teki "window-specific fluke" tezi şimdi
  TF-specific de oldu: sembol karakteri TF'e göre değişiyor, tek
  konfigürasyon ile +EV stabil değil.
- BTC ve BNB iki TF'de de neredeyse aynı kötülükte (-4.8/-4.9pp ve
  -11.2/-11.4pp). Bu sembollere OTE setup'ı uymuyor, TF ile alakası yok.

**Sözleşme verdict:** ICT projesi terk edildi. Run23 OOS testi atlandı
(sözleşme şartı: IS fail → OOS yapma). Pivot to Opsiyon A (ICT
fundamental abandon → statistical/ML/order-flow yaklaşımları).

**Nasıl kullan:**
- ICT primitives (swing, FVG, OB, MSS, sweep) ve setup'lar (sweep+FVG, OTE)
  bu sembol setinde çalışmıyor. Yeni denemede ICT-aile setup'ları **baseline
  kabul etmeyin** — Q-011 cevabı A: terk.
- 20 backtest iterasyonu (Sweep+FVG 5 sweep + OTE 14 öncesi + Run20 IS +
  Run21 OOS + Run22 IS) sonucu **hiç bir ICT setup-symbol-TF kombinasyonu
  ardışık iki pencerede +EV vermedi**.
- TF değişimi tek başına yeterli değil. ICT'nin asıl problemi paradigma
  uygunluğu: Forex session-bound varsayımları + retracement öngörülebilirliği
  + displacement güvenilirliği crypto'da geçici durumlar.
- **Pre-signed contract disiplini doğru iş yaptı:** kriterler IS sonrası
  revize edilmeden uygulandı, "yakın fail" rasyonalizasyonuna kapı açılmadı.
  Sonraki paradigm denemesinde aynı disiplin geçerli.

**İlgili dosyalar:**
[run22_contract.md](run22_contract.md),
[multi_run22.json](../../data/backtest/multi_run22.json),
[BTCUSDT_run22.json](../../data/backtest/BTCUSDT_run22.json) (ve diğer 4)

---

## F-13 — XRP-OTE F-11'in üçüncü tekrarı: 15pp margin swing

**Bulgu:** Run20 IS (2025-11..2026-05) XRP-OTE **+7.0pp** margin (N=280,
return +68.9%, Sharpe 2.03). Run21 OOS (aynı config, 2025-05..2025-10)
**-8.3pp** (N=276, return -53.1%, Sharpe -3.39, Max DD 54%).
Margin swing **+7.0 → -8.3 = ~15pp**, ETH Run13/15'teki 11pp swing'den
daha büyük.

İki bağımsız 6-ay penceresi, ikisinde de N>F-02 eşiği (200). Bu yine
F-11'in "tek window +EV ≠ edge" kuralı — bu kez **üçüncü kez** tetiklendi:

| Sembol-Setup | IS margin | OOS margin | Swing | Karar |
|---|---|---|---|---|
| ETH Sweep+FVG (Run13/15) | +2.85pp | -8.10pp | 11pp | Edge yok |
| XRP OTE (Run20/21) | +7.00pp | -8.30pp | 15pp | Edge yok |
| (BTC sweep, BNB sweep, vb.) | Çoğu IS -EV | — | — | Edge yok |

**19 backtest iterasyonu (5 sweep + 14 OTE öncesi + Run20 + Run21)
sonucu: hiçbir ICT setup-symbol kombinasyonu ardışık iki pencerede +EV
göstermedi.**

**Why:** Crypto piyasası rejim değişikliği fazla. Bir setup'ın "rejim-uyumlu"
olduğu pencerede +EV görünmesi cherry-pick'ten ayırt edilemez. ICT'nin
Forex spot piyasası için tasarlanmış varsayımları (fixed session-bound
behavior, displacement güvenilirliği, retracement öngörülebilirliği)
crypto'da geçici durumlar yaratıyor.

**Nasıl kullan:**
- ICT setup'ları için **üç-pencere disiplini** standart: IS + OOS-prior +
  OOS-later. Tek IS pencere bilgi-tüketici, gerçek edge testi değil.
- N=200-300 bile yeterli değil — pencere seçimi de varyans kaynağı.
  Cross-window N≥600 (üç bağımsız 6-ay penceresi) olmadan "edge var"
  kabul edilmesin.
- Naïve sembol karakterizasyonu (F-07'nin "XRP mean-reverter") rehberlik
  vermiyor: F-12'de paradigma reversal gözlemledik, F-13'te o da çöktü.
- Pivot kararı: Q-011 (ICT paradigm full abandon mi, sembol-tarama mı,
  farklı TF mi?)

**İlgili dosyalar:**
[Run20](../../data/backtest/multi_ote_run20.json),
[Run21](../../data/backtest/multi_ote_run21_xrp_oos.json)

---

## F-12 — OTE'nin paradigm reversal sürprizi (IS-only)

**Bulgu:** OTE trend-following olarak tasarlandı. Run20 IS'de **sadece XRP**
+EV (+7.0pp). F-07'de XRP "low-vol mean-reverter" diye nitelendirilmişti
— yani OTE'nin paradigmasına ters karakter. Diğer 4 sembol (BTC -4.8,
ETH -3.6, SOL -7.9, BNB -11.2pp) -EV.

Hipotez: OTE retracement-bağımlı. Mean-reverting sembollerde
retracements güvenilir ve sık → fib zone'a hassas dokunuş → daha iyi
WR. Trending sembollerde impulse sonrası fiyat retracement yapmadan
devam edebilir ya da geç entry'de trend bitmiş olur → SL.

Yani **sembol-strategy eşleştirmesi tezin TERSİ ile mi çalışıyor?**
- Trend-following setup → mean-reverting karaktere uyum
- Mean-reversion setup → trending karaktere uyum

**Caveat:** Bu IS gözlemi F-13'te OOS validation'da çöktü. Yani paradigm
reversal HİPOTEZİ de window-specific çıktı. F-12'nin pratik değeri kaldıysa
"naïve sembol karakterizasyonuna güvenme" uyarısıdır.

**Nasıl kullan:**
- "Bu setup şu tür sembollere uyar" iddialarını test öncesi varsayma.
- F-07'deki sembol-bazlı strateji aileleri planını **deneye dayalı** yap
  (paradigmaya değil).
- Trend-following setup'ı sadece "trending coin" üstünde, mean-rev'i
  sadece "ranging coin" üstünde test etme tuzağına düşme. Çapraz test et.

---

## F-11 — Run13'ün ETH +EV'si window-specific, OOS'ta çöktü (Run15)

**Bulgu:** Run13'te (2025-11-01 → 2026-05-11) ETH-only N=67 ile **+EV**
görünüyordu (WR 35.82%, margin +2.85pp, +$11.18/trade, +7.49% return).
"ETH'te edge var, niche bot yapalım" hipotezi konuldu.

Out-of-sample test (Run15, 2025-05-01 → 2025-10-31, aynı config, ETH-only):
- N=86 (Run13'ten büyük örnek)
- WR **25.58%**, margin **-8.10pp**, -$28.62/trade, -24.61% return
- Profit factor 0.69, Max DD 32.13%

İki bağımsız 6-aylık pencere arasında margin **+2.85 → -8.10** (~11pp swing).
"Real edge" iki window'da da +EV gerektirir; bir pencerede +EV diğerinde
sert -EV = sample variance / regime luck.

**Why:** Run13'ün pozitif pencereyi (Nov→May) cherry-pick etmesi — backtest
hep en yeni datayla başladığı için bu pencere "varsayılan" oldu. Önceki
6 ay test edilseydi en başta -EV görülürdü.

**Nasıl kullan:**
- **OOS validation iki ayrı zaman penceresinde, ardışık** yapılmalı.
  IS+OOS aynı 6 ayın train/test'i değil; iki tam 6 ay olmalı.
- "Promising single-symbol +EV" sinyali **kabul edilecekse** önce
  paralel pencere test'i. Tek window pozitifse hâlâ %50 rastlantı.
- Run13'ün thin margin'i (+2.85pp) zaten kırmızı bayraktı; break-even
  WR'ye %3 altında durmak istatistiksel anlamlılık vermez.
- F-02'nin "küçük-N tuzağı"nın **window-tuzağı** versiyonu: örnek
  büyütmek tek başına yetmez, pencere de çeşitlendirilmeli.

**Karar:** ETH-only niche bot yapılmıyor (Opsiyon B). Sweep+FVG her
sembolde -EV; ADR-0011 doğrulandı, yeni strateji ailesine geç.

**İlgili runlar:** [ETHUSDT_run13.json](../../data/backtest/ETHUSDT_run13.json),
[ETHUSDT_run15.json](../../data/backtest/ETHUSDT_run15.json)

---

## F-10 — Fee + ranging filter tek başına yetmiyor

**Bulgu:** Doğru fee modeli + ranging filter ile sistem -$2480 → -$86
geldi (run9 → run10) ama hâlâ negatif. Ek 1-2 filtre daha gerek olabilir
ya da setup'ın kendisi -EV.

**Nasıl kullan:**
- Marginal improvement her step önemli ama **break-even'e geçiş** lazım,
  kâra değil. -$86 / 36 trade ≈ -$2.4/trade, fee modeli +%3 hatası
  bunu paritede gösterebilir.
- "Bir filtre daha eklersem +EV olur" optimizm bias'ı: her filtre N'i
  düşürür, overfitting riski artar. Her yeni filtre için **out-of-sample**
  window ayır.

---

## Özet — Yeni strateji denemesine başlamadan önce

1. **N≥200 olmadan herhangi bir conclusion verme** (F-01, F-02).
2. **Maker/taker fee'yi baştan modelle**, net RR üstünden hedefle (F-04).
3. **Regime filter olmadan canlıya çıkma** (F-05).
4. **Sembol-bazlı parametre setleri planla**, uniform setup'a güvenme (F-03, F-07).
5. **Killzone, weekend, vb. Forex'ten gelen filtreleri** validate etmeden
   açma (F-06, F-08).
6. **BTC-only positive ≠ strategy works**. Multi-symbol pooled metric
   kullan (F-09).
7. **TP/SL tasarımı volatility-aware olmak zorunda** — tek formül her
   sembole uymaz (F-03).
8. **Single-window +EV ≠ edge.** İki bağımsız zaman penceresinde
   ardışık +EV şart. Thin margin (<+4pp) hâlâ rastlantı (F-11).
   **Üç bağımsız pencere** disiplini ICT'de standart yap (F-13).
9. **Sembol karakteri (mean-reverter / trender) sezgisi yanıltıcı.**
   Paradigma-eşleştirmesi varsayımına güvenme, deneye dayalı seç (F-12).
10. **20 iterasyon ICT bilançosu (Sweep+FVG + OTE 5m + OTE 15m):**
    hiçbir setup-symbol-TF kombinasyonu ardışık iki pencerede +EV
    vermedi. Run22 sözleşmeyle FAIL → Q-011 cevabı A: ICT terk (F-14).
11. **Pre-signed acceptance contract disiplini etkili.** IS sonuçları
    görünmeden kriter yazıldı, "yakın fail" rasyonalizasyon yasağı
    eklendi (F-14). Sonraki paradigm denemesinde aynı disiplin.
