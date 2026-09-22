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
Vault corpus — the text added to the Obsidian vault over a long window, for entity extraction.

The File Recovery reader in vendor/obsidian-microlite answers "what changed this week".
It cannot answer "what changed this year": File Recovery prunes (`keepDays`, 60 on this
machine), the vault is not under version control, and no backup retained the older revisions.
Twelve months of true diffs do not exist to be read. So this tool assembles the best available
corpus and *labels every block with how it was obtained*, so a downstream reader can trust or
discount each one:

    git       real diffs from the local vault mirror (tools/vault_snapshot.sh). Unbounded and
              exact, but only covers the period since the mirror was bootstrapped.
    recovery  real diffs from File Recovery, net over the window (oldest snapshot in the
              window → current file on disk). Exact, but only ~`keepDays` deep.
    entry     dated entries (`260405 …`, `2026-04-05 …`, or the same as a heading) whose date
              falls in the window. Catches text appended to old notes, which no mtime scan can
              separate — but only where the date convention was followed.
    file      whole notes whose mtime falls in the window and which no layer above covered.
              Maximum recall, no notion of "added": old lines come along with the new.

Precedence runs git → recovery → entry → file; identical text is emitted once, by the
highest-confidence layer that produced it.

Writes a markdown file to read and a JSONL sidecar (one object per block) to feed a model.

Usage:
    ./vault_corpus.py                          # last 12 months → corpus/
    ./vault_corpus.py --months 6
    ./vault_corpus.py --layers git,recovery    # exact diffs only, no approximations
    ./vault_corpus.py --help
"""

from __future__ import annotations
import argparse
import datetime as dt
import difflib
import fnmatch
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# The File Recovery reader lives in obsidian-microlite, pinned here as a submodule — one copy,
# in the repo that owns it, which the Obsidian plugin is a tested port of. microlite_hunks.py
# runs it for a week; this reads the same snapshots over a year, which is why it wants the
# loader rather than the renderer.
VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "obsidian-microlite" / "manual"
if not (VENDOR / "hunking_obsidian.py").exists():
    sys.exit(f"Missing {VENDOR / 'hunking_obsidian.py'} — run: git submodule update --init")
sys.path.insert(0, str(VENDOR))
import hunking_obsidian as hob  # noqa: E402  (submodule: File Recovery reader)
from _term import link
from _dates import months_ago

LAYERS = ("git", "recovery", "entry", "file")
DEFAULT_MIRROR = Path.home() / ".local/share/petrograph/vault-history"
DEFAULT_EXCLUDES = (".obsidian/*", ".trash/*", "microlite/*", "microlite-hunks-*.md")
# A dated entry: `260405 …`, `2026-04-05 …`, optionally as a heading or bullet.
DATED = re.compile(r"^(?:#{1,6}\s+|[-*]\s+)?(\d{4}-\d{2}-\d{2}|\d{6})(?=\D|$)")
EARLIEST = dt.date(2010, 1, 1)  # a "date" older than this is a number that looks like one


@dataclass
class Block:
    """One contiguous piece of text, and the provenance that justifies including it."""

    path: str
    date: dt.date
    layer: str
    text: str
    note: str = ""  # human-readable detail, e.g. the commit sha or the base snapshot

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass
class Layer:
    name: str
    blocks: list[Block] = field(default_factory=list)
    how: str = ""
    skipped: str = ""


# ── shared helpers ──────────────────────────────────────────────────────────────────────


def added_lines(before: str, after: str) -> list[str]:
    """The `+` side of a zero-context unified diff — the lines this change introduced."""
    return [
        line[1:]
        for line in difflib.unified_diff(
            before.splitlines(), after.splitlines(), lineterm="", n=0
        )
        if line.startswith("+") and not line.startswith("+++")
    ]


def parse_date(raw: str, today: dt.date) -> dt.date | None:
    try:
        d = (
            dt.date.fromisoformat(raw)
            if "-" in raw
            else dt.date(2000 + int(raw[:2]), int(raw[2:4]), int(raw[4:6]))
        )
    except ValueError:
        return None
    # Guard both ends: `240629` in a filename-like line is a date; `999999` is not, and a
    # far-future date is a typo, not a journal entry.
    return d if EARLIEST <= d <= today + dt.timedelta(days=2) else None


def excluded(rel: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(Path(rel).name, p) for p in patterns)


def vault_markdown(vault: Path, patterns: tuple[str, ...]):
    """(relative posix path, absolute path) for every included note, sorted."""
    for abs_path in sorted(vault.rglob("*.md")):
        rel = abs_path.relative_to(vault).as_posix()
        if not excluded(rel, patterns):
            yield rel, abs_path


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


# ── layer: git mirror ───────────────────────────────────────────────────────────────────


def layer_git(mirror: Path, start: dt.date, patterns: tuple[str, ...]) -> Layer:
    """Real diffs from the vault mirror. The bootstrap commit is a baseline, not an addition."""
    layer = Layer("git", how=f"`git log -p` over the mirror at `{mirror}`")
    if not (mirror / ".git").is_dir():
        layer.skipped = f"no mirror at {mirror} — run tools/vault_snapshot.sh to start one"
        return layer

    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(mirror), "log",
         f"--since={start.isoformat()}", "--reverse", "--no-merges", "--unified=0",
         "--no-color", "--pretty=format:%x00%H%x1f%cI%x1f%P", "--", "*.md"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:  # e.g. a mirror with no commits yet
        layer.skipped = (out.stderr.strip().splitlines() or ["git log failed"])[0]
        return layer

    baseline = 0
    for record in out.stdout.split("\0")[1:]:
        header, _, patch = record.partition("\n")
        sha, when, parents = (header.split("\x1f") + ["", ""])[:3]
        if not parents.strip():
            # The bootstrap commit adds the whole vault at once; counting it as "text added
            # in the window" would be a lie, and would duplicate the `file` layer wholesale.
            baseline += 1
            continue
        date = dt.datetime.fromisoformat(when).date()
        rel, buf = None, []

        def flush():
            if rel and buf and not excluded(rel, patterns):
                layer.blocks.append(
                    Block(rel, date, "git", "\n".join(buf), f"commit {sha[:9]}")
                )

        for line in patch.splitlines():
            if line.startswith("+++ b/"):
                flush()
                rel, buf = line[6:], []
            elif line.startswith("diff --git ") or line.startswith("--- "):
                continue
            elif line.startswith("+") and rel is not None:
                buf.append(line[1:])
        flush()

    if baseline and not layer.blocks:
        layer.skipped = (
            "mirror holds only its baseline commit — real diffs begin at the next snapshot"
        )
    return layer


# ── layer: File Recovery ────────────────────────────────────────────────────────────────


def layer_recovery(
    app_dir: Path, vault: Path, needle: str | None, start: dt.date, patterns: tuple[str, ...]
) -> tuple[Layer, dt.date | None]:
    """Net diff per note: the window's first snapshot → the file as it stands now."""
    layer = Layer("recovery", how="Obsidian File Recovery snapshots, net over the window")
    leveldb = app_dir / "IndexedDB" / hob.LEVELDB
    blob_dir = app_dir / "IndexedDB" / hob.BLOB
    if not leveldb.exists():
        layer.skipped = f"no File Recovery database at {leveldb}"
        return layer, None

    vault_id = hob.vault_id_for(app_dir, needle) if needle else None
    if needle and not vault_id:
        layer.skipped = f"no vault matching {needle!r} in {app_dir / 'obsidian.json'}"
        return layer, None

    # load_snapshots swaps in a native Snappy decompressor when cramjam is installed, which is
    # why it is in the dependencies above — a year of snapshots is the same per-block cost a
    # week is, only twelve times as much of it.
    # Obsidian holds the LevelDB open; copy it before reading, as hunking_obsidian does.
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        shutil.copytree(leveldb, t / "l")
        if blob_dir.exists():
            shutil.copytree(blob_dir, t / "b")
        snaps, _ = hob.load_snapshots(t / "l", t / "b" if blob_dir.exists() else None, vault_id)

    if not snaps:
        layer.skipped = "no snapshots found — is the File recovery core plugin enabled?"
        return layer, None

    start_ms = dt.datetime.combine(start, dt.time.min).timestamp() * 1000
    oldest = min(ts for versions in snaps.values() for ts, _ in versions)

    for rel, versions in snaps.items():
        if excluded(rel, patterns):
            continue
        in_window = [v for v in versions if v[0] >= start_ms]
        if not in_window:
            continue
        prior = [v for v in versions if v[0] < start_ms]
        # Base on the last snapshot *before* the window when there is one; otherwise on the
        # window's first snapshot, so pre-existing text is never claimed as added.
        base = prior[-1][1] if prior else in_window[0][1]
        target_ms, target = in_window[-1]
        note = f"since snapshot {hob.iso(prior[-1][0] if prior else in_window[0][0])}"

        live = vault / rel
        if live.is_file():  # fold in edits made after the last snapshot
            target = read_text(live)
            target_ms = max(target_ms, live.stat().st_mtime * 1000)
            note += ", vs. current file"

        lines = added_lines(base, target)
        if lines:
            layer.blocks.append(
                Block(rel, hob.local(target_ms).date(), "recovery", "\n".join(lines), note)
            )

    return layer, hob.local(oldest).date()


# ── layer: dated entries ────────────────────────────────────────────────────────────────


def layer_entries(vault: Path, start: dt.date, patterns: tuple[str, ...]) -> Layer:
    """Entries whose own date sits in the window, wherever in the vault they live."""
    layer = Layer("entry", how="entries self-dated `YYMMDD` / `YYYY-MM-DD` inside the window")
    today = dt.date.today()
    for rel, abs_path in vault_markdown(vault, patterns):
        current: dt.date | None = None
        buf: list[str] = []

        def flush():
            if current and current >= start and any(line.strip() for line in buf):
                layer.blocks.append(
                    Block(rel, current, "entry", "\n".join(buf).strip(), "self-dated entry")
                )

        for line in read_text(abs_path).splitlines():
            m = DATED.match(line)
            if m:
                d = parse_date(m.group(1), today)
                if d:
                    flush()
                    current, buf = d, [line]
                    continue
            if current:
                buf.append(line)
        flush()
    return layer


# ── layer: whole files ──────────────────────────────────────────────────────────────────


def layer_files(
    vault: Path, start: dt.date, patterns: tuple[str, ...], covered: set[str]
) -> Layer:
    """Notes touched in the window that nothing above accounted for — recall, not precision."""
    layer = Layer("file", how="whole note, mtime in the window, uncovered by the layers above")
    for rel, abs_path in vault_markdown(vault, patterns):
        if rel in covered:
            continue
        mtime = dt.date.fromtimestamp(abs_path.stat().st_mtime)
        if mtime < start:
            continue
        text = read_text(abs_path).strip()
        if text:
            layer.blocks.append(Block(rel, mtime, "file", text, f"mtime {mtime.isoformat()}"))
    return layer


# ── output ──────────────────────────────────────────────────────────────────────────────


def render(blocks: list[Block], layers: list[Layer], start: dt.date, months: int,
           coverage: dt.date | None, dropped: int) -> str:
    notes = len({b.path for b in blocks})
    chars = sum(b.chars for b in blocks)
    out = [
        f"# Vault corpus — last {months} months",
        "",
        f"_generated {dt.datetime.now():%Y-%m-%d %H:%M} · window {start} → {dt.date.today()} · "
        f"{len(blocks)} blocks across {notes} notes · {chars:,} chars_",
        "",
        "Every block is labelled with the layer that produced it. Exactness decreases down the "
        "table; `file` blocks are whole notes, so text in them is *present*, not necessarily "
        "*added* in the window.",
        "",
        "| Layer | Blocks | Notes | Chars | How it was obtained |",
        "|-------|-------:|------:|------:|---------------------|",
    ]
    for layer in layers:
        if layer.blocks:
            kept = [b for b in blocks if b.layer == layer.name]
            out.append(
                f"| `{layer.name}` | {len(kept)} | {len({b.path for b in kept})} | "
                f"{sum(b.chars for b in kept):,} | {layer.how} |"
            )
        else:
            out.append(f"| `{layer.name}` | — | — | — | _{layer.skipped or layer.how}_ |")

    out += ["", "## Coverage", ""]
    if coverage:
        out.append(
            f"Line-level history is genuine only back to **{coverage}** (File Recovery "
            f"retention). Before that date the `entry` and `file` layers are reconstructions "
            f"from the vault's current state, not a record of what was written when."
        )
    else:
        out.append("No snapshot history was readable; every block below is a reconstruction.")
    out.append("")
    out.append(
        "Run `tools/vault_snapshot.sh` on a cadence: once its mirror has two commits the "
        "`git` layer supersedes all of this with exact diffs, with no retention limit."
    )
    if dropped:
        out.append("")
        out.append(f"{dropped} duplicate blocks were dropped in favour of a higher layer.")

    out += ["", "## Blocks — newest first", ""]
    for b in blocks:
        out.append(f"### {b.date} · `{b.path}` · {b.layer}")
        out.append("")
        if b.note:
            out.append(f"_{b.note}_")
            out.append("")
        # Diff layers keep the `+` so the markdown reads as a diff; reconstructions are plain.
        fence, body = (
            ("```diff", "\n".join("+" + line for line in b.text.splitlines()))
            if b.layer in ("git", "recovery")
            else ("```text", b.text)
        )
        out += [fence, body, "```", ""]
    return "\n".join(out) + "\n"


# ── main ────────────────────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--months", type=int, default=12, help="Window length. Default 12.")
    ap.add_argument("--vault-dir", type=Path, default=Path.home() / "notes",
                    help="Vault root. Default ~/notes (symlinks are resolved).")
    ap.add_argument("--mirror", type=Path, default=DEFAULT_MIRROR,
                    help="Git mirror written by tools/vault_snapshot.sh.")
    ap.add_argument("--app-dir", type=Path, default=None, help="Obsidian application directory.")
    ap.add_argument("--vault", default=None,
                    help="Limit File Recovery to one vault by path substring. "
                         "Defaults to the vault-dir's own name.")
    ap.add_argument("--layers", default=",".join(LAYERS),
                    help=f"Comma-separated subset of {','.join(LAYERS)}.")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="Extra path glob to skip; repeatable.")
    ap.add_argument("--out", type=Path, default=None,
                    help="Markdown output. Default corpus/vault-corpus-<date>-<N>mo.md")
    ap.add_argument("--jsonl", type=Path, default=None,
                    help="JSONL sidecar. Default: the markdown path with a .jsonl suffix.")
    args = ap.parse_args()

    wanted = [name.strip() for name in args.layers.split(",") if name.strip()]
    if bad := [name for name in wanted if name not in LAYERS]:
        sys.exit(f"Unknown layer(s): {', '.join(bad)}. Choose from {', '.join(LAYERS)}.")

    vault = args.vault_dir.expanduser().resolve()  # ~/notes is a symlink into iCloud
    if not vault.is_dir():
        sys.exit(f"Vault not found: {vault}")
    app_dir = args.app_dir or hob.default_app_dir()
    needle = args.vault if args.vault is not None else vault.name
    patterns = DEFAULT_EXCLUDES + tuple(args.exclude)
    start = months_ago(args.months)

    layers: list[Layer] = []
    coverage: dt.date | None = None
    for name in LAYERS:
        if name not in wanted:
            layers.append(Layer(name, skipped="not requested"))
            continue
        print(f"… {name}", file=sys.stderr, flush=True)
        if name == "git":
            layers.append(layer_git(args.mirror.expanduser(), start, patterns))
        elif name == "recovery":
            layer, coverage = layer_recovery(app_dir, vault, needle, start, patterns)
            layers.append(layer)
        elif name == "entry":
            layers.append(layer_entries(vault, start, patterns))
        else:
            covered = {b.path for layer in layers for b in layer.blocks}
            layers.append(layer_files(vault, start, patterns, covered))

    # Precedence: identical text is kept once, by the most exact layer that produced it.
    blocks, seen, dropped = [], set(), 0
    for layer in layers:
        for b in layer.blocks:
            digest = hashlib.sha1(b.text.strip().encode()).hexdigest()
            if digest in seen:
                dropped += 1
                continue
            seen.add(digest)
            blocks.append(b)
    blocks.sort(key=lambda b: (b.date, b.path), reverse=True)

    stem = f"vault-corpus-{dt.date.today()}-{args.months}mo"
    md_path = args.out or Path("corpus") / f"{stem}.md"
    jsonl_path = args.jsonl or md_path.with_suffix(".jsonl")
    for p in (md_path, jsonl_path):
        p.parent.mkdir(parents=True, exist_ok=True)

    md_path.write_text(render(blocks, layers, start, args.months, coverage, dropped))
    with jsonl_path.open("w") as fh:
        for b in blocks:
            fh.write(json.dumps({"path": b.path, "date": b.date.isoformat(), "layer": b.layer,
                                 "chars": b.chars, "provenance": b.note, "text": b.text}) + "\n")

    chars = sum(b.chars for b in blocks)
    print(f"\n{len(blocks)} blocks · {len({b.path for b in blocks})} notes · {chars:,} chars "
          f"(~{chars // 4:,} tokens) · window {start} → {dt.date.today()}")
    for layer in layers:
        kept = [b for b in blocks if b.layer == layer.name]
        detail = (f"{len(kept):>5} blocks  {sum(b.chars for b in kept):>9,} chars"
                  if kept else f"      — {layer.skipped or 'nothing in window'}")
        print(f"  {layer.name:<9}{detail}")
    if dropped:
        print(f"  {'(dedup)':<9}{dropped:>5} blocks dropped to a higher layer")
    print(f"\n  read  {link(md_path)}\n  feed  {link(jsonl_path)}")


if __name__ == "__main__":
    main()
