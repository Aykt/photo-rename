"""
photo_rename test paketi.

Fixture'lar calisma aninda uretilir — repoda ikili dosya tutulmaz,
boylece CI her platformda ek varlik indirmeden calisir.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import photo_rename as pr  # noqa: E402


# --------------------------------------------------------------------------
# Fixture ureticileri
# --------------------------------------------------------------------------

def make_jpeg(path: Path, exif_dt: str | None = None, xmp: bytes | None = None):
    im = Image.new("RGB", (16, 16), (40, 70, 100))
    kwargs = {}
    if exif_dt:
        ex = im.getexif()
        ex.get_ifd(0x8769)[pr.TAG_DT_ORIGINAL] = exif_dt
        kwargs["exif"] = ex
    if xmp:
        kwargs["xmp"] = xmp
    im.save(path, **kwargs)
    return path


def _atom(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + typ + payload


def make_mov(path: Path, mvhd_dt: datetime | None = None,
             apple_date: str | None = None) -> Path:
    """Yalnizca moov/mvhd (+ istege bagli Apple ilst) iceren minimal MOV."""
    secs = 0
    if mvhd_dt:
        secs = int(mvhd_dt.timestamp()) + pr.MAC_EPOCH
    mvhd = _atom(b"mvhd", bytes([0, 0, 0, 0])          # version + flags
                 + struct.pack(">I", secs)              # creation_time
                 + struct.pack(">I", secs)              # modification_time
                 + struct.pack(">I", 1000)              # timescale
                 + struct.pack(">I", 0)                 # duration
                 + b"\x00" * 80)

    inner = mvhd
    if apple_date:
        keys = _atom(b"keys", struct.pack(">I", 0) + struct.pack(">I", 1)
                     + _atom(b"mdta", b"com.apple.quicktime.creationdate"))
        ilst = _atom(b"ilst", _atom(
            struct.pack(">I", 1),
            _atom(b"data", struct.pack(">I", 1) + struct.pack(">I", 0)
                  + apple_date.encode())))
        hdlr = _atom(b"hdlr", b"\x00" * 8 + b"mdta" + b"\x00" * 12)
        inner += _atom(b"meta", hdlr + keys + ilst)

    data = _atom(b"ftyp", b"qt  " + b"\x00" * 8) + _atom(b"moov", inner)
    path.write_bytes(data)
    return path


def args_ns(**over):
    base = dict(fs_time=None, no_filename=False, apply=False, recursive=False,
                lower_ext=False, verbose=False, unresolved_dir=None,
                journal=None, directory=None, rollback=None, show_times=False)
    base.update(over)
    return argparse.Namespace(**base)


# --------------------------------------------------------------------------
# Goruntu metadata'si
# --------------------------------------------------------------------------

def test_exif_datetime_original(tmp_path):
    f = make_jpeg(tmp_path / "a.jpg", exif_dt="2026:08:02 23:44:12")
    dt, src = pr.resolve_metadata(f)
    assert dt == datetime(2026, 8, 2, 23, 44, 12)
    assert src == "EXIF:DateTimeOriginal"


def test_xmp_fallback_when_no_exif(tmp_path):
    xmp = (b'<x:xmpmeta><rdf:RDF><rdf:Description '
           b'photoshop:DateCreated="2026-05-01T10:22:00"/></rdf:RDF></x:xmpmeta>')
    f = make_jpeg(tmp_path / "b.jpg", xmp=xmp)
    dt, src = pr.resolve_metadata(f)
    assert dt == datetime(2026, 5, 1, 10, 22)
    assert src.startswith("XMP:")


def test_no_metadata_returns_empty(tmp_path):
    f = make_jpeg(tmp_path / "c.jpg")
    dt, _ = pr.resolve_metadata(f)
    assert dt is None


# --------------------------------------------------------------------------
# QuickTime / MOV
# --------------------------------------------------------------------------

def test_mov_mvhd(tmp_path):
    f = make_mov(tmp_path / "v.MOV", mvhd_dt=datetime(2026, 3, 17, 8, 15, 33))
    dt, src = pr.from_quicktime(f)
    assert src == "quicktime:mvhd"
    assert dt is not None and dt.year == 2026


def test_mov_apple_tag_preferred_and_not_converted(tmp_path):
    """Apple etiketi duvar saatidir; offset cevrilmemeli."""
    f = make_mov(tmp_path / "v2.MOV",
                 mvhd_dt=datetime(2020, 1, 1),
                 apple_date="2026-07-04T21:07:45+0300")
    dt, src = pr.from_quicktime(f)
    assert src == "quicktime:creationdate"
    assert dt == datetime(2026, 7, 4, 21, 7, 45)


def test_mov_zero_timestamp_rejected(tmp_path):
    """Metadata'sti olmayan MOV 1904-01-01 verir; gecersiz sayilmali."""
    f = make_mov(tmp_path / "v3.MOV", mvhd_dt=None)
    dt, _ = pr.from_quicktime(f)
    assert dt is None


@pytest.mark.parametrize("payload", [b"", b"\x00" * 16, b"\xff" * 64])
def test_malformed_container_does_not_crash(tmp_path, payload):
    f = tmp_path / "bad.MOV"
    f.write_bytes(payload)
    assert pr.from_quicktime(f) == (None, "")


def test_truncated_container_does_not_crash(tmp_path):
    full = make_mov(tmp_path / "ok.MOV", mvhd_dt=datetime(2026, 1, 1))
    cut = tmp_path / "cut.MOV"
    cut.write_bytes(full.read_bytes()[:24])
    assert pr.from_quicktime(cut)[0] is None


# --------------------------------------------------------------------------
# Dosya adi kaliplari
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("IMG-20260629-WA0007", datetime(2026, 6, 29, 0, 0)),
    ("PXL_20260701_083015123", datetime(2026, 7, 1, 8, 30)),
    ("20260701_083015", datetime(2026, 7, 1, 8, 30)),
    ("Screenshot 2026-06-29 at 19.54.12", datetime(2026, 6, 29, 19, 54)),
])
def test_filename_patterns(name, expected):
    dt, _ = pr.from_filename(Path(f"{name}.jpg"))
    assert dt == expected


def test_filename_pattern_rejects_non_date():
    assert pr.from_filename(Path("IMG_1914.jpg"))[0] is None


# --------------------------------------------------------------------------
# Yeniden adlandirma guvenligi
# --------------------------------------------------------------------------

def test_reserve_target_never_overwrites(tmp_path):
    (tmp_path / "2026-08-02-2344.jpg").write_text("mevcut")
    dst = pr.reserve_target(tmp_path, "2026-08-02-2344", ".jpg")
    assert dst.name == "2026-08-02-2344_2.jpg"
    assert (tmp_path / "2026-08-02-2344.jpg").read_text() == "mevcut"


def test_symlinks_are_skipped(tmp_path):
    real = make_jpeg(tmp_path / "real.jpg", exif_dt="2026:01:01 00:00:00")
    link = tmp_path / "link.jpg"
    link.symlink_to(real)
    assert [p.name for p in pr.collect_files(tmp_path, False)] == ["real.jpg"]


def test_already_named_files_are_skipped():
    assert pr.ALREADY_NAMED.match("2026-08-02-2344")
    assert pr.ALREADY_NAMED.match("2026-08-02-2344_3")
    assert not pr.ALREADY_NAMED.match("IMG_1914")


def test_dry_run_changes_nothing(tmp_path):
    make_jpeg(tmp_path / "a.jpg", exif_dt="2026:08:02 23:44:12")
    pr.run(args_ns(directory=str(tmp_path), apply=False))
    assert (tmp_path / "a.jpg").exists()


def test_apply_and_rollback_roundtrip(tmp_path):
    make_jpeg(tmp_path / "a.jpg", exif_dt="2026:08:02 23:44:12")
    make_jpeg(tmp_path / "b.jpg", exif_dt="2026:08:02 23:44:55")  # ayni dakika
    pr.run(args_ns(directory=str(tmp_path), apply=True))

    names = sorted(p.name for p in tmp_path.glob("*.jpg"))
    assert names == ["2026-08-02-2344.jpg", "2026-08-02-2344_2.jpg"]

    journal = next(tmp_path.glob(".rename-journal-*.json"))
    assert pr.rollback(str(journal)) == 0
    assert sorted(p.name for p in tmp_path.glob("*.jpg")) == ["a.jpg", "b.jpg"]


def test_journal_records_source(tmp_path):
    make_jpeg(tmp_path / "a.jpg", exif_dt="2026:08:02 23:44:12")
    pr.run(args_ns(directory=str(tmp_path), apply=True))
    journal = json.loads(next(tmp_path.glob(".rename-journal-*.json")).read_text())
    assert journal[0]["source"] == "EXIF:DateTimeOriginal"


def test_unresolved_dir_quarantine(tmp_path):
    make_jpeg(tmp_path / "nometa.jpg")
    out = tmp_path / "_zamansiz"
    pr.run(args_ns(directory=str(tmp_path), apply=True, unresolved_dir=str(out)))
    assert (out / "nometa.jpg").exists()
    assert not (tmp_path / "nometa.jpg").exists()


# --------------------------------------------------------------------------
# Dosya sistemi fallback
# --------------------------------------------------------------------------

def test_fs_time_is_opt_in(tmp_path):
    f = make_jpeg(tmp_path / "x.jpg")
    assert pr.resolve_datetime(f, args_ns())[0] is None
    assert pr.resolve_datetime(f, args_ns(fs_time="modified"))[0] is not None


def test_fs_time_modified_matches_stat(tmp_path):
    f = make_jpeg(tmp_path / "y.jpg")
    dt, src = pr.from_fs(f, "modified")
    assert dt == datetime.fromtimestamp(f.stat().st_mtime)
    assert "Modified" in src


# --------------------------------------------------------------------------
# Zaman ayristirma
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["0000:00:00 00:00:00", "", "gecersiz", None])
def test_invalid_exif_strings(raw):
    assert pr._parse_exif_string(raw) is None


def test_wallclock_drops_offset():
    assert pr._parse_wallclock("2026-07-04T21:07:45+0300") == \
        datetime(2026, 7, 4, 21, 7, 45)


@pytest.mark.parametrize("year,ok", [(1904, False), (1989, False),
                                     (2026, True), (2200, False)])
def test_sanity_window(year, ok):
    assert (pr._sane(datetime(year, 1, 1)) is not None) is ok
