#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
_cache — one name for a thing that was expensive to make, derived from everything that made it.

Not a script. This was two functions inside `_speech.py`, where it was written for TTS chunks
and described in terms of audio. It moved here when a second kind of paid call needed the same
discipline: `hunk_connections.py` sends a week of hunks to Haiku, and a re-run that changed
nothing should cost nothing. The rule is not about audio, and the code never was either — the
payload has always been the caller's to decide.

The rule, in one line: **if changing it changes the answer, it belongs in the key.** For a
narration that means the voice, the model and the output format; for a model call it means the
inputs and the prompt that will be sent with them. Leaving something out is how a cache starts
returning a stale answer that looks like a fresh one, which is worse than no cache at all.

No dependencies, so importing this leaves a tool's `dependencies = []` intact.
"""

from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path


def cache_path(cache_dir: Path, payload: dict, suffix: str) -> Path:
    """A name for one expensive result, derived from everything that decides what it is.

    The caller decides what goes in the payload, and the rule is the same for every caller: if
    changing it changes the result, it belongs in the key. For audio that includes the output
    format, which means a tier upgrade — 128 kbps to 192 — misses and re-synthesises. That is
    the honest answer: you asked for better audio.
    """
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return cache_dir / f"{hashlib.sha256(blob.encode()).hexdigest()}{suffix}"


def write_atomic(path: Path, data: bytes) -> None:
    """Write-then-rename: a killed process must not leave a truncated file that later reads
    as a valid cache hit.

    The temporary name carries the pid because these caches now have more than one writer —
    chatterbox_tts.py --concurrency fans a render out across worker processes that share one
    cache directory. They are given disjoint chunks, so two of them should never be writing
    the same key; the pid makes that a fact about the filesystem rather than a fact about the
    scheduling, which is the kind of guarantee worth having for free.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.part")
    tmp.write_bytes(data)
    tmp.replace(path)
