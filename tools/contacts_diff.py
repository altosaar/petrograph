#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
contacts_diff — what changed between two contact snapshots.

Takes two JSON files written by extract_contacts.py and reports who you talk to
more, who you talk to less, who is new, and who has gone quiet. The comparison is
messages per month rather than raw totals, so a 6-month snapshot and a 12-month one
are still comparable.

Contacts are matched on `key` — the normalized phone, email, or group id — and not
on name or rank. A name can change when Contacts is edited and a rank changes every
run; the key is the person.

Nothing here reads a database or the network: it is two JSON files and arithmetic.

Usage:
    ./contacts_diff.py                              # two newest snapshots
    ./contacts_diff.py old.json new.json
    ./contacts_diff.py --min-change 2 --top 40
"""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

SNAPSHOTS = Path("messages/snapshots")


def rate(contact: dict, months: int) -> float:
    """Messages per month, so windows of different lengths compare honestly."""
    return contact["count"] / max(months, 1)


def load(path: Path) -> tuple[dict, dict]:
    data = json.loads(path.read_text())
    months = data.get("months") or 1
    by_key = {}
    for c in data["contacts"]:
        # `key` arrived with the diffing work; older snapshots have to fall back to
        # the first handle, which is the same string for everyone but a group.
        key = c.get("key") or (c["handles"][0] if c.get("handles") else c["name"])
        by_key[key] = c
    return data, by_key


def bar(delta: float, width: int = 14) -> str:
    """A signed bar, so the shape of the change reads before the numbers do."""
    n = min(width, int(abs(delta)))
    return ("+" if delta > 0 else "-") * max(1, n)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("old", nargs="?", type=Path, help="Earlier snapshot.")
    ap.add_argument("new", nargs="?", type=Path, help="Later snapshot.")
    ap.add_argument("--min-change", type=float, default=1.0, metavar="N",
                    help="Hide changes smaller than N messages/month. Default 1.")
    ap.add_argument("--top", type=int, default=25, metavar="N",
                    help="Rows per section. Default 25; 0 for all.")
    ap.add_argument("--dir", type=Path, default=SNAPSHOTS,
                    help=f"Where snapshots live. Default {SNAPSHOTS}")
    args = ap.parse_args()

    if args.old and args.new:
        old_path, new_path = args.old, args.new
    else:
        found = sorted(args.dir.glob("contacts-*.json"))
        if len(found) < 2:
            sys.exit(f"Need two snapshots in {args.dir} to compare; found {len(found)}.\n"
                     f"  Take one now with:  just contacts-snapshot\n"
                     f"  Then again later, and this will diff them.")
        old_path, new_path = found[-2], found[-1]

    for p in (old_path, new_path):
        if not p.exists():
            sys.exit(f"Not found: {p}")

    old_data, old = load(old_path)
    new_data, new = load(new_path)
    old_months = old_data.get("months") or 1
    new_months = new_data.get("months") or 1

    moved, gone, arrived = [], [], []
    for key, c in new.items():
        if key in old:
            before, after = rate(old[key], old_months), rate(c, new_months)
            moved.append((after - before, before, after, c["name"], c.get("kind", "person")))
        else:
            arrived.append((rate(c, new_months), c["name"], c.get("kind", "person")))
    for key, c in old.items():
        if key not in new:
            gone.append((rate(c, old_months), c["name"], c.get("kind", "person")))

    up = sorted((m for m in moved if m[0] >= args.min_change), key=lambda m: -m[0])
    down = sorted((m for m in moved if m[0] <= -args.min_change), key=lambda m: m[0])
    arrived.sort(key=lambda a: -a[0])
    gone.sort(key=lambda g: -g[0])

    def trim(rows):
        return rows[:args.top] if args.top else rows

    def tag(kind):
        return " (group)" if kind == "group" else ""

    w = 34
    print(f"{old_path.name}  →  {new_path.name}")
    print(f"{old_data['window']['start']} → {old_data['window']['end']}   "
          f"({old_months}mo, {len(old)} contacts)")
    print(f"{new_data['window']['start']} → {new_data['window']['end']}   "
          f"({new_months}mo, {len(new)} contacts)")
    print("\nRates are messages per month. Both windows are normalized, so the "
          "numbers\ncompare even when the snapshots cover different spans.")

    for title, rows in (("TALKING MORE", trim(up)), ("TALKING LESS", trim(down))):
        print(f"\n{title}")
        if not rows:
            print(f"  nothing moved by {args.min_change:g}+ messages/month")
            continue
        print(f"  {'':<{w}} {'before':>8} {'after':>8} {'change':>8}")
        for delta, before, after, name, kind in rows:
            label = (name + tag(kind))[:w]
            print(f"  {label:<{w}} {before:>8.1f} {after:>8.1f} {delta:>+8.1f}  {bar(delta)}")

    for title, rows, note in (
        ("NEW SINCE THE LAST SNAPSHOT", trim(arrived), "not present before"),
        ("GONE QUIET", trim(gone), "no longer clears the floor"),
    ):
        print(f"\n{title}  ({note})")
        if not rows:
            print("  none")
            continue
        for r, name, kind in rows:
            print(f"  {(name + tag(kind))[:w]:<{w}} {r:>8.1f}")

    steady = len(moved) - len(up) - len(down)
    print(f"\n{len(up)} up · {len(down)} down · {steady} steady · "
          f"{len(arrived)} new · {len(gone)} gone")
    if args.top and any(len(r) > args.top for r in (up, down, arrived, gone)):
        print(f"(sections capped at {args.top} rows — pass --top 0 for everything)")


if __name__ == "__main__":
    main()
