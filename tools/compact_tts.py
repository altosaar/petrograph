#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
compact_tts — turn a compaction markdown file into narrated audio via Cartesia.

Reads a compaction/microlite markdown file, strips YAML front-matter and markdown
syntax, and synthesises it with Cartesia TTS. The audio is a derived artifact: by
default it lands beside the markdown that produced it, and inside sessions/<date>/
the session names it — lifelog-<date>-voice.wav, exactly as eleven_tts.py would name
it, so a week's inputs and its audio are one folder whichever engine narrated it and
the plain lifelog-<date>.mp3 stays free for the master. Cartesia's mp3 is decoded to
24-bit PCM for that, so the master is the only encode after it; an --out ending in .mp3
gets Cartesia's bytes untouched.

The API key is read from CARTESIA_API_KEY — environment or a local .env, never
hardcoded and never committed.

Usage:
    ./compact_tts.py sessions/2026-05-15/lifelog-2026-05-15.md
    ./compact_tts.py <file.md> --voice clive
    ./compact_tts.py <file.md> --out /tmp/out.mp3
    ./compact_tts.py --list-voices
"""

from __future__ import annotations
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError

# One naming rule, whichever engine produced the narration — see _names.py; and one spelling
# rule for the figures in it, so this engine is handed the same words as the other two.
from _names import voice_name
from _numbers import spell_numbers
from _overwrite import guard
from _env import api_key
from _speech import encode

BASE = "https://api.cartesia.ai"
VERSION = "2026-03-01"
MODEL = "sonic-2"
DEFAULT_VOICE = "katie"  # "Katie - Friendly Fixer"


def headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Cartesia-Version": VERSION}


def get_json(path: str, key: str) -> dict:
    req = Request(f"{BASE}{path}", headers=headers(key))
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def list_voices(key: str) -> list[dict]:
    return get_json("/voices", key)["data"]


def pick_voice(voices: list[dict], want: str) -> dict:
    want = want.lower()
    for v in voices:
        if want in v.get("name", "").lower():
            return v
    names = "\n".join(f"  {v['name']}  ({v['id']})" for v in voices)
    sys.exit(f"No voice matching {want!r}. Available:\n{names}")


def strip_markdown(raw: str) -> str:
    # Drop YAML front-matter (the context-engineering log) if present.
    if raw.startswith("---\n"):
        end = raw.find("\n---", 4)
        if end != -1:
            # -1 when the file ends on the closing --- with no trailing newline. Slicing from
            # there would put the front-matter back, and we would narrate and pay for the YAML.
            nl = raw.find("\n", end + 1)
            raw = raw[nl + 1 :] if nl != -1 else ""
    text = re.sub(r"^#+\s+", "", raw, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    # Line by line, so a token is never split across one — _numbers.py works on prose, and a
    # line of prose is what it gets. "$2,480" leaves here as words, the way _speech.py's
    # strip_markdown hands words to the other two engines.
    text = "\n".join(spell_numbers(line) for line in text.splitlines())
    return text.strip()


def synthesize(text: str, voice_id: str, key: str) -> bytes:
    payload = {
        "model_id": MODEL,
        "transcript": text,
        "voice": {"mode": "id", "id": voice_id},
        "output_format": {
            "container": "mp3",
            "encoding": "mp3",
            "sample_rate": 44100,
        },
    }
    req = Request(
        f"{BASE}/tts/bytes",
        data=json.dumps(payload).encode(),
        headers={**headers(key), "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=180) as resp:
            return resp.read()
    except HTTPError as e:
        sys.exit(f"Cartesia error {e.code}: {e.read()[:500].decode(errors='replace')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Narrate a compaction markdown file via Cartesia.")
    ap.add_argument("markdown", nargs="?", type=Path, help="Path to the compaction .md file.")
    ap.add_argument("--voice", default=DEFAULT_VOICE, help=f"Voice name substring (default: {DEFAULT_VOICE}).")
    ap.add_argument("--out", type=Path,
                    help="Output .wav (24-bit) or .mp3 (default: beside the input markdown; "
                         "inside sessions/ the session folder names it, e.g. "
                         "lifelog-2026-05-15-voice.wav, leaving the plain name for the master).")
    ap.add_argument("--list-voices", action="store_true", help="List available voices and exit.")
    args = ap.parse_args()

    key = api_key("CARTESIA_API_KEY")

    if args.list_voices:
        for v in list_voices(key):
            print(f"{v['id']}  {v['name']:32}  {v.get('gender',''):10}  {v.get('country','')}  {v.get('description','')[:60]}")
        return

    if not args.markdown:
        ap.error("markdown file is required (or use --list-voices)")
    if not args.markdown.exists():
        sys.exit(f"No such file: {args.markdown}")

    text = strip_markdown(args.markdown.read_text())
    print(f"Text: {len(text):,} chars from {args.markdown.name}")

    voice = pick_voice(list_voices(key), args.voice)
    print(f"Voice: {voice['name']} ({voice['id']})")

    # -voice inside a session, like eleven_tts.py: this is a narration, and the plain name
    # belongs to the master. Without it a Cartesia render and an ElevenLabs master collide.
    out = args.out or voice_name(args.markdown)
    guard(out, what=f"narrating {args.markdown.name}")
    out.parent.mkdir(parents=True, exist_ok=True)

    print("Requesting TTS…")
    audio = synthesize(text, voice["id"], key)
    if out.suffix.lower() == ".mp3":
        out.write_bytes(audio)  # Cartesia's own mp3, untouched
    else:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "cartesia.mp3"
            src.write_bytes(audio)
            encode(src, out, "192k")  # the bitrate is moot: a .wav is written as PCM
    print(f"Saved {out.stat().st_size / 1024:.1f} KB → {out}")


if __name__ == "__main__":
    main()
