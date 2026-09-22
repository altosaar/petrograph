#!/usr/bin/env -S uv run --script --managed-python
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["chatterbox-tts>=0.1.7", "numpy", "setuptools<81"]
# ///
# The only tool here that is not `dependencies = []`, and the header earns some explanation.
#
# <3.13 because chatterbox-tts pins torch==2.6.0 below Python 3.14, and torch 2.6 ships no
# wheels above 3.12. >=3.11 because that is what Resemble develop and test against.
#
# setuptools<81 because resemble-perth — the watermarker chatterbox instantiates on every
# model load — imports pkg_resources, which Python stopped shipping at 3.12 and setuptools
# itself removed at 82. perth declares neither, and swallows the ImportError: the package
# imports as None and the model dies on load with "'NoneType' object is not callable",
# several minutes into downloading its own weights. The upper pin is the load-bearing half —
# a bare "setuptools" resolves to a version that has already dropped the module.
#
# --managed-python because uv prefers an already-installed interpreter that satisfies the
# range, and on a Mac carrying both Homebrew prefixes that can be the x86_64 one under
# /usr/local — for which torch publishes no macOS wheels at all, so resolution fails with a
# platform-tag error that has nothing to do with anything you did. Forcing uv's own download
# gets a native interpreter on every machine, which is the only kind that can run this.
"""
chatterbox_tts — narrate a microlite markdown file without the letter leaving this machine.

Sibling to eleven_tts.py, and the reason it exists is not audio quality: it is that the
compaction letter is the most private document this repo produces. A week of therapy notes,
finances, sleep and who you saw is read aloud, and the ElevenLabs path posts every character
of it to a third party who will hold it under whatever retention policy they hold it under.
This tool does the same job with Resemble AI's Chatterbox, locally: the text goes to a model
on this disk, the audio comes back from it, and nothing is sent anywhere. The only thing that
crosses the network is the model weights, once, on first run.

ElevenLabs remains the default. It is what every existing session was rendered with, it is
better at long-form prosody, and switching what an unqualified `just render` does — or where
it sends the letter — is a thing you should have to say. Say it with `--tts-engine chatterbox`,
an `engine: chatterbox` line in a session's front-matter, or once and for all with
PETROGRAPH_TTS_ENGINE=chatterbox in .env. See _speech.py's resolve_engine for the precedence.

Everything either engine does either side of synthesis is shared code in _speech.py — the same
front-matter stripping, the same block parsing, the same chunking, the same leveling, the same
naming and the same hand-off to mix_music.py. A locally-narrated letter is the same letter.

THE VOICE. Chatterbox is a zero-shot cloner: it has one built-in speaker, and any other voice
comes from a reference clip of 10-20 seconds that you point it at. There is no catalogue and
no voice ids, so the ElevenLabs default (Victoria — "warm and calm, deep, ideal for narration")
cannot be selected here; it has to be supplied as audio. Three ways to get a reference, in
descending order of how comfortable they are:

  1. Record ~20 seconds of yourself, or of anyone who has agreed to it, reading in the register
     you want. Fully local, unambiguous, and the result is a voice nobody else is selling.
  2. Take a permissively-licensed narration clip — LibriVox is public domain — from a reader
     whose delivery is close to what you are after.
  3. Point --capture-reference at a narration you have already rendered with Victoria, which
     lifts a clean 20-second window out of it. Note that this is a voice ElevenLabs licenses
     from a real person and their terms restrict using their output to clone voices elsewhere;
     that is a call to make knowingly rather than by accident, which is why it is not automatic.

Without any reference the built-in voice is used and the run says so plainly. It is a
perfectly good narrator. It is not Victoria, and this tool will not pretend otherwise.

Calm is the other half, and that part does transfer: --exaggeration below the 0.5 default
flattens the delivery, and a low --cfg-weight slows the pacing down (the model's own guidance
— higher exaggeration speeds speech up, lower CFG is what compensates). The defaults here sit
at 0.35/0.35 with --temperature 0.6, which is the settings-shaped part of "warm and calm":
deliberate, even, and consistent across the sixty-odd chunks of a long letter.

PAUSES ARE REAL SILENCE. ElevenLabs reads a spaced hyphen as a beat, so its pauses ride along
inside the text. Chatterbox has no such convention — it would simply read the dashes — so the
document's shape is spliced in as actual silence between chunks instead: --header-gap after a
section heading (the longest), --bullet-gap and --newline-gap after a list item or a line
break, --sentence-gap between sentences of one paragraph. Milliseconds, not dashes, and the
result is exact rather than a thing the model might or might not honour.

CACHING, for a different reason than upstream. Nothing here is billed, but a long letter is
tens of minutes of compute, and a run that dies on chunk 40 of 60 should not redo the 39 that
worked. Chunks land in share/.tts-cache/chatterbox as they come back, keyed by the text, the
reference clip, the model and every generation parameter — --seed included, because without a
fixed seed the same sentence is a different recording each time and the cache would be a lie.

WATERMARKING. Chatterbox embeds Resemble's inaudible PerTh watermark in everything it
generates. It survives mp3 encoding and the mix chain. It carries no identity — it marks the
audio as synthetic, nothing more — but it is there, locally generated or not, and you should
know it is there.

Usage:
    ./chatterbox_tts.py input.md --normalize
    ./chatterbox_tts.py input.md --voice-sample share/voices/victoria.wav --normalize
    ./chatterbox_tts.py input.md --dry-run                    # nothing loaded, nothing written
    ./chatterbox_tts.py --capture-reference old-narration.mp3 # → share/voices/victoria.wav
    ./chatterbox_tts.py input.md --model turbo --voice-sample ref.wav
"""

from __future__ import annotations
import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

# Sibling modules with no dependencies of their own. _speech.py is everything that happens
# either side of synthesis, shared with eleven_tts.py so that "the same letter, locally" is
# true of the code and not just of the sentence.
from _names import master_name, voice_name
from _overwrite import guard
from _term import link, Steps
from _env import REPO_ROOT
from _speech import (
    bed_length, block_summary, cache_path, chunk_text, clamp, clock, encode,
    estimate_length, ffmpeg, music_tracks, normalize, probe_duration, strip_markdown,
    write_atomic,
)

# Chatterbox wraps its token-by-token decode loop in a tqdm bar, so a sixty-chunk letter
# paints tens of thousands of progress lines — down an ssh pipe, when the render is remote,
# and interleaved into illegibility once --concurrency puts several workers on one terminal.
# The per-chunk lines this tool prints itself are the progress worth having.
#
# It has to be set here rather than beside the model load: tqdm's env support reads os.environ
# when tqdm.std is *imported*, not when a bar is made, and torch may pull it in on the way past.
# Setting it to "0" would not work either — tqdm coerces with bool(), for which "0" is true.
# The empty string is the way back to a visible bar.
os.environ.setdefault("TQDM_DISABLE", "1")

# Resemble AI's three checkpoints. `chatterbox` is the 500M original and the default here: it
# is the only one that honours exaggeration and CFG, which are exactly the knobs that make a
# narration calm. turbo (350M) and nano are faster and ignore them, and both insist on a
# reference clip — from_pretrained ships no built-in speaker for either.
MODELS = ("chatterbox", "turbo", "nano")
DEFAULT_MODEL = "chatterbox"
SAMPLE_RATE = 24_000  # S3GEN_SR — what every Chatterbox checkpoint synthesises at
DELIVERY_RATE = 44_100  # what the narration is written at, matching the remote engine and the mixer

# Where a reference clip lives if you do not name one. Under share/, which is gitignored
# whole — a voice print is a recording of a person, and it is not this repo's to publish.
VOICES_DIR = REPO_ROOT / "share" / "voices"
DEFAULT_VOICE_SAMPLE = VOICES_DIR / "victoria.wav"

# Local chunks, kept apart from the remote engine's so `du` can tell you which cache is which.
DEFAULT_CACHE_DIR = REPO_ROOT / "share" / ".tts-cache" / "chatterbox"

# Warm, calm, deliberate — the settings-shaped half of the target voice. Below the model's own
# 0.5/0.5 on both: lower exaggeration flattens the delivery, and lower CFG slows the pacing
# (Resemble's guidance is that exaggeration speeds speech up and low CFG is the compensation).
# A low temperature is a long-letter decision rather than a tonal one: sixty chunks that have
# to sound like one sitting want the least sampling variance they can get.
GENERATION = {
    "exaggeration": 0.35,
    "cfg_weight": 0.35,
    "temperature": 0.6,
    "repetition_penalty": 1.2,  # the model default; drops stuck-loop artefacts on long chunks
}
DEFAULT_SEED = 0  # fixed, so a chunk is reproducible and the cache means something

# Chatterbox degrades on long inputs and there is no upstream reason to feed it any — unlike
# the remote engine, where chunking is an opt-in fix for volume drift, here it is how the
# model is meant to be driven. Hence a default rather than a flag you have to remember.
DEFAULT_CHUNK = 300

# Milliseconds of real silence after each kind of block. Scaled to sound like the remote
# engine's 1/2/2/3 spaced hyphens, which measured at roughly a quarter-second per dash.
DEFAULT_GAPS = {"sentence": 250, "text": 500, "bullet": 500, "header": 900}

# An unmeasured starting point, and the only number here that is. It self-corrects: the first
# render fills the cache, and every --dry-run after that reads the real durations off the
# audio instead. Speech only — the gaps are known exactly and added on top.
CHARS_PER_SECOND = 14.0

# What upstream hardcodes as the decode ceiling (chatterbox/tts.py, with a TODO beside it
# saying it should come from the config). A 300-char chunk is around 500 speech tokens, so it
# does not normally bind — which is exactly why a chunk that reaches it is worth counting.
# Hitting the ceiling means the model never emitted an end-of-speech token, and the audio is
# a runaway rather than a slow chunk. Kept here to name the number the profile reports against.
MAX_NEW_TOKENS = 1000

# The three stages inside one generate() call, in pipeline order, as instrument() tallies them.
STAGES = {"t3": "t3 decode", "s3gen": "s3gen flow", "watermark": "watermark"}

# One worker, i.e. today's behaviour, because the right number is a property of the machine
# and this file does not know which machine it is on. The remote engine, which does know it is
# talking to a particular GPU box, supplies its own — see remote_chatterbox.py's remote_render.
DEFAULT_CONCURRENCY = 1

# How much of a failed worker's output to repeat at the end. Its lines have already scrolled
# past interleaved with three other workers', so the point is to put the traceback back
# together in one place, not to show it for the first time.
TAIL_LINES = 12

REFERENCE_SECONDS = 20  # long enough for turbo's >5s floor with room to spare
MIN_REFERENCE_SECONDS = 5.0


# ── the voice ─────────────────────────────────────────────────────────────────────────────

def capture_reference(src: Path, dst: Path, seconds: int, start: float) -> None:
    """Lift a clean mono window out of an existing recording, for use as a reference clip.

    Deliberately minimal processing. Chatterbox wants a plain speech sample, not a mastered
    one, and anything this stage does to the clip — compression, EQ, loudness matching — is a
    colour the clone then inherits. All that happens is: seek, take the window, collapse to
    mono at the model's own rate, and trim the leading silence so the clip opens on speech
    rather than on room tone.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-ss", f"{start:g}", "-t", str(seconds), "-i", str(src),
           "-ac", "1", "-ar", str(SAMPLE_RATE),
           "-af", "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-50dB",
           str(dst))


def resolve_reference(explicit: Path | None, model: str) -> Path | None:
    """The reference clip to clone, or None for the built-in voice.

    An explicit --voice-sample that does not exist is an error, because you asked for a
    specific voice and did not get it. The default path missing is not — it just means no
    reference has been captured on this machine yet, and the built-in speaker is a working
    answer. turbo and nano have no built-in speaker, so for them it is an error either way.
    """
    if explicit is not None:
        path = explicit.expanduser()
        if not path.exists():
            sys.exit(f"No such voice sample: {path}")
        return path
    if DEFAULT_VOICE_SAMPLE.exists():
        return DEFAULT_VOICE_SAMPLE
    if model != "chatterbox":
        sys.exit(f"--model {model} has no built-in voice, so it needs a reference clip.\n"
                 f"  Record ~{REFERENCE_SECONDS}s of clean speech and pass --voice-sample, or "
                 f"lift a window out of audio you already have:\n"
                 f"    ./tools/chatterbox_tts.py --capture-reference <audio>")
    return None


# ── wav, without a dependency to read it ──────────────────────────────────────────────────
#
# The model hands back float samples and everything downstream is ffmpeg, so the only format
# that has to exist in this process is 16-bit mono PCM at the model's rate. The stdlib writes
# and reads exactly that, which keeps the chunk cache made of files you can double-click.

def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def read_wav(path: Path) -> np.ndarray:
    # 16-bit mono at SAMPLE_RATE is the only shape this cache ever holds, and the rate is in
    # the key — so anything else on disk is a file from somewhere else, not a stale chunk.
    with wave.open(str(path), "rb") as w:
        shape = (w.getnchannels(), w.getsampwidth(), w.getframerate())
        if shape != (1, 2, SAMPLE_RATE):
            raise RuntimeError(f"{path}: expected 16-bit mono at {SAMPLE_RATE} Hz, got "
                               f"{shape[0]}ch/{8 * shape[1]}-bit at {shape[2]} Hz")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


def to_pcm16(wav) -> np.ndarray:
    """The model's float tensor, as the int16 the cache and the assembler both speak."""
    data = wav.squeeze(0).detach().cpu().numpy().astype("float32")
    return (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")


# ── the document, as chunks with beats after them ─────────────────────────────────────────

def plan(blocks: list[tuple[str, str]], size: int, gaps: dict[str, int]) -> list[tuple[str, float]]:
    """(text, seconds of silence after it), in order.

    A chunk never spans a block boundary, which is what makes the beats placeable at all: the
    last piece of a block gets the pause that block's kind calls for, and the pieces before it
    get the plain sentence beat. The document ends on speech, not on silence.
    """
    out: list[tuple[str, float]] = []
    for kind, text in blocks:
        pieces = chunk_text(text, size)
        for i, piece in enumerate(pieces):
            last = i == len(pieces) - 1
            out.append((piece, gaps[kind if last else "sentence"] / 1000))
    if out:
        out[-1] = (out[-1][0], 0.0)
    return out


# ── synthesis ─────────────────────────────────────────────────────────────────────────────

def pick_device(requested: str) -> str:
    """cuda, then Apple's MPS, then the CPU — or whatever was asked for, unexamined."""
    import torch
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_model(name: str, device: str, reference: Path | None, exaggeration: float):
    """The checkpoint, on the device, with the reference clip already folded in.

    prepare_conditionals is called once here rather than per chunk. Passing audio_prompt_path
    to generate() re-derives the speaker embedding, the prompt tokens and the decoder
    conditioning every single call — on a sixty-chunk letter that is fifty-nine repetitions of
    work whose answer cannot change.
    """
    if name == "chatterbox":
        from chatterbox.tts import ChatterboxTTS
        model = ChatterboxTTS.from_pretrained(device=device)
    else:
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        model = ChatterboxTurboTTS.from_pretrained(device=device, nano=(name == "nano"))
    if reference is not None:
        model.prepare_conditionals(str(reference), exaggeration=exaggeration)
    return model


def generate(model, name: str, text: str, params: dict, seed: int):
    """One chunk of speech. Seeded first, so the same inputs give the same audio.

    turbo and nano route through a distilled decoder that has no CFG and no emotion vector;
    passing either would only earn a warning per chunk, so they are dropped rather than sent
    and ignored. The cache key still carries them, which is right — they are part of what was
    asked for, and a run that starts honouring them is a different recording.
    """
    import torch
    torch.manual_seed(seed)
    if name == "chatterbox":
        return model.generate(
            text,
            exaggeration=params["exaggeration"],
            cfg_weight=params["cfg_weight"],
            temperature=params["temperature"],
            repetition_penalty=params["repetition_penalty"],
        )
    return model.generate(
        text,
        temperature=params["temperature"],
        repetition_penalty=params["repetition_penalty"],
    )


def instrument(model, tally: dict) -> None:
    """Time the three stages inside one generate() call, without forking upstream's pipeline.

    ChatterboxTTS.generate is a straight line — t3.inference turns text into speech tokens,
    s3gen.inference turns those tokens into a waveform, the watermarker stamps it — and each
    of the three is reached through an *instance* attribute. So they can be shadowed on the
    loaded object, which leaves the library unpatched and keeps no vendored copy of a pipeline
    upstream is free to change.

    Wall-clock per chunk is already printed, but it cannot tell a long chunk from a slow one.
    Tokens per second can, and it is the number that decides whether the decode is the stage
    worth attacking or whether the time is really going to the vocoder. Chunks that reach
    MAX_NEW_TOKENS are counted apart, because that is a quality problem wearing a speed
    problem's clothes: the model never said it was finished.
    """
    def timed(owner, attr: str, key: str, after=None) -> None:
        fn = getattr(owner, attr, None)
        if fn is None:
            return  # turbo and nano do not have every stage the full checkpoint has
        def wrapper(*a, **kw):
            t = time.perf_counter()
            try:
                out = fn(*a, **kw)
            finally:
                # In the finally, so a chunk that dies still reports the time it burned.
                tally[key] = tally.get(key, 0.0) + time.perf_counter() - t
            if after is not None:
                try:
                    after(out)
                except Exception:
                    pass  # a profiler that can fail a render is worse than no profiler
            return out
        setattr(owner, attr, wrapper)

    def count_tokens(out) -> None:
        n = int(out.shape[-1])
        tally["tokens"] = tally.get("tokens", 0) + n
        tally["ceiling"] = tally.get("ceiling", 0) + (n >= MAX_NEW_TOKENS)

    timed(model.t3, "inference", "t3", count_tokens)
    timed(model.t3, "inference_turbo", "t3", count_tokens)
    timed(model.s3gen, "inference", "s3gen")
    timed(model.watermarker, "apply_watermark", "watermark")


def stage_report(tally: dict) -> str | None:
    """The per-stage split of a run's synthesis, or None if nothing was generated.

    Percentages are of the three stages summed rather than of the run, so they answer "where
    did synthesis go" without the model load and the encode diluting them. Steps.report below
    already puts synthesis in the context of the whole run.
    """
    total = sum(tally.get(key, 0.0) for key in STAGES)
    if not total:
        return None
    parts = []
    for key, label in STAGES.items():
        dt = tally.get(key, 0.0)
        if not dt:
            continue
        note = f", {tally['tokens']:,} tokens at {tally['tokens'] / dt:,.1f}/s" \
            if key == "t3" and tally.get("tokens") else ""
        parts.append(f"{label} {dt:,.1f}s ({100 * dt / total:.0f}%{note})")
    line = "  " + " · ".join(parts)
    if tally.get("ceiling"):
        line += (f"\n  {tally['ceiling']} chunk(s) ran to the {MAX_NEW_TOKENS}-token ceiling "
                 "without emitting end-of-speech — listen to those before trusting them.")
    return line


def chunk_path(cache_dir: Path, text: str, model: str, reference: Path | None,
               params: dict, seed: int) -> Path:
    """Where one chunk of audio lives, named for everything that decides how it sounds.

    The reference clip goes in by content, not by path: two files with the same bytes are the
    same voice, and renaming one should not throw away a cache. Its size and mtime would be
    cheaper and would also be wrong the first time you re-export a clip to the same name.
    """
    voice = hashlib.sha256(reference.read_bytes()).hexdigest() if reference else "builtin"
    return cache_path(cache_dir, {
        "text": text, "model": model, "voice": voice, "params": params,
        "seed": seed, "rate": SAMPLE_RATE,
    }, ".wav")


# ── the fan-out ───────────────────────────────────────────────────────────────────────────
#
# A chunk is already a pure function of its cache key: generate() re-seeds before every one,
# the conditionals are fixed at load, and the KV cache is fresh per call. Nothing carries from
# one chunk to the next, so which order they are made in — and on which core — cannot change
# what comes back. Sixty independent jobs and a GPU running one of them at a time.
#
# They are fanned out to worker *processes* rather than threads, and the reason is the seed.
# torch.manual_seed sets a process-global generator, and the sampler upstream reaches for that
# generator with no way to hand it a private one. Two threads interleaved inside one process
# would therefore draw from each other's stream, and a chunk's audio would depend on how the
# scheduler happened to run it — which would make every entry in a cache keyed by --seed a
# quiet lie. A worker process has its own generator and takes its chunks one at a time, so the
# audio it produces is bit-identical to what the sequential path would have produced. That is
# the property the whole design is for, and it is checkable in a minute: render a few chunks
# into an empty --cache-dir, shasum them, empty it, render the same chunks again with
# --concurrency 3, and the hashes are the same hashes under the same names.
#
# The workers need no channel back: they write into the same content-addressed cache the
# sequential path writes into, and the parent reads its results out of it. The cache was
# already the mechanism for resuming a killed run; this is the same mechanism, used sideways.
#
# What it does not do is make every machine faster. The win is there only where one decode
# leaves the device idle, which is the case on the box — a batch-of-two 500M model on a 48GB
# L40S — and is not the case on a laptop. Measured on an M-series Mac over mps, --concurrency 3
# came out *slower* than one worker: three copies of the model to load, and a GPU that one
# process had already saturated, so per-worker throughput fell from 7.2 tokens/s to under 5.
# Hence a default of 1 here and a default of 3 only in remote_chatterbox.py, which knows it is
# talking to a machine where the premise holds.

def plan_digest(chunks: list[str]) -> str:
    """A short hash of the chunk list, so a worker can refuse a plan that is not the parent's.

    Every flag that feeds plan() has to reach the worker or it will number its chunks
    differently and write real audio under keys the parent is not looking at. Nothing detects
    that on its own — the parent simply finds nothing where it expected audio, several minutes
    later. Agreeing on this digest up front turns a wasted fan-out into a two-second exit.
    """
    return hashlib.sha256("\x00".join(chunks).encode()).hexdigest()[:16]


def index_list(raw: str) -> list[int]:
    """--only 12,13,14 → [12, 13, 14]. Chunk numbers as printed, which is to say 1-based."""
    try:
        return [int(part) for part in raw.split(",") if part.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected comma-separated chunk numbers, got {raw!r}") from None


def worker_argv(args, numbers: list[int], digest: str) -> list[str]:
    """The command line for one worker: everything deciding the plan or the audio, nothing else.

    Any flag feeding plan() or chunk_path() belongs here — the gap settings included, which
    change no chunk's audio but do change how many chunks there are and therefore what every
    index means. Anything added later that touches either must join this list.

    Deliberately absent: --out, --music, --normalize, --no-compress, --bitrate, --mix-engine,
    --concurrency. A worker must never reach the overwrite prompt, the encoder or the mixer. It
    makes chunks; the parent makes the letter.

    sys.executable rather than the shebang, because this file is a uv script and the parent is
    already running inside the environment uv resolved for it. Going through the shebang again
    would re-resolve it once per worker for no gain.
    """
    argv = [sys.executable, __file__, str(args.markdown),
            "--model", args.model,
            "--device", args.device,
            "--chunk", str(args.chunk),
            "--sentence-gap", str(args.sentence_gap),
            "--newline-gap", str(args.newline_gap),
            "--bullet-gap", str(args.bullet_gap),
            "--header-gap", str(args.header_gap),
            "--exaggeration", str(args.exaggeration),
            "--cfg-weight", str(args.cfg_weight),
            "--temperature", str(args.temperature),
            "--repetition-penalty", str(args.repetition_penalty),
            "--seed", str(args.seed),
            "--cache-dir", str(args.cache_dir),
            "--expect-plan", digest,
            "--only", ",".join(str(n) for n in numbers)]
    if args.limit is not None:
        argv += ["--limit", str(args.limit)]
    if args.voice_sample is not None:
        argv += ["--voice-sample", str(args.voice_sample)]
    return argv


def prefetch_weights(name: str) -> None:
    """Pull the checkpoint into the HF cache before spawning, so N cold workers do not race.

    Best-effort by design. huggingface_hub locks its own downloads, so a race would be slow
    rather than wrong, and the file list here is upstream's and may change under us. Neither is
    worth failing a render over — and on the box `--prewarm` has already done this.
    """
    if name != "chatterbox":
        return  # turbo and nano fetch a different repo; they can race harmlessly
    try:
        from huggingface_hub import hf_hub_download
        from chatterbox.tts import REPO_ID
        for fname in ("ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors",
                      "tokenizer.json", "conds.pt"):
            hf_hub_download(repo_id=REPO_ID, filename=fname)
    except Exception as e:
        print(f"  (could not pre-fetch the weights: {type(e).__name__}: {e} — "
              "the workers will fetch them themselves)", file=sys.stderr)


def fan_out(args, chunks: list[str], cached: list[Path | None], hits: set[int],
            workers: int, digest: str) -> list[tuple[int, int]]:
    """Synthesise every missing chunk across `workers` processes. Returns [(worker, exit code)].

    The work list is de-duplicated by cache path first. Two identical chunk texts — a repeated
    bullet, a line that appears twice — are one entry in a content-addressed cache, and handing
    both to different workers would have two processes writing one key. Deduplicating removes
    that and the duplicate compute in the same move.

    Shards are round-robin over the *missing* indices rather than over all of them. Striding
    across the whole list would hand one worker every cache hit and leave it idle on a resume.
    """
    todo, seen = [], set()
    for i in range(len(chunks)):
        if i in hits or cached[i] in seen:
            continue
        seen.add(cached[i])
        todo.append(i)
    if not todo:
        return []

    workers = max(1, min(workers, len(todo)))
    shards = [shard for shard in ([todo[n::workers] for n in range(workers)]) if shard]
    prefetch_weights(args.model)
    print(f"Fanning {len(todo)} chunk(s) out across {len(shards)} worker(s) on one GPU — "
          f"each loads its own copy of {args.model}, and they meet in the chunk cache.",
          flush=True)

    # One intra-op thread each: the box is 4 vCPU, the decode loop is Python-heavy, and the
    # watermarker is numpy on the CPU. N workers each helping themselves to every core is
    # contention, not parallelism.
    env = {**os.environ, "TQDM_DISABLE": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    procs = [(n, subprocess.Popen(worker_argv(args, [i + 1 for i in shard], digest), env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, bufsize=1))
             for n, shard in enumerate(shards, 1)]

    # Reader threads are safe here in a way worker threads were not: this process holds no
    # model and touches no RNG. All they do is label a line and print it under a lock.
    lock = threading.Lock()
    tails: dict[int, list[str]] = {n: [] for n, _ in procs}

    def pump(n: int, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            line = line.rstrip()
            tails[n].append(line)
            del tails[n][:-TAIL_LINES]
            with lock:
                print(f"  [w{n}] {line}", flush=True)

    threads = [threading.Thread(target=pump, args=(n, proc), daemon=True) for n, proc in procs]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
        # Every worker is waited on even after one has failed. Its siblings are still filling
        # the cache, and none of what they finish is worth throwing away to exit sooner.
        codes = [(n, proc.wait()) for n, proc in procs]
    except KeyboardInterrupt:
        for _, proc in procs:
            proc.terminate()
        codes = [(n, proc.wait()) for n, proc in procs]

    failed = [(n, code) for n, code in codes if code != 0]
    for n, code in failed:
        print(f"\nworker {n} exited {code}. Its last lines:", file=sys.stderr)
        for line in tails[n]:
            print(f"  [w{n}] {line}", file=sys.stderr)
    return failed


def run_worker(args, chunks: list[str], params: dict, reference: Path | None) -> None:
    """The --only half of --concurrency: synthesise the named chunks into the cache and stop.

    Reached before the parent's output path, the overwrite guard, the encoder and the mixer, so
    that a worker structurally cannot touch any of them. It has one job and no opinion about
    what the chunks are for.
    """
    # Nothing here caps the thread pool: a worker spawned by fan_out inherits OMP_NUM_THREADS=1
    # from it, which is what torch sizes its intra-op pool from, and a worker you ran yourself
    # to re-roll one chunk should have the whole machine.
    cache_dir = args.cache_dir.expanduser()
    device = pick_device(args.device)
    if device == "mps":
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    numbers = sorted(set(args.only))
    bad = [n for n in numbers if not 1 <= n <= len(chunks)]
    if bad:
        sys.exit(f"--only: no such chunk(s) {bad} — this letter has {len(chunks)}.")

    print(f"loading {args.model} on {device} for {len(numbers)} chunk(s)", flush=True)
    model = load_model(args.model, device, reference, args.exaggeration)
    tally: dict = {}
    instrument(model, tally)

    for n in numbers:
        text = chunks[n - 1]
        path = chunk_path(cache_dir, text, args.model, reference, params, args.seed)
        if path.exists() and path.stat().st_size:
            continue  # a sibling got here first, or a resumed fan-out
        t = time.perf_counter()
        samples = to_pcm16(generate(model, args.model, text, params, args.seed))
        write_atomic(path, wav_bytes(samples, SAMPLE_RATE))
        print(f"chunk {n}/{len(chunks)}: {len(text)} chars, "
              f"{len(samples) / SAMPLE_RATE:.1f}s in {time.perf_counter() - t:.1f}s", flush=True)
    stages = stage_report(tally)
    if stages:
        print(stages.strip(), flush=True)


# ── main ──────────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Narrate a microlite markdown file locally, via Chatterbox.")
    ap.add_argument("markdown", nargs="?", type=Path, help="Path to the markdown file.")
    ap.add_argument("--voice-sample", type=Path, metavar="WAV",
                    help="Reference clip (10-20s of clean speech) to clone the voice from. "
                         f"Default: {DEFAULT_VOICE_SAMPLE.relative_to(REPO_ROOT)} if it exists, "
                         "otherwise the model's built-in speaker.")
    ap.add_argument("--model", choices=MODELS, default=DEFAULT_MODEL,
                    help=f"Checkpoint (default: {DEFAULT_MODEL}). turbo/nano are faster but "
                         "ignore --exaggeration and --cfg-weight and require a reference clip.")
    ap.add_argument("--device", default="auto",
                    help="auto (cuda → mps → cpu), or name one: cuda, mps, cpu.")
    ap.add_argument("--limit", type=int, help="Cap characters synthesised, to shorten a test run.")
    ap.add_argument("--chunk", type=int, default=DEFAULT_CHUNK, metavar="CHARS",
                    help=f"Synthesise in ≤CHARS-char pieces (default: {DEFAULT_CHUNK}).")
    ap.add_argument("--sentence-gap", type=int, default=DEFAULT_GAPS["sentence"], metavar="MS",
                    help=f"Silence between sentences of one paragraph "
                         f"(default: {DEFAULT_GAPS['sentence']}ms; 0 = off).")
    ap.add_argument("--newline-gap", type=int, default=DEFAULT_GAPS["text"], metavar="MS",
                    help=f"Silence after a line break / between paragraphs "
                         f"(default: {DEFAULT_GAPS['text']}ms).")
    ap.add_argument("--bullet-gap", type=int, default=DEFAULT_GAPS["bullet"], metavar="MS",
                    help=f"Silence after a bullet-point item (default: {DEFAULT_GAPS['bullet']}ms).")
    ap.add_argument("--header-gap", type=int, default=DEFAULT_GAPS["header"], metavar="MS",
                    help=f"Silence after a section heading — the longest beat "
                         f"(default: {DEFAULT_GAPS['header']}ms).")
    ap.add_argument("--exaggeration", type=float, default=GENERATION["exaggeration"],
                    help=f"Emotional intensity, 0-1 (default: {GENERATION['exaggeration']}; "
                         "the model's own default is 0.5, lower is flatter and calmer).")
    ap.add_argument("--cfg-weight", type=float, default=GENERATION["cfg_weight"],
                    help=f"Classifier-free guidance, 0-1 (default: {GENERATION['cfg_weight']}; "
                         "lower slows the pacing down, which is what makes it read as calm).")
    ap.add_argument("--temperature", type=float, default=GENERATION["temperature"],
                    help=f"Sampling temperature (default: {GENERATION['temperature']}; low keeps "
                         "sixty chunks sounding like one sitting).")
    ap.add_argument("--repetition-penalty", type=float, default=GENERATION["repetition_penalty"],
                    help=f"(default: {GENERATION['repetition_penalty']}).")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED,
                    help=f"Sampling seed (default: {DEFAULT_SEED}). Part of the cache key: "
                         "change it to re-roll a chunk you did not like.")
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR,
                    help="Where synthesised chunks are kept, so a run that dies partway is not "
                         f"recomputed (default: {DEFAULT_CACHE_DIR.relative_to(REPO_ROOT)}).")
    ap.add_argument("--no-cache", action="store_true",
                    help="Synthesise every chunk afresh and store nothing.")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, metavar="N",
                    help=f"Synthesise N chunks at once, in N worker processes sharing the "
                         f"device (default: {DEFAULT_CONCURRENCY}). The audio is identical "
                         "either way — chunks are independent and a worker keeps its own "
                         "seeded sampler. Needs the cache, which is how the workers report "
                         "back, and a copy of the model each, which is what limits N.")
    ap.add_argument("--only", type=index_list, metavar="N,N,…",
                    help="Synthesise exactly these chunks into the cache and exit, numbered as "
                         "the progress lines number them. This is the worker half of "
                         "--concurrency, and by hand it is how you re-roll one chunk: delete "
                         "it from the cache, bump --seed, and name it here.")
    ap.add_argument("--expect-plan", metavar="SHA",
                    help=argparse.SUPPRESS)  # parent-to-worker agreement check; see plan_digest
    ap.add_argument("--dry-run", action="store_true",
                    help="Say what would be synthesised — chunks, characters, cache hits, how "
                         "long the narration runs — and load no model.")
    ap.add_argument("--normalize", action="store_true",
                    help="Compress + RMS-normalise (~-20 dB) with true peak < -3 dBTP.")
    ap.add_argument("--no-compress", action="store_true",
                    help="With --normalize, level only — skip the compressor. Use when the "
                         "output feeds mix_music.py, whose compressor does the dynamics.")
    ap.add_argument("--bitrate", default="192k",
                    help="Bitrate when --out is an mp3 (default: 192k; a .wav is 24-bit PCM).")
    ap.add_argument("--out", type=Path,
                    help="Output .wav (24-bit) or .mp3 (default: beside the input markdown; "
                         "inside sessions/ the session folder names it, e.g. "
                         "lifelog-2026-05-15-voice.wav, leaving the plain name for the master).")
    ap.add_argument("--music", action="append", metavar="PATH[,PATH…]",
                    help="Also render the master over this backing track (via mix_music.py). "
                         "Repeat, or comma-separate, for several.")
    ap.add_argument("--mix-engine", choices=("oss", "vst"), default="oss",
                    help="Which mix_music.py voice chain the backing-track mix uses "
                         "(default: oss). Only applies with --music.")
    ap.add_argument("--capture-reference", type=Path, metavar="AUDIO",
                    help="Lift a reference clip out of an existing recording and exit. Writes "
                         "--voice-sample, or the default path. See this file's header on where "
                         "a reference clip should and should not come from.")
    ap.add_argument("--reference-seconds", type=int, default=REFERENCE_SECONDS,
                    help=f"Length of the captured clip (default: {REFERENCE_SECONDS}).")
    ap.add_argument("--reference-start", type=float, default=0.0, metavar="SECONDS",
                    help="Where to start the captured clip (default: 0).")
    return ap


def main() -> None:
    args = build_parser().parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg is required (brew install ffmpeg).")

    # ── capture a reference clip and stop ─────────────────────────────────────────────────
    if args.capture_reference is not None:
        src = args.capture_reference.expanduser()
        if not src.exists():
            sys.exit(f"No such file: {src}")
        dst = (args.voice_sample or DEFAULT_VOICE_SAMPLE).expanduser()
        capture_reference(src, dst, args.reference_seconds, args.reference_start)
        secs = probe_duration(dst) or 0.0
        print(f"Reference clip: {clock(secs)} from {src.name} → {link(dst)}")
        if secs < MIN_REFERENCE_SECONDS:
            print(f"  Warning: {secs:.1f}s is short — Chatterbox wants 10-20s, and turbo/nano "
                  f"refuse anything under {MIN_REFERENCE_SECONDS:g}s. Try --reference-start "
                  "past the quiet opening.", file=sys.stderr)
        return

    if args.markdown is None:
        sys.exit("a markdown file is required (or use --capture-reference).")
    if not args.markdown.exists():
        sys.exit(f"No such file: {args.markdown}")
    music = music_tracks(args.music)
    for path in music:
        if not path.exists():
            sys.exit(f"No such file: {path}")

    reference = resolve_reference(args.voice_sample, args.model)
    params = {
        "exaggeration": args.exaggeration,
        "cfg_weight": args.cfg_weight,
        "temperature": args.temperature,
        "repetition_penalty": args.repetition_penalty,
    }
    gaps = {"sentence": args.sentence_gap, "text": args.newline_gap,
            "bullet": args.bullet_gap, "header": args.header_gap}

    steps = Steps()
    blocks = clamp(strip_markdown(args.markdown.read_text()), args.limit)
    pieces = plan(blocks, args.chunk, gaps)
    chunks = [text for text, _ in pieces]
    silences = [gap for _, gap in pieces]
    chars = sum(len(c) for c in chunks)
    if not chunks:
        sys.exit(f"{args.markdown}: nothing to narrate — no prose survived the front-matter "
                 "and markdown stripping.")

    # A worker turns back here, before the output path, the overwrite guard and the mixer are
    # so much as computed. Everything above this line is the plan; everything below it is the
    # letter, and the letter is the parent's business.
    digest = plan_digest(chunks)
    if args.expect_plan and args.expect_plan != digest:
        sys.exit(f"--expect-plan {args.expect_plan} but this plan is {digest}: the parent and "
                 "this worker disagree about how the letter divides into chunks. A flag that "
                 "feeds plan() is missing from worker_argv.")
    if args.only is not None:
        if not args.only:
            sys.exit("--only: name at least one chunk.")
        if args.no_cache:
            sys.exit("--only writes into the cache and nothing else, so --no-cache would "
                     "leave it with nowhere to put the audio.")
        run_worker(args, chunks, params, reference)
        return

    workers = max(1, args.concurrency)
    if workers > 1 and args.no_cache:
        sys.exit("--concurrency needs the cache: it is how the workers hand their chunks back. "
                 "Drop --no-cache, or drop --concurrency.")

    out = args.out or voice_name(args.markdown)
    # Nothing is billed over here, but a finished master is no less gone for having been
    # free to make — and the model load that follows is minutes. Ask first, once, for the
    # narration and the master both; the mixer below inherits the answer.
    if not args.dry_run:
        guard(out, master_name(out) if music else None,
              what=f"narrating {args.markdown.name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    cache_dir = None if args.no_cache else args.cache_dir.expanduser()

    print(f"Gaps (ms): sentence {args.sentence_gap}, newline {args.newline_gap}, "
          f"bullet {args.bullet_gap}, header {args.header_gap} — "
          f"{len(blocks)} block(s): {block_summary(blocks)}")
    voice = f"cloned from {reference.name}" if reference else "the model's built-in speaker"
    print(f"model={args.model} voice={voice} seed={args.seed}")
    print(f"generation={params}")
    if reference is None:
        print("  No reference clip, so this is Chatterbox's own voice — not Victoria. "
              "See --capture-reference.")
    steps.mark("parse markdown")

    # Count the hits before doing anything, so the run opens by saying what it will actually
    # compute. Nothing here is billed, but on a CPU a chunk is real minutes.
    if cache_dir is not None:
        cached: list[Path | None] = [
            chunk_path(cache_dir, c, args.model, reference, params, args.seed) for c in chunks]
        hits = {i for i, p in enumerate(cached) if p.exists() and p.stat().st_size}
    else:
        cached = [None] * len(chunks)
        hits = set()
    print(f"Synthesising {chars:,} chars in {len(chunks)} chunk(s) ≤{args.chunk}, "
          f"locally — nothing is sent anywhere.")
    if hits:
        todo = sum(len(chunks[i]) for i in range(len(chunks)) if i not in hits)
        print(f"  {len(hits)}/{len(chunks)} chunk(s) already synthesised — "
              f"{todo:,} of {chars:,} chars left to do.")

    if args.dry_run:
        speech, measured, rate = estimate_length(chunks, cached, CHARS_PER_SECOND)
        secs = speech + sum(silences)
        how = ("every chunk measured off the cache — exact" if measured == len(chunks) else
               f"{measured}/{len(chunks)} measured off the cache, the rest at {rate:.1f} chars/s"
               if measured else f"nothing cached — {CHARS_PER_SECOND} chars/s assumed")
        print(f"\nDry run — no model loaded, nothing written.\n"
              f"  {len(chunks) - len(hits)} chunk(s) would be synthesised, "
              f"{sum(len(chunks[i]) for i in range(len(chunks)) if i not in hits):,} characters.\n"
              f"  ≈{clock(secs)} of narration ({how}), of which "
              f"{clock(sum(silences))} is pauses.")
        print("  Cost is wall-clock on this machine, not credits — and the first run also "
              "downloads the model weights.")
        if music:
            bed = bed_length(music)
            if bed:
                print(f"  backing bed {clock(bed)} from {len(music)} track(s) — "
                      + ", ".join(p.name for p in music))
                print(f"  the sequence would loop {secs / bed:.1f}× under the letter."
                      if secs > bed else
                      f"  {clock(bed - secs)} of backing track spare — no loop needed.")
        return

    # The fan-out, when there is one, runs before the loop below rather than inside it: the
    # workers fill the cache, and the loop then finds everything already there and does what it
    # does on a resumed run. Which is the honest description of what happened — the chunks
    # really are cached by the time the parent reads them.
    fanned = 0
    if workers > 1 and len(hits) < len(chunks):
        failed = fan_out(args, chunks, cached, hits, workers, digest)
        before = hits
        hits = {i for i, path in enumerate(cached)
                if path is not None and path.exists() and path.stat().st_size}
        fanned = len(hits - before)
        steps.mark(f"fan out ({fanned} generated, {workers} workers)")
        if len(hits) < len(chunks):
            print(f"\n{len(hits)}/{len(chunks)} chunk(s) are cached and will not be recomputed "
                  "— re-run the same command to pick up from here.", file=sys.stderr)
            sys.exit(f"{len(failed)} worker(s) failed and "
                     f"{len(chunks) - len(hits)} chunk(s) never arrived.")

    # Every chunk missing from the cache means loading the model; a fully cached re-render is
    # a stitch and an encode, and should not pay a gigabyte of weights to do it.
    model = None
    tally: dict = {}  # filled by instrument(), read by stage_report() after the loop
    if len(hits) < len(chunks):
        device = pick_device(args.device)
        if device == "mps":
            # A few ops still have no MPS kernel. Without this the run dies partway through
            # the first chunk on an operator name nobody should have to look up.
            os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        print(f"Loading {args.model} on {device} (first run downloads the weights)…", flush=True)
        model = load_model(args.model, device, reference, args.exaggeration)
        instrument(model, tally)
        steps.mark(f"load model ({args.model}, {device})")

    parts: list[np.ndarray] = []
    from_cache = 0
    try:
        for i, (text, gap) in enumerate(pieces):
            path = cached[i]
            if path is not None and i in hits:
                samples = read_wav(path)
                from_cache += 1
                print(f"  chunk {i + 1}/{len(chunks)}: cached")
            else:
                t = time.perf_counter()
                samples = to_pcm16(generate(model, args.model, text, params, args.seed))
                if path is not None:
                    # Written the instant it comes back, not at the end: a run killed on chunk
                    # 40 of 60 keeps the 39 it computed, and re-running resumes from there.
                    write_atomic(path, wav_bytes(samples, SAMPLE_RATE))
                print(f"  chunk {i + 1}/{len(chunks)}: {len(text)} chars, "
                      f"{len(samples) / SAMPLE_RATE:.1f}s in {time.perf_counter() - t:.1f}s")
            parts.append(samples)
            if gap:
                parts.append(np.zeros(int(gap * SAMPLE_RATE), dtype="<i2"))
    except (Exception, KeyboardInterrupt) as e:
        # Nothing was paid for, but something was waited for. Whatever finished is already on
        # disk, so say how much and make the next move obvious: run the same command again.
        if cache_dir is not None:
            kept = sum(1 for c in chunks
                       if chunk_path(cache_dir, c, args.model, reference,
                                     params, args.seed).exists())
            print(f"\n{kept}/{len(chunks)} chunk(s) are cached and will not be recomputed — "
                  "re-run the same command to pick up from here.", file=sys.stderr)
        sys.exit(f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)
    if from_cache:
        made = f" — {fanned} of them made by the workers just now" if fanned else ""
        print(f"  {from_cache}/{len(chunks)} chunk(s) came from the cache{made}.")
    stages = stage_report(tally)
    if stages:
        print(stages)
    steps.mark(f"synthesis ({len(chunks) - from_cache} generated, {from_cache} cached)")

    with tempfile.TemporaryDirectory() as td:
        # Assembled in memory and written once. The pieces are all 16-bit mono at one rate by
        # construction, so this is a concatenation rather than a mix — no filter graph, no
        # resampling, and the beats land on the sample they were asked for.
        combined = Path(td) / "combined.wav"
        combined.write_bytes(wav_bytes(np.concatenate(parts), SAMPLE_RATE))
        steps.mark("assemble")

        if args.normalize:
            rms, peak = normalize(combined, out, args.bitrate,
                                  compress=not args.no_compress, rate=DELIVERY_RATE)
            how = "Levelled" if args.no_compress else "Compressed + normalised"
            print(f"{how}: RMS {rms:.1f} dB, peak {peak:.1f} dB."
                  + (" (dynamics left to the mixer downstream)" if args.no_compress else ""))
        else:
            encode(combined, out, args.bitrate, rate=DELIVERY_RATE)
        steps.mark("level + encode")

    secs = probe_duration(out) or 0.0
    print(f"Saved {out.stat().st_size / 1024:.1f} KB, {clock(secs)} → {link(out)}"
          f"  (nothing billed — synthesised on this machine)")

    # Same hand-off as the remote engine, to the same mixer, which names the master by
    # dropping the -voice suffix (see _names.master_name).
    if music:
        music_out = master_name(out)
        mixer = Path(__file__).with_name("mix_music.py")
        print(f"Mixing over {' → '.join(p.name for p in music)} → {music_out.name} "
              f"({args.mix_engine} chain)")
        subprocess.run(
            [str(mixer), str(out), *[a for p in music for a in ("--music", str(p))],
             "--out", str(music_out), "--engine", args.mix_engine],
            check=True,
        )
        steps.mark("mix (mix_music.py)")

    steps.report("timing — chatterbox_tts")


if __name__ == "__main__":
    main()
