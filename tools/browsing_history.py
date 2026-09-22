#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
browsing_history — the week as it happened in the browser, straight out of its history.

The weekly review bundle already sees the Obsidian diff, the calendar, the week's spending
and the Oura summary. It cannot see where the week actually went online. This writes that
down — as a shape, not as a log. One row per site per hour: how many visits, how long they
lasted, and what the pages were called, busiest site first within each hour.

The unit is the site-hour because the question is "what was I doing on Tuesday afternoon",
and 2,300 timestamped URLs answer that worse than 500 rows do. URLs are left out entirely:
a query string is most of a URL's length and none of its meaning, and the domain plus the
page title already says what the time was spent on.

It is a leaf. Nothing here knows about `weekly_review.py`; it writes a markdown file with
its own H1, which is all `attachments:` in a session's front-matter needs. Run it by hand
when you want the week's browsing in the read, and leave it out when you do not.

Three families of browser, one document. Chrome — and every Chromium relative, Brave,
Chromium, Edge, Arc — keeps a `History` database; Firefox — and Zen, LibreWolf, Waterfox —
keeps `places.sqlite`; Safari keeps `History.db`. The three schemas share nothing: different
tables, three different epochs, two of them counting microseconds and one counting seconds,
a bitfield against an enum against a foreign key for "this row is a redirect", and only one
of them remembers how long you stayed. Each is read by its own `Source`, everything
downstream of `fetch` sees the same `Visit`, and which one you have is decided by looking at
the tables rather than at the path — so `--history <any file>` needs no flag to go with it.

Local and read-only, in that order:
  · The history database is opened through the immutable URI (`file:…?immutable=1`), so
    SQLite cannot take a write lock, replay the journal, or create one. Nothing is copied
    and nothing is modified — which also means a page visited seconds ago may be missing,
    since a write still sitting in the browser's write-ahead log is invisible here.
    Firefox carries a far bigger WAL than Chrome does (megabytes, checkpointed lazily),
    so quit it first if you want the last hour of the week to be in the file.
  · No network. `dependencies = []` above is the proof, and it survives the imports:
    the underscore-prefixed modules beside this one pull in nothing but the standard
    library. There is no
    client that could send a browsing history anywhere, and no model is asked about it.
  · The output is a list of every page you looked at. The tool refuses to write it
    anywhere git would track it.

What the databases make you decide, and what this decides:
  · Time is counted from a different year in each, and in different units. Chrome's
    `visit_time` is microseconds since 1601-01-01 UTC, the Windows FILETIME epoch;
    Firefox's `visit_date` is microseconds since 1970; Safari's `visit_time` is *seconds*
    since 2001, the Apple epoch the message databases also use. A `Source` carries both
    the offset and the scale, so the three arrive at `fetch` as one kind of number.
  · Most rows are not pages you looked at. Redirect hops, subframes and browser-internal
    pages are all visits — Chrome packs the fact into bits on `transition`, Firefox into
    a small enum on `visit_type`, Safari into a `redirect_destination` foreign key — and
    a raw dump is roughly a fifth machinery. They are filtered by default and counted in
    the summary, so nothing vanishes silently; `--all` keeps everything.
  · Sites stamp their own name onto every page title, so half a LinkedIn title is the
    word LinkedIn. The suffix is found per site by agreement between its own titles and
    stripped, since the row already says which site it is.
  · Only Chrome records a duration. Its `visit_duration` is often zero, and tabs run in
    parallel, so adding it up gives days longer than a day. Time on page is the *union*
    of the intervals, clamped to the hour a visit is filed under: six tabs open for an
    hour is an hour. It is still a floor rather than a measurement, and the visit counts
    are the sturdier of the two numbers. Neither Firefox nor Safari stores a duration at
    all — so their documents leave the time columns out entirely, rather than printing a
    column of dashes that would read as "under a minute" when it means "unknowable".

Usage:
    ./browsing_history.py                          # last 7 days, Chrome's Default profile
    ./browsing_history.py --days 14
    ./browsing_history.py --browser firefox        # the profile Firefox actually launches
    ./browsing_history.py --browser firefox --profile default-release
    ./browsing_history.py --browser safari         # one history, no profiles
    ./browsing_history.py --profile "Profile 1"    # another Chrome profile
    ./browsing_history.py --history ~/Library/…/Brave-Browser/Default/History
    ./browsing_history.py --all                    # no filtering
    ./browsing_history.py --out -                  # stdout
    ./browsing_history.py --help
"""

from __future__ import annotations
import argparse
import configparser
import datetime as dt
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit
from _term import link, say
from _db import check_access, connect
from _env import REPO_ROOT

CHROME = Path.home() / "Library/Application Support/Google/Chrome"
FIREFOX = Path.home() / "Library/Application Support/Firefox"
SAFARI = Path.home() / "Library/Safari/History.db"

# Chrome stores time as microseconds since 1601-01-01 UTC (the FILETIME epoch), so a Unix
# timestamp needs this many seconds added before it can be compared to a `visit_time`.
# Firefox counts from the Unix epoch itself, and so needs nothing added. Safari counts
# from 2001, which is CFAbsoluteTime — the epoch extract_contacts.py meets in chat.db.
CHROME_EPOCH = 11644473600  # int(dt.datetime(1970,1,1) - dt.datetime(1601,1,1)) in seconds
FIREFOX_EPOCH = 0
APPLE_EPOCH = -978307200  # int(dt.datetime(1970,1,1) - dt.datetime(2001,1,1)) in seconds

# Stored units per second. Chrome and Firefox count microseconds; Safari counts seconds,
# as a float, which is the one place these three differ in scale rather than in offset.
MICROSECONDS, SECONDS = 1_000_000, 1

# Chrome's transition column packs a core type in the low byte and qualifier bits in the
# high ones. Only the few that decide whether a row is a page someone looked at are named.
TRANSITION_CORE = 0xFF
AUTO_SUBFRAME, MANUAL_SUBFRAME = 3, 4
REDIRECT = 0xC0000000  # CLIENT_REDIRECT | SERVER_REDIRECT

# Firefox says the same things with a plain enum in `visit_type` — nsINavHistoryService's
# TRANSITION_* values — rather than a bitfield. EMBED is its automatic subframe and
# FRAMED_LINK its manual one; modern Firefox keeps most embeds in memory instead of on
# disk, so that bucket is usually empty rather than missing.
FX_EMBED, FX_REDIRECT_PERMANENT, FX_REDIRECT_TEMPORARY, FX_FRAMED_LINK = 4, 5, 6, 8

WEB_SCHEMES = {"http", "https"}

# ── opening the database ─────────────────────────────────────────────────────

# ── which browser ────────────────────────────────────────────────────────────

def chrome_junk(row: sqlite3.Row) -> str | None:
    """Which bucket of machinery a Chrome row is, or None if it is a page someone saw."""
    if row["kind"] & REDIRECT:
        return "redirect hops"
    if (row["kind"] & TRANSITION_CORE) in (AUTO_SUBFRAME, MANUAL_SUBFRAME):
        return "subframes"
    return None


def firefox_junk(row: sqlite3.Row) -> str | None:
    """The same question of a Firefox row, whose answer is an enum rather than bits."""
    if row["kind"] in (FX_REDIRECT_PERMANENT, FX_REDIRECT_TEMPORARY):
        return "redirect hops"
    if row["kind"] in (FX_EMBED, FX_FRAMED_LINK):
        return "subframes"
    return None


def safari_junk(row: sqlite3.Row) -> str | None:
    """And of a Safari row, which answers by pointing at the visit it redirected to.

    Safari records no subframe visits at all, so that bucket simply never fills here —
    the summary line lists what was actually dropped, so an absent bucket says so by
    being absent. It does record loads that failed, which are places you tried to go
    rather than places you went.
    """
    if row["kind"] is not None:
        return "redirect hops"
    if not row["load_successful"]:
        return "failed loads"
    return None


class Source:
    """One browser family's history schema: how to read it, and what it can be asked.

    This class is the whole of the difference between the three browsers. Every query
    aliases to the same five names — `visit_time`, `url`, `title`, `kind`,
    `visit_duration` — so a row from any of them is read the same way, and every `Visit`
    past `fetch` is the same object regardless of where it came from. A query may select
    extra columns beyond those five (Safari's `load_successful`); they are that source's
    own business, read only by its own `junk`.

    `name` is the family, not the product — a Brave database reports as Chrome, which is
    true of its schema and is the only sense in which the document means it.

    `epoch` is seconds to subtract from a stored timestamp to get a Unix one, and `scale`
    is how many stored units make a second. Between them any of the three becomes the
    same number.
    """

    __slots__ = ("durations", "epoch", "junk", "name", "query", "scale")

    def __init__(self, name: str, epoch: int, scale: int, query: str, junk,
                 durations: bool) -> None:
        self.name = name
        self.epoch = epoch
        self.scale = scale
        self.query = query
        self.junk = junk
        self.durations = durations


CHROME_SOURCE = Source(
    "Chrome", CHROME_EPOCH, MICROSECONDS,
    "SELECT v.visit_time, u.url, u.title, v.transition AS kind, v.visit_duration "
    "FROM visits v JOIN urls u ON u.id = v.url "
    "WHERE v.visit_time >= ? ORDER BY v.visit_time",
    chrome_junk, durations=True,
)

# The literal 0 duration in the two below is not a measurement standing in for a missing
# one: `durations` is False, so nothing downstream ever reads it. It keeps one row shape.
FIREFOX_SOURCE = Source(
    "Firefox", FIREFOX_EPOCH, MICROSECONDS,
    "SELECT v.visit_date AS visit_time, p.url, p.title, "
    "v.visit_type AS kind, 0 AS visit_duration "
    "FROM moz_historyvisits v JOIN moz_places p ON p.id = v.place_id "
    "WHERE v.visit_date >= ? ORDER BY v.visit_date",
    firefox_junk, durations=False,
)

# Safari hangs the title on the visit rather than on the item, because the same URL can
# be titled differently on different days — which is the per-visit truth the other two
# approximate by overwriting.
SAFARI_SOURCE = Source(
    "Safari", APPLE_EPOCH, SECONDS,
    "SELECT v.visit_time, i.url, v.title, "
    "v.redirect_destination AS kind, v.load_successful, 0 AS visit_duration "
    "FROM history_visits v JOIN history_items i ON i.id = v.history_item "
    "WHERE v.visit_time >= ? ORDER BY v.visit_time",
    safari_junk, durations=False,
)


def sniff(con: sqlite3.Connection, db: Path) -> Source:
    """Which family wrote this file, decided by what tables are in it.

    Cheaper and steadier than trusting the path: a database passed to `--history`, copied
    out of a backup, or sitting in a profile directory someone renamed still lands on the
    reader that can actually parse it.
    """
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for want, source in (({"moz_historyvisits", "moz_places"}, FIREFOX_SOURCE),
                         ({"history_visits", "history_items"}, SAFARI_SOURCE),
                         ({"visits", "urls"}, CHROME_SOURCE)):
        if want <= tables:
            return source
    sys.exit(f"{db} is a database, but not a browser history.\n"
             "  Expected Chrome's `visits`/`urls`, Firefox's `moz_historyvisits`/`moz_places`,\n"
             "  or Safari's `history_visits`/`history_items`.\n"
             "  Firefox keeps history in places.sqlite and Safari in History.db — not in\n"
             "  the other .sqlite files that sit beside them.")


def firefox_profile(name: str | None) -> Path:
    """The places.sqlite for a named profile, or for the one Firefox actually launches.

    Firefox profile directories are `<salt>.<name>`, so the name a profile is known by is
    only ever the suffix — `--profile default-release`, never the salt. Which profile is
    *default* takes two passes, because profiles.ini answers twice: an `[Install…]`
    section carries `Default=<path>` (since Firefox 67) and a `[Profile…]` section carries
    the legacy `Default=1`. Where they disagree the install wins, since that is the one a
    running Firefox opened; the legacy flag is often left on a profile long since emptied.
    """
    root = FIREFOX / "Profiles"
    profiles = sorted(d for d in root.glob("*") if d.is_dir()) if root.is_dir() else []

    def listing(dirs: list[Path]) -> str:
        return "\n".join(f"    {d.name.split('.', 1)[-1]:<28} {d.name}" for d in dirs)

    if name:
        for d in profiles:
            if name in (d.name, d.name.split(".", 1)[-1]):
                return d / "places.sqlite"
        sys.exit(f"No Firefox profile called {name!r} under {root}\n"
                 "  The ones that exist:\n" + (listing(profiles) or "    (none)"))

    ini = configparser.ConfigParser()
    # Firefox writes section names in mixed case and profiles.ini has no interpolation
    # syntax to honour, so read it as the flat key/value file it is.
    ini.read(ini_path := FIREFOX / "profiles.ini")
    candidates: list[Path] = []
    for section in ini.sections():
        if section.startswith("Install") and (rel := ini[section].get("Default")):
            candidates.append(FIREFOX / rel)
    for section in ini.sections():
        if section.startswith("Profile") and ini[section].get("Default") == "1":
            if path := ini[section].get("Path"):
                candidates.append(FIREFOX / path if ini[section].get("IsRelative") == "1"
                                  else Path(path))
    # A profile named as default can still have no history — a fresh one, or the stale
    # `Default=1` above. Take the first candidate that has actually been browsed in.
    for path in candidates:
        if (db := path / "places.sqlite").exists():
            return db

    found = [d for d in profiles if (d / "places.sqlite").exists()]
    if len(found) == 1:
        return found[0] / "places.sqlite"
    if not found:
        why = (f"{len(candidates)} profile(s) are named default, and none has a places.sqlite"
               if candidates else f"there is no readable profiles.ini at {ini_path}")
        sys.exit(f"No Firefox history under {root}\n"
                 f"  ({why}.)\n"
                 "  Is Firefox installed, and has it been run at least once?")
    sys.exit(f"Several Firefox profiles have history and {ini_path} does not settle which\n"
             "  is default. Name one with --profile:\n" + listing(found))


# ── reading ──────────────────────────────────────────────────────────────────

class Visit:
    """One visit: when it happened, whose site, what the page was called, how long it lasted."""

    __slots__ = ("duration", "host", "title", "when")

    def __init__(self, when: dt.datetime, url: str, title: str, duration: int) -> None:
        self.when = when
        self.host = (urlsplit(url).hostname or "—").removeprefix("www.")
        self.title = title
        self.duration = duration

    @property
    def hour(self) -> dt.datetime:
        return self.when.replace(minute=0, second=0, microsecond=0)


def fetch(con: sqlite3.Connection, source: Source, days: int,
          keep_all: bool) -> tuple[list[Visit], Counter]:
    """Every visit in the window, oldest first, filtered unless --all, and what was dropped."""
    since = dt.datetime.now(dt.timezone.utc).timestamp() + source.epoch - days * 86400
    try:
        rows = con.execute(source.query, (int(since * source.scale),)).fetchall()
    except sqlite3.DatabaseError as e:
        sys.exit(f"Cannot read the history database: {e}\n"
                 f"  If {source.name} is mid-write, quit it and try again.")

    dropped: Counter = Counter()
    visits: list[Visit] = []
    for row in rows:
        url = row["url"] or ""
        if not keep_all:
            if bucket := source.junk(row):
                dropped[bucket] += 1
                continue
            if urlsplit(url).scheme not in WEB_SCHEMES:
                dropped["browser-internal pages"] += 1
                continue
        when = dt.datetime.fromtimestamp(row["visit_time"] / source.scale - source.epoch)
        # A title can carry newlines and pipes; both would break the table it lands in.
        title = " ".join((row["title"] or "").split())
        # Chrome falls back to the URL when a page has no title of its own, and Firefox
        # leaves it null. That URL is the one thing this document is trying not to be, so
        # both become untitled instead.
        if title.startswith(("http://", "https://")):
            title = ""
        visits.append(Visit(when, url, title, row["visit_duration"] or 0))
    return visits, dropped


# ── grouping ─────────────────────────────────────────────────────────────────

# Sites stamp their own name onto every page title. Once a row already says which site it
# is, that suffix is repeated noise — half of a LinkedIn title is the word LinkedIn.
SEPARATORS = (" | ", " — ", " – ", " · ", " -- ", " - ")


def house_suffix(titles: list[str]) -> str:
    """The label a site puts on the end of most of its titles, or "" if it does not.

    Established by agreement rather than by a list of sites: split each title on its last
    separator, and if one tail is short and lands on at least half of them, that is the
    site's own name being repeated.
    """
    tails: Counter = Counter()
    for title in titles:
        for sep in SEPARATORS:
            if sep in title:
                tails[title.rsplit(sep, 1)[1]] += 1
                break
    if not tails:
        return ""
    tail, n = tails.most_common(1)[0]
    return tail if n >= 2 and n * 2 >= len(titles) and len(tail) <= 30 else ""


def shorten(title: str, suffix: str, limit: int = 60) -> str:
    """One page title, with the site's own name off the end and a cap on the rest."""
    for sep in SEPARATORS:
        if suffix and title.endswith(sep + suffix):
            title = title[: -len(sep + suffix)]
            break
    title = title.strip() or "(untitled)"
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def union(spans: list[tuple[float, float]]) -> int:
    """Seconds covered by a set of possibly-overlapping intervals.

    Tabs run in parallel, so adding `visit_duration` up gives days longer than a day —
    six tabs open for an hour is one hour, not six. Merging the intervals is what makes
    the time columns something you can read as time.
    """
    ordered = sorted(spans)
    total, (start, end) = 0.0, ordered[0]
    for s, e in ordered[1:]:
        if s > end:
            total += end - start
            start, end = s, e
        else:
            end = max(end, e)
    return int(total + end - start)


class Block:
    """One site, in one hour: how many visits, how long, and what the pages were called."""

    __slots__ = ("host", "hour", "spans", "titles", "visits")

    def __init__(self, hour: dt.datetime, host: str) -> None:
        self.hour = hour
        self.host = host
        self.visits = 0
        self.spans: list[tuple[float, float]] = []
        self.titles: Counter = Counter()

    def add(self, v: Visit) -> None:
        self.visits += 1
        self.titles[v.title] += 1
        # Clamped to the hour it is filed under, so no site-hour can claim more than an
        # hour. A visit that outlives its hour loses the overhang: time on page is a
        # floor, which is the honest direction for a number `visit_duration` often
        # records as zero anyway.
        opened = v.when.timestamp()
        closes = v.hour.timestamp() + 3600
        self.spans.append((min(opened, closes), min(opened + v.duration / 1e6, closes)))

    @property
    def seconds(self) -> int:
        return union(self.spans)


def group(visits: list[Visit]) -> list[Block]:
    """Collapse visits into one block per site per hour — the unit the document is made of."""
    blocks: dict[tuple[dt.datetime, str], Block] = {}
    for v in visits:
        key = (v.hour, v.host)
        blocks.setdefault(key, Block(*key)).add(v)
    return list(blocks.values())


# ── writing ──────────────────────────────────────────────────────────────────

def dwell(secs: int) -> str:
    """A duration in the roundest form that is still true. Under a minute is not worth a cell."""
    if secs < 60:
        return "—"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{secs % 3600 // 60:02d}m"


def cell(text: str) -> str:
    """A pipe inside a cell ends the cell, and titles are full of them."""
    return text.replace("|", "\\|")


def pages(block: Block, most: int = 3) -> str:
    """What the time on a site actually was: its commonest page titles, then a tail count."""
    suffix = house_suffix(list(block.titles))
    seen: Counter = Counter()
    for title, n in block.titles.items():
        seen[shorten(title, suffix)] += n
    named = "; ".join(f"{t} ×{n}" if n > 1 else t for t, n in seen.most_common(most))
    if len(seen) > most:
        named += f"; +{len(seen) - most} more"
    return named


def row(*cells: str) -> str:
    """One markdown table row, so the optional time column is one `append` and not a format."""
    return "| " + " | ".join(cells) + " |"


def render(blocks: list[Block], days: int, dropped: Counter, source: Source) -> str:
    """The whole document: an H1 the bundle can title itself from, a summary, then the days.

    Every table here has an optional last column. Where the browser records no duration
    the column is dropped rather than filled with `dwell(0)`, whose em-dash means "under
    a minute" — a quiet, wrong claim about every row in the file.
    """
    timed = source.durations
    by_day: dict[dt.date, list[Block]] = {}
    for b in blocks:
        by_day.setdefault(b.hour.date(), []).append(b)

    first, last = min(by_day), max(by_day)
    sites: Counter = Counter()
    spans: dict[str, list[tuple[float, float]]] = {}
    for b in blocks:
        sites[b.host] += b.visits
        spans.setdefault(b.host, []).extend(b.spans)
    total = sum(sites.values())

    def clock(rows: list[Block]) -> str:
        """Wall-clock time across a set of blocks — the union, so parallel tabs count once."""
        return dwell(union([s for b in rows for s in b.spans]))

    def timed_cols(*head: str) -> list[str]:
        """A header row and its alignment row, with `On page` only if there is one."""
        cells = [*head] + (["On page"] if timed else [])
        return [row(*cells), row(*(["---"] + ["---:"] * (len(cells) - 1)))]

    # Which browser is in the title because a week can carry one attachment per browser,
    # and the bundle names an attachment by its H1.
    window = f"last {days} days" if days > 1 else "last 24 hours"
    out = [f"# Browsing history ({source.name}) — {window}", ""]
    out += ["## Summary", ""]
    out += [f"- **Window:** {first:%A, %-d %B} → {last:%A, %-d %B %Y}"]
    out += [(f"- **Visits:** {total:,} across {len(sites):,} sites, "
             f"grouped into {len(blocks):,} site-hours")]
    if dropped:
        out += ["- **Filtered out:** "
                + ", ".join(f"{n:,} {what}" for what, n in dropped.most_common())
                + " — rerun with `--all` to keep them"]
    out += [""]

    out += timed_cols("Day", "Visits", "Sites")
    for day in sorted(by_day):
        rows = by_day[day]
        cells = [f"{day:%a %-d %b}", f"{sum(b.visits for b in rows):,}",
                 f"{len({b.host for b in rows}):,}"]
        out += [row(*cells, *([clock(rows)] if timed else []))]
    out += [""]

    out += ["Where the time went, by visits:", ""]
    out += timed_cols("Site", "Visits", "Share")
    for host, n in sites.most_common(20):
        cells = [cell(host), f"{n:,}", f"{n / total:.0%}"]
        out += [row(*cells, *([dwell(union(spans[host]))] if timed else []))]
    if timed:
        out += ["", (f"Time on page is a floor: it counts only what {source.name} recorded a "
                     "duration for, and two sites open at once each count the hour, so the "
                     "site rows overlap each other. Only the per-day column is wall-clock."), ""]
    else:
        out += ["", (f"{source.name} records no per-visit duration, so this document has no "
                     "time-on-page column — the counts and the spread of site-hours are the "
                     "whole of what it can say. A site-hour means at least one visit in that "
                     "hour, not an hour spent there."), ""]

    for day in sorted(by_day):
        rows = by_day[day]
        heading = f"## {day:%A, %-d %B} — {sum(b.visits for b in rows):,} visits"
        out += [heading + (f", {clock(rows)} on page" if timed else ""), ""]
        head = ["Hour", "Site", "Visits"] + (["On page"] if timed else []) + ["Pages"]
        out += [row(*head), row(*(["---", "---", "---:"]
                                  + (["---:"] if timed else []) + ["---"]))]
        # Hour ascending so the day reads forwards; within an hour, the busiest site first.
        for b in sorted(rows, key=lambda b: (b.hour, -b.visits, b.host)):
            cells = [f"{b.hour:%H:%M}", cell(b.host), str(b.visits)]
            cells += [dwell(b.seconds)] if timed else []
            out += [row(*cells, cell(pages(b)))]
        out += [""]

    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7, help="Window in days (default: %(default)s).")
    ap.add_argument("--browser", choices=("chrome", "firefox", "safari"), default="chrome",
                    help="Whose history to read (default: %(default)s). Only picks the "
                         "default path — with --history, the file's own schema decides.")
    ap.add_argument("--profile", default=None,
                    help="Which profile. Chrome takes the directory name ('Default', "
                         "'Profile 1'); Firefox takes the name you know it by "
                         "('default-release'), since its directories carry a salt; Safari "
                         "has no profiles and takes none. Default: Chrome's 'Default', or "
                         "the profile Firefox launches.")
    ap.add_argument("--history", type=Path,
                    help="A history database to read instead, of either family — a "
                         "Chromium relative's `History` (Brave, Chromium, Edge, Arc) or a "
                         "Firefox relative's `places.sqlite` (Zen, LibreWolf, Waterfox).")
    ap.add_argument("--all", action="store_true",
                    help="Keep redirect hops, subframes and browser-internal pages.")
    ap.add_argument("--out", type=Path,
                    help="Where to write (default: browsing/browsing-<browser>-<date>-<N>d.md). "
                         "`-` writes to stdout and saves nothing.")
    args = ap.parse_args()

    if args.days < 1:
        sys.exit("--days must be at least 1.")

    if args.history:
        db = args.history.expanduser()
    elif args.browser == "firefox":
        db = firefox_profile(args.profile)
    elif args.browser == "safari":
        if args.profile:
            sys.exit("Safari keeps one history and has no profiles — drop --profile.")
        db = SAFARI
    else:
        db = CHROME / (args.profile or "Default") / "History"
    check_access(db, "browser history",
                 "  Pass --profile for another profile of the same browser, --browser for\n"
                 "  the other family, or --history to name a database outright.")
    con = connect(db)
    source = sniff(con, db)
    visits, dropped = fetch(con, source, args.days, args.all)
    if not visits:
        # Safari has one history, so there is no other profile to have picked wrongly.
        hint = ("" if source is SAFARI_SOURCE else
                f"\n  Wrong profile? The ones that exist: ls '{db.parent.parent}'")
        sys.exit(f"No visits in the last {args.days} days in {db}.{hint}")
    blocks = group(visits)
    doc = render(blocks, args.days, dropped, source)

    if str(args.out) == "-":
        try:
            sys.stdout.write(doc)
            sys.stdout.flush()
        except BrokenPipeError:
            # Reader closed early (e.g. `| head`) — exit quietly, as hunking_obsidian.py does.
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return

    # The browser is in the filename so a week can carry both without one clobbering the
    # other — they are different weeks, seen from two browsers, not two takes on one.
    stamp = f"{source.name.lower()}-{dt.date.today().isoformat()}-{args.days}d"
    out = args.out or REPO_ROOT / "browsing" / f"browsing-{stamp}.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    # This file is every page you looked at. Refuse to put it anywhere git will take it.
    if subprocess.run(["git", "check-ignore", "-q", str(out)]).returncode != 0:
        sys.exit(f"Refusing to write {out}: it is not gitignored, and it holds real\n"
                 "  browsing history. Add 'browsing/' to .gitignore, then re-run.")
    out.write_text(doc)

    say(f"\n{source.name}: {len(visits):,} visits · {len({v.host for v in visits}):,} sites · "
        f"{len(blocks):,} rows · {len(doc):,} characters")
    for what, n in dropped.most_common():
        say(f"(filtered)  {n:,} {what}")
    say(f"\n  wrote  {link(out)}")
    say("\nAdd it to this week's session front-matter (`just weekly` does this for you):\n"
        f"  attachments: {out.resolve()}")


if __name__ == "__main__":
    main()
