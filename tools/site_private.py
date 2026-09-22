#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
site_private — put ONE real page behind a password, on its own deployment.

A separate tool from `site_build.py`, and deliberately not a flag on it. That one reads the
synthetic vault, has no argument that points it anywhere else, and refuses outright if a real note
title appears in what it is about to publish. Those are the properties that make it safe to run
without thinking, and a `--private` flag would have quietly turned the guard into a preference.
Two tools, two directories, two Cloudflare projects, two passwords. Nothing here can be reached by
running the other one wrong.

WHAT THIS PUBLISHES. Every square on a connectome page is a diff of a private note, and the panel
behind each arrow is the text of one. The page also carries the week's letter in quotation, a
sleep report and a ledger. That is the most personal artefact this repository produces, and
hosting it makes it a different kind of object from a file on a laptop: reachable, cached by
something, and outliving the afternoon that wanted it. So this asks every time, and it publishes
only the pages it was handed — named on the command line or the two defaults below, never a glob
over the sessions directory. An index is generated over exactly those, so the deployment has no
URL on it that was not chosen.

The gate is the same `site/functions/_middleware.ts` the synthetic deployment uses, which fails
closed: until a password is set the project serves a 503 rather than a page. For this one a
password is the floor rather than the ceiling — see the note `--yes` prints.

Usage:
    ./site_private.py                    # say what it would publish, write nothing
    ./site_private.py --yes              # → site/private/
    ./site_private.py --page sessions/connections-2026-06-06.html --yes   # repeatable
"""

from __future__ import annotations
import argparse
import shutil
import sys
from pathlib import Path

from _env import REPO_ROOT
from _page import STYLE, palette_css
from _synth import SYNTH_ROOT
from _term import link

SESSIONS = REPO_ROOT / "sessions"
PRIVATE = REPO_ROOT / "site" / "private"

# What gets published when nothing is named, and how each one is described on the index. A short
# fixed list rather than a glob: the point of this tool is that the set is chosen, and a glob over
# `sessions/` would put whatever happened to be rendered last behind the password.
DEFAULTS = [
    ("connections-*-topics.html", "The connectome",
     "One square is one unit — every hunk of the last four weeks, plus that week's sleep report "
     "and ledger. One arrow is one connection a sentence of the letter drew between two of them. "
     "Coloured by topic. Click an arrow; the slider above the chart starts at the strongest."),
    ("microlite-waffle-mosaic.html", "The waffle",
     "Every archived week's hunks as squares, one per unit, with the diff behind each one a click "
     "away. Two views: one square per hunk, or one per round number of characters changed."),
]

INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>petrograph</title>
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
  <h1>petrograph</h1>
  <div class="meta">{meta}</div>
</header>
<div class="layout">
  <section class="panel">
    <h2>{count}</h2>
    <div class="detail">
{cards}
      <p style="font-size:13px">Both pages load their plotting library from a CDN, so they want a
      network. That is also why they are here rather than in your files app: opened from a local
      file they cannot start the database they run on.</p>
    </div>
  </section>
</div>
</body>
</html>
"""


# Every page this tool has ever put behind the password, one filename a line. Local and ignored —
# it holds nothing but names. It exists because Pages keeps a removed asset in each data centre's
# cache for up to a week after the deployment that dropped it, and pages.dev has no purge: a page
# that was replaced goes on answering its old URL, to anyone with the password, until the cache
# happens to let go. A redirect is the one thing that outranks that cache, and to write one for a
# page you have to remember it existed.
LEDGER = REPO_ROOT / "site" / ".private-published"


def redirects(now: list[str]) -> list[str]:
    """`_redirects` lines sending every page published before and not served now to the index.

    Both spellings, because Pages answers a page at its name and at its name without `.html`, and
    a stale copy is cached under whichever one somebody asked for. 302 rather than 301: a browser
    remembers a 301 for good, and a page that comes back one week should be reachable again.
    """
    before = LEDGER.read_text().split() if LEDGER.is_file() else []
    gone = [name for name in dict.fromkeys(before) if name not in now]
    return [f"/{stem}  /  302" for name in gone for stem in (name, name.removesuffix(".html"))]


def resolve(patterns) -> list[tuple[Path, str, str]]:
    """The pages to publish. Named explicitly, or the two defaults — never a glob over sessions."""
    out = []
    for pattern, title, blurb in patterns:
        found = sorted(SESSIONS.glob(pattern))
        if found:
            out.append((found[-1].resolve(), title, blurb))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="One real page, copied to its own deployment directory, behind its own password.")
    ap.add_argument("--page", type=Path, action="append",
                    help="A page to publish. Repeatable. Default: the newest topic connectome "
                         "and the newest Mosaic waffle.")
    ap.add_argument("--yes", action="store_true",
                    help="Actually write it. Without this the tool only says what it would do.")
    args = ap.parse_args()

    if args.page:
        pages = [(p.expanduser().resolve(), p.stem, "") for p in args.page]
    else:
        pages = resolve(DEFAULTS)
    if not pages:
        sys.exit(f"Nothing to publish — no matching page under {SESSIONS}.")

    for page, _, _ in pages:
        if not page.is_file():
            sys.exit(f"{page} does not exist.")
        # The synthetic vault has its own tool, its own directory and its own project. Publishing
        # it through this one would put fiction behind the password meant for a life, and — worse
        # — would make the two indistinguishable in six months.
        if page.is_relative_to(SYNTH_ROOT.resolve()):
            sys.exit(f"{page.name} is synthetic. Use: just site-build")

    total = sum(p.stat().st_size for p, _, _ in pages)
    for page, title, _ in pages:
        print(f"page     {str(page.relative_to(REPO_ROOT)):<46} {page.stat().st_size / 1024:>5,.0f} KB  {title}")
    print(f"serves   {len(pages)} pages and an index over exactly those, and nothing else")
    retired = redirects([p.name for p, _, _ in pages])
    if retired:
        print(f"retires  {len(retired) // 2} page{'s' if len(retired) != 2 else ''} published "
              f"before, each redirected to the index: "
              + ", ".join(r.split()[0].lstrip("/") for r in retired[::2]))
    print()
    print("This is the real vault. Every square is a diff of a note, every arrow opens two of")
    print("them, and the page quotes the week's letter, the sleep report and the ledger.")

    if not args.yes:
        print("\nNothing written. Pass --yes to publish it.")
        return

    if PRIVATE.exists():
        shutil.rmtree(PRIVATE)
    PRIVATE.mkdir(parents=True)
    cards = []
    for page, title, blurb in pages:
        shutil.copy2(page, PRIVATE / page.name)
        cards.append(f'      <a class="card" href="{page.name}"><b>{title}</b>'
                     + (f'<span>{blurb}</span>' if blurb else "") + "</a>")
    (PRIVATE / "index.html").write_text(
        INDEX.format(meta=f"{len(pages)} pages · {total / 1024:,.0f} KB · private",
                     count="Behind the password",
                     cards="\n".join(cards))
        .replace("__STYLE__", STYLE + "\n" + palette_css()))
    if retired:
        (PRIVATE / "_redirects").write_text("\n".join(retired) + "\n")
    # Remembered after the write, so a run that failed half way does not record a page as served.
    names = (LEDGER.read_text().split() if LEDGER.is_file() else []) + [p.name for p, _, _ in pages]
    LEDGER.write_text("\n".join(dict.fromkeys(names)) + "\n")
    print(f"\n  wrote  {link(PRIVATE)}  ({len(pages) + 1 + bool(retired)} files)")
    print("\nA password is the floor here, not the ceiling. Basic auth sends the same credential")
    print("on every request, has no audit trail and cannot be revoked for one person — so if the")
    print("URL and the password travel together once, everything behind them travels with them.")
    print("Cloudflare Access gives per-person identity, a log and revocation; site/README.md has")
    print("the four steps, and the gate prefers it automatically once its secrets are set.")
    print("\nNext:  just site-private-password   then   just site-private-deploy")


if __name__ == "__main__":
    main()
