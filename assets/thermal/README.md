# /assets/thermal — Valley Drone Works image assets

This folder holds every roof, thermal and solar image the Valley Drone Works
website may show. Each image the site uses must have a row in
[`MANIFEST.md`](MANIFEST.md). If an image has no row, or its row still says
`PENDING`, the site must not use it.

**This is a portable staging folder.** It sits at the root of the EACOS repo
for now, but EACOS is *not* confirmed as the Valley Drone Works site and is not
the live site. When the real site repo is confirmed, the whole folder moves
there as one unit. Nothing in it depends on EACOS.

## Folder layout

```
assets/thermal/
├── README.md          ← this file (labeling rules)
├── MANIFEST.md        ← one row per slot: file, source type, caption, alt, pages, consent
├── process.py         ← imports supplied files, makes web copies, checks the rules
├── originals/         ← untouched originals, byte-for-byte as supplied. Never edit these.
│   └── pair-original.jpg   (source of the pair-visible / pair-thermal panels)
└── *.jpg              ← web copies: max 1920 px wide, under 400 KB each
```

## Labeling rules

Every image is one of three source types. The source type sets how the image
is captioned and where it may appear. Captions are fixed. Use them exactly as
written and never paraphrase them.

### DESIGN ARTWORK
- Always captioned exactly:
  **`Illustrative artwork — not a thermal scan of a real property.`**
- **Never** used inside a report or a sample finding. The Sample Packet
  counts as a report, so no DESIGN ARTWORK goes inside it. That includes its
  cover and its findings spreads.

### TRAINING DATA
- Always captioned exactly:
  **`Training — sample data, not a client site.`**

### CLIENT JOB
- Used **only** with a written consent reference. That reference must be
  recorded in the manifest's `Consent ref` column.
- An image is marked CLIENT JOB only when the site owner supplies the consent
  reference.
- **This library currently has no CLIENT JOB images and no consent
  references.** No row may be marked CLIENT JOB.

### Findings spreads (Sample Packet)
Findings spreads use clearly drawn example markups labeled as examples. They
never use DESIGN ARTWORK photos.

## Sourcing rules

- Use only image files the site owner supplies. No stock imagery, and no
  generated or replacement imagery.
- Captions are never invented. Only the two fixed captions above are used.
- An image's source type is never guessed from how it looks. It comes from
  the owner.

## Web copies

- Max **1920 px wide** and under **400 KB** each, saved as JPEG with metadata
  (including GPS) removed. Originals keep their metadata.
- Images are scaled, never cropped. **Thermal images are never cropped**,
  never recoloured and never re-paletted. Only their size changes.
  Images are never enlarged.
- `pair-visible.jpg` and `pair-thermal.jpg` are a matched pair for the Roof
  Scan slider. Their web copies come out the same size so the two line up.
- Web copies are viewing images only. Measurements come from `originals/`
  only.

## Workflow

```bash
# 1. Copy the supplied folder in. Every supplied file is stored untouched in originals/.
python3 assets/thermal/process.py import path/to/valley-drone-works/assets-thermal

# 2. Fill in alt text in MANIFEST.md, then set Status to Ready

# 3. Check the folder against the rules (run again after any change)
python3 assets/thermal/process.py check
```

To rebuild web copies from `originals/` (for example after moving the folder),
run `python3 assets/thermal/process.py build`.
