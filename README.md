# fixture-calendar

footcal.cbdm.app takım feed'lerini birleştirir, her maça uyarı ekler ve GitHub Actions ile
günde 2 kez güncellenen tek bir `fixtures.ics` üretir. Sunucu yok, API anahtarı yok.

## Kurulum (5 adım)

1. GitHub'da **public** bir repo aç (örn. `fixture-calendar`). Bu klasördeki dosyaları olduğu gibi yükle
   (`.github/workflows/build.yml` yolunu koru).
2. `teams.json` dosyasını düzenle:
   - `teams`: footcal takım ID'leri (footcal URL'sindeki sayı, örn. `https://footcal.cbdm.app/team/645/` → `645`).
     ID bulmak için: https://footcal.cbdm.app/search
   - `competitions`: istersen lig/turnuva ID'leri (`/comp/2/` → `2`), gerekmiyorsa boş bırak.
   - `alerts_minutes_before`: uyarı zamanları, dakika olarak. `[60, 15]` = 1 saat ve 15 dk önce. `0` = maç başlarken.
3. Repo → **Settings → Actions → General → Workflow permissions** → "Read and write permissions" seç, kaydet.
   (Bot'un `fixtures.ics`'i commit'lemesi için gerekir.)
4. Repo → **Actions → "Fikstürü güncelle" → Run workflow** ile ilk çalıştırmayı elle tetikle.
   Bitince repoda `fixtures.ics` görünmeli. Sonrası otomatik: her gün 08:00 ve 20:00 (TR),
   ayrıca `teams.json`'ı her değiştirdiğinde.
5. Apple Takvim'e abone ol. Adres:

       webcal://raw.githubusercontent.com/<kullanici>/<repo>/main/fixtures.ics

   - **Mac:** Takvim → Dosya → Yeni Takvim Aboneliği → adresi yapıştır → Konum: **iCloud** →
     Otomatik yenile: **Her saat** → "Kaldır: Uyarılar" kutusunun **işaretsiz** olduğundan emin ol.
   - **iPhone:** Ayarlar → Takvim → Hesaplar → Hesap Ekle → Diğer → Abone Olunan Takvim Ekle → adresi yapıştır.
     (Mac'te iCloud'a eklediysen zaten iPhone'a da gelir; ikisini birden yapma.)

Eski footcal aboneliğini sil, yoksa maçlar iki kere görünür.

## Notlar

- Aynı maç iki takımın feed'inde de varsa (derbi) UID üzerinden tekilleştirilir, bir kez görünür.
- footcal takım başına yaklaşık son 5 + gelecek 10 maçı verir; pencere her güncellemede ileri kayar.
- footcal erişilemezse eski `fixtures.ics` korunur, takvimin boşalmaz.
- Uyarı sayısını/zamanını değiştirmek için sadece `teams.json`'ı düzenleyip commit'le; Actions kendisi yeniden üretir.

---

# FC 27 Ultimate Team takvimi (`fc27.ics`)

Aynı repoda ikinci bir takvim. Üç katman:

| Katman | Kaynak | Otomatik mi? |
|---|---|---|
| SBC / Evolution / Objective **bitişleri** | fut.gg (JSON API, yoksa sayfa verisi) | Evet, 6 saatte bir |
| Promo **başlangıç/bitiş** | `fc27_events.json` → `promos` | Elle — duyuru gelince tarihi düzelt |
| Haftalık **ritüeller** (TOTW, ödüller, Cuma içerik saati) | `fc27_events.json` → `rituals` | RRULE, sezon boyunca |

Uyarılar: bitişlerden 24 sa + 3 sa önce, promolardan 24 sa önce, ritüellerde tam saatinde.
Hepsi `fc27_events.json` başındaki alanlardan değişir.

## Kurulum

1. Yeni dosyaları repoya ekle: `fc27_build.py`, `fc27_events.json`, `.github/workflows/fc27.yml`, `.gitignore`.
2. Actions → **"FC 27 takvimini güncelle"** → Run workflow.
3. Apple Takvim'e ikinci abonelik (Uyarıları Kaldır **kapalı**!):

       https://raw.githubusercontent.com/MustafaEEroglu/fixtures/main/fc27.ics

   Takvime farklı bir renk ver, maçlarla karışmasın.

## İlk hafta kalibrasyonu (önemli)

fut.gg'nin FC 27 verisi 18–25 Eylül'den itibaren dolacak. Script bitiş saatini
alan adını tahmin ederek buluyor (`expiresAt`, `endTime`, `deadline`...). İlk gerçek
çalışmadan sonra:

- Actions log'unda `[OK] SBC: N bitiş` satırlarına bak. `0 bitiş` görüyorsan alan adı tutmamıştır.
- Aynı çalışmanın **Artifacts → futgg-debug** dosyasını indir; içindeki `sbc.json` /
  `evolutions.json`'a bakıp gerçek alan adını `fc27_build.py`'deki `find_dt(...)` regex'ine ekle.

Bu tek seferlik bir ayar; sonrası kendi kendine çalışır.

## Promo tarihlerini güncellemek

`fc27_events.json` → `promos` listesinde ilgili satırın `start`/`end`'ini düzelt,
`"estimated": true` alanını kaldır, commit'le. Actions otomatik yeniden üretir; Apple
"(tahmini)" etiketini düşürür ve tarihi kaydırır (UID sabit olduğu için etkinlik çoğalmaz).
Tarih formatı: tüm gün için `"2026-10-23"`, saatli için `"2026-09-23T20:00"` (TR saati).
