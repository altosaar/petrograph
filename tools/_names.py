#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
_names — what a session's audio is called, decided once.

Four tools and one justfile recipe all have to agree on where a narration goes and what the
master built from it is called. They used to agree by copy-paste, which is to say they did not:
weekly_review.py looked for <date>-music.mp3 while mix_music.py was writing lifelog-<date>.mp3,
so a finished master was reported as never having been made. The rule lives here now, and the
tools ask rather than remember.

The rule, in two parts:

  Inside sessions/<date>/, the folder is the identity. Most of a session's files are named
  for what they are — microlite.md, bundle.md — so every week's would render over the last
  one's audio. The date is already the folder, so repeating it in the filename is redundant
  in place; but audio is the one artifact that leaves the folder, for a phone or a car, where
  the filename is the only context there is. Hence lifelog-<date>.

  The letter carries that name already — it is lifelog-<date>.md, see _session.py — so for
  the letter this rule is now the identity function, and its audio is simply its own stem.
  Any other markdown in the folder is a second thing to narrate rather than the letter, and
  its own name comes along: lifelog-<date>-<stem>, beside the markdown as always. Rendering a
  variant used to land on the letter's own master and destroy it, because the folder was the
  whole identity; a variant that already carries the lifelog-<date>- prefix keeps it rather
  than being given a second one, so lifelog-<date>-metaphors.md → lifelog-<date>-metaphors.mp3
  whether it was named by hand or by this rule.

  Anywhere else, the markdown's own stem is a name somebody chose, and is left alone.

  The master is the thing you actually play, so it takes the plain name and the narration
  carries -voice: lifelog-<date>-voice.wav → lifelog-<date>.mp3. A narration that does not end
  in -voice is a one-off rather than part of a set, and its master takes -music instead, so
  mixing an arbitrary file still lands somewhere obvious.

  Inside a session the narration is a WAV because it is an intermediate: the mixer reads it,
  and the master is the one mp3 encoded after the engine's own. Writing it as an mp3 put a
  second lossy generation between the API and the master for nothing. A narration outside a
  session is a finished file rather than a stage, and stays an mp3.

Importable and runnable both. `dependencies = []` is load-bearing: eleven_tts.py, compact_tts.py
and weekly_review.py all declare no dependencies, and uv resolves only the entry script's, so
anything imported into them must add nothing. That is also why this could not simply live in
mix_music.py, which is where the master is written — mix_music pulls in pedalboard, numpy and
scipy, and importing it from a dependencies = [] script would fail at the import.

The --sh form exists for the justfile, which cannot import Python and was carrying a fifth copy
of the rule in bash:

    eval "$(./tools/_names.py --sh sessions/2026-05-15/lifelog-2026-05-15.md)"
    # stem, voice, narration, mix_mp3, voice_stem
"""

from __future__ import annotations
import shlex
import sys
from pathlib import Path

# What a session's files are called is _session.py's to say, not a second copy here — the
# letter's name and the prefix its audio is built from are the same string now.
from _session import LIFELOG


def in_session(md: Path) -> bool:
    # sessions/<date>/<anything>.md — inside one, the folder is the identity, not the file.
    return Path(md).resolve().parent.parent.name == "sessions"


def audio_stem(md: Path) -> str:
    """The bare name every audio file for this markdown is built from."""
    md = Path(md).resolve()
    if not in_session(md):
        return md.stem
    session = f"{LIFELOG}-{md.parent.name}"
    # The letter is named for the session already, so it is its own answer — and so is a
    # variant that was named the same way, which is what stops `lifelog-<date>-metaphors.md`
    # from being handed a second copy of the prefix.
    if md.stem == session or md.stem.startswith(f"{session}-"):
        return md.stem
    # Anything else in the folder is a second thing to narrate, and its own name distinguishes
    # it from the letter's.
    return f"{session}-{md.stem}"


def voice_name(md: Path) -> Path:
    """Where the narration goes: beside the markdown that produced it.

    One folder holds a session's inputs and its outputs, so every command that takes a session
    reads and writes in the same place. Inside a session the renders are a set, so the
    narration takes -voice and leaves the plain name to the master — and is lossless, since
    the master is what gets encoded.
    """
    md = Path(md).resolve()
    return md.parent / (f"{audio_stem(md)}-voice.wav" if in_session(md) else f"{audio_stem(md)}.mp3")


def narration(md: Path) -> Path:
    """The narration already on disk for this markdown, for a remix to read.

    The WAV a render writes now, or else the -voice.mp3 that a render from before the
    narration went lossless left behind — paid for once, and still a fine thing to remix.
    """
    voice = voice_name(md)
    older = voice.with_suffix(".mp3")
    return older if not voice.exists() and older.exists() else voice


def master_name(voice: Path) -> Path:
    """Where the finished master goes, given the narration it is built from."""
    voice = Path(voice)
    stem = voice.stem
    return voice.with_name(f"{stem[:-6]}.mp3" if stem.endswith("-voice") else f"{stem}-music.mp3")


def stem_name(voice: Path) -> Path:
    """The remixable WAV of the colored voice, named off the master it belongs to."""
    master = master_name(voice)
    return master.with_name(f"{master.stem}-stem.wav")


def main() -> None:
    args = sys.argv[1:]
    if len(args) != 2 or args[0] != "--sh":
        sys.exit(f"usage: {Path(sys.argv[0]).name} --sh <markdown>")
    md = Path(args[1]).resolve()
    voice = voice_name(md)
    # voice is where a render writes the narration; narration is the one a remix reads.
    for var, value in (("stem", audio_stem(md)), ("voice", voice), ("narration", narration(md)),
                       ("mix_mp3", master_name(voice)), ("voice_stem", stem_name(voice))):
        print(f"{var}={shlex.quote(str(value))}")


if __name__ == "__main__":
    main()
