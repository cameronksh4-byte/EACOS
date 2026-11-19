# /assets/thermal — Valley Drone Works image assets

This folder holds every roof, thermal and solar image the Valley Drone Works
website may show. Each image the site uses must have a row in
[`MANIFEST.md`](MANIFEST.md). If an image has no row, or its row still says
`PENDING`, the site must not use it.

## Folder layout

```
assets/thermal/
├── README.md          ← this file (labeling rules)
├── MANIFEST.md        ← one row per slot: file, source type, caption, alt, pages, consent
├── process.py         ← makes web copies and checks the folder against the rules
├── originals/         ← untouched originals, byte-for-byte as supplied. Never edit these.
└── *.jpg              ← web copies: max 1920 px wide, under 400 KB each
```

## Labeling rules

Every image is one of three source types. The source type sets how the image
is captioned and where it may appear.

### DESIGN ARTWORK
- Its caption must say that it is **illustrative artwork**. The exact wording
  for each image is written in the manifest by the site owner.
- It is **never** used inside a report, a sample report or a sample finding,
  and never presented as an inspection result.

### TRAINING DATA
- It is always captioned exactly:
  **`Training - sample data, not a client site`**
- That caption is never shortened, reworded or left out.

### CLIENT JOB
- It may be used **only** with a written consent reference. That reference
  must be recorded in the manifest's `Consent ref` column.
- An image is marked CLIENT JOB only when the site owner supplies the consent
  reference. Without one it is not marked CLIENT JOB and is not published.
- Its web copy has its metadata (including GPS location) removed by
  `process.py`. The original in `originals/` keeps it.

## Sourcing rules

- Use only image files the site owner supplies. No stock imagery, and no
  generated or replacement imagery.
- Captions are never invented. A caption is either the fixed TRAINING DATA
  caption or wording the owner supplied. Until then it reads `PENDING`.
- An image's source type is never guessed from how it looks.

## Web copies

- Max **1920 px wide** and under **400 KB** each, saved as JPEG.
- Images are scaled, never cropped. **Thermal images are never cropped**,
  never recoloured and never re-paletted. Only their size changes.
  Images are never enlarged.
- The two `vt-pair-*` files are a matched pair. Their originals must have the
  same aspect ratio, and their web copies come out the same size so the two
  line up exactly.
- Web copies are viewing images only. Any temperature (radiometric) data in a
  thermal original is lost in the web copy. Measurements come from
  `originals/` only.

## Adding an image

```bash
# 1. Make the original + web copy for a slot (refuses to overwrite an existing original)
python3 assets/thermal/process.py add hero-roof /path/to/supplied/file.jpg

# 2. Fill in that slot's row in MANIFEST.md (source type, caption, alt, pages, consent ref)

# 3. Check the folder against the rules
python3 assets/thermal/process.py check
```

Slots: `hero-roof`, `roof-detail`, `vt-pair-visible`, `vt-pair-thermal`,
`solar-array`, `texture-band`, `packet-cover`.
