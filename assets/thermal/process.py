#!/usr/bin/env python3
"""Make web copies of supplied thermal-site images and check them against the rules.

    python3 process.py add <slot> <supplied-file>   copy original + make web copy
    python3 process.py check                         check files and MANIFEST.md

Rules (see README.md): originals are copied byte-for-byte and never changed;
web copies are JPEG, max 1920 px wide, under 400 KB, scaled but never cropped,
never enlarged, with EXIF/GPS metadata removed. The vt-pair files come out the
same size. Needs Pillow.
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

SLOTS = ["hero-roof", "roof-detail", "vt-pair-visible", "vt-pair-thermal",
         "solar-array", "texture-band", "packet-cover"]
PAIR = ("vt-pair-visible", "vt-pair-thermal")
MAX_WIDTH = 1920
MAX_BYTES = 400 * 1000  # "under 400KB"; decimal KB is the stricter reading of 400 KB vs 400 KiB
SOURCE_TYPES = {"DESIGN ARTWORK", "TRAINING DATA", "CLIENT JOB"}
TRAINING_CAPTION = "Training - sample data, not a client site"
ASPECT_TOLERANCE = 0.005  # pair originals must match aspect ratio within 0.5%


def die(msg):
    sys.exit(f"error: {msg}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def web_path(slot):
    return HERE / f"{slot}.jpg"


def original_for(slot):
    found = sorted(ORIGINALS.glob(f"{slot}__*"))
    return found[0] if found else None


def load(src):
    img = Image.open(src)
    if img.mode in ("I", "I;16", "I;16B", "I;16L", "F"):
        # Raw radiometric / 16-bit data: turning it into a viewable image means
        # choosing a palette and a temperature span, which would recolour it.
        die(f"{src.name} is raw {img.mode} data. Supply a rendered export "
            "(JPEG/PNG/TIFF in RGB) of this image instead.")
    img = ImageOps.exif_transpose(img)  # apply camera rotation; no crop
    icc = img.info.get("icc_profile")
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img, icc


def write_web(src, dst, width):
    """Scale src to `width` (never larger than the original) and save under MAX_BYTES.
    Returns the width actually used (smaller than asked if the size cap forced it)."""
    img, icc = load(src)
    width = min(width, img.width, MAX_WIDTH)
    while True:
        height = round(img.height * width / img.width)
        scaled = img if width == img.width else img.resize((width, height), Image.LANCZOS)
        for quality in range(90, 49, -5):
            tmp = dst.with_suffix(".tmp.jpg")
            # No exif= argument: EXIF (incl. GPS) is not written to the web copy.
            scaled.save(tmp, "JPEG", quality=quality, optimize=True,
                        progressive=True, icc_profile=icc)
            if tmp.stat().st_size < MAX_BYTES:
                tmp.replace(dst)
                return width
        tmp.unlink()
        width = int(width * 0.9)  # shrink the whole frame; never crop


def check_pair_aspect(a, b):
    (ia, _), (ib, _) = load(a), load(b)
    ra, rb = ia.width / ia.height, ib.width / ib.height
    if abs(ra - rb) / ra > ASPECT_TOLERANCE:
        die(f"pair images have different aspect ratios ({ia.width}x{ia.height} vs "
            f"{ib.width}x{ib.height}). They cannot be matched without cropping, "
            "so nothing was stored.")
    return ia, ib


def make_pair():
    a, b = (original_for(s) for s in PAIR)
    if not (a and b):
        return
    ia, ib = check_pair_aspect(a, b)
    width = min(ia.width, ib.width, MAX_WIDTH)
    while True:
        wa = write_web(a, web_path(PAIR[0]), width)
        wb = write_web(b, web_path(PAIR[1]), width)
        if wa == wb == width:
            break
        width = min(wa, wb)
    # Heights can differ by rounding inside the tolerance; force identical size.
    sizes = {Image.open(web_path(s)).size for s in PAIR}
    if len(sizes) != 1:
        w, h = min(sizes)
        for s in PAIR:
            p = web_path(s)
            im = Image.open(p)
            if im.size != (w, h):
                im.resize((w, h), Image.LANCZOS).save(p, "JPEG", quality=85, optimize=True,
                                                     progressive=True)


def cmd_add(slot, src):
    if slot not in SLOTS:
        die(f"unknown slot '{slot}'. Slots: {', '.join(SLOTS)}")
    src = Path(src).resolve()
    if not src.is_file():
        die(f"no such file: {src}")
    if original_for(slot):
        die(f"slot '{slot}' already has an original ({original_for(slot).name}). "
            "Originals are never overwritten; remove it by hand if it really must change.")
    load(src)  # refuse unreadable / raw radiometric files before storing anything
    if slot in PAIR:
        other = original_for(PAIR[1 - PAIR.index(slot)])
        if other:
            check_pair_aspect(src, other)
    ORIGINALS.mkdir(exist_ok=True)
    dst = ORIGINALS / f"{slot}__{src.name}"
    shutil.copy2(src, dst)
    if sha256(src) != sha256(dst):
        dst.unlink()
        die("copy of original does not match the supplied file")
    if slot in PAIR:
        make_pair()
        if not all(original_for(s) for s in PAIR):
            print(f"{slot}: original stored. Web copy is made once the other half of the pair is added.")
            return
    else:
        write_web(dst, web_path(slot), MAX_WIDTH)
    print(f"{slot}: original -> {dst.relative_to(HERE)}")
    print("Now fill in this slot's row in MANIFEST.md, then run: process.py check")


def manifest_rows():
    rows = {}
    for line in MANIFEST.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 9 and cells[0] in SLOTS:
            keys = ["slot", "file", "original", "source", "caption", "alt",
                    "pages", "consent", "status"]
            rows[cells[0]] = dict(zip(keys, cells))
    return rows


def blank(v):
    return v in ("", "—", "-", "PENDING")


def cmd_check():
    errors, notes = [], []
    rows = manifest_rows()
    for slot in SLOTS:
        if slot not in rows:
            errors.append(f"{slot}: no row in MANIFEST.md")
    for slot, r in rows.items():
        web = web_path(slot)
        if web.exists():
            size = web.stat().st_size
            w, h = Image.open(web).size
            if w > MAX_WIDTH:
                errors.append(f"{slot}: web copy is {w}px wide (max {MAX_WIDTH})")
            if size >= MAX_BYTES:
                errors.append(f"{slot}: web copy is {size} bytes (must be under {MAX_BYTES})")
            if not original_for(slot):
                errors.append(f"{slot}: web copy has no original in originals/")
        source = r["source"].strip("`")
        caption = r["caption"].strip("`")
        if not blank(source) and source not in SOURCE_TYPES:
            errors.append(f"{slot}: unknown source type '{source}'")
        if source == "TRAINING DATA" and caption != TRAINING_CAPTION:
            errors.append(f"{slot}: TRAINING DATA caption must be exactly '{TRAINING_CAPTION}'")
        if source == "CLIENT JOB" and blank(r["consent"]):
            errors.append(f"{slot}: CLIENT JOB with no consent reference — not allowed")
        if source == "DESIGN ARTWORK" and not blank(caption) and "illustrative" not in caption.lower():
            errors.append(f"{slot}: DESIGN ARTWORK caption must say it is illustrative artwork")
        pending = [k for k in ("original", "source", "caption", "alt", "pages")
                   if r[k] == "PENDING"]
        if r["status"] == "Ready":
            if pending:
                errors.append(f"{slot}: marked Ready but has PENDING fields: {', '.join(pending)}")
            if not web.exists():
                errors.append(f"{slot}: marked Ready but {web.name} does not exist")
        elif pending:
            notes.append(f"{slot}: not usable yet (PENDING: {', '.join(pending)})")
    if all(s in rows for s in PAIR):
        a, b = rows[PAIR[0]], rows[PAIR[1]]
        if a["source"] != b["source"] or a["consent"] != b["consent"]:
            errors.append("vt-pair: both halves must have the same source type and consent ref")
        if all(web_path(s).exists() for s in PAIR):
            sa, sb = (Image.open(web_path(s)).size for s in PAIR)
            if sa != sb:
                errors.append(f"vt-pair: web copies differ in size ({sa} vs {sb})")
    for n in notes:
        print("note: ", n)
    for e in errors:
        print("ERROR:", e)
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["add"] and len(args) == 3:
        cmd_add(args[1], args[2])
    elif args == ["check"]:
        sys.exit(cmd_check())
    else:
        sys.exit(__doc__)
