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

## Received originals — not yet assigned to a slot

Supplied by the owner on 2026-11-19 and stored byte-for-byte in `originals/`.
The slot column is a **proposal** the owner asked for. Nothing is used on the
site until the owner approves it. The
descriptions below only identify each file. They are not captions, alt
text or source types.

| Original | Size | SHA-256 | What it shows (identification only) | Assigned slot |
|---|---|---|---|---|
| `originals/supplied-01.webp` | 2000×1328 | `9994b5e7a6c5b4ed8f09f767948fb430c211147d76815f95a918fe1506b049f6` | Angled thermal view of a flat roof, round vent top-left, red hotspot in centre | **Proposed:** roof-detail + texture-band — awaiting approval |
| `originals/supplied-02.webp` | 2000×1342 | `929eb1b03558c461ad633e67a9e546a51ca2e84f8ec67c43971b433a40beb9d8` | Straight-down thermal view of a roof with 8 HVAC units, hotspots near the right-hand units | Spare (alternative pair-thermal) |
| `originals/supplied-03.webp` | 2000×1493 | `5ba2a0de00bcdcf5b4c79eb213c434b0ef3bbc28e58957586e592aa3d741e03f` | Angled thermal view of a flat roof, square vent top-left, large red hotspot | Spare (alternative roof-detail) |
| `originals/supplied-04.webp` | 2000×1125 | `ceccd3feb6a70337f6ac27cc6c2ccfd7f5453760a41f4ff38509deb795b76dca` | Drone flying at dusk over a large roof shown in thermal colours | **Proposed:** hero-roof — awaiting approval |
| `originals/supplied-05.webp` | 2000×1342 | `bb8fe6f9ae0957eeeeca2ff354af525934ebe418dc26d3d56dc0bc1362909870` | Same straight-down roof as 02, hotspots along the middle seam and the lower-centre unit | **Proposed:** pair-thermal — awaiting approval |
| `originals/supplied-06.webp` | 2000×1328 | `bda68efce6b981db435dea92d389ee9a82855d442066882ebbb531d611d75239` | Straight-down thermal view of the same roof as 02/05, evenly warm units and roof edges, no single hotspot. Same scene but not pixel-aligned with 02/05 (14 px shorter, slightly offset) | Spare |
| `originals/supplied-07.webp` | 2000×1328 | `91f721b7443152e8018be597af61f1754d92904cc07fbbca7d95a8b372df4936` | Drone at dusk over the same 8-unit roof seen at an angle, large red-orange warm area lower-left | Spare (alternative hero-roof) |
| `originals/supplied-08.webp` | 2000×1131 | `2d311d6271b8eb03230254cc6cff30f183f87b359d25a73d5cb4e58da7e0d84a` | Drone at dusk over a large roof with rows of solar panels, warm cells on several panels; same sky/drone frame as 04 | **Proposed:** solar-array — awaiting approval |
| *(not stored — file not received)* | — | — | Visible-light, straight-down photo of the same roof as 02 and 05 (8 HVAC units, 2 skylights, parking bay bottom-right). Shown in chat but no file reached the workspace. | **Proposed:** pair-visible — awaiting approval and the file |

Gaps against the slots: the only **visible-light** photo (same view as 02/05)
was shown in chat, but its file has not been received. None of the files is a
split `pair-original.jpg`. No file is wide enough for **texture-band** without
cropping or extending it. The owner hasn't said which, if any, is
**TRAINING DATA**, so **packet-cover** has no proposal.

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
| 2026-11-19 | — | Five supplied WebP originals stored untouched in `originals/` as `supplied-01…05.webp`; not yet assigned to slots | owner |
| 2026-11-19 | — | Supplied originals 06–08 stored untouched; slot mapping proposed for owner approval (not applied) | owner |
