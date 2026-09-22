#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
eleven_tts — narrate a microlite markdown file via ElevenLabs.

Sibling to compact_tts.py (Cartesia) and chatterbox_tts.py (local): same
strip-and-synthesise shape, ElevenLabs API. Strips YAML front-matter and markdown, then
writes the narration beside the markdown that produced it — inside sessions/<date>/ the
session names it, so a week's inputs and its audio are one folder: lifelog-<date>-voice.wav
for the narration, 24-bit because it is only a stage, and lifelog-<date>.mp3 for the master
once --music has laid a bed under it. The API's mp3 is decoded once and the master is the
next thing encoded; nothing in between is lossy.

Everything either side of synthesis lives in _speech.py and is shared with the local
engine, so a letter narrated here and a letter narrated there are shaped by one piece of
code rather than by two that agree for now. This is the default engine; chatterbox_tts.py
is the one that keeps the letter on the machine, and _speech.resolve_engine decides which
of them a `just render` or a `review-and-narrate` actually calls.

Highest-quality defaults: eleven_multilingual_v2 at mp3_44100_192 (Creator+ tier).
The API key is read from ELEVENLABS_API_KEY — environment or a local .env, never
hardcoded and never committed. Characters billed are printed after each request.

Long-form audio drifts quieter over a single generation. --chunk splits the text
into fresh short requests (each resets the drift) stitched with ffmpeg; --normalize
compresses the dynamic range, RMS-normalises, and caps the true peak below -3 dBTP.
Every chunk is cached on disk under share/.tts-cache, keyed by the text, the voice, the
model, the format and the two neighbouring chunks it is conditioned on. The cache is written
the instant a chunk comes back, not at the end of the run — so a render that dies on chunk 40
of 60, on a quota that ran out or a dropped connection, has already banked the 40 it paid for,
and running exactly the same command again buys only what is missing. Without that, ElevenLabs
bills for audio that goes into a temporary directory and is then deleted. --no-cache opts out.

--dry-run prices a render without calling anything, needing no API key, and says how long the
narration will run — cached chunks are measured off the audio rather than estimated, so
re-checking an unchanged file is exact. Pass --music and it also reports the backing bed's
length and how many times it would loop underneath.

Chunks are fanned out over a bounded thread pool (--concurrency, default 3) and
reassembled by index, so parallelism cuts wall-clock without changing the audio.

Pauses follow the document's shape rather than punctuation alone: the markdown is
parsed into blocks, and the beat after each one is set by what it was — section
heading longest (--header-pause), bullet item and line break a little more than a
plain sentence break (--bullet-pause / --newline-pause / --sentence-pause). Headings
and bullets also get a full stop, since without one the voice runs a heading straight
into the paragraph beneath it.

Usage:
    ./eleven_tts.py input.md
    ./eleven_tts.py input.md --limit 50                  # cap chars sent (conserve credits)
    ./eleven_tts.py input.md --chunk 300 --normalize     # long-form: stitched + leveled
    ./eleven_tts.py input.md --voice <id> --out out.mp3
    ./eleven_tts.py input.md --model eleven_v3 --format mp3_44100_128
"""

from __future__ import annotations
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# Sibling modules, no dependencies of their own — see _names.py on why the rule lives there
# and not in mix_music.py, which is the tool that actually writes the master, and _speech.py
# on why everything from the markdown to the mp3 is shared with the local engine.
from _names import master_name, voice_name
from _overwrite import guard
from _term import link, Steps
from _env import REPO_ROOT, api_key
from _speech import (
    bed_length, block_summary, cache_path, chunk_text, clamp, clock, encode,
    estimate_length, music_tracks, normalize, stitch, strip_markdown, write_atomic,
)

BASE = "https://api.elevenlabs.io"
MODEL = "eleven_multilingual_v2"  # high-quality long-form default
OUTPUT_FORMAT = "mp3_44100_192"  # highest-quality mp3 (Creator+ tier)
FALLBACK_FORMAT = "mp3_44100_128"  # highest mp3 below the Creator tier
DEFAULT_VOICE = "WeAAwKYcS06VmXw086yZ"
# Synthesised chunks, keyed by everything that decides what they sound like. Under share/,
# which is gitignored whole — these are the letter, read aloud.
DEFAULT_CACHE_DIR = REPO_ROOT / "share" / ".tts-cache"
DEFAULT_CONCURRENCY = 3  # bounded to the tier's request-concurrency cap, not the chunk count

# Honed voice_settings (see the Get-voice-settings schema): stability/similarity_boost/style
# are 0-1 doubles, speed is a multiplier, speaker-boost is a bool. multilingual_v2 honors all.
VOICE_SETTINGS = {
    "stability": 0.4,
    "similarity_boost": 0.7,
    "style": 0.5,
    "use_speaker_boost": True,
    "speed": 0.9,
}

# Measured, not guessed: a letter of a few thousand words came back at roughly this rate at the
# settings above — 12.7 characters per second, pauses included. Only ever a fallback; once a
# chunk is in the cache, --dry-run reads its real duration instead of extrapolating.
CHARS_PER_SECOND = 12.7


def insert_pauses(blocks: list[tuple[str, str]], sentence: int, newline: int,
                  bullet: int, header: int) -> str:
    # Add breathing room with spaced hyphens. ElevenLabs reads "-" as a short pause (more
    # dashes = longer), and unlike <break> tags plain text survives the chunk/stitch
    # boundaries, so pauses land consistently. The beat that follows a block depends on
    # what the block was: a section heading gets the longest, a bullet or a line break a
    # little more than a plain sentence break.
    def gap(dashes: int) -> str:
        return " " + " ".join("-" * dashes) + " " if dashes > 0 else " "

    after = {"header": header, "bullet": bullet, "text": newline}
    out: list[str] = []
    for i, (kind, text) in enumerate(blocks):
        out.append(gap(sentence).join(re.split(r"(?<=[.!?])\s+", text)))
        if i < len(blocks) - 1:
            out.append(gap(after[kind]))
    return "".join(out).strip()


def chunk_path(cache_dir: Path, text: str, voice_id: str, model: str, fmt: str,
               settings: dict | None, previous_text: str | None, next_text: str | None) -> Path:
    """Where one chunk of audio lives, named for everything that decides how it sounds.

    previous_text/next_text are in the key because they are not decoration: they are sent as
    boundary conditioning, so the same sentence in a different neighbourhood is a different
    recording. Leaving them out would serve a chunk that flows into the wrong line.

    The payload is what it has always been, key for key — changing its shape would miss every
    chunk already on disk and re-bill a letter that has already been paid for once.
    """
    return cache_path(cache_dir, {
        "text": text, "voice": voice_id, "model": model, "format": fmt,
        "settings": settings or VOICE_SETTINGS, "prev": previous_text, "next": next_text,
    }, ".mp3")


def cached_synthesize(cache_dir: Path | None, text: str, voice_id: str, model: str, fmt: str,
                      key: str, settings: dict | None = None,
                      previous_text: str | None = None,
                      next_text: str | None = None) -> tuple[bytes, bool]:
    """One chunk, from disk if it has ever been synthesised before. Returns (audio, was_cached).

    Written the instant it comes back, not at the end of the run. That is the whole point:
    a run that dies on chunk 40 of 60 — a quota that ran out, a dropped connection — has
    already banked the 39 it paid for, and the next attempt buys only what is missing.
    Without it, ElevenLabs bills for work that goes into a temporary directory and is then
    deleted, which is exactly how a failed render costs full price twice.
    """
    if cache_dir is None:
        return synthesize(text, voice_id, model, fmt, key, settings, previous_text, next_text), False
    path = chunk_path(cache_dir, text, voice_id, model, fmt, settings, previous_text, next_text)
    if path.exists() and path.stat().st_size:
        return path.read_bytes(), True
    audio = synthesize(text, voice_id, model, fmt, key, settings, previous_text, next_text)
    write_atomic(path, audio)
    return audio, False


def synthesize(
    text: str, voice_id: str, model: str, fmt: str, key: str,
    settings: dict | None = None,
    previous_text: str | None = None, next_text: str | None = None, retries: int = 4,
) -> bytes:
    # previous_text/next_text give the model boundary context so stitched chunks flow (not billed).
    # Raises RuntimeError (never sys.exit) so it is safe to call from worker threads.
    payload = {"text": text, "model_id": model, "voice_settings": settings or VOICE_SETTINGS}
    if previous_text:
        payload["previous_text"] = previous_text
    if next_text:
        payload["next_text"] = next_text
    data = json.dumps(payload).encode()
    for attempt in range(retries):
        req = Request(
            f"{BASE}/v1/text-to-speech/{voice_id}?output_format={fmt}",
            data=data,
            headers={"xi-api-key": key, "Content-Type": "application/json"},
        )
        try:
            with urlopen(req, timeout=180) as resp:
                return resp.read()
        except HTTPError as e:
            body = e.read()[:500].decode(errors="replace")
            # "Highest quality possible": if the tier can't serve this format, drop one tier.
            if e.code == 403 and "output_format_not_allowed" in body and fmt != FALLBACK_FORMAT:
                print(f"  {fmt} needs Creator+; falling back to {FALLBACK_FORMAT}.")
                return synthesize(text, voice_id, model, FALLBACK_FORMAT, key, settings, previous_text, next_text, retries)
            # Rate limit / transient server error: back off and retry (concurrency raises 429 odds).
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"ElevenLabs error {e.code}: {body}")
        except URLError:
            if attempt < retries - 1:
                time.sleep(2**attempt)
                continue
            raise
    raise RuntimeError("ElevenLabs: retries exhausted")


def main() -> None:
    ap = argparse.ArgumentParser(description="Narrate a microlite markdown file via ElevenLabs.")
    ap.add_argument("markdown", type=Path, help="Path to the markdown file.")
    ap.add_argument("--voice", default=DEFAULT_VOICE, help=f"Voice id (default: {DEFAULT_VOICE}).")
    ap.add_argument("--model", default=MODEL, help=f"Model id (default: {MODEL}).")
    ap.add_argument("--format", default=OUTPUT_FORMAT, help=f"Output format (default: {OUTPUT_FORMAT}).")
    ap.add_argument("--limit", type=int, help="Cap characters sent, to conserve credits.")
    ap.add_argument("--sentence-pause", type=int, default=1, metavar="DASHES",
                    help="Spaced hyphens between sentences for breathing room — ElevenLabs reads "
                         "'-' as a pause, more = longer (default: 1; 0 = off; 3+ is inconsistent).")
    ap.add_argument("--newline-pause", type=int, default=2, metavar="DASHES",
                    help="Pause after a line break / between paragraphs (default: 2).")
    ap.add_argument("--bullet-pause", type=int, default=2, metavar="DASHES",
                    help="Pause after a bullet-point item (default: 2).")
    ap.add_argument("--header-pause", type=int, default=3, metavar="DASHES",
                    help="Pause after a markdown section heading — the longest beat "
                         "(default: 3; drop to 2 if 3 dashes render inconsistently).")
    ap.add_argument("--chunk", type=int, metavar="CHARS",
                    help="Generate in ≤CHARS-char chunks and stitch (fixes long-form volume drift).")
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR,
                    help="Where synthesised chunks are kept so a failed or repeated run is "
                         f"not billed twice (default: {DEFAULT_CACHE_DIR.relative_to(REPO_ROOT)}).")
    ap.add_argument("--no-cache", action="store_true",
                    help="Synthesise every chunk afresh and store nothing.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Say what would be billed — chunks, characters, cache hits — and "
                         "call nothing. Run this first when credits are tight.")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                    help=f"Parallel chunk requests, bounded to tier limit (default: {DEFAULT_CONCURRENCY}).")
    ap.add_argument("--normalize", action="store_true",
                    help="Compress + RMS-normalise (~-20 dB) with true peak < -3 dBTP (needs ffmpeg).")
    ap.add_argument("--no-compress", action="store_true",
                    help="With --normalize, level only — skip the compressor. Use when the output "
                         "feeds mix_music.py, whose compressor does the dynamics; stacking both "
                         "compresses the voice twice (measured LRA 2.6 vs 5-9 typical).")
    ap.add_argument("--out", type=Path,
                    help="Output .wav (24-bit) or .mp3 (default: beside the input markdown; "
                         "inside sessions/ the session folder names it, e.g. "
                         "lifelog-2026-05-15-voice.wav, leaving the plain name for the master).")
    ap.add_argument("--music", action="append", metavar="PATH[,PATH…]",
                    help="Also render the master over this backing track (via "
                         "mix_music.py). Repeat, or comma-separate, for several: they play in "
                         "order, crossfading into each other, and the sequence loops.")
    ap.add_argument("--mix-engine", choices=("oss", "vst"), default="oss",
                    help="Which mix_music.py voice chain the backing-track mix uses: 'oss' "
                         "(default, pedalboard built-ins, nothing to install) or 'vst' (the "
                         "licensed plug-ins). Only applies with --music.")
    # voice_settings overrides — default to the honed VOICE_SETTINGS.
    ap.add_argument("--stability", type=float, default=VOICE_SETTINGS["stability"])
    ap.add_argument("--similarity-boost", type=float, default=VOICE_SETTINGS["similarity_boost"])
    ap.add_argument("--style", type=float, default=VOICE_SETTINGS["style"])
    ap.add_argument("--speed", type=float, default=VOICE_SETTINGS["speed"])
    ap.add_argument("--no-speaker-boost", action="store_true", help="Disable use_speaker_boost.")
    args = ap.parse_args()

    if not args.markdown.exists():
        sys.exit(f"No such file: {args.markdown}")
    if (args.chunk or args.normalize) and shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg is required for --chunk/--normalize (brew install ffmpeg).")
    music = music_tracks(args.music)
    for path in music:
        if not path.exists():
            sys.exit(f"No such file: {path}")

    settings = {
        "stability": args.stability,
        "similarity_boost": args.similarity_boost,
        "style": args.style,
        "use_speaker_boost": not args.no_speaker_boost,
        "speed": args.speed,
    }
    steps = Steps()
    # A dry run calls nothing, so it should not demand a key to say what a render would cost.
    key = None if args.dry_run else api_key("ELEVENLABS_API_KEY")
    blocks = clamp(strip_markdown(args.markdown.read_text()), args.limit)
    text = insert_pauses(blocks, args.sentence_pause, args.newline_pause,
                         args.bullet_pause, args.header_pause)
    print(f"Pauses (hyphens): sentence {args.sentence_pause}, newline {args.newline_pause}, "
          f"bullet {args.bullet_pause}, header {args.header_pause} — "
          f"{len(blocks)} block(s): {block_summary(blocks)}")
    out = args.out or voice_name(args.markdown)
    # Before the first character is billed, not at the encode: a render that is going to
    # replace a finished one should be stopped, not interrupted after it has been paid for.
    # The master goes in the same question, so the mixer below inherits the answer.
    if not args.dry_run:
        guard(out, master_name(out) if music else None,
              what=f"narrating {args.markdown.name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    bitrate = f"{args.format.split('_')[-1]}k" if args.format.startswith("mp3_") else "128k"
    cache_dir = None if args.no_cache else args.cache_dir.expanduser()
    print(f"voice={args.voice} model={args.model} format={args.format}")
    print(f"voice_settings={settings}")
    steps.mark("parse markdown")

    # What this run actually costs, as opposed to what the document weighs — a chunk served
    # from the cache is not billed, and reporting otherwise overstates every re-render.
    billed_chars = len(text)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        if args.chunk:
            chunks = chunk_text(text, args.chunk)
            workers = max(1, min(args.concurrency, len(chunks)))

            def neighbours(i: int) -> tuple[str | None, str | None]:
                return (chunks[i - 1] if i else None,
                        chunks[i + 1] if i + 1 < len(chunks) else None)

            # Count the hits before spending anything, so the run opens by saying what it
            # will actually cost rather than what the document happens to weigh.
            if cache_dir is not None:
                cached = [chunk_path(cache_dir, chunks[i], args.voice, args.model,
                                     args.format, settings, *neighbours(i))
                          for i in range(len(chunks))]
                hits = [i for i, p in enumerate(cached) if p.exists()]
            else:
                cached = [None] * len(chunks)
                hits = []
            billed = billed_chars = sum(len(chunks[i]) for i in range(len(chunks))
                                        if i not in set(hits))
            print(f"Sending {len(text):,} chars in {len(chunks)} chunk(s) ≤{args.chunk}, "
                  f"{workers} in parallel.")
            if hits:
                print(f"  {len(hits)}/{len(chunks)} chunk(s) already synthesised — "
                      f"{billed:,} of {len(text):,} chars to bill.")
            elif cache_dir is not None:
                print(f"  nothing cached yet — {billed:,} chars to bill.")

            if args.dry_run:
                print(f"\nDry run — nothing called, nothing written.\n"
                      f"  {len(chunks) - len(hits)} chunk(s) would be synthesised, "
                      f"{billed:,} characters billed.")
                secs, measured, rate = estimate_length(chunks, cached, CHARS_PER_SECOND)
                how = ("every chunk measured off the cache — exact"
                       if measured == len(chunks) else
                       f"{measured}/{len(chunks)} measured off the cache, the rest at {rate:.1f} chars/s"
                       if measured else
                       f"nothing cached — {CHARS_PER_SECOND} chars/s assumed")
                print(f"  ≈{clock(secs)} of narration ({how}).")
                if music:
                    bed = bed_length(music)
                    if bed:
                        names = ", ".join(p.name for p in music)
                        print(f"  backing bed {clock(bed)} from {len(music)} track(s) — {names}")
                        print(f"  the sequence would loop {secs / bed:.1f}× under the letter."
                              if secs > bed else
                              f"  {clock(bed - secs)} of backing track spare — no loop needed.")
                return

            def synth_chunk(i: int) -> tuple[bytes, bool]:
                prev, nxt = neighbours(i)
                return cached_synthesize(
                    cache_dir, chunks[i], args.voice, args.model, args.format, key, settings,
                    previous_text=prev, next_text=nxt,
                )

            # Fan out to a bounded pool; reassemble strictly by index so order is preserved.
            audios: list[bytes] = [b""] * len(chunks)
            from_cache = 0
            try:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    futures = {ex.submit(synth_chunk, i): i for i in range(len(chunks))}
                    for done, fut in enumerate(as_completed(futures), 1):
                        i = futures[fut]
                        audios[i], was_cached = fut.result()
                        from_cache += was_cached
                        mark = "cached" if was_cached else f"{len(chunks[i])} chars"
                        print(f"  chunk {i + 1}/{len(chunks)}: {mark} ({done} done)")
            except RuntimeError as e:
                # Whatever came back before the failure is already on disk. Say so, because
                # the useful next move is to run exactly the same command again.
                if cache_dir is not None:
                    kept = sum(1 for i in range(len(chunks))
                               if chunk_path(cache_dir, chunks[i], args.voice, args.model,
                                             args.format, settings, *neighbours(i)).exists())
                    print(f"\n{kept}/{len(chunks)} chunk(s) are cached and will not be billed "
                          "again — re-run the same command to pick up from here.", file=sys.stderr)
                sys.exit(str(e))
            if from_cache:
                print(f"  {from_cache}/{len(chunks)} chunk(s) came from the cache.")

            paths = []
            for i, audio in enumerate(audios):
                p = td / f"chunk{i:03d}.mp3"
                p.write_bytes(audio)
                paths.append(p)
            steps.mark(f"synthesis ({len(chunks)} chunks, {workers} parallel)")
            combined = td / "combined.wav"
            stitch(paths, combined)
            steps.mark("stitch")
        else:
            print(f"Sending {len(text):,} chars from {args.markdown.name} (billed as characters).")
            if args.dry_run:
                print(f"\nDry run — nothing called, nothing written.\n"
                      f"  ≈{clock(len(text) / CHARS_PER_SECOND)} of narration "
                      f"({CHARS_PER_SECOND} chars/s assumed; --chunk measures it properly).")
                return
            combined = td / "combined.mp3"
            try:
                audio, was_cached = cached_synthesize(
                    cache_dir, text, args.voice, args.model, args.format, key, settings)
                if was_cached:
                    print("  came from the cache — nothing billed.")
                    billed_chars = 0
                combined.write_bytes(audio)
                steps.mark("synthesis (single request)")
            except RuntimeError as e:
                sys.exit(str(e))

        if args.normalize:
            rms, peak = normalize(combined, out, bitrate, compress=not args.no_compress)
            how = "Levelled" if args.no_compress else "Compressed + normalised"
            print(f"{how}: RMS {rms:.1f} dB, peak {peak:.1f} dB."
                  + (" (dynamics left to the mixer downstream)" if args.no_compress else ""))
            steps.mark("level + encode")
        elif args.chunk or out.suffix.lower() != ".mp3":
            encode(combined, out, bitrate)
        else:
            out.write_bytes(combined.read_bytes())  # the API's own mp3, untouched

    cost = (f"{billed_chars:,} of {len(text):,} characters billed"
            if billed_chars != len(text) else f"characters billed: {len(text):,}")
    print(f"Saved {out.stat().st_size / 1024:.1f} KB → {link(out)}  ({cost})")

    # Optional backing track: hand the finished narration to mix_music.py, which names the
    # master by dropping the -voice suffix (see master_name) — so the master is the plain name.
    if music:
        music_out = master_name(out)
        mixer = Path(__file__).with_name("mix_music.py")
        over = " → ".join(p.name for p in music)
        print(f"Mixing over {over} → {music_out.name} ({args.mix_engine} chain)")
        subprocess.run(
            [str(mixer), str(out), *[a for p in music for a in ("--music", str(p))],
             "--out", str(music_out),
             "--engine", args.mix_engine],
            check=True,
        )
        steps.mark("mix (mix_music.py)")

    steps.report("timing — eleven_tts")


if __name__ == "__main__":
    main()
