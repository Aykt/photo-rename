# Katki

```bash
git clone https://github.com/<kullanici-adiniz>/photo-rename.git
cd photo-rename
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q && ruff check .
```

## Yeni bir zaman kaynagi eklemek

1. `from_<kaynak>(path) -> tuple[datetime | None, str]` imzasinda bir fonksiyon yaz.
   Ikinci deger `--show-times` ciktisinda gorunen kaynak etiketidir.
2. `resolve_metadata()` icindeki zincire guvenilirlik sirasina gore yerlestir.
3. `tests/` altina bir test ekle. Fixture'lar calisma aninda uretilir;
   repoya ikili dosya ekleme.

## Kurallar

- Yeni bir zorunlu bagimlilik eklemeden once yerlesik bir cozum ara.
  Dis araclar (`exiftool`, `ffprobe`) yalnizca yedek katman olabilir.
- Guvenilmez ikili veri ayristiran kod sinirli olmali: boyut dogrulamasi,
  ozyineleme tavani, okuma uzunlugu siniri.
- Bulunamayan bir zamani tahmin etme. Yanlis etiketlenmis veri, etiketsiz
  veriden daha zararlidir.
