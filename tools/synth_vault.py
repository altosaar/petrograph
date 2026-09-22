#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
synth_vault — the synthetic vault written by hand, and assembled by the same code as the generated one.

`synth_corpus.py` plans four weeks with a model and writes them out. That got the shape right —
the right number of squares, the right mix of added and mixed and removed, filenames a person
would actually type — and it got the content wrong in ways a chart makes very visible. A planner
working note by note has no memory: the footage is on the 4th in six files and the 2nd in a
seventh, the inquest is two years ago in one note and next month in another, and `revise` lands a
paragraph beside a near-identical copy of itself often enough that a reader who clicks a square
at random has a fair chance of landing on one. None of that is fixable by asking better. The
facts have to be held in one place by something that can hold facts.

So this is that place. `synthetic/vault/<note>.md` is one note's whole life: the text it held
before the four weeks began, and then its full text at the end of each week it was touched in.
Nothing here is a diff. The diffs are arithmetic, and `synth_corpus.assemble` already does that
arithmetic with `difflib` — which is the point of importing it rather than re-deriving what a
session directory is. A hunk id is a position in a generated file, and there must be exactly one
piece of code that decides those.

WHAT IS HAND-WRITTEN AND WHAT IS NOT. The notes are, and so are the four openers in
`_context-<date>.md`, because those two are the story. The sleep report and the ledger are still
`_synth_sources.py` arithmetic, seeded and unchanged, so the figures the notes cite are the
figures the attachments actually carry. The letters are still written by Opus from the assembled
week, because whether a read can work out what it is reading is the entire experiment and
handing it the answer would end that.

Usage:
    ./synth_vault.py                 # write synthetic/sessions/ from synthetic/vault/
    ./synth_vault.py --check         # parse and report, write nothing
"""

from __future__ import annotations
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from _synth import (ANCHOR, PROFILE, REFERENCE, SESSIONS, SYNTH_ROOT, WEEKS, compactions, label,
                    week_dates)
from _term import link

sys.path.insert(0, str(Path(__file__).resolve().parent))
from synth_corpus import assemble, full_threshold  # noqa: E402

VAULT = SYNTH_ROOT / "vault"

# `<!-- pre -->` opens the text a note already held when the window opened, and never appears in
# any week; it is the context the first real edit is a change against. A note that begins with a
# week marker instead is a file this person started during the run, and its first hunk is the
# whole thing arriving at once — which is a real kind of edit and worth having a few of.
MARKER = re.compile(r"^<!--\s*(pre|\d{4}-\d{2}-\d{2})\s*-->\s*$", re.MULTILINE)

CONTEXT = "_context-"

# ── the calendar, and getting off it ──────────────────────────────────────────────────────────
#
# The vault is written against `_synth.REFERENCE`, which is a real calendar with real weekdays in
# it — that is what lets a note say "tuesday is gothersgade" and "thursday the offsite" and be
# checkable. What ships has to be dated somewhere near now instead, or a page opened next year
# reads as an archive of a fortnight in a season that has been and gone.
#
# So every date is shifted by a whole number of weeks on the way out, and then written without
# its year. Whole weeks because the weekday has to survive; without the year because a corpus
# that names one starts looking stale the moment the year turns, and because nothing downstream
# needs it — `pretty()` on the page has always thrown the year away.
#
# Three forms go in and one comes out. `2026-02-04`, `**04.02**` and `feb 04` are all the same
# day written three ways, which is how somebody actually writes dates in their own notes, and
# all three leave as `11-20`. The two shorthands carry no year, so they are read against the
# reference year; anything with its own year (the death, the year before) keeps it until the
# shift, and loses it after.
MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DOTTED = re.compile(r"\b(\d{2})\.(\d{2})\b")                    # 04.02 — day first
SHORT = re.compile(r"\b(" + "|".join(MONTHS) + r") (\d{2})\b", re.I)  # feb 04


def shifter(delta: dt.timedelta, ref_year: int):
    """A function that rewrites every date in a passage onto the live calendar, year dropped."""
    def moved(y: int, m: int, d: int) -> str:
        try:
            return f"{dt.date(y, m, d) + delta:%m-%d}"
        except ValueError:                       # 02-30 and friends: not a date, leave it alone
            return ""

    def run(text: str) -> str:
        text = ISO.sub(lambda m: moved(int(m[1]), int(m[2]), int(m[3])) or m[0], text)
        text = DOTTED.sub(lambda m: moved(ref_year, int(m[2]), int(m[1])) or m[0], text)
        text = SHORT.sub(
            lambda m: moved(ref_year, MONTHS[m[1].lower()], int(m[2])) or m[0], text)
        return text
    return run


def states(path: Path) -> dict[str, str]:
    """One vault file → {"pre"|date: the note's full text at that point}, in file order."""
    text = path.read_text()
    marks = list(MARKER.finditer(text))
    if not marks:
        sys.exit(f"{path}: no `<!-- pre -->` or `<!-- YYYY-MM-DD -->` markers in it.")
    out = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip()
        if m.group(1) in out:
            sys.exit(f"{path}: `{m.group(1)}` appears twice.")
        out[m.group(1)] = body
    return out


def edits(dates: list[str], move) -> list[dict]:
    """Every note's states → the (week, note, before, after) list `assemble` writes from.

    The chain is what makes the diffs mean anything: a week's `before` is whatever the note held
    at the end of the last week it was touched in, not the end of the previous week, so a note
    left alone for a fortnight and then returned to produces one diff against what it actually
    said — which is the edit the writer actually made, and the arrow the connectome is for.
    """
    found = []
    for path in sorted(VAULT.glob("*.md")):
        if path.name.startswith(CONTEXT):
            continue
        note, seen = path.name, states(path)
        if bad := [k for k in seen if k != "pre" and k not in dates]:
            sys.exit(f"{path}: {', '.join(bad)} is not one of the run's weeks ({dates[0]}…{dates[-1]}).")
        state = move(seen.get("pre", ""))
        for date in dates:                       # in week order, whatever order the file used
            if date not in seen:
                continue
            after = move(seen[date])
            if after == state:
                sys.exit(f"{path}: {date} is identical to the state before it — that is a "
                         f"note-week that changed nothing, and it would draw no square.")
            found.append({"week": dates.index(date) + 1, "note": note,
                          "before": state, "after": after})
            state = after
    return found


def contexts(pairs: list[tuple[str, str]], move) -> dict[str, str]:
    """The four openers per week — read under the reference date, filed under the live one."""
    want = ("this upcoming week", "tomorrow", "today", "emotionally")
    out = {}
    for ref, live in pairs:
        path = VAULT / f"{CONTEXT}{ref}.md"
        if not path.is_file():
            sys.exit(f"No {path.name} in {VAULT} — every week needs its four openers.")
        text = path.read_text().strip()
        if missing := [w for w in want if not re.search(rf"(?mi)^{w}", text)]:
            sys.exit(f"{path.name}: no paragraph starts with {', '.join(missing)}.")
        out[live] = move(text)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Assemble synthetic/sessions/ from the hand-written vault in synthetic/vault/.")
    ap.add_argument("--end", default=ANCHOR,
                    help="Date of the newest week. The default is computed — about two months "
                         "out, on the reference calendar's weekday — so a corpus regenerated at "
                         "any point lands in the near future rather than in a past spring.")
    ap.add_argument("--weeks", type=int, default=WEEKS, help=f"How many (default {WEEKS}).")
    ap.add_argument("--out", type=Path, default=SESSIONS, help="Where the sessions go.")
    ap.add_argument("--seed", type=int, default=7,
                    help="Seeds the sleep report, the ledger and the day each edit lands on.")
    ap.add_argument("--fresh", action="store_true",
                    help="Throw away the letters too. Default keeps them, which is only right "
                         "when the dates have not moved — see the warning this prints.")
    ap.add_argument("--check", action="store_true", help="Parse and report. Write nothing.")
    args = ap.parse_args()

    if not VAULT.is_dir():
        sys.exit(f"No hand-written vault at {VAULT}.")
    if not PROFILE.is_file():
        sys.exit(f"No profile at {PROFILE} — run: just synth-profile")
    profile = json.loads(PROFILE.read_text())

    # Two calendars. The vault is composed against the reference one and every date in it is
    # absolute there; the run happens on the live one. Week i of the first is week i of the
    # second, and the gap between them is a whole number of weeks by construction.
    ref_dates = week_dates(REFERENCE, args.weeks)
    live_dates = week_dates(args.end, args.weeks)
    delta = dt.date.fromisoformat(live_dates[-1]) - dt.date.fromisoformat(ref_dates[-1])
    if delta.days % 7:
        sys.exit(f"--end {args.end} is {delta.days} days from the reference, which is not a whole "
                 "number of weeks. Every weekday written into the vault would come out wrong.")
    move = shifter(delta, dt.date.fromisoformat(REFERENCE).year)
    pairs = list(zip(ref_dates, live_dates))
    # The Friday each week is counted to, and the day it was actually read — `_synth.HELD`.
    held = compactions(live_dates)

    found = edits(ref_dates, move)
    notes = {e["note"] for e in found}
    print(f"vault    {len(notes)} notes · {len(found)} note-weeks")
    print(f"shift    {ref_dates[-1]} → {live_dates[-1]}  ({delta.days // 7} weeks, "
          f"{dt.date.fromisoformat(live_dates[-1]):%A} either end)")
    for ref, live in pairs:
        n = sum(1 for e in found if ref_dates[e["week"] - 1] == ref)
        print(f"  {ref} → {label(held[live])} {dt.date.fromisoformat(held[live]):%a}  {n:>3} edits")
    threshold = full_threshold(found, profile["kind_mix"]["full"])
    print(f"whole    notes under {threshold:,} chars come back entire, not as a diff")

    if args.check:
        print("\nCheck only — nothing written.")
        return

    # A letter is written from a dated week. If the dates have moved under one, it is describing
    # a calendar that no longer exists — so say so rather than quietly keeping it.
    stale = [p for p in args.out.glob("*/lifelog-*.md")
             if p.is_file() and p.parent.name not in {label(held[d]) for d in live_dates}]
    if stale and not args.fresh:
        print(f"\nnote     {len(stale)} letters sit under week folders this run does not write.\n"
              "         They were read from a different calendar. Re-run `just synth-letters "
              "--force`.")

    # The cast is the story's, and the payees are the short names rather than the descriptions
    # the roster carries for the writer's benefit. A ledger line reading "Ursa — the coffee place
    # on the corner of Strandgade. Open at six." is not a payee, it is a stage direction.
    import _story
    plan = {"roster": {"organizations": ["Elsinore Group", "Norvik", "Wittenberg", "Bispebjerg",
                                         "Fen & Co", "Kronborg Tower"],
                       "places": ["Ursa", "Roskilde", "Nordhavn", "Gothersgade Fencing Hall",
                                  "Strandgade", "Helsingør"]},
            "threads": [], "edits": found, "contexts": contexts(pairs, move)}
    assert _story.NAME == "hamlet"

    written, threshold = assemble(plan, live_dates, profile, args.seed, args.out,
                                  keep_letters=not args.fresh, threshold=threshold,
                                  keys=dict(zip(live_dates, ref_dates)), held=held)
    print()
    for session in written:
        print(f"  wrote  {link(session)}")
    print("\nNext:  just synth-letters              (opus reads the newest week, and only that one)")


if __name__ == "__main__":
    main()
