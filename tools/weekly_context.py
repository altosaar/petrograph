#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
weekly_context — run this week's context providers, then scaffold the session that reads them.

The review bundle is the week's Obsidian diff plus whatever else you decided the week was:
the spending, the sleep, the browsing, a calendar export. Those come from elsewhere — some
of them from repositories this one cannot see and should not contain — and until now the
assembling was done by a script over in the finance repository, which had to know
petrograph's template name, its `--new` flag, and its front-matter keys to do it.

This inverts that. A provider is a command that writes one markdown file. petrograph runs
the commands it is told to run, collects the files they produce, and points `attachments:`
at them. It learns nothing else: not what a Plaid is, not where an Oura token lives, not
whether the file it just wired in is a ledger or a sleep report. `attachments:` was already
domain-blind — `weekly_review.py` reads paths and inlines markdown, with no idea what any
of it means — and this extends that blindness from reading to running.

Which means the sensitive material stays where it is. Providers write into their own
repositories, authenticate with their own credentials, and hand back a path. Nothing
personal has to land in this working tree for the review to see it, and a provider you
have not configured is simply a section that is not in your file.

Configuration is `providers.conf` beside this repository's justfile — gitignored, because
it holds the paths of your private repositories. `providers.example.conf` is the tracked
copy to start from.

    [finances]
    command = ~/projects/your-ledger/scripts/weekly-context.sh --days {days} --sync --out {out}
    out     = ~/projects/your-ledger/context/finances-{date}.md

`{days}` is the review window, `{date}` today in ISO form, `{session}` this session's own
directory, and `{out}` the path from the same section — so a provider is told where to
write and this tool knows where to look, rather than parsing whatever the command printed.

Pointing a provider's `out` at `{session}` is what makes a session self-contained: the diff,
the ledger and the sleep report land in one folder beside the file you fill in, which is the
folder you then paste from. `sessions/` is gitignored whole, so that is safe — but it does
mean the copy lives here rather than only in the repository that generated it.

What this decides, and why:
  · A provider that fails does not stop the others. A missing Oura token should cost you
    the sleep attachment, not the week's review — so failures are reported at the end, the
    session is still written with whatever did work, and the exit status is nonzero so a
    script driving this can still tell something was missing.
  · A command that exits 0 without writing its file has failed. The check is the file, not
    the status, because that is the failure that would otherwise reach Claude as a silently
    thinner bundle.
  · A file with nothing in it is louder than a warning at the end deserves to be. A source
    that has not been synced writes a correct document reporting a week in which nothing
    happened, exits 0, and reads exactly like a week in which nothing did — and the model
    will narrate that difference either way. So every attachment is measured for content
    after it lands, and one that is only a frame is called out by name, in the provider's
    own words for it. Not a failure: the bundle is still usable, and which empty weeks are
    real is yours to say, not this tool's.
  · Commands run through the shell, from the repository root. They are yours, in a file
    only you write, and they need pipes and `~` and arguments to be useful.
  · An existing session file is never re-scaffolded from the template. Re-running rewrites
    two front-matter lines in place — `attachments:` and `since:` — and touches nothing
    else, so the paragraph you wrote survives being given fresher paths to read.

Usage:
    ./weekly_context.py                     # every provider, 7 days, then scaffold
    ./weekly_context.py --days 14
    ./weekly_context.py --only finances,oura
    ./weekly_context.py --skip browsing
    ./weekly_context.py --dry-run           # print the commands, run nothing
    ./weekly_context.py --no-session        # just the attachments
    ./weekly_context.py --llm opencode      # ...and let opencode write the read + letter
    ./weekly_context.py --tts-engine chatterbox   # ...narrated on this machine, not posted
    ./weekly_context.py --music ~/beds/rain.flac  # ...over a backing bed of your choosing
    ./weekly_context.py --blank-context     # ...and needing no edits at all
    ./weekly_context.py --help

The last four are decisions about the session rather than about the providers, and they are
written into the file rather than kept here: `llm:`, `engine:` and `music:` are front-matter
keys the review already reads, so choosing one at scaffold time is choosing it once instead of
on every later command. `--blank-context` fills in the two things that are otherwise yours to type —
the title, and the freeform paragraph — with a stand-in title and a context that says plainly
that none was written, which is the honest version of an empty week: the read then works from
the diff and the attachments and does not invent a mood to explain them.
"""

from __future__ import annotations
import argparse
import configparser
import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path
from _term import link, say
from _env import REPO_ROOT
from _llm import LLMS, resolve_llm
from _session import INPUT, ensure_stub
from _speech import (engine_label, engine_tool, engine_where, music_field,
                     music_tracks, resolve_engine)

TOOLS = REPO_ROOT / "tools"
# Where this week's session is written, unless --out-root says otherwise. weekly_review.py has
# always read $PETROGRAPH_OUT for the same question and this file had not, so setting it moved
# half a session: `--new` scaffolded the input file under it while the providers wrote their
# attachments into the repo, and the run then looked for an edit-me.md that was somewhere
# else. The repository is the code; the sessions are yours, and they can live apart from it.
DEFAULT_OUT_ROOT = Path(os.environ.get("PETROGRAPH_OUT") or REPO_ROOT).expanduser().resolve()


CONF = REPO_ROOT / "providers.conf"
EXAMPLE = REPO_ROOT / "providers.example.conf"

# ── the configuration ────────────────────────────────────────────────────────

class Provider:
    """One command, the file it promises to write, and which front-matter key gets the path.

    `role` is `attachment` for almost everything — the file is inlined into the bundle
    under its own heading, and `attachments:` is a list. The exception is `diff`, which
    the week's Obsidian history claims: it is the spine of the read rather than something
    beside it, it gets its own bundle section, and `--resume` reads it back out of the
    session. So it lands in a `diff:` key of its own, and there can be only one.
    """

    __slots__ = ("command", "name", "out", "role")

    def __init__(self, name: str, command: str, out: Path, role: str) -> None:
        self.name = name
        self.command = command
        self.out = out
        self.role = role


ROLES = ("attachment", "diff")


def fill(text: str, days: int, date: dt.date, slug: str, out: str = "") -> str:
    """Substitute the placeholders.

    Done by replacement rather than `str.format` so that a command containing a brace of
    its own — a jq filter, an awk program — survives being a command.
    """
    for token, value in (("{days}", str(days)), ("{date}", date.isoformat()),
                         ("{year}", f"{date:%Y}"),
                         ("{session}", f"sessions/{date:%Y-%m-%d}"),
                         ("{out}", out)):
        text = text.replace(token, value)
    return text


def read_providers(conf: Path, days: int, date: dt.date, slug: str,
                   out_root: Path) -> list[Provider]:
    """Every section of providers.conf, in file order, with placeholders resolved."""
    if not conf.exists():
        example = f"\n  Start from the tracked example:\n    cp {EXAMPLE.name} {conf.name}" \
            if EXAMPLE.exists() else ""
        sys.exit(f"No provider configuration at {conf}{example}")

    # Commands are full of % in URLs and printf formats, and none of it is interpolation.
    parsed = configparser.ConfigParser(interpolation=None)
    try:
        parsed.read(conf)
    except configparser.Error as e:
        sys.exit(f"{conf} is not readable as an INI file:\n  {e}")

    providers: list[Provider] = []
    for name in parsed.sections():
        section = parsed[name]
        if not (command := section.get("command", "").strip()):
            sys.exit(f"{conf}: [{name}] has no `command` line.")
        # Default the destination into this repo's gitignored context/, which is the right
        # answer for a provider whose output is not itself sensitive. Anything private
        # should name a path inside the repository that owns it.
        raw_out = section.get("out", "").strip() or f"context/{name}-{{date}}.md"
        out = Path(fill(raw_out, days, date, slug)).expanduser()
        if not out.is_absolute():
            # Against the output root, not the repo: a relative `out` names something this
            # run produces, and `{session}` expands to one of them.
            out = out_root / out
        role = section.get("role", "attachment").strip().lower()
        if role not in ROLES:
            sys.exit(f"{conf}: [{name}] has role = {role}, which is not one of "
                     f"{', '.join(ROLES)}.")
        providers.append(Provider(name, fill(command, days, date, slug, str(out)), out, role))
    if not providers:
        sys.exit(f"{conf} has no [sections] in it — nothing to run.")
    if len(diffs := [p.name for p in providers if p.role == "diff"]) > 1:
        sys.exit(f"{conf}: {', '.join(diffs)} all claim `role = diff`, and a review has "
                 "one diff.\n  Give the others the default role, or comment them out.")
    return providers


# ── running them ─────────────────────────────────────────────────────────────

def exposed(path: Path) -> bool:
    """Whether this file sits in a git repository that would happily commit it.

    Every provider output is personal by construction — a ledger, a week of heart rates, a
    list of every page someone opened. `browsing_history.py` refuses outright to write
    outside a gitignored path; this cannot refuse, because the file exists by the time we
    look, so it warns instead. A path in no repository at all is nobody's business and
    passes quietly.
    """
    where = path.parent
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=where,
                            capture_output=True, text=True)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return False
    return subprocess.run(["git", "check-ignore", "-q", str(path)],
                          cwd=where, capture_output=True).returncode != 0


def run(provider: Provider) -> str | None:
    """Run one provider. Returns None on success, or the reason it failed.

    The file is the contract. A command can exit 0 and write nothing — a stale API token
    that returns an empty document, a script whose own error path forgot to exit nonzero —
    and that failure is worse than a crash, because it reaches the review as a bundle that
    is quietly missing a week of something.
    """
    before = provider.out.stat().st_mtime if provider.out.exists() else None
    provider.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        done = subprocess.run(provider.command, shell=True, cwd=REPO_ROOT,
                              capture_output=True, text=True)
    except OSError as e:
        return f"could not start the command: {e}"

    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()
        return f"exited {done.returncode}" + (f": {tail[-1][:160]}" if tail else "")
    if not provider.out.exists():
        return f"exited 0 but wrote no {provider.out.name}"
    if provider.out.stat().st_size == 0:
        return f"wrote an empty {provider.out.name}"
    if before is not None and provider.out.stat().st_mtime == before:
        return f"left the existing {provider.out.name} untouched"
    return None


# A heading, an italic caption under it, a table's own rule, a comment, a horizontal rule.
# None of these is a week — they are the frame every one of these tools draws before it
# writes what it found. Table *rows* are deliberately not here: a row is content.
FRAME = re.compile(r"^\s*(#{1,6}\s|_[^_].*_\s*$|\|[\s|:-]+\|\s*$|<!--|-{3,}\s*$|\s*$)")

# Below this much, a document is a frame with a sentence in it. Two numbers rather than one
# because either alone misfires: a real week can be short (one transaction, one hunk), and a
# provider can pad an empty answer over several polite lines.
THIN_LINES = 2
THIN_CHARS = 400


def hollow(path: Path) -> str | None:
    """What a provider wrote instead of a week, or None if it wrote one.

    `run()` asks whether the file exists and has bytes in it, which is the contract a
    provider signs. This asks the weaker, later question: is there anything *in* what it
    wrote? A ledger that has not been synced, a browser closed all week, a ring left on the
    charger — each produces a correct document reporting nothing, exits 0, and reaches the
    read as a week in which nothing happened, which is not the same thing as a week nobody
    recorded. The model cannot tell those apart either, and will narrate the difference.

    Still domain-blind, which is the whole design of this file: it counts what is not frame
    and hands back the provider's own sentence rather than deciding what a transaction is.
    """
    try:
        text = path.read_text(errors="replace")
    except OSError as e:
        return f"could not be read back: {e}"
    body = [ln.rstrip() for ln in text.splitlines() if not FRAME.match(ln)]
    if not body:
        return "nothing but headings"
    if len(body) <= THIN_LINES and sum(len(ln) for ln in body) < THIN_CHARS:
        # Its own words for it — "No transactions in this window." says more than any
        # sentence this file could compose about a domain it deliberately knows nothing of.
        return body[0].strip().strip("*_")
    return None


# ── the session file ─────────────────────────────────────────────────────────

def scaffold(slug: str, template: str, out_root: Path, llm: str = "", engine: str = "",
             blank: bool = False, music: str = "", model: str = "",
             effort: str = "") -> tuple[Path, bool]:
    """Ask weekly_review.py for this week's input file, so there is one scaffolder.

    Returns the path and whether it had to be made. An existing one is left exactly as it
    is — the template would take the week-context paragraph with it, and so would the four
    decisions below, which is why they are passed on rather than applied here: the file this
    tool edits in place is the front-matter's `attachments:`, `diff:` and `since:`, and those
    are this run's findings. Who writes the week, who narrates it, what plays under the
    narration, and whether it needs writing at all are the session's own, and get set once,
    when it is made.
    """
    session = out_root / "sessions" / f"{dt.date.today():%Y-%m-%d}" / INPUT
    if session.exists():
        ensure_stub(session.parent)
        return session, False
    # --out-root passed rather than left to the environment: this is the one call where the
    # two tools have to agree about where a session is, and agreeing by both reading the same
    # variable is agreement only until one of them is run without it.
    subprocess.run([str(TOOLS / "weekly_review.py"), "--new", "--slug", slug,
                    "--template", template, "--out-root", str(out_root)]
                   + (["--llm", llm] if llm else [])
                   + (["--tts-engine", engine] if engine else [])
                   + (["--music", music] if music else [])
                   + (["--model", model] if model else [])
                   + (["--effort", effort] if effort else [])
                   + (["--blank-context"] if blank else []),
                   cwd=REPO_ROOT, check=True, stdout=subprocess.DEVNULL)
    return session, True


INDEX_RE = re.compile(r"\n*<!-- attachment index.*?-->\n*", re.DOTALL)

# The same shape weekly_review.py strips from a front-matter value, so that what this reads
# back out of `attachments:` is exactly what that will read when the review runs.
COMMENT_RE = re.compile(r"\s+#(?:\s.*)?$")


def already_wired(text: str) -> list[Path]:
    """The attachment paths a previous run left in the front-matter."""
    m = re.search(r"^attachments:(.*)$", text, flags=re.MULTILINE)
    if not m:
        return []
    raw = COMMENT_RE.sub("", m.group(1)).strip()
    return [Path(part.strip()) for part in raw.split(",") if part.strip()]


def index_block(attachments: list[Path], diff: Path | None) -> str:
    """A clickable list of what this week's session reads, for whoever opens the file.

    `attachments:` has to stay a plain comma-separated list of paths — that is what
    `weekly_review.py` parses, and markdown links in YAML would be a private dialect of
    front-matter that nothing else in the world reads. So the links live in the body, in
    an HTML comment: `strip_comments` drops it before the bundle is built, which means it
    costs no tokens and cannot be mistaken by the model for something you wrote.
    """
    rows = [(("diff" if p is diff else "attachment"), p) for p in
            ([diff] if diff else []) + attachments]
    lines = ["<!-- attachment index — written by `just weekly`, stripped before the bundle"]
    for role, path in rows:
        size = f"{(path.stat().st_size + 1023) // 1024} KB" if path.exists() else "missing"
        lines.append(f"- {role}: [{path.name}]({path.as_uri()}) — {size}")
    return "\n".join(lines) + "\n-->\n"


def wire(session: Path, attachments: list[Path], diff: Path | None, days: int,
         merge: bool) -> list[Path]:
    """Point the session's front-matter at what the providers produced.

    Absolute paths, because a provider's output usually lives in another repository and
    `weekly_review.py` resolves a relative one against `sessions/`.

    A full run is authoritative and replaces the list. A filtered one — `--only browsing`,
    `--skip oura` — merges instead, because rebuilding one attachment is not a statement
    that the others should stop existing, and silently shortening the bundle is the exact
    failure this tool spends the rest of its time trying to prevent.

    Returns the list as it now stands, so the index block and the summary describe the
    file rather than just this run.
    """
    text = session.read_text()
    if merge:
        # Matched on filename, not full path: a provider that changed where it writes is
        # still the same attachment, and keeping both would hand Claude the week's spending
        # twice. The fresh one wins, and holds its place at the end of the list.
        fresh = {p.name for p in attachments}
        kept = [p for p in already_wired(text) if p.name not in fresh]
        attachments = kept + attachments
    joined = ", ".join(str(p) for p in attachments)
    text, wired = re.subn(r"^attachments:.*$", f"attachments: {joined}", text,
                          count=1, flags=re.MULTILINE)
    if not wired:
        return []
    text = re.sub(r"^since:.*$", f"since: {days} # days of Obsidian history to diff", text,
                  count=1, flags=re.MULTILINE)
    if diff:
        # An absent `diff:` key is not an error: it means weekly_review.py builds the diff
        # itself, which is what happens when the microlite provider is skipped or fails.
        text = re.sub(r"^diff:.*$", f"diff: {diff} # built by the microlite provider", text,
                      count=1, flags=re.MULTILINE)
    elif merge and (m := re.search(r"^diff:(.*)$", text, flags=re.MULTILINE)):
        # Not run this time, but a previous run may have left one — the index should
        # describe the session as it now is, not only what this invocation touched.
        if kept_diff := COMMENT_RE.sub("", m.group(1)).strip():
            diff = Path(kept_diff)
    text = INDEX_RE.sub("\n", text).rstrip() + "\n\n" + index_block(attachments, diff)
    session.write_text(text)
    return attachments


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7,
                    help="Window for every provider, and `since` for the diff (default: "
                         "%(default)s).")
    ap.add_argument("--slug", default="compaction",
                    help="Stem for this week's files (default: %(default)s).")
    ap.add_argument("--template", default="sessions/TEMPLATE.md",
                    help="Template to scaffold from (default: %(default)s).")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT,
                    help="Where sessions/ is written — the scaffold, the attachments the "
                         "providers produce, and everything the review later adds beside "
                         "them (default: $PETROGRAPH_OUT, else the repo). The same flag "
                         "weekly_review.py takes, and passed on to it, so the two cannot "
                         "disagree about where this week is.")
    ap.add_argument("--model",
                    help="The `model:` line to stamp into the session — a Claude alias, or "
                         "the `provider/model` pair opencode wants. Passed through to "
                         "weekly_review.py --new.")
    ap.add_argument("--effort",
                    help="The `effort:` line to stamp into the session — the reasoning effort "
                         "asked of that model.")
    ap.add_argument("--llm", choices=LLMS,
                    help="Who writes the read and the letter when this session is reviewed. "
                         "Written into the session as `llm:`, so it is decided once here "
                         "rather than repeated on every later command. Default: claude.")
    ap.add_argument("--tts-engine", metavar="NAME|SCRIPT",
                    help="Who narrates the letter: 'eleven' posts it to ElevenLabs, "
                         "'chatterbox' synthesises it on this machine and sends nothing, and "
                         "a path is a script of your own. Written into the session as "
                         "`engine:`. Unset, the session says nothing and the default holds.")
    ap.add_argument("--music", action="append", metavar="PATH[,PATH…]",
                    help="A backing track for the narration, written into the session as "
                         "`music:` — the letter is then mixed over it and the master is what "
                         "you play. Repeat, or comma-separate, for several: they play in "
                         "order and the sequence loops under a long letter. Unset, the "
                         "session says nothing and the narration is voice alone.")
    ap.add_argument("--blank-context", action="store_true",
                    help="Scaffold a session that needs no edits: a stand-in title, and a "
                         "week context saying none was written. For a week you want read off "
                         "the diff and the attachments alone.")
    ap.add_argument("--conf", type=Path, default=CONF,
                    help=f"Provider configuration (default: {CONF.name}).")
    ap.add_argument("--only", default="",
                    help="Run only these providers, comma-separated.")
    ap.add_argument("--skip", default="",
                    help="Run everything except these, comma-separated.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would run, run nothing, write nothing.")
    ap.add_argument("--no-session", action="store_true",
                    help="Produce the attachments and stop — do not touch sessions/.")
    args = ap.parse_args()

    if args.days < 1:
        sys.exit("--days must be at least 1.")

    # Asked before a provider runs, not when the scaffold is written: the providers are the
    # slow, billed part of this, and finding out about a mistyped narrator on the far side of
    # them is finding out after paying for it.
    try:
        llm = resolve_llm(args.llm) if args.llm else ""
        engine = resolve_engine(args.tts_engine) if args.tts_engine else ""
        if engine:
            engine_tool(engine)
        music = music_field(args.music)
    except ValueError as e:
        sys.exit(str(e))

    today = dt.date.today()
    out_root = args.out_root.expanduser().resolve()
    providers = read_providers(args.conf.expanduser(), args.days, today, args.slug, out_root)
    names = {p.name for p in providers}
    for flag, given in (("--only", args.only), ("--skip", args.skip)):
        if unknown := {n.strip() for n in given.split(",") if n.strip()} - names:
            sys.exit(f"{flag}: no such provider: {', '.join(sorted(unknown))}\n"
                     f"  {args.conf} defines: {', '.join(sorted(names))}")
    if args.only:
        wanted = {n.strip() for n in args.only.split(",") if n.strip()}
        providers = [p for p in providers if p.name in wanted]
    if args.skip:
        unwanted = {n.strip() for n in args.skip.split(",") if n.strip()}
        providers = [p for p in providers if p.name not in unwanted]
    if not providers:
        sys.exit("Every provider was filtered out — nothing to run.")

    if args.dry_run:
        say(f"Would run {len(providers)} provider(s) from {args.conf}:\n")
        for p in providers:
            role = "" if p.role == "attachment" else f"  (role: {p.role})"
            say(f"  [{p.name}]{role}\n    $ {p.command}\n    → {p.out}\n")
        for label, value in (("llm", llm), ("engine", engine), ("music", music)):
            if value:
                say(f"The session would be scaffolded with {label}: {value}")
        if args.blank_context:
            say("…and with a stand-in title and no week context to write.")
        say("Dry run — nothing was executed and no session was scaffolded.")
        return

    done: list[Path] = []
    diff: Path | None = None
    failed: list[tuple[str, str]] = []
    unsafe: list[Path] = []
    empty: list[tuple[str, Path, str]] = []
    for i, p in enumerate(providers, 1):
        say(f"==> {i}/{len(providers)}  {p.name:<12} {p.out.name}")
        if reason := run(p):
            say(f"    failed — {reason}")
            failed.append((p.name, reason))
        else:
            say(f"    {p.out.stat().st_size:,} bytes" + ("  (the week's diff)"
                                                         if p.role == "diff" else ""))
            if p.role == "diff":
                diff = p.out
            else:
                done.append(p.out)
            if nothing := hollow(p.out):
                say(f"    EMPTY: {nothing} — see below")
                empty.append((p.name, p.out, nothing))
            if exposed(p.out):
                say("    WARNING: not gitignored where it landed — see below")
                unsafe.append(p.out)

    if not args.no_session and (done or diff):
        session, made = scaffold(args.slug, args.template, out_root, llm, engine,
                                 args.blank_context, music, args.model or "",
                                 args.effort or "")
        say(f"\n==> session    {session.name} ({'scaffolded' if made else 'already existed'})")
        if made:
            if llm:
                say(f"    llm: {llm} — writes the read and the letter")
            if engine:
                say(f"    engine: {engine_label(engine)} — the letter is {engine_where(engine)}")
            if music:
                # Separated by the same routine that wrote the line, rather than on every
                # comma: half these filenames are `{Label Co., Ltd.}`, and a count
                # that says two when one track was named is a bug report waiting to be filed.
                beds = music_tracks([music])
                say(f"    music: {', '.join(b.name for b in beds)} — mixed under the narration")
            if args.blank_context:
                say("    title and week context written for you — nothing left to edit")
        elif llm or engine or music or args.blank_context:
            # These are the session's own decisions, and this run is not the one that made it.
            # Rewriting them now would quietly change how a week you had already set up gets
            # written and narrated — so say what was ignored rather than act on it.
            say("    left as it is — `llm:`, `engine:`, `music:` and the week context belong\n"
                "    to the session that already exists. Edit it, or delete it and run this\n"
                "    again.")
        filtered = bool(args.only or args.skip)
        if listed := wire(session, done, diff, args.days, merge=filtered):
            kept = len(listed) - len(done)
            say(f"    {'merged' if filtered else 'wired'} {len(done)} attachment(s)"
                + (f" alongside {kept} already there" if kept > 0 else "")
                + (", the diff" if diff else "") + f", and since: {args.days}")
        else:
            say("    no `attachments:` key in the front-matter — wire these in by hand:\n"
                f"      attachments: {', '.join(str(p) for p in done)}")

    say("\nContext ready:")
    for path in ([diff] if diff else []) + done:
        say(f"  {path.name:<34} {(path.stat().st_size + 1023) // 1024:>5} KB\n"
            f"      {link(path)}")
    if empty:
        say(f"\n!!  {len(empty)} attachment(s) ran fine and came back with nothing in them:")
        for name, path, nothing in empty:
            say(f"      {name:<12} {nothing}")
            say(f"      {'':<12} {path.name}")
        say("    The commands worked; it is the week behind them that is missing. A source\n"
            "    that has not been synced, a browser that was closed, a ring left on the\n"
            "    charger — each writes a correct document saying nothing happened, and a read\n"
            "    cannot tell that apart from a week in which nothing did. Refresh the source\n"
            "    and re-run just this one — `just weekly --only <name>` merges into the\n"
            "    session already scaffolded — before paying for a read of it.")

    if failed:
        say(f"\n{len(failed)} provider(s) failed — the bundle will be missing them:")
        for name, reason in failed:
            say(f"  {name:<12} {reason}")

    if unsafe:
        say(f"\n{len(unsafe)} attachment(s) landed in a git repository that would commit them.")
        say("These hold real spending, real physiology, or every page you opened — add the\n"
            "directory to that repository's .gitignore before your next commit there:")
        for path in unsafe:
            say(f"  {path}")

    if not args.no_session and (done or diff):
        session = out_root / "sessions" / f"{today:%Y-%m-%d}"
        # Relative when the sessions are in the repo, absolute when they are not — a bare
        # `sessions/2026-05-20` is a lie about where the file is if out_root is elsewhere.
        def show(path: Path) -> Path:
            return path.relative_to(out_root) if out_root == REPO_ROOT else path
        if args.blank_context and made:
            # Nothing was left for you to write, so the first step is not a step.
            say(f"\nNext:\n  1. just review-and-narrate {show(session)} --dry-run")
            say("  2. Drop --dry-run when the bundle looks right.")
        else:
            say(f"\nNext:\n  1. Fill in `title` and the week-context paragraph in "
                f"{show(session / INPUT)}")
            say(f"  2. just review-and-narrate {show(session)} --dry-run")
            say("  3. Drop --dry-run when the bundle looks right.")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
