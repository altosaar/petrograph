#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
_speech — the half of narration that does not depend on who, or what, is speaking.

Two engines now turn a microlite markdown file into an mp3 — eleven_tts.py
through ElevenLabs' servers, chatterbox_tts.py entirely on this machine — and almost
everything between the markdown and the mp3 is the same work either way: strip the
front-matter, keep the document's shape, pack sentences into chunks, stitch the pieces,
level the result, measure what came out. Only the middle step differs, which is the one
that decides where the text goes.

That middle step is the whole privacy question, so it is worth keeping the seam clean: a
letter narrated locally must be shaped by exactly the same code as one narrated remotely,
or "the same letter, without the network" quietly stops being true.

What is deliberately *not* here: how a pause is asked for. ElevenLabs reads a spaced hyphen
as a beat, so its pauses are characters in the text it is sent; Chatterbox has no such
convention and gets real silence spliced between its chunks instead. Same intent, two
mechanisms, and each lives with the engine that understands it.

No dependencies, so importing this leaves a tool's `dependencies = []` intact — which is
load-bearing for eleven_tts.py and compact_tts.py, and merely polite for the one script
here that does pull in half a gigabyte of model weights.

Importable and runnable both, for the same reason _names.py is: the justfile cannot import
Python, and the alternative to the --sh form is a second copy of the precedence rule written
in bash, which is how the naming rule drifted into five copies before it was pulled in here.

    eval "$(./tools/_speech.py --sh)"          # engine, tts_tool — from the environment
    eval "$(./tools/_speech.py --sh chatterbox)"   # …or from an explicit override
    eval "$(./tools/_speech.py --sh ../narrate.py)" # …or from a script that is not from here

A third kind of answer is a path. The two engines below are the ones this repo ships, but the
seam is a command line, not an import, so anything that answers to it can narrate: point the
flag, the front-matter or PETROGRAPH_TTS_ENGINE at a script and it is handed the same markdown
and the same --out that chatterbox_tts.py would have been. The contract is that script's half
of chatterbox_tts.py's interface — a markdown path, --normalize, --no-compress, --out, and
--voice-sample and --music where they apply — and honouring it is on the script.
"""

from __future__ import annotations

# Moved to _cache.py when a model call needed the same discipline as a TTS chunk;
# imported back in so the two engines keep reaching for them where they always have.
from _cache import cache_path, write_atomic  # noqa: F401
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

# The one sibling this module imports, and for the same reason it exists at all: a number is
# spelled out before either engine sees it, so both narrate the same sentence and a --dry-run
# prices the words rather than the digits they were written as. No dependencies of its own,
# and no path to set up — uv puts the script's own directory first on sys.path.
from _numbers import spell_numbers

# The engines built in here, and for each one the tool that is it and where the letter goes.
# Adding a fourth means adding a line here and a file beside them: nothing downstream —
# weekly_review.py, the justfile — names a script.
#
# The second half of each entry is not decoration. A run announces where it is about to send
# the letter, and that sentence used to be written at the call site, as `"sent to ElevenLabs"
# if engine == "eleven" else "synthesised locally"` — which is a claim about privacy derived
# from an else. Kept here it is the engine's own answer, and a new engine cannot be added
# without saying what its answer is.
ENGINES = {
    # ElevenLabs: the letter is sent to their servers.
    "eleven": ("eleven_tts.py", "sent to ElevenLabs"),
    # Resemble AI's Chatterbox: nothing leaves this machine.
    "chatterbox": ("chatterbox_tts.py", "synthesised on this machine"),
}
DEFAULT_ENGINE = "eleven"

TARGET_RMS = -20.0  # dB, mid of the requested -23…-18 band
TRUE_PEAK = -3.5  # dBFS limiter ceiling, leaving headroom to stay below -3 dBTP

HEADER_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*$")
BULLET_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")
RULE_RE = re.compile(r"^([-*_])(?:\s*\1){2,}$")  # --- / *** horizontal rules: not speech

# The three block kinds strip_markdown emits, in the order a document tends to hold them.
KINDS = ("header", "bullet", "text")


# ── the document ──────────────────────────────────────────────────────────────────────────

def strip_markdown(raw: str) -> list[tuple[str, str]]:
    """Drop YAML front-matter and markdown, keeping the document's shape.

    Returns one (kind, text) block per line, kind in {"header", "bullet", "text"}, so
    pause insertion can put a longer beat after a section heading than between two
    sentences of the same paragraph. Headings and bullets carry no terminal punctuation
    of their own, which would run them straight into whatever follows — so each gets a
    full stop, giving the voice something to land on (and giving chunk_text a clean
    sentence boundary to split at).

    Numbers are spelled into English on the way through — "$2,480" becomes "one thousand
    four hundred sixteen dollars" — because a letter is written in figures and every engine
    guesses differently at what a figure says. See _numbers.py; the guessing is what garbled
    them. It happens here, before the split into blocks and long before either synthesiser,
    so the spelling is the same sentence for both of them.
    """
    if raw.startswith("---\n"):  # the context-engineering log, if present
        end = raw.find("\n---", 4)
        if end != -1:
            # -1 when the file ends on the closing --- with no trailing newline. Slicing from
            # there would put the front-matter back, and we would narrate and pay for the YAML.
            nl = raw.find("\n", end + 1)
            raw = raw[nl + 1 :] if nl != -1 else ""
    blocks: list[tuple[str, str]] = []
    for line in raw.splitlines():
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"\*(.+?)\*", r"\1", line)
        line = line.strip()
        if not line or RULE_RE.match(line):
            continue
        header = HEADER_RE.match(line)
        bullet = None if header else BULLET_RE.match(line)
        kind = "header" if header else "bullet" if bullet else "text"
        text = (header or bullet).group(1).strip() if (header or bullet) else line
        if not text:
            continue
        text = spell_numbers(text)
        if kind != "text" and text[-1] not in ".!?:;,":
            text += "."
        blocks.append((kind, text))
    return blocks


def clamp(blocks: list[tuple[str, str]], limit: int | None) -> list[tuple[str, str]]:
    # Cap billed characters on the spoken content, trimming back to a word boundary so
    # speech stays clean. Measured before pauses are added, so --limit counts words, not
    # the hyphens we insert between them.
    if limit is None:
        return blocks
    kept, used = [], 0
    for kind, text in blocks:
        if used + len(text) <= limit:
            kept.append((kind, text))
            used += len(text) + 1
            continue
        room = limit - used
        if room > 0:
            cut = text[:room]
            cut = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip()
            if cut:
                kept.append((kind, cut))
        break
    return kept


def block_summary(blocks: list[tuple[str, str]]) -> str:
    """"3 header, 12 bullet, 40 text" — what the document turned out to be made of."""
    counts = {kind: sum(1 for k, _ in blocks if k == kind) for kind in KINDS}
    return ", ".join(f"{n} {kind}" for kind, n in counts.items() if n)


def chunk_text(text: str, size: int) -> list[str]:
    # Pack whole sentences into <=size chunks; hard-split any sentence longer than size on words.
    chunks: list[str] = []
    cur = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(sentence) > size:
            if cur:
                chunks.append(cur)
                cur = ""
            line = ""
            for word in sentence.split():
                if line and len(line) + 1 + len(word) > size:
                    chunks.append(line)
                    line = word
                else:
                    line = f"{line} {word}".strip()
            cur = line
        elif cur and len(cur) + 1 + len(sentence) > size:
            chunks.append(cur)
            cur = sentence
        else:
            cur = f"{cur} {sentence}".strip()
    if cur:
        chunks.append(cur)
    return chunks


# ── the cache ─────────────────────────────────────────────────────────────────────────────

# ── ffmpeg ────────────────────────────────────────────────────────────────────────────────

def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


# Scratch audio between two stages of one run: 32-bit float, so a gain change or a filter
# between stages never rounds to an integer grid. ffmpeg's own default for a .wav is 16-bit,
# which would quantise the narration before it had even been levelled.
SCRATCH = ("-c:a", "pcm_f32le")


def codec(dst: Path, bitrate: str) -> tuple[str, ...]:
    """How a delivered narration is written, decided by the name it was asked for.

    A .wav is an intermediate — inside a session the narration feeds mix_music.py, which
    encodes the master — so it is 24-bit PCM, and the master's is the only lossy encode after
    the engine's own. Anything else is an mp3 at `bitrate`: a narration outside a session is
    the finished file, and that one should be small enough to play on a phone.
    """
    if dst.suffix.lower() == ".wav":
        return ("-c:a", "pcm_s24le")
    return ("-c:a", "libmp3lame", "-b:a", bitrate)


def measure(path: Path) -> tuple[float, float]:
    # (RMS dB, peak dB) via volumedetect.
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    rms = float(re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", p.stderr).group(1))
    peak = float(re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", p.stderr).group(1))
    return rms, peak


def probe_duration(path: Path) -> float | None:
    # Seconds of audio, or None for anything ffprobe cannot read. Used on cached chunks and
    # on backing tracks alike, so an estimate never has to guess at a file it can just read.
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(p.stdout.strip())
    except ValueError:
        return None


def clock(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m // 60}:{m % 60:02d}:{s:02d}" if m >= 60 else f"{m}:{s:02d}"


def stitch(paths: list[Path], dst: Path) -> None:
    inputs = [arg for p in paths for arg in ("-i", str(p))]
    graph = "".join(f"[{i}:a]" for i in range(len(paths))) + f"concat=n={len(paths)}:v=0:a=1[a]"
    ffmpeg(*inputs, "-filter_complex", graph, "-map", "[a]", *SCRATCH, str(dst))


def normalize(src: Path, dst: Path, bitrate: str, compress: bool = True,
              rate: int | None = None) -> tuple[float, float]:
    # Compress dynamic range, gain to TARGET_RMS, then limit true peak below -3 dBTP.
    #
    # compress=False skips the compressor and levels only. Use it when this narration is
    # headed for mix_music.py: that chain runs its own compressor (3.5:1), and stacking this
    # 4:1 compressor in front of it squeezes the voice twice by two stages that know
    # nothing about each other — measured at LRA 2.6, where spoken word normally sits 5-9.
    # Standalone voice-only output still wants the compression, hence the flag.
    #
    # rate resamples on the way out. The remote engine already returns 44.1k and passes None;
    # the local one synthesises at 24k, and delivering that rate would hand mix_music.py a
    # narration coarser than the bed it is about to sit under.
    with tempfile.TemporaryDirectory() as td:
        stage = src
        if compress:
            stage = Path(td) / "comp.wav"
            ffmpeg("-i", str(src), "-af",
                   "acompressor=threshold=-20dB:ratio=4:attack=15:release=250",
                   *SCRATCH, str(stage))
        rms, _ = measure(stage)
        gain = TARGET_RMS - rms
        limit = 10 ** (TRUE_PEAK / 20)
        # level=disabled: alimiter auto-normalises to 0 dB otherwise, undoing the RMS gain.
        ffmpeg("-i", str(stage), "-af", f"volume={gain:.2f}dB,alimiter=limit={limit:.4f}:level=disabled",
               *(("-ar", str(rate)) if rate else ()),
               *codec(dst, bitrate), str(dst))
    return measure(dst)


def encode(src: Path, dst: Path, bitrate: str, rate: int | None = None) -> None:
    """Straight to dst's format, no leveling — the --normalize-less path out of a render."""
    ffmpeg("-i", str(src), *(("-ar", str(rate)) if rate else ()), *codec(dst, bitrate), str(dst))


# ── what it will sound like before it exists ──────────────────────────────────────────────

def estimate_length(chunks: list[str], cached: list[Path | None],
                    fallback_rate: float) -> tuple[float, int, float]:
    """How long the narration will run: (seconds, chunks measured, chars/second used).

    A chunk already in the cache is the audio itself, so its length is read off the file and
    is not an estimate at all — a re-render of an unchanged letter is exact. What is missing
    is extrapolated at the rate the cached chunks actually came back at, which folds in the
    voice, the speed and how densely the pauses were laid in. With nothing cached there is
    nothing to calibrate against, and the engine's own measured fallback rate stands in.
    """
    known = [(len(c), probe_duration(p))
             for c, p in zip(chunks, cached) if p is not None and p.exists()]
    known = [(n, d) for n, d in known if d]
    rate = sum(n for n, _ in known) / sum(d for _, d in known) if known else fallback_rate
    missing = sum(len(c) for c, p in zip(chunks, cached) if not (p is not None and p.exists()))
    return sum(d for _, d in known) + missing / rate, len(known), rate


def _named(raw: str, base: Path | None) -> Path:
    """Where a candidate piece would be, so that "does this exist?" is asked about it."""
    path = Path(raw).expanduser()
    return path if path.is_absolute() or base is None else base / path


def music_tracks(chunks: list[str] | None, base: Path | None = None) -> list[Path]:
    """One flat, ordered bed however it was asked for: --music a --music b, or --music a,b.

    A comma is how you say "and then", and it is also a character macOS is perfectly happy to
    put in a filename — `{Label Co., Ltd. TECD-1}` is a real album folder, and
    splitting it blind produced two paths that exist nowhere and a "No such file" that pointed
    at neither the file nor the reason.

    So the separator is resolved against the disk rather than assumed: at each position, the
    longest run of comma-joined pieces that names something real is taken as one path, and a
    position that names nothing real falls through as a single piece. Longest-first is what
    makes `A/{Co., Ltd.}/1.flac,A/{Co., Ltd.}/2.flac` come apart in the one place it should —
    which matters because comma-separating is all `just render` offers, so "repeat the flag
    instead" is not a way out of it there.

    A path that simply does not exist still splits, harmlessly: every piece falls through to
    the caller's existence check, which is where a missing track should be reported anyway.

    `base` is what a relative piece is asked about, for the caller that is not reading a
    command line: a session's `music:` list is resolved against the session directory, and
    checking those against the process's working directory would split a real path — the one
    failure this routine exists to prevent — for no reason but where it was run from.
    """
    tracks: list[Path] = []
    for chunk in chunks or []:
        pieces = chunk.split(",")
        i = 0
        while i < len(pieces):
            for j in range(len(pieces), i, -1):
                joined = ",".join(pieces[i:j]).strip()
                # The empty string is not a candidate: Path("") is Path("."), which exists,
                # and would swallow a trailing comma as a track pointing at the repo.
                if joined and _named(joined, base).exists():
                    tracks.append(Path(joined).expanduser())
                    i = j
                    break
            else:
                if pieces[i].strip():
                    tracks.append(Path(pieces[i].strip()).expanduser())
                i += 1
    return tracks


def music_field(chunks: list[str] | None) -> str:
    """The `music:` front-matter line for tracks named on a command line, or "".

    Absolute, because the two places a relative path could be read from are not the same
    place: one typed here is relative to the directory you typed it in, and one sitting in a
    session's front-matter is resolved against the session. Writing what was meant is the
    only way those agree.

    Checked now rather than when the narrator reaches for it, which is on the far side of
    both model calls: a mistyped backing track should cost a re-run of the command that
    named it, not the read and the letter. Raises ValueError, for the caller to report.

    A comma in a track's name survives being written into the list, because the list is read
    back by the same music_tracks that separated it — `{Label Co., Ltd.}` is a real
    album folder, and a bed you cannot name is not much of an option.
    """
    tracks: list[str] = []
    for track in music_tracks(chunks):
        path = track.resolve()
        if not path.exists():
            raise ValueError(f"--music: {track} does not exist.")
        tracks.append(str(path))
    return ", ".join(tracks)


def bed_length(tracks: list[Path], crossfade: float = 2.0) -> float | None:
    # What mix_music.py will build from these files: spliced end to end, each join eating one
    # crossfade. Mirrors splice() there; keep the default in step with --loop-crossfade.
    # Measured before that stage trims each track's leading and trailing silence, so the real
    # bed comes out a few seconds shorter — near enough for deciding how much music to line up.
    lengths = [probe_duration(p) for p in tracks]
    if not lengths or any(d is None for d in lengths):
        return None
    return sum(lengths) - crossfade * (len(lengths) - 1)


# ── which engine ──────────────────────────────────────────────────────────────────────────

def is_engine_path(engine: str) -> bool:
    """Is this engine a script somewhere, rather than one of the names above?

    A path is anything with a separator in it or a .py on the end, which is what an engine
    living outside this repo inevitably looks like and what no built-in name ever will.
    """
    return "/" in engine or engine.endswith(".py")


def resolve_engine(*candidates: str | None) -> str:
    """Which engine narrates, given the answers in order of precedence.

    Callers pass what they know, most specific first — a --tts-engine flag, then a session's
    `engine:` front-matter — and whatever is left falls through to $PETROGRAPH_TTS_ENGINE
    (environment or the repo's .env, so a machine can be switched over once and stay switched)
    and finally to eleven.

    The default is deliberately the remote one. It is what every existing session was rendered
    with, and changing what an unqualified `just render` sounds like — or where it sends the
    letter — is not something a new file should do on its own. Choosing the local engine is a
    thing you say, in a flag, in the front-matter, or once in .env.

    An answer that looks like a path is taken as one, and is returned verbatim: a narrator can
    be a script this repo has never heard of, as long as it answers to the same command line as
    the two that live here. That is how narrating on a GPU box you rent by the hour stays out
    of petrograph — the wrapper knows about ssh, and this module knows only that it was handed
    a script. It is returned unlowered, because unlike a name, a filename's case matters.
    """
    from _env import dotenv_key  # local import: keeps this module importable on its own

    for choice in (*candidates, os.environ.get("PETROGRAPH_TTS_ENGINE"),
                   dotenv_key("PETROGRAPH_TTS_ENGINE"), DEFAULT_ENGINE):
        if choice:
            name = choice.strip()
            if is_engine_path(name):
                return name
            name = name.lower()
            if name not in ENGINES:
                raise ValueError(
                    f"unknown TTS engine {choice!r} — expected one of {', '.join(ENGINES)}, "
                    "or the path to a script that narrates like one")
            return name
    return DEFAULT_ENGINE


def engine_tool(engine: str) -> Path:
    """The script that narrates for this engine.

    A built-in name resolves beside this module in tools/. A path resolves against the working
    directory if it is relative, and is checked here rather than at the subprocess call — a
    mistyped engine should fail saying it is a mistyped engine, not as a bare ENOENT from
    somewhere three stages into a run that has already paid for two model calls.
    """
    if not is_engine_path(engine):
        return Path(__file__).with_name(ENGINES[engine][0])
    path = Path(engine).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_file():
        raise ValueError(f"TTS engine {engine!r} is not a file (looked at {path})")
    if not os.access(path, os.X_OK):
        raise ValueError(f"TTS engine {path} is not executable — chmod +x it")
    return path


def engine_label(engine: str) -> str:
    """A short name for this engine, fit to print in a line about a run."""
    return Path(engine).stem if is_engine_path(engine) else engine


def engine_where(engine: str) -> str:
    """Where this engine sends the letter, in words, for a run to say out loud before it does.

    An outside script is not asked to justify itself and is not vouched for: this says what is
    actually known, which is its name. What it does with the letter is between you and it.
    """
    if is_engine_path(engine):
        return f"handed to {Path(engine).name}"
    return ENGINES[engine][1]


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] != "--sh" or len(args) > 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} --sh [engine]")
    try:
        engine = resolve_engine(args[1] if len(args) == 2 else None)
    except ValueError as e:
        sys.exit(str(e))
    try:
        tool = engine_tool(engine)
    except ValueError as e:
        sys.exit(str(e))
    # engine stays the raw answer, because the justfile branches on it being "eleven". The
    # label and the sentence are for printing, so a recipe can say where the letter went
    # without holding a second copy of the rule that decides.
    for var, value in (("engine", engine), ("tts_tool", tool),
                       ("engine_label", engine_label(engine)),
                       ("engine_where", engine_where(engine))):
        print(f"{var}={shlex.quote(str(value))}")


if __name__ == "__main__":
    main()
