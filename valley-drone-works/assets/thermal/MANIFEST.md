# Thermal image manifest — Valley Drone Works

Rules for every column are in [README.md](README.md). The site may only use an
image whose row has **no `PENDING` fields** and whose status is `Ready`.

Fixed captions. Use them exactly as written:

- DESIGN ARTWORK: `Illustrative artwork — not a thermal scan of a real property.`
- TRAINING DATA: `Training — sample data, not a client site.`

This library has **no CLIENT JOB images and no consent references.** No row
may be marked CLIENT JOB.

| Slot | File name (web copy) | Original | Source type | Caption (exact, shown on site) | Alt text | Allowed pages | Consent ref | Status |
|---|---|---|---|---|---|---|---|---|
| hero-roof | `hero-roof.jpg` | `originals/hero-roof.jpg` | DESIGN ARTWORK | Illustrative artwork — not a thermal scan of a real property. | PENDING | Home hero only | N/A | Awaiting files |
| roof-detail | `roof-detail.jpg` | `originals/roof-detail.jpg` | DESIGN ARTWORK | Illustrative artwork — not a thermal scan of a real property. | PENDING | Roof Scan page, and Sample Packet gallery only (never inside a findings spread) | N/A | Awaiting files — see open question 1 |
| pair-visible | `pair-visible.jpg` | `originals/pair-visible.jpg` (left panel of `originals/pair-original.jpg`) | DESIGN ARTWORK | Illustrative artwork — not a thermal scan of a real property. | PENDING | Roof Scan slider, left side | N/A | Awaiting files |
| pair-thermal | `pair-thermal.jpg` | `originals/pair-thermal.jpg` (right panel of `originals/pair-original.jpg`) | DESIGN ARTWORK | Illustrative artwork — not a thermal scan of a real property. | PENDING | Roof Scan slider, right side | N/A | Awaiting files |
| solar-array | `solar-array.jpg` | `originals/solar-array.jpg` | DESIGN ARTWORK | Illustrative artwork — not a thermal scan of a real property. | PENDING | Solar page only, which stays unpublished | N/A | Awaiting files |
| texture-band | `texture-band.jpg` | `originals/texture-band.jpg` | DESIGN ARTWORK | Illustrative artwork — not a thermal scan of a real property. | PENDING | About page background, low opacity only | N/A | Awaiting files |
| packet-cover | `packet-cover.jpg` | `originals/packet-cover.jpg` | TRAINING DATA | Training — sample data, not a client site. | PENDING | Sample Packet cover only | N/A | Awaiting files |

## Slot notes

- **hero-roof**: full-width hero on the Home page. Web copy is up to 1920 px wide.
- **roof-detail**: may not appear inside a findings spread. Findings spreads
  use clearly drawn example markups labeled as examples, never DESIGN ARTWORK
  photos.
- **pair-visible / pair-thermal**: the left and right panels of
  `originals/pair-original.jpg`, split at the centre divider. No thermal
  content was cropped. They are used together in the Roof Scan
  before/after slider. Their web copies are made at the same size and are
  never cropped.
- **solar-array**: the Solar page stays unpublished, so this image does not
  appear on the live site until that changes.
- **texture-band**: the same artwork as `roof-detail.jpg`, reused as a
  background. Only as the About page background, at low opacity. Because it is
  decorative, its alt text will likely be empty (`""`). That still needs your
  confirmation.
- **packet-cover**: the Sample Packet **counts as a report**, so its cover
  cannot be DESIGN ARTWORK. It is TRAINING DATA with the fixed training
  caption.

## Open questions

1. **roof-detail in the Sample Packet gallery.** The Sample Packet counts as a
   report, and DESIGN ARTWORK is never used inside a report. If the "Sample
   Packet gallery" is inside the packet itself, roof-detail can't go there. If
   it is a web page that shows the packet and sits outside the packet document,
   it can. Please confirm which. Until then, the site uses roof-detail on the
   Roof Scan page only.
2. **Alt text** for every slot stays `PENDING` until the images are in this
   workspace and the wording is approved.

## Change log

| Date | Slot | Change | By |
|---|---|---|---|
| 2026-11-19 | all | Manifest created; all slots awaiting supplied images | — |
| 2026-11-19 | all | Source types, fixed captions, allowed pages and consent (N/A) recorded from owner's instructions; pair slots renamed to `pair-visible` / `pair-thermal`; no CLIENT JOB rows | owner |
