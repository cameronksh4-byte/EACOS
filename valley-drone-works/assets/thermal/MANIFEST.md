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
| hero-roof | `hero-roof.jpg` | `originals/supplied-04.webp` | DESIGN ARTWORK | Illustrative artwork — not a thermal scan of a real property. | DRAFT — awaiting approval: Illustration of a drone flying at dusk above a large flat commercial roof shown in thermal colours, with rooftop units glowing orange and a broad warm patch across one side of the roof. | Home hero only | N/A | Approved image; alt text awaiting approval |
| roof-detail | `roof-detail.jpg` | `originals/supplied-01.webp` | DESIGN ARTWORK | Illustrative artwork — not a thermal scan of a real property. | DRAFT — awaiting approval: Illustration of a flat roof in thermal colours seen at an angle, with a round roof vent in the top-left corner and a red-and-yellow warm patch spreading across the membrane seams. | Roof Scan page, and Sample Packet gallery only (never inside a findings spread) | N/A | Approved image; alt text awaiting approval — see open question 1 |
| pair-visible | `pair-visible.jpg` | PENDING — visible-light photo approved, file not received | DESIGN ARTWORK | Illustrative artwork — not a thermal scan of a real property. | PENDING | Roof Scan slider, left side | N/A | Approved; waiting for the visible-light file |
| pair-thermal | `pair-thermal.jpg` | `originals/supplied-05.webp` | DESIGN ARTWORK | Illustrative artwork — not a thermal scan of a real property. | DRAFT — awaiting approval: Illustration of a flat roof seen from directly above in thermal colours, with eight rooftop HVAC units and warm yellow-to-red areas along the centre seam and around one lower unit. | Roof Scan slider, right side | N/A | Approved image; web copy is made together with pair-visible |
| solar-array | `solar-array.jpg` | `originals/supplied-08.webp` | DESIGN ARTWORK | Illustrative artwork — not a thermal scan of a real property. | DRAFT — awaiting approval: Illustration of a drone at dusk above a large flat roof with rows of solar panels shown in thermal colours, several panels showing warm orange cells. | Solar page only, which stays unpublished | N/A | Approved image; alt text awaiting approval |
| texture-band | `texture-band.jpg` | `originals/supplied-01.webp` (same artwork as roof-detail) | DESIGN ARTWORK | Illustrative artwork — not a thermal scan of a real property. | DRAFT — awaiting approval: empty (`""`) — decorative background | About page background, low opacity only | N/A | Approved image; alt text and display shape awaiting approval — see open question 3 |
| packet-cover | `packet-cover.jpg` | PENDING — needs a training-data file, or a no-photo decision | TRAINING DATA | Training — sample data, not a client site. | PENDING | Sample Packet cover only | N/A | Waiting for a training-data file — see open question 4 |

## Received originals

Supplied by the owner on 2026-11-19 and stored byte-for-byte in `originals/`.
Slot assignments were proposed and then **approved by the owner on
2026-11-19**. The descriptions below only identify each file. They are not
captions, alt text or source types.

| Original | Size | SHA-256 | What it shows (identification only) | Assigned slot |
|---|---|---|---|---|
| `originals/supplied-01.webp` | 2000×1328 | `9994b5e7a6c5b4ed8f09f767948fb430c211147d76815f95a918fe1506b049f6` | Angled thermal view of a flat roof, round vent top-left, red hotspot in centre | roof-detail + texture-band (approved) |
| `originals/supplied-02.webp` | 2000×1342 | `929eb1b03558c461ad633e67a9e546a51ca2e84f8ec67c43971b433a40beb9d8` | Straight-down thermal view of a roof with 8 HVAC units, hotspots near the right-hand units | Spare (alternative pair-thermal) |
| `originals/supplied-03.webp` | 2000×1493 | `5ba2a0de00bcdcf5b4c79eb213c434b0ef3bbc28e58957586e592aa3d741e03f` | Angled thermal view of a flat roof, square vent top-left, large red hotspot | Spare (alternative roof-detail) |
| `originals/supplied-04.webp` | 2000×1125 | `ceccd3feb6a70337f6ac27cc6c2ccfd7f5453760a41f4ff38509deb795b76dca` | Drone flying at dusk over a large roof shown in thermal colours | hero-roof (approved) |
| `originals/supplied-05.webp` | 2000×1342 | `bb8fe6f9ae0957eeeeca2ff354af525934ebe418dc26d3d56dc0bc1362909870` | Same straight-down roof as 02, hotspots along the middle seam and the lower-centre unit | pair-thermal (approved) |
| `originals/supplied-06.webp` | 2000×1328 | `bda68efce6b981db435dea92d389ee9a82855d442066882ebbb531d611d75239` | Straight-down thermal view of the same roof as 02/05, evenly warm units and roof edges, no single hotspot. Same scene but not pixel-aligned with 02/05 (14 px shorter, slightly offset) | Spare |
| `originals/supplied-07.webp` | 2000×1328 | `91f721b7443152e8018be597af61f1754d92904cc07fbbca7d95a8b372df4936` | Drone at dusk over the same 8-unit roof seen at an angle, large red-orange warm area lower-left | Spare (alternative hero-roof) |
| `originals/supplied-08.webp` | 2000×1131 | `2d311d6271b8eb03230254cc6cff30f183f87b359d25a73d5cb4e58da7e0d84a` | Drone at dusk over a large roof with rows of solar panels, warm cells on several panels; same sky/drone frame as 04 | solar-array (approved) |
| *(not stored — file not received)* | — | — | Visible-light, straight-down photo of the same roof as 02 and 05 (8 HVAC units, 2 skylights, parking bay bottom-right). Shown in chat but no file reached the workspace. | pair-visible (approved; file not received) |

Still missing: the **visible-light** file for pair-visible (shown in chat, never
received as a file), and a **TRAINING DATA** file for packet-cover.

## Slot notes

- **hero-roof**: full-width hero on the Home page. Web copy is up to 1920 px wide.
- **roof-detail**: may not appear inside a findings spread. Findings spreads
  use clearly drawn example markups labeled as examples, never DESIGN ARTWORK
  photos.
- **pair-visible / pair-thermal**: two separate files of the same straight-down
  view, a visible-light photo and thermal artwork 05. They are not halves of a
  split `pair-original.jpg`. They are used together in the Roof Scan
  before/after slider. The visible file must be the same size as 05
  (2000×1342), so the two line up. Their web copies are made at the same size
  and are never cropped.
- **solar-array**: the Solar page stays unpublished, so this image does not
  appear on the live site until that changes.
- **texture-band**: the same artwork as roof-detail (`supplied-01.webp`),
  reused as a background. Only as the About page background, at low opacity.
  Because it is decorative, the draft alt text is empty (`""`).
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
2. **Alt text.** The drafts in the table are awaiting the owner's approval. A row
   isn't `Ready` until its alt text is approved.
3. **texture-band display shape.** Image 01 is 3:2, not wide. Choose one:
   (A) extend it to a wide 4:1 band (outpaint, no crop);
   (B) supply a separate wide background image;
   (C) show 01 at low opacity and accept that the browser hides the parts that
   don't fit a wide area, which is a visual crop.
   The web copy itself is the whole image, uncropped.
4. **packet-cover.** Send a frame you know is from a training flight (not a
   client site), or decide the cover has no photo.

## Change log

| Date | Slot | Change | By |
|---|---|---|---|
| 2026-11-19 | all | Manifest created; all slots awaiting supplied images | — |
| 2026-11-19 | all | Source types, fixed captions, allowed pages and consent (N/A) recorded from owner's instructions; pair slots renamed to `pair-visible` / `pair-thermal`; no CLIENT JOB rows | owner |
| 2026-11-19 | — | Five supplied WebP originals stored untouched in `originals/` as `supplied-01…05.webp`; not yet assigned to slots | owner |
| 2026-11-19 | — | Supplied originals 06–08 stored untouched; slot mapping proposed for owner approval (not applied) | owner |
| 2026-11-19 | hero-roof, roof-detail, texture-band, pair-thermal, solar-array, pair-visible | Owner approved the proposed mapping (04, 01, 01, 05, 08, visible-light photo); web copies made; alt text drafted for approval | owner |
