#!/usr/bin/env python3
"""
photo_rename.py v3 — Fotograflari cekilme zamanina gore yeniden adlandirir.

Hedef format : YYYY-AA-GG-SSDD   ->  2026-08-02-2344.jpg

Zaman kaynagi oncelik sirasi (guvenilirlik azalan sirada):
  1. EXIF    DateTimeOriginal / DateTimeDigitized / DateTime      [Pillow]
  2. XMP     photoshop:DateCreated / xmp:CreateDate               [Pillow]
  3. IPTC    DateCreated (2:055) + TimeCreated (2:060)            [Pillow]
  4. exiftool  (kuruluysa; HEIC/RAW/video dahil her formati okur)
  5. sips / mdls  (yalnizca macOS; pillow-heif yoksa HEIC icin)
  6. Sidecar  .json (Google Takeout photoTakenTime) / .xmp
  7. Dosya adi kalibi (IMG-20260629-WA0001, PXL_20260701_0830 ...)
  8. Video: QuickTime/MP4 atom okuyucu (bagimliliksiz)
       moov/meta com.apple.quicktime.creationdate  -> yerel saat + offset
       moov/mvhd creation_time                     -> UTC, 1904 epoch
     ardindan ffprobe / exiftool / mdls
  9. Dosya sistemi zamani --> yalnizca --fs-time ile.
       created  = Finder "Created"  (st_birthtime)
       modified = Finder "Modified" (st_mtime)
     Bu deger cekim ani degil, dosyanin diske yazildigi andir.

Varsayilan davranis DRY-RUN'dir. Gercek islem icin --apply gerekir.

Bagimlilik: Pillow        (pip install Pillow)
HEIC icin : pillow-heif   (pip install pillow-heif)   <-- onerilen
            veya exiftool (brew install exiftool)
            veya macOS'un yerlesik sips/mdls araclari (otomatik)
Video icin: ek arac gerekmez (yerlesik MOV/MP4 okuyucu).
            ffprobe/exiftool varsa yedek olarak kullanilir.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from PIL import Image, IptcImagePlugin
except ImportError:
    sys.exit("HATA: Pillow kurulu degil.  ->  python3 -m pip install --user Pillow")

try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
    HEIF_OK = True
except ImportError:
    HEIF_OK = False

HAS_EXIFTOOL = shutil.which("exiftool") is not None
IS_MACOS = sys.platform == "darwin"

# --- Sabitler ---------------------------------------------------------------

HEIC_EXT = {".heic", ".heif", ".hif", ".avif"}
IMAGE_EXT = {".jpg", ".jpeg", ".jpe", ".tif", ".tiff", ".png", ".webp",
             ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf"} | HEIC_EXT
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".3gp"}

EXIF_IFD = 0x8769
TAG_DT_ORIGINAL = 36867
TAG_DT_DIGITIZED = 36868
TAG_DT = 306

OUTPUT_FMT = "%Y-%m-%d-%H%M"
ALREADY_NAMED = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}(_\d+)?$")

NAME_PATTERNS = [
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)-WA\d+", re.I), "wa"),
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})[_\-T ]"
                r"(\d{2})(\d{2})(\d{2})?\d{0,3}(?!\d)"), "full"),
    (re.compile(r"(20\d{2})-(\d{2})-(\d{2})[ _T]?(?:at )?(\d{2})[.:\-](\d{2})"), "full"),
]


# --- Yardimcilar ------------------------------------------------------------

def _parse_exif_string(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    value = value.strip().rstrip("\x00")
    if not value or value.startswith("0000"):
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(v[:19], fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _parse_wallclock(value: str) -> datetime | None:
    """
    ISO-8601'i duvar saati olarak okur: offset varsa ATILIR, cevrilmez.
    '2026-07-04T21:07:45+0300' -> 2026-07-04 21:07:45
    """
    if not value:
        return None
    v = value.strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?", v)
    if not m:
        return None
    y, mo, d, hh, mi, ss = (int(x) if x else 0 for x in m.groups())
    try:
        return datetime(y, mo, d, hh, mi, ss)
    except ValueError:
        return None


def _run(cmd: list[str], timeout: int = 20) -> str | None:
    """Dis arac calistirir. Dosya adi argv ile gecer, kabuk yorumlamasi yok."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, check=False)
        return out.stdout if out.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


# --- Kaynak 1-3: Pillow ile dosya ici metadata -----------------------------

def from_exif(im) -> tuple[datetime | None, str]:
    exif = im.getexif()
    if not exif:
        return None, ""
    sub = exif.get_ifd(EXIF_IFD)
    for tag, table, label in (
        (TAG_DT_ORIGINAL, sub, "EXIF:DateTimeOriginal"),
        (TAG_DT_DIGITIZED, sub, "EXIF:DateTimeDigitized"),
        (TAG_DT, exif, "EXIF:DateTime"),
    ):
        dt = _parse_exif_string(table.get(tag))
        if dt:
            return dt, label
    return None, ""


def from_xmp(im) -> tuple[datetime | None, str]:
    raw = im.info.get("XML:com.adobe.xmp") or im.info.get("xmp")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    if not raw:
        return None, ""
    for key in ("photoshop:DateCreated", "xmp:CreateDate",
                "exif:DateTimeOriginal", "xmp:ModifyDate"):
        m = re.search(rf'{key}\s*=\s*"([^"]+)"', raw) or \
            re.search(rf'<{key}>([^<]+)</{key}>', raw)
        if m:
            dt = _parse_iso(m.group(1))
            if dt:
                return dt, f"XMP:{key.split(':')[1]}"
    return None, ""


def from_iptc(im) -> tuple[datetime | None, str]:
    try:
        info = IptcImagePlugin.getiptcinfo(im)
    except Exception:
        return None, ""
    if not info:
        return None, ""
    date_b = info.get((2, 55))
    if not date_b:
        return None, ""
    d = date_b.decode("ascii", "ignore") if isinstance(date_b, bytes) else str(date_b)
    time_b = info.get((2, 60))
    t = (time_b.decode("ascii", "ignore") if isinstance(time_b, bytes)
         else str(time_b or ""))[:6] or "000000"
    try:
        return datetime.strptime(d[:8] + t.ljust(6, "0"), "%Y%m%d%H%M%S"), "IPTC:DateCreated"
    except ValueError:
        return None, ""


def from_pillow(path: Path) -> tuple[datetime | None, str]:
    """Pillow ile acip EXIF/XMP/IPTC dener. Acamazsa bos doner (hata firlatmaz)."""
    try:
        with Image.open(path) as im:
            for fn in (from_exif, from_xmp, from_iptc):
                dt, src = fn(im)
                if dt:
                    return dt, src
    except Exception:
        return None, ""
    return None, ""


# --- Kaynak 4: exiftool -----------------------------------------------------

def from_exiftool(path: Path) -> tuple[datetime | None, str]:
    """
    exiftool her formati okur (HEIC, RAW, video). Kuruluysa en saglam kaynak.
    -j JSON ciktisi, -n ham deger, -G0 grup adlari.
    """
    if not HAS_EXIFTOOL:
        return None, ""
    out = _run(["exiftool", "-j", "-m", "-fast2",
                "-DateTimeOriginal", "-CreateDate", "-SubSecDateTimeOriginal",
                "-ContentCreateDate", "-MediaCreateDate", str(path)])
    if not out:
        return None, ""
    try:
        data = json.loads(out)[0]
    except (json.JSONDecodeError, IndexError, KeyError):
        return None, ""
    for key in ("SubSecDateTimeOriginal", "DateTimeOriginal", "CreateDate",
                "ContentCreateDate", "MediaCreateDate"):
        val = data.get(key)
        if val:
            dt = _parse_exif_string(str(val))
            if dt:
                return dt, f"exiftool:{key}"
    return None, ""


# --- Kaynak 5: macOS yerlesik araclari --------------------------------------

def from_macos(path: Path) -> tuple[datetime | None, str]:
    """
    pillow-heif ve exiftool yoksa macOS'un kendi araclari HEIC okuyabilir.
      sips -g creation -> goruntunun kendi metadata'si
      mdls             -> Spotlight indeksi (EXIF'ten turetilir, UTC doner)
    """
    if not IS_MACOS:
        return None, ""

    out = _run(["sips", "-g", "creation", str(path)], timeout=15)
    if out:
        m = re.search(r"creation:\s*(\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2})", out)
        if m:
            dt = _parse_exif_string(m.group(1))
            if dt:
                return dt, "sips:creation"

    out = _run(["mdls", "-raw", "-name", "kMDItemContentCreationDate", str(path)],
               timeout=15)
    if out:
        raw = out.strip()
        if raw and raw != "(null)":
            try:
                dt = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
                if "+0000" in raw:  # UTC -> yerel saat
                    dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
                return dt, "mdls:ContentCreationDate"
            except ValueError:
                pass
    return None, ""


# --- Kaynak 6: sidecar ------------------------------------------------------

def from_sidecar(path: Path) -> tuple[datetime | None, str]:
    candidates = [
        path.with_suffix(path.suffix + ".json"),
        path.with_suffix(path.suffix + ".supplemental-metadata.json"),
        path.with_suffix(".json"),
        path.with_suffix(path.suffix + ".xmp"),
        path.with_suffix(".xmp"),
    ]
    for c in candidates:
        if not c.is_file() or c.is_symlink():
            continue
        try:
            text = c.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if c.suffix == ".json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            for key in ("photoTakenTime", "creationTime"):
                ts = (data.get(key) or {}).get("timestamp")
                if ts:
                    try:
                        return datetime.fromtimestamp(int(ts)), f"sidecar:{key}"
                    except (ValueError, OSError):
                        pass
        else:
            for key in ("photoshop:DateCreated", "xmp:CreateDate",
                        "exif:DateTimeOriginal"):
                m = re.search(rf'{key}\s*=\s*"([^"]+)"', text) or \
                    re.search(rf'<{key}>([^<]+)</{key}>', text)
                if m:
                    dt = _parse_iso(m.group(1))
                    if dt:
                        return dt, f"sidecar:{c.suffix}"
    return None, ""


# --- Kaynak 7: dosya adi ----------------------------------------------------

def from_filename(path: Path) -> tuple[datetime | None, str]:
    for rx, kind in NAME_PATTERNS:
        m = rx.search(path.stem)
        if not m:
            continue
        g = [x for x in m.groups() if x]
        try:
            if kind == "wa":
                dt = datetime.strptime("".join(g[:3]), "%Y%m%d")
            else:
                dt = datetime.strptime("".join(g[:5]), "%Y%m%d%H%M")
        except ValueError:
            continue
        return dt, "dosya-adi" + (" (saat yok -> 00:00)" if kind == "wa" else "")
    return None, ""


# --- Kaynak 8a: QuickTime/MP4 atom parser (bagimliliksiz) -------------------

MAC_EPOCH = 2082844800          # 1904-01-01 -> 1970-01-01 fark (saniye)
MIN_YEAR, MAX_YEAR = 1990, 2100  # makul cekim tarihi araligi
MAX_ATOM_SCAN = 64 * 1024 * 1024  # metadata atomu icin ust sinir


def _sane(dt: datetime | None) -> datetime | None:
    """Bozuk/sifir zaman damgalarini eler (ornegin 1904-01-01)."""
    if dt and MIN_YEAR <= dt.year <= MAX_YEAR:
        return dt
    return None


def _atoms(f, start: int, end: int):
    """
    Bir atom kapsayicisini gezer. Girdi guvenilmez bir dosya oldugu icin
    her boyut siniri dogrulanir; hatali boyut taramayi durdurur.
    """
    f.seek(start)
    while start + 8 <= end:
        hdr = f.read(8)
        if len(hdr) < 8:
            return
        size = int.from_bytes(hdr[:4], "big")
        typ = hdr[4:8]
        body = start + 8
        if size == 1:                      # 64-bit genisletilmis boyut
            ext = f.read(8)
            if len(ext) < 8:
                return
            size = int.from_bytes(ext, "big")
            body = start + 16
        elif size == 0:                    # dosya sonuna kadar
            size = end - start
        if size < 8 or start + size > end:
            return
        yield typ, body, start + size
        start += size
        f.seek(start)


def _find(f, start: int, end: int, want: bytes, depth: int = 0):
    """Belirli bir atomu bir seviye icinde arar."""
    if depth > 6:
        return None
    for typ, body, stop in _atoms(f, start, end):
        if typ == want:
            return body, stop
    return None


def _apple_creationdate(f, mbody: int, mstop: int) -> str | None:
    """
    moov/meta icindeki keys+ilst ciftinden
    com.apple.quicktime.creationdate degerini cikarir.
    Bu deger yerel saat + UTC offset tasir; mvhd'den daha dogrudur.
    """
    # 'meta' bazi dosyalarda 4 baytlik version/flags ile baslar, bazilarinda degil
    f.seek(mbody)
    probe = f.read(8)
    if len(probe) >= 8 and probe[4:8] not in (b"hdlr", b"keys", b"ilst"):
        mbody += 4

    keys = _find(f, mbody, mstop, b"keys")
    ilst = _find(f, mbody, mstop, b"ilst")
    if not keys or not ilst:
        return None

    # keys: version/flags(4) + entry_count(4) + [size(4) + namespace(4) + ad]
    kbody, kstop = keys
    f.seek(kbody)
    head = f.read(8)
    if len(head) < 8:
        return None
    count = int.from_bytes(head[4:8], "big")
    if not 0 < count <= 1024:
        return None
    names: list[bytes] = []
    pos = kbody + 8
    for _ in range(count):
        f.seek(pos)
        h = f.read(8)
        if len(h) < 8:
            break
        ksize = int.from_bytes(h[:4], "big")
        if ksize < 8 or pos + ksize > kstop:
            break
        names.append(f.read(min(ksize - 8, 256)))
        pos += ksize

    try:
        idx = names.index(b"com.apple.quicktime.creationdate") + 1
    except ValueError:
        return None

    # ilst: her kayit size(4) + index(4) + data atomu
    ibody, istop = ilst
    for typ, body, stop in _atoms(f, ibody, istop):
        if int.from_bytes(typ, "big") != idx:
            continue
        data = _find(f, body, stop, b"data")
        if not data:
            return None
        dbody, dstop = data
        f.seek(dbody + 8)                 # type(4) + locale(4) atla
        payload = f.read(min(dstop - dbody - 8, 64))
        return payload.decode("ascii", "ignore").strip() or None
    return None


def from_quicktime(path: Path) -> tuple[datetime | None, str]:
    """
    MOV/MP4 kapsayicisini dogrudan okur — ffprobe veya exiftool gerekmez.
      1. moov/meta -> com.apple.quicktime.creationdate  (yerel saat + offset)
      2. moov/mvhd -> creation_time                     (UTC, 1904 epoch)
    """
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            moov = None
            for typ, body, stop in _atoms(f, 0, size):
                if typ == b"moov":
                    moov = (body, stop)
                    break
            if not moov:
                return None, ""
            mb, ms = moov
            if ms - mb > MAX_ATOM_SCAN:
                ms = mb + MAX_ATOM_SCAN

            # 1) Apple yerel-saat etiketi (moov/meta veya moov/udta/meta)
            for container in (_find(f, mb, ms, b"meta"),
                              (lambda u: _find(f, u[0], u[1], b"meta") if u else None)
                              (_find(f, mb, ms, b"udta"))):
                if not container:
                    continue
                raw = _apple_creationdate(f, container[0], container[1])
                if raw:
                    # Offset'i CEVIRMEDEN birak: bu deger cekim yerindeki duvar
                    # saatidir ve EXIF ile ayni anlamda olmalidir. Ceviri
                    # yapilsaydi ayni anda cekilen foto ve video farkli
                    # isimlenirdi.
                    dt = _parse_wallclock(raw) or _parse_exif_string(raw)
                    if _sane(dt):
                        return dt, "quicktime:creationdate"

            # 2) mvhd — UTC, yerel saate cevrilir
            mvhd = _find(f, mb, ms, b"mvhd")
            if mvhd:
                f.seek(mvhd[0])
                head = f.read(4)
                if len(head) == 4:
                    ver = head[0]
                    raw = f.read(8 if ver == 1 else 4)
                    if len(raw) == (8 if ver == 1 else 4):
                        secs = int.from_bytes(raw, "big") - MAC_EPOCH
                        try:
                            dt = (datetime.fromtimestamp(secs, timezone.utc)
                                  .astimezone().replace(tzinfo=None))
                        except (OverflowError, OSError, ValueError):
                            return None, ""
                        if _sane(dt):
                            return dt, "quicktime:mvhd"
    except (OSError, ValueError):
        return None, ""
    return None, ""


# --- Kaynak 8b: video (dis araclar) ----------------------------------------

def from_ffprobe(path: Path) -> tuple[datetime | None, str]:
    out = _run(["ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_entries", "format_tags=creation_time", str(path)])
    if not out:
        return None, ""
    try:
        raw = (json.loads(out).get("format", {})
               .get("tags", {}).get("creation_time"))
    except json.JSONDecodeError:
        return None, ""
    if not raw:
        return None, ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, ""
    if dt.tzinfo:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt, "ffprobe:creation_time"


# --- Kaynak 9: dosya sistemi ------------------------------------------------

def from_fs(path: Path, mode: str) -> tuple[datetime, str]:
    """
    Finder karsiliklari:  created -> st_birthtime,  modified -> st_mtime
    Bu degerler cekim ani degil, dosyanin diske yazildigi andir.
    """
    st = path.stat()
    birth = getattr(st, "st_birthtime", None)
    mtime = st.st_mtime
    if mode == "created":
        ts, label = (birth, "st_birthtime/Created") if birth else \
                    (mtime, "st_mtime (birthtime yok)")
    elif mode == "modified":
        ts, label = mtime, "st_mtime/Modified"
    else:
        ts = min(birth, mtime) if birth else mtime
        label = "fs-earliest"
    return datetime.fromtimestamp(ts), f"fs:{label}"


# --- Zincir -----------------------------------------------------------------

def resolve_metadata(path: Path) -> tuple[datetime | None, str]:
    """Dosya ici / dis arac kaynaklari. fs-time haric."""
    ext = path.suffix.lower()

    if ext in IMAGE_EXT:
        # HEIC + pillow-heif yoksa Pillow'u denemeye gerek yok
        if not (ext in HEIC_EXT and not HEIF_OK):
            dt, src = from_pillow(path)
            if dt:
                return dt, src
        for fn in (from_exiftool, from_macos):
            dt, src = fn(path)
            if dt:
                return dt, src
    elif ext in VIDEO_EXT:
        # from_quicktime once: bagimliliksiz calisir ve Apple etiketi
        # yerel saati tasidigi icin ffprobe'un UTC degerinden daha dogrudur
        for fn in (from_quicktime, from_ffprobe, from_exiftool, from_macos):
            dt, src = fn(path)
            if dt:
                return dt, src
    else:
        return None, "desteklenmeyen-uzanti"

    dt, src = from_sidecar(path)
    if dt:
        return dt, src
    return None, ""


def resolve_datetime(path: Path, args) -> tuple[datetime | None, str]:
    dt, src = resolve_metadata(path)
    if dt:
        return dt, src
    if not args.no_filename:
        dt, src = from_filename(path)
        if dt:
            return dt, src
    if args.fs_time:
        return from_fs(path, args.fs_time)
    return None, "metadata-yok"


# --- Yeniden adlandirma -----------------------------------------------------

def reserve_target(directory: Path, stem: str, ext: str) -> Path | None:
    """Hedefi O_EXCL ile atomik rezerve eder; hicbir dosyanin uzerine yazmaz."""
    for i in range(1, 1000):
        candidate = directory / (stem + ("" if i == 1 else f"_{i}") + ext)
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            return candidate
        except FileExistsError:
            continue
    return None


def collect_files(root: Path, recursive: bool) -> list[Path]:
    it = root.rglob("*") if recursive else root.glob("*")
    out = []
    for p in it:
        if p.is_symlink() or not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix.lower() in IMAGE_EXT | VIDEO_EXT:
            out.append(p)
    return sorted(out)


def banner() -> None:
    bits = [f"pillow-heif: {'VAR' if HEIF_OK else 'YOK'}",
            f"exiftool: {'VAR' if HAS_EXIFTOOL else 'YOK'}",
            f"sips/mdls: {'VAR' if IS_MACOS else 'YOK'}"]
    print("HEIC okuyucular -> " + " | ".join(bits))
    if not (HEIF_OK or HAS_EXIFTOOL or IS_MACOS):
        print("UYARI: HEIC dosyalari okunamaz. "
              "python3 -m pip install --user pillow-heif\n")
    else:
        print()


def show_times(root: Path, recursive: bool) -> int:
    files = collect_files(root, recursive)
    if not files:
        print("Dosya bulunamadi.")
        return 0

    def fmt(dt):
        return dt.strftime("%Y-%m-%d %H:%M") if dt else "-"

    banner()
    hdr = f"{'DOSYA':<30}{'METADATA':<18}{'CREATED':<18}{'MODIFIED':<18}KAYNAK"
    print(hdr)
    print("-" * (len(hdr) + 12))
    for f in files:
        meta, src = resolve_metadata(f)
        if not meta:
            meta, src = from_filename(f)
        st = f.stat()
        birth = getattr(st, "st_birthtime", None)
        name = f.name if len(f.name) <= 29 else f.name[:26] + "..."
        print(f"{name:<30}{fmt(meta):<18}"
              f"{fmt(datetime.fromtimestamp(birth)) if birth else '-':<18}"
              f"{fmt(datetime.fromtimestamp(st.st_mtime)):<18}"
              f"{src or 'metadata-yok'}")
    print("\nMETADATA sutunu dolu olanlar gercek cekim zamanini kullanir.")
    print("Bos olanlar icin --fs-time devreye girer (Created, yoksa Modified).")
    return 0


def run(args) -> int:
    root = Path(args.directory).resolve()
    if not root.is_dir():
        sys.exit(f"HATA: dizin bulunamadi: {root}")

    unresolved_dir = Path(args.unresolved_dir).resolve() if args.unresolved_dir else None
    files = collect_files(root, args.recursive)
    if not files:
        print("Islenecek dosya bulunamadi.")
        return 0

    banner()
    journal, renamed, skipped, failed, moved = [], 0, 0, 0, 0
    stats: dict[str, int] = {}

    for src in files:
        ext = src.suffix.lower() if args.lower_ext else src.suffix

        if ALREADY_NAMED.match(src.stem):
            skipped += 1
            if args.verbose:
                print(f"[ATLA ] {src.name}  (zaten formatli)")
            continue

        dt, source = resolve_datetime(src, args)
        stats[source] = stats.get(source, 0) + 1

        if dt is None:
            failed += 1
            print(f"[HATA ] {src.relative_to(root)}  ->  zaman yok ({source})")
            if unresolved_dir and args.apply:
                unresolved_dir.mkdir(parents=True, exist_ok=True)
                dst = reserve_target(unresolved_dir, src.stem, ext)
                if dst:
                    shutil.move(str(src), str(dst))
                    journal.append({"from": str(src), "to": str(dst),
                                    "source": "unresolved"})
                    moved += 1
            continue

        stem = dt.strftime(OUTPUT_FMT)

        if not args.apply:
            print(f"[KURU ] {src.relative_to(root)}  ->  {stem}{ext}   [{source}]")
            renamed += 1
            continue

        dst = reserve_target(src.parent, stem, ext)
        if dst is None:
            failed += 1
            print(f"[HATA ] {src.name}  ->  cakisma limiti asildi")
            continue
        try:
            os.replace(src, dst)
        except OSError as exc:
            failed += 1
            try:
                os.unlink(dst)
            except OSError:
                pass
            print(f"[HATA ] {src.name}  ->  {exc}")
            continue

        journal.append({"from": str(src), "to": str(dst), "source": source})
        renamed += 1
        print(f"[TAMAM] {src.relative_to(root)}  ->  {dst.name}   [{source}]")

    if args.apply and journal:
        jpath = Path(args.journal) if args.journal else \
            root / f".rename-journal-{datetime.now():%Y%m%d-%H%M%S}.json"
        jpath.write_text(json.dumps(journal, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        print(f"\nGeri alma kaydi: {jpath}")
        print(f'Geri almak icin : python3 "{Path(sys.argv[0]).resolve()}" '
              f'--rollback "{jpath}"')

    print("\nZaman kaynagi dagilimi:")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {v:5d}  {k}")

    mode = "UYGULANDI" if args.apply else "KURU CALISMA (degisiklik yok)"
    print(f"\n{mode} | islenen: {renamed}  atlanan: {skipped}  "
          f"hata: {failed}  karantina: {moved}")
    return 1 if failed and not unresolved_dir else 0


def rollback(journal_path: str) -> int:
    entries = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    ok = err = 0
    for e in reversed(entries):
        src, dst = Path(e["to"]), Path(e["from"])
        if not src.exists():
            print(f"[ATLA ] {src.name} bulunamadi")
            continue
        if dst.exists():
            print(f"[HATA ] hedef zaten var: {dst.name}")
            err += 1
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            ok += 1
            print(f"[GERI ] {src.name}  ->  {dst.name}")
        except OSError as exc:
            err += 1
            print(f"[HATA ] {src.name}: {exc}")
    print(f"\nGeri alinan: {ok}  hata: {err}")
    return 1 if err else 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Fotograflari cekim zamanina gore yeniden adlandirir "
                    "(YYYY-AA-GG-SSDD).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Ornek:\n"
               "  python3 photo_rename.py ~/Fotograflar -r --show-times\n"
               "  python3 photo_rename.py ~/Fotograflar -r\n"
               "  python3 photo_rename.py ~/Fotograflar -r --apply --fs-time\n"
               "  python3 photo_rename.py --rollback kayit.json\n")
    p.add_argument("directory", nargs="?")
    p.add_argument("-r", "--recursive", action="store_true",
                   help="Alt dizinleri de tara")
    p.add_argument("--apply", action="store_true",
                   help="Gercekten yeniden adlandir (varsayilan: kuru calisma)")
    p.add_argument("--fs-time", choices=["created", "modified", "earliest"],
                   nargs="?", const="created", metavar="MOD",
                   help="Metadata yoksa dosya sistemi zamanini kullan. "
                        "created=Finder 'Created' (varsayilan), "
                        "modified=Finder 'Modified', earliest=ikisinin kucugu")
    p.add_argument("--allow-mtime", dest="fs_time", action="store_const",
                   const="modified", help="--fs-time modified ile ayni (eski ad)")
    p.add_argument("--no-filename", action="store_true",
                   help="Dosya adindan tarih cikarmayi devre disi birak")
    p.add_argument("--unresolved-dir", metavar="DIR",
                   help="Zamani cozulemeyen dosyalari bu dizine tasi")
    p.add_argument("--lower-ext", action="store_true",
                   help="Uzantiyi kucuk harfe cevir (.HEIC -> .heic)")
    p.add_argument("--journal", help="Geri alma kaydinin yazilacagi dosya")
    p.add_argument("--rollback", metavar="JSON", help="Islemleri geri al")
    p.add_argument("--show-times", action="store_true",
                   help="Hicbir sey degistirmeden tum zaman kaynaklarini listele")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    if args.rollback:
        return rollback(args.rollback)
    if not args.directory:
        p.error("dizin belirtilmedi (veya --rollback kullanin)")
    if args.show_times:
        return show_times(Path(args.directory).resolve(), args.recursive)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
