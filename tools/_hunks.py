#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
_hunks — what a hunk is, and how a week's worth of them is read off disk.

Not a script. It was the parser inside `microlite_waffle.py`, and it moved here the moment a
second tool needed the same hunks: `hunk_connections.py` has to name the ones it sends to a
model, and `connection_waffle.py` has to draw the ones that came back. Three copies of "what
counts as a hunk" is three chances for an id to mean two different things — which is the one
failure this whole feature cannot survive, since a connection is a pair of ids.

A hunk is one `@@` block in a session's `microlite.md`: one contiguous stretch of change in one
note. A short note comes back from the reader as whole current content rather than a diff, so it
has no `@@` to count; it gets one `kind="full"` hunk instead of vanishing from the week, and that
content is its text. It used to be kept empty — one square, nothing in it — which meant the model
reading a week's connections was shown a title and a blank for every short note, and could only
guess which of them a sentence was about. A third of a week's hunks are that kind.

A hunk also carries `chars`: the text on its changed lines, without the `+`/`-` that marks them.
That is what the characters view weighs, and it belongs here rather than in the view, for the
same reason the rest does — one answer to what a hunk is.

An id is `<week>/<ordinal>` — `2026-06-06/12`, or `11-13/12` where the week carries no year. Self-describing on purpose: it survives a week
being added to or dropped from a run (a positional `W2-12` would not), and a model reading the
list can see which week a hunk belongs to without being told separately, which is most of what
it needs to tell a connection within a week from one that reaches back across weeks.

No dependencies, so importing this leaves a tool's `dependencies = []` intact.
"""

from __future__ import annotations
import datetime as dt
import re
from pathlib import Path

# `2026-06-06` for a real session and `11-13` for a synthetic one, whose directories carry no
# year — see `_synth.label`. Either way it is the key a hunk id is built from, and the only rule
# that matters is that the same folder always produces the same key.
DATE = re.compile(r"(?:\d{4}-)?\d{2}-\d{2}")
# `_generated Fri 11-13 10:15:04`. The weekday is optional and only the yearless files carry
# one — a week named `2026-06-06` can have its weekday computed, and a week named `11-13`
# cannot, so the writer that dropped the year is the one that has to hand it over.
GENERATED = re.compile(r"^_generated (?:([A-Z][a-z]{2}) )?((?:\d{4}-)?\d{2}-\d{2})")
FENCE = re.compile(r"^```(\w*)")
# `## note.md — 14 edits in window`, and the older `## note.md` with the count on its own line.
HEADING = re.compile(r"^## (.+?)(?: — .*)?$")


def parse(path: Path) -> tuple[str, str, list[dict]]:
    """One microlite.md → (week, weekday, hunks). The week is the session date; a hunk is one `@@`."""
    lines = Path(path).read_text(errors="replace").splitlines()
    week = Path(path).parent.name if DATE.fullmatch(Path(path).parent.name) else ""
    weekday = ""
    hunks: list[dict] = []
    fence, note, hunk, whole = None, None, None, None

    def close() -> None:
        nonlocal hunk
        if hunk:
            hunk["kind"] = ("mixed" if hunk["add"] and hunk["dele"]
                            else "added" if hunk["add"] else "removed")
            # What the characters view measures: the text on the changed lines, without the
            # `+`/`-` that marks them. Not the hunk's length — the context lines a diff carries
            # are what the change sits next to, and nobody edited them.
            hunk["chars"] = sum(len(l) - 1 for l in hunk["text"] if l.startswith(("+", "-")))
            hunk["text"] = "\n".join(hunk["text"]).rstrip()
            hunks.append(hunk)
            hunk = None

    for line in lines:
        if m := FENCE.match(line):
            close()
            if whole is not None:                    # the fence that ends a whole note
                whole["text"] = whole["text"].rstrip()
                whole = None
            fence = None if fence is not None else (m.group(1) or "text")
            # A short note comes back as whole content rather than a diff: one square for it,
            # holding what the note says. `chars` stays 0 — nothing in it is a changed line.
            if fence == "markdown" and note:
                whole = {"note": note, "head": "(current content — no diff)",
                         "add": 0, "dele": 0, "chars": 0, "kind": "full", "text": ""}
                hunks.append(whole)
            continue
        if fence is not None:
            if whole is not None:
                whole["text"] += line + "\n"
                continue
            if fence != "diff":
                continue
            if line.startswith("@@"):
                close()
                hunk = {"note": note, "head": line, "add": 0, "dele": 0, "text": []}
            elif hunk is not None:
                hunk["text"].append(line)
                if line.startswith("+"):
                    hunk["add"] += 1
                elif line.startswith("-"):
                    hunk["dele"] += 1
            continue
        # Outside every fence, so a `##` in somebody's note is note text and not a heading.
        if (m := GENERATED.match(line)):
            weekday = weekday or (m.group(1) or "")
            week = week or m.group(2)
        elif m := HEADING.match(line):
            # `.md` is what tells a note apart from the reader's own sections ("Activity by
            # day", "Sync events") and from anything appended after the hunks.
            name = m.group(1).strip()
            note = name if name.endswith(".md") else None

    close()
    return week or Path(path).stem, weekday, hunks


DAYS = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
        "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}


def weekday(week: dict) -> str:
    """The day a session ends on, spelled out, or "" if nothing knows.

    Computed where the week carries its year and read off the file where it does not. A session
    named `11-13` is a day with no weekday recoverable from it, so the writer that dropped the
    year puts one in `microlite.md`'s header and `parse` carries it up to here.
    """
    if len(week.get("week", "")) == 10:
        return dt.date.fromisoformat(week["week"]).strftime("%A")
    return DAYS.get(week.get("weekday", ""), "")


def load(paths, hunks_only: bool = False) -> list[dict]:
    """Every microlite.md → weeks, chronological, each hunk carrying its own id.

    The id is assigned here and nowhere else. A tool that names a hunk to a model and a tool
    that draws the answer have to mean the same square by `2026-06-06/12`, and the only way to
    be sure of that is for one function to have decided it.
    """
    weeks = []
    for path in paths:
        week, weekday, hunks = parse(Path(path))
        if hunks_only:
            hunks = [h for h in hunks if h["kind"] != "full"]
        if not hunks:
            continue
        for i, h in enumerate(hunks):
            h["id"] = f"{week}/{i}"
            h["ord"] = i
            h["week"] = week
        weeks.append({"week": week, "weekday": weekday, "hunks": hunks,
                      # Each note once, in the order the review itself presented them.
                      "notes": list(dict.fromkeys(h["note"] for h in hunks))})
    weeks.sort(key=lambda w: w["week"])
    return weeks


def by_id(weeks: list[dict]) -> dict[str, dict]:
    """Every hunk in every week, keyed by id — what turns a model's answer back into squares."""
    return {h["id"]: h for w in weeks for h in w["hunks"]}
