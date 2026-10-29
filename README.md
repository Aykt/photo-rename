# photo-rename

Rename photos and videos to their capture time: `IMG_1914.HEIC` → `2026-08-02-2344.heic`

Reads the real capture time from EXIF, XMP, IPTC, QuickTime atoms, sidecar files
and filename patterns — in that order of reliability — and tells you which source
each file used.

```
$ photo-rename ~/Photos -r --show-times

DOSYA                         METADATA          CREATED           MODIFIED          KAYNAK
IMG_0001.JPG                  2026-08-02 23:44  2026-06-29 19:54  2026-06-29 19:54  EXIF:DateTimeOriginal
IMG_5351.HEIC                 2026-03-17 08:15  2026-06-29 19:54  2026-06-29 19:54  EXIF:DateTimeOriginal
IMG_6003.MOV                  2026-07-04 21:07  2026-06-29 19:54  2026-06-29 19:54  quicktime:creationdate
IMG-20260629-WA0007.jpg       2026-06-29 00:00  2026-06-29 19:54  2026-06-29 19:54  dosya-adi
IMG_1914.jpg                  -                 2026-06-29 19:54  2026-06-29 19:54  metadata-yok
```

Türkçe dokümantasyon: [README.tr.md](README.tr.md)

## Why

Most renamers read one tag and give up. Messaging apps strip EXIF for privacy,
Finder's "Created" date is the copy time rather than the capture time, and
HEIC/MOV need readers that Pillow does not ship with. This tool walks a full
fallback chain, refuses to invent a timestamp it could not find, and keeps an
undo journal for every run.

## Install

```bash
git clone https://github.com/<your-username>/photo-rename.git
cd photo-rename
pip install .
```

Or run it as a single file — the script has no required dependency beyond Pillow:

```bash
pip install Pillow
python3 photo_rename.py ~/Photos --show-times
```

### Optional readers

| Need | Install | Without it |
|---|---|---|
| HEIC / HEIF | `pip install pillow-heif` | falls back to `exiftool`, then macOS `sips`/`mdls` |
| RAW, any format | `brew install exiftool` | other readers still cover common cases |
| Video | nothing — MOV/MP4 parsed natively | `ffprobe`/`exiftool` used as backup if present |

The script prints which readers it found on startup.

## Usage

```bash
photo-rename ~/Photos -r --show-times                      # inspect, change nothing
photo-rename ~/Photos -r                                   # dry run
photo-rename ~/Photos -r --apply --lower-ext               # rename
photo-rename ~/Photos -r --apply --fs-time                 # allow filesystem fallback
photo-rename --rollback .rename-journal-20261029-134104.json
```

Nothing is renamed without `--apply`. Every `--apply` writes a JSON journal that
`--rollback` replays in reverse.

### Options

| Flag | Effect |
|---|---|
| `-r, --recursive` | descend into subdirectories |
| `--apply` | actually rename (default is a dry run) |
| `--show-times` | list every time source per file, change nothing |
| `--fs-time [MODE]` | last-resort filesystem time: `created` (default), `modified`, `earliest` |
| `--no-filename` | disable date extraction from filenames |
| `--unresolved-dir DIR` | move files with no resolvable time here instead of renaming |
| `--lower-ext` | normalise extension case (`.HEIC` → `.heic`) |
| `--journal PATH` | write the undo journal somewhere specific |
| `--rollback JSON` | undo a previous run |

## Time source priority

| # | Source | Notes |
|---|---|---|
| 1 | EXIF `DateTimeOriginal` / `DateTimeDigitized` / `DateTime` | shutter time, ground truth |
| 2 | XMP `photoshop:DateCreated`, `xmp:CreateDate` | survives some EXIF stripping |
| 3 | IPTC `DateCreated` + `TimeCreated` | older editing pipelines |
| 4 | `exiftool` | if installed; covers HEIC, RAW, video |
| 5 | `sips` / `mdls` | macOS only; `mdls` can hold EXIF-derived dates after stripping |
| 6 | QuickTime `com.apple.quicktime.creationdate`, then `moov/mvhd` | native parser, no dependency |
| 7 | Sidecar `.json` (Google Takeout `photoTakenTime`) or `.xmp` | |
| 8 | Filename pattern | `IMG-20260629-WA0001`, `PXL_20260701_0830…`, `Screenshot 2026-06-29 at 19.54` |
| 9 | Filesystem time | opt-in via `--fs-time` only |

### Timezone handling

EXIF carries wall-clock time with no timezone, so it is used verbatim. The Apple
QuickTime tag carries wall-clock plus a UTC offset — the offset is **dropped, not
converted**, so a photo and a video shot at the same moment get the same name.
`moov/mvhd` is genuinely UTC with no wall-clock information, so it is converted to
local time; the source label tells you which path a file took.

## Safety

- **Dry run by default.** Renaming is hard to reverse; `--apply` is required.
- **No overwrites.** Targets are reserved with `O_CREAT|O_EXCL` before `os.replace`,
  which closes the check-then-rename TOCTOU race. Same-minute collisions get `_2`, `_3`.
- **Undo journal.** Every run records `from`/`to`/`source` as JSON for `--rollback`.
- **Symlinks skipped.** Prevents following a link outside the target tree.
- **Filesystem time is opt-in.** `st_mtime` changes on copy and sync; it is a
  transfer timestamp, not a capture time, and never used unless you ask.
- **Bounded binary parsing.** The QuickTime reader validates every atom size against
  its container, caps recursion depth, bounds key counts and read lengths, limits
  metadata scanning to 64 MB, and rejects timestamps outside 1990–2100. Malformed,
  truncated and zero-byte containers are covered by tests.

## Limitations

- Minute precision. Burst shots collide and are disambiguated with `_2`, `_3`, which
  does not preserve shot order. Change `OUTPUT_FMT` to `"%Y-%m-%d-%H%M%S"` for seconds.
- WhatsApp filenames carry a date but no time; those files get `00:00` and the source
  label says so.
- Stripped metadata cannot be recovered from the file. Re-export the original
  (Photos → Export Unmodified Original) or use the Google Takeout `.json` sidecars.

## Development

```bash
pip install -r requirements-dev.txt
pytest -q
```

Tests generate their own fixtures at runtime — no binary assets in the repo.
