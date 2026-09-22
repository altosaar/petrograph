#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "ccl-chromium-reader @ git+https://github.com/cclgroupltd/ccl_chromium_reader.git@b51a01c913799f5af2735f964d4627275369e90e",
#   "ccl-simplesnappy    @ git+https://github.com/cclgroupltd/ccl_simplesnappy.git@3d085230baa8c46cf2090ebba29bf6e8eab31087",
#   "cramjam>=2.8",
# ]
# ///
"""
microlite_hunks — the week's Obsidian history, generated here, from one canonical renderer.

There is one implementation of "a week of edits as diffs": `hunking_obsidian.py`, which lives in
the obsidian-microlite repository and is pinned here as the vendor/obsidian-microlite submodule.
The Obsidian plugin is a TypeScript port of it, and that repo's test suite pins the port to this
script's output as a golden oracle — so pressing the ribbon icon in Obsidian and running this
produce the same review. One renderer, two front ends.

This wrapper reimplements nothing: it sets the window and the output path, so a provider can
call it with {days} and {out}, and passes any other flag straight through.

The reader is fast because of a change made where it lives: it spent ~96% of its time in a
pure-Python Snappy decompressor, and now swaps in cramjam's native one when it is installed —
24.6s to 0.9s on the same database, snapshots comparing equal. That is why cramjam is in the
dependencies above; without it the reader stays correct and gets slow again.

WHY NOT DRIVE THE PLUGIN. It would be the same code, but it is the wrong shape for a pipeline:
the plugin only runs inside Obsidian, so it needs the app running and focused, cannot run under
cron or over ssh, and would have to be triggered through a URI handler that does not exist yet.
Reading the note it leaves behind has the same problem one step removed — the note is only as
fresh as the last time somebody pressed the button, and `just weekly` would either review last
week or stop and ask for a keystroke. Generating the hunks here needs nothing running.

Usage:
    ./microlite_hunks.py --days 7 --out microlite.md
    ./microlite_hunks.py --days 30 --out -
"""

from __future__ import annotations
import argparse
import importlib.util
import sys
from pathlib import Path
from _env import REPO_ROOT

ORACLE = REPO_ROOT / "vendor" / "obsidian-microlite" / "manual" / "hunking_obsidian.py"


def load_oracle():
    if not ORACLE.exists():
        sys.exit(
            f"Missing {ORACLE}.\n"
            "The File Recovery reader is pinned as a submodule; run: git submodule update --init"
        )
    spec = importlib.util.spec_from_file_location("hunking_obsidian", ORACLE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["hunking_obsidian"] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    ap = argparse.ArgumentParser(
        description="The week's Obsidian hunks, via the canonical File Recovery reader.",
        epilog="Any other flag is passed through to hunking_obsidian.py unchanged.")
    ap.add_argument("--days", type=int, default=7, help="Window in days (default: 7).")
    ap.add_argument("--out", type=Path, default=Path("-"), help="Destination, or - for stdout.")
    args, passthrough = ap.parse_known_args()

    oracle = load_oracle()
    # Hand the oracle its own argv and let its main() do the work, so the flags, the defaults
    # and the output are the ones that repo tests, rather than a second opinion about them.
    # --net is a first→last diff per note, which is what a weekly read wants: the shape of the
    # week, not every keystroke on the way there.
    sys.argv = ["hunking_obsidian.py", "--since", str(args.days), "--net",
                "--out", str(args.out), *passthrough]
    oracle.main()


if __name__ == "__main__":
    main()
