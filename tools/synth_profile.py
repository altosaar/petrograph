#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["baml-bridge==0.17.0", "pydantic>=2"]
# ///
"""
synth_profile — what the real vault is shaped like, measured and then forgotten.

The only tool here that reads the private notes and hands any of them to a model, and it exists
so that nothing else has to. What it writes out is `synthetic/profile.json`: about sixty numbers
describing habits — how many hunks a week holds, how long they run, how often a note comes back
a fortnight later, how many people a typical edit names — and `synthetic/style-guide.md`, the
same numbers written as instructions somebody could follow. Both are committed. Neither contains
a word of anybody's notes.

TWO KINDS OF MEASUREMENT, and the split is deliberate. Anything a computer can count is counted
here in Python: sizes, ratios, recurrence, how often a line starts in lower case, how often one
is a bullet. No model is involved and none could help. The model is asked only for the two
judgements arithmetic cannot make — how many distinct people, places, organisations, addresses,
events and figures a hunk names, and whether part of it was pasted in from somebody else — and
`baml_src/synth_profile.baml` gives it nowhere to put anything else, because `HunkShape` has no
string field. That is the wall between the real vault and the fake one: a return type, not a
promise. The counts are then averaged over every hunk in the run before they are written down,
so what survives describes the habit and not any hunk.

Cached whole under `share/.baml-cache`, keyed by every hunk's text and the prompt's own source,
like the other passes here. Two hundred calls is a couple of minutes and a few cents once.

Usage:
    ./synth_profile.py                  # every session with a microlite.md
    ./synth_profile.py --dry-run        # what would be measured and what it would cost
    ./synth_profile.py --no-llm         # the arithmetic only, no API call, no entity counts
"""

from __future__ import annotations
import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from _cache import cache_path, write_atomic
from _env import REPO_ROOT, api_key
from _hunks import load
from _synth import PROFILE, STYLE_GUIDE, SYNTH_ROOT
from _term import link

SESSIONS = Path(os.environ.get("PETROGRAPH_OUT") or REPO_ROOT).expanduser().resolve() / "sessions"
CACHE_DIR = REPO_ROOT / "share" / ".baml-cache"
PROMPT = REPO_ROOT / "baml_src" / "synth_profile.baml"

# How much of a hunk the profiler reads. The same window `hunk_connections.py` uses, so the two
# passes see comparable material and the entity rates describe what a reader of the chart sees.
HUNK_CHARS = 420

# Which fields of HunkShape are counts to be averaged, and which are rates to be proportioned.
COUNTS = ("people", "places", "organizations", "addresses", "events", "numbers")
FLAGS = ("has_pasted_message", "has_typos", "has_tags")

BULLET = re.compile(r"^[-*+]\s|^\d+[.)]\s")
HEADING = re.compile(r"^#{1,6}\s")
CHECKBOX = re.compile(r"^[-*+]\s\[[ xX]\]")


def quantiles(values: list[float]) -> dict:
    """A distribution as five numbers. Enough to imitate a shape; not enough to reconstruct one."""
    xs = sorted(values)
    if not xs:
        return {}

    def at(q: float) -> float:
        return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1)

    return {"min": round(xs[0], 1), "p25": at(0.25), "median": at(0.5),
            "p75": at(0.75), "p90": at(0.9), "max": round(xs[-1], 1),
            "mean": round(statistics.fmean(xs), 1)}


def changed_lines(hunk: dict) -> list[str]:
    """The `+` lines of a hunk, unmarked — what the writer actually typed."""
    return [l[1:] for l in hunk["text"].splitlines() if l.startswith("+")]


def arithmetic(weeks: list[dict]) -> dict:
    """Everything measurable without a model. No text leaves this function."""
    hunks = [h for w in weeks for h in w["hunks"]]
    diffs = [h for h in hunks if h["kind"] != "full"]

    # How often a note title comes back in a later week. The distribution only — a title is a
    # name somebody chose, and the whole point of a synthetic corpus is not to publish them.
    span = Counter()
    seen: dict[str, set] = {}
    for w in weeks:
        for note in w["notes"]:
            seen.setdefault(note, set()).add(w["week"])
    for wks in seen.values():
        span[len(wks)] += 1
    total_notes = sum(span.values()) or 1

    lines = [l for h in diffs for l in changed_lines(h) if l.strip()]
    starts = [l.lstrip() for l in lines if l.lstrip() and l.lstrip()[0].isalpha()]

    titles = list(seen)
    kinds = Counter(h["kind"] for h in hunks)

    return {
        "weeks": len(weeks),
        "hunks_per_week": quantiles([len(w["hunks"]) for w in weeks]),
        "notes_per_week": quantiles([len(w["notes"]) for w in weeks]),
        "hunks_per_note": round(len(hunks) / max(1, sum(len(w["notes"]) for w in weeks)), 2),
        # What the edit did, as fractions. `full` is a short note the reader returned whole
        # rather than as a diff, and it is a quarter of the squares on the chart.
        "kind_mix": {k: round(kinds[k] / max(1, len(hunks)), 3)
                     for k in ("added", "mixed", "removed", "full")},
        "hunk_chars": quantiles([h["chars"] for h in diffs]),
        "added_lines": quantiles([h["add"] for h in diffs]),
        "removed_lines": quantiles([h["dele"] for h in diffs]),
        "note_returns": {str(k): round(v / total_notes, 3) for k, v in sorted(span.items())},
        "title": {
            "chars": quantiles([len(t) for t in titles]),
            "hyphens": quantiles([t.count("-") for t in titles]),
            "all_lower_rate": round(sum(t == t.lower() for t in titles) / max(1, len(titles)), 3),
            "has_digit_rate": round(sum(any(c.isdigit() for c in t) for t in titles)
                                    / max(1, len(titles)), 3),
        },
        "line": {
            "chars": quantiles([len(l) for l in lines]),
            "lower_start_rate": round(sum(s[0].islower() for s in starts) / max(1, len(starts)), 3),
            "bullet_rate": round(sum(bool(BULLET.match(l)) for l in lines) / max(1, len(lines)), 3),
            "checkbox_rate": round(sum(bool(CHECKBOX.match(l)) for l in lines) / max(1, len(lines)), 3),
            "heading_rate": round(sum(bool(HEADING.match(l)) for l in lines) / max(1, len(lines)), 3),
            "url_rate": round(sum("http" in l for l in lines) / max(1, len(lines)), 3),
        },
    }


def entity_rates(units: list[str], no_llm: bool, no_cache: bool) -> dict | None:
    """Per-hunk entity counts, averaged. The one thing here a model is asked for."""
    if no_llm:
        return None
    key = cache_path(CACHE_DIR, {
        "fn": "ProfileHunk",
        "prompt": PROMPT.read_text(errors="replace") if PROMPT.is_file() else "",
        "units": units,
    }, ".json")
    if key.exists() and not no_cache:
        print("shapes   from cache — nothing billed.")
        shapes = json.loads(key.read_text())
    else:
        os.environ["ANTHROPIC_API_KEY"] = api_key("ANTHROPIC_API_KEY")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            from baml_sdk import ProfileHunk
        except ImportError as exc:  # pragma: no cover - a setup failure, not a runtime one
            sys.exit(f"Could not import the generated BAML client ({exc}).\nRun: just baml")
        print(f"shapes   asking haiku for {len(units):,} hunk shapes…")
        shapes = []
        for i, text in enumerate(units, 1):
            s = ProfileHunk(hunk=text)
            # Read field by field into plain numbers. Nothing else on the object is kept, and
            # there is nothing else on the object to keep.
            shapes.append({f: int(getattr(s, f)) for f in COUNTS}
                          | {f: bool(getattr(s, f)) for f in FLAGS})
            if i % 25 == 0 or i == len(units):
                print(f"  {i:>4}/{len(units)}")
        write_atomic(key, json.dumps(shapes).encode())

    n = max(1, len(shapes))
    return {
        "per_hunk": {f: round(sum(s[f] for s in shapes) / n, 2) for f in COUNTS},
        "share_of_hunks_naming_one": {
            f: round(sum(bool(s[f]) for s in shapes) / n, 3) for f in COUNTS},
        "rates": {f: round(sum(s[f] for s in shapes) / n, 3) for f in FLAGS},
        "hunks_measured": len(shapes),
    }


def rewrap(text: str, width: int = 96) -> str:
    """Join each bullet back onto one logical line, then wrap it once, evenly.

    The template is written wrapped so the source is readable, which leaves the rendered file
    broken mid-clause wherever an interpolated number came out a different width than the last
    run. This is what makes the committed guide read like a document rather than like output.
    """
    import textwrap
    out, buf = [], []

    def flush() -> None:
        if buf:
            joined = " ".join(" ".join(buf).split())
            out.extend(textwrap.wrap(joined, width, subsequent_indent="  ") or [""])
            buf.clear()

    for line in text.split("\n"):
        if line.startswith("- "):
            flush()
            buf.append(line)
        elif buf and line.startswith("  ") and line.strip():
            buf.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)


def style_guide(p: dict) -> str:
    """The profile as instructions. Numbers only — this is what the generator is shown."""
    line, title, ent = p["line"], p["title"], p.get("entities") or {}
    per = ent.get("per_hunk", {})
    rates = ent.get("rates", {})
    kind = p["kind_mix"]

    def pct(x) -> str:
        return f"{round(100 * (x or 0))}%"

    entity_lines = "\n".join(
        f"- **{name}**: {per[name]} per edit on average; "
        f"{pct(ent['share_of_hunks_naming_one'][name])} of edits name at least one."
        for name in COUNTS if name in per
    ) or "- (not measured — run without `--no-llm` for entity rates)"

    return f"""# Style guide — how this notebook is written

Measured from a real four-week run of the pipeline and reduced to rates. Nothing below is
quoted, paraphrased or summarised from any note: every statement here is a number that came out
of counting, or a rule written from one. This file is what `synth_corpus.py` shows the model
that writes the synthetic vault, and it is the only description of the original it ever sees.

## Shape of a week

- {p['hunks_per_week']['median']:.0f} edits in a typical week, across
  {p['notes_per_week']['median']:.0f} notes — about {p['hunks_per_note']} edits per note.
- {pct(kind['added'])} of edits only add lines, {pct(kind['mixed'])} both add and remove,
  {pct(kind['removed'])} only remove. The remaining {pct(kind['full'])} are notes short enough
  that the reader returns them whole instead of as a diff.
- An edit changes {p['hunk_chars']['median']:.0f} characters at the median, but the
  distribution has a long tail: a quarter run past {p['hunk_chars']['p75']:.0f} and the largest
  in a month runs to {p['hunk_chars']['max']:.0f}. Most edits are small. A few are an evening.
- {pct(p['note_returns'].get('1'))} of notes are touched in one week only.
  {pct(p['note_returns'].get('2'))} come back in a second week,
  {pct(p['note_returns'].get('3', 0)) } in a third,
  {pct(p['note_returns'].get('4', 0))} in all four. The handful that recur are the spine.

## Filenames

- {title['chars']['median']:.0f} characters at the median, up to {title['chars']['max']:.0f}.
- {title['hyphens']['median']:.0f} hyphens at the median and {title['hyphens']['p90']:.0f} at the ninetieth percentile — these are keyword stacks, not
  sentences: a note is named so it can be found again by typing any one of the words in it.
- {pct(title['all_lower_rate'])} are entirely lower case.
  {pct(title['has_digit_rate'])} contain a digit, usually a year.

## Voice

- **Lower case by default.** {pct(line['lower_start_rate'])} of lines that begin with a letter
  begin with a lower-case one. Sentences start in lower case, proper nouns are inconsistently
  capitalised, and a line that begins in upper case is usually one that was pasted in.
- **Typos stay in.** {pct(rates.get('has_typos'))} of edits carry an obvious slip: a missing
  apostrophe, a doubled word, a transposition, a dropped capital. Nobody proofreads a notebook.
  Do not correct them and do not add them evenly — cluster them in the fast, late entries.
- **Other people's words get pasted in.** {pct(rates.get('has_pasted_message'))} of edits hold
  text from somewhere else: a message, an email, part of a chat, a quote. These arrive with
  their own punctuation and capitalisation intact, and are usually introduced by a fragment
  like "from me to X:" or nothing at all.
- **Tags and links.** {pct(rates.get('has_tags'))} of edits use #hashtags or [[wiki links]],
  mid-sentence rather than in a block at the end.
- **Line length** runs to {line['chars']['median']:.0f} characters at the median with a tail to
  {line['chars']['max']:.0f} — a mix of one-line fragments and unbroken paragraphs typed in one
  go. Real line breaks are rare inside a thought.
- **Structure**: {pct(line['bullet_rate'])} of lines are bullets, {pct(line['checkbox_rate'])}
  are checkboxes, {pct(line['heading_rate'])} are headings, {pct(line['url_rate'])} carry a URL.
  Most lines are none of those: they are prose in a file.
- **It is allowed to be dull.** Half of a notebook is logistics — what to buy, who to call,
  what a thing cost, when the thing is. That is not filler, it is the substance.

## Density of names

How many distinct things a typical edit names. This is what makes an entry feel like it belongs
to somebody with a life rather than to nobody:

{entity_lines}

## What the diffs have to look like

The edits are not written as diffs and must not be. A model writes the new prose; the tool
splices it into the note it already had and runs a real unified diff over the before and after.
That is what makes the hunk headers, the line counts and the context lines correct — and every
identifier on the connectome page is a position in that file, so they have to be.

What the generator controls is *where* the change lands, and that is what produces the kind mix
above: new material appended or prepended is an add-only edit, a rewritten passage is a mixed
one, a deleted passage is a remove-only one. Prepending is what a dated journal does; appending
is what a running list does; revising is what a plan does when it changes.
"""


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Measure the real vault's shape, so a synthetic one can imitate it.")
    ap.add_argument("--out", type=Path, default=PROFILE, help=f"Where the profile goes ({PROFILE.name}).")
    ap.add_argument("--hunk-chars", type=int, default=HUNK_CHARS,
                    help=f"Characters of each hunk the shape pass reads (default {HUNK_CHARS}).")
    ap.add_argument("--no-llm", action="store_true",
                    help="Arithmetic only. No API call, no entity rates.")
    ap.add_argument("--no-cache", action="store_true", help="Ask for the shapes again.")
    ap.add_argument("--dry-run", action="store_true", help="Say what would be measured. No call.")
    args = ap.parse_args()

    paths = sorted(SESSIONS.glob("*/microlite.md"))
    if not paths:
        sys.exit(f"No microlite.md under {SESSIONS} — nothing to measure.")
    weeks = load(paths)
    hunks = [h for w in weeks for h in w["hunks"]]
    units = [h["text"][:args.hunk_chars].rstrip() for h in hunks if h["kind"] != "full"]

    print(f"weeks    {len(weeks)}: {', '.join(w['week'] for w in weeks)}")
    print(f"hunks    {len(hunks):,} ({len(units):,} with a diff to measure)")
    if args.dry_run:
        print(f"\nDry run — {0 if args.no_llm else len(units):,} calls would be billed.")
        return

    profile = arithmetic(weeks)
    if rates := entity_rates(units, args.no_llm, args.no_cache):
        profile["entities"] = rates

    profile = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": None if args.no_llm else "claude-haiku-4-5-20251001",
        # Named, not shown: the reader of this file should be able to tell what was measured
        # without the file being a list of anybody's weeks.
        "measured": {"weeks": len(weeks), "hunks": len(hunks), "notes":
                     sum(len(w["notes"]) for w in weeks)},
        **profile,
    }

    SYNTH_ROOT.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, indent=2) + "\n")
    STYLE_GUIDE.write_text(rewrap(style_guide(profile)))
    print(f"\n  wrote  {link(args.out)}")
    print(f"  wrote  {link(STYLE_GUIDE)}")


if __name__ == "__main__":
    main()
