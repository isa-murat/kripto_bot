# Run22 Acceptance Contract

PEŞİNEN imzalanmış başarı/başarısızlık kriterleri. Post-hoc rasyonalizasyon, "şu tweak ile dene", "bir parametre daha" YASAK.

## Run22 IS Başarı Kriterleri (Nov 2025 → May 2026)

ZORUNLU şartlar (HEPSİ sağlanmalı):
- [ ] Pooled margin > +3pp
- [ ] En az 3 sembolde individual margin > +2pp
- [ ] Pooled N > 200 (yeterli sample size)
- [ ] Pooled WR > break-even WR + 3pp

Bunlardan biri eksikse → Run22 BAŞARISIZ → ICT projesini terk, Opsiyon A'ya geç.

## Run23 OOS Doğrulama (Run22 başarılıysa, May → Oct 2025)

ZORUNLU şartlar:
- [ ] Pooled margin > +1pp (IS'in %30'undan fazla erozyon olmamış)
- [ ] IS'te +EV olan sembollerin en az %60'ı OOS'ta da +EV
- [ ] Hiçbir sembolde individual margin < -5pp (catastrophic failure yok)

Bunlardan biri eksikse → ICT projesini terk, Opsiyon A'ya geç.

## YASAK LİSTESİ

Şunlar bu sözleşmeye aykırıdır ve YAPILAMAZ:
- Run22 sonrası "biraz parametre tweak edelim" denemeleri
- Run22 sonuçlarına göre acceptance kriterlerini değiştirmek
- Tek bir sembolün +EV olmasını "kısmen başarı" saymak
- IS başarılı OOS başarısızsa "üçüncü pencere deneyelim" demek
- 1h ve 15m kombinasyonunu 2h+30m gibi varyantlarla revize etmek

19 iterasyondan sonra disiplinden çıkmak için artık zaman yok.
