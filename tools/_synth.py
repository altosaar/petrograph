"""
_synth — what the synthetic corpus is made of, and where it lives.

Not a script. `synth_profile.py` measures the real vault with it, `synth_corpus.py` writes the
fake one with it, and both have to agree about two things: what a synthetic week is made of, and
where the vault it goes into is.

THE POINT OF THE WHOLE THING. The connectome pages are worth showing to somebody, and they
cannot be shown, because every square on them is a diff of a private note. So this generates a
second vault: four weeks of note edits, a sleep report, a ledger and a letter, in the same
formats, with nobody's life in them. `PETROGRAPH_OUT=synthetic` then points every tool in this
repo at that vault instead, and the same waffle, the same connectome and the same topic
colouring come out — same code, synthetic input. Nothing downstream knows the difference, and
nothing downstream had to change to not know it.

THE ONE RULE, AND IT IS STILL THE ONLY ONE. No text from the real vault ever reaches the model
that writes the fake one. Profiling reads the real hunks and returns *counts* — how many people a
hunk tends to name, how often one holds a pasted message — and the BAML class it returns has no
string field in it, so a real name cannot come back even if a model tried to send one. The
generator is then shown those numbers, a style guide, and a cast it was given or asked for
itself. It is a clean room, and the wall is the return type rather than a promise.

WHAT USED TO BE HERE. A gitignored `topics.conf` held an allow list the corpus was written from
and a deny list nothing in it could contain, and every generating prompt carried the deny list as
a "never write this" block. It was the wrong instrument. The wall against the real vault is the
profile's return type, and that wall does not need help; what the deny list actually did was stop
the synthetic writer from saying how they felt, which is the one thing a journal is for. A vault
whose author may not be upset is not a harder test of the connectome, it is a thinner one. So the
gate is gone, and the corpus is free to be as plainly emotional as the person writing it would
be. Nothing in it is anybody's life.

No dependencies, so importing this leaves a tool's `dependencies = []` intact.
"""

from __future__ import annotations
import datetime as dt

from _env import REPO_ROOT

# One root, laid out exactly like the repo's own, because that is what makes it a drop-in:
# `PETROGRAPH_OUT=synthetic` is read by every tool here that looks for sessions, so the
# synthetic vault is reached by moving one environment variable rather than by a flag on each.
SYNTH_ROOT = REPO_ROOT / "synthetic"
SESSIONS = SYNTH_ROOT / "sessions"
PROFILE = SYNTH_ROOT / "profile.json"
STYLE_GUIDE = SYNTH_ROOT / "style-guide.md"

# What a storyless run is written about. Material, not a filter — nothing is checked against this
# list, and a note that wanders off it is not discarded. It exists because a planner asked to
# invent a month of somebody's notes from nothing returns a month of the same note, and because
# the connectome colours by topic and wants more than three colours on it. A run with `--story`
# ignores this entirely and takes its subjects from the story, which knows what its own weeks are
# about.
SUBJECTS = [
    "finance", "physiology", "family", "work", "friendship", "sleep", "reading", "writing",
    "music", "travel", "cooking", "legal", "property", "exercise", "technology", "grief",
]

# Four weeks, the last of them the one whose letter is read. Fewer than four and the connectome
# has nothing interesting to draw: an arrow that stays inside one column is the ordinary case,
# and the ones worth the page are the ones that reach back two or three weeks.
WEEKS = 4

# The calendar the hand-written vault is composed against. Every date inside `synthetic/vault/`
# is an absolute date in this frame, which is what makes the vault readable and diffable; the
# assembler shifts them onto the live calendar on the way out. A Friday, and the weekday matters:
# the shift is always a whole number of weeks, so "tuesday is gothersgade" and "thursday the
# offsite" stay true however far the corpus travels.
REFERENCE = "2026-02-27"

# How far ahead the newest week sits. Two months puts the corpus in the near future, which is
# where a demo wants to be — a chart of last spring reads as an archive, and a chart of next
# month reads as a life somebody is in the middle of.
MONTHS_AHEAD = 2


def anchor(today: dt.date | None = None) -> str:
    """The newest week's date: about `MONTHS_AHEAD` out, on the reference's weekday.

    Snapped to that weekday so the offset from `REFERENCE` is a whole number of weeks and every
    day-name written into the vault survives the move. Then nudged forward, a week at a time, if
    the four weeks would straddle a new year — not out of squeamishness about the year, which is
    not printed anywhere any more, but because the week directories are named `mm-dd` and a
    straddle is the one case where sorting those by name gives the wrong order.
    """
    today = today or dt.date.today()
    month = today.month - 1 + MONTHS_AHEAD
    year, month = today.year + month // 12, month % 12 + 1
    day = min(today.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
                          31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    target = dt.date(year, month, day)
    weekday = dt.date.fromisoformat(REFERENCE).weekday()
    end = target + dt.timedelta(days=(weekday - target.weekday()) % 7)
    while (end - dt.timedelta(days=7 * (WEEKS - 1))).year != end.year:
        end += dt.timedelta(days=7)
    return end.isoformat()


ANCHOR = anchor()

# Not every read happens on the Friday. Days each week's read is held past the Friday its edits
# are counted to, oldest week first: the first read on a Saturday, the second on a Sunday, the
# last two on the Friday. Only those two can move. Week three's opener is written the evening
# before the Saturday drive to Roskilde, which is in week four's notes; and week four's letter is
# the one the connectome is read out of. The edits stay on the days they fell, except that a
# read held into the weekend pushes the next week's earliest edits past it — anything written by
# the time of a read is in that read.
HELD = (1, 2, 0, 0)


def compactions(dates: list[str]) -> dict[str, str]:
    """Each week's Friday → the day its read actually happened, which names the folder. See `HELD`."""
    return {d: (dt.date.fromisoformat(d) + dt.timedelta(days=HELD[i] if i < len(HELD) else 0))
            .isoformat() for i, d in enumerate(dates)}


def label(date: str) -> str:
    """`2026-11-13` → `11-13`. What a session directory and its files are actually called.

    The year is dropped on the way to disk and never written anywhere the corpus can be read
    from, so a vault regenerated once does not start looking stale the January after. What it
    costs is that two runs a year apart collide in the same folder, which is the right trade for
    a thing that exists to be looked at rather than kept.
    """
    return date[5:] if len(date) == 10 else date


def week_dates(end: str = "", weeks: int = WEEKS) -> list[str]:
    """The session dates, oldest first — `end` and the weeks before it, seven days apart."""
    last = dt.date.fromisoformat(end or ANCHOR)
    return [(last - dt.timedelta(days=7 * i)).isoformat() for i in reversed(range(weeks))]
