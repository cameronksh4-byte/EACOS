# Valley Drone Works — site root

This is the root folder for the Valley Drone Works website. The site is built
as standalone static files, and every site path is relative to this folder. For
example, `/assets/thermal/` means `valley-drone-works/assets/thermal/`.

**Staging only.** This folder sits inside the EACOS repo for now. EACOS is
*not* confirmed as the Valley Drone Works site and is not the live site. When
the real site repo is confirmed, this whole folder moves there as one unit.
Nothing in it depends on EACOS.

```
valley-drone-works/
├── README.md
└── assets/
    └── thermal/       ← image library, manifest and labeling rules (see its README)
```
