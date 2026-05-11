# Glossary — ICT & Project Terms

> Tek satırlık tanımlar. Detay isteyen konular için ayrı bir
> `docs/concepts/<konu>.md` açılabilir.

## ICT (Inner Circle Trader) terimleri

| Terim | Tanım |
|---|---|
| **ICT** | Inner Circle Trader — Michael J. Huddleston'ın smart money konseptlerine dayanan trading metodolojisi |
| **Market Structure** | Fiyatın swing high ve swing low'larından oluşan yapı |
| **Swing High / Low** | Solunda ve sağında daha düşük/yüksek mumlar olan lokal tepe/dip |
| **BOS (Break of Structure)** | Trend yönünde son swing high/low'un kırılması — trend devamı sinyali |
| **CHoCH (Change of Character)** | Trend tersine son swing high/low'un kırılması — trend dönüş ihtimali |
| **MSS (Market Structure Shift)** | CHoCH + displacement (güçlü mum) ile onaylanmış yapı değişimi |
| **Displacement** | ATR'nin belirgin üstünde (örn. 1.5× ATR) güçlü impulsive mum |
| **FVG (Fair Value Gap)** | 3 mumdan oluşan imbalance: candle[i-1].high < candle[i+1].low (bullish), tersi bearish. "Imbalance" da denir. |
| **Order Block (OB)** | Güçlü hareket öncesi son opposite mum (bullish hareket öncesi son bearish mum gibi) |
| **Breaker Block** | Fail olmuş ve yön değiştirmiş Order Block |
| **Mitigation Block** | Henüz test edilmemiş Order Block |
| **Liquidity** | Stop-loss kümelerinin toplandığı bölgeler — swing high üstü, swing low altı, equal H/L |
| **BSL / SSL** | Buy-Side Liquidity (alış stop'ları, swing high üstü) / Sell-Side Liquidity (satış stop'ları, swing low altı) |
| **Liquidity Sweep / Stop Hunt** | Fitil ile likidite seviyesini geçip body ile geri dönen mum — likiditenin "alınması" |
| **Equal Highs / Lows** | Yakın seviyelerde 2+ swing — güçlü likidite mıknatısı |
| **Premium / Discount** | Range'in %50 üstü = premium (short bölgesi), altı = discount (long bölgesi) |
| **OTE (Optimal Trade Entry)** | Sweep sonrası 0.62-0.79 fib retracement bölgesi |
| **PD Array** | Premium/Discount Array — fiyatın etkileşim kurması beklenen seviye/zone |
| **Killzone** | İşlem yapılması tercih edilen yüksek likidite seansları (London, NY) |
| **London KZ** | London açılış killzone — TR saatiyle ~10:00-13:00 |
| **NY KZ** | New York açılış killzone — TR saatiyle ~15:30-18:30 |
| **Silver Bullet** | NY AM (16:30-17:30 TR) veya PM (20:00-21:00 TR) 1 saatlik penceredeki ICT setup'ı |
| **PO3 (Power of 3)** | Accumulation → Manipulation → Distribution 3-fazlı fiyat şeması |
| **Judas Swing** | Gerçek hareket öncesi kısa süreli ters yöndeki manipülatif hareket |
| **SMT (Smart Money Tool) Divergence** | Korelasyonlu varlıkların (örn. BTC vs ETH) divergence göstermesi |
| **HTF / LTF** | Higher Timeframe / Lower Timeframe — bağıl olarak büyük/küçük zaman dilimi |
| **Bias** | Belirli timeframe'de trend yönü beklentisi (BULL / BEAR / NEUTRAL) |

## Trading / engine terimleri

| Terim | Tanım |
|---|---|
| **Paper trading** | Sanal portföy üzerinde gerçek fiyatla simüle edilen işlem |
| **Slippage** | Emir verildiği fiyat ile fill olduğu fiyat arasındaki fark |
| **RR (Risk/Reward)** | Risk başına potansiyel kazanç oranı — 1:2 = TP mesafesi SL'in 2 katı |
| **Cooldown** | Aynı sembolde art arda sinyal üretmeyi engelleyen süre filtresi |
| **Look-ahead bias** | Backtest'te o anda mümkün olmayan bilgiyi (gelecek bar) kullanma hatası |
| **Profit Factor (PF)** | Toplam kâr / toplam zarar — 1.5+ iyi, 2+ çok iyi |
| **Max Drawdown (MDD)** | Equity curve'ün tepe noktasından en düşük seviyeye yüzdesel düşüşü |
| **Walk-forward analysis** | Parametreyi periyot A'da optimize edip B'de test etme yöntemi |
| **OHLCV** | Open / High / Low / Close / Volume — mum verisi |
| **Perp / Perpetual** | Vade tarihi olmayan futures kontratı |

## Proje-içi terimler

| Terim | Tanım |
|---|---|
| **Hot buffer** | RAM'deki son N mum — Polars DataFrame |
| **Cold storage** | Diskte Parquet olarak saklanan tarihsel mumlar |
| **Signal router** | Strateji sinyallerini cooldown/dedup/risk filtresinden geçiren modül |
| **Pipeline** | Bar close → bias → liquidity → sweep → MSS → FVG → signal akışı |
