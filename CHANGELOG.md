# Changelog

Bu proje [Semantic Versioning](https://semver.org/lang/tr/) kullanir.

## [1.0.0] - 2026-10-29

### Eklenen
- EXIF / XMP / IPTC zincirinden cekim zamani okuma (Pillow)
- HEIC/HEIF destegi: `pillow-heif`, `exiftool` ve macOS `sips`/`mdls` yedekleriyle
- Bagimliliksiz QuickTime/MP4 okuyucu: `com.apple.quicktime.creationdate` ve `moov/mvhd`
- Google Takeout `.json` ve `.xmp` sidecar destegi
- Dosya adi kalibi cikarimi (WhatsApp, Pixel, Screenshot)
- `--fs-time` ile opt-in dosya sistemi zamani (`created` / `modified` / `earliest`)
- `--show-times` denetim modu
- `--unresolved-dir` ile zamani cozulemeyen dosyalari karantinaya alma
- JSON geri alma kaydi ve `--rollback`
- `O_CREAT|O_EXCL` ile atomik hedef rezervasyonu (TOCTOU yarisi kapali)
