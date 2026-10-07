"""Turn a launch kit into a one-file landing page with a lead capture form.

The form posts to any form backend you choose (Formspree, Tally, ConvertKit,
Netlify Forms...) via `lead_capture.form_action` in profile.yaml. Host the
generated file anywhere static: GitHub Pages, Netlify, Cloudflare Pages.
"""

from html import escape
from pathlib import Path

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name}</title>
<style>
 :root{{--ink:#14213d;--muted:#5c677d;--accent:#2a6f4e;--bg:#fbfaf7}}
 body{{margin:0;font:17px/1.6 system-ui,sans-serif;color:var(--ink);background:var(--bg)}}
 main{{max-width:680px;margin:0 auto;padding:56px 16px}}
 h1{{font-size:2.2rem;line-height:1.15;margin:0 0 12px}} .sub{{color:var(--muted);font-size:1.15rem}}
 ul{{padding-left:1.2em}} li{{margin:6px 0}}
 form{{display:flex;gap:8px;flex-wrap:wrap;margin:28px 0}}
 input{{flex:1 1 220px;padding:12px;border:1px solid #c9ccd3;border-radius:8px;font:inherit}}
 button{{padding:12px 18px;border:0;border-radius:8px;background:var(--accent);color:#fff;font:inherit;cursor:pointer}}
 .card{{background:#fff;border:1px solid #e6e3dc;border-radius:12px;padding:20px;margin-top:28px}}
</style></head><body><main>
<h1>{headline}</h1><p class="sub">{subheadline}</p>
<ul>{value_props}</ul>
<div class="card"><strong>Free: {lead_magnet_title}</strong><ul>{outline}</ul>
<form action="{form_action}" method="POST">
 <input type="hidden" name="product" value="{idea_id}">
 <input type="email" name="email" placeholder="you@company.com" required aria-label="Work email">
 <button type="submit">{cta}</button>
</form></div>
<div class="card"><strong>Founding customer offer</strong><p>{offer}</p></div>
</main></body></html>
"""


def render_landing(idea: dict, kit: dict, form_action: str) -> str:
    li = lambda items: "".join(f"<li>{escape(x)}</li>" for x in items)  # noqa: E731
    return TEMPLATE.format(
        name=escape(idea["name"]), idea_id=escape(idea["id"]),
        headline=escape(kit["headline"]), subheadline=escape(kit["subheadline"]),
        value_props=li(kit["value_props"]), lead_magnet_title=escape(kit["lead_magnet_title"]),
        outline=li(kit["lead_magnet_outline"]), cta=escape(kit["call_to_action"]),
        offer=escape(kit["founding_offer"]), form_action=escape(form_action),
    )


def write_landing(idea: dict, kit: dict, form_action: str, out_dir: Path) -> Path:
    path = out_dir / "sites" / idea["id"] / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_landing(idea, kit, form_action))
    return path
