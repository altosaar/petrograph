#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
_overwrite — a render never replaces a finished one without being asked.

Audio is gitignored, so an overwritten master is not recoverable: no commit holds it, and
`just render` re-billing the same letter cannot get the old mix back, because the old mix was
made from a different letter. That is exactly how this module came to exist — a second letter
in the same session folder rendered over the first session's master, and the only copy was
gone by the time the run printed where it had put things.

So: before anything is synthesised, mixed or encoded, every output path that already exists is
listed and a yes is asked for on the terminal. Rules that follow from that:

  Ask early. The prompt belongs before the API call, not before the final encode — the point
  is to stop a run, not to interrupt one you have already paid for.

  Ask once. A yes is written into the environment as PETROGRAPH_OVERWRITE, so the mixer a
  narrator hands off to does not ask again about the same files. Set it yourself (=1) for an
  unattended run that is meant to replace what is there.

  No terminal means no. Under cron, in a pipe, or anywhere /dev/tty cannot be opened, there is
  nobody to ask, and the run stops instead of guessing — PETROGRAPH_OVERWRITE=1 is how such a
  run says it meant it.

Importable and runnable both. `dependencies = []` is load-bearing for the same reason it is in
_names.py: the tools that import this declare no dependencies of their own, and uv resolves
only the entry script's. The runnable form is for the justfile, which cannot import Python:

    ./tools/_overwrite.py sessions/2026-05-22/lifelog-2026-05-22.mp3 …
    # exit 0 = go ahead (nothing there, or a yes); exit 1 = leave it alone
"""

from __future__ import annotations
import os
import sys
from datetime import datetime
from pathlib import Path

ENV = "PETROGRAPH_OVERWRITE"
YES = {"1", "y", "yes", "true", "force"}


def forced() -> bool:
    """Whether the environment has already answered for every prompt in this run."""
    return os.environ.get(ENV, "").strip().lower() in YES


def _describe(p: Path) -> str:
    st = p.stat()
    when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    size = f"{st.st_size / 1048576:.1f} MB" if st.st_size >= 1048576 else f"{st.st_size / 1024:.0f} KB"
    return f"    {p.name}   {size}, written {when}"


def _ask(n: int) -> bool:
    """A yes, from a person, or False. Never raises, never guesses."""
    question = f"\n  Overwrite {'it' if n == 1 else f'all {n}'}? [y/N] "
    # /dev/tty before stdin: these tools are routinely run with their output piped through
    # grep or tail and their input redirected, and the question still has to reach a person.
    # Where there is no controlling terminal to open — a login shell that lost one, some
    # sandboxes — a stdin that is itself a terminal is the same person, so it will do.
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write(question)
            tty.flush()
            return tty.readline().strip().lower() in {"y", "yes"}
    except OSError:
        pass
    if sys.stdin.isatty():
        sys.stderr.write(question)
        sys.stderr.flush()
        return (sys.stdin.readline() or "").strip().lower() in {"y", "yes"}
    print(f"  No terminal to ask on, so this is a no. Set {ENV}=1 to overwrite unattended.",
          file=sys.stderr)
    return False


def guard(*paths, what: str = "this run") -> None:
    """Ask before replacing any of `paths` that exist. Does not return on a no."""
    existing: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        if raw is None:
            continue
        p = Path(raw)
        if p.exists() and p.resolve() not in seen:
            seen.add(p.resolve())
            existing.append(p)
    if not existing:
        return

    where = existing[0].resolve().parent
    print(f"\n!!  {len(existing)} file(s) in {where} would be replaced by {what}:",
          file=sys.stderr)
    for p in existing:
        print(_describe(p), file=sys.stderr)
    print("    Audio is gitignored — replacing these does not put them anywhere recoverable.",
          file=sys.stderr)

    if forced():
        print(f"    {ENV} is set: replacing them without asking.", file=sys.stderr)
        return
    if not _ask(len(existing)):
        sys.exit("\nNothing was written. Move or rename what you want to keep — inside a "
                 "session, a differently-named markdown renders to a name of its own.")
    # A yes covers everything this process goes on to spawn, so the mixer a narrator hands
    # off to does not ask a second time about files already named above.
    os.environ[ENV] = "1"


def main() -> None:
    args = [a for a in sys.argv[1:] if a]
    if not args:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <path>...")
    guard(*args)


if __name__ == "__main__":
    main()
