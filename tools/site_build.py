#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
site_build — assemble the one-off host's public directory, from the synthetic vault and nothing else.

The connectome pages cannot be opened from a file on iOS. Not a bug in them: DuckDB-WASM spawns a
Web Worker and instantiates a thirty-megabyte module, both fetched cross-origin, and a page opened
from `file://` has an opaque origin, so WebKit blocks both and the page sits on "loading
duckdb-wasm" forever. Every browser on iOS is WebKit, so there is no browser to switch to. Served
over https the same page works, which is what `site/` is for.

WHAT THIS REFUSES TO DO. The real vault's pages are every square a diff of a private note, and a
hosted URL is a different kind of object from a file on a laptop — it is reachable, it is cached,
and it outlives the intention behind it. So the source directory is not a parameter. This reads
`synthetic/` and there is no flag that makes it read anywhere else; a path that resolves outside
it stops the build. Then it checks what it copied against every note title in the real vault,
because a synthetic corpus that had somehow taken a real title would be the one thing on these
pages a reader could recognise.

The gate on the far end fails closed (see site/functions/_middleware.ts), so a deployment made
before the password is set serves nothing rather than serving this to everybody. That is what
makes it safe to deploy first and configure second, which is the order anybody actually works in.

Usage:
    ./site_build.py                 # → site/public/
    ./site_build.py --check         # verify only, write nothing
"""

from __future__ import annotations
import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from _env import REPO_ROOT
from _hunks import load
from _page import STYLE, palette_css
from _synth import SYNTH_ROOT
from _term import link

# Not a parameter, and not reachable from the command line. See the header.
SOURCE = SYNTH_ROOT / "sessions"
PUBLIC = REPO_ROOT / "site" / "public"
REAL = REPO_ROOT / "sessions"

# What gets published, and what each one is, in the order a reader should meet them.
PAGES = [
    ("connections-*-topics.html", "The connectome, by topic",
     "One square is one unit, one arrow is one connection a sentence of that week's letter drew "
     "between two of them. Coloured by what each unit is about. Click an arrow."),
    ("connections-*.html", "The connectome, by what the edit did",
     "The same chart and the same arrows, with each square coloured by whether its edit only "
     "added lines, only removed them, or both."),
    ("microlite-waffle-mosaic.html", "The waffle, drawn by Mosaic",
     "Every week's hunks as squares, one per unit, with the diff behind each one a click away."),
    ("microlite-waffle.html", "The waffle, drawn by hand",
     "The same view with no library and no network at all — the one page here that already "
     "opens from a file on a phone."),
]


def collect() -> list[tuple[Path, str, str]]:
    """The pages to publish, resolved against the synthetic vault and checked to be inside it."""
    out, seen = [], set()
    for pattern, title, blurb in PAGES:
        for path in sorted(SOURCE.glob(pattern)):
            # A glob cannot escape its root, but a symlink inside it can. Resolve and prove.
            real = path.resolve()
            if not real.is_relative_to(SYNTH_ROOT.resolve()):
                sys.exit(f"Refusing: {path} resolves to {real}, outside {SYNTH_ROOT}.")
            if real in seen:
                continue
            seen.add(real)
            out.append((real, title, blurb))
    return out


def verify(paths: list[Path]) -> None:
    """The one check, and it is absolute.

    A real note title in a published page is a privacy failure, so there is no flag for it and no
    way past it. Titles rather than bodies because a title is short, distinctive and the thing a
    reader would actually recognise — and because the bodies of these pages are synthetic by
    construction, written by a generator that was never shown a real one.

    There used to be a second check here, against the topic gate's deny list. It is gone with the
    gate: what it stopped was a synthetic bereavement being described as one, which is not a
    privacy question and was never this function's business.
    """
    weeks = load(sorted(REAL.glob("*/microlite.md"))) if REAL.is_dir() else []
    titles = {h["note"][:-3].lower() for w in weeks for h in w["hunks"] if len(h["note"]) > 11}

    for path in paths:
        low = path.read_text(errors="replace").lower()
        # A hosted URL outlives the intention behind it, and this is the one thing on these pages
        # that could be traced to a person.
        if leaked := sorted(t for t in titles if t in low):
            sys.exit(f"Refusing to publish {path.name}: it contains real note titles "
                     f"({', '.join(leaked[:3])}). Nothing overrides this.")

    print(f"checked  {len(paths)} pages · no real note titles (of {len(titles)} checked)")


INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>petrograph — synthetic</title>
<style>
__STYLE__
  .layout {{ grid-template-columns: 1fr; max-width: 62ch; }}
  .panel {{ box-shadow: none; border-width: 2px; }}
  .detail p {{ margin: 0 0 14px; }}
  a.card {{
    display: block; color: inherit; text-decoration: none;
    border: 2px solid var(--ink); background: var(--paper);
    padding: 12px 14px; margin-bottom: 12px;
  }}
  a.card:hover {{ background: var(--card); }}
  a.card b {{ font-family: Helvetica, Arial, sans-serif; }}
  a.card span {{ display: block; font-size: 13px; margin-top: 5px; }}
</style>
</head>
<body>
<header>
  <h1>Synthetic</h1>
  <div class="meta">{meta}</div>
</header>
<div class="layout">
  <section class="panel">
    <h2>Four pages</h2>
    <div class="detail">
      <p>Every square on these charts is an edit to a note in a vault that does not exist, kept
      by a person who does not exist. Nothing here is anybody's. The pipeline that drew them is
      the one that draws the real thing, pointed at a different folder.</p>
{cards}
      <p style="font-size:13px">Three of the four load a plotting library from a CDN, so they
      want a network. That is also why they are here rather than in your files app: from a local
      file they cannot start the database they run on.</p>
    </div>
  </section>
</div>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Assemble site/public from the synthetic vault. Synthetic only, by construction.")
    ap.add_argument("--check", action="store_true", help="Verify the pages and write nothing.")
    args = ap.parse_args()

    if not SOURCE.is_dir():
        sys.exit(f"No synthetic vault at {SOURCE} — run: just synth")
    pages = collect()
    if not pages:
        sys.exit(f"No pages under {SOURCE} — run: just synth-pages")
    verify([p for p, _, _ in pages])
    if args.check:
        for p, title, _ in pages:
            print(f"  would publish  {p.name:<44} {title}")
        return

    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    PUBLIC.mkdir(parents=True)
    cards = []
    for path, title, blurb in pages:
        shutil.copy2(path, PUBLIC / path.name)
        cards.append(f'      <a class="card" href="{path.name}"><b>{title}</b>'
                     f'<span>{blurb}</span></a>')
    meta = (f"{len(pages)} pages · generated "
            f"{datetime.now(timezone.utc):%Y-%m-%d} · nobody's data")
    (PUBLIC / "index.html").write_text(
        INDEX.format(meta=meta, cards="\n".join(cards))
        .replace("__STYLE__", STYLE + "\n" + palette_css()))

    total = sum(p.stat().st_size for p in PUBLIC.iterdir())
    print(f"\n{len(pages) + 1} files · {total / 1024:,.0f} KB")
    print(f"  wrote  {link(PUBLIC)}")
    print("\nNext:  just site-deploy")


if __name__ == "__main__":
    main()
