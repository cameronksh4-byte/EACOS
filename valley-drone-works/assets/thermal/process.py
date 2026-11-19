#!/usr/bin/env python3
"""Import supplied Valley Drone Works images, make web copies, and check the rules.

    python3 process.py import <supplied-folder>   store originals untouched + build web copies
    python3 process.py build                      rebuild web copies from originals/
    python3 process.py check                      check files and MANIFEST.md

Rules (see README.md): originals are copied byte-for-byte and never changed;
web copies are JPEG, max 1920 px wide, under 400 KB, scaled but never cropped,
never enlarged, with EXIF/GPS metadata removed. The pair files come out the
same size. Needs Pillow. Paths are relative to this file, so the folder can be
moved as one unit.
"""
import hashlib
import re
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
ORIGINALS = HERE / "originals"
MANIFEST = HERE / "MANIFEST.md"

# slot -> supplied file name (also the web copy's name)
SLOTS = {
    "hero-roof": "hero-roof.jpg",
    "roof-detail": "roof-detail.jpg",
    "pair-visible": "pair-visible.jpg",
    "pair-thermal": "pair-thermal.jpg",
    "solar-array": "solar-array.jpg",
    "texture-band": "texture-band.jpg",
    "packet-cover": "packet-cover.jpg",
}
PAIR = ("pair-visible", "pair-thermal")
EXTRA_ORIGINALS = ["pair-original.jpg"]  # import keeps it untouched if supplied
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}

MAX_WIDTH = 1920
MAX_BYTES = 400 * 1000  # "under 400KB"; decimal KB is the stricter reading
ASPECT_TOLERANCE = 0.005

SOURCE_TYPES = {"DESIGN ARTWORK", "TRAINING DATA", "CLIENT JOB"}
CAPTIONS = {
    "DESIGN ARTWORK": "Illustrative artwork — not a thermal scan of a real property.",
    "TRAINING DATA": "Training — sample data, not a client site.",
}
REPORT_SLOTS = {"packet-cover"}  # inside the Sample Packet, which counts as a report


def die(msg):
    sys.exit(f"error: {msg}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load(src):
    img = Image.open(src)
    if img.mode in ("I", "I;16", "I;16B", "I;16L", "F"):
        die(f"{src.name} is raw {img.mode} data; making it viewable would recolour it. "
            "Supply a rendered RGB export instead.")
    img = ImageOps.exif_transpose(img)  # apply camera rotation; no crop
    icc = img.info.get("icc_profile")
    return (img if img.mode == "RGB" else img.convert("RGB")), icc


def write_web(src, dst, width, size=None):
    """Scale src to `width` (never enlarge) and save under MAX_BYTES with no EXIF.
    `size` forces exact dimensions (used to match the pair). Returns width used."""
    img, icc = load(src)
    width = min(width, img.width, MAX_WIDTH)
    while True:
        target = size or (width, round(img.height * width / img.width))
        scaled = img if target == img.size else img.resize(target, Image.LANCZOS)
        tmp = dst.with_suffix(".tmp.jpg")
        for quality in range(90, 49, -5):
            scaled.save(tmp, "JPEG", quality=quality, optimize=True,
                        progressive=True, icc_profile=icc)
            if tmp.stat().st_size < MAX_BYTES:
                tmp.replace(dst)
                return target[0]
        tmp.unlink()
        if size:
            size = None
        width = int(width * 0.9)  # shrink the whole frame; never crop


def original_of(slot, rows=None):
    """The original a slot's web copy is made from: the first `originals/...`
    path in that slot's Original column in MANIFEST.md (the manifest is the
    source of truth). None if the row names no original or the file is missing."""
    r = (rows or manifest_rows()).get(slot)
    m = re.search(r"`(originals/[^`]+)`", r["original"]) if r else None
    p = HERE / m.group(1) if m else None
    return p if p and p.is_file() else None


def build():
    rows = manifest_rows()
    for slot, name in SLOTS.items():
        src = original_of(slot, rows)
        if slot in PAIR:
            continue
        if not src:
            print(f"{slot}: no original yet (no web copy made)")
            continue
        w = write_web(src, HERE / name, MAX_WIDTH)
        print(f"{slot}: {src.name} -> {name} ({w}px wide, {(HERE / name).stat().st_size} bytes)")
    a, b = (original_of(s, rows) for s in PAIR)
    if not (a and b):
        print("pair: both originals needed before the matched pair is made "
              f"(have: {', '.join(s for s, p in zip(PAIR, (a, b)) if p) or 'neither'})")
    else:
        (ia, _), (ib, _) = load(a), load(b)
        ra, rb = ia.width / ia.height, ib.width / ib.height
        if abs(ra - rb) / ra > ASPECT_TOLERANCE:
            die(f"pair panels differ in shape ({ia.size} vs {ib.size}); they can't be "
                "matched without cropping, so no pair web copies were made.")
        width = min(ia.width, ib.width, MAX_WIDTH)
        while True:
            size = (width, round(min(ia.height / ia.width, ib.height / ib.width) * width))
            wa = write_web(a, HERE / SLOTS[PAIR[0]], width, size)
            wb = write_web(b, HERE / SLOTS[PAIR[1]], width, size)
            if wa == wb == width:
                break
            width = min(wa, wb)
        print(f"pair: {a.name} + {b.name} ({size[0]}x{size[1]} each)")


def store(src, name):
    dst = ORIGINALS / name
    if dst.exists():
        if sha256(dst) != sha256(src):
            die(f"originals/{name} already exists and differs from {src}. "
                "Originals are never overwritten.")
        return "already stored"
    shutil.copy2(src, dst)
    if sha256(src) != sha256(dst):
        dst.unlink()
        die(f"copy of {src} does not match the supplied file")
    return "stored"


def cmd_import(folder):
    folder = Path(folder).resolve()
    if not folder.is_dir():
        die(f"no such folder: {folder}")
    if folder == HERE:
        die("import from the supplied folder, not from this folder itself")
    ORIGINALS.mkdir(exist_ok=True)
    expected = list(SLOTS.values()) + EXTRA_ORIGINALS
    found = {}
    for name in expected:
        for cand in (folder / name, folder / "originals" / name):
            if cand.is_file():
                found[name] = cand
                break
    for name in expected:
        if name in found:
            load(found[name])  # refuse unreadable/raw files before storing
    for name, src in found.items():
        print(f"{name}: {store(src, name)} in originals/")
    for name in expected:
        if name not in found:
            print(f"not found in supplied folder: {name}")
    used = set(found.values())
    for p in sorted(folder.rglob("*")):
        if p.is_file() and p.suffix.lower() in IMAGE_EXT and p not in used:
            print(f"ignored (not in manifest): {p.relative_to(folder)}")
    build()
    print("Next: fill in alt text in MANIFEST.md, then run: process.py check")


KEYS = ["slot", "file", "original", "source", "caption", "alt", "pages",
        "consent", "status"]


def manifest_table():
    """Every data row of the image table, in order (duplicates kept)."""
    out = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if (len(cells) == len(KEYS) and cells[0] not in ("Slot", "")
                and not set(cells[0]) <= set("-: ")):
            out.append(dict(zip(KEYS, cells)))
    return out


def manifest_rows():
    return {r["slot"]: r for r in manifest_table() if r["slot"] in SLOTS}


def aspect_matches(a, b, tol=ASPECT_TOLERANCE):
    ra, rb = a[0] / a[1], b[0] / b[1]
    return abs(ra - rb) / ra <= tol


def blank(v):
    return v.strip("`") in ("", "—", "-", "PENDING", "N/A")


def cmd_check():
    errors, warnings, notes = [], [], []
    table = manifest_table()
    rows = manifest_rows()
    for slot in SLOTS:
        if slot not in rows:
            errors.append(f"{slot}: no row in MANIFEST.md")
    # Every image file outside originals/ has exactly one manifest row.
    named = [r["file"].strip("`") for r in table]
    for r in table:
        if r["slot"] not in SLOTS:
            errors.append(f"manifest row for unknown slot '{r['slot']}'")
    for name in sorted(set(n for n in named if named.count(n) > 1)):
        errors.append(f"{name}: appears in {named.count(name)} manifest rows (must be exactly one)")
    for p in sorted(HERE.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() in IMAGE_EXT:
            if named.count(p.name) != 1:
                errors.append(f"{p.name}: has {named.count(p.name)} manifest rows (must be exactly one)")
        else:
            notes.append(f"{p.name}: not an image, so needs no manifest row")
    # Every row names a source type and a caption.
    for r in table:
        for key, label in (("source", "source type"), ("caption", "caption")):
            if blank(r[key]):
                errors.append(f"{r['slot']}: row does not name a {label}")
    # No image is cropped compared with its original (shape must match).
    for slot, name in SLOTS.items():
        web, orig = HERE / name, original_of(slot, rows)
        if web.exists() and orig:
            ws, os_ = Image.open(web).size, ImageOps.exif_transpose(Image.open(orig)).size
            if not aspect_matches(ws, os_):
                errors.append(f"{slot}: web copy {ws} has a different shape from original "
                              f"{orig.name} {os_} — cropped or distorted")
            else:
                notes.append(f"{slot}: web copy {ws[0]}x{ws[1]} has the same shape as "
                             f"{orig.name} {os_[0]}x{os_[1]} — not cropped")
    for slot, r in rows.items():
        name = SLOTS[slot]
        source = r["source"].strip("`")
        caption = r["caption"].strip("`")
        if r["file"].strip("`") != name:
            errors.append(f"{slot}: file name should be {name}")
        if not blank(source) and source not in SOURCE_TYPES:
            errors.append(f"{slot}: unknown source type '{source}'")
        if source in CAPTIONS and caption != CAPTIONS[source]:
            errors.append(f"{slot}: {source} caption must be exactly '{CAPTIONS[source]}'")
        if source == "CLIENT JOB" and blank(r["consent"]):
            errors.append(f"{slot}: CLIENT JOB with no consent reference — not allowed")
        if source == "DESIGN ARTWORK" and slot in REPORT_SLOTS:
            errors.append(f"{slot}: inside the Sample Packet (a report) — cannot be DESIGN ARTWORK")
        if source == "DESIGN ARTWORK" and "packet" in r["pages"].lower():
            warnings.append(f"{slot}: DESIGN ARTWORK listed for a Sample Packet page — "
                            "allowed only if that page is outside the packet itself")
        web, orig = HERE / name, original_of(slot, rows)
        if web.exists():
            w, _ = Image.open(web).size
            if w > MAX_WIDTH:
                errors.append(f"{slot}: web copy is {w}px wide (max {MAX_WIDTH})")
            if web.stat().st_size >= MAX_BYTES:
                errors.append(f"{slot}: web copy is {web.stat().st_size} bytes (max {MAX_BYTES - 1})")
            if not orig:
                errors.append(f"{slot}: web copy has no original named in MANIFEST.md / originals/")
        pending = [k for k in ("source", "caption", "alt", "pages")
                   if r[k] == "PENDING" or r[k].startswith("DRAFT")]
        if r["status"] == "Ready":
            if pending:
                errors.append(f"{slot}: marked Ready but PENDING: {', '.join(pending)}")
            if not web.exists():
                errors.append(f"{slot}: marked Ready but {name} does not exist")
        else:
            why = [f"PENDING: {', '.join(pending)}"] if pending else []
            if not orig:
                why.append("original not supplied yet")
            notes.append(f"{slot}: not usable yet ({'; '.join(why) or r['status']})")
    if all(s in rows for s in PAIR):
        a, b = (rows[s] for s in PAIR)
        if (a["source"], a["consent"]) != (b["source"], b["consent"]):
            errors.append("pair: both halves must have the same source type and consent ref")
        if all((HERE / SLOTS[s]).exists() for s in PAIR):
            sa, sb = (Image.open(HERE / SLOTS[s]).size for s in PAIR)
            if sa != sb:
                errors.append(f"pair: web copies differ in size ({sa} vs {sb})")
    for n in notes:
        print("note:   ", n)
    for w in warnings:
        print("WARNING:", w)
    for e in errors:
        print("ERROR:  ", e)
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["import"] and len(args) == 2:
        cmd_import(args[1])
    elif args == ["build"]:
        build()
    elif args == ["check"]:
        sys.exit(cmd_check())
    else:
        sys.exit(__doc__)
