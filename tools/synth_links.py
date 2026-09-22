#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["baml-bridge==0.17.0", "pydantic>=2"]
# ///
"""
synth_links — the arrows that reach back, authored on purpose.

The connectome exists to show one thing: that two edits made a fortnight apart, in notes that
share no word, turn out to be about the same thing. The generated corpus was not producing them.
Four weeks, a hundred and sixty squares, thirty-five connections — and exactly ONE of those
crossed a week boundary. Everything else stayed inside the column it started in.

The cause is not a bug, it is the planner working as asked. `MakeThreads` writes each beat to
stand on its own, so week one's beat and week four's beat on the same note are both complete and
share no phrase. A reader can see they are the same subject. Nothing in either says so, and
`hunk_connections.py` only reports a pair when a sentence of the letter names both ends — which
is the right rule, since the evidence for every arrow on that page is a sentence you can read.

So the reaching-back is planted, in the two places it has to exist at once:

  · in the CURRENT week's note, a callback that picks the earlier note up by its own specifics —
    the figure, the date, the decision that is actually written there. Two notes about "cycling"
    are not connected; two notes where one says the service cost 127 dollars and the other says
    it has now cost 127 twice are.
  · in the LETTER, one sentence under its open loops, naming both ends. Without it the notes
    match and no arrow is drawn, because nothing reported the match.

WHICH PAIRS. Chosen by arithmetic, not by a model. Every current-week note is scored against
every earlier note on how much uncommon vocabulary they share, and the best-scoring pairs win,
spread so no note is used twice before every note has been used once. Overlap of real words is a
better signal for "these are about the same thing" than a topic label is, it costs nothing, and
it is inspectable — `--dry-run` prints the pairs and their scores before anything is written.
The model's job is the writing, which is the part arithmetic cannot do.

The open-loops section is in register rather than bolted on: the ACT prompt asks the read for
"logistics or operations in the upcoming days such as summarizing any open loops", so a letter
that ends by naming the threads still running is the document doing what it was asked.

Idempotent. Re-running replaces its own section in the letter rather than appending a second
one, and re-assembles the vault from the same plan, so the ids on the chart do not move.

Usage:
    ./synth_links.py --dry-run          # the pairs it would plant, with their scores
    ./synth_links.py                    # plant them, then: just synth-connections
    ./synth_links.py --per-week 6
"""

from __future__ import annotations
import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

from _cache import cache_path, write_atomic
from _env import REPO_ROOT, api_key
from _session import compaction
from _synth import ANCHOR, PROFILE, SESSIONS, STYLE_GUIDE, SYNTH_ROOT, WEEKS, week_dates
from _term import link, say
from synth_corpus import CACHE_DIR, PROMPT, assemble, full_threshold, generate

# The section the sentences land in. Matched to be replaced, so running this twice does not
# leave the letter with two of them.
HEADING = "## Open loops still running"

# What the vault had before anything was planted into it, per earlier week. Written once and
# read every time after, because this stage is idempotent — it rebuilds the notes and the
# letter's section from scratch on each run — and so the number it is aiming at has to be the
# untouched one. Measuring "what is there now" instead made each run target its own last
# result, which is a ratchet rather than a target.
BASELINE = SYNTH_ROOT / "links-baseline.json"

# How much of a note the writer of the callback is shown. Enough to pick up a real detail from;
# a whole long note would crowd out the one it is being asked to connect to.
UNIT_CHARS = 900

# Words that say nothing about what a note is about. Short words are dropped by length; these
# are the long ones that are still noise.
STOP = {
    "about", "after", "again", "already", "another", "because", "before", "being", "between",
    "could", "doing", "during", "either", "enough", "every", "first", "getting", "going",
    "instead", "might", "moment", "months", "morning", "night", "other", "probably", "really",
    "right", "should", "since", "something", "still", "their", "there", "these", "thing",
    "things", "think", "those", "though", "through", "today", "tomorrow", "tonight", "until",
    "week", "weeks", "where", "which", "while", "would", "yesterday",
}

WORD = re.compile(r"[a-z][a-z'-]{4,}|\d[\d.,]*")


def changed(edit: dict) -> str:
    """What this week actually wrote into the note — which is what a hunk on the chart IS.

    Scoring the note as it stands was the first attempt and it was wrong in a way that looked
    right: a note's content is cumulative, so week one's text is a subset of week four's, and
    every note matched its own earlier self at 1.000 while every genuine cross-note thread
    scored below the noise. `hunk_connections.py` is shown diffs, not notes, so the diff is what
    two units have to share.
    """
    was = set(edit["before"].splitlines())
    return "\n".join(l for l in edit["after"].splitlines() if l.strip() and l not in was)


def terms(text: str) -> set[str]:
    """The uncommon words and the figures in a note — what makes two notes recognisably one thread."""
    return {w for w in WORD.findall(text.lower()) if w not in STOP}


def score(a: str, b: str) -> float:
    """How much two notes share, as a fraction of what they say between them.

    Jaccard rather than a raw count, so a long note does not outrank a short one merely by
    having more words in it. Figures count as terms and are worth the most in practice: two
    notes that name the same amount of money are almost always the same thread.
    """
    x, y = terms(a), terms(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def unit(edit: dict, date: str) -> str:
    """One unit as the writer of a callback is shown it: where it is, and what it added."""
    body = changed(edit)[:UNIT_CHARS].rstrip()
    return f"[{date} · {edit['note']} · topic {edit['topic']}]\n{body}"


def choose(plan: dict, dates: list[str], per_week: dict) -> list[tuple[str, dict, dict, float]]:
    """The pairs worth planting: for each earlier week, its best matches to the current one.

    Spread deliberately. A note that matches everything would otherwise take every slot in a
    week and put four arrows on one square, which says less than four arrows on four squares.
    Each end is used once per week before any end is used twice.
    """
    current = [e for e in plan["edits"] if e["week"] == len(dates)]
    chosen = []
    for w, date in enumerate(dates[:-1], 1):
        earlier = [e for e in plan["edits"] if e["week"] == w]
        ranked = sorted(
            ((score(changed(a), changed(b)), a, b) for a in current for b in earlier),
            key=lambda t: -t[0])
        used_cur: set[str] = set()
        used_old: set[str] = set()
        taken, want = 0, per_week.get(date, 0)
        for s, a, b in ranked:
            if s <= 0 or taken >= want:
                break
            if a["note"] in used_cur or b["note"] in used_old:
                continue
            used_cur.add(a["note"])
            used_old.add(b["note"])
            chosen.append((date, a, b, s))
            taken += 1
    return chosen


def existing_inter(session: Path, dates: list[str]) -> dict:
    """How many connections already cross out of the current week, and into which week."""
    found = sorted(session.glob("connections-*.json"))
    if not found:
        return {}
    data = json.loads(found[-1].read_text())
    cur = data["week"]
    out = {d: 0 for d in dates[:-1]}
    for c in data["connections"]:
        ends = {c["from"].split("/")[0] if "/" in c["from"] else cur,
                c["to"].split("/")[0] if "/" in c["to"] else cur}
        if len(ends) == 2 and cur in ends:
            other = (ends - {cur}).pop()
            if other in out:
                out[other] += 1
    out["_total"] = len(data["connections"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Plant long-range links so the connectome has arrows that cross a week.")
    ap.add_argument("--end", default=ANCHOR, help=f"Date of the newest week (default {ANCHOR}).")
    ap.add_argument("--weeks", type=int, default=WEEKS, help=f"How many weeks (default {WEEKS}).")
    ap.add_argument("--seed", type=int, default=7, help="Seed for every non-model choice.")
    ap.add_argument("--per-week", type=int,
                    help="Links to plant back into each earlier week (default: from the target).")
    ap.add_argument("--target", type=float, default=0.30,
                    help="Growth in total connections to aim for (default 0.30).")
    ap.add_argument("--out", type=Path, default=SESSIONS, help="Where the sessions are.")
    ap.add_argument("--rebaseline", action="store_true",
                    help="Take the current connections as the untouched baseline and record "
                         "them. Only right when the letter has no planted section in it.")
    ap.add_argument("--no-cache", action="store_true", help="Write the links again.")
    ap.add_argument("--dry-run", action="store_true", help="Print the pairs. No call, no write.")
    args = ap.parse_args()

    # Gitignored measurements of the real vault, written together by `just synth-profile`; see
    # synth_corpus.py for why a clone starts without them.
    for needed in (PROFILE, STYLE_GUIDE):
        if not needed.is_file():
            sys.exit(f"No {needed.name} at {needed} — run: just synth-profile")
    profile = json.loads(PROFILE.read_text())
    style = STYLE_GUIDE.read_text()
    dates = week_dates(args.end, args.weeks)
    session = args.out / dates[-1]

    before = existing_inter(session, dates)
    total = before.pop("_total", 0)
    # Evenly across the earlier weeks, as asked. The target is a share of ALL the connections
    # rather than of the inter-week ones, because there is essentially no inter-week baseline to
    # grow — one arrow, into one week — and thirty per cent of one is not a plan.
    # Not every planted link is reported back: the connections pass quotes a letter sentence
    # only when it can resolve both ends to ids, and about two in five make it. Measured across
    # runs, and used to size the planting rather than guessed at.
    YIELD = 0.4

    if not total and not args.per_week:
        sys.exit(f"No connections-*.json in {session} to measure against.\n"
                 "  The target is a share of what is already there, so there has to be a\n"
                 "  baseline first:  just synth-connections\n"
                 "  ...or say how many to plant outright, with --per-week.")
    earlier = dates[:-1]
    if BASELINE.is_file() and not args.rebaseline:
        have = {d: json.loads(BASELINE.read_text()).get(d, 0) for d in earlier}
    else:
        have = {d: before.get(d, 0) for d in earlier}
        BASELINE.write_text(json.dumps(have, indent=2) + "\n")

    # Thirty per cent of the connections that CROSS a week, which is the set being asked about —
    # not of every connection on the chart. Taking the share of the total was the first attempt
    # and it overshot by a factor of three, because the crossing ones are a quarter of the whole.
    #
    # Evenly spread means levelling, not adding the same number to each. The three earlier weeks
    # do not start equal — this corpus opened at 1, 8 and 4 — so a flat increment preserves
    # exactly the lopsidedness it was asked to remove. One shared number is what each week is
    # brought up to, and a week already past it is left alone; nothing here can remove an arrow.
    even = math.ceil(sum(have.values()) * (1 + args.target) / len(earlier))
    per_week = ({d: args.per_week for d in earlier} if args.per_week else
                {d: min(14, math.ceil(max(0, even - have[d]) / YIELD)) for d in earlier})

    say(f"==> {total} connections now · {sum(before.values())} of them cross a week")
    say(f"    baseline before any planting: "
        + ", ".join(f"{d} {have[d]}" for d in earlier) + f" ({sum(have.values())} crossing)")
    say(f"    +{args.target:.0%} on those, levelled: {even} into each earlier week")
    say("    planting " + ", ".join(f"{per_week[d]} into {d}" for d in earlier)
        + f" (about {YIELD:.0%} of a planted link is reported back)\n")

    # Free: every call this replays is already on disk from `just synth-corpus`.
    plan = generate(profile, style, dates, args.seed, False, False, 8)
    # Held before a single callback is added, and handed back to `assemble` afterwards. See the
    # note there: recomputing it would re-render the three weeks this stage does not touch.
    keep = full_threshold(plan["edits"], profile["kind_mix"]["full"])
    pairs = choose(plan, dates, per_week)

    say(f"\n{len(pairs)} pairs chosen by shared vocabulary:")
    for date, a, b, s in pairs:
        say(f"  {s:.3f}  {a['note']}  ←  {date} · {b['note']}")
    if args.dry_run:
        say("\nDry run — no API call made, nothing billed.")
        return

    os.environ["ANTHROPIC_API_KEY"] = api_key("ANTHROPIC_API_KEY")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from baml_sdk import PlantLink
    except ImportError as exc:  # pragma: no cover - a setup failure, not a runtime one
        sys.exit(f"Could not import the generated BAML client ({exc}).\nRun: just baml")

    base = {"prompt": PROMPT.read_text(errors="replace"), "style": style}
    sentences, planted, skipped = [], 0, 0
    say("")
    for date, a, b, _s in pairs:
        earlier, current = unit(b, date), unit(a, dates[-1])

        def produce(earlier=earlier, current=current) -> dict | None:
            got = PlantLink(earlier=earlier, current=current, style=style)
            # The model is given an explicit way to refuse, and refusing is the right answer
            # often enough that taking it away would buy links nobody should believe.
            if "SKIP" in got.callback.upper() and len(got.callback) < 40:
                return None
            return {"callback": got.callback.strip(), "sentence": got.sentence.strip()}

        key = cache_path(CACHE_DIR, {**base, "fn": "PlantLink",
                                     "earlier": earlier, "current": current}, ".json")
        if key.exists() and not args.no_cache:
            got = json.loads(key.read_text())
        else:
            got = produce()
            write_atomic(key, json.dumps(got, ensure_ascii=False).encode())
        if got is None:
            skipped += 1
            say(f"  skip   {a['note']}  ←  {date} · {b['note']}")
            continue
        # Into the note, where it becomes added lines in that week's diff.
        a["after"] = a["after"].rstrip() + "\n\n" + got["callback"]
        sentences.append(got["sentence"])
        planted += 1
        say(f"  plant  {a['note']}  ←  {date} · {b['note']}")

    if not planted:
        sys.exit("\nNothing planted — every pair was refused. Try --per-week with a larger number.")

    written, threshold = assemble(plan, dates, profile, args.seed, args.out, threshold=keep)

    # And into the letter, which is where an arrow's evidence has to be readable.
    letter = session / compaction(session)
    text = letter.read_text()
    text = re.split(rf"^{re.escape(HEADING)}\s*$", text, flags=re.MULTILINE)[0].rstrip()
    body = "\n".join(f"- {s}" for s in sentences)
    letter.write_text(f"{text}\n\n{HEADING}\n\n"
                      f"Threads that started before this week and are still going:\n\n{body}\n")

    say(f"\n{planted} links planted, {skipped} refused · vault re-assembled "
        f"(whole notes under {threshold:,} chars)")
    say(f"  wrote  {link(letter)}")
    say("\nNext:  just synth-connections     (re-read the letter against the hunks)")


if __name__ == "__main__":
    main()
