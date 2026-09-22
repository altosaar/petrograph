"""
_db — opening a database another application owns, without disturbing it.

Not a script. Chrome, Firefox, Safari and Messages all keep SQLite files that are live while
the app is running, and reading them has two failure modes worth spelling out rather than
letting through as a stack trace: macOS holds them behind Full Disk Access, and the file may
simply not be there.

No dependencies, so importing this leaves a tool's `dependencies = []` intact.
"""

from __future__ import annotations
import sqlite3
import sys
from pathlib import Path


def connect(db: Path) -> sqlite3.Connection:
    """Open read-only, without taking a lock.

    immutable=1 is what lets this run while the owning app holds the file: SQLite is told the
    database will not change underneath it, so it takes no lock and writes no journal. The
    cost is the tail — anything the app has not checkpointed yet is not here.
    """
    try:
        con = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
    except sqlite3.OperationalError as e:
        sys.exit(f"Cannot open {db}: {e}")
    con.row_factory = sqlite3.Row
    return con


def check_access(db: Path, what: str, missing: str = "") -> None:
    """Say the two ways this fails in English, rather than as a stack trace.

    `what` names the database in the permission message ("Messages", "the Chrome history").
    `missing` is the advice for a file that is not there at all, which differs per tool —
    a browser can suggest another profile, Messages cannot.
    """
    try:
        db.open("rb").close()
    except PermissionError:
        sys.exit(
            f"Permission denied: {db}\n"
            f"  macOS keeps the {what} database behind Full Disk Access. Grant it to the\n"
            "  app running this command (Terminal, iTerm, Zed, …) in\n"
            "    System Settings → Privacy & Security → Full Disk Access\n"
            "  then fully quit and reopen that app — the grant only applies to a new process."
        )
    except FileNotFoundError:
        sys.exit(f"Not found: {db}" + (f"\n{missing}" if missing else ""))
