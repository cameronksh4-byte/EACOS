# Scroll-through hero

The Valley AgentWorks intro video (interpolated to 60 fps) plays as the visitor scrolls. The picture stays pinned while four captions fade in, ending on "Stop losing jobs to voicemail" and a **Book a free audit** button.

Open `index.html` to see a working demo. It has no dependencies and no build step.

## How it works
- The video is exported as **150 WebP frames**, which are drawn on a `<canvas>` as you scroll:
  - `frames/desktop/`: 1280 px wide, about 4.9 MB in total.
  - `frames/mobile/`: 720 px wide, about 2.6 MB. Used when the screen is 760 px wide or less.
- Frames load in a coarse-to-fine order (every 16th frame, then every 8th, and so on). Scrolling works almost immediately and gets smoother as the remaining frames arrive.
- **Reduced motion and no JavaScript:** frames aren't downloaded. Visitors see a single still of the closing frame with the final message and button.
- **No layout shift:** a one-line script in `<head>` decides before first paint whether scrolling is on. Measured CLS is 0.
- **English/Spanish:** each text element has a `data-es` attribute. The component switches when `<html lang>` changes to `es`, so it follows the site's own language toggle.
- **Keyboard:** pressing Tab to the CTA jumps to the end of the section, where the button is visible.

## Add it to the website
1. Copy this folder into the site, for example as `/assets/scroll-hero/`.
2. In `<head>` of the page, add the snippet below, plus the stylesheet link with the path adjusted:
   ```html
   <script>if(!matchMedia("(prefers-reduced-motion: reduce)").matches)document.documentElement.classList.add("sh-js")</script>
   <link rel="preload" as="image" href="/assets/scroll-hero/poster.webp" fetchpriority="high">
   <link rel="stylesheet" href="/assets/scroll-hero/scroll-hero.css">
   ```
3. Paste the `<section class="sh">…</section>` block and its `<script>` tag from `index.html` where the hero should go.
   - Set `data-src="/assets/scroll-hero/frames/"`.
   - Fix the `poster.webp` and `poster-end.webp` paths.
4. Brand colors and font: edit the `--sh-*` variables at the top of `scroll-hero.css`. The copper accent is a placeholder taken from the video.
5. Scroll length: `--sh-scroll` (default `400vh`). A smaller value makes the video play faster.
6. Captions: edit the text and its `data-es` translation. `data-from` and `data-to` set when each caption shows, as fractions of the scroll (0–1).

## Re-exporting frames from a new video
```bash
ffmpeg -i video.mp4 -vf "select='not(mod(n\,4))',scale=1280:-2:flags=lanczos" -fps_mode vfr -c:v libwebp -quality 72 frames/desktop/f%03d.webp
ffmpeg -i video.mp4 -vf "select='not(mod(n\,4))',scale=720:-2:flags=lanczos"  -fps_mode vfr -c:v libwebp -quality 68 frames/mobile/f%03d.webp
```
Then set `data-frames` to the new frame count.

## Tested (Chromium, headless)
| Check | Result |
|---|---|
| Desktop 1440×900: frames load, each caption appears in its scroll range, CTA at the end | Pass |
| Mobile 390×844: mobile frame set used, captions wrap, CTA visible | Pass |
| Reduced motion: no frame downloads, closing still with headline and CTA | Pass |
| Switching `<html lang="es">` swaps every caption to Spanish | Pass |
| Tab to the CTA scrolls it into view | Pass |
| Cumulative Layout Shift | 0 |
| Console errors | None |

Not yet tested: real iOS Safari and Android devices, and a Lighthouse run on the live site.
