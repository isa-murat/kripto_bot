# Memory Bank

> Bu klasör, projeyi geliştiren AI agent'ın (Claude) **kalıcı hafızasıdır.**
> Her oturum başında okur, oturum sonunda günceller. Kullanıcı için de
> proje kararlarının ve durumunun referans noktasıdır.

## Dosyalar

| Dosya | Amaç | Ne zaman güncellenir |
|---|---|---|
| [decisions.md](decisions.md) | ADR — verilen kararlar, neden, alternatifler | Yeni mimari/teknik karar verildiğinde |
| [progress.md](progress.md) | Faz/görev ilerleme tablosu | Her görev tamamlanınca veya yarım kalınca |
| [open_questions.md](open_questions.md) | Cevap bekleyen sorular | Yeni soru çıkınca / cevaplanınca |
| [findings.md](findings.md) | Backtest'lerden çıkan ampirik bulgular | Yeni bulgu üretildiğinde; yeni strateji denemesi öncesi okunur |
| [glossary.md](glossary.md) | ICT ve proje terimleri sözlüğü | Yeni terim kullanılınca |

## Kurallar

1. **Kararlar `decisions.md`'ye ADR formatında yazılır.** Yeni karar = yeni ADR
   numarası. Eski kararlar değiştirilmez, sadece "Superseded by ADR-XXXX" notu
   eklenir.
2. **`progress.md` her oturumda güncellenir.** "Şu an buradayız" durumu burada.
3. **Open question yanıtlanınca:** ya `decisions.md`'ye karar olarak taşınır,
   ya da basitçe silinir.
4. **Glossary tek satırlık tanımlardan oluşur.** Detay isteyen kavramlar için
   ayrıca `docs/concepts/<konu>.md` açılabilir.

## Format örnekleri

### ADR (decisions.md)

```markdown
## ADR-XXXX — Başlık

**Tarih:** YYYY-MM-DD
**Durum:** Accepted | Superseded by ADR-YYYY | Deprecated
**Bağlam:** Hangi problemi çözüyoruz, hangi kısıtlar var?
**Karar:** Ne yapacağız?
**Alternatifler:** Nelere baktık, niye seçmedik?
**Sonuçlar:** Bu kararın yan etkileri/riskleri.
```

### Open question

```markdown
- [ ] **Q-XXX:** Soru metni
  - Bağlam: niye sorduk
  - Etkilediği yerler: hangi modül/karar
```
