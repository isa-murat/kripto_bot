# Run22 Acceptance Contract

PEŞİNEN imzalanmış başarı/başarısızlık kriterleri. Post-hoc rasyonalizasyon, "şu tweak ile dene", "bir parametre daha" YASAK.

**Verdict (2026-05-11):** ❌ **IS FAIL** — 4 kriterden 3'ü kesin altta.
ICT projesi terk, Run23 OOS testi atlandı. Bkz F-14.

## Run22 IS Başarı Kriterleri (Nov 2025 → May 2026)

ZORUNLU şartlar (HEPSİ sağlanmalı):
- [x] ~~Pooled margin > +3pp~~ → **-3.4pp** ❌ FAIL (6.4pp altta)
- [x] ~~En az 3 sembolde individual margin > +2pp~~ → **1** (SOL +3.8pp tek) ❌ FAIL
- [x] Pooled N > 200 (yeterli sample size) → 541 ✅ PASS
- [x] ~~Pooled WR > break-even WR + 3pp~~ → 32.7% < 39.1% gerek ❌ FAIL (6.4pp altta)

**Verdict:** FAIL → ICT projesini terk, Opsiyon A'ya geç.

## Run23 OOS Doğrulama (Run22 başarılıysa, May → Oct 2025)

**ATLANDI — Sözleşme şartı: IS fail → OOS yapma.**

ZORUNLU şartlar (Run22 IS pass olmadığı için uygulanmadı):
- ~~Pooled margin > +1pp (IS'in %30'undan fazla erozyon olmamış)~~
- ~~IS'te +EV olan sembollerin en az %60'ı OOS'ta da +EV~~
- ~~Hiçbir sembolde individual margin < -5pp (catastrophic failure yok)~~

## YASAK LİSTESİ

Şunlar bu sözleşmeye aykırıdır ve YAPILAMAZ:
- Run22 sonrası "biraz parametre tweak edelim" denemeleri
- Run22 sonuçlarına göre acceptance kriterlerini değiştirmek
- Tek bir sembolün +EV olmasını "kısmen başarı" saymak
- IS başarılı OOS başarısızsa "üçüncü pencere deneyelim" demek
- 1h ve 15m kombinasyonunu 2h+30m gibi varyantlarla revize etmek
- "Yakın fail" rasyonalizasyonu yasaktır: Bir kriter eşiğin %5'inden az
  farkla altında kalsa bile FAIL'dir. Yuvarlama, "neredeyse pass",
  "OOS güçlü olursa IS'i affederiz" gibi argümanlar geçersiz. Eşikler
  matematiksel kesin sınırlardır.

19 iterasyondan sonra disiplinden çıkmak için artık zaman yok.
