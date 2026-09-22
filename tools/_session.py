#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
_session — what a session directory is made of.

A session is a directory named for its date, and these are the files in it. Each says what it
is rather than where it falls in a sequence: you edit `edit-me.md`, microlite writes
`microlite.md`, Claude's letter lands in `lifelog-<date>.md`. The attachments a session reads
are copied in beside them, so one folder holds everything a paste into Claude needs.

The letter is the exception to "the folder is the identity", and deliberately: it is the one
document that is read aloud, and its audio has always been `lifelog-<date>.mp3` because a file
on a phone or in a car has no folder to be read in. Letting the markdown carry the same name
means the letter and every render of it are one set, sorted together and named the same, and
that `_names.py` no longer has to translate one naming scheme into another. It was
`claude-output.md`, which said who wrote it rather than what it is.

The layout lived in weekly_review.py, which owns the pipeline, and weekly_context.py carried a
partial copy under a comment reading "kept in step with weekly_review.py" — kept in step by
hand, which is the thing this module exists to stop.

STUB_MARKER especially. `just render` refuses to narrate a session whose letter is still the
placeholder, and it found that out by grepping for the literal string, spelled out a third time
in the justfile. Three hand-synchronised copies of the guard that stands between you and paying
ElevenLabs to read an HTML comment aloud. The recipe now asks:

    ./tools/_session.py --stub-marker

Importable and runnable both, for that reason. No dependencies, so importing it leaves a
tool's `dependencies = []` intact.
"""

from __future__ import annotations
import sys
from pathlib import Path

INPUT = "edit-me.md"            # the one file you write
DIFF = "microlite.md"           # the week's Obsidian history
ANALYSIS = "claude-analysis.md"  # the ACT read (unattended runs only)
BUNDLE = "bundle.md"            # --dry-run only: the assembled prompt, to read or paste

# What the letter and every render of it are called. The prefix lives here rather than in
# _names.py because it names a file in a session first and an audio file second, and _names
# imports it — one direction, so there is nothing to keep in step.
LIFELOG = "lifelog"


def compaction(session: Path | str) -> str:
    """The letter's filename in `session`: `lifelog-<date>.md`, the name its audio takes too.

    A function and not a constant, because the name carries the date and the date is the
    folder. Takes the session directory, or just its name, so a caller holding either can ask.
    """
    return f"{LIFELOG}-{Path(session).name}.md"

STUB_MARKER = "STUB — paste Claude's letter over this whole file"
STUB = f"""<!-- {STUB_MARKER}.
     Everything else in this folder is what you feed Claude; this is what comes back.
     `just render` will not narrate while this marker is still here. -->
"""


def ensure_stub(session: Path) -> Path:
    """Put the empty place the answer goes into the folder, if it is not there already.

    The stub exists from the start so the session shows the whole shape of the work at a
    glance: the files you feed Claude, and the one file that comes back. It is never
    overwritten — once a letter is in it, it is the letter.
    """
    stub = session / compaction(session)
    if not stub.exists():
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(STUB)
    return stub


def main() -> None:
    args = sys.argv[1:]
    if args == ["--stub-marker"]:
        print(STUB_MARKER)
        return
    # `ls -t sessions/*/claude-output.md` used to find every letter at once. A glob cannot say
    # "named for the folder it is in", so the recipe asks for the list instead of building it.
    if len(args) > 1 and args[0] == "--letters":
        letters = [d / compaction(d) for d in (Path(a) for a in args[1:])]
        for letter in sorted((p for p in letters if p.is_file()),
                             key=lambda p: p.stat().st_mtime, reverse=True):
            print(letter)
        return
    sys.exit(f"usage: {Path(sys.argv[0]).name} --stub-marker | --letters <session>...")


if __name__ == "__main__":
    main()
