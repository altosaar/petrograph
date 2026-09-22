#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
synth_letter — one week of the synthetic vault, read once, by Opus.

`weekly_review.py` writes a real week in two model turns: the ACT read into `claude-analysis.md`,
then a narrative compaction of that read into the letter. That shape exists because the letter is
going to be *narrated*, and a letter written straight off a hundred kilobytes of diff reads like
a report rather than like something addressed to somebody.

The synthetic vault does not want that. Here the letter is a document to be read on a page and,
more to the point, the thing `hunk_connections.py` reads back against the hunks — so what it
needs is one pass with the ACT prompt, over the same bundle a real week assembles, landing
straight in `lifelog-<date>.md`. One prompt, one call, one file.

TWO PROMPTS AND AN ANSWER GO IN. `prompts/act-analysis.md` is the ask, unchanged from the one a
real week is read with. `prompts/synthetic-style-guide.md` is the register and the prose
principles, appended after it — that used to be a section inside the read prompt and is now one
file every path shares. And `## Week context`, out of the session's own `edit-me.md`, carries the
read prompt's own four openers answered: "This upcoming week...", "Tomorrow...", "Today...",
"Emotionally, I...", in the writer's first person, which is the half of the week the diffs cannot
say. The blanks arrive a few lines above their filled-in selves, which is exactly the shape a
person pasting this into a chat window produces, and Opus reads the pair the way a person means
it. See `synth_corpus.py` stage 5.

Nothing here reimplements the bundle. `build_bundle`, `prompt_body`, `parse_front_matter` and
`run_claude` are imported from `weekly_review.py`, so a synthetic week is assembled by exactly
the code that assembles a real one — the same `## Week context`, the same attachment headings,
the same unfenced diff. If that changes there, it changes here, which is the point of importing
it rather than copying forty lines of it.

Nothing filters the answer. The read is asked for a clinical-psychology take on a fictional
bereavement, and a tool that then flinched at the vocabulary it asked for would be checking the
wrong thing — what keeps this safe is that there is no real person anywhere in the inputs.

ONE LETTER, AND IT IS THE LAST WEEK'S. The first three weeks are what the fourth is read against,
not things to be read in their own right. A letter for each of them was a letter nothing
downstream used — and worse, a place for the read to have already said what the fourth week's
letter ought to have to arrive at by itself. So the default is the newest session and nothing
else; `--session` still reads any week by hand.

Usage:
    ./synth_letter.py                                   # the newest synthetic week, if it has none
    ./synth_letter.py --session synthetic/sessions/2026-02-27
    ./synth_letter.py --force                           # rewrite letters that already exist
    ./synth_letter.py --dry-run                         # assemble and price the bundle, call nothing
"""

from __future__ import annotations
import argparse
import datetime as dt
import shutil
import sys
import tempfile
import time
from pathlib import Path

from _env import REPO_ROOT
from _session import INPUT, STUB_MARKER, compaction
from _synth import SESSIONS
from _term import link, say
from weekly_review import (ACT_PROMPT, STYLE_PROMPT, build_bundle, parse_front_matter,
                           prompt_body, run_claude, strip_comments)

MODEL = "opus"




def letter_doc(meta: dict, body: str) -> str:
    """The letter, with the front-matter a session's letter carries.

    Deliberately not `weekly_review.compaction_doc`: that one records a `compaction:` prompt and
    an `analysis:` sibling, and here there is neither. A document should not name a stage that
    did not happen.
    """
    return f"""---
title: {meta['interlocutor']} — {meta['title']}
interlocutor: {meta['interlocutor']}
date: {meta['date']}
slug: {meta['stem']}
stage: act-read
llm: claude
model: {meta['model']}
session: {meta['session']}
sources:
  - obsidian: {meta['stem']}/microlite.md
  - attachments: {meta['attachments'] or 'none'}
prompts:
  analysis: {meta['act_prompt']}
  style: {meta['style_prompt']}
synthetic: true
tags: [synthetic, act-read]
---

{body}
"""


def write_one(session: Path, model: str, force: bool, dry_run: bool) -> bool:
    """One session → its letter. False if there was nothing to do."""
    edit = session / INPUT
    if not edit.is_file():
        say(f"  skip   {session.name}: no {INPUT}")
        return False
    out = session / compaction(session)
    if out.is_file() and STUB_MARKER not in out.read_text(errors="replace") and not force:
        say(f"  have   {session.name}: a letter is already there (--force to rewrite)")
        return False

    fields, context = parse_front_matter(edit.read_text(), edit)
    context = strip_comments(context)
    diff = (session / "microlite.md").read_text(errors="replace")

    attachments = []
    for raw in (a.strip() for a in fields.get("attachments", "").split(",")):
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (session / path).resolve()
        if path.is_file():
            attachments.append((path.name, path.read_text().strip(), False))

    since = fields.get("since") or "7"
    ask = f"{prompt_body(ACT_PROMPT)}\n\n{prompt_body(STYLE_PROMPT)}"
    bundle = build_bundle(ask, context, attachments, diff, since)
    say(f"  {session.name}  bundle {len(bundle):,} chars ≈ {len(bundle) // 4:,} tokens")
    if dry_run:
        return False

    t = time.perf_counter()
    # Started in an empty directory that belongs to no project, so the CLI brings no memory,
    # CLAUDE.md or skills into a letter about a man who does not exist — see `run_claude`.
    with tempfile.TemporaryDirectory(prefix="synth-letter-") as nowhere:
        body, data = run_claude(bundle, model=model, session="", resume=False,
                                effort=fields.get("effort") or "", cwd=nowhere)
    # The CLI files its own housekeeping (a title, a summary) under a smaller model in the same
    # usage record, so the first key is not necessarily the writer. The one that wrote most is.
    usage = data.get("modelUsage") or {}
    meta = {
        "title": fields.get("title") or f"synthetic week {session.name}",
        "interlocutor": fields.get("interlocutor") or "Bobby",
        "date": fields.get("date") or session.name,
        "stem": session.name,
        "model": max(usage, key=lambda m: usage[m].get("outputTokens", 0)) if usage else model,
        "session": data.get("session_id", ""),
        "attachments": ", ".join(name for name, _, _ in attachments),
        "act_prompt": str(ACT_PROMPT.relative_to(REPO_ROOT)),
        "style_prompt": str(STYLE_PROMPT.relative_to(REPO_ROOT)),
    }
    out.write_text(letter_doc(meta, body))
    say(f"         {len(body):,} chars in {time.perf_counter() - t:.0f}s → {link(out)}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(
        description="One Opus pass with the ACT prompt per synthetic week → lifelog-<date>.md.")
    ap.add_argument("--session", type=Path, action="append",
                    help="A session directory. Repeatable; default is the newest synthetic "
                         "session only — see ONE LETTER above.")
    ap.add_argument("--model", default=MODEL, help=f"Claude alias (default {MODEL}).")
    ap.add_argument("--force", action="store_true", help="Rewrite letters that already exist.")
    ap.add_argument("--dry-run", action="store_true", help="Assemble and size the bundles only.")
    args = ap.parse_args()

    if not shutil.which("claude"):
        sys.exit("`claude` is not on PATH — the letter is written by the same CLI a real week uses.")
    sessions = args.session or sorted(d for d in SESSIONS.glob("*/") if (d / INPUT).is_file())[-1:]
    if not sessions:
        sys.exit(f"No synthetic session under {SESSIONS} — run: just synth-corpus")

    say(f"==> {len(sessions)} sessions · one ACT read each · {args.model}\n")
    wrote = sum(write_one(Path(s), args.model, args.force, args.dry_run) for s in sessions)
    say(f"\n{wrote} letters written." if not args.dry_run else "\nDry run — nothing billed.")
    if wrote:
        say("Next:  just synth-pages")


if __name__ == "__main__":
    main()
