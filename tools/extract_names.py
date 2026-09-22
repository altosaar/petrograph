#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["anthropic>=0.69"]
# ///
"""
extract_names — named-entity extraction over a vault corpus, one chunk at a time.

Reads the JSONL written by vault_corpus.py, splits each block into overlapping
chunks, and asks Claude Haiku 4.5 for the names in each. Output is one JSON file
holding both the chunks and the mentions, so names_html.py can render every name
against the text it came from without re-chunking (and re-deriving different
boundaries).

Cheap by construction, in four ways:
  · Haiku 4.5 ($1/MTok in, $5/MTok out) — the least expensive current model.
  · Structured outputs (`output_config.format`), so the model returns validated
    JSON and no tokens are spent on prose or retries after a bad parse.
  · A chunk cache keyed by (model, prompt, text): re-runs cost nothing, an
    interrupted run resumes, and a widened corpus only pays for what is new.
  · `--dry-run` prices the whole job from a local token estimate before spending.

Every returned name is checked against the chunk it came from and dropped if it
isn't there — an extractor that invents a name is worse than one that misses it,
because nothing downstream can tell the difference.

Usage:
    ./extract_names.py --dry-run                  # chunk count + cost estimate
    ./extract_names.py                            # extract from the newest corpus
    ./extract_names.py corpus/<file>.jsonl --limit 20
    ./extract_names.py --help
"""

from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from _env import api_key

MODEL = "claude-haiku-4-5"
PRICE_IN, PRICE_OUT = 1.00, 5.00  # USD per million tokens (Haiku 4.5)
KINDS = ("person", "organization", "place")

# Bumping this invalidates the cache — it is part of the cache key, so a changed
# prompt re-extracts rather than silently mixing old and new results.
PROMPT_VERSION = 1
SYSTEM = """You extract named entities from personal notes and journals.

Return every proper name that appears in the text: people (including first-name-only
and nicknames), organizations, and places. Include a name once per distinct entity,
using the fullest form that appears in this text.

Do not return: pronouns, job titles without a name, generic nouns, dates, months,
weekdays, file names, URLs, or the author's own first-person references. Do not
infer names that are not written in the text. If there are no names, return an
empty list."""

SCHEMA = {
    "type": "object",
    "properties": {
        "names": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                },
                "required": ["name", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["names"],
    "additionalProperties": False,
}


# ── chunking ────────────────────────────────────────────────────────────────────


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Fixed-size chunks with overlap, cut at line boundaries where one is near.

    The standard recursive-split shape: pack lines up to `size`, and carry the last
    `overlap` characters into the next chunk so a name on a boundary is seen whole
    by at least one chunk. A single line longer than `size` is hard-split.
    """
    chunks: list[str] = []
    buf = ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:  # pathological single line — hard-split it
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(line[:size])
            line = line[size:]
        if len(buf) + len(line) > size and buf:
            chunks.append(buf)
            buf = buf[-overlap:] if overlap else ""
        buf += line
    if buf.strip():
        chunks.append(buf)
    return [c for c in chunks if c.strip()]


def build_chunks(blocks: list[dict], size: int, overlap: int) -> list[dict]:
    out = []
    for b in blocks:
        for part in chunk_text(b["text"], size, overlap):
            out.append(
                {
                    "text": part,
                    "path": b["path"],
                    "date": b["date"],
                    "layer": b["layer"],
                    "provenance": b.get("provenance", ""),
                }
            )
    return out


# ── names ───────────────────────────────────────────────────────────────────────


def normalize(name: str) -> str:
    """Lowercase, unpunctuated key. Two spellings of one person must collide here."""
    n = name.strip().lower()
    n = re.sub(r"[’']s\b", "", n)  # possessive
    n = re.sub(r"[^\w\s&-]", " ", n)  # punctuation, keeping & and hyphen
    return re.sub(r"\s+", " ", n).strip()


def grounded(name: str, text: str) -> bool:
    """Is this name actually in the chunk? Guards against a confabulated name."""
    low = text.lower()
    if name.lower() in low:
        return True
    # "Bobby Nyholm" against "nyholm ... bobby" — accept when every word of the name is present.
    words = [w for w in normalize(name).split() if len(w) > 2]
    return bool(words) and all(w in low for w in words)


# ── extraction ──────────────────────────────────────────────────────────────────


def cache_key(text: str) -> str:
    return hashlib.sha1(f"{MODEL}\0{PROMPT_VERSION}\0{text}".encode()).hexdigest()


def extract_one(client, text: str) -> tuple[list[dict], int, int]:
    """One chunk → its names, plus the tokens it cost."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": text}],
    )
    if response.stop_reason == "refusal":
        return [], response.usage.input_tokens, response.usage.output_tokens
    payload = next((b.text for b in response.content if b.type == "text"), "{}")
    names = json.loads(payload).get("names", [])
    return names, response.usage.input_tokens, response.usage.output_tokens


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("corpus", nargs="?", type=Path,
                    help="Corpus JSONL from vault_corpus.py. Default: newest in corpus/.")
    ap.add_argument("--chunk-chars", type=int, default=4000,
                    help="Target chunk size (~1000 tokens). Default 4000.")
    ap.add_argument("--overlap", type=int, default=200,
                    help="Characters carried into the next chunk. Default 200.")
    ap.add_argument("--concurrency", type=int, default=8, help="Requests in flight.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Extract at most N chunks — a cheap sample of a big corpus.")
    ap.add_argument("--layers", default="",
                    help="Comma-separated provenance layers to keep (default: all).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Chunk and price the job; make no API calls.")
    ap.add_argument("--out", type=Path, default=None,
                    help="Default: corpus/names-<corpus-stem>.json")
    ap.add_argument("--cache", type=Path, default=Path("corpus/.extract-cache.json"))
    args = ap.parse_args()

    corpus = args.corpus or max(Path("corpus").glob("*.jsonl"), default=None,
                                key=lambda p: p.stat().st_mtime)
    if not corpus or not corpus.exists():
        sys.exit("No corpus JSONL found — run tools/vault_corpus.py first.")

    blocks = [json.loads(line) for line in corpus.read_text().splitlines() if line.strip()]
    if args.layers:
        keep = {s.strip() for s in args.layers.split(",")}
        blocks = [b for b in blocks if b["layer"] in keep]
    chunks = build_chunks(blocks, args.chunk_chars, args.overlap)
    if args.limit:
        chunks = chunks[: args.limit]

    chars = sum(len(c["text"]) for c in chunks)
    # ~4 chars/token, plus the system prompt on every request.
    est_in = chars // 4 + len(chunks) * 220
    est_out = len(chunks) * 120
    est_cost = est_in / 1e6 * PRICE_IN + est_out / 1e6 * PRICE_OUT
    print(f"{len(chunks)} chunks · {chars:,} chars from {corpus}")
    print(f"estimate: ~{est_in:,} in + ~{est_out:,} out tokens ≈ ${est_cost:.2f} on {MODEL}")
    if args.dry_run:
        return

    cache: dict[str, list] = {}
    if args.cache.exists():
        cache = json.loads(args.cache.read_text())
    cached = sum(1 for c in chunks if cache_key(c["text"]) in cache)
    if cached:
        print(f"{cached} chunks already cached — {len(chunks) - cached} to extract")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key("ANTHROPIC_API_KEY"))
    lock = threading.Lock()
    totals = {"in": 0, "out": 0, "done": 0, "failed": 0}

    def run(index: int) -> tuple[int, list[dict]]:
        text = chunks[index]["text"]
        key = cache_key(text)
        if key in cache:
            return index, cache[key]
        try:
            names, tin, tout = extract_one(client, text)
        except Exception as exc:  # one bad chunk must not lose the whole run
            with lock:
                totals["failed"] += 1
                print(f"  chunk {index}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return index, []
        with lock:
            cache[key] = names
            totals["in"] += tin
            totals["out"] += tout
            totals["done"] += 1
            if totals["done"] % 25 == 0:
                spent = totals["in"] / 1e6 * PRICE_IN + totals["out"] / 1e6 * PRICE_OUT
                print(f"  {totals['done']}/{len(chunks) - cached} extracted · ${spent:.2f}",
                      flush=True)
        return index, names

    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = dict(pool.map(run, range(len(chunks))))
    finally:  # a Ctrl-C mid-run still banks the chunks already paid for
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        args.cache.write_text(json.dumps(cache))

    # Group mentions by normalized name; the display form is the commonest spelling.
    entities: dict[str, dict] = {}
    dropped = 0
    for index, names in results.items():
        chunk = chunks[index]
        for entry in names:
            raw = entry.get("name", "").strip()
            key = normalize(raw)
            if not key or not grounded(raw, chunk["text"]):
                dropped += 1
                continue
            e = entities.setdefault(
                key, {"name": key, "kinds": {}, "surfaces": {}, "mentions": []}
            )
            e["kinds"][entry.get("kind", "person")] = e["kinds"].get(entry.get("kind", "person"), 0) + 1
            e["surfaces"][raw] = e["surfaces"].get(raw, 0) + 1
            e["mentions"].append({"chunk": index, "surface": raw})

    for e in entities.values():
        e["kind"] = max(e["kinds"], key=e["kinds"].get)
        e["display"] = max(e["surfaces"], key=e["surfaces"].get)
        e["count"] = len(e["mentions"])
        dates = sorted(chunks[m["chunk"]]["date"] for m in e["mentions"])
        e["first"], e["last"] = dates[0], dates[-1]
        del e["kinds"], e["surfaces"]

    used = sorted({m["chunk"] for e in entities.values() for m in e["mentions"]})
    remap = {old: new for new, old in enumerate(used)}
    for e in entities.values():
        for m in e["mentions"]:
            m["chunk"] = remap[m["chunk"]]

    out = args.out or Path("corpus") / f"names-{corpus.stem.replace('vault-corpus-', '')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "corpus": str(corpus),
        "model": MODEL,
        "chunk_chars": args.chunk_chars,
        "chunks": [chunks[i] for i in used],
        "entities": sorted(entities.values(), key=lambda e: -e["count"]),
    }, ensure_ascii=False))

    spent = totals["in"] / 1e6 * PRICE_IN + totals["out"] / 1e6 * PRICE_OUT
    print(f"\n{len(entities)} distinct names · "
          f"{sum(e['count'] for e in entities.values())} mentions across {len(used)} chunks")
    if dropped:
        print(f"{dropped} returned names dropped — not found in their own chunk")
    if totals["failed"]:
        print(f"{totals['failed']} chunks failed (see stderr); re-run to retry just those")
    print(f"billed this run: {totals['in']:,} in + {totals['out']:,} out = ${spent:.2f}")
    print(f"\n  wrote  {out}\n  render  ./tools/names_html.py {out}")


if __name__ == "__main__":
    main()
