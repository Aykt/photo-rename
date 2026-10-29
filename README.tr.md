# photo-rename

Fotoğraf ve videoları çekilme zamanına göre yeniden adlandırır:
`IMG_1914.HEIC` → `2026-08-02-2344.heic`

Gerçek çekim zamanını EXIF, XMP, IPTC, QuickTime atomları, sidecar dosyaları ve
dosya adı kalıplarından — güvenilirlik sırasıyla — okur ve her dosyanın hangi
kaynaktan isimlendiğini söyler.

```
$ photo-rename ~/Fotograflar -r --show-times

DOSYA                         METADATA          CREATED           MODIFIED          KAYNAK
IMG_0001.JPG                  2026-08-02 23:44  2026-06-29 19:54  2026-06-29 19:54  EXIF:DateTimeOriginal
IMG_5351.HEIC                 2026-03-17 08:15  2026-06-29 19:54  2026-06-29 19:54  EXIF:DateTimeOriginal
IMG_6003.MOV                  2026-07-04 21:07  2026-06-29 19:54  2026-06-29 19:54  quicktime:creationdate
IMG-20260629-WA0007.jpg       2026-06-29 00:00  2026-06-29 19:54  2026-06-29 19:54  dosya-adi
IMG_1914.jpg                  -                 2026-06-29 19:54  2026-06-29 19:54  metadata-yok
```

## Neden

Çoğu yeniden adlandırıcı tek bir etikete bakar ve pes eder. Mesajlaşma
uygulamaları gizlilik gerekçesiyle EXIF'i siler, Finder'daki "Created" alanı çekim
anı değil kopyalama anıdır, HEIC ve MOV ise Pillow'un tek başına okuyamadığı
formatlardır. Bu araç tam bir yedek zinciri yürütür, bulamadığı bir zamanı uydurmaz
ve her çalışma için geri alma kaydı tutar.

## Kurulum

```bash
git clone https://github.com/<kullanici-adiniz>/photo-rename.git
cd photo-rename
pip install .
```

Ya da tek dosya olarak çalıştır — Pillow dışında zorunlu bağımlılığı yok:

```bash
pip install Pillow
python3 photo_rename.py ~/Fotograflar --show-times
```

### İsteğe bağlı okuyucular

| İhtiyaç | Kurulum | Kurulmazsa |
|---|---|---|
| HEIC / HEIF | `pip install pillow-heif` | `exiftool`, sonra macOS `sips`/`mdls` devreye girer |
| RAW, her format | `brew install exiftool` | diğer okuyucular yaygın durumları kapsar |
| Video | gerekmez — MOV/MP4 yerleşik okunur | `ffprobe`/`exiftool` varsa yedek olarak kullanılır |

Script başlarken hangi okuyucuları bulduğunu yazar.

## Kullanım

```bash
photo-rename ~/Fotograflar -r --show-times                 # incele, hiçbir şey değiştirme
photo-rename ~/Fotograflar -r                              # kuru çalışma
photo-rename ~/Fotograflar -r --apply --lower-ext          # yeniden adlandır
photo-rename ~/Fotograflar -r --apply --fs-time            # dosya sistemi zamanına izin ver
photo-rename --rollback .rename-journal-20261029-134104.json
```

`--apply` verilmeden hiçbir dosya değişmez. Her `--apply` bir JSON kaydı yazar,
`--rollback` bu kaydı ters sırada geri alır.

### Seçenekler

| Bayrak | Etkisi |
|---|---|
| `-r, --recursive` | alt dizinleri de tara |
| `--apply` | gerçekten yeniden adlandır (varsayılan kuru çalışma) |
| `--show-times` | her dosyanın tüm zaman kaynaklarını listele, hiçbir şey değiştirme |
| `--fs-time [MOD]` | son çare dosya sistemi zamanı: `created` (varsayılan), `modified`, `earliest` |
| `--no-filename` | dosya adından tarih çıkarmayı kapat |
| `--unresolved-dir DIZIN` | zamanı çözülemeyen dosyaları yeniden adlandırmak yerine buraya taşı |
| `--lower-ext` | uzantıyı küçük harfe çevir (`.HEIC` → `.heic`) |
| `--journal YOL` | geri alma kaydını belirli bir yere yaz |
| `--rollback JSON` | önceki çalışmayı geri al |

## Zaman kaynağı önceliği

| # | Kaynak | Not |
|---|---|---|
| 1 | EXIF `DateTimeOriginal` / `DateTimeDigitized` / `DateTime` | deklanşör anı, ground truth |
| 2 | XMP `photoshop:DateCreated`, `xmp:CreateDate` | bazı EXIF silme işlemlerinden sağ çıkar |
| 3 | IPTC `DateCreated` + `TimeCreated` | eski düzenleme akışları |
| 4 | `exiftool` | kuruluysa; HEIC, RAW ve videoyu kapsar |
| 5 | `sips` / `mdls` | yalnızca macOS; `mdls` silme sonrası EXIF türevi tarihi tutabilir |
| 6 | QuickTime `com.apple.quicktime.creationdate`, sonra `moov/mvhd` | yerleşik parser, bağımlılıksız |
| 7 | Sidecar `.json` (Google Takeout `photoTakenTime`) veya `.xmp` | |
| 8 | Dosya adı kalıbı | `IMG-20260629-WA0001`, `PXL_20260701_0830…`, `Screenshot 2026-06-29 at 19.54` |
| 9 | Dosya sistemi zamanı | yalnızca `--fs-time` ile |

### Saat dilimi davranışı

EXIF saat dilimi bilgisi taşımayan duvar saatidir, olduğu gibi kullanılır. Apple'ın
QuickTime etiketi duvar saati + UTC offset taşır; offset **çevrilmez, atılır** —
böylece aynı anda çekilen bir fotoğraf ile video aynı ismi alır. `moov/mvhd` ise
gerçekten UTC'dir ve duvar saati bilgisi içermez, yerel saate çevrilir. Çıktıdaki
kaynak etiketi hangi dosyanın hangi yoldan geçtiğini gösterir.

## Güvenlik

- **Varsayılan kuru çalışma.** Yeniden adlandırma geri dönüşü zor bir işlemdir;
  `--apply` zorunludur.
- **Üzerine yazma yok.** Hedef, `os.replace` öncesinde `O_CREAT|O_EXCL` ile atomik
  rezerve edilir; bu, klasik kontrol-sonra-taşı TOCTOU yarışını kapatır. Aynı
  dakikadaki çakışmalar `_2`, `_3` eki alır.
- **Geri alma kaydı.** Her çalışma `from`/`to`/`source` üçlüsünü JSON olarak yazar.
- **Symlink atlanır.** Hedef ağacın dışına çıkma riskini engeller.
- **Dosya sistemi zamanı opt-in.** `st_mtime` kopyalama ve senkronizasyonda değişir;
  bu bir aktarım damgasıdır, çekim zamanı değildir ve istenmedikçe kullanılmaz.
- **Sınırlandırılmış ikili ayrıştırma.** QuickTime okuyucu her atom boyutunu
  kapsayıcısına karşı doğrular, özyineleme derinliğini sınırlar, anahtar sayısı ve
  okuma uzunluklarını tavanlar, metadata taramasını 64 MB ile sınırlar ve 1990–2100
  dışındaki damgaları reddeder. Bozuk, kırpılmış ve sıfır baytlık kapsayıcılar test
  edilmiştir.

## Sınırlar

- Dakika hassasiyeti. Seri çekimler çakışır ve `_2`, `_3` ile ayrılır; bu, çekim
  sırasını korumaz. Saniye için `OUTPUT_FMT` değerini `"%Y-%m-%d-%H%M%S"` yap.
- WhatsApp dosya adları tarih taşır ama saat taşımaz; bu dosyalar `00:00` alır ve
  kaynak etiketi bunu belirtir.
- Silinmiş metadata dosyadan kurtarılamaz. Orijinali yeniden dışa aktar
  (Photos → Export Unmodified Original) veya Google Takeout `.json` sidecar'larını
  kullan.

## Geliştirme

```bash
pip install -r requirements-dev.txt
pytest -q
```

Testler fixture'larını çalışma anında üretir — repoda ikili dosya tutulmaz.
