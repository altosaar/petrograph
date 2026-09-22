#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["baml-bridge==0.17.0", "pydantic>=2"]
# ///
"""
unit_topics — what each unit is about, read one unit at a time.

The connectome colours a square by what the edit did: added, removed, mixed. That is a property
of the diff and not of the writer — a week spent pruning one note and a week spent building
another look alike. Topic is the other axis, and nothing in the vault records it: there are no
tags, the folder is a date, and a note's title is a name its author chose years ago.

So it is read rather than looked up. Two topics are fixed because two of the week's sources are —
`finance` for the ledger, `physiology` for what the ring records — and the rest are discovered.
Each unit gets its own Haiku call, shown the topics found so far, and either picks one or names
a new one. The list grows as it goes, which is why the calls are made in order and not in
parallel: a unit classified against a fuller list has a better chance of reusing a topic than
inventing one, and reuse is the whole point of a vocabulary.

Every unit is classified, not only the connected ones, because a topic is a fact about the unit
and the view that draws them all should be able to colour them all.

The pass is cached whole, under `share/.baml-cache`, keyed by every unit's text and the prompt's
own source. Two hundred calls is a few minutes and a few cents the first time and nothing after.

Usage:
    ./unit_topics.py                      # the newest week's connections file
    ./unit_topics.py --week 2026-06-06
    ./unit_topics.py --dry-run            # unit and call counts, no API call
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from _cache import cache_path, write_atomic
from _env import REPO_ROOT, api_key
from _hunks import load
from _term import link

SESSIONS = Path(os.environ.get("PETROGRAPH_OUT") or REPO_ROOT).expanduser().resolve() / "sessions"
CACHE_DIR = REPO_ROOT / "share" / ".baml-cache"
PROMPT = REPO_ROOT / "baml_src" / "topics.baml"

# The two that are not up for discussion, because two of the week's sources are exactly these.
FIXED = ["finance", "physiology"]

# How much of a unit the classifier reads. A topic is the gist, and the gist is at the top.
UNIT_CHARS = 600


def unit_text(u: dict) -> str:
    """What the model is shown for one unit: where it came from, and what it says."""
    body = u["text"][:UNIT_CHARS].rstrip()
    return f"{u['week']} · {u['note']}\n{u['head']}\n{body}".strip()


def normalize(topic: str, known: list[str]) -> str:
    """One topic name per idea. A model asked for a word will sometimes return a sentence."""
    t = " ".join(topic.strip().strip(".").lower().split())
    if not t:
        return "misc"
    # A long answer is prose, not a topic. Truncating it to two words manufactures a topic
    # that looks real — "i need" arrived that way, sixteen units strong — so it is refused
    # instead, and the schema is what stops it happening rather than this.
    if len(t.split()) > 2:
        return "misc"
    # Case and stray plurals are not new topics.
    for k in known:
        if k == t or k == t.rstrip("s") or k.rstrip("s") == t:
            return k
    return t


def main() -> None:
    ap = argparse.ArgumentParser(
        description="A topic for every unit, discovered one unit at a time by Haiku.")
    ap.add_argument("--week", help="Session date whose connections file names the weeks.")
    ap.add_argument("--out", type=Path, help="Where the JSON goes (default: in the session).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Count the units and the calls. No API call, nothing billed.")
    ap.add_argument("--deny", default="",
                    help="Comma-separated words that may not be used as a topic name. A unit "
                         "that lands on one is filed under `misc` instead. Discovery is the "
                         "point of this pass, so this is a list of names rather than of "
                         "subjects — the units keep their meaning, they just stop putting that "
                         "word on the chart and in the legend.")
    ap.add_argument("--max-topics", type=int, default=0, metavar="N",
                    help="Stop inventing once N topics exist, the fixed two included: from "
                         "then on each unit is shown the list as closed and asked to pick from "
                         "it, and a name that is still new is filed under `misc`, the one "
                         "overflow bucket. Default 0 is uncapped. The palettes hold nine topic "
                         "hues, so a tenth topic is drawn in the neutral either way.")
    ap.add_argument("--no-cache", action="store_true", help="Classify again from scratch.")
    args = ap.parse_args()

    found = sorted(SESSIONS.glob("*/connections-*.json"))
    if args.week:
        found = [p for p in found if p.parent.name == args.week]
    if not found:
        sys.exit(f"No connections-*.json under {SESSIONS} — run: just connections")
    src = json.loads(found[-1].read_text())
    target = src["week"]

    # The same units the connectome draws, from the same loader, so a topic lands on a square.
    weeks = load([SESSIONS / w / "microlite.md" for w in src["weeks"]])
    units = [dict(h) for w in weeks for h in w["hunks"]]
    units += [{"id": s["id"], "week": target, "note": s["id"], "head": "",
               "text": s["text"], "kind": "context"} for s in src.get("sources", [])]

    texts = [unit_text(u) for u in units]
    key = cache_path(CACHE_DIR, {
        "fn": "ClassifyUnit",
        "prompt": PROMPT.read_text(errors="replace") if PROMPT.is_file() else "",
        "fixed": FIXED,
        "units": texts,
        # Only in the key when set, so every pass cached before the flag existed still hits.
        **({"max_topics": args.max_topics} if args.max_topics else {}),
    }, ".json")
    cached = key.exists() and not args.no_cache

    print(f"units    {len(units):,} across {len(weeks)} weeks "
          f"({sum(1 for u in units if u['kind'] == 'context')} sources)")
    print(f"fixed    {', '.join(FIXED)}")
    print(f"cache    {'hit — this pass is already on disk' if cached else f'miss — {len(units):,} calls would be billed'}")

    if args.dry_run:
        print("\nDry run — no API call made, nothing billed.")
        return

    if cached:
        print("\nfrom cache — nothing billed.")
        assigned = json.loads(key.read_text())
    else:
        os.environ["ANTHROPIC_API_KEY"] = api_key("ANTHROPIC_API_KEY")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            from baml_sdk import ClassifyUnit
        except ImportError as exc:  # pragma: no cover - a setup failure, not a runtime one
            sys.exit(f"Could not import the generated BAML client ({exc}).\nRun: just baml")

        print()
        topics = list(FIXED)
        assigned = {}
        for i, (u, text) in enumerate(zip(units, texts), 1):
            # In order, and each call sees what the ones before it found. That is the whole
            # mechanism: the list is built by reading, and a fuller list means more reuse.
            closed = bool(args.max_topics) and len(topics) >= args.max_topics
            listed = "\n".join(topics) + (
                "\n\n(This list is closed. Choose one of the topics above; do not name a new "
                "one.)" if closed else "")
            answer = ClassifyUnit(unit=text, topics=listed)
            topic = normalize(answer.topic, topics)
            if topic not in topics and closed:
                topic = "misc"
            if topic not in topics:
                topics.append(topic)
                print(f"  {i:>4}/{len(units)}  new topic: {topic}")
            assigned[u["id"]] = topic
            if i % 25 == 0 or i == len(units):
                print(f"  {i:>4}/{len(units)}  {len(topics)} topics so far")
        write_atomic(key, json.dumps(assigned, ensure_ascii=False).encode())

    # Applied over the answer rather than inside it, and after the cache on purpose: the cache
    # holds what the model actually said, and which names are allowed is this file's opinion,
    # which can change without invalidating two hundred calls. Same rule `hunk_connections.py`
    # follows when it validates ids.
    if deny := [d.strip().lower() for d in args.deny.split(",") if d.strip()]:
        refused = {t for t in set(assigned.values())
                   if any(re.search(rf"\b{re.escape(d)}\b", t) for d in deny)}
        if refused:
            assigned = {u: ("misc" if t in refused else t) for u, t in assigned.items()}
            print(f"\nrefused  {', '.join(sorted(refused))} — filed under misc")

    counts: dict[str, int] = {}
    for topic in assigned.values():
        counts[topic] = counts.get(topic, 0) + 1
    # Fixed first, then by how much of the vault each one turned out to cover.
    order = [t for t in FIXED if t in counts] + sorted(
        (t for t in counts if t not in FIXED), key=lambda t: (-counts[t], t))

    out = args.out or SESSIONS / target / f"topics-{target}.json"
    out.write_text(json.dumps({
        "week": target,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": "claude-haiku-4-5-20251001",
        "weeks": [w["week"] for w in weeks],
        "topics": order,
        "units": assigned,
    }, ensure_ascii=False, indent=2))

    print(f"\n{len(order)} topics over {len(assigned):,} units")
    for t in order:
        print(f"  {counts[t]:>4}  {t}")
    print(f"\n  wrote  {link(out)}")


if __name__ == "__main__":
    main()
