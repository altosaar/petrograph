#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["baml-bridge==0.17.0", "pydantic>=2"]
# ///
"""
hunk_connections — the connections a week's letter drew between hunks, made explicit.

The vault has no links in it. A hunk is one atomic edit to one note, the writer never tags them
and never rereads them, and the waffle chart draws each one as a square that knows nothing about
any other square. What the chart cannot show is the thing the practice is actually for: that
edits made days apart, in notes that share no word, turn out to be about the same thing.

The letter already knows. It is written from those same hunks, and its prose does the connecting
— one clause naming a note from Monday and a night from Thursday as the same subject. So rather
than ask a model to find connections from scratch, this reads the letter back against the hunks
it came from and asks only which pairs a sentence joined. The evidence is a sentence the week
already produced, which is why every arrow on the chart can be traced to one.

Each end of a connection carries its own rationale. One rationale for the pair kept coming back
vague about which half was which — a figure named without saying which unit it came from, a
clause that could have described either end — so the schema asks for one about `from_unit`
alone and one about `to_unit` alone. Splitting the field is what made the answers specific.

The model is Haiku, and the contract is BAML's: `baml_src/connections.baml` declares the return
type, so the answer arrives as typed objects rather than as JSON to be trusted. Ids are checked
here on top of that — both must resolve to real hunks and must differ — because a type system
can promise a string and not that the string names something.

Whether a connection is intra-week or inter-week is computed, never asked. Every id carries its
own week (`2026-06-06/12`, see `_hunks.py`), so it is arithmetic; asking the model would only
produce a second answer that can disagree with the first.

The answer is cached on disk under `share/.baml-cache`, keyed by the letter, the hunks, the
context and the text of `baml_src/connections.baml` itself — so re-running costs nothing, and
editing the prompt or the window correctly misses. A hit needs neither the API key nor the
generated client, which is why the cache is checked before either is reached for. `--no-cache`
buys a fresh answer.

Weeks are included when they have both hunks and a written letter — a session whose letter is
still the stub has nothing to draw connections from, and one with no microlite has no squares.
The letter that gets *read* is one week's (`--week`, default the most recent); the hunks it may
reach back into are every included week's, which is what lets an arrow cross a column.

Usage:
    ./hunk_connections.py                      # the most recent week's letter
    ./hunk_connections.py --week 2026-05-29
    ./hunk_connections.py --dry-run            # sizes and the hunk list, no API call
"""

from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from typing import NamedTuple
from pathlib import Path

from _cache import cache_path, write_atomic
from _env import REPO_ROOT, api_key
from _hunks import by_id, load
from _session import INPUT, STUB_MARKER, compaction
from _term import link

SESSIONS = Path(os.environ.get("PETROGRAPH_OUT") or REPO_ROOT).expanduser().resolve() / "sessions"


class Found(NamedTuple):
    """One connection as the model gave it, before this file has an opinion about it.

    The same four fields BAML returns, so the code below reads one shape whether the answer
    came from the API a moment ago or from disk a week ago.
    """
    sentence: str
    from_unit: str
    to_unit: str
    from_rationale: str
    to_rationale: str
    strength: int

# Beside the TTS chunk cache, under the directory the gitignore already covers whole. A model
# call is the same kind of thing as a synthesised chunk: paid for once, identical every time
# the inputs are, and pointless to buy twice.
CACHE_DIR = REPO_ROOT / "share" / ".baml-cache"
PROMPT = REPO_ROOT / "baml_src" / "connections.baml"

# How much of a hunk's body the model is shown. Enough to tell what the edit was about; a whole
# week of untruncated diffs is mostly context lines, and they crowd out the letter.
HUNK_CHARS = 420

# How much of the LETTER goes into one call. Asked for a whole letter at once, the pass is not
# wrong so much as unrepeatable: the same corpus and the same prompt returned 61 connections,
# then 54, then 24, and one week's count swung from 8 to 0 without a single word changing on that
# week's side. A model given fifteen thousand characters and told "report every sentence that
# connects two units" reports the ones it noticed, and which ones it notices is a coin toss.
#
# Handed a quarter of the letter it reports nearly all of them, because the instruction is now
# proportionate to the text. Four or five calls instead of one, each cached on its own, and the
# answers are merged — a sentence that names two units is a connection whichever chunk it
# arrived in, and a pair found twice is one arrow either way.
LETTER_CHARS = 4000

# Below this many characters a quotation is not evidence. Every connection claims that one
# sentence of the letter joined two units, and the sentence is what the page puts on screen to
# prove it — so a four-word imperative or a bare "that is why" are not weak connections, they
# are connections whose evidence was left behind in the paragraph they came out of. The model is
# right that those paragraphs join two things; it has quoted the wrong six words of them. Forty
# is where the fragments stop and the shortest sentence that can carry a claim on its own begins,
# and it is a flag because that is a judgement.
MIN_SENTENCE = 40


def written_letter(session: Path) -> Path | None:
    """The session's letter, if it holds one — a stub is a file, not a letter."""
    letter = session / compaction(session)
    if not letter.is_file():
        return None
    return None if STUB_MARKER in letter.read_text(errors="replace") else letter


def qualifying(sessions: Path) -> list[Path]:
    """Sessions with hunks to draw. Only the week being read needs a letter: the weeks before it
    are what that letter is read against, and the synthetic corpus writes exactly one."""
    return [d for d in sorted(sessions.glob("*/")) if (d / "microlite.md").is_file()]


def hunk_list(weeks: list[dict], chars: int) -> str:
    """Every hunk, one block each, id first. This is the only place ids reach the model."""
    out = []
    for w in weeks:
        for h in w["hunks"]:
            body = h["text"][:chars].rstrip()
            if len(h["text"]) > chars:
                body += "\n…"
            # The date leads, before the title. A note is edited across many weeks and every
            # one of those is a separate unit with different content; with only the title on
            # the line the model had to reconstruct the week from the id to tell them apart,
            # and a connection between two edits of one note is exactly what is worth finding.
            out.append(f"[{h['id']}] {h['week']} · {h['note']} · "
                       f"{h['kind']} +{h['add']} −{h['dele']}\n"
                       f"{h['head']}\n{body}".rstrip())
    return "\n\n".join(out)


# The punctuation an id can arrive wearing. The hunk list presents every id inside brackets —
# `[2026-06-06/12] 2026-06-06 · note.md · …` — because that is what makes it findable at the
# start of a block, and a model asked to copy the id sometimes copies the brackets with it. That
# is not a hallucinated id, it is the right id with punctuation on, and dropping it costs a real
# arrow: one run came back with every single connection bracketed and the chart resolved none of
# them. Stripped here rather than in the prompt, because a prompt can be asked and this can be
# guaranteed.
STRIP = " \t`'\"[](),.;:"


def resolve(raw: str, units: dict) -> str:
    """A model's id as the id it meant. Exact match first; the rest is only tried if it fails."""
    if raw in units:
        return raw
    bare = (raw or "").strip().strip(STRIP).strip()
    if bare in units:
        return bare
    # Sometimes the whole header line comes back rather than the id at the front of it —
    # "[2026-02-13/0] 2026-02-13 · anniversary-film-shooting-notes.md". The id is the first token
    # and everything after it is the label the list printed beside it, so taking the token is not
    # a guess. Checked against the unit list either way, so a wrong one still costs its arrow.
    head = bare.split()[0].strip(STRIP) if bare.split() else ""
    return head if head in units else raw


# The other half of the same idea, applied to the evidence rather than to the ids. A connection's
# whole claim is that the LETTER joined these two units, and the sentence is what is put on screen
# to prove it — so a sentence the letter does not contain is not weak evidence, it is a different
# document's evidence. It happens: the hunks are first-person notes and the letter quotes them, so
# a model reading the two side by side will sometimes hand back a line from the vault and score it
# 5. Measured on a real week this drops nothing at all (70 of 70 verified); on a synthetic vault
# whose notes and letter share a voice it dropped two thirds.
#
# Compared as word sequences with everything else thrown away, because the letter is markdown and
# the quote is not. "| **Lisbon trip** | 28–31 March | Nothing to do this week. |" comes back as
# "Lisbon trip — 28–31 March — Nothing to do this week." — the same sentence, re-punctuated out of
# a table, and a literal match would drop it. Those table rows are the open loops, which are the
# cross-week connections, which are the ones worth most here.
def words(text: str) -> str:
    """Text as a padded sequence of bare words, so one can be searched for inside another."""
    return " " + " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", text or "").lower())) + " "


def chunks(letter: str, size: int) -> list[str]:
    """The letter in readable pieces, split between paragraphs and never inside one.

    Paragraph boundaries rather than a fixed cut, because the unit of evidence here is a
    sentence quoted verbatim: a chunk that ends mid-sentence can only produce a quote that
    matches nothing in the file it came from.
    """
    out: list[str] = []
    for para in letter.split("\n\n"):
        if out and len(out[-1]) + len(para) + 2 <= size:
            out[-1] = f"{out[-1]}\n\n{para}"
        else:
            out.append(para)
    return out or [letter]


def sources(session: Path) -> list[dict]:
    """The attachments the letter was actually written from — the sleep report, the ledger.

    These are units, not just background. The letter reads a note against the body and against
    the ledger all the time — a night's sleep figure set beside what was written that day — and a
    model that can only name hunks has to either drop that sentence or point at a hunk that is
    only half of what it meant. So each one gets an id — its filename — and can stand at either end of a connection.

    Read off the session's own `attachments:` key rather than by globbing the folder, and the
    difference is not pedantry. A session directory also holds a week of browsing history,
    which the providers wrote there but which `weekly_review.py` never put in front of Claude.
    Globbing would hand the most revealing file in the repo to an API that never saw it the
    first time, and would quietly answer a different question than "what did the letter know".
    """
    edit = session / INPUT
    if not edit.is_file():
        return []
    declared = ""
    for line in edit.read_text(errors="replace").splitlines():
        if line.startswith("attachments:"):
            declared = line.split(":", 1)[1].split("#")[0]
            break
    out = []
    for name in (n.strip() for n in declared.split(",")):
        path = Path(name)
        if not name or not path.is_file():
            continue
        out.append({"id": path.name, "text": path.read_text(errors="replace")})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Connections between hunks, found by Haiku in the week's own letter.")
    ap.add_argument("--week", help="Session date whose letter is read (default: the most recent).")
    ap.add_argument("--out", type=Path, help="Where the JSON goes (default: in the session).")
    ap.add_argument("--weeks", type=int, default=0, metavar="N",
                    help="Read against the last N calendar weeks, ending on the letter's own — "
                         "4 is a month. A session counts if its whole seven days fall inside "
                         "that span, so an irregular one that overlaps its neighbour (a session "
                         "three days after another) is left out rather than read twice. Default 0 "
                         "keeps every session with hunks, up to and including the letter's own — "
                         "only that one needs a letter.A week after the letter's is never included "
                         "either way: a letter cannot have read a note not yet written.")
    ap.add_argument("--hunk-chars", type=int, action="append", metavar="N",
                    help=f"Characters of each hunk shown to the model (default {HUNK_CHARS}). "
                         "Repeat to read the letter against the hunks at several lengths and "
                         "pool what the readings found, the way the passes over the letter are "
                         "pooled. Each reading is cached on its own.")
    ap.add_argument("--min-sentence", type=int, default=MIN_SENTENCE,
                    help=f"Shortest quotation that counts as evidence (default {MIN_SENTENCE} "
                         "characters). A connection is a claim that one sentence joined two "
                         "units, and a six-word fragment cannot name two of anything — see "
                         "MIN_SENTENCE. 0 keeps everything the model returned.")
    ap.add_argument("--letter-chars", type=int, default=LETTER_CHARS,
                    help=f"Characters of the letter read per pass (default {LETTER_CHARS}). "
                         "One pass over a whole letter reports a different subset every time; "
                         "several smaller ones report nearly all of it, repeatably.")
    ap.add_argument("--no-cache", action="store_true",
                    help="Ask the model again even if this exact call is already on disk.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build the inputs and report their size. No API call, nothing billed.")
    args = ap.parse_args()

    sessions = qualifying(SESSIONS)
    lettered = [d.name for d in sessions if written_letter(d)]
    if not lettered:
        sys.exit(f"No session under {SESSIONS} has both a microlite.md and a written letter.")

    weeks = load([d / "microlite.md" for d in sessions])
    # The newest week that has been READ, not the newest week: one with hunks and no letter yet
    # can still be read against, but there is nothing in it to read.
    target = args.week or lettered[-1]
    # The letter's week and the ones before it, never after. With --weeks, a calendar window:
    # a session covers the seven days ending on its date, so for the whole of it to sit inside
    # N weeks ending on the target its date has to be no more than N-1 weeks earlier. Counting
    # sessions instead would be right only while they happen to be a week apart, and they are
    # not always. A yearless synthetic week has no calendar to measure, so it is counted.
    upto = [w for w in weeks if w["week"] <= target]
    if args.weeks and len(target) == 10:
        first = (dt.date.fromisoformat(target) - dt.timedelta(days=7 * (args.weeks - 1))).isoformat()
        upto = [w for w in upto if w["week"] >= first]
    elif args.weeks:
        upto = upto[-args.weeks:]
    weeks = upto
    hunks = by_id(weeks)
    session = SESSIONS / target
    letter = written_letter(session)
    if letter is None:
        sys.exit(f"{target} has no written letter — nothing to read connections out of.\n"
                 f"Weeks that do: {', '.join(w['week'] for w in weeks)}")

    letter_text = letter.read_text(errors="replace")
    # One reading per hunk length. How much of each hunk the model is shown changes which
    # square it pins a sentence on: a short excerpt cuts off the half of a long entry that a
    # sentence quotes, a long one buries the letter under the notes. Neither is right every
    # time, so a corpus can be read at two lengths and the answers pooled — the same move as
    # reading the letter in passes, one dimension over.
    hunk_chars = args.hunk_chars or [HUNK_CHARS]
    readings = [(n, hunk_list(weeks, n)) for n in hunk_chars]
    week_sources = sources(session)
    # The id goes in brackets ahead of the content, the same shape the hunk list uses, because
    # the model is asked to copy ids out of both lists and they should read as one habit.
    context_text = "\n\n".join(f"[{src['id']}]\n{src['text']}" for src in week_sources) or "(none)"
    size = len(letter_text) + max(len(t) for _, t in readings) + len(context_text)

    # Everything that decides the answer, and nothing that does not. The prompt is in the key
    # by its own source text, so editing `connections.baml` misses the way it should — a cache
    # that survived a prompt change would hand back the old prompt's answer as if it were new.
    pieces = chunks(letter_text, args.letter_chars)
    jobs = [(piece, text, cache_path(CACHE_DIR, {
        "fn": "ConnectUnits",
        "prompt": PROMPT.read_text(errors="replace") if PROMPT.is_file() else "",
        "letter": piece,
        "hunks": text,
        "context": context_text,
    }, ".json")) for _, text in readings for piece in pieces]
    keys = [k for _, _, k in jobs]
    cached = all(k.exists() for k in keys) and not args.no_cache

    print(f"letter   {letter.name}  {len(letter_text):,} chars")
    for n, text in readings:
        print(f"hunks    {len(hunks):,} across {len(weeks)} weeks  {len(text):,} chars "
              f"(≤{n:,} each)")
    print(f"sources  {len(week_sources)}: {', '.join(x['id'] for x in week_sources) or 'none'}"
          f"  {len(context_text):,} chars")
    print(f"total    {size:,} chars  ≈ {size // 4:,} tokens")
    print(f"letter   read in {len(pieces)} passes of ≤{args.letter_chars:,} chars"
          + (f", in {len(readings)} readings" if len(readings) > 1 else ""))
    print(f"cache    {sum(k.exists() for k in keys)}/{len(keys)} passes already on disk")

    if args.dry_run:
        print("\nDry run — no API call made, nothing billed.")
        return

    if cached:
        # A hit needs neither the key nor the generated client, which is the point of checking
        # here rather than inside the call: a re-run costs an import it never has to make.
        print("\nfrom cache — nothing billed.")
        found = [Found(**c) for k in keys for c in json.loads(k.read_text())]
    else:
        # The key is read here rather than left to the SDK so a missing one fails before the
        # import cost and says which variable, the way every other tool in this repo does.
        os.environ["ANTHROPIC_API_KEY"] = api_key("ANTHROPIC_API_KEY")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            from baml_sdk import ConnectUnits
        except ImportError as exc:  # pragma: no cover - a setup failure, not a runtime one
            sys.exit(f"Could not import the generated BAML client ({exc}).\nRun: just baml")

        print()
        found = []
        for n, (piece, text, k) in enumerate(jobs, 1):
            if k.exists() and not args.no_cache:
                got = json.loads(k.read_text())
            else:
                print(f"asking haiku… {n}/{len(jobs)}")
                answers = ConnectUnits(letter=piece, hunks=text, context=context_text)
                got = [{"sentence": c.sentence, "from_unit": c.from_unit, "to_unit": c.to_unit,
                        "from_rationale": c.from_rationale, "to_rationale": c.to_rationale,
                        "strength": int(c.strength)} for c in answers]
                # Written before anything is validated or dropped, so the cache holds what the
                # model actually said. Validation is this file's opinion and may change; the
                # answer will not.
                write_atomic(k, json.dumps(got, ensure_ascii=False).encode())
            found += [Found(**c) for c in got]

    # Strongest first, so when two readings (or two passes) report the same sentence joining the
    # same pair, the stronger one is the one kept. Stable, so equal strengths keep their order.
    found.sort(key=lambda c: -int(c.strength))

    # What an id may name: any hunk, or any of this week's sources. A source has no diff and
    # no ordinal — only a name and the week it belongs to, which is all the rest of this needs.
    units = {**hunks, **{src["id"]: {"week": target, "note": src["id"]} for src in week_sources}}
    letter_words = words(letter_text)
    kept, dropped, misquoted, fragments, seen = [], [], [], [], set()
    for c in found:
        from_id, to_id = resolve(c.from_unit, units), resolve(c.to_unit, units)
        a, b = units.get(from_id), units.get(to_id)
        # A type system can promise a string, not that the string names a hunk. An id that does
        # not resolve costs its arrow rather than putting a dangling one on the chart.
        if a is None or b is None or from_id == to_id:
            dropped.append(c)
            continue
        # And a sentence the letter does not contain is somebody else's evidence. See words().
        if (said := words(c.sentence)) == "  " or said not in letter_words:
            misquoted.append(c)
            continue
        if len(c.sentence.strip()) < args.min_sentence:
            fragments.append(c)
            continue
        # Chunks overlap in what they can see, so one sentence can come back twice. The same
        # sentence joining the same pair is one connection however many passes reported it.
        if (fingerprint := (c.sentence.strip(), from_id, to_id)) in seen:
            continue
        seen.add(fingerprint)
        kept.append({
            "sentence": c.sentence, "strength": int(c.strength),
            "from": from_id, "from_rationale": c.from_rationale,
            "to": to_id, "to_rationale": c.to_rationale,
            # Computed, not asked for: the ids already say which weeks these are.
            "kind": "intra" if a["week"] == b["week"] else "inter",
        })

    out = args.out or session / f"connections-{target}.json"
    out.write_text(json.dumps({
        "week": target,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": "claude-haiku-4-5-20251001",
        "weeks": [w["week"] for w in weeks],
        "hunk_chars": hunk_chars,
        # The sources travel with the answer: the page draws a square for each one, and the
        # session's attachments could change under it otherwise.
        "sources": week_sources,
        "connections": kept,
    }, ensure_ascii=False, indent=2))

    intra = sum(1 for c in kept if c["kind"] == "intra")
    print(f"\n{len(kept):,} connections · {intra} within a week · {len(kept) - intra} across weeks")
    if dropped:
        print(f"\n{len(dropped)} dropped — the id names no unit:")
        for c in dropped:
            print(f"  {c.from_unit} → {c.to_unit}")
    if fragments:
        print(f"\n{len(fragments)} dropped — the quotation is under {args.min_sentence} "
              f"characters and names nothing:")
        for c in fragments[:6]:
            print(f"  {c.sentence.strip()!r}")
        if len(fragments) > 6:
            print(f"  … and {len(fragments) - 6} more")
    if misquoted:
        # Worth printing rather than counting quietly. A handful is the model rounding a quote
        # off; most of the answer is the letter and the hunks sounding alike enough that it
        # stopped telling them apart, which is a fact about the corpus and not about this run.
        print(f"\n{len(misquoted)} dropped — the sentence is not in {letter.name}:")
        for c in misquoted[:8]:
            print(f"  {c.sentence[:88]}")
        if len(misquoted) > 8:
            print(f"  … and {len(misquoted) - 8} more")
    print(f"\n  wrote  {link(out)}")


if __name__ == "__main__":
    main()
