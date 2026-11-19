# Thermal image manifest — Valley Drone Works

Rules for every column are in [README.md](README.md). The site may only use an
image whose row has **no `PENDING` fields** and whose status is `Ready`.

**Source type** is one of `DESIGN ARTWORK`, `TRAINING DATA` or `CLIENT JOB`.
`CLIENT JOB` needs a consent reference from the site owner in `Consent ref`.
**Caption** is shown on the site word for word. For `TRAINING DATA` it is
always `Training - sample data, not a client site`.

| Slot | File name (web copy) | Original | Source type | Caption (exact, shown on site) | Alt text | Allowed pages | Consent ref | Status |
|---|---|---|---|---|---|---|---|---|
| hero-roof | `hero-roof.jpg` | PENDING | PENDING | PENDING | PENDING | PENDING | — | Awaiting image |
| roof-detail | `roof-detail.jpg` | PENDING | PENDING | PENDING | PENDING | PENDING | — | Awaiting image |
| vt-pair-visible | `vt-pair-visible.jpg` | PENDING | PENDING | PENDING | PENDING | PENDING | — | Awaiting image |
| vt-pair-thermal | `vt-pair-thermal.jpg` | PENDING | PENDING | PENDING | PENDING | PENDING | — | Awaiting image |
| solar-array | `solar-array.jpg` | PENDING | PENDING | PENDING | PENDING | PENDING | — | Awaiting image |
| texture-band | `texture-band.jpg` | PENDING | PENDING | PENDING | PENDING | PENDING | — | Awaiting image |
| packet-cover | `packet-cover.jpg` | PENDING | PENDING | PENDING | PENDING | PENDING | — | Awaiting image |

## Slot notes

- **hero-roof**: full-width hero. Web copy is up to 1920 px wide.
- **roof-detail**: close-up of the roof.
- **vt-pair-visible / vt-pair-thermal**: one matched pair of the same view,
  one visible and one thermal. Both rows must have the same source type and
  the same consent ref. The originals must share an aspect ratio, and neither
  is cropped.
- **solar-array**: solar panel array.
- **texture-band**: wide, low-detail image used as a section background. It
  is decoration and shows no caption on the page, but it still needs a source
  type and allowed pages. Alt text is empty (`""`) if it is purely decorative.
- **packet-cover**: cover image for the packet. If the packet counts as a
  report or sample finding, this slot **cannot** hold DESIGN ARTWORK.

## Change log

| Date | Slot | Change | By |
|---|---|---|---|
| 2026-11-19 | all | Manifest created; all slots awaiting supplied images | — |
